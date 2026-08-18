from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from bin.chatgpt_oracle_archived_report import ReportValidationError, validate_report


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifacts(tmp_path: Path) -> tuple[Path, Path, Path, str, str]:
    project = tmp_path / "project"
    evidence = project / ".ai-bridge" / "workflow" / "final-web-gate"
    evidence.mkdir(parents=True)
    output = evidence / "final-web-gate-output.md"
    receipt = evidence / "stage-result.json"
    _ = output.write_text("approved final output\n", encoding="utf-8")
    _ = receipt.write_text('{"status":"PASS"}\n', encoding="utf-8")
    return project, output, receipt, sha(output), sha(receipt)


def lines_for(
    project: Path,
    output: Path,
    receipt: Path,
    output_hash: str,
    receipt_hash: str,
) -> list[str]:
    return [
        f"* 최종 게이트 출력: {output.relative_to(project).as_posix()}",
        f"  * SHA-256: {output_hash}",
        f"* 단계 receipt: {receipt.relative_to(project).as_posix()}",
        f"  * SHA-256: {receipt_hash}",
        "  * status: PASS",
        "  * next_stage: complete",
        "  * ready_for_next: true",
    ]


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "wrong_parent", "swapped_hashes", "substring", "duplicate_status"],
)
def test_report_sections_reject_ambiguous_or_misassociated_fields(
    tmp_path: Path, mutation: str,
) -> None:
    project, output, receipt, output_hash, receipt_hash = artifacts(tmp_path)
    lines = lines_for(project, output, receipt, output_hash, receipt_hash)
    if mutation == "duplicate":
        lines[0:0] = lines[:2]
    elif mutation == "wrong_parent":
        lines.insert(2, f"  * Receipt SHA-256: {receipt_hash}")
    elif mutation == "swapped_hashes":
        lines[1], lines[3] = f"  * SHA-256: {receipt_hash}", f"  * SHA-256: {output_hash}"
    elif mutation == "substring":
        lines[0] = lines[0].replace("최종 게이트 출력", "최종 게이트 출력 참고")
    else:
        lines.append("  * status: PASS")

    with pytest.raises(ReportValidationError):
        validate_report(
            lines,
            project_root=project,
            output_path=output,
            output_sha256=output_hash,
            receipt_path=receipt,
            receipt_sha256=receipt_hash,
        )


@pytest.mark.parametrize("mode", ["traversal", "other_root", "symlink"])
def test_report_paths_reject_escape_and_symlink_aliases(tmp_path: Path, mode: str) -> None:
    project, output, receipt, output_hash, receipt_hash = artifacts(tmp_path)
    lines = lines_for(project, output, receipt, output_hash, receipt_hash)
    if mode == "traversal":
        lines[0] = "* 최종 게이트 출력: ../outside.md"
    elif mode == "other_root":
        outside = tmp_path / "outside.md"
        _ = outside.write_bytes(output.read_bytes())
        lines[0] = f"* 최종 게이트 출력: {outside}"
    else:
        alias = project / "output-link.md"
        try:
            alias.symlink_to(output)
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")
        lines[0] = "* 최종 게이트 출력: output-link.md"

    with pytest.raises(ReportValidationError):
        validate_report(
            lines,
            project_root=project,
            output_path=output,
            output_sha256=output_hash,
            receipt_path=receipt,
            receipt_sha256=receipt_hash,
        )


def test_existing_english_absolute_report_remains_valid(tmp_path: Path) -> None:
    project, output, receipt, output_hash, receipt_hash = artifacts(tmp_path)
    validate_report(
        [
            f"- `Output`: `{output}`",
            f"- `Output SHA-256`: `{output_hash}`",
            f"- `Receipt`: `{receipt}`",
            f"- `Receipt SHA-256`: `{receipt_hash}`",
            "- `status`: `PASS`",
            "- `next_stage`: `complete`",
            "- `ready_for_next`: `true`",
        ],
        project_root=project,
        output_path=output,
        output_sha256=output_hash,
        receipt_path=receipt,
        receipt_sha256=receipt_hash,
    )
