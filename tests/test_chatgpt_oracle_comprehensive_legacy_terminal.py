from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


RUNTIME_PATH = (
    Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_comprehensive.py"
)


def load_runtime():
    spec = importlib.util.spec_from_file_location(
        "oracle_comprehensive_legacy_terminal_test",
        RUNTIME_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepare_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_root = tmp_path.parent / f"{tmp_path.name}-host-state"
    profile_seed = tmp_path.parent / f"{tmp_path.name}-profile-seed"
    state_root.mkdir()
    profile_seed.mkdir()
    policy = state_root / "host-policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema": "codex.chatgpt.oracle-host-policy/v1",
                "profile_seed": str(profile_seed),
                "profile_mode": "copy-per-run",
                "max_total_concurrency": 5,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(state_root))
    monkeypatch.setenv("CODEX_ORACLE_HOST_POLICY", str(policy))
    mission = tmp_path / "initial.md"
    mission.write_text("legacy terminal mission", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "codex.chatgpt.oracle-comprehensive/v1",
                "workflow_id": "a" * 32,
                "project_root": str(tmp_path),
                "workflow_dir": str(tmp_path / "workflow"),
                "initial_mission_path": str(mission),
                "app_name": "DevSpace",
                "model": "gpt-5.6",
                "local_gate_command": ["python", "-c", "raise SystemExit(0)"],
            }
        ),
        encoding="utf-8",
    )
    return load_runtime(), manifest, mission


def write_legacy_terminal_receipt(module, tmp_path: Path, mission: Path) -> Path:
    output = tmp_path / "implementation-output.md"
    output.write_text("legacy local gate passed", encoding="utf-8")
    receipt = tmp_path / "stage-result.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": module.RECEIPT_SCHEMA,
                "workflow_id": "a" * 32,
                "stage": "implementation",
                "attempt_id": "b" * 32,
                "input_mission_sha256": module.sha(mission),
                "status": "completed",
                "output_path": str(output),
                "output_sha256": module.sha(output),
                "next_stage": "complete",
                "next_mission_path": str(tmp_path / "terminal-mission.md"),
                "next_mission_sha256": "c" * 64,
                "ready_for_next": False,
                "blocker": "",
            }
        ),
        encoding="utf-8",
    )
    return receipt


def write_attention_implementation_state(
    module,
    config,
    mission: Path,
    receipt: Path,
    tmp_path: Path,
) -> Path:
    run_dir = tmp_path / "oracle-run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps({"schema": module.RUNNER.STATE.STATE_SCHEMA, "run_id": "b" * 32}),
        encoding="utf-8",
    )
    module._write(
        module._state_path(config, config["workflow_id"]),
        {
            "schema": module.STATE_SCHEMA,
            "status": "attention_required",
            "workflow_id": config["workflow_id"],
            "manifest_sha256": config["manifest_sha256"],
            "current_stage": "implementation",
            "current_attempt_id": "b" * 32,
            "current_input_sha256": module.sha(mission),
            "current_mission_path": str(mission),
            "receipt_path": str(receipt),
            "oracle_run_id": "b" * 32,
            "oracle_run_dir": str(run_dir),
            "next_index": 4,
            "records": [],
        },
    )
    return run_dir


def test_legacy_terminal_implementation_receipt_completes_without_oracle_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an exact legacy workflow waiting on its already-created terminal receipt.
    module, manifest, mission = prepare_workflow(tmp_path, monkeypatch)
    config = module.load_manifest(manifest)
    receipt = write_legacy_terminal_receipt(module, tmp_path, mission)
    module._write(
        module._state_path(config, config["workflow_id"]),
        {
            "schema": module.STATE_SCHEMA,
            "status": "awaiting_receipt",
            "workflow_id": config["workflow_id"],
            "manifest_sha256": config["manifest_sha256"],
            "current_stage": "implementation",
            "current_attempt_id": "b" * 32,
            "current_input_sha256": module.sha(mission),
            "current_mission_path": str(mission),
            "receipt_path": str(receipt),
            "next_index": 4,
            "records": [],
        },
    )
    gate_calls: list[list[str]] = []

    def reject_oracle_replay(*_args, **_kwargs):
        raise AssertionError("terminal receipt consumption must not replay Oracle")

    def pass_local_gate(command, **_kwargs):
        gate_calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "gate passed", "")

    # When: the original manifest resumes through the installed workflow entry point.
    result = module.run_workflow(
        manifest,
        oracle_execute=reject_oracle_replay,
        local_gate_runner=pass_local_gate,
    )

    # Then: the receipt is consumed once, the local gate runs, and the workflow completes.
    assert result["ok"] is True
    assert result["status"] == "complete"
    assert gate_calls == [["python", "-c", "raise SystemExit(0)"]]


