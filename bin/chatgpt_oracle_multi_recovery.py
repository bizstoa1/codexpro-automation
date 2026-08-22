from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Callable

CORE = sys.modules.get("chatgpt_oracle_multi_core")
ARTIFACTS = sys.modules.get("chatgpt_oracle_multi_artifacts")
if CORE is None or ARTIFACTS is None:
    raise ImportError("Web Multi core and artifacts must be loaded first")
CAPABILITY = CORE.CAPABILITY
RESULT_SCHEMA = CORE.RESULT_SCHEMA
RUNNER = CORE.RUNNER
STATE = CORE.STATE
MultiError = CORE.MultiError
_capability_evidence = CORE._capability_evidence
_dict = CORE._dict
_inside = CORE._inside
_read_json = CORE._read_json
_result_base = CORE._result_base
_write_json = CORE._write_json
load_manifest = CORE.load_manifest
_bound_merger_result = ARTIFACTS._bound_merger_result
_child_manifest = ARTIFACTS._child_manifest
_merger_transport = ARTIFACTS._merger_transport
_write_bytes_atomic = ARTIFACTS._write_bytes_atomic


def reconcile_recovered_lanes(manifest_path: Path) -> dict[str, Any]:
    """Rebind durable exact-run outputs to an interrupted parent without submitting.

    This is intentionally a host-only recovery step.  It validates every
    original lane against the persisted parent/lane/mission identity, restores
    stable-order handoffs, and prepares the merger mission.  It never calls the
    Oracle runner and therefore cannot create a replacement conversation.
    """
    config = load_manifest(manifest_path)
    result_path = config["output_dir"] / "result.json"
    result = _read_json(result_path)
    if result.get("schema") != RESULT_SCHEMA:
        raise MultiError("existing multi result schema is invalid")
    parent_id = str(result.get("parent_id") or "").strip()
    if len(parent_id) != 64:
        raise MultiError("existing multi result has no valid parent identity")
    recorded = result.get("lanes")
    if not isinstance(recorded, list):
        raise MultiError("existing multi result has no lane ledger")
    by_id = {str(item.get("id") or ""): item for item in recorded if isinstance(item, dict)}
    expected_ids = [lane["id"] for lane in config["solvers"]]
    if set(by_id) != set(expected_ids) or len(by_id) != len(expected_ids):
        raise MultiError("existing lane ledger does not match the manifest")
    reconciled: list[dict[str, Any]] = []
    for lane in config["solvers"]:
        prior = by_id[lane["id"]]
        run_dir = Path(str(prior.get("run_dir") or "")).expanduser()
        if not run_dir.is_absolute():
            raise MultiError(f"lane {lane['id']} has no absolute exact run directory")
        run_dir = run_dir.resolve()
        if not STATE.is_within(STATE.oracle_state_root(), run_dir):
            raise MultiError(f"lane {lane['id']} exact run directory is outside Oracle host state")
        state_path = run_dir / "state.json"
        output_path = run_dir / "output.md"
        if not state_path.is_file() or not output_path.is_file() or not output_path.read_bytes().strip():
            raise MultiError(f"lane {lane['id']} has no durable recovered output")
        state = _read_json(state_path)
        mission = _dict(state.get("mission"))
        oracle = _dict(state.get("oracle"))
        if state.get("run_id") not in {None, run_dir.name}:
            raise MultiError(f"lane {lane['id']} run identity mismatch")
        if Path(str(state.get("project_root") or "")).resolve() != config["project_root"]:
            raise MultiError(f"lane {lane['id']} project identity mismatch")
        if state.get("parallel_parent_id") != parent_id:
            raise MultiError(f"lane {lane['id']} parent identity mismatch")
        expected_mission_sha = hashlib.sha256(lane["mission_path"].read_bytes()).hexdigest()
        if mission.get("sha256") != expected_mission_sha:
            raise MultiError(f"lane {lane['id']} mission identity mismatch")
        if state.get("status") != "complete" or state.get("terminal_harvested") is not True:
            raise MultiError(f"lane {lane['id']} is not terminally harvested")
        artifact_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
        if state.get("artifact_sha256") != artifact_sha:
            raise MultiError(f"lane {lane['id']} durable output hash mismatch")
        prior_locator = str(prior.get("session_locator") or "").strip()
        exact_locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
        if prior_locator and prior_locator != exact_locator:
            raise MultiError(f"lane {lane['id']} exact session identity mismatch")
        handoff = config["output_dir"] / "handoffs" / f"{lane['id']}.md"
        _write_bytes_atomic(handoff, output_path.read_bytes())
        reconciled.append({
            "id": lane["id"],
            "ok": True,
            "run_dir": str(run_dir),
            "output_path": str(handoff),
            "session_locator": exact_locator,
            "artifact_sha256": artifact_sha,
        })
    merger_mission = _merger_transport(config, reconciled, parent_id)
    updated = _result_base(
        status="merger_ready",
        ok=True,
        writes_performed=True,
        merger_count=0,
        lanes=reconciled,
        merger_run_dir=result.get("merger_run_dir"),
        capability=(
            dict(result["capability"])
            if isinstance(result.get("capability"), dict)
            else {"status": "active-recovery", "lease_created": True}
        ),
        parent_id=parent_id,
        manifest_sha256=config["manifest_sha256"],
        successful_lane_count=len(reconciled),
        merger_mission_path=str(merger_mission),
        recovery_mode="exact-runs-no-submit",
    )
    _write_json(result_path, updated)
    return updated


