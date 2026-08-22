from __future__ import annotations

import hashlib
import sys
import uuid
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

CORE = sys.modules.get("chatgpt_oracle_multi_core")
ARTIFACTS = sys.modules.get("chatgpt_oracle_multi_artifacts")
if CORE is None or ARTIFACTS is None:
    raise ImportError("Web Multi core and artifacts must be loaded first")
CAPABILITY = CORE.CAPABILITY
RUNNER = CORE.RUNNER
STATE = CORE.STATE
MultiError = CORE.MultiError
_capability_evidence = CORE._capability_evidence
_dict = CORE._dict
_result_base = CORE._result_base
_write_json = CORE._write_json
load_manifest = CORE.load_manifest
_bound_merger_result = ARTIFACTS._bound_merger_result
_child_manifest = ARTIFACTS._child_manifest
_merger_transport = ARTIFACTS._merger_transport
_run_lane = ARTIFACTS._run_lane


def run_multi(
    manifest_path: Path,
    *,
    dry_run: bool = False,
    execute: Callable[..., dict[str, Any]] = RUNNER.execute_run,
    parent_lock_held: bool = False,
) -> dict[str, Any]:
    config = load_manifest(manifest_path)
    subjects = [item["id"] for item in config["solvers"]] + ["merger"]
    capability, tokens = CAPABILITY.open_web_multi(
        config["project_root"],
        [(item["id"], item["mission_path"]) for item in config["solvers"]],
        config["merger_mission_path"],
        max_concurrency=config["max_concurrency"],
        subjects=subjects,
        control_root=config["output_dir"],
        dry_run=dry_run,
    )
    if dry_run:
        return _result_base(
            status="dry-run",
            ok=True,
            writes_performed=False,
            merger_count=1,
            lanes=[{"id": item, "status": "planned"} for item in subjects[:-1]],
            merger_run_dir=None,
            capability=_capability_evidence(capability),
            manifest_sha256=config["manifest_sha256"],
            max_concurrency=config["max_concurrency"],
        )
    if config["output_dir"].exists() or config["output_dir"].is_symlink():
        CAPABILITY.finish(
            capability,
            terminal_harvested=False,
            safe_pre_submit=True,
            pre_submit_reason="web-multi-output-exists",
        )
        raise MultiError("output_dir already exists; v2 refuses overwrite or resume-by-replacement")
    parent_id = hashlib.sha256(f"{config['project_root']}:{uuid.uuid4().hex}".encode()).hexdigest()
    config["output_dir"].mkdir(parents=True, exist_ok=False)
    result_path = config["output_dir"] / "result.json"
    lanes: list[dict[str, Any]] = []

    def running_result() -> dict[str, Any]:
        completed = {item["id"]: item for item in lanes}
        ordered = [
            completed.get(item["id"], {"id": item["id"], "status": "pending"})
            for item in config["solvers"]
        ]
        return _result_base(
            status="running",
            ok=False,
            writes_performed=True,
            merger_count=0,
            lanes=ordered,
            merger_run_dir=None,
            capability=_capability_evidence(capability),
            parent_id=parent_id,
            manifest_sha256=config["manifest_sha256"],
            successful_lane_count=len(
                [item for item in lanes if item.get("ok") and item.get("terminal_harvested")]
            ),
        )

    _write_json(result_path, running_result())
    # The parent owns normal same-project exclusion. Children use the separate
    # parent-scoped launch mutex and may wait concurrently after submission.
    lock = nullcontext() if parent_lock_held else STATE.project_submit_mutex(config["project_root"], timeout_seconds=30)
    with lock:
        for start in range(0, len(config["solvers"]), config["max_concurrency"]):
            wave = config["solvers"][start : start + config["max_concurrency"]]
            with ThreadPoolExecutor(max_workers=len(wave), thread_name_prefix="oracle-multi") as pool:
                futures = {
                    pool.submit(
                        _run_lane,
                        config,
                        lane,
                        parent_id,
                        execute,
                        False,
                        tokens[lane["id"]],
                    ): lane
                    for lane in wave
                }
                for future in as_completed(futures):
                    lane = futures[future]
                    try:
                        lanes.append(future.result())
                    except Exception as exc:  # noqa: BROAD_EXCEPT_OK - isolate one provider lane failure
                        lanes.append({
                            "id": lane["id"],
                            "ok": False,
                            "run_dir": None,
                            "output_path": None,
                            "session_locator": None,
                            "terminal_harvested": False,
                            "error_code": str(getattr(exc, "code", "ORACLE_LANE_ATTENTION_REQUIRED")),
                        })
                    _write_json(result_path, running_result())
        order = {item["id"]: index for index, item in enumerate(config["solvers"])}
        lanes.sort(key=lambda item: order[item["id"]])
        successful = [
            item
            for item in lanes
            if item["ok"] and item["output_path"] and item["terminal_harvested"]
        ]
        if len(successful) != len(lanes):
            all_terminal = all(item["terminal_harvested"] for item in lanes)
            receipt = CAPABILITY.finish(
                capability,
                terminal_harvested=all_terminal,
            )
            result = _result_base(
                status="failed" if all_terminal else "attention_required",
                ok=False,
                writes_performed=True,
                merger_count=0,
                lanes=lanes,
                merger_run_dir=None,
                capability=_capability_evidence(capability, receipt),
                parent_id=parent_id,
                manifest_sha256=config["manifest_sha256"],
                successful_lane_count=len(successful),
            )
            _write_json(result_path, result)
            return result
        merger_mission = _merger_transport(config, successful, parent_id)
        merger_manifest = _child_manifest(
            config,
            {"id": "merger", "mission_path": merger_mission},
            parent_id,
        )
        _write_json(
            result_path,
            _result_base(
                status="merger_submitting",
                ok=False,
                writes_performed=True,
                merger_count=1,
                lanes=lanes,
                merger_run_dir=None,
                capability=_capability_evidence(capability),
                parent_id=parent_id,
                manifest_sha256=config["manifest_sha256"],
                successful_lane_count=len(successful),
                merger_mission_path=str(merger_mission),
                merger_submission_count=1,
            ),
        )
        merger = execute(
            merger_manifest,
            dry_run=False,
            capability_token=tokens["merger"],
        )
    merger_state = _dict(merger.get("result"))
    terminal = (
        all(item["terminal_harvested"] for item in lanes)
        and merger_state.get("terminal_harvested") is True
    )
    materialized, materialization_error = (
        _bound_merger_result(config, parent_id, merger)
        if merger.get("ok") and terminal
        else (None, None)
    )
    receipt = CAPABILITY.finish(capability, terminal_harvested=terminal)
    passed = bool(merger.get("ok")) and terminal and materialization_error is None
    status = "complete" if passed else "failed" if terminal else "attention_required"
    result = _result_base(
        status=status,
        ok=status == "complete",
        writes_performed=True,
        merger_count=1,
        lanes=lanes,
        merger_run_dir=merger.get("run_dir"),
        capability=_capability_evidence(capability, receipt),
        parent_id=parent_id,
        manifest_sha256=config["manifest_sha256"],
        successful_lane_count=len(successful),
        next_stage_result_path=str(materialized) if materialized else None,
        merger_mission_path=str(merger_mission),
        merger_submission_count=1,
        **({"merger_materialization_error": materialization_error} if materialization_error else {}),
    )
    _write_json(result_path, result)
    return result
