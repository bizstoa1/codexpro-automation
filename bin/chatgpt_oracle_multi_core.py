from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

SCHEMA = "codex.chatgpt.oracle-multi/v2"
RESULT_SCHEMA = "codex.chatgpt.oracle-multi-result/v2"
MERGER_OUTPUT_SCHEMA = "codex.chatgpt.oracle-multi-merger-output/v1"
MERGER_OUTPUT_KEYS = {
    "schema", "workflow_id", "stage", "attempt_id", "input_mission_sha256",
    "status", "output_text", "next_stage", "next_mission_text", "ready_for_next", "blocker",
}
LANE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
BIN = Path(__file__).resolve().parent


class MultiError(RuntimeError):
    pass


class MultiModuleLoadError(RuntimeError):
    def __init__(self, path: Path):
        super().__init__(f"module unavailable: {path}")
        self.path = path


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise MultiModuleLoadError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load("chatgpt_oracle_multi_runner", BIN / "chatgpt_oracle_run.py")
STATE = RUNNER.STATE
WORKSPACE_CONFIG = _load("chatgpt_oracle_multi_workspace_config", BIN / "chatgpt_workspace_config.py")
CAPABILITY = _load("chatgpt_oracle_multi_capability", BIN / "chatgpt_capability_runtime.py")



def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MultiError("manifest must be a JSON object")
    return value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _inside(root: Path, value: Any, *, exists: bool = True) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        raise MultiError("all paths must be absolute")
    path = path.resolve(strict=exists)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MultiError(f"path outside project: {path}") from exc
    return path


def load_manifest(path: Path) -> dict[str, Any]:
    value = _read_json(path.resolve(strict=True))
    if value.get("schema") != SCHEMA:
        raise MultiError(f"schema must be {SCHEMA}")
    root = Path(str(value.get("project_root") or "")).expanduser().resolve(strict=True)
    output_dir = _inside(root, value.get("output_dir"), exists=False)
    protected = ((root / ".git").resolve(strict=False), (root / ".codex").resolve(strict=False))
    if output_dir == root or any(output_dir == item or item in output_dir.parents for item in protected):
        raise MultiError("output_dir must be a dedicated non-control project subtree")
    if value.get("allowed_worktree_roots") or value.get("parent_capability_id"):
        raise MultiError("Web Multi v2 forbids worktrees and nested capabilities")
    if (
        value.get("completion_policy") != "all-lanes"
        or value.get("merger_policy") != "exactly-one"
        or value.get("nesting") != "forbidden"
    ):
        raise MultiError("Web Multi v2 requires all lanes, exactly one merger, and no nesting")
    solvers = value.get("solvers")
    if not isinstance(solvers, list) or not 2 <= len(solvers) <= 25:
        raise MultiError("solvers must contain 2..25 lanes")
    normalized = []
    seen = set()
    for index, item in enumerate(solvers):
        if not isinstance(item, dict):
            raise MultiError("each solver must be an object")
        if set(item) != {"id", "mission_path", "access"}:
            raise MultiError("solver fields must be id, mission_path, and access")
        lane = str(item.get("id") or f"solver-{index}").strip()
        if LANE_RE.fullmatch(lane) is None or lane in seen:
            raise MultiError("solver ids must be unique")
        seen.add(lane)
        access = str(item.get("access") or "")
        if access != "read-only":
            raise MultiError("Web Multi v2 solver access must be read-only")
        normalized.append({
            "id": lane,
            "mission_path": _inside(root, item.get("mission_path")),
            "access": access,
            "project_root": root,
        })
    merger = _inside(root, value.get("merger_mission_path"))
    if any(item["mission_path"] == merger for item in normalized):
        raise MultiError("merger mission must be distinct from solver missions")
    next_stage_result = (
        _inside(root, value.get("next_stage_result_path"), exists=False)
        if value.get("next_stage_result_path")
        else None
    )
    if next_stage_result is not None and output_dir not in next_stage_result.parents:
        raise MultiError("next_stage_result_path must stay under output_dir")
    raw_binding = value.get("next_stage_binding")
    if next_stage_result is not None:
        if (
            not isinstance(raw_binding, dict)
            or set(raw_binding) != {"workflow_id", "stage"}
            or re.fullmatch(r"[0-9a-f]{32}", str(raw_binding.get("workflow_id") or "")) is None
            or raw_binding.get("stage") != "web-multi"
        ):
            raise MultiError("next_stage_result_path requires an exact web-multi workflow binding")
    elif raw_binding is not None:
        raise MultiError("next_stage_binding requires next_stage_result_path")
    concurrency = int(value.get("max_concurrency", 5))
    if not 1 <= concurrency <= 5:
        raise MultiError("max_concurrency must be within 1..5")
    try:
        app_name = WORKSPACE_CONFIG.normalize_app_name(
            value.get("app_name") or WORKSPACE_CONFIG.configured_app_name()
        )
    except ValueError as exc:
        raise MultiError(str(exc)) from exc
    try:
        host_policy = STATE.HOST_POLICY.load_host_policy(STATE.oracle_state_root())
    except STATE.HOST_POLICY.OracleHostPolicyError as exc:
        raise MultiError(f"{exc.code}: {exc}") from exc
    explicit_profile_raw = str(value.get("copy_profile") or "").strip()
    if explicit_profile_raw:
        explicit_profile = Path(explicit_profile_raw).expanduser().resolve(strict=True)
        if explicit_profile != host_policy.profile_seed:
            raise MultiError("HOST_PROFILE_SEED_MISMATCH: copy_profile must match host policy")
    if concurrency > host_policy.max_total_concurrency:
        raise MultiError("HOST_CONCURRENCY_MISMATCH: max_concurrency exceeds host policy")
    return {
        **value,
        "project_root": root,
        "output_dir": output_dir,
        "solvers": normalized,
        "merger_mission_path": merger,
        "next_stage_result_path": next_stage_result,
        "max_concurrency": concurrency,
        "app_name": app_name,
        "model": str(value.get("model") or "gpt-5.6").strip(),
        "copy_profile": host_policy.profile_seed,
        "host_policy_sha256": host_policy.sha256,
        "manifest_sha256": hashlib.sha256(path.resolve(strict=True).read_bytes()).hexdigest(),
        "manifest_path": path.resolve(strict=True),
        "next_stage_binding": dict(raw_binding) if isinstance(raw_binding, dict) else {},
    }


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_bytes_atomic(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _capability_evidence(capability: Any = None, receipt: Any = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    if capability is not None:
        evidence.update({
            "contract_sha256": hashlib.sha256(capability.contract_json.encode("utf-8")).hexdigest(),
            "lease_id": capability.lease_id,
            "lease_created": capability.lease_id is not None,
            "status": "active" if capability.lease_id is not None else "dry-run",
        })
    if isinstance(receipt, dict):
        evidence["status"] = str(receipt.get("status") or "unknown")
        evidence["receipt"] = receipt
    return evidence


def _result_base(
    *,
    status: str,
    ok: bool,
    writes_performed: bool,
    merger_count: int,
    lanes: list[dict[str, Any]],
    merger_run_dir: Any,
    capability: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "status": status,
        "ok": ok,
        "writes_performed": writes_performed,
        "merger_count": merger_count,
        "lanes": lanes,
        "merger_run_dir": merger_run_dir,
        "capability": capability,
        **extra,
    }
