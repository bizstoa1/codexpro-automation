from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias


CORE = sys.modules.get("chatgpt_oracle_multi_core")
if CORE is None:
    raise ImportError("Web Multi core must be loaded first")
CAPABILITY = CORE.CAPABILITY
MultiError = CORE.MultiError
RESULT_SCHEMA = CORE.RESULT_SCHEMA
STORAGE = CAPABILITY.LEASE.STORAGE

ATTESTATION_SCHEMA = "codex.chatgpt.oracle-multi-completion-attestation/v1"
ATTESTATION_KEYS = {
    "schema",
    "project_root",
    "manifest_sha256",
    "parent_id",
    "result_path",
    "result_sha256",
    "receipt_path",
    "receipt_sha256",
    "lease_id",
    "capability_sha256",
}
HEX_DIGITS = frozenset("0123456789abcdef")
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CompletionExpectation:
    project_root: Path
    manifest_sha256: str
    result_path: Path
    receipt_path: Path | None


@dataclass(frozen=True, slots=True)
class VerifiedCompletion:
    result: JsonObject
    result_path: Path
    receipt_path: Path | None
    receipt_sha256: str | None
    parent_id: str


def _is_sha256(value: JsonValue) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX_DIGITS


def _project_file(root: Path, path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise MultiError(f"{label} must be absolute")
    canonical_root = root.expanduser().resolve(strict=True)
    candidate = path.expanduser()
    try:
        relative = candidate.relative_to(canonical_root)
    except ValueError as exc:
        raise MultiError(f"{label} is outside the exact project root") from exc
    current = canonical_root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise MultiError(f"{label} contains a symlink")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(canonical_root)
    except ValueError as exc:
        raise MultiError(f"{label} escapes the exact project root") from exc
    if not resolved.is_file():
        raise MultiError(f"{label} must be a regular file")
    return resolved


def _json_bytes(path: Path, label: str) -> tuple[bytes, JsonObject]:
    data = path.read_bytes()
    try:
        value: JsonValue = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MultiError(f"{label} must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise MultiError(f"{label} must be a JSON object")
    return data, value


def _completion_parts(
    expectation: CompletionExpectation,
) -> tuple[Path, bytes, JsonObject, Path | None, bytes | None, str]:
    root = expectation.project_root.expanduser().resolve(strict=True)
    if not _is_sha256(expectation.manifest_sha256):
        raise MultiError("completion manifest identity is invalid")
    result_path = _project_file(root, expectation.result_path, "completion result")
    result_bytes, result = _json_bytes(result_path, "completion result")
    parent_id = result.get("parent_id")
    if (
        result.get("schema") != RESULT_SCHEMA
        or result.get("status") != "complete"
        or result.get("ok") is not True
        or result.get("manifest_sha256") != expectation.manifest_sha256
        or result.get("merger_count") != 1
        or result.get("merger_submission_count") != 1
        or not _is_sha256(parent_id)
    ):
        raise MultiError("completion result identity is invalid")
    raw_receipt = result.get("next_stage_result_path")
    if expectation.receipt_path is None:
        if raw_receipt is not None:
            raise MultiError("completion result has an unexpected receipt")
        return result_path, result_bytes, result, None, None, str(parent_id)
    receipt_path = _project_file(root, expectation.receipt_path, "completion receipt")
    if raw_receipt != str(receipt_path):
        raise MultiError("completion receipt path identity mismatch")
    receipt_bytes = receipt_path.read_bytes()
    return result_path, result_bytes, result, receipt_path, receipt_bytes, str(parent_id)


def _host_paths(root: Path, manifest_sha256: str, parent_id: str) -> tuple[Path, Path]:
    state_root = CAPABILITY.capability_state_root().expanduser().resolve()
    directory, _active = STORAGE.lease_paths(state_root, root)
    attestation = directory / "completions" / manifest_sha256 / f"{parent_id}.json"
    return attestation, directory


def _released_lease(directory: Path, lease_id: str, capability_sha256: str, root: Path) -> None:
    if not _is_sha256(lease_id) or not _is_sha256(capability_sha256):
        raise MultiError("completion lease identity is invalid")
    archive = directory / "archive" / f"{lease_id}.json"
    try:
        lease = STORAGE.read_json(archive)
    except STORAGE.CapabilityLeaseError as exc:
        raise MultiError("completion lease archive is unavailable") from exc
    if (
        lease.get("lease_id") != lease_id
        or lease.get("capability_id") != capability_sha256
        or lease.get("capability_sha256") != capability_sha256
        or lease.get("project_root") != str(root)
        or lease.get("state") != "released"
        or lease.get("terminal_harvested") is not True
        or lease.get("postflight") != "passed"
    ):
        raise MultiError("completion lease archive identity is invalid")


def attest_completion(
    expectation: CompletionExpectation,
    lease_id: str,
    capability_sha256: str,
) -> Path:
    root = expectation.project_root.expanduser().resolve(strict=True)
    result_path, result_bytes, result, receipt_path, receipt_bytes, parent_id = _completion_parts(
        expectation
    )
    capability = result.get("capability")
    if (
        not isinstance(capability, dict)
        or capability.get("lease_id") != lease_id
        or capability.get("contract_sha256") != capability_sha256
        or capability.get("status") != "released"
    ):
        raise MultiError("completion capability evidence is invalid")
    attestation_path, directory = _host_paths(root, expectation.manifest_sha256, parent_id)
    _released_lease(directory, lease_id, capability_sha256, root)
    value: JsonObject = {
        "schema": ATTESTATION_SCHEMA,
        "project_root": str(root),
        "manifest_sha256": expectation.manifest_sha256,
        "parent_id": parent_id,
        "result_path": str(result_path),
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "receipt_path": str(receipt_path) if receipt_path is not None else None,
        "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest() if receipt_bytes is not None else None,
        "lease_id": lease_id,
        "capability_sha256": capability_sha256,
    }
    STORAGE.write_json(attestation_path, value)
    return attestation_path


def verify_completion(expectation: CompletionExpectation) -> VerifiedCompletion:
    root = expectation.project_root.expanduser().resolve(strict=True)
    result_path, result_bytes, result, receipt_path, receipt_bytes, parent_id = _completion_parts(
        expectation
    )
    attestation_path, directory = _host_paths(root, expectation.manifest_sha256, parent_id)
    try:
        identity = attestation_path.stat()
        attestation = STORAGE.read_json(attestation_path)
    except (OSError, STORAGE.CapabilityLeaseError) as exc:
        raise MultiError("host completion attestation is unavailable") from exc
    if (
        attestation_path.is_symlink()
        or not stat.S_ISREG(identity.st_mode)
        or (os.name != "nt" and stat.S_IMODE(identity.st_mode) & 0o077)
        or set(attestation) != ATTESTATION_KEYS
    ):
        raise MultiError("host completion attestation is unsafe")
    receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest() if receipt_bytes is not None else None
    expected: JsonObject = {
        "schema": ATTESTATION_SCHEMA,
        "project_root": str(root),
        "manifest_sha256": expectation.manifest_sha256,
        "parent_id": parent_id,
        "result_path": str(result_path),
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "receipt_path": str(receipt_path) if receipt_path is not None else None,
        "receipt_sha256": receipt_sha256,
        "lease_id": attestation.get("lease_id"),
        "capability_sha256": attestation.get("capability_sha256"),
    }
    if attestation != expected:
        raise MultiError("host completion attestation identity mismatch")
    lease_id = attestation.get("lease_id")
    capability_sha256 = attestation.get("capability_sha256")
    if not isinstance(lease_id, str) or not isinstance(capability_sha256, str):
        raise MultiError("host completion attestation lease identity is invalid")
    _released_lease(directory, lease_id, capability_sha256, root)
    capability = result.get("capability")
    if (
        not isinstance(capability, dict)
        or capability.get("lease_id") != lease_id
        or capability.get("contract_sha256") != capability_sha256
        or capability.get("status") != "released"
    ):
        raise MultiError("completion result capability identity mismatch")
    return VerifiedCompletion(result, result_path, receipt_path, receipt_sha256, parent_id)
