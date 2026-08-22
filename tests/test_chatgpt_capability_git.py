from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "bin" / "chatgpt_capability_git.py"
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def load():
    spec = importlib.util.spec_from_file_location("capability_git_test", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def initialize(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "codex/capability-test", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Capability Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "capability@example.test"], check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)


def contract(root: Path, *, access: str = "bounded-write") -> dict[str, JsonValue]:
    mission = root / ".ai-bridge" / "mission.md"
    authority = root / ".ai-bridge" / "authority.json"
    profile = root / ".codex" / "project-capabilities.json"
    mission.parent.mkdir(exist_ok=True)
    profile.parent.mkdir(exist_ok=True)
    mission.write_text("mission\n", encoding="utf-8")
    authority.write_text("{}\n", encoding="utf-8")
    profile.write_text("{}\n", encoding="utf-8")
    return {
        "schema": "codex.chatgpt.project-capability/v1",
        "actor": "pro" if access == "bounded-write" else "web-multi",
        "access": access,
        "binding": {
            "project_root": str(root.resolve()),
            "mission_path": str(mission.resolve()),
            "authority_path": str(authority.resolve()),
            "profile_path": str(profile.resolve()),
        },
        "paths": {
            "read_roots": [str(root.resolve())],
            "write_roots": [str((root / "src").resolve())] if access == "bounded-write" else [],
            "write_deny_roots": [str((root / ".git").resolve()), str(profile.resolve())],
        },
        "git": {
            "head_policy": "unchanged",
            "index_policy": "unchanged",
            "protected_refs": ["refs/heads/main", "refs/heads/master"],
            "push_policy": "forbidden",
        },
    }


def test_bounded_write_postflight_accepts_only_authorized_paths(tmp_path: Path) -> None:
    module = load()
    initialize(tmp_path)
    capability = contract(tmp_path)
    baseline = module.capture_baseline(capability)

    (tmp_path / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    evidence = module.verify_postflight(capability, baseline)

    assert evidence["status"] == "passed"
    assert evidence["changed_paths"] == ["src/app.py"]
    assert evidence["head_unchanged"] is True
    assert evidence["index_unchanged"] is True


def test_postflight_rejects_out_of_scope_git_and_symlink_changes(tmp_path: Path) -> None:
    module = load()
    initialize(tmp_path)
    capability = contract(tmp_path)
    baseline = module.capture_baseline(capability)
    (tmp_path / "outside.txt").write_text("no\n", encoding="utf-8")

    with pytest.raises(module.CapabilityGitError) as outside:
        module.verify_postflight(capability, baseline)
    assert outside.value.code == "CAPABILITY_DIFF_OUT_OF_SCOPE"

    (tmp_path / "outside.txt").unlink()
    (tmp_path / "src" / "link").symlink_to(tmp_path / "README.md")
    with pytest.raises(module.CapabilityGitError) as symlink:
        module.verify_postflight(capability, baseline)
    assert symlink.value.code == "CAPABILITY_SYMLINK_FORBIDDEN"

    (tmp_path / "src" / "link").unlink()
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-qm", "drift"], check=True)
    with pytest.raises(module.CapabilityGitError) as head:
        module.verify_postflight(capability, baseline)
    assert head.value.code == "CAPABILITY_GIT_BASELINE_DRIFT"


def test_read_only_postflight_requires_exact_unchanged_status(tmp_path: Path) -> None:
    module = load()
    initialize(tmp_path)
    capability = contract(tmp_path, access="read-only")
    baseline = module.capture_baseline(capability)
    assert module.verify_postflight(capability, baseline)["changed_paths"] == []

    (tmp_path / "src" / "app.py").write_text("VALUE = 9\n", encoding="utf-8")
    with pytest.raises(module.CapabilityGitError) as changed:
        module.verify_postflight(capability, baseline)
    assert changed.value.code == "CAPABILITY_READ_ONLY_DRIFT"


def test_read_only_postflight_ignores_only_declared_host_control_paths(tmp_path: Path) -> None:
    module = load()
    initialize(tmp_path)
    capability = contract(tmp_path, access="read-only")
    control = tmp_path / ".ai-bridge/web-multi"
    binding = capability["binding"]
    assert isinstance(binding, dict)
    binding["host_control_paths"] = [str(control.resolve())]
    baseline = module.capture_baseline(capability)
    control.mkdir(parents=True)
    (control / "result.json").write_text("{}\n", encoding="utf-8")

    assert module.verify_postflight(capability, baseline)["changed_paths"] == []

    (tmp_path / "src/app.py").write_text("VALUE = 7\n", encoding="utf-8")
    with pytest.raises(module.CapabilityGitError) as changed:
        module.verify_postflight(capability, baseline)
    assert changed.value.code == "CAPABILITY_READ_ONLY_DRIFT"


def test_index_and_protected_ref_drift_are_rejected(tmp_path: Path) -> None:
    module = load()
    initialize(tmp_path)
    capability = contract(tmp_path)
    baseline = module.capture_baseline(capability)
    (tmp_path / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src/app.py"], check=True)
    with pytest.raises(module.CapabilityGitError) as index:
        module.verify_postflight(capability, baseline)
    assert index.value.code == "CAPABILITY_GIT_INDEX_CHANGED"

    subprocess.run(["git", "-C", str(tmp_path), "reset", "-q", "HEAD", "--", "src/app.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "branch", "main"], check=True)
    with pytest.raises(module.CapabilityGitError) as protected:
        module.verify_postflight(capability, baseline)
    assert protected.value.code == "CAPABILITY_PROTECTED_REF_CHANGED"


def test_ignored_metacharacter_filename_is_a_literal_git_pathspec(tmp_path: Path) -> None:
    module = load()
    initialize(tmp_path)
    value = contract(tmp_path)
    wildcard = tmp_path / "*"
    wildcard.write_text("mission\n", encoding="utf-8")
    binding = value["binding"]
    assert isinstance(binding, dict)
    binding["mission_path"] = str(wildcard.resolve())
    baseline = module.capture_baseline(value)
    (tmp_path / "outside.txt").write_text("must remain visible\n", encoding="utf-8")

    with pytest.raises(module.CapabilityGitError) as changed:
        module.verify_postflight(value, baseline)

    assert changed.value.code == "CAPABILITY_DIFF_OUT_OF_SCOPE"
