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


def test_reported_exact_bindings_allow_transcript_without_final_output_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an exact archived browser transcript that reports independent evidence.
    fixtures = load_fixtures()
    module = fixtures.load_module()
    manifest, _, run_dir = fixtures.fixture(tmp_path, monkeypatch)
    value = json.loads(manifest.read_text())
    transcript = Path(value["transcript_path"])
    final_output = Path(value["final_gate_output_path"])
    transcript.write_text(
        "\n".join([
            "# Exact Oracle result",
            f"- `Output`: `{final_output}`",
            f"- `Output SHA-256`: `{value['final_gate_output_sha256']}`",
            f"- `Receipt`: `{value['pass_stage_receipt_path']}`",
            f"- `Receipt SHA-256`: `{value['pass_stage_receipt_sha256']}`",
            "- `status`: `PASS`",
            "- `next_stage`: `complete`",
            "- `ready_for_next`: `true`",
            "",
            "TASK_OUTCOME: EXECUTED",
        ]) + "\n",
        encoding="utf-8",
    )
    value["transcript_sha256"] = fixtures.sha(transcript)
    manifest.write_text(json.dumps(value), encoding="utf-8")
    assert final_output.read_bytes() not in transcript.read_bytes()

    # When: settlement validates the summary-form transcript.
    result = module.settle(
        manifest,
        confirmation=module.CONFIRMATION_TOKEN,
        process_alive=lambda _pid: False,
    )

    # Then: the independently hash-bound output is materialized without web action.
    assert result["ok"] is True
    assert (run_dir / "output.md").read_bytes() == final_output.read_bytes()
    assert set(result["zero_action_counters"].values()) == {0}


@pytest.mark.parametrize("mutation_index", range(9))
def test_report_values_and_final_marker_reject_meaningless_substrings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_index: int,
) -> None:
    # Given: every expected token appears, but one report value or final position is invalid.
    fixtures = load_fixtures()
    module = fixtures.load_module()
    manifest, _, run_dir = fixtures.fixture(tmp_path, monkeypatch)
    value = json.loads(manifest.read_text())
    transcript = Path(value["transcript_path"])
    report_lines = [
        f"- `Output`: `{value['final_gate_output_path']}`",
        f"- `Output SHA-256`: `{value['final_gate_output_sha256']}`",
        f"- `Receipt`: `{value['pass_stage_receipt_path']}`",
        f"- `Receipt SHA-256`: `{value['pass_stage_receipt_sha256']}`",
        "- `status`: `PASS`",
        "- `next_stage`: `complete`",
        "- `ready_for_next`: `true`",
        "TASK_OUTCOME: EXECUTED",
    ]
    if mutation_index < len(report_lines):
        report_lines[mutation_index] += " trailing prose"
    else:
        report_lines.append("trailing prose after the execution marker")
    transcript.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    value["transcript_sha256"] = fixtures.sha(transcript)
    manifest.write_text(json.dumps(value), encoding="utf-8")

    # When: settlement validates the transcript before creating output.
    with pytest.raises(module.SettlementError) as exc:
        module.settle(
            manifest,
            confirmation=module.CONFIRMATION_TOKEN,
            process_alive=lambda _pid: False,
        )

    # Then: exact-line semantics reject the token-containing prose fail closed.
    assert exc.value.code == "TRANSCRIPT_ANSWER_INVALID"
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


def test_unrelated_prefix_hashes_do_not_block_korean_nested_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = load_fixtures()
    module = fixtures.load_module()
    manifest, _, run_dir = fixtures.fixture(tmp_path, monkeypatch)
    value = json.loads(manifest.read_text())
    project = Path(value["project_root"])
    old_output = Path(value["final_gate_output_path"])
    old_receipt = Path(value["pass_stage_receipt_path"])
    evidence_dir = project / ".ai-bridge" / "workflow" / "stages" / "final-web-gate"
    evidence_dir.mkdir(parents=True)
    final_output = evidence_dir / "final-web-gate-output.md"
    final_output.write_bytes(old_output.read_bytes())
    pass_receipt = evidence_dir / "stage-result.json"
    receipt_value = json.loads(old_receipt.read_text())
    receipt_value.update({
        "output_path": str(final_output),
        "output_sha256": fixtures.sha(final_output),
    })
    pass_receipt.write_text(json.dumps(receipt_value), encoding="utf-8")
    output_relative = final_output.relative_to(project).as_posix()
    receipt_relative = pass_receipt.relative_to(project).as_posix()
    transcript = Path(value["transcript_path"])
    transcript.write_text(
        "\n".join([
            "* 검토 출력: .ai-bridge/review-output.md",
            f"  * SHA-256: {'1' * 64}",
            "* 최종 검토: .ai-bridge/final-review.md",
            f"  * SHA-256: {'2' * 64}",
            "* appendix: .ai-bridge/appendix.md",
            f"  * SHA-256: {'3' * 64}",
            "* prefix evidence: .ai-bridge/prefix.md",
            f"  * SHA-256: {'4' * 64}",
            f"* 최종 게이트 출력: {output_relative}",
            f"  * SHA-256: {fixtures.sha(final_output)}",
            f"* 단계 receipt: {receipt_relative}",
            f"  * SHA-256: {fixtures.sha(pass_receipt)}",
            "  * status: PASS",
            "  * next_stage: complete",
            "  * ready_for_next: true",
            "",
            "TASK_OUTCOME: EXECUTED",
        ]) + "\n",
        encoding="utf-8",
    )
    value.update({
        "transcript_sha256": fixtures.sha(transcript),
        "final_gate_output_path": str(final_output),
        "final_gate_output_sha256": fixtures.sha(final_output),
        "pass_stage_receipt_path": str(pass_receipt),
        "pass_stage_receipt_sha256": fixtures.sha(pass_receipt),
    })
    manifest.write_text(json.dumps(value), encoding="utf-8")
    result = module.settle(
        manifest,
        confirmation=module.CONFIRMATION_TOKEN,
        process_alive=lambda _pid: False,
    )
    assert result["ok"] is True
    assert (run_dir / "output.md").read_bytes() == final_output.read_bytes()
    assert set(result["zero_action_counters"].values()) == {0}
