from __future__ import annotations

import hashlib
import importlib.util
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, TypeAlias


CONTRACT_SCHEMA = "codex.chatgpt.project-capability/v1"
LEASE_SCHEMA = "codex.chatgpt.project-capability-lease/v1"
SUBJECT_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
BIN = Path(__file__).resolve().parent

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class CapabilityModuleLoadError(RuntimeError):
    def __init__(self, path: Path):
        super().__init__(f"capability module unavailable: {path}")
        self.path = path


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CapabilityModuleLoadError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STORAGE = _load(
    "chatgpt_capability_lease_storage",
    BIN / "chatgpt_capability_lease_storage.py",
)
TOKEN = _load("chatgpt_capability_token", BIN / "chatgpt_capability_token.py")
CapabilityLeaseError = STORAGE.CapabilityLeaseError
_TransactionLock = STORAGE.TransactionLock
_object = STORAGE.object_value
_canonical = STORAGE.canonical_bytes
_read_json = STORAGE.read_json
_write_json = STORAGE.write_json
_secret = STORAGE.secret
_paths = STORAGE.lease_paths
_token = TOKEN.sign_token
TOKEN_SCHEMA = TOKEN.TOKEN_SCHEMA


@dataclass(frozen=True, slots=True)
class LeaseHandle:
    lease_id: str
    path: Path
    capability_path: Path
    tokens: Mapping[str, str]


def acquire_lease(
    contract: JsonObject,
    state_root: Path,
    subjects: list[str],
    *,
    git_baseline: JsonObject | None = None,
) -> LeaseHandle:
    binding = _object(contract.get("binding"), "contract.binding")
    root_raw = binding.get("project_root")
    if contract.get("schema") != CONTRACT_SCHEMA or not isinstance(root_raw, str):
        raise CapabilityLeaseError("CAPABILITY_SCHEMA_INVALID", "capability contract is invalid")
    root = Path(root_raw).expanduser().resolve(strict=True)
    if not subjects or len(subjects) > 26 or len(set(subjects)) != len(subjects) or any(SUBJECT_RE.fullmatch(item) is None for item in subjects):
        raise CapabilityLeaseError("CAPABILITY_SUBJECT_INVALID", "capability subjects are invalid")
    state = state_root.expanduser().resolve()
    directory, active = _paths(state, root)
    canonical = _canonical(contract)
    capability_id = hashlib.sha256(canonical).hexdigest()
    capability_path = directory / "contracts" / f"{capability_id}.json"
    with _TransactionLock(directory / "transaction.lock"):
        if active.exists() or active.is_symlink():
            prior = _read_json(active)
            raise CapabilityLeaseError("CAPABILITY_LEASE_CONFLICT", "another capability owner is active", {"lease_id": prior.get("lease_id")})
        lease_id = secrets.token_hex(32)
        secret = _secret(state)
        token_values: dict[str, str] = {}
        subject_rows: list[JsonValue] = []
        for subject in subjects:
            payload: JsonObject = {"schema": TOKEN_SCHEMA, "lease_id": lease_id, "capability_id": capability_id, "project_root": str(root), "subject_id": subject}
            token_values[subject] = _token(payload, secret)
            subject_rows.append({"subject_id": subject, "token_sha256": hashlib.sha256(token_values[subject].encode("utf-8")).hexdigest()})
        actor, access = contract.get("actor"), contract.get("access")
        if not isinstance(actor, str) or not isinstance(access, str):
            raise CapabilityLeaseError("CAPABILITY_SCHEMA_INVALID", "capability actor is invalid")
        lease: JsonObject = {
            "schema": LEASE_SCHEMA,
            "lease_id": lease_id,
            "capability_id": capability_id,
            "capability_path": str(capability_path),
            "capability_sha256": capability_id,
            "project_root": str(root),
            "owner": {"actor": actor, "access": access},
            "state": "active",
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "subjects": subject_rows,
            "terminal_harvested": False,
            "postflight": "pending",
            "git_baseline": git_baseline,
        }
        _write_json(capability_path, contract)
        _write_json(active, lease)
    return LeaseHandle(lease_id, active, capability_path, MappingProxyType(token_values))


