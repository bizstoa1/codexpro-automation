from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import TypeAlias

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "bin" / "chatgpt_capability_policy.py"
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def load():
    spec = importlib.util.spec_from_file_location("capability_policy_test", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def contract(project: Path, profile: Path, actor: str) -> dict[str, JsonValue]:
    return {
        "schema": "codex.chatgpt.project-capability/v1",
        "actor": actor,
        "access": "bounded-write" if actor == "pro" else "read-only",
        "binding": {
            "project_root": str(project.resolve()),
            "profile_path": str(profile.resolve()),
            "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        },
    }


def test_host_policy_is_required_and_exact_profile_hash_is_monotonic(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    profile = project / ".codex/project-capabilities.json"
    profile.parent.mkdir(parents=True)
    profile.write_text("{}\n", encoding="utf-8")
    capability = contract(project, profile, "pro")

    with pytest.raises(module.CapabilityPolicyError) as missing:
        module.admit(capability, state)
    assert missing.value.code == "CAPABILITY_HOST_POLICY_REQUIRED"

    installed = module.install_host_policy(
        state,
        [module.ProjectAdmission(project, True, True, True)],
        max_web_multi_concurrency=5,
    )
    admitted = module.admit(capability, state)

    assert installed.is_file()
    assert admitted["project_root"] == str(project.resolve())
    assert admitted["profile_sha256"] == hashlib.sha256(profile.read_bytes()).hexdigest()

    profile.write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(module.CapabilityPolicyError) as changed:
        module.admit(contract(project, profile, "pro"), state)
    assert changed.value.code == "CAPABILITY_PROFILE_NOT_QUALIFIED"


def test_host_policy_modes_and_concurrency_fail_closed(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    profile = project / ".codex/project-capabilities.json"
    profile.parent.mkdir(parents=True)
    profile.write_text("{}\n", encoding="utf-8")
    module.install_host_policy(
        state,
        [module.ProjectAdmission(project, False, True, True)],
        max_web_multi_concurrency=3,
    )

    assert module.admit(contract(project, profile, "oracle"), state)["oracle_read"] is True
    with pytest.raises(module.CapabilityPolicyError) as pro:
        module.admit(contract(project, profile, "pro"), state)
    assert pro.value.code == "CAPABILITY_PRO_DISABLED"
    with pytest.raises(module.CapabilityPolicyError) as multi:
        module.admit(contract(project, profile, "web-multi"), state, requested_concurrency=4)
    assert multi.value.code == "CAPABILITY_HOST_CONCURRENCY_MISMATCH"


def test_policy_install_rejects_duplicate_or_overlapping_state_roots(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    profile = project / ".codex/project-capabilities.json"
    profile.parent.mkdir(parents=True)
    profile.write_text("{}\n", encoding="utf-8")

    with pytest.raises(module.CapabilityPolicyError) as duplicate:
        module.install_host_policy(
            tmp_path / "state",
            [
                module.ProjectAdmission(project, False, True, True),
                module.ProjectAdmission(project, False, True, True),
            ],
            max_web_multi_concurrency=5,
        )
    assert duplicate.value.code == "CAPABILITY_HOST_POLICY_INVALID"

    with pytest.raises(module.CapabilityPolicyError) as overlap:
        module.install_host_policy(
            project / ".state",
            [module.ProjectAdmission(project, False, True, True)],
            max_web_multi_concurrency=5,
        )
    assert overlap.value.code == "CAPABILITY_HOST_STATE_OVERLAP"


def test_configure_cli_preview_is_pure_and_pro_is_an_exact_subset(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load()
    state = tmp_path / "state"
    one = tmp_path / "one"
    two = tmp_path / "two"
    for project in (one, two):
        profile = project / ".codex/project-capabilities.json"
        profile.parent.mkdir(parents=True)
        profile.write_text("{}\n", encoding="utf-8")

    preview_exit = module.main([
        "configure",
        "--state-root", str(state),
        "--project-root", str(one),
        "--project-root", str(two),
        "--enable-pro-root", str(two),
        "--max-web-multi-concurrency", "4",
        "--dry-run",
    ])
    preview = json.loads(capsys.readouterr().out)

    assert preview_exit == 0
    assert not module.policy_path(state).exists()
    rows = {row["project_root"]: row for row in preview["policy"]["projects"]}
    assert rows[str(one.resolve())]["pro"] is False
    assert rows[str(two.resolve())]["pro"] is True

    with pytest.raises(module.CapabilityPolicyError) as outside:
        module.main([
            "configure",
            "--state-root", str(state),
            "--project-root", str(one),
            "--enable-pro-root", str(tmp_path / "outside"),
        ])
    assert outside.value.code == "CAPABILITY_HOST_POLICY_INVALID"
