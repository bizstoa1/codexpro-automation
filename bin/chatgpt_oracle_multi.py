from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Iterable

BIN = Path(__file__).resolve().parent


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


CORE = _load("chatgpt_oracle_multi_core", BIN / "chatgpt_oracle_multi_core.py")
ARTIFACTS = _load("chatgpt_oracle_multi_artifacts", BIN / "chatgpt_oracle_multi_artifacts.py")
ATTESTATION = _load(
    "chatgpt_oracle_multi_attestation",
    BIN / "chatgpt_oracle_multi_attestation.py",
)
RECONCILE = _load(
    "chatgpt_oracle_multi_reconcile",
    BIN / "chatgpt_oracle_multi_reconcile.py",
)
RECOVERY = _load("chatgpt_oracle_multi_recovery", BIN / "chatgpt_oracle_multi_recovery.py")
EXECUTION = _load("chatgpt_oracle_multi_execution", BIN / "chatgpt_oracle_multi_execution.py")

SCHEMA = CORE.SCHEMA
RESULT_SCHEMA = CORE.RESULT_SCHEMA
MERGER_OUTPUT_SCHEMA = CORE.MERGER_OUTPUT_SCHEMA
MERGER_OUTPUT_KEYS = CORE.MERGER_OUTPUT_KEYS
RUNNER = CORE.RUNNER
STATE = CORE.STATE
WORKSPACE_CONFIG = CORE.WORKSPACE_CONFIG
CAPABILITY = CORE.CAPABILITY
MultiError = CORE.MultiError
_read_json = CORE._read_json
_dict = CORE._dict
_inside = CORE._inside
load_manifest = CORE.load_manifest
_write_bytes_atomic = CORE._write_bytes_atomic
_write_json = CORE._write_json
_capability_evidence = CORE._capability_evidence
_result_base = CORE._result_base
_child_manifest = ARTIFACTS._child_manifest
_run_lane = ARTIFACTS._run_lane
_merger_transport = ARTIFACTS._merger_transport
_load_merger_envelope = ARTIFACTS._load_merger_envelope
_materialize_bound_merger = ARTIFACTS._materialize_bound_merger
_bound_merger_result = ARTIFACTS._bound_merger_result
ATTESTATION_SCHEMA = ATTESTATION.ATTESTATION_SCHEMA
CompletionExpectation = ATTESTATION.CompletionExpectation
VerifiedCompletion = ATTESTATION.VerifiedCompletion
attest_completion = ATTESTATION.attest_completion
verify_completion = ATTESTATION.verify_completion
reconcile_recovered_lanes = RECONCILE.reconcile_recovered_lanes
resume_recovered_merger = RECOVERY.resume_recovered_merger
run_multi = EXECUTION.run_multi


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run independent Oracle browser sessions in waves and merge handoffs.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reconcile-recovered", action="store_true")
    parser.add_argument("--resume-merger", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.reconcile_recovered and args.resume_merger:
            raise MultiError("choose exactly one recovery action")
        if args.reconcile_recovered:
            if args.dry_run:
                raise MultiError("--reconcile-recovered cannot be combined with --dry-run")
            result = reconcile_recovered_lanes(args.manifest)
        elif args.resume_merger:
            result = resume_recovered_merger(args.manifest, dry_run=args.dry_run)
        else:
            result = run_multi(args.manifest, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK - CLI boundary renders one structured failure
        result = {"ok": False, "error": {"code": "ORACLE_MULTI_FAILED", "message": str(exc)}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
