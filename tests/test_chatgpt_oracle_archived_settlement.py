from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "chatgpt_oracle_archived_settlement.py"
RUN_ID = "06e8991c2f1a449590b77a6553c649ba"
LOCATOR = "oracle-fixture-06e8991c2f"
sys.path.insert(0, str(ROOT / "bin"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module():
    name = "chatgpt_oracle_archived_settlement_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    state_root = tmp_path / "host-state"
    run_dir = state_root / "runs" / "project-key" / RUN_ID
    project = tmp_path / "project"
    test_home = tmp_path / "home"
    receipts = tmp_path / "codex-home" / "receipts"
    run_dir.mkdir(parents=True)
    project.mkdir()
    test_home.mkdir()
    receipts.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(test_home))
    monkeypatch.setenv("USERPROFILE", str(test_home))
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(state_root))
    monkeypatch.setenv("CODEX_HOME", str(receipts.parent))

    final_output = project / "workflow" / "stages" / "final" / "output.md"
    final_output.parent.mkdir(parents=True)
    answer = b"Archived exact answer.\n\nTASK_OUTCOME: EXECUTED\n"
    final_output.write_bytes(answer)
    recovery_transcript = run_dir / "transcript.md"
    recovery_transcript.write_bytes(b"Recovered tab detached.\nNode.js v26.5.0\n")
    archived_transcript = test_home / ".oracle" / "sessions" / LOCATOR / "artifacts" / "transcript.md"
    archived_transcript.parent.mkdir(parents=True)
    pass_receipt = final_output.parent / "stage-result.json"
    pass_receipt.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-stage-result/v1",
        "workflow_id": "a" * 32,
        "stage": "final-web-gate",
        "attempt_id": "b" * 32,
        "input_mission_sha256": "c" * 64,
        "status": "PASS",
        "output_path": str(final_output),
        "output_sha256": sha(final_output),
        "next_stage": "complete",
        "ready_for_next": True,
        "blocker": "",
    }), encoding="utf-8")
    archived_transcript.write_text(f"- Output: `{final_output}`\n- Output SHA-256: `{sha(final_output)}`\n- Receipt: `{pass_receipt}`\n- Receipt SHA-256: `{sha(pass_receipt)}`\n- status: PASS\n- next_stage: complete\n- ready_for_next: true\n\nTASK_OUTCOME: EXECUTED\n", encoding="utf-8")
    release_receipt = receipts / "codexpro-release-prior.json"
    release_receipt.write_text(json.dumps({
        "schema": "codexpro.install-release-receipt/v1",
        "release": {"commit": "d" * 40},
    }), encoding="utf-8")
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-run-state/v1",
        "run_id": RUN_ID,
        "project_root": str(project),
        "status": "attention_required",
        "session_authority": "submitted_unknown",
        "terminal_harvested": False,
        "transport_status": "incomplete",
        "task_outcome": "pending",
        "artifact_sha256": None,
        "exit_code": 1,
        "oracle": {"slug": LOCATOR, "session_locator": LOCATOR},
        "artifacts": {
            "output": str(run_dir / "output.md"),
            "transcript": str(recovery_transcript),
            "stdout": str(run_dir / "stdout.log"),
            "stderr": str(run_dir / "stderr.log"),
        },
    }), encoding="utf-8")
    manifest = project / "archived-settlement.json"
    manifest.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-archived-exact-settlement/v1",
        "exact_run_id": RUN_ID,
        "session_locator": LOCATOR,
        "project_root": str(project),
        "run_dir": str(run_dir),
        "state_sha256": sha(state_path),
        "transcript_path": str(archived_transcript),
        "transcript_sha256": sha(archived_transcript),
        "recovery_transcript_sha256": sha(recovery_transcript),
        "final_gate_output_path": str(final_output),
        "final_gate_output_sha256": sha(final_output),
        "pass_stage_receipt_path": str(pass_receipt),
        "pass_stage_receipt_sha256": sha(pass_receipt),
        "prior_runtime_release_receipt_path": str(release_receipt),
        "prior_runtime_release_receipt_sha256": sha(release_receipt),
    }), encoding="utf-8")
    return manifest, state_path, run_dir


def test_settlement_requires_separate_exact_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    manifest, state_path, run_dir = fixture(tmp_path, monkeypatch)

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation="ORACLE_ARCHIVED_EXACT_TRANSCRIPT_SETTLEMENT")

    assert exc.value.code == "CONFIRMATION_REQUIRED"
    assert not (run_dir / "output.md").exists()
    assert json.loads(state_path.read_text())["status"] == "attention_required"


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("state_sha256", "0" * 64, "HASH_MISMATCH"),
        ("exact_run_id", "f" * 32, "RUN_ID_MISMATCH"),
        ("session_locator", "oracle-other-12345678", "LOCATOR_MISMATCH"),
        ("transcript_sha256", "0" * 64, "HASH_MISMATCH"),
        ("recovery_transcript_sha256", "0" * 64, "HASH_MISMATCH"),
        ("final_gate_output_sha256", "0" * 64, "HASH_MISMATCH"),
        ("pass_stage_receipt_sha256", "0" * 64, "HASH_MISMATCH"),
        ("prior_runtime_release_receipt_sha256", "0" * 64, "HASH_MISMATCH"),
    ],
)
def test_every_exact_binding_is_revalidated_before_output_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
    code: str,
) -> None:
    module = load_module()
    manifest, _, run_dir = fixture(tmp_path, monkeypatch)
    value = json.loads(manifest.read_text())
    value[field] = replacement
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)

    assert exc.value.code == code
    assert not (run_dir / "output.md").exists()


