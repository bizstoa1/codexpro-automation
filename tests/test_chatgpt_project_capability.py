from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_project_capability.py"


def load():
    spec = importlib.util.spec_from_file_location("project_capability_test", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def initialize_repository(root: Path, branch: str = "codex/capability-test") -> None:
    subprocess.run(["git", "init", "-q", "-b", branch, str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Capability Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "capability@example.test"], check=True)
    (root / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "test baseline"], check=True)


def write_profile(root: Path, *, pro_enabled: bool = True, web_multi_enabled: bool = True) -> Path:
    profile = root / ".codex" / "project-capabilities.json"
    profile.parent.mkdir()
    profile.write_text(
        json.dumps(
            {
                "schema": "codex.chatgpt.project-capability-profile/v1",
                "pro": {
                    "enabled": pro_enabled,
                    "write_root_ceiling": ["src", "docs/capability-canary.md"],
                    "commands": "none",
                    "require_clean_git": True,
                    "require_nonprotected_branch": True,
                },
                "web_multi": {
                    "enabled": web_multi_enabled,
                    "access": "read-only",
                    "min_lanes": 2,
                    "max_lanes": 25,
                    "max_concurrency": 5,
                    "all_lanes_required": True,
                    "merger_policy": "exactly-one",
                    "nesting": "forbidden",
                },
                "protected_branches": ["main", "master"],
                "write_deny_paths": [
                    ".git",
                    ".codex",
                    "AGENTS.md",
                    ".ai-bridge",
                ],
                "external_actions": "deny",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return profile


def write_authority(root: Path, mission: Path, paths: list[str]) -> Path:
    authority = root / ".ai-bridge" / "mission-authority.json"
    authority.parent.mkdir(exist_ok=True)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    authority.write_text(
        json.dumps(
            {
                "schema": "codex.chatgpt.pro-mission-authority/v1",
                "project_root": str(root.resolve()),
                "mission_path": str(mission.resolve()),
                "mission_sha256": sha256(mission),
                "expected_head": head,
                "allowed_write_paths": paths,
                "allowed_command_ids": [],
                "external_actions": "deny",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return authority


def test_profile_is_required_and_unknown_fields_fail_closed(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    mission = tmp_path / "mission.md"
    mission.write_text("work\n", encoding="utf-8")

    with pytest.raises(module.CapabilityError) as missing:
        module.compile_pro_contract(tmp_path, mission, tmp_path / "missing.json")
    assert missing.value.code == "CAPABILITY_PROFILE_REQUIRED"

    profile = write_profile(tmp_path)
    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    profile.write_text(json.dumps(payload), encoding="utf-8")
    authority = write_authority(tmp_path, mission, ["src"])

    with pytest.raises(module.CapabilityError) as invalid:
        module.compile_pro_contract(tmp_path, mission, authority)
    assert invalid.value.code == "CAPABILITY_SCHEMA_INVALID"


def test_pro_contract_binds_exact_root_mission_head_and_write_ceiling(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    (tmp_path / "src").mkdir()
    mission = tmp_path / "mission.md"
    mission.write_text("write only src\n", encoding="utf-8")
    profile = write_profile(tmp_path)
    authority = write_authority(tmp_path, mission, ["src"])

    contract = module.compile_pro_contract(tmp_path, mission, authority).as_dict()

    assert contract["schema"] == "codex.chatgpt.project-capability/v1"
    assert contract["actor"] == "pro"
    assert contract["access"] == "bounded-write"
    assert contract["binding"]["project_root"] == str(tmp_path.resolve())
    assert contract["binding"]["mission_sha256"] == sha256(mission)
    assert contract["binding"]["profile_sha256"] == sha256(profile)
    assert contract["binding"]["authority_path"] == str(authority.resolve())
    assert contract["binding"]["authority_sha256"] == sha256(authority)
    assert contract["binding"]["required_reads"] == [
        {"path": str(mission.resolve()), "sha256": sha256(mission)}
    ]
    assert contract["paths"]["write_roots"] == [str((tmp_path / "src").resolve())]
    assert str((tmp_path / ".git").resolve()) in contract["paths"]["read_deny_roots"]
    assert contract["commands"] == {"mode": "none", "rules": []}
    assert contract["git"]["head_policy"] == "unchanged"
    assert contract["git"]["index_policy"] == "unchanged"
    assert contract["git"]["push_policy"] == "forbidden"
    assert contract["external_actions"] == []


def test_pro_contract_write_denies_exact_mission_and_authority_under_source(
    tmp_path: Path,
) -> None:
    module = load()
    initialize_repository(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    mission = source / "mission.md"
    mission.write_text("change source but preserve controls\n", encoding="utf-8")
    write_profile(tmp_path)
    original_authority = write_authority(tmp_path, mission, ["src"])
    authority = source / "authority.json"
    original_authority.replace(authority)

    contract = module.compile_pro_contract(tmp_path, mission, authority).as_dict()

    denies = contract["paths"]["write_deny_roots"]
    assert str(mission.resolve()) in denies
    assert str(authority.resolve()) in denies


def test_pro_clean_gate_treats_metacharacter_mission_as_literal(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    mission = tmp_path / "[x]"
    mission.write_text("literal filename\n", encoding="utf-8")
    write_profile(tmp_path)
    authority = write_authority(tmp_path, mission, ["src"])
    (tmp_path / "x").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(module.CapabilityError) as dirty:
        module.compile_pro_contract(tmp_path, mission, authority)

    assert dirty.value.code == "CAPABILITY_GIT_BASELINE_DIRTY"


def test_pro_rejects_out_of_ceiling_denied_dirty_and_protected_branch(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    mission = tmp_path / "mission.md"
    mission.write_text("work\n", encoding="utf-8")
    write_profile(tmp_path)

    outside = write_authority(tmp_path, mission, ["secrets"])
    with pytest.raises(module.CapabilityError) as scope:
        module.compile_pro_contract(tmp_path, mission, outside)
    assert scope.value.code == "CAPABILITY_WRITE_OUT_OF_SCOPE"

    denied = write_authority(tmp_path, mission, ["AGENTS.md"])
    with pytest.raises(module.CapabilityError) as forbidden:
        module.compile_pro_contract(tmp_path, mission, denied)
    assert forbidden.value.code == "CAPABILITY_PATH_FORBIDDEN"

    clean = write_authority(tmp_path, mission, ["src"])
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(module.CapabilityError) as dirty:
        module.compile_pro_contract(tmp_path, mission, clean)
    assert dirty.value.code == "CAPABILITY_GIT_BASELINE_DIRTY"

    (tmp_path / "dirty.txt").unlink()
    subprocess.run(["git", "-C", str(tmp_path), "branch", "-m", "main"], check=True)
    with pytest.raises(module.CapabilityError) as branch:
        module.compile_pro_contract(tmp_path, mission, clean)
    assert branch.value.code == "CAPABILITY_PROTECTED_BRANCH"


def test_pro_rejects_changed_mission_and_authority_head(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    mission = tmp_path / "mission.md"
    mission.write_text("before\n", encoding="utf-8")
    write_profile(tmp_path)
    authority = write_authority(tmp_path, mission, ["src"])
    mission.write_text("after\n", encoding="utf-8")

    with pytest.raises(module.CapabilityError) as changed:
        module.compile_pro_contract(tmp_path, mission, authority)
    assert changed.value.code == "CAPABILITY_MISSION_CHANGED"

    mission.write_text("before\n", encoding="utf-8")
    payload = json.loads(authority.read_text(encoding="utf-8"))
    payload["expected_head"] = "0" * 40
    authority.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.CapabilityError) as head:
        module.compile_pro_contract(tmp_path, mission, authority)
    assert head.value.code == "CAPABILITY_GIT_BASELINE_DRIFT"


def test_pro_rejects_missing_mandatory_denies_and_empty_or_duplicate_write_scope(
    tmp_path: Path,
) -> None:
    module = load()
    initialize_repository(tmp_path)
    mission = tmp_path / "mission.md"
    mission.write_text("work\n", encoding="utf-8")
    profile = write_profile(tmp_path)
    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["write_deny_paths"].remove(".git")
    profile.write_text(json.dumps(payload), encoding="utf-8")
    authority = write_authority(tmp_path, mission, ["src"])

    with pytest.raises(module.CapabilityError) as unsafe_profile:
        module.compile_pro_contract(tmp_path, mission, authority)
    assert unsafe_profile.value.code == "CAPABILITY_SCHEMA_INVALID"

    write_profile_payload = json.loads(profile.read_text(encoding="utf-8"))
    write_profile_payload["write_deny_paths"].append(".git")
    profile.write_text(json.dumps(write_profile_payload), encoding="utf-8")
    empty = write_authority(tmp_path, mission, [])
    with pytest.raises(module.CapabilityError) as empty_scope:
        module.compile_pro_contract(tmp_path, mission, empty)
    assert empty_scope.value.code == "CAPABILITY_SCHEMA_INVALID"

    duplicate = write_authority(tmp_path, mission, ["src", "src"])
    with pytest.raises(module.CapabilityError) as duplicate_scope:
        module.compile_pro_contract(tmp_path, mission, duplicate)
    assert duplicate_scope.value.code == "CAPABILITY_SCHEMA_INVALID"


def test_pro_requires_and_write_protects_every_applicable_agents_file(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    (tmp_path / "AGENTS.md").write_text("root rules\n", encoding="utf-8")
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    (nested / "AGENTS.md").write_text("feature rules\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "AGENTS.md", "src/feature/AGENTS.md"],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "add instructions"], check=True)
    mission = tmp_path / "mission.md"
    mission.write_text("implement feature\n", encoding="utf-8")
    write_profile(tmp_path)
    authority = write_authority(tmp_path, mission, ["src/feature"])

    contract = module.compile_pro_contract(tmp_path, mission, authority).as_dict()

    assert contract["binding"]["required_reads"] == [
        {"path": str(mission.resolve()), "sha256": sha256(mission)},
        {"path": str((tmp_path / "AGENTS.md").resolve()), "sha256": sha256(tmp_path / "AGENTS.md")},
        {"path": str((nested / "AGENTS.md").resolve()), "sha256": sha256(nested / "AGENTS.md")},
    ]
    assert str((nested / "AGENTS.md").resolve()) in contract["paths"]["write_deny_roots"]


def test_pro_rejects_mission_outside_exact_project_root(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    project.mkdir()
    initialize_repository(project)
    write_profile(project)
    mission = tmp_path / "outside-mission.md"
    mission.write_text("outside\n", encoding="utf-8")
    authority = write_authority(project, mission, ["src"])

    with pytest.raises(module.CapabilityError) as outside:
        module.compile_pro_contract(project, mission, authority)

    assert outside.value.code == "CAPABILITY_ROOT_MISMATCH"


def test_web_multi_contract_is_read_only_all_of_n_and_non_nested(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    mission_paths = []
    for name in ("architecture", "security"):
        mission = tmp_path / f"{name}.md"
        mission.write_text(name + "\n", encoding="utf-8")
        mission_paths.append((name, mission))
    merger = tmp_path / "merger.md"
    merger.write_text("merge\n", encoding="utf-8")
    control_root = tmp_path / ".ai-bridge/web-multi"
    write_profile(tmp_path)

    contract = module.compile_web_multi_contract(
        tmp_path,
        mission_paths,
        merger,
        max_concurrency=2,
        control_root=control_root,
    ).as_dict()

    assert contract["actor"] == "web-multi"
    assert contract["access"] == "read-only"
    assert contract["paths"]["write_roots"] == []
    assert str((tmp_path / ".git").resolve()) in contract["paths"]["read_deny_roots"]
    assert contract["commands"] == {"mode": "none", "rules": []}
    assert contract["topology"]["completion_policy"] == "all-lanes"
    assert contract["topology"]["merger_policy"] == "exactly-one"
    assert contract["topology"]["nesting"] == "forbidden"
    assert contract["topology"]["max_provider_concurrency"] == 2
    assert contract["binding"]["host_control_paths"] == [str(control_root.resolve())]
    assert contract["subjects"]["lanes"] == [
        {"id": "architecture", "read_deny_roots": [str(control_root.resolve())]},
        {"id": "security", "read_deny_roots": [str(control_root.resolve())]},
    ]
    assert contract["subjects"]["merger"] == {"id": "merger", "read_deny_roots": []}


def test_web_multi_rejects_write_lanes_partial_policy_and_nested_parent(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    one = tmp_path / "one.md"
    two = tmp_path / "two.md"
    merger = tmp_path / "merger.md"
    for path in (one, two, merger):
        path.write_text(path.stem + "\n", encoding="utf-8")
    profile = write_profile(tmp_path)

    with pytest.raises(module.CapabilityError) as nested:
        module.compile_web_multi_contract(
            tmp_path,
            [("one", one), ("two", two)],
            merger,
            max_concurrency=2,
            parent_capability_id="a" * 64,
        )
    assert nested.value.code == "WEB_MULTI_NESTING_FORBIDDEN"

    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["web_multi"]["access"] = "worktree-write"
    profile.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.CapabilityError) as write_lane:
        module.compile_web_multi_contract(
            tmp_path,
            [("one", one), ("two", two)],
            merger,
            max_concurrency=2,
        )
    assert write_lane.value.code == "WEB_MULTI_WRITE_FORBIDDEN"


def test_single_oracle_contract_is_read_only_and_binds_mission_profile_and_head(
    tmp_path: Path,
) -> None:
    module = load()
    initialize_repository(tmp_path)
    mission = tmp_path / "mission.md"
    mission.write_text("analyze only\n", encoding="utf-8")
    profile = write_profile(tmp_path)

    contract = module.compile_read_only_contract(tmp_path, mission).as_dict()

    assert contract["actor"] == "oracle"
    assert contract["access"] == "read-only"
    assert contract["binding"]["mission_sha256"] == sha256(mission)
    assert contract["binding"]["profile_sha256"] == sha256(profile)
    assert contract["paths"]["write_roots"] == []
    assert contract["topology"] == {"kind": "single", "nesting": "forbidden"}


def test_read_only_oracle_accepts_preexisting_dirty_baseline(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    mission = tmp_path / "mission.md"
    mission.write_text("analyze only\n", encoding="utf-8")
    write_profile(tmp_path)
    (tmp_path / "preexisting-user-note.txt").write_text("keep\n", encoding="utf-8")

    contract = module.compile_read_only_contract(tmp_path, mission).as_dict()

    assert contract["access"] == "read-only"


def test_regular_oracle_control_write_is_limited_to_exact_host_stage(tmp_path: Path) -> None:
    module = load()
    initialize_repository(tmp_path)
    control = tmp_path / ".ai-bridge" / "runtime" / "stage-one"
    control.mkdir(parents=True)
    mission = control / "mission.md"
    mission.write_text("plan and write only the bound receipt\n", encoding="utf-8")
    write_profile(tmp_path)

    contract = module.compile_read_only_contract(
        tmp_path,
        mission,
        control_write_root=control,
    ).as_dict()

    assert contract["actor"] == "oracle"
    assert contract["access"] == "control-write"
    assert contract["paths"]["write_roots"] == [str(control.resolve())]
    assert str(mission.resolve()) in contract["paths"]["write_deny_roots"]
    assert str((tmp_path / ".ai-bridge/runtime").resolve()) not in contract["paths"]["write_deny_roots"]
    assert contract["binding"]["host_control_paths"] == [str(control.resolve())]
    assert contract["binding"]["required_reads"] == [
        {"path": str(mission.resolve()), "sha256": sha256(mission)}
    ]
