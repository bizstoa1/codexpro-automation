from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "bin" / "chatgpt_capability_lease.py"
SCHEMA_PATH = ROOT / "contracts" / "project-capability-lease-v1.schema.json"


def assert_lease_schema(value: dict[str, object]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)


def load():
    spec = importlib.util.spec_from_file_location("capability_lease_test", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def contract(project: Path, *, actor: str = "web-multi"):
    access = "read-only" if actor == "web-multi" else "bounded-write"
    return {
        "schema": "codex.chatgpt.project-capability/v1",
        "actor": actor,
        "access": access,
        "binding": {
            "project_root": str(project.resolve()),
            "mission_path": str((project / "mission.md").resolve()),
            "mission_sha256": "a" * 64,
            "profile_sha256": "b" * 64,
            "head_oid": "c" * 40,
        },
        "paths": {
            "read_roots": [str(project.resolve())],
            "write_roots": [] if access == "read-only" else [str((project / "src").resolve())],
            "write_deny_roots": [str((project / ".git").resolve())],
        },
        "commands": {"mode": "none", "rules": []},
        "topology": {"kind": "web-multi" if actor == "web-multi" else "single", "nesting": "forbidden"},
        "external_actions": [],
    }


def test_acquire_creates_one_durable_owner_without_persisting_tokens(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()

    lease = module.acquire_lease(contract(project), state, ["solver-one", "solver-two", "merger"])
    payload = json.loads(lease.path.read_text(encoding="utf-8"))

    assert payload["schema"] == "codex.chatgpt.project-capability-lease/v1"
    assert payload["state"] == "active"
    assert payload["owner"] == {"actor": "web-multi", "access": "read-only"}
    assert [item["subject_id"] for item in payload["subjects"]] == ["solver-one", "solver-two", "merger"]
    assert all("token" not in item for item in payload["subjects"])
    assert set(lease.tokens) == {"solver-one", "solver-two", "merger"}
    assert all(token not in lease.path.read_text(encoding="utf-8") for token in lease.tokens.values())
    assert_lease_schema(payload)

    with pytest.raises(module.CapabilityLeaseError) as conflict:
        module.acquire_lease(contract(project), state, ["other"])
    assert conflict.value.code == "CAPABILITY_LEASE_CONFLICT"


def test_signed_subject_token_is_exact_root_lease_and_subject_bound(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    other = tmp_path / "other"
    state = tmp_path / "state"
    project.mkdir()
    other.mkdir()
    lease = module.acquire_lease(contract(project), state, ["solver"])
    token = lease.tokens["solver"]

    verified = module.verify_subject_token(token, state, project)
    assert verified["lease_id"] == lease.lease_id
    assert verified["subject_id"] == "solver"
    assert verified["project_root"] == str(project.resolve())

    with pytest.raises(module.CapabilityLeaseError) as root_mismatch:
        module.verify_subject_token(token, state, other)
    assert root_mismatch.value.code == "CAPABILITY_ROOT_MISMATCH"

    replacement = ("A" if token[0] != "A" else "B") + token[1:]
    with pytest.raises(module.CapabilityLeaseError) as tampered:
        module.verify_subject_token(replacement, state, project)
    assert tampered.value.code == "CAPABILITY_TOKEN_INVALID"


def test_terminal_and_postflight_are_both_required_before_release(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    lease = module.acquire_lease(contract(project, actor="pro"), state, ["pro"])

    with pytest.raises(module.CapabilityLeaseError) as terminal:
        module.release_lease(lease, terminal_harvested=False, postflight_ok=True)
    assert terminal.value.code == "CAPABILITY_TERMINAL_EVIDENCE_REQUIRED"
    assert json.loads(lease.path.read_text(encoding="utf-8"))["state"] == "active"

    with pytest.raises(module.CapabilityLeaseError) as postflight:
        module.release_lease(lease, terminal_harvested=True, postflight_ok=False)
    assert postflight.value.code == "CAPABILITY_POSTFLIGHT_FAILED"
    assert json.loads(lease.path.read_text(encoding="utf-8"))["state"] == "quarantined"
    assert_lease_schema(json.loads(lease.path.read_text(encoding="utf-8")))

    with pytest.raises(module.CapabilityLeaseError) as quarantined:
        module.acquire_lease(contract(project), state, ["new"])
    assert quarantined.value.code == "CAPABILITY_LEASE_CONFLICT"


def test_successful_release_archives_evidence_and_allows_new_owner(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    first = module.acquire_lease(contract(project), state, ["solver"])

    receipt = module.release_lease(first, terminal_harvested=True, postflight_ok=True)

    assert receipt["state"] == "released"
    assert not first.path.exists()
    archive = first.path.parent / "archive" / f"{first.lease_id}.json"
    assert json.loads(archive.read_text(encoding="utf-8"))["state"] == "released"
    assert_lease_schema(receipt)
    second = module.acquire_lease(contract(project), state, ["solver"])
    assert second.lease_id != first.lease_id


def test_proven_pre_submit_abort_archives_without_terminal_evidence(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    lease = module.acquire_lease(contract(project, actor="pro"), state, ["pro"])

    receipt = module.abort_pre_submit_lease(lease, reason="oracle-version-rejected")

    assert receipt["state"] == "aborted_pre_submit"
    assert receipt["terminal_harvested"] is False
    assert not lease.path.exists()
    archived = lease.path.parent / "archive" / f"{lease.lease_id}.json"
    assert json.loads(archived.read_text(encoding="utf-8"))["abort_reason"] == "oracle-version-rejected"
    assert_lease_schema(receipt)


def test_active_lease_recovery_recreates_exact_tokens_without_persisting_them(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    baseline = {"schema": "codex.chatgpt.capability-git-baseline/v1", "head_oid": "a" * 40}
    original = module.acquire_lease(
        contract(project),
        state,
        ["lane-one", "merger"],
        git_baseline=baseline,
    )

    recovered = module.recover_active_lease(project, state)
    payload = json.loads(recovered.path.read_text(encoding="utf-8"))

    assert recovered.lease_id == original.lease_id
    assert dict(recovered.tokens) == dict(original.tokens)
    assert payload["git_baseline"] == baseline
    assert all(token not in recovered.path.read_text(encoding="utf-8") for token in recovered.tokens.values())


def test_secret_must_be_a_private_regular_file_and_root_key_matches_js_lowercase(
    tmp_path: Path,
) -> None:
    module = load()
    project = tmp_path / "StraßeProject"
    state = tmp_path / "state"
    project.mkdir()
    state.mkdir()
    secret = state / "capability-secret.key"
    secret.write_bytes(b"x" * 32)
    if os.name != "nt":
        secret.chmod(0o644)
        with pytest.raises(module.CapabilityLeaseError) as public_secret:
            module.acquire_lease(contract(project), state, ["solver"])
        assert public_secret.value.code == "CAPABILITY_SECRET_INVALID"
        secret.chmod(0o600)

    lease = module.acquire_lease(contract(project), state, ["solver"])
    expected_key = hashlib.sha256(str(project.resolve()).lower().encode("utf-8")).hexdigest()[:24]
    assert lease.path.parents[1].name == expected_key


def test_secret_symlink_is_rejected(tmp_path: Path) -> None:
    module = load()
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    state.mkdir()
    outside = tmp_path / "outside-secret"
    outside.write_bytes(b"y" * 32)
    (state / "capability-secret.key").symlink_to(outside)

    with pytest.raises(module.CapabilityLeaseError) as unsafe:
        module.acquire_lease(contract(project), state, ["solver"])

    assert unsafe.value.code == "CAPABILITY_SECRET_INVALID"
