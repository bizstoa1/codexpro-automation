from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

CORE = sys.modules.get("chatgpt_oracle_multi_core")
if CORE is None:
    raise ImportError("chatgpt_oracle_multi_core must be loaded first")
MERGER_OUTPUT_KEYS = CORE.MERGER_OUTPUT_KEYS
MERGER_OUTPUT_SCHEMA = CORE.MERGER_OUTPUT_SCHEMA
STATE = CORE.STATE
MultiError = CORE.MultiError
_dict = CORE._dict
_read_json = CORE._read_json
_write_bytes_atomic = CORE._write_bytes_atomic
_write_json = CORE._write_json


def _child_manifest(config: dict[str, Any], lane: dict[str, Any], parent_id: str) -> Path:
    lane_root = config["output_dir"] / "lanes" / lane["id"]
    manifest = lane_root / "oracle.json"
    provenance = lane_root / "child-provenance.json"
    _write_json(provenance, {
        "schema": "codex.chatgpt.oracle-multi-child-provenance/v1",
        "parent_id": parent_id,
        "parent_manifest_path": str(config["manifest_path"]),
        "parent_manifest_sha256": config["manifest_sha256"],
        "project_root": str(lane.get("project_root") or config["project_root"]),
        "lane_id": lane["id"],
        "mission_path": str(lane["mission_path"]),
        "mission_sha256": hashlib.sha256(lane["mission_path"].read_bytes()).hexdigest(),
    })
    _write_json(
        manifest,
        {
            "schema": STATE.SCHEMA,
            "project_root": str(lane.get("project_root") or config["project_root"]),
            "mission_path": str(lane["mission_path"]),
            "app_name": config["app_name"],
            "mode": "browser",
            "model": config["model"],
            "model_strategy": "select",
            "thinking_time": "extra-high",
            "copy_profile": str(config["copy_profile"]),
            "research": "off",
            "archive": "auto",
            "parallel_parent_id": parent_id,
            "web_multi_child_provenance_path": str(provenance),
            "capability_required": True,
            "capability_kind": "web-multi-read-only",
            "capability_subject_id": lane["id"],
        },
    )
    return manifest


def _run_lane(
    config: dict[str, Any],
    lane: dict[str, Any],
    parent_id: str,
    execute: Callable[..., dict[str, Any]],
    dry_run: bool,
    capability_token: str,
) -> dict[str, Any]:
    manifest = _child_manifest(config, lane, parent_id)
    result = execute(manifest, dry_run=dry_run, capability_token=capability_token)
    output = None
    session_locator = None
    if not dry_run and result.get("run_dir"):
        run_dir = Path(str(result["run_dir"]))
        source = run_dir / "output.md"
        state_path = run_dir / "state.json"
        if state_path.is_file():
            state = _read_json(state_path)
            oracle = _dict(state.get("oracle"))
            session_locator = oracle.get("session_locator")
        if source.is_file() and source.read_bytes().strip():
            output = config["output_dir"] / "handoffs" / f"{lane['id']}.md"
            _write_bytes_atomic(output, source.read_bytes())
    return {
        "id": lane["id"],
        "ok": bool(result.get("ok")),
        "run_dir": result.get("run_dir"),
        "output_path": str(output) if output else None,
        "session_locator": session_locator,
        "terminal_harvested": (
            result.get("result", {}).get("terminal_harvested") is True
            if isinstance(result.get("result"), dict)
            else False
        ),
    }