def test_legacy_terminal_compatibility_does_not_accept_a_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the legacy terminal shape carries a blocker instead of a clean result.
    module, manifest, mission = prepare_workflow(tmp_path, monkeypatch)
    config = module.load_manifest(manifest)
    receipt = write_legacy_terminal_receipt(module, tmp_path, mission)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["blocker"] = "gate evidence is incomplete"
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    # When/Then: validation remains fail-closed outside the exact clean legacy shape.
    with pytest.raises(module.WorkflowError, match="stage receipt did not pass"):
        module._validate_receipt(
            config,
            receipt,
            "a" * 32,
            "implementation",
            "b" * 32,
            module.sha(mission),
        )


def test_terminal_blocked_implementation_consumes_exact_local_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a terminal blocked Oracle transport and its exact local implementation receipt.
    module, manifest, mission = prepare_workflow(tmp_path, monkeypatch)
    config = module.load_manifest(manifest)
    receipt = write_legacy_terminal_receipt(module, tmp_path, mission)
    run_dir = write_attention_implementation_state(
        module, config, mission, receipt, tmp_path
    )
    recoveries: list[Path] = []

    def terminal_blocked_recovery(
        exact_run_dir: Path,
        *,
        action: str,
        dry_run: bool,
    ):
        recoveries.append(exact_run_dir)
        assert (action, dry_run) == ("live", False)
        return {
            "ok": False,
            "status": "attention_required",
            "result": {
                "session_authority": "terminal",
                "terminal_harvested": True,
                "transport_status": "complete",
                "task_outcome": "blocked",
                "browser_observer": {"status": "process-exited"},
            },
        }

    def reject_oracle_replay(*_args, **_kwargs):
        raise AssertionError("terminal implementation settlement must not replay Oracle")

    def pass_local_gate(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, "gate passed", "")

    # When: the original workflow resumes without a replacement submission.
    result = module.run_workflow(
        manifest,
        oracle_execute=reject_oracle_replay,
        oracle_recover=terminal_blocked_recovery,
        local_gate_runner=pass_local_gate,
    )

    # Then: terminal ownership permits the bound receipt and local gate to complete.
    assert (result["ok"], result["status"]) == (True, "complete")
    assert recoveries == [run_dir]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_authority", "live"), ("terminal_harvested", False),
        ("task_outcome", "success"), ("browser_observer", None),
    ],
)
def test_unsettled_implementation_does_not_consume_local_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str | bool | None,
) -> None:
    # Given: one required terminal settlement signal is absent.
    module, manifest, mission = prepare_workflow(tmp_path, monkeypatch)
    config = module.load_manifest(manifest)
    receipt = write_legacy_terminal_receipt(module, tmp_path, mission)
    write_attention_implementation_state(module, config, mission, receipt, tmp_path)
    terminal_result = {
        "session_authority": "terminal",
        "terminal_harvested": True,
        "transport_status": "complete",
        "task_outcome": "blocked",
        "browser_observer": {"status": "process-exited"},
    }
    terminal_result[field] = value

    def unsettled_recovery(*_args, **_kwargs):
        return {"ok": False, "status": "attention_required", "result": terminal_result}

    def reject_side_effect(*_args, **_kwargs):
        raise AssertionError("unsettled evidence must not advance the workflow")

    # When: the workflow attempts exact recovery.
    result = module.run_workflow(
        manifest,
        oracle_execute=reject_side_effect,
        oracle_recover=unsettled_recovery,
        local_gate_runner=reject_side_effect,
    )

    # Then: the existing attention owner remains bound and the receipt is untouched.
    assert (result["ok"], result["status"]) == (False, "attention_required")
