from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Literal, assert_never

import pytest
from jsonschema import Draft202012Validator

HELPERS_PATH = Path(__file__).resolve().parent / "test_chatgpt_oracle_multi.py"
ATTESTATION_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "oracle-multi-completion-attestation-v1.schema.json"
)
HELPERS_SPEC = importlib.util.spec_from_file_location("oracle_multi_security_helpers", HELPERS_PATH)
assert HELPERS_SPEC is not None and HELPERS_SPEC.loader is not None
helpers = importlib.util.module_from_spec(HELPERS_SPEC)
sys.modules[HELPERS_SPEC.name] = helpers
HELPERS_SPEC.loader.exec_module(helpers)

Mutation = Literal["result-bytes", "parent", "manifest", "receipt-path", "receipt-bytes"]
RecoveryContender = Literal["resume", "reconcile"]


def _successful_execute(path: Path, *, dry_run: bool, capability_token: str):
    run_dir = path.parent / "fake-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.md").write_text(f"answer {path.parent.name}", encoding="utf-8")
    return {"ok": True, "run_dir": str(run_dir), "result": {"terminal_harvested": True}}


def _bound_completion(tmp_path: Path):
    module = helpers.load()
    manifest = helpers.make_manifest(tmp_path, 2)
    receipt = tmp_path / "out" / "merger" / "stage-result.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["next_stage_result_path"] = str(receipt.resolve())
    payload["next_stage_binding"] = {"workflow_id": "a" * 32, "stage": "web-multi"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()

    def execute(path: Path, *, dry_run: bool, capability_token: str):
        child = json.loads(path.read_text(encoding="utf-8"))
        if child["capability_subject_id"] == "merger":
            run_dir = Path(os.environ["CODEX_ORACLE_STATE_ROOT"]) / "merger-run"
            output = {
                "schema": module.MERGER_OUTPUT_SCHEMA,
                "workflow_id": "a" * 32,
                "stage": "web-multi",
                "attempt_id": child["parallel_parent_id"],
                "input_mission_sha256": manifest_sha,
                "status": "PASS",
                "output_text": "Merged advisory\n",
                "next_stage": "review",
                "next_mission_text": "Review the merged advisory.\n",
                "ready_for_next": True,
                "blocker": "",
            }
        else:
            run_dir = path.parent / "fake-run"
            output = f"answer {path.parent.name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(output) if isinstance(output, dict) else output
        (run_dir / "output.md").write_text(rendered, encoding="utf-8")
        return {"ok": True, "run_dir": str(run_dir), "result": {"terminal_harvested": True}}

    result = module.run_multi(manifest, execute=execute)
    expectation = module.CompletionExpectation(
        tmp_path.resolve(),
        manifest_sha,
        tmp_path / "out" / "result.json",
        receipt,
    )
    return module, result, expectation, receipt


def _ready_recovered_merger(tmp_path: Path) -> tuple[Any, Path]:
    module = helpers.load()
    manifest = helpers.make_manifest(tmp_path, 2)
    config = module.load_manifest(manifest)
    module.CAPABILITY.open_web_multi(
        config["project_root"],
        [(lane["id"], lane["mission_path"]) for lane in config["solvers"]],
        config["merger_mission_path"],
        max_concurrency=config["max_concurrency"],
        subjects=[lane["id"] for lane in config["solvers"]] + ["merger"],
        control_root=config["output_dir"],
    )
    state_root = Path(os.environ["CODEX_ORACLE_STATE_ROOT"])
    parent_id = "c" * 64
    recorded: list[dict[str, str]] = []
    for lane in config["solvers"]:
        run_dir = state_root / f"recovery-{lane['id']}"
        run_dir.mkdir(parents=True)
        output = run_dir / "output.md"
        output.write_text(f"answer {lane['id']}", encoding="utf-8")
        artifact_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        locator = f"oracle-{lane['id']}"
        (run_dir / "state.json").write_text(json.dumps({
            "project_root": str(tmp_path.resolve()),
            "parallel_parent_id": parent_id,
            "status": "complete",
            "terminal_harvested": True,
            "artifact_sha256": artifact_sha,
            "mission": {"sha256": hashlib.sha256(lane["mission_path"].read_bytes()).hexdigest()},
            "oracle": {"session_locator": locator},
        }), encoding="utf-8")
        recorded.append({"id": lane["id"], "run_dir": str(run_dir), "session_locator": locator})
    module._write_json(config["output_dir"] / "result.json", {
        "schema": module.RESULT_SCHEMA,
        "status": "failed",
        "ok": False,
        "writes_performed": True,
        "parent_id": parent_id,
        "manifest_sha256": config["manifest_sha256"],
        "merger_count": 0,
        "merger_submission_count": 0,
        "lanes": recorded,
        "merger_run_dir": None,
        "capability": {"status": "retained"},
    })
    module.reconcile_recovered_lanes(manifest)
    return module, manifest


@pytest.mark.parametrize(
    ("terminal_harvested", "expected_status"),
    [(False, "attention_required"), (True, "failed")],
)
def test_multi_stops_before_later_wave_when_prior_ownership_is_not_successful(
    tmp_path: Path,
    terminal_harvested: bool,
    expected_status: str,
) -> None:
    module = helpers.load()
    calls: list[str] = []

    def execute(path: Path, *, dry_run: bool, capability_token: str):
        value = json.loads(path.read_text(encoding="utf-8"))
        calls.append(str(value["capability_subject_id"]))
        return {
            "ok": False,
            "run_dir": None,
            "result": {"terminal_harvested": terminal_harvested},
        }

    result = module.run_multi(helpers.make_manifest(tmp_path, 7), execute=execute)

    assert result["status"] == expected_status
    assert len(calls) == 5
    assert set(calls) == {"s0", "s1", "s2", "s3", "s4"}
    assert [lane["status"] for lane in result["lanes"][5:]] == ["pending", "pending"]


def test_reconcile_recovered_lanes_rejects_any_prior_merger_submission(
    tmp_path: Path,
) -> None:
    module = helpers.load()
    manifest = helpers.make_manifest(tmp_path, 2)
    config = module.load_manifest(manifest)
    module._write_json(
        config["output_dir"] / "result.json",
        {
            "schema": module.RESULT_SCHEMA,
            "status": "attention_required",
            "ok": False,
            "writes_performed": True,
            "parent_id": "a" * 64,
            "manifest_sha256": config["manifest_sha256"],
            "merger_count": 1,
            "merger_submission_count": 1,
            "merger_run_dir": str(tmp_path / "first-merger"),
            "prior_merger_run_dirs": [str(tmp_path / "older-merger")],
            "lanes": [
                {"id": lane["id"], "run_dir": "", "session_locator": ""}
                for lane in config["solvers"]
            ],
            "capability": {"status": "retained"},
        },
    )

    with pytest.raises(module.MultiError, match="prior merger submission"):
        module.reconcile_recovered_lanes(manifest)


@pytest.mark.parametrize("contender", ["resume", "reconcile"])
def test_recovery_mutex_prevents_concurrent_duplicate_merger_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contender: RecoveryContender,
) -> None:
    module, manifest = _ready_recovered_merger(tmp_path)
    mutex = threading.Lock()
    counter_guard = threading.Lock()
    first_execute_entered = threading.Event()
    second_waiting = threading.Event()
    release_first = threading.Event()
    entries = 0
    calls: list[dict[str, Any]] = []

    @contextmanager
    def gated_mutex(_root: Path, *, timeout_seconds: float):
        nonlocal entries
        assert timeout_seconds == 30
        with counter_guard:
            entries += 1
            entry = entries
        if entry == 2:
            second_waiting.set()
        assert mutex.acquire(timeout=5)
        try:
            yield
        finally:
            mutex.release()

    monkeypatch.setattr(module.STATE, "project_submit_mutex", gated_mutex)

    def execute(path: Path, *, dry_run: bool, capability_token: str) -> dict[str, Any]:
        assert not dry_run
        assert capability_token
        calls.append(json.loads(path.read_text(encoding="utf-8")))
        first_execute_entered.set()
        assert release_first.wait(timeout=5)
        return {
            "ok": True,
            "run_dir": str(tmp_path / "merger-run"),
            "result": {"terminal_harvested": True},
        }

    def contend() -> dict[str, Any]:
        match contender:
            case "resume":
                return module.resume_recovered_merger(manifest, execute=execute)
            case "reconcile":
                return module.reconcile_recovered_lanes(manifest)
            case _:
                assert_never(contender)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(module.resume_recovered_merger, manifest, execute=execute)
        assert first_execute_entered.wait(timeout=5)
        second = pool.submit(contend)
        assert second_waiting.wait(timeout=5)
        assert len(calls) == 1
        release_first.set()
        assert first.result(timeout=5)["status"] == "complete"
        with pytest.raises(module.MultiError):
            second.result(timeout=5)
    assert len(calls) == 1