def resume_recovered_merger(
    manifest_path: Path,
    *,
    execute: Callable[..., dict[str, Any]] = RUNNER.execute_run,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Submit only the prepared merger after exact child recovery."""
    if dry_run:
        raise MultiError("v2 merger resume dry-run is forbidden because it must bind the active lease")
    config = load_manifest(manifest_path)
    result_path = config["output_dir"] / "result.json"
    result = _read_json(result_path)
    if result.get("schema") != RESULT_SCHEMA or result.get("status") != "merger_ready":
        raise MultiError("multi result is not ready for merger-only resume")
    if result.get("merger_submission_count") not in {None, 0}:
        raise MultiError("the exact merger was already submitted; replacement is forbidden")
    parent_id = str(result.get("parent_id") or "").strip()
    lanes = result.get("lanes")
    if len(parent_id) != 64 or not isinstance(lanes, list) or len(lanes) != len(config["solvers"]):
        raise MultiError("merger-ready result identity is incomplete")
    expected_ids = [lane["id"] for lane in config["solvers"]]
    if [str(lane.get("id") or "") for lane in lanes if isinstance(lane, dict)] != expected_ids:
        raise MultiError("merger-ready lane order does not match the manifest")
    merger_mission = Path(str(result.get("merger_mission_path") or "")).resolve(strict=True)
    expected_merger = (config["output_dir"] / "merger" / "mission.md").resolve(strict=True)
    if merger_mission != expected_merger:
        raise MultiError("merger mission identity mismatch")
    merger_text = merger_mission.read_text(encoding="utf-8")
    last_position = -1
    for lane in lanes:
        output_path = _inside(config["project_root"], lane.get("output_path"))
        artifact_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
        if lane.get("artifact_sha256") != artifact_sha:
            raise MultiError(f"lane {lane.get('id')} handoff hash mismatch")
        position = merger_text.find(str(output_path), last_position + 1)
        if position < 0:
            raise MultiError(f"lane {lane.get('id')} is absent or out of order in the merger mission")
        last_position = position
    merger_manifest = _child_manifest(
        config,
        {"id": "merger", "mission_path": merger_mission},
        parent_id,
    )
    capability, tokens = CAPABILITY.resume_web_multi(config["project_root"])
    contract = capability.contract()
    binding = contract.get("binding") if isinstance(contract.get("binding"), dict) else {}
    controls = binding.get("host_control_paths") if isinstance(binding, dict) else None
    if controls != [str(config["output_dir"])]:
        raise MultiError("active capability does not bind the exact merger control root")
    normalized_lanes = [dict(item) for item in lanes if isinstance(item, dict)]
    submitting = _result_base(
        status="merger_submitting",
        ok=False,
        writes_performed=True,
        merger_count=1,
        lanes=normalized_lanes,
        merger_run_dir=result.get("merger_run_dir"),
        capability=_capability_evidence(capability),
        **{
            key: value
            for key, value in result.items()
            if key not in {"schema", "status", "ok", "writes_performed", "merger_count", "lanes", "merger_run_dir", "capability", "merger_submission_count"}
        },
        merger_submission_count=1,
    )
    _write_json(result_path, submitting)
    merger = execute(
        merger_manifest,
        dry_run=False,
        capability_token=tokens["merger"],
    )
    previous = [str(item) for item in result.get("prior_merger_run_dirs") or [] if str(item)]
    if result.get("merger_run_dir"):
        previous.append(str(result["merger_run_dir"]))
    merger_state = _dict(merger.get("result"))
    terminal = merger_state.get("terminal_harvested") is True
    materialized, materialization_error = (
        _bound_merger_result(config, parent_id, merger)
        if merger.get("ok") and terminal
        else (None, None)
    )
    receipt = CAPABILITY.finish(
        capability,
        terminal_harvested=terminal,
    )
    passed = bool(merger.get("ok")) and terminal and materialization_error is None
    status = "complete" if passed else "failed" if terminal else "attention_required"
    updated = _result_base(
        status=status,
        ok=status == "complete",
        writes_performed=True,
        merger_count=1,
        lanes=normalized_lanes,
        merger_run_dir=merger.get("run_dir"),
        capability=_capability_evidence(capability, receipt),
        **{
            key: value
            for key, value in result.items()
            if key not in {"schema", "status", "ok", "writes_performed", "merger_count", "lanes", "merger_run_dir", "capability", "prior_merger_run_dirs", "merger_submission_count", "next_stage_result_path", "merger_materialization_error"}
        },
        prior_merger_run_dirs=list(dict.fromkeys(previous)),
        merger_submission_count=1,
        next_stage_result_path=str(materialized) if materialized else None,
        **({"merger_materialization_error": materialization_error} if materialization_error else {}),
    )
    _write_json(result_path, updated)
    return updated
