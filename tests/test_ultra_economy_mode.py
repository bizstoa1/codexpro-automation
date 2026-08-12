from __future__ import annotations

import importlib.util
import json
import os
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
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str(
        (tmp_path.parent / f"{tmp_path.name}-host").resolve()
    )
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
        "max_stages": 4,
        "local_gate_command": ["python", "-c", "raise SystemExit(0)"],
    }), encoding="utf-8")
    return path


def test_ultra_economy_skill_has_fail_closed_current_runtime_gate() -> None:
    text = SKILL.read_text(encoding="utf-8")
    compact = " ".join(text.split())
    assert "current task runtime" in text
    assert "Do not infer them from `~/.codex/config.toml`" in text
    assert "gpt-5.6-luna" in text and "`max`" in text
    assert "stop before creating a subagent, browser, Oracle, Pro, or web session" in compact
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


def test_ultra_economy_runtime_rejects_non_luna_or_non_max(tmp_path: Path, monkeypatch) -> None:
    module = load_comprehensive()
    path = workflow_manifest(tmp_path)
    monkeypatch.setattr(module.RUNTIME_IDENTITY, "current_runtime_identity", lambda: {
        "model": "gpt-5.6-sol", "reasoning_effort": "max"
    })
    with pytest.raises(module.WorkflowError, match="ULTRA_ECONOMY_MAIN_MODEL_REQUIRED"):
        module.run_workflow(path, dry_run=True)


def test_ultra_economy_runtime_fails_before_state_when_identity_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    module = load_comprehensive()
    path = workflow_manifest(tmp_path)

    def unavailable():
        raise module.RUNTIME_IDENTITY.RuntimeIdentityError("matching Codex runtime turn context is unavailable")

    monkeypatch.setattr(module.RUNTIME_IDENTITY, "current_runtime_identity", unavailable)
    with pytest.raises(module.WorkflowError, match="ULTRA_ECONOMY_MAIN_MODEL_UNVERIFIED"):
        module.run_workflow(path, dry_run=True)
    assert not (tmp_path / "workflow").exists()
    monkeypatch.setattr(module.RUNTIME_IDENTITY, "current_runtime_identity", lambda: {
        "model": "gpt-5.6-luna", "reasoning_effort": "high"
    })
    with pytest.raises(module.WorkflowError, match="ULTRA_ECONOMY_MAIN_MODEL_REQUIRED"):
        module.run_workflow(path, dry_run=True)


def test_ultra_economy_runtime_dry_run_is_pro_first_and_read_only(tmp_path: Path, monkeypatch) -> None:
    module = load_comprehensive()
    monkeypatch.setattr(module.RUNTIME_IDENTITY, "current_runtime_identity", lambda: {
        "model": "gpt-5.6-luna", "reasoning_effort": "max"
    })
    seen: dict[str, object] = {}

    def preview(path: Path, *, dry_run: bool):
        assert dry_run is True
        seen.update(json.loads(path.read_text(encoding="utf-8")))
        mission = Path(str(seen["mission_path"])).read_text(encoding="utf-8")
        assert "[ULTRA_ECONOMY_DESIGN_CONTRACT]" in mission
        return {"ok": True}

    result = module.run_workflow(workflow_manifest(tmp_path), dry_run=True, oracle_execute=preview)
    assert result["stage"] == "pro"
    assert seen["transport"] == "pro-devspace-readonly"
    assert seen["thinking_time"] == "heavy"