def recover_active_lease(project_root: Path, state_root: Path) -> LeaseHandle:
    root = project_root.expanduser().resolve(strict=True)
    state = state_root.expanduser().resolve()
    directory, active = _paths(state, root)
    with _TransactionLock(directory / "transaction.lock"):
        lease = _read_json(active)
        lease_id = lease.get("lease_id")
        capability_id = lease.get("capability_id")
        if (
            lease.get("schema") != LEASE_SCHEMA
            or lease.get("state") != "active"
            or lease.get("project_root") != str(root)
            or not isinstance(lease_id, str)
            or not isinstance(capability_id, str)
        ):
            raise CapabilityLeaseError("CAPABILITY_LEASE_UNRESOLVED", "active capability lease is invalid")
        capability_path = directory / "contracts" / f"{capability_id}.json"
        if Path(str(lease.get("capability_path") or "")).resolve() != capability_path.resolve():
            raise CapabilityLeaseError("CAPABILITY_LEASE_UNRESOLVED", "capability path binding is invalid")
        contract = _read_json(capability_path)
        if hashlib.sha256(_canonical(contract)).hexdigest() != capability_id:
            raise CapabilityLeaseError("CAPABILITY_LEASE_UNRESOLVED", "capability contract hash changed")
        rows = lease.get("subjects")
        if not isinstance(rows, list):
            raise CapabilityLeaseError("CAPABILITY_SUBJECT_MISMATCH", "capability subjects are invalid")
        secret = _secret(state)
        tokens: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise CapabilityLeaseError("CAPABILITY_SUBJECT_MISMATCH", "capability subject is invalid")
            subject = row.get("subject_id")
            expected = row.get("token_sha256")
            if not isinstance(subject, str) or not isinstance(expected, str):
                raise CapabilityLeaseError("CAPABILITY_SUBJECT_MISMATCH", "capability subject is invalid")
            payload: JsonObject = {
                "schema": TOKEN_SCHEMA,
                "lease_id": lease_id,
                "capability_id": capability_id,
                "project_root": str(root),
                "subject_id": subject,
            }
            token = _token(payload, secret)
            if hashlib.sha256(token.encode("utf-8")).hexdigest() != expected:
                raise CapabilityLeaseError("CAPABILITY_SUBJECT_MISMATCH", "recovered token hash differs")
            tokens[subject] = token
    return LeaseHandle(lease_id, active, capability_path, MappingProxyType(tokens))


def verify_subject_token(token: str, state_root: Path, project_root: Path) -> JsonObject:
    payload = TOKEN.verify_token(token, state_root)
    root = project_root.expanduser().resolve(strict=True)
    if payload.get("schema") != TOKEN_SCHEMA or payload.get("project_root") != str(root):
        raise CapabilityLeaseError("CAPABILITY_ROOT_MISMATCH", "capability token root mismatch")
    directory, active = _paths(state_root.resolve(), root)
    lease = _read_json(active)
    if lease.get("state") != "active" or lease.get("lease_id") != payload.get("lease_id") or lease.get("capability_id") != payload.get("capability_id"):
        raise CapabilityLeaseError("CAPABILITY_LEASE_UNRESOLVED", "capability token has no active lease")
    subject_id = payload.get("subject_id")
    rows = lease.get("subjects")
    if not isinstance(subject_id, str) or not isinstance(rows, list):
        raise CapabilityLeaseError("CAPABILITY_SUBJECT_MISMATCH", "capability subject is invalid")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    matched = [item for item in rows if isinstance(item, dict) and item.get("subject_id") == subject_id and item.get("token_sha256") == token_hash]
    if len(matched) != 1 or Path(str(lease.get("capability_path") or "")).resolve() != (directory / "contracts" / f"{lease.get('capability_id')}.json").resolve():
        raise CapabilityLeaseError("CAPABILITY_SUBJECT_MISMATCH", "capability subject is not bound")
    return payload


def release_lease(handle: LeaseHandle, *, terminal_harvested: bool, postflight_ok: bool) -> JsonObject:
    directory = handle.path.parent
    with _TransactionLock(directory / "transaction.lock"):
        lease = _read_json(handle.path)
        if lease.get("lease_id") != handle.lease_id or lease.get("state") != "active":
            raise CapabilityLeaseError("CAPABILITY_LEASE_UNRESOLVED", "capability lease is not active")
        if not terminal_harvested:
            raise CapabilityLeaseError("CAPABILITY_TERMINAL_EVIDENCE_REQUIRED", "terminal exact-session evidence is required")
        if not postflight_ok:
            lease.update({"state": "quarantined", "terminal_harvested": True, "postflight": "failed"})
            _write_json(handle.path, lease)
            raise CapabilityLeaseError("CAPABILITY_POSTFLIGHT_FAILED", "capability postflight failed")
        lease.update({"state": "released", "terminal_harvested": True, "postflight": "passed", "released_at": datetime.now(timezone.utc).isoformat()})
        _write_json(directory / "archive" / f"{handle.lease_id}.json", lease)
        handle.path.unlink()
    return lease


def abort_pre_submit_lease(handle: LeaseHandle, *, reason: str) -> JsonObject:
    normalized = reason.strip().casefold()
    if SUBJECT_RE.fullmatch(normalized) is None:
        raise CapabilityLeaseError("CAPABILITY_ABORT_REASON_INVALID", "pre-submit abort reason is invalid")
    directory = handle.path.parent
    with _TransactionLock(directory / "transaction.lock"):
        lease = _read_json(handle.path)
        if lease.get("lease_id") != handle.lease_id or lease.get("state") != "active":
            raise CapabilityLeaseError("CAPABILITY_LEASE_UNRESOLVED", "capability lease is not active")
        lease.update(
            {
                "state": "aborted_pre_submit",
                "terminal_harvested": False,
                "postflight": "not_required",
                "abort_reason": normalized,
                "released_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write_json(directory / "archive" / f"{handle.lease_id}.json", lease)
        handle.path.unlink()
    return lease
