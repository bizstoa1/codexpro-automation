from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "test_chatgpt_oracle_archived_settlement.py"


def load_fixtures():
    name = "chatgpt_oracle_archived_settlement_cli_fixtures"
    spec = importlib.util.spec_from_file_location(name, FIXTURE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_settles_only_the_fixture_without_oracle_or_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = load_fixtures()
    module = fixtures.load_module()
    manifest, state_path, run_dir = fixtures.fixture(tmp_path, monkeypatch)

    completed = subprocess.run(
        [
            sys.executable,
            str(fixtures.MODULE_PATH),
            "--manifest",
            str(manifest),
            "--confirmation",
            module.CONFIRMATION_TOKEN,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "CODEX_ORACLE_STATE_ROOT": str(run_dir.parents[2])},
    )

    result = json.loads(completed.stdout)
    state = json.loads(state_path.read_text())
    assert completed.returncode == 0
    assert result["ok"] is True
    assert set(result["zero_action_counters"].values()) == {0}
    assert state["session_authority"] == "settled_executed"


def test_broken_settlement_symlink_blocks_before_output_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = load_fixtures()
    module = fixtures.load_module()
    manifest, _, run_dir = fixtures.fixture(tmp_path, monkeypatch)
    settlement = run_dir / "archived-exact-transcript-settlement.json"
    settlement.symlink_to(run_dir / "missing-receipt.json")

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)

    assert exc.value.code == "SETTLEMENT_MUST_BE_ABSENT"
    assert not (run_dir / "output.md").exists()
