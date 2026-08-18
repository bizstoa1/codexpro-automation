from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class ContractUnavailableError(RuntimeError):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"Oracle archived settlement contract unavailable: {path}")


def _load_contract():
    path = Path(__file__).resolve().with_name("chatgpt_oracle_archived_contract.py")
    spec = importlib.util.spec_from_file_location("oracle_archived_settlement_contract", path)
    if spec is None or spec.loader is None:
        raise ContractUnavailableError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()

CONFIRMATION_TOKEN = CONTRACT.CONFIRMATION_TOKEN
SettlementError = CONTRACT.SettlementError


def _create_bytes_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise SettlementError("OUTPUT_MUST_BE_ABSENT", "exact run output already exists") from exc


def _create_json_exclusive(path: Path, payload: JsonObject) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def settle(
    manifest_path: Path,
    *,
    confirmation: str,
    process_alive: Callable[[int], bool] = CONTRACT.RUNNER.process_is_alive,
) -> JsonObject:
    if confirmation.strip() != CONFIRMATION_TOKEN:
        raise SettlementError("CONFIRMATION_REQUIRED", f"confirmation must be exactly {CONFIRMATION_TOKEN}")
    preliminary = CONTRACT.load_binding(manifest_path)
    with CONTRACT.STATE.project_submit_mutex(preliminary.project_root, timeout_seconds=30):
        binding = CONTRACT.load_binding(manifest_path)
        if binding.project_root != preliminary.project_root:
            raise SettlementError("MANIFEST_CHANGED", "project binding changed while acquiring the exact mutex")
        state, checked_pids, final_bytes = CONTRACT.validate(binding, process_alive)
        output_path = binding.run_dir / "output.md"
        receipt_path = binding.run_dir / CONTRACT.SETTLEMENT_NAME
        _create_bytes_exclusive(output_path, final_bytes)
        recorded: JsonObject = {
            "schema": CONTRACT.RECEIPT_SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "confirmation": CONFIRMATION_TOKEN,
            "bindings": {
                "exact_run_id": binding.exact_run_id,
                "session_locator": binding.session_locator,
                "project_root": str(binding.project_root),
                "run_dir": str(binding.run_dir),
                "state_path": str(binding.run_dir / "state.json"),
                "state_sha256": binding.state_sha256,
                "transcript_path": str(binding.transcript_path),
                "transcript_sha256": binding.transcript_sha256,
                "final_gate_output_path": str(binding.final_gate_output_path),
                "final_gate_output_sha256": binding.final_gate_output_sha256,
                "pass_stage_receipt_path": str(binding.pass_stage_receipt_path),
                "pass_stage_receipt_sha256": binding.pass_stage_receipt_sha256,
                "prior_runtime_release_receipt_path": str(binding.prior_runtime_release_receipt_path),
                "prior_runtime_release_receipt_sha256": binding.prior_runtime_release_receipt_sha256,
            },
            "output_path": str(output_path),
            "output_sha256": CONTRACT.sha(output_path),
            "active_pid_count": 0,
            "run_owned_pids_checked": list(checked_pids),
            "zero_action_counters": dict(CONTRACT.ZERO_ACTION_COUNTERS),
            "state_transition": {
                "status": "complete",
                "session_authority": "settled_executed",
                "terminal_harvested": False,
                "task_outcome": "executed",
            },
        }
        _create_json_exclusive(receipt_path, recorded)
        receipt_sha256 = CONTRACT.sha(receipt_path)
        state.update({
            "status": "complete",
            "session_authority": "settled_executed",
            "terminal_harvested": False,
            "transport_status": "archived_exact_transcript_settled",
            "task_outcome": "executed",
            "task_outcome_reason": "user-confirmed-hash-bound-archived-exact-transcript",
            "artifact_sha256": CONTRACT.sha(output_path),
            "archived_exact_transcript_settlement": {
                "schema": "codex.chatgpt.oracle-settlement-reference/v1",
                "path": str(receipt_path),
                "sha256": receipt_sha256,
            },
        })
        CONTRACT.STATE.write_json_atomic(binding.run_dir / "state.json", state)
    return {
        "ok": True,
        "status": "archived_exact_transcript_settled",
        "run_dir": str(binding.run_dir),
        "output_sha256": binding.final_gate_output_sha256,
        "settlement_receipt_path": str(receipt_path),
        "settlement_receipt_sha256": receipt_sha256,
        "zero_action_counters": dict(CONTRACT.ZERO_ACTION_COUNTERS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Settle one approved archived exact Oracle transcript")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--confirmation", required=True)
    args = parser.parse_args(argv)
    try:
        result = settle(args.manifest, confirmation=args.confirmation)
    except SettlementError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
