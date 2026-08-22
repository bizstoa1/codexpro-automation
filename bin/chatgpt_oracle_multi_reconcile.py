from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any


CORE = sys.modules.get("chatgpt_oracle_multi_core")
ARTIFACTS = sys.modules.get("chatgpt_oracle_multi_artifacts")
if CORE is None or ARTIFACTS is None:
    raise ImportError("Web Multi core and artifacts must be loaded first")
RESULT_SCHEMA = CORE.RESULT_SCHEMA
STATE = CORE.STATE
MultiError = CORE.MultiError
_capability_evidence = CORE._capability_evidence
_dict = CORE._dict
_read_json = CORE._read_json
_result_base = CORE._result_base
_write_json = CORE._write_json
load_manifest = CORE.load_manifest
_merger_transport = ARTIFACTS._merger_transport
_write_bytes_atomic = ARTIFACTS._write_bytes_atomic


def reconcile_recovered_lanes(manifest_path: Path) -> dict[str, Any]:
    config = load_manifest(manifest_path)
    with STATE.project_submit_mutex(config["project_root"], timeout_seconds=30):
        return _reconcile_recovered_lanes(config)


def _reconcile_recovered_lanes(config: dict[str, Any]) -> dict[str, Any]:
    result_path = config["output_dir"] / "result.json"
    result = _read_json(result_path)
    if result.get("schema") != RESULT_SCHEMA:
        raise MultiError("existing multi result schema is invalid")
    if (
        result.get("manifest_sha256") != config["manifest_sha256"]
        or result.get("merger_count") != 0
        or result.get("merger_submission_count") != 0
        or result.get("merger_run_dir") is not None
        or result.get("prior_merger_run_dirs") not in (None, [])
    ):
        raise MultiError("prior merger submission evidence forbids lane reconciliation")
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
        merger_run_dir=None,
        capability=(
            dict(result["capability"])
            if isinstance(result.get("capability"), dict)
            else {"status": "active-recovery", "lease_created": True}
        ),
        parent_id=parent_id,
        manifest_sha256=config["manifest_sha256"],
        successful_lane_count=len(reconciled),
        merger_mission_path=str(merger_mission),
        merger_submission_count=0,
        prior_merger_run_dirs=[],
        recovery_mode="exact-runs-no-submit",
    )
    _write_json(result_path, updated)
    return updated
