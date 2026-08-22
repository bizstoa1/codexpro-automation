from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "bin" / "chatgpt_capability_runtime.py"


def load():
    spec = importlib.util.spec_from_file_location("capability_runtime_test", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def initialize(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "codex/capability-runtime", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Capability Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "capability@example.test"], check=True)
    (root / "src").mkdir()
    (root / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)


def controls(root: Path) -> tuple[Path, Path]:
    profile = root / ".codex/project-capabilities.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps({
        "schema": "codex.chatgpt.project-capability-profile/v1",
        "pro": {
            "enabled": True,
            "write_root_ceiling": ["src"],
            "commands": "none",
            "require_clean_git": True,
            "require_nonprotected_branch": True,
        },
        "web_multi": {
            "enabled": True,
            "access": "read-only",
            "min_lanes": 2,
            "max_lanes": 5,
            "max_concurrency": 5,
            "all_lanes_required": True,
            "merger_policy": "exactly-one",
            "nesting": "forbidden",
        },
        "protected_branches": ["main", "master"],
        "write_deny_paths": [".git", ".codex", ".ai-bridge", "AGENTS.md"],
        "external_actions": "deny",
    }), encoding="utf-8")
    mission = root / "mission.md"
    mission.write_text("update src/app.py\n", encoding="utf-8")
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    authority = root / "authority.json"
    authority.write_text(json.dumps({
        "schema": "codex.chatgpt.pro-mission-authority/v1",
        "project_root": str(root.resolve()),
        "mission_path": str(mission.resolve()),
        "mission_sha256": hashlib.sha256(mission.read_bytes()).hexdigest(),
        "expected_head": head,
        "allowed_write_paths": ["src"],
        "allowed_command_ids": [],
        "external_actions": "deny",
    }), encoding="utf-8")
    return mission, authority


def test_pro_runtime_dry_run_is_pure_and_terminal_success_releases(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    initialize(project)
    mission, authority = controls(project)
    module.POLICY.install_host_policy(
        state,
        [module.POLICY.ProjectAdmission(project, True, True, True)],
        max_web_multi_concurrency=5,
    )
    before = {
        path.relative_to(state).as_posix(): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file()
    }

    dry = module.open_pro(project, mission, authority, state_root=state, dry_run=True)
    assert dry.token is None
    after = {
        path.relative_to(state).as_posix(): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file()
    }
    assert after == before

    session = module.open_pro(project, mission, authority, state_root=state, dry_run=False)
    assert session.token
    assert session.token not in session.lease_path.read_text(encoding="utf-8")
    (project / "src/app.py").write_text("VALUE = 2\n", encoding="utf-8")

    receipt = module.finish(session, terminal_harvested=True)

    assert receipt["status"] == "released"
    assert receipt["postflight"]["changed_paths"] == ["src/app.py"]
    assert not session.lease_path.exists()


def test_prompt_binding_requires_one_line_token_and_exact_checkout_root(tmp_path: Path) -> None:
    module = load()

    prompt = module.bind_prompt(
        "@DevSpace Read the mission.",
        tmp_path.resolve(),
        "opaque.signed-token",
    )

    assert prompt.startswith(f'@DevSpace First call open_workspace(path="{tmp_path.resolve()}"')
    assert 'mode="checkout"' in prompt
    assert 'capabilityToken="opaque.signed-token"' in prompt
    assert "\n" not in prompt
    with pytest.raises(module.CapabilityRuntimeError) as invalid:
        module.bind_prompt("@DevSpace Read the mission.", tmp_path, "bad token")
    assert invalid.value.code == "CAPABILITY_TOKEN_INVALID"


def test_pro_runtime_quarantines_out_of_scope_terminal_drift(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    initialize(project)
    mission, authority = controls(project)
    module.POLICY.install_host_policy(
        state,
        [module.POLICY.ProjectAdmission(project, True, True, True)],
        max_web_multi_concurrency=5,
    )
    session = module.open_pro(project, mission, authority, state_root=state, dry_run=False)
    (project / "README.md").write_text("escaped\n", encoding="utf-8")

    with pytest.raises(module.CapabilityRuntimeError) as failure:
        module.finish(session, terminal_harvested=True)

    assert failure.value.code == "CAPABILITY_DIFF_OUT_OF_SCOPE"
    assert json.loads(session.lease_path.read_text(encoding="utf-8"))["state"] == "quarantined"


def test_single_session_recovery_reconstructs_exact_subject_and_baseline(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    initialize(project)
    mission, authority = controls(project)
    module.POLICY.install_host_policy(
        state,
        [module.POLICY.ProjectAdmission(project, True, True, True)],
        max_web_multi_concurrency=5,
    )
    opened = module.open_pro(project, mission, authority, state_root=state)

    recovered = module.resume_single(project, expected_actor="pro", state_root=state)

    assert recovered.lease_id == opened.lease_id
    assert recovered.subject_id == "pro"
    assert recovered.token == opened.token
    receipt = module.finish(recovered, terminal_harvested=True)
    assert receipt["status"] == "released"
