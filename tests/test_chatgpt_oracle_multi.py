from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_multi.py"
RESULT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "oracle-multi-result-v2.schema.json"


def assert_result_schema(value: dict[str, object]) -> None:
    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)


def load():
    spec = importlib.util.spec_from_file_location("oracle_multi_test", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_manifest(tmp_path: Path, count: int = 7) -> Path:
    state_root = (tmp_path.parent / f"{tmp_path.name}-host-state").resolve()
    profile_seed = (tmp_path.parent / f"{tmp_path.name}-oracle-profile-seed").resolve()
    profile_seed.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    policy_path = state_root / "host-policy.json"
    policy_path.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-host-policy/v1",
        "profile_seed": str(profile_seed),
        "profile_mode": "copy-per-run",
        "max_total_concurrency": 5,
    }), encoding="utf-8")
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str(state_root)
    os.environ["CODEX_ORACLE_HOST_POLICY"] = str(policy_path)
    os.environ["CODEX_CAPABILITY_STATE_ROOT"] = str(state_root / "capabilities")
    subprocess.run(["git", "init", "-q", "-b", "codex/web-multi-test", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Web Multi Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "multi@example.test"], check=True)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "baseline"], check=True)
    profile = tmp_path / ".codex/project-capabilities.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps({
        "schema": "codex.chatgpt.project-capability-profile/v1",
        "pro": {
            "enabled": False,
            "write_root_ceiling": [],
            "commands": "none",
            "require_clean_git": True,
            "require_nonprotected_branch": True,
        },
        "web_multi": {
            "enabled": True,
            "access": "read-only",
            "min_lanes": 2,
            "max_lanes": 25,
            "max_concurrency": 5,
            "all_lanes_required": True,
            "merger_policy": "exactly-one",
            "nesting": "forbidden",
        },
        "protected_branches": ["main", "master"],
        "write_deny_paths": [".git", ".codex", ".ai-bridge", "AGENTS.md"],
        "external_actions": "deny",
    }), encoding="utf-8")
    capability_state = Path(os.environ["CODEX_CAPABILITY_STATE_ROOT"])
    capability_state.mkdir(parents=True)
    (capability_state / "host-policy.json").write_text(json.dumps({
        "schema": "codex.chatgpt.capability-host-policy/v1",
        "max_web_multi_concurrency": 5,
        "external_actions": "deny",
        "projects": [{
            "project_root": str(tmp_path.resolve()),
            "profile_path": str(profile.resolve()),
            "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
            "pro": False,
            "oracle_read": True,
            "web_multi": True,
        }],
    }), encoding="utf-8")
    missions = []
    for index in range(count):
        path = tmp_path / f"solver-{index}.md"
        path.write_text(f"solve {index}", encoding="utf-8")
        missions.append({"id": f"s{index}", "mission_path": str(path.resolve()), "access": "read-only"})
    merger = tmp_path / "merge.md"
    merger.write_text("Merge every listed handoff.", encoding="utf-8")
    manifest = tmp_path / "multi.json"
    manifest.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-multi/v2",
        "project_root": str(tmp_path.resolve()),
        "output_dir": str((tmp_path / "out").resolve()),
        "app_name": "DevSpace",
        "model": "gpt-5.6",
        "max_concurrency": 5,
        "completion_policy": "all-lanes",
        "merger_policy": "exactly-one",
        "nesting": "forbidden",
        "solvers": missions,
        "merger_mission_path": str(merger.resolve()),
    }), encoding="utf-8")
    return manifest


def test_manifest_accepts_configured_workspace_app_name(tmp_path: Path) -> None:
    module = load()
    path = make_manifest(tmp_path, 2)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["app_name"] = "OtherWorkspace"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert module.load_manifest(path)["app_name"] == "OtherWorkspace"


