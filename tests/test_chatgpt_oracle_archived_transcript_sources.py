from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "test_chatgpt_oracle_archived_settlement.py"


def load_fixtures():
    name = "chatgpt_oracle_archived_transcript_source_fixtures"
    spec = importlib.util.spec_from_file_location(name, FIXTURE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_run_local_recovery_transcript_is_independently_hash_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = load_fixtures()
    module = fixtures.load_module()
    manifest, state_path, run_dir = fixtures.fixture(tmp_path, monkeypatch)
    state = json.loads(state_path.read_text())
    recovery_transcript = Path(state["artifacts"]["transcript"])
    recovery_transcript.write_text("changed recovery evidence\n", encoding="utf-8")

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)

    assert exc.value.code == "HASH_MISMATCH"
    assert not (run_dir / "output.md").exists()


def test_only_canonical_regular_archived_transcript_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = load_fixtures()
    module = fixtures.load_module()
    manifest, _, run_dir = fixtures.fixture(tmp_path, monkeypatch)
    value = json.loads(manifest.read_text())
    archived_transcript = Path(value["transcript_path"])
    outside = tmp_path / "external-archive.md"
    outside.write_bytes(archived_transcript.read_bytes())
    value.update({"transcript_path": str(outside), "transcript_sha256": fixtures.sha(outside)})
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)

    assert exc.value.code == "ARCHIVED_TRANSCRIPT_PATH_INVALID"
    assert not (run_dir / "output.md").exists()

    archived_transcript.unlink()
    archived_transcript.symlink_to(outside)
    value.update({
        "transcript_path": str(archived_transcript),
        "transcript_sha256": fixtures.sha(archived_transcript),
    })
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)
    assert exc.value.code == "REGULAR_FILE_REQUIRED"


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("outside", "PROJECT_CONTAINMENT_REQUIRED"),
        ("symlink", "REGULAR_FILE_REQUIRED"),
    ],
)
def test_recovery_transcript_must_remain_a_run_local_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    code: str,
) -> None:
    fixtures = load_fixtures()
    module = fixtures.load_module()
    manifest, state_path, run_dir = fixtures.fixture(tmp_path, monkeypatch)
    value = json.loads(manifest.read_text())
    state = json.loads(state_path.read_text())
    recovery_transcript = Path(state["artifacts"]["transcript"])
    outside = tmp_path / "external-recovery.md"
    outside.write_bytes(recovery_transcript.read_bytes())
    if mode == "outside":
        state["artifacts"]["transcript"] = str(outside)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        value["state_sha256"] = fixtures.sha(state_path)
        value["recovery_transcript_sha256"] = fixtures.sha(outside)
    else:
        recovery_transcript.unlink()
        recovery_transcript.symlink_to(outside)
        value["recovery_transcript_sha256"] = fixtures.sha(recovery_transcript)
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)

    assert exc.value.code == code
    assert not (run_dir / "output.md").exists()