def test_exact_host_completion_attestation_is_accepted(tmp_path: Path) -> None:
    module = helpers.load()
    manifest = helpers.make_manifest(tmp_path, 2)
    result = module.run_multi(manifest, execute=_successful_execute)
    verified = module.verify_completion(
        module.CompletionExpectation(
            tmp_path.resolve(),
            hashlib.sha256(manifest.read_bytes()).hexdigest(),
            tmp_path / "out" / "result.json",
            None,
        )
    )

    assert verified.parent_id == result["parent_id"]
    assert verified.result["status"] == "complete"
    assert verified.receipt_path is None
    state_root = Path(os.environ["CODEX_CAPABILITY_STATE_ROOT"])
    attestations = [path for path in state_root.rglob("*.json") if "completions" in path.parts]
    assert len(attestations) == 1
    attestation = json.loads(attestations[0].read_text(encoding="utf-8"))
    schema = json.loads(ATTESTATION_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(attestation)
    if os.name != "nt":
        assert attestations[0].stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "mutation",
    ["result-bytes", "parent", "manifest", "receipt-path", "receipt-bytes"],
)
def test_host_completion_attestation_rejects_project_visible_drift(
    tmp_path: Path,
    mutation: Mutation,
) -> None:
    module, _result, expectation, receipt = _bound_completion(tmp_path)
    result_path = expectation.result_path
    value = json.loads(result_path.read_text(encoding="utf-8"))
    match mutation:
        case "result-bytes":
            value["forged"] = True
            module._write_json(result_path, value)
        case "parent":
            value["parent_id"] = "f" * 64
            module._write_json(result_path, value)
        case "manifest":
            value["manifest_sha256"] = "f" * 64
            module._write_json(result_path, value)
        case "receipt-path":
            value["next_stage_result_path"] = str(tmp_path / "forged-receipt.json")
            module._write_json(result_path, value)
        case "receipt-bytes":
            receipt.write_bytes(receipt.read_bytes() + b"\n")
        case _:
            assert_never(mutation)

    with pytest.raises(module.MultiError):
        module.verify_completion(expectation)
