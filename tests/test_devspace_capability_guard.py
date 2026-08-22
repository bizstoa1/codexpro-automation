from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[1]
LEASE_PATH = ROOT / "bin" / "chatgpt_capability_lease.py"
GUARD_PATH = ROOT / "bin" / "devspace-compat" / "1.0.4" / "capability-guard.mjs"
DRIVER_PATH = ROOT / "tests" / "fixtures" / "devspace-capability-driver.mjs"
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def load_lease():
    spec = importlib.util.spec_from_file_location("devspace_guard_lease_test", LEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def capability(root: Path, access: str) -> dict[str, JsonValue]:
    mission = root / "mission.md"
    required_reads: list[JsonValue] = (
        [{"path": str(mission.resolve()), "sha256": hashlib.sha256(mission.read_bytes()).hexdigest()}]
        if access in {"bounded-write", "control-write"}
        else []
    )
    return {
        "schema": "codex.chatgpt.project-capability/v1",
        "actor": "pro" if access == "bounded-write" else "oracle" if access == "control-write" else "web-multi",
        "access": access,
        "binding": {
            "project_root": str(root.resolve()),
            "mission_path": str((root / "mission.md").resolve()),
            "mission_sha256": "a" * 64,
            "profile_path": str((root / ".codex/project-capabilities.json").resolve()),
            "profile_sha256": "b" * 64,
            "head_oid": "c" * 40,
            "required_reads": required_reads,
        },
        "paths": {
            "read_roots": [str(root.resolve())],
            "write_roots": [str((root / "src").resolve())] if access in {"bounded-write", "control-write"} else [],
            "read_deny_roots": [str((root / ".git").resolve())],
            "write_deny_roots": [
                str((root / ".git").resolve()),
                str((root / "AGENTS.md").resolve()),
                str((root / ".codex/project-capabilities.json").resolve()),
            ],
        },
        "commands": {"mode": "none", "rules": []},
        "git": {
            "head_policy": "unchanged",
            "index_policy": "unchanged",
            "protected_refs": ["refs/heads/main", "refs/heads/master"],
            "push_policy": "forbidden",
        },
        "topology": {"kind": "single", "nesting": "forbidden"},
        "external_actions": [],
    }


def run_guard(state: Path, root: Path, token: str, actions):
    completed = subprocess.run(
        ["node", str(DRIVER_PATH), str(GUARD_PATH), str(state), str(root), token, json.dumps(actions)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_read_only_workspace_allows_reads_and_denies_every_mutation(tmp_path: Path) -> None:
    lease_module = load_lease()
    project = tmp_path / "project"
    state = tmp_path / "state"
    (project / "src").mkdir(parents=True)
    (project / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    lease = lease_module.acquire_lease(capability(project, "read-only"), state, ["solver"])

    results = run_guard(
        state,
        project,
        lease.tokens["solver"],
        [
            {"kind": "open", "workspaceId": "ws-read"},
            {"kind": "read", "workspaceId": "ws-read", "path": "src/app.py"},
            {"kind": "read", "workspaceId": "ws-read", "path": ".git/config"},
            {"kind": "write", "workspaceId": "ws-read", "path": "src/app.py"},
            {"kind": "patch", "workspaceId": "ws-read", "actions": [{"kind": "update", "path": "src/app.py"}]},
            {"kind": "command", "workspaceId": "ws-read", "command": "git status"},
        ],
    )

    assert results == [
        {"ok": True, "access": "read-only", "subjectId": "solver"},
        {"ok": True},
        {"ok": False, "code": "CAPABILITY_READ_FORBIDDEN"},
        {"ok": False, "code": "CAPABILITY_WRITE_FORBIDDEN"},
        {"ok": False, "code": "CAPABILITY_WRITE_FORBIDDEN"},
        {"ok": False, "code": "CAPABILITY_COMMAND_FORBIDDEN"},
    ]


def test_bounded_workspace_checks_each_write_and_patch_move_path(tmp_path: Path) -> None:
    lease_module = load_lease()
    project = tmp_path / "project"
    state = tmp_path / "state"
    (project / "src").mkdir(parents=True)
    (project / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "mission.md").write_text("change src only\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    lease = lease_module.acquire_lease(capability(project, "bounded-write"), state, ["pro"])

    results = run_guard(
        state,
        project,
        lease.tokens["pro"],
        [
            {"kind": "open", "workspaceId": "ws-write"},
            {"kind": "read", "workspaceId": "ws-write", "path": "mission.md"},
            {"kind": "write", "workspaceId": "ws-write", "path": "src/app.py"},
            {"kind": "write", "workspaceId": "ws-write", "path": "README.md"},
            {"kind": "write", "workspaceId": "ws-write", "path": "AGENTS.md"},
            {
                "kind": "patch",
                "workspaceId": "ws-write",
                "actions": [{"kind": "update", "path": "src/app.py", "moveTo": "README.md"}],
            },
        ],
    )

    assert [item["code"] for item in results[3:]] == [
        "CAPABILITY_WRITE_OUT_OF_SCOPE",
        "CAPABILITY_PATH_FORBIDDEN",
        "CAPABILITY_WRITE_OUT_OF_SCOPE",
    ]
    assert results[:3] == [
        {"ok": True, "access": "bounded-write", "subjectId": "pro"},
        {"ok": True},
        {"ok": True},
    ]


def test_control_write_allows_exact_host_output_but_no_other_project_write(tmp_path: Path) -> None:
    lease_module = load_lease()
    project = tmp_path / "project"
    state = tmp_path / "state"
    (project / "src").mkdir(parents=True)
    (project / "mission.md").write_text("write the receipt\n", encoding="utf-8")
    contract = capability(project, "control-write")
    lease = lease_module.acquire_lease(contract, state, ["oracle"])

    results = run_guard(
        state,
        project,
        lease.tokens["oracle"],
        [
            {"kind": "open", "workspaceId": "ws-control"},
            {"kind": "read", "workspaceId": "ws-control", "path": "mission.md"},
            {"kind": "write", "workspaceId": "ws-control", "path": "src/result.json"},
            {"kind": "write", "workspaceId": "ws-control", "path": "README.md"},
            {"kind": "command", "workspaceId": "ws-control", "command": "git status"},
        ],
    )

    assert results == [
        {"ok": True, "access": "control-write", "subjectId": "oracle"},
        {"ok": True},
        {"ok": True},
        {"ok": False, "code": "CAPABILITY_WRITE_OUT_OF_SCOPE"},
        {"ok": False, "code": "CAPABILITY_COMMAND_FORBIDDEN"},
    ]


def test_writer_requires_successful_exact_read_and_rechecks_bytes_before_every_write(
    tmp_path: Path,
) -> None:
    lease_module = load_lease()
    project = tmp_path / "project"
    state = tmp_path / "state"
    (project / "src").mkdir(parents=True)
    (project / "mission.md").write_text("authorized mission\n", encoding="utf-8")
    lease = lease_module.acquire_lease(capability(project, "bounded-write"), state, ["pro"])

    results = run_guard(
        state,
        project,
        lease.tokens["pro"],
        [
            {"kind": "open", "workspaceId": "ws-attested"},
            {"kind": "write", "workspaceId": "ws-attested", "path": "src/app.py"},
            {"kind": "read", "workspaceId": "ws-attested", "path": "mission.md"},
            {"kind": "write", "workspaceId": "ws-attested", "path": "src/app.py"},
            {"kind": "hostWrite", "path": "mission.md", "content": "changed mission\n"},
            {"kind": "write", "workspaceId": "ws-attested", "path": "src/app.py"},
        ],
    )

    assert results == [
        {"ok": True, "access": "bounded-write", "subjectId": "pro"},
        {"ok": False, "code": "CAPABILITY_ENTRY_ATTESTATION_REQUIRED"},
        {"ok": True},
        {"ok": True},
        {"ok": True},
        {"ok": False, "code": "CAPABILITY_ENTRY_CHANGED"},
    ]


def test_token_open_is_bounded_and_unbound_or_quarantined_workspace_fails(tmp_path: Path) -> None:
    lease_module = load_lease()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    lease = lease_module.acquire_lease(capability(project, "read-only"), state, ["solver"])

    results = run_guard(
        state,
        project,
        lease.tokens["solver"],
        [
            {"kind": "read", "workspaceId": "unbound", "path": "README.md"},
            {"kind": "open", "workspaceId": "ws-1"},
            {"kind": "open", "workspaceId": "ws-2"},
            {"kind": "open", "workspaceId": "ws-3"},
            {"kind": "open", "workspaceId": "ws-worktree", "mode": "worktree"},
        ],
    )
    assert [item.get("code") for item in results] == [
        "CAPABILITY_WORKSPACE_BINDING_REQUIRED",
        None,
        None,
        "CAPABILITY_OPEN_RETRY_EXHAUSTED",
        "CAPABILITY_WORKTREE_FORBIDDEN",
    ]

    payload = json.loads(lease.path.read_text(encoding="utf-8"))
    payload["state"] = "quarantined"
    lease.path.write_text(json.dumps(payload), encoding="utf-8")
    quarantined = run_guard(
        state,
        project,
        lease.tokens["solver"],
        [{"kind": "open", "workspaceId": "ws-q"}],
    )
    assert quarantined == [{"ok": False, "code": "CAPABILITY_LEASE_UNRESOLVED"}]


def test_web_multi_lane_cannot_read_host_handoffs_but_exact_merger_can(tmp_path: Path) -> None:
    lease_module = load_lease()
    project = tmp_path / "project"
    state = tmp_path / "state"
    control = project / ".ai-bridge/web-multi"
    control.mkdir(parents=True)
    (control / "handoff.md").write_text("lane output\n", encoding="utf-8")
    (project / "handoff-alias").symlink_to(control, target_is_directory=True)
    contract = capability(project, "read-only")
    contract["subjects"] = {
        "lanes": [{"id": "lane-one", "read_deny_roots": [str(control.resolve())]}],
        "merger": {"id": "merger", "read_deny_roots": []},
    }
    lease = lease_module.acquire_lease(contract, state, ["lane-one", "merger"])

    lane = run_guard(
        state,
        project,
        lease.tokens["lane-one"],
        [
            {"kind": "open", "workspaceId": "lane"},
            {"kind": "read", "workspaceId": "lane", "path": ".ai-bridge/web-multi/handoff.md"},
            {"kind": "read", "workspaceId": "lane", "path": "handoff-alias/handoff.md"},
        ],
    )
    merger = run_guard(
        state,
        project,
        lease.tokens["merger"],
        [
            {"kind": "open", "workspaceId": "merger"},
            {"kind": "read", "workspaceId": "merger", "path": ".ai-bridge/web-multi/handoff.md"},
        ],
    )

    assert lane[1] == {"ok": False, "code": "CAPABILITY_READ_FORBIDDEN"}
    assert lane[2] == {"ok": False, "code": "CAPABILITY_READ_FORBIDDEN"}
    assert merger[1] == {"ok": True}


def test_recursive_reads_reject_scopes_that_contain_subject_denies(tmp_path: Path) -> None:
    lease_module = load_lease()
    project = tmp_path / "project"
    state = tmp_path / "state"
    control = project / ".ai-bridge" / "web-multi"
    source = project / "src"
    control.mkdir(parents=True)
    source.mkdir()
    (control / "handoff.md").write_text("private lane handoff\n", encoding="utf-8")
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    contract = capability(project, "read-only")
    contract["subjects"] = {
        "lanes": [{"id": "lane-one", "read_deny_roots": [str(control.resolve())]}],
        "merger": {"id": "merger", "read_deny_roots": []},
    }
    lease = lease_module.acquire_lease(contract, state, ["lane-one", "merger"])

    results = run_guard(
        state,
        project,
        lease.tokens["lane-one"],
        [
            {"kind": "open", "workspaceId": "lane"},
            {"kind": "recursiveRead", "workspaceId": "lane", "path": "."},
            {"kind": "recursiveRead", "workspaceId": "lane", "path": "src"},
        ],
    )

    assert results[1] == {"ok": False, "code": "CAPABILITY_READ_FORBIDDEN"}
    assert results[2] == {"ok": True}


def test_capability_review_is_forbidden_and_control_files_remain_immutable(
    tmp_path: Path,
) -> None:
    lease_module = load_lease()
    project = tmp_path / "project"
    state = tmp_path / "state"
    source = project / "src"
    source.mkdir(parents=True)
    mission = project / "mission.md"
    authority = source / "authority.json"
    mission.write_text("change source only\n", encoding="utf-8")
    authority.write_text("{}\n", encoding="utf-8")
    contract = capability(project, "bounded-write")
    binding = contract["binding"]
    paths = contract["paths"]
    assert isinstance(binding, dict)
    assert isinstance(paths, dict)
    binding["authority_path"] = str(authority.resolve())
    denied = paths["write_deny_roots"]
    assert isinstance(denied, list)
    denied.extend((str(mission.resolve()), str(authority.resolve())))
    lease = lease_module.acquire_lease(contract, state, ["pro"])

    results = run_guard(
        state,
        project,
        lease.tokens["pro"],
        [
            {"kind": "open", "workspaceId": "pro"},
            {"kind": "read", "workspaceId": "pro", "path": "mission.md"},
            {"kind": "write", "workspaceId": "pro", "path": "mission.md"},
            {"kind": "write", "workspaceId": "pro", "path": "src/authority.json"},
            {"kind": "write", "workspaceId": "pro", "path": "src/app.py"},
            {"kind": "review", "workspaceId": "pro"},
        ],
    )

    assert results[2:4] == [
        {"ok": False, "code": "CAPABILITY_PATH_FORBIDDEN"},
        {"ok": False, "code": "CAPABILITY_PATH_FORBIDDEN"},
    ]
    assert results[4] == {"ok": True}
    assert results[5] == {"ok": False, "code": "CAPABILITY_REVIEW_FORBIDDEN"}