@pytest.mark.parametrize("relative", [".git/refs/heads/web-multi", ".codex/web-multi"])
def test_manifest_rejects_output_below_protected_control_tree(
    tmp_path: Path,
    relative: str,
) -> None:
    module = load()
    path = make_manifest(tmp_path, 2)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["output_dir"] = str((tmp_path / relative).resolve())
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.MultiError, match="non-control project subtree"):
        module.load_manifest(path)


def test_web_multi_dry_run_is_compile_only_and_writes_nothing(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 2)
    output = tmp_path / "out"
    capability_state = Path(os.environ["CODEX_CAPABILITY_STATE_ROOT"])
    before = {
        path.relative_to(capability_state).as_posix(): path.read_bytes()
        for path in capability_state.rglob("*")
        if path.is_file()
    }

    result = module.run_multi(
        manifest,
        dry_run=True,
        execute=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    assert result["status"] == "dry-run"
    assert result["writes_performed"] is False
    assert result["merger_count"] == 1
    assert_result_schema(result)
    assert not output.exists()
    after = {
        path.relative_to(capability_state).as_posix(): path.read_bytes()
        for path in capability_state.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_multi_uses_unique_child_manifests_waves_and_merger(tmp_path: Path) -> None:
    module = load()
    calls = []

    def fake_execute(path: Path, *, dry_run: bool, capability_token: str):
        assert capability_token
        value = json.loads(path.read_text(encoding="utf-8"))
        calls.append(value)
        run_dir = path.parent / "fake-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.md").write_text(f"answer {path.parent.name}", encoding="utf-8")
        return {"ok": True, "run_dir": str(run_dir), "result": {"terminal_harvested": True}}

    result = module.run_multi(make_manifest(tmp_path), execute=fake_execute)
    assert result["ok"] is True
    assert result["status"] == "complete"
    assert_result_schema(result)
    assert len(result["lanes"]) == 7
    assert len(calls) == 8
    assert len({item["parallel_parent_id"] for item in calls}) == 1
    assert all(item["app_name"] == "DevSpace" for item in calls)
    assert all(item["model"] == "gpt-5.6" for item in calls)
    assert all(item["model_strategy"] == "select" for item in calls)
    assert all(item["thinking_time"] == "extra-high" for item in calls)
    assert all(item["capability_required"] is True for item in calls)
    assert all("capability_token" not in item for item in calls)
    host_policy = json.loads(
        Path(os.environ["CODEX_ORACLE_HOST_POLICY"]).read_text(encoding="utf-8")
    )
    assert all(item["copy_profile"] == host_policy["profile_seed"] for item in calls)
    merger_text = Path(calls[-1]["mission_path"]).read_text(encoding="utf-8")
    assert merger_text.count(".md") == 7


def test_read_only_merger_output_is_locally_materialized_as_bound_receipt(
    tmp_path: Path,
) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 2)
    receipt = tmp_path / "out" / "merger" / "stage-result.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["next_stage_result_path"] = str(receipt.resolve())
    payload["next_stage_binding"] = {"workflow_id": "a" * 32, "stage": "web-multi"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()

    def fake_execute(path: Path, *, dry_run: bool, capability_token: str):
        assert not dry_run
        assert capability_token
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

    result = module.run_multi(manifest, execute=fake_execute)

    assert result["status"] == "complete"
    assert result["next_stage_result_path"] == str(receipt.resolve())
    value = json.loads(receipt.read_text(encoding="utf-8"))
    assert value["schema"] == "codex.chatgpt.oracle-stage-result/v1"
    assert value["attempt_id"] == result["parent_id"]
    assert value["input_mission_sha256"] == manifest_sha
    assert Path(value["output_path"]).read_text(encoding="utf-8") == "Merged advisory\n"
    assert Path(value["next_mission_path"]).read_text(encoding="utf-8") == "Review the merged advisory.\n"
    assert_result_schema(result)


def test_invalid_bound_merger_envelope_fails_without_receipt(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 2)
    receipt = tmp_path / "out" / "merger" / "stage-result.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["next_stage_result_path"] = str(receipt.resolve())
    payload["next_stage_binding"] = {"workflow_id": "a" * 32, "stage": "web-multi"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    def fake_execute(path: Path, *, dry_run: bool, capability_token: str):
        child = json.loads(path.read_text(encoding="utf-8"))
        run_dir = (
            Path(os.environ["CODEX_ORACLE_STATE_ROOT"]) / "bad-merger-run"
            if child["capability_subject_id"] == "merger"
            else path.parent / "fake-run"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.md").write_text("not a bound JSON envelope", encoding="utf-8")
        return {"ok": True, "run_dir": str(run_dir), "result": {"terminal_harvested": True}}

    result = module.run_multi(manifest, execute=fake_execute)

    assert result["status"] == "failed"
    assert result["next_stage_result_path"] is None
    assert not receipt.exists()


def test_multi_requires_all_lanes_and_rejects_over_capacity(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 3)
    calls = []
    def fake_execute(path: Path, *, dry_run: bool, capability_token: str):
        calls.append(path)
        run_dir = path.parent / "fake-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        if path.parent.name == "s1":
            return {"ok": False, "run_dir": str(run_dir), "result": {"terminal_harvested": True}}
        (run_dir / "output.md").write_text("ok", encoding="utf-8")
        return {"ok": True, "run_dir": str(run_dir), "result": {"terminal_harvested": True}}

    result = module.run_multi(manifest, execute=fake_execute)
    assert result["status"] == "failed"
    assert len(calls) == 3
    assert result["merger_run_dir"] is None
    assert_result_schema(result)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["max_concurrency"] = 6
    manifest.write_text(json.dumps(value), encoding="utf-8")
    try:
        module.load_manifest(manifest)
    except module.MultiError:
        pass
    else:
        raise AssertionError("capacity > 5 must fail")


def test_multi_retains_lease_and_reports_attention_when_any_lane_is_not_terminal(
    tmp_path: Path,
) -> None:
    module = load()
    calls = []

    def fake_execute(path: Path, *, dry_run: bool, capability_token: str):
        calls.append(path)
        run_dir = path.parent / "fake-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        return {"ok": False, "run_dir": str(run_dir), "result": {"terminal_harvested": False}}

    result = module.run_multi(make_manifest(tmp_path, 2), execute=fake_execute)

    assert result["status"] == "attention_required"
    assert result["merger_count"] == 0
    assert result["capability"]["status"] == "retained"
    assert len(calls) == 2
    assert_result_schema(result)


def test_multi_rejects_lane_path_traversal(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 2)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["solvers"][0]["id"] = "../../outside"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    try:
        module.load_manifest(manifest)
    except module.MultiError:
        pass
    else:
        raise AssertionError("unsafe lane id must fail")


def test_reconcile_recovered_lanes_restores_stable_order_without_submission(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 3)
    config = module.load_manifest(manifest)
    state_root = Path(os.environ["CODEX_ORACLE_STATE_ROOT"])
    parent_id = "a" * 64
    recorded = []
    for lane in reversed(config["solvers"]):
        run_dir = state_root / lane["id"]
        run_dir.mkdir(parents=True)
        output = run_dir / "output.md"
        output.write_text(f"answer {lane['id']}", encoding="utf-8")
        artifact_sha = module.hashlib.sha256(output.read_bytes()).hexdigest()
        locator = f"oracle-{lane['id']}"
        (run_dir / "state.json").write_text(json.dumps({
            "project_root": str(tmp_path.resolve()),
            "parallel_parent_id": parent_id,
            "status": "complete",
            "terminal_harvested": True,
            "artifact_sha256": artifact_sha,
            "mission": {"sha256": module.hashlib.sha256(lane["mission_path"].read_bytes()).hexdigest()},
            "oracle": {"session_locator": locator},
        }), encoding="utf-8")
        recorded.append({"id": lane["id"], "ok": False, "run_dir": str(run_dir), "session_locator": locator})
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

    result = module.reconcile_recovered_lanes(manifest)

    assert result["status"] == "merger_ready"
    assert [lane["id"] for lane in result["lanes"]] == ["s0", "s1", "s2"]
    assert result["successful_lane_count"] == 3
    merger_text = Path(result["merger_mission_path"]).read_text(encoding="utf-8")
    positions = [
        merger_text.index(str(config["output_dir"] / "handoffs" / f"s{index}.md"))
        for index in range(3)
    ]
    assert positions == sorted(positions)
    assert result["merger_run_dir"] is None
    assert result["merger_submission_count"] == 0
    assert_result_schema(result)


def test_reconcile_recovered_lanes_rejects_parent_identity_mismatch(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 2)
    config = module.load_manifest(manifest)
    state_root = Path(os.environ["CODEX_ORACLE_STATE_ROOT"])
    module._write_json(config["output_dir"] / "result.json", {
        "schema": module.RESULT_SCHEMA,
        "status": "failed",
        "ok": False,
        "writes_performed": True,
        "parent_id": "a" * 64,
        "manifest_sha256": config["manifest_sha256"],
        "merger_count": 0,
        "merger_submission_count": 0,
        "lanes": [
            {"id": lane["id"], "run_dir": str(state_root / lane["id"]), "session_locator": f"oracle-{lane['id']}"}
            for lane in config["solvers"]
        ],
        "merger_run_dir": None,
        "capability": {"status": "retained"},
    })
    first = config["solvers"][0]
    run_dir = state_root / first["id"]
    run_dir.mkdir()
    output = run_dir / "output.md"
    output.write_text("answer", encoding="utf-8")
    (run_dir / "state.json").write_text(json.dumps({
        "project_root": str(tmp_path.resolve()),
        "parallel_parent_id": "b" * 64,
        "status": "complete",
        "terminal_harvested": True,
        "artifact_sha256": module.hashlib.sha256(output.read_bytes()).hexdigest(),
        "mission": {"sha256": module.hashlib.sha256(first["mission_path"].read_bytes()).hexdigest()},
        "oracle": {"session_locator": f"oracle-{first['id']}"},
    }), encoding="utf-8")

    with pytest.raises(module.MultiError, match="parent identity mismatch"):
        module.reconcile_recovered_lanes(manifest)


def test_resume_recovered_merger_submits_only_stable_order_merger(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 2)
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
    recorded = []
    for lane in config["solvers"]:
        run_dir = state_root / lane["id"]
        run_dir.mkdir(parents=True)
        output = run_dir / "output.md"
        output.write_text(f"answer {lane['id']}", encoding="utf-8")
        artifact_sha = module.hashlib.sha256(output.read_bytes()).hexdigest()
        locator = f"oracle-{lane['id']}"
        (run_dir / "state.json").write_text(json.dumps({
            "project_root": str(tmp_path.resolve()),
            "parallel_parent_id": parent_id,
            "status": "complete",
            "terminal_harvested": True,
            "artifact_sha256": artifact_sha,
            "mission": {"sha256": module.hashlib.sha256(lane["mission_path"].read_bytes()).hexdigest()},
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
    calls = []

    def fake_execute(path: Path, *, dry_run: bool, capability_token: str):
        assert capability_token
        calls.append(json.loads(path.read_text(encoding="utf-8")))
        return {
            "ok": True,
            "run_dir": str(tmp_path / "new-merger-run"),
            "result": {"terminal_harvested": True},
        }

    result = module.resume_recovered_merger(manifest, execute=fake_execute)

    assert result["status"] == "complete"
    assert len(calls) == 1
    assert calls[0]["parallel_parent_id"] == parent_id
    assert Path(calls[0]["mission_path"]).name == "mission.md"
    assert result["merger_run_dir"].endswith("new-merger-run")
    assert result["prior_merger_run_dirs"] == []
    assert_result_schema(result)
