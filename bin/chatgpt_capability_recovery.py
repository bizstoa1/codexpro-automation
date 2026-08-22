from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias


SESSION = importlib.import_module("chatgpt_capability_session")
LEASE = importlib.import_module("chatgpt_capability_lease")
GIT = importlib.import_module("chatgpt_capability_git")
POLICY = importlib.import_module("chatgpt_capability_policy")
CapabilityRuntimeError = SESSION.CapabilityRuntimeError
CapabilitySession = SESSION.CapabilitySession
LeaseProtocol = SESSION.LeaseProtocol
BaselineProtocol = SESSION.BaselineProtocol

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _recover_active(
    project_root: Path,
    state: Path,
) -> tuple[LeaseProtocol, JsonObject, BaselineProtocol]:
    lease = LEASE.recover_active_lease(project_root, state)
    contract_value: JsonValue = json.loads(lease.capability_path.read_text(encoding="utf-8"))
    if not isinstance(contract_value, dict):
        raise CapabilityRuntimeError("CAPABILITY_SCHEMA_INVALID", "recovered contract is invalid")
    lease_value: JsonValue = json.loads(lease.path.read_text(encoding="utf-8"))
    if not isinstance(lease_value, dict):
        raise CapabilityRuntimeError("CAPABILITY_LEASE_UNRESOLVED", "active lease is invalid")
    raw = lease_value.get("git_baseline")
    if not isinstance(raw, dict):
        raise CapabilityRuntimeError("CAPABILITY_GIT_BASELINE_DRIFT", "durable Git baseline is missing")
    refs = raw.get("protected_refs")
    ignored = raw.get("ignored_paths")
    if not isinstance(refs, list) or not isinstance(ignored, list):
        raise CapabilityRuntimeError("CAPABILITY_GIT_BASELINE_DRIFT", "durable Git baseline is invalid")
    protected: list[tuple[str, str | None]] = []
    for item in refs:
        if not isinstance(item, dict) or not isinstance(item.get("ref"), str):
            raise CapabilityRuntimeError("CAPABILITY_GIT_BASELINE_DRIFT", "durable Git refs are invalid")
        oid = item.get("oid")
        if oid is not None and not isinstance(oid, str):
            raise CapabilityRuntimeError("CAPABILITY_GIT_BASELINE_DRIFT", "durable Git ref OID is invalid")
        protected.append((str(item["ref"]), oid))
    scalar_keys = ("project_root", "access", "head_oid", "branch", "index_oid", "status_sha256")
    if any(not isinstance(raw.get(key), str) for key in scalar_keys) or any(
        not isinstance(item, str) for item in ignored
    ):
        raise CapabilityRuntimeError("CAPABILITY_GIT_BASELINE_DRIFT", "durable Git baseline fields are invalid")
    baseline = GIT.GitBaseline(
        Path(str(raw["project_root"])).resolve(strict=True),
        str(raw["access"]),
        str(raw["head_oid"]),
        str(raw["branch"]),
        str(raw["index_oid"]),
        tuple(protected),
        str(raw["status_sha256"]),
        tuple(Path(str(item)).resolve(strict=False) for item in ignored),
    )
    return lease, contract_value, baseline


def _recovered_session(
    lease: LeaseProtocol,
    contract: JsonObject,
    baseline: BaselineProtocol,
    state: Path,
    subject_id: str,
    token: str | None,
) -> CapabilitySession:
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return CapabilitySession(
        canonical,
        baseline.project_root,
        subject_id,
        token,
        lease.path,
        lease.lease_id,
        state,
        lease,
        baseline,
    )


def resume_single(
    project_root: Path,
    *,
    expected_actor: str,
    state_root: Path | None = None,
) -> CapabilitySession:
    if expected_actor not in {"oracle", "pro"}:
        raise CapabilityRuntimeError("CAPABILITY_SCHEMA_INVALID", "single actor is invalid")
    state = (state_root or SESSION.capability_state_root()).expanduser().resolve()
    lease, contract, baseline = _recover_active(project_root, state)
    topology = contract.get("topology")
    if (
        contract.get("actor") != expected_actor
        or not isinstance(topology, dict)
        or topology.get("kind") != "single"
        or len(lease.tokens) != 1
    ):
        raise CapabilityRuntimeError("CAPABILITY_SCHEMA_INVALID", "active lease is not the expected single session")
    POLICY.admit(contract, state)
    subject_id, token = next(iter(lease.tokens.items()))
    return _recovered_session(lease, contract, baseline, state, subject_id, token)


def resume_web_multi(
    project_root: Path,
    *,
    state_root: Path | None = None,
) -> tuple[CapabilitySession, Mapping[str, str]]:
    state = (state_root or SESSION.capability_state_root()).expanduser().resolve()
    lease, contract, baseline = _recover_active(project_root, state)
    topology = contract.get("topology")
    if (
        contract.get("actor") != "web-multi"
        or contract.get("access") != "read-only"
        or not isinstance(topology, dict)
        or topology.get("kind") != "web-multi"
    ):
        raise CapabilityRuntimeError("CAPABILITY_SCHEMA_INVALID", "active lease is not Web Multi")
    maximum = topology.get("max_provider_concurrency")
    if not isinstance(maximum, int) or isinstance(maximum, bool):
        raise CapabilityRuntimeError("CAPABILITY_SCHEMA_INVALID", "Web Multi concurrency is invalid")
    POLICY.admit(contract, state, requested_concurrency=maximum)
    session = _recovered_session(lease, contract, baseline, state, "web-multi-parent", None)
    return session, lease.tokens


def finish(
    session: CapabilitySession,
    *,
    terminal_harvested: bool,
    safe_pre_submit: bool = False,
    pre_submit_reason: str = "oracle-pre-submit-failed",
) -> JsonObject:
    if session.lease is None:
        return {"status": "dry-run", "lease_created": False}
    if safe_pre_submit:
        receipt = LEASE.abort_pre_submit_lease(session.lease, reason=pre_submit_reason)
        return {"status": str(receipt["state"]), "lease_id": session.lease_id}
    if not terminal_harvested:
        return {"status": "retained", "lease_id": session.lease_id}
    try:
        postflight = GIT.verify_postflight(session.contract(), session.baseline)
    except GIT.CapabilityGitError as exc:
        try:
            LEASE.release_lease(session.lease, terminal_harvested=True, postflight_ok=False)
        except LEASE.CapabilityLeaseError as quarantine:
            if quarantine.code != "CAPABILITY_POSTFLIGHT_FAILED":
                raise CapabilityRuntimeError(
                    "CAPABILITY_POSTFLIGHT_QUARANTINE_FAILED",
                    "capability postflight failed and the lease could not be quarantined",
                    {"postflight_code": exc.code, "lease_code": quarantine.code},
                ) from quarantine
        raise CapabilityRuntimeError(exc.code, str(exc), exc.evidence) from exc
    receipt = LEASE.release_lease(session.lease, terminal_harvested=True, postflight_ok=True)
    return {
        "status": str(receipt["state"]),
        "lease_id": session.lease_id,
        "postflight": postflight,
    }
