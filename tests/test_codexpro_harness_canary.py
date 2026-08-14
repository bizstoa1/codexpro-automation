from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "run_harness_canary.py"
    spec = importlib.util.spec_from_file_location("run_harness_canary_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compressed_canary_proves_status_audit_without_termination(tmp_path: Path) -> None:
    module = load_module()
    result = module.run_canary(state_root=tmp_path / "state", real_time=False)

    assert result["ok"] is True
    assert [item["phase"] for item in result["timeline"]] == ["RUNNING", "RUNNING", "RUNNING"]
    audit = result["timeline"][1]["status_audit"]
    assert audit["threshold_kind"] == "caution-status-audit"
    assert audit["time_alone_is_terminal"] is False
    assert audit["new_submission_authorized"] is False
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["handoff_sha256"] == result["handoff_sha256"]
    assert receipt["final_phase"] == "RUNNING"
