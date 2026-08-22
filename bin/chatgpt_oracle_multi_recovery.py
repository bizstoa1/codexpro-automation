from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Callable

CORE = sys.modules.get("chatgpt_oracle_multi_core")
ARTIFACTS = sys.modules.get("chatgpt_oracle_multi_artifacts")
ATTESTATION = sys.modules.get("chatgpt_oracle_multi_attestation")
if CORE is None or ARTIFACTS is None or ATTESTATION is None:
    raise ImportError("Web Multi core, artifacts, and attestation must be loaded first")
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
CompletionExpectation = ATTESTATION.CompletionExpectation
attest_completion = ATTESTATION.attest_completion


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
    with STATE.project_submit_mutex(config["project_root"], timeout_seconds=30):
        return _resume_recovered_merger(config, execute)


def _resume_recovered_merger(
    config: dict[str, Any],
    execute: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    result_path = config["output_dir"] / "result.json"
    result = _read_json(result_path)
    if result.get("schema") != RESULT_SCHEMA or result.get("status") != "merger_ready":
        raise MultiError("multi result is not ready for merger-only resume")
    if (
        result.get("manifest_sha256") != config["manifest_sha256"]
        or result.get("merger_count") != 0
        or result.get("merger_submission_count") != 0
        or result.get("merger_run_dir") is not None
        or result.get("prior_merger_run_dirs") not in (None, [])
    ):
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
    expected_contract = CAPABILITY.PROJECT.compile_web_multi_contract(
        config["project_root"],
        [(lane["id"], lane["mission_path"]) for lane in config["solvers"]],
        config["merger_mission_path"],
        max_concurrency=config["max_concurrency"],
        control_root=config["output_dir"],
    )
    if capability.contract_json != expected_contract.canonical_json:
        raise MultiError("active capability does not bind the exact Web Multi manifest")
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
    if status == "complete":
        if capability.lease_id is None:
            raise MultiError("complete Web Multi recovery has no lease identity")
        attest_completion(
            CompletionExpectation(
                config["project_root"],
                config["manifest_sha256"],
                result_path,
                materialized,
            ),
            capability.lease_id,
            hashlib.sha256(capability.contract_json.encode("utf-8")).hexdigest(),
        )
    return updated
