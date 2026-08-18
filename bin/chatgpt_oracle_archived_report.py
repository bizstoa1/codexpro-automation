from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final


OUTPUT_PATH_KEYS: Final = frozenset({
    "output",
    "output_path",
    "final_output",
    "final_output_path",
    "final_gate_output",
    "final_gate_output_path",
    "최종_게이트_출력",
})
OUTPUT_HASH_KEYS: Final = frozenset({
    "output_sha256",
    "output_sha_256",
    "final_output_sha256",
    "final_output_sha_256",
    "final_gate_output_sha256",
    "final_gate_output_sha_256",
})
RECEIPT_PATH_KEYS: Final = frozenset({
    "receipt",
    "receipt_path",
    "stage_receipt",
    "stage_receipt_path",
    "pass_receipt",
    "pass_receipt_path",
    "pass_stage_receipt",
    "pass_stage_receipt_path",
    "단계_receipt",
})
RECEIPT_HASH_KEYS: Final = frozenset({
    "receipt_sha256",
    "receipt_sha_256",
    "stage_receipt_sha256",
    "stage_receipt_sha_256",
    "pass_receipt_sha256",
    "pass_receipt_sha_256",
    "pass_stage_receipt_sha256",
    "pass_stage_receipt_sha_256",
})
GENERIC_HASH_KEYS: Final = frozenset({"sha256", "sha_256"})
RECEIPT_FIELDS: Final = {
    "status": "PASS",
    "next_stage": "complete",
    "ready_for_next": "true",
}


class ReportValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Field:
    indent: int
    key: str
    value: str


@dataclass(slots=True)
class _Section:
    indent: int
    path_text: str
    hashes: list[str] = field(default_factory=list)
    fields: dict[str, list[str]] = field(default_factory=dict)


def _normalize_key(value: str) -> str:
    return "_".join(value.strip(" `*_").casefold().replace("-", " ").split())


def _clean_value(value: str) -> str:
    return value.strip().strip("`*_").strip()


def _field(raw_line: str) -> _Field | None:
    expanded = raw_line.expandtabs(4)
    indent = len(expanded) - len(expanded.lstrip(" "))
    line = expanded[indent:]
    if line.startswith(("- ", "* ", "+ ")):
        line = line[2:].strip()
    key, separator, value = line.partition(":")
    if not separator:
        return None
    return _Field(indent=indent, key=_normalize_key(key), value=_clean_value(value))


def _new_section(current: _Section | None, parsed: _Field) -> _Section:
    if current is not None:
        raise ReportValidationError("duplicate artifact section")
    return _Section(indent=parsed.indent, path_text=parsed.value)


def _bind_hash(section: _Section | None, value: str) -> None:
    if section is None:
        raise ReportValidationError("artifact hash has no parent section")
    section.hashes.append(value)


def _contained_regular_path(project_root: Path, value: str, expected: Path) -> Path:
    raw = Path(value).expanduser()
    if not value or any(part == ".." for part in raw.parts):
        raise ReportValidationError("artifact path traversal is not allowed")
    candidate = raw if raw.is_absolute() else project_root / raw
    try:
        relative = candidate.relative_to(project_root)
    except ValueError as exc:
        raise ReportValidationError("artifact path leaves the exact project") from exc
    cursor = project_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ReportValidationError("artifact path must not traverse a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReportValidationError("artifact path must identify an existing file") from exc
    if not resolved.is_file() or resolved != expected:
        raise ReportValidationError("artifact path does not match the approved binding")
    return resolved


def validate_report(
    lines: list[str],
    *,
    project_root: Path,
    output_path: Path,
    output_sha256: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> None:
    output: _Section | None = None
    receipt: _Section | None = None
    active: _Section | None = None
    active_kind = ""
    for raw_line in lines:
        if not raw_line.strip():
            active = None
            active_kind = ""
            continue
        parsed = _field(raw_line)
        if parsed is None:
            if active is not None:
                active = None
                active_kind = ""
            continue
        if parsed.key in OUTPUT_PATH_KEYS:
            output = _new_section(output, parsed)
            active, active_kind = output, "output"
        elif parsed.key in RECEIPT_PATH_KEYS:
            receipt = _new_section(receipt, parsed)
            active, active_kind = receipt, "receipt"
        elif parsed.key in OUTPUT_HASH_KEYS:
            if active is not None and parsed.indent > active.indent and active_kind != "output":
                raise ReportValidationError("output hash is nested under the wrong artifact")
            _bind_hash(output, parsed.value)
        elif parsed.key in RECEIPT_HASH_KEYS:
            if active is not None and parsed.indent > active.indent and active_kind != "receipt":
                raise ReportValidationError("receipt hash is nested under the wrong artifact")
            _bind_hash(receipt, parsed.value)
        elif parsed.key in GENERIC_HASH_KEYS:
            if active is None or parsed.indent <= active.indent:
                active = None
                active_kind = ""
                continue
            _bind_hash(active, parsed.value)
        elif parsed.key in RECEIPT_FIELDS:
            if active is not None and parsed.indent > active.indent and active_kind != "receipt":
                raise ReportValidationError("receipt field is nested under the wrong artifact")
            if receipt is None:
                raise ReportValidationError("receipt field has no receipt section")
            receipt.fields.setdefault(parsed.key, []).append(parsed.value)
        elif active is not None and parsed.indent <= active.indent:
            active = None
            active_kind = ""
    if output is None or receipt is None:
        raise ReportValidationError("exactly one output and receipt section are required")
    if output.hashes != [output_sha256] or receipt.hashes != [receipt_sha256]:
        raise ReportValidationError("artifact hashes do not match their parent bindings")
    if any(receipt.fields.get(key) != [expected] for key, expected in RECEIPT_FIELDS.items()):
        raise ReportValidationError("receipt completion fields are invalid")
    _ = _contained_regular_path(project_root, output.path_text, output_path)
    _ = _contained_regular_path(project_root, receipt.path_text, receipt_path)