def _merger_transport(
    config: dict[str, Any],
    successful: list[dict[str, Any]],
    parent_id: str,
) -> Path:
    source = config["merger_mission_path"].read_text(encoding="utf-8")
    paths = "\n".join(f"- {item['output_path']}" for item in successful)
    target = config["output_dir"] / "merger" / "mission.md"
    receipt_line = (
        "\n[BOUND_MERGER_OUTPUT_CONTRACT]\n"
        f"workflow_id={config['next_stage_binding'].get('workflow_id', '')}\n"
        f"stage={config['next_stage_binding'].get('stage', '')}\n"
        f"attempt_id={parent_id}\n"
        f"input_mission_sha256={config['manifest_sha256']}\n"
        "Remain read-only. Do not write a receipt or any project file. Return exactly one JSON object and no "
        "surrounding prose or Markdown fence. The exact keys are schema, workflow_id, stage, attempt_id, "
        "input_mission_sha256, status, output_text, next_stage, next_mission_text, ready_for_next, blocker. "
        f"schema must be {MERGER_OUTPUT_SCHEMA}. A passing result requires status=PASS, next_stage=review, "
        "ready_for_next=true, blocker=\"\", and nonempty output_text and next_mission_text. The host will "
        "validate the exact identities and materialize those strings locally.\n"
        if config.get("next_stage_result_path")
        else ""
    )
    _write_bytes_atomic(
        target,
        f"{source.rstrip()}\n\n[INPUT_HANDOFFS]\n{paths}\n{receipt_line}".encode("utf-8"),
    )
    return target


def _load_merger_envelope(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise MultiError(f"merger output contains duplicate key: {key}")
            value[key] = item
        return value

    try:
        envelope = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MultiError("bound merger output must be one strict UTF-8 JSON object") from exc
    if not isinstance(envelope, dict) or set(envelope) != MERGER_OUTPUT_KEYS:
        raise MultiError("bound merger output must contain the exact closed key set")
    return envelope


def _materialize_bound_merger(
    config: dict[str, Any],
    parent_id: str,
    merger: dict[str, Any],
) -> Path | None:
    receipt_path = config.get("next_stage_result_path")
    if receipt_path is None:
        return None
    run_dir = Path(str(merger.get("run_dir") or "")).expanduser()
    if not run_dir.is_absolute():
        raise MultiError("bound merger has no absolute exact run directory")
    output_source = (run_dir / "output.md").resolve(strict=True)
    if not STATE.is_within(STATE.oracle_state_root(), output_source):
        raise MultiError("bound merger output is outside Oracle host state")
    envelope = _load_merger_envelope(output_source)
    binding = config["next_stage_binding"]
    if (
        envelope.get("schema") != MERGER_OUTPUT_SCHEMA
        or envelope.get("workflow_id") != binding["workflow_id"]
        or envelope.get("stage") != "web-multi"
        or envelope.get("attempt_id") != parent_id
        or envelope.get("input_mission_sha256") != config["manifest_sha256"]
    ):
        raise MultiError("bound merger output identity mismatch")
    output_text = envelope.get("output_text")
    next_mission_text = envelope.get("next_mission_text")
    if (
        envelope.get("status") != "PASS"
        or envelope.get("next_stage") != "review"
        or envelope.get("ready_for_next") is not True
        or envelope.get("blocker") != ""
        or not isinstance(output_text, str)
        or not output_text.strip()
        or not isinstance(next_mission_text, str)
        or not next_mission_text.strip()
    ):
        raise MultiError("bound merger output did not pass")
    stage_dir = receipt_path.parent
    materialized_output = stage_dir / "output.md"
    next_mission = stage_dir / "next-mission.md"
    output_bytes = output_text.encode("utf-8")
    next_mission_bytes = next_mission_text.encode("utf-8")
    _write_bytes_atomic(materialized_output, output_bytes)
    _write_bytes_atomic(next_mission, next_mission_bytes)
    _write_json(receipt_path, {
        "schema": "codex.chatgpt.oracle-stage-result/v1",
        "workflow_id": binding["workflow_id"],
        "stage": "web-multi",
        "attempt_id": parent_id,
        "input_mission_sha256": config["manifest_sha256"],
        "status": "PASS",
        "output_path": str(materialized_output),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "next_stage": "review",
        "next_mission_path": str(next_mission),
        "next_mission_sha256": hashlib.sha256(next_mission_bytes).hexdigest(),
        "ready_for_next": True,
        "blocker": "",
    })
    return receipt_path


def _bound_merger_result(
    config: dict[str, Any],
    parent_id: str,
    merger: dict[str, Any],
) -> tuple[Path | None, str | None]:
    if config.get("next_stage_result_path") is None:
        return None, None
    try:
        return _materialize_bound_merger(config, parent_id, merger), None
    except (MultiError, OSError) as exc:
        return None, str(exc)