def test_settlement_exclusively_materializes_final_gate_bytes_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    manifest, state_path, run_dir = fixture(tmp_path, monkeypatch)
    manifest_value = json.loads(manifest.read_text())
    final_output = Path(manifest_value["final_gate_output_path"])

    result = module.settle(
        manifest,
        confirmation=module.CONFIRMATION_TOKEN,
        process_alive=lambda _pid: False,
    )

    output = run_dir / "output.md"
    receipt_path = run_dir / "archived-exact-transcript-settlement.json"
    receipt = json.loads(receipt_path.read_text())
    state = json.loads(state_path.read_text())
    assert output.read_bytes() == final_output.read_bytes()
    assert receipt["bindings"]["state_sha256"] == manifest_value["state_sha256"]
    assert receipt["bindings"]["transcript_sha256"] == manifest_value["transcript_sha256"]
    assert receipt["bindings"]["recovery_transcript_sha256"] == manifest_value["recovery_transcript_sha256"]
    assert receipt["bindings"]["prior_runtime_release_receipt_sha256"] == manifest_value["prior_runtime_release_receipt_sha256"]
    assert set(receipt["zero_action_counters"].values()) == {0}
    assert receipt["active_pid_count"] == 0
    assert state["status"] == "complete"
    assert state["session_authority"] == "settled_executed"
    assert state["terminal_harvested"] is False
    assert state["task_outcome"] == "executed"
    assert state["artifact_sha256"] == sha(output)
    assert result["settlement_receipt_sha256"] == sha(receipt_path)

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)
    assert exc.value.code in {"HASH_MISMATCH", "OUTPUT_MUST_BE_ABSENT"}


def test_active_pid_and_invalid_pass_or_transcript_semantics_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    manifest, state_path, run_dir = fixture(tmp_path, monkeypatch)
    state = json.loads(state_path.read_text())
    state["host_watchdog"] = {"oracle_process_pid": 4242}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    value = json.loads(manifest.read_text())
    value["state_sha256"] = sha(state_path)
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(module.SettlementError) as exc:
        module.settle(
            manifest,
            confirmation=module.CONFIRMATION_TOKEN,
            process_alive=lambda pid: pid == 4242,
        )
    assert exc.value.code == "ACTIVE_PROCESS"
    assert not (run_dir / "output.md").exists()

    state.pop("host_watchdog")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    receipt = Path(value["pass_stage_receipt_path"])
    receipt_value = json.loads(receipt.read_text())
    receipt_value["ready_for_next"] = False
    receipt.write_text(json.dumps(receipt_value), encoding="utf-8")
    transcript = Path(value["transcript_path"])
    transcript.write_text(transcript.read_text().replace(value["pass_stage_receipt_sha256"], sha(receipt)), encoding="utf-8")
    value.update({"state_sha256": sha(state_path), "transcript_sha256": sha(transcript), "pass_stage_receipt_sha256": sha(receipt)})
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)
    assert exc.value.code == "PASS_RECEIPT_INVALID"

    receipt_value["ready_for_next"] = True
    receipt.write_text(json.dumps(receipt_value), encoding="utf-8")
    transcript = Path(value["transcript_path"])
    transcript.write_text("Archived answer without the bound final bytes.\n", encoding="utf-8")
    value.update({
        "pass_stage_receipt_sha256": sha(receipt),
        "transcript_sha256": sha(transcript),
    })
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)
    assert exc.value.code == "TRANSCRIPT_ANSWER_INVALID"


def test_project_evidence_symlink_is_rejected_before_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    manifest, _, run_dir = fixture(tmp_path, monkeypatch)
    value = json.loads(manifest.read_text())
    final_output = Path(value["final_gate_output_path"])
    outside = tmp_path / "outside-output.md"
    outside.write_bytes(final_output.read_bytes())
    final_output.unlink()
    final_output.symlink_to(outside)

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)

    assert exc.value.code == "REGULAR_FILE_REQUIRED"
    assert not (run_dir / "output.md").exists()


def test_failure_after_output_creation_preserves_partial_evidence_and_blocks_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    manifest, state_path, run_dir = fixture(tmp_path, monkeypatch)

    def fail_receipt(_path: Path, _payload: dict[str, str | int | bool | None]) -> None:
        raise OSError("simulated create-only receipt failure")

    monkeypatch.setattr(module, "_create_json_exclusive", fail_receipt)
    with pytest.raises(OSError, match="simulated"):
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)

    assert (run_dir / "output.md").is_file()
    assert not (run_dir / "archived-exact-transcript-settlement.json").exists()
    assert json.loads(state_path.read_text())["status"] == "attention_required"

    with pytest.raises(module.SettlementError) as exc:
        module.settle(manifest, confirmation=module.CONFIRMATION_TOKEN)
    assert exc.value.code == "OUTPUT_MUST_BE_ABSENT"
