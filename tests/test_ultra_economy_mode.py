from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ultra-economy-mode" / "SKILL.md"
UI = ROOT / "skills" / "ultra-economy-mode" / "agents" / "openai.yaml"


def load_comprehensive():
    path = ROOT / "bin" / "chatgpt_oracle_comprehensive.py"
    spec = importlib.util.spec_from_file_location("ultra_economy_comprehensive_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def workflow_manifest(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "*.md\n*.json\nworkflow/\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", "-b", "codex/ultra-economy-test", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Ultra Economy Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "ultra-economy@example.test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore", "src/app.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "baseline"], check=True)
    state_root = (tmp_path.parent / f"{tmp_path.name}-host").resolve()
    profile_seed = (tmp_path.parent / f"{tmp_path.name}-oracle-profile-seed").resolve()
    profile_seed.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    policy_path = state_root / "host-policy.json"
    policy_path.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-host-policy/v1",
        "profile_seed": str(profile_seed),
        "profile_mode": "copy-per-run",
        "max_total_concurrency": 5,
    }), encoding="utf-8")
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str(state_root)
    os.environ["CODEX_ORACLE_HOST_POLICY"] = str(policy_path)
    capability_state = state_root / "capabilities"
    os.environ["CODEX_CAPABILITY_STATE_ROOT"] = str(capability_state)
    profile = tmp_path / ".codex/project-capabilities.json"
    profile.parent.mkdir(exist_ok=True)
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
            "max_lanes": 25,
            "max_concurrency": 5,
            "all_lanes_required": True,
            "merger_policy": "exactly-one",
            "nesting": "forbidden",
        },
        "protected_branches": ["main", "master"],
        "write_deny_paths": [".git", ".codex", ".ai-bridge", "AGENTS.md"],
        "external_actions": "deny",
    }), encoding="utf-8")
    capability_state.mkdir(parents=True, exist_ok=True)
    (capability_state / "host-policy.json").write_text(json.dumps({
        "schema": "codex.chatgpt.capability-host-policy/v1",
        "max_web_multi_concurrency": 5,
        "external_actions": "deny",
        "projects": [{
            "project_root": str(tmp_path.resolve()),
            "profile_path": str(profile.resolve()),
            "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
            "pro": True,
            "oracle_read": True,
            "web_multi": True,
        }],
    }), encoding="utf-8")
    mission = tmp_path / "mission.md"
    mission.write_text("Design the implementation.", encoding="utf-8")
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-comprehensive/v1",
        "workflow_id": "a" * 32,
        "workflow_profile": "ultra-economy",
        "initial_stage": "pro",
        "project_root": str(tmp_path.resolve()),
        "workflow_dir": str((tmp_path / "workflow").resolve()),
        "initial_mission_path": str(mission.resolve()),
        "app_name": "codex",
        "model": "gpt-5.6",
        "pro_write_paths": ["src"],
        "max_stages": 4,
        "local_gate_command": ["python", "-c", "raise SystemExit(0)"],
    }), encoding="utf-8")
    return path


def test_ultra_economy_skill_has_one_time_user_activation_handshake() -> None:
    text = SKILL.read_text(encoding="utf-8")
    compact = " ".join(text.split())
    assert "first" in text and "exactly one concise instruction" in compact
    assert "gpt-5.6-luna" in text and "`max`" in text
    assert "Do not inspect, infer, or verify" in text
    assert "including after compaction, recovery, stage transitions" in compact
    assert "asking again" in text
    assert "Never rewrite the user's global model defaults" in text


def test_ultra_economy_skill_forces_fresh_luna_max_workers_and_web_stages() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "fresh `default`" in text
    assert "Do not use the globally configured scout" in text
    assert "qualified Pro design" in text
    assert "regular web implementation" in text
    assert "separate regular web final verification" in text
    assert "zero-exit local" in text


def test_ultra_economy_skill_ui_metadata_is_discoverable() -> None:
    text = UI.read_text(encoding="utf-8")
    assert 'display_name: "Ultra Economy Mode"' in text
    assert "$ultra-economy-mode" in text


def test_ultra_economy_runtime_does_not_reinspect_model_or_reasoning(tmp_path: Path, monkeypatch) -> None:
    module = load_comprehensive()
    path = workflow_manifest(tmp_path)
    assert not hasattr(module, "RUNTIME_IDENTITY")

    seen: dict[str, object] = {}

    def preview(oracle_manifest: Path, *, dry_run: bool, capability_token: str | None = None):
        assert capability_token is None
        seen.update(json.loads(oracle_manifest.read_text(encoding="utf-8")))
        return {"ok": True}

    result = module.run_workflow(path, dry_run=True, oracle_execute=preview)
    assert result["stage"] == "pro"
    assert seen["transport"] == "pro-devspace"


def test_ultra_economy_runtime_dry_run_is_pro_first_and_writable(tmp_path: Path, monkeypatch) -> None:
    module = load_comprehensive()
    seen: dict[str, object] = {}

    def preview(path: Path, *, dry_run: bool, capability_token: str | None = None):
        assert dry_run is True
        assert capability_token is None
        seen.update(json.loads(path.read_text(encoding="utf-8")))
        mission = Path(str(seen["mission_path"])).read_text(encoding="utf-8")
        assert "[ULTRA_ECONOMY_DESIGN_CONTRACT]" in mission
        return {"ok": True}

    result = module.run_workflow(workflow_manifest(tmp_path), dry_run=True, oracle_execute=preview)
    assert result["stage"] == "pro"
    assert seen["transport"] == "pro-devspace"
    assert seen["thinking_time"] == "heavy"
