from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path


BIN = Path(__file__).resolve().parent


class CapabilityModuleLoadError(RuntimeError):
    def __init__(self, path: Path):
        super().__init__(f"capability module unavailable: {path}")
        self.path = path


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CapabilityModuleLoadError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SESSION = _load("chatgpt_capability_session", BIN / "chatgpt_capability_session.py")
PROJECT = _load("chatgpt_project_capability", BIN / "chatgpt_project_capability.py")
LEASE = _load("chatgpt_capability_lease", BIN / "chatgpt_capability_lease.py")
GIT = _load("chatgpt_capability_git", BIN / "chatgpt_capability_git.py")
POLICY = _load("chatgpt_capability_policy", BIN / "chatgpt_capability_policy.py")
RECOVERY = _load("chatgpt_capability_recovery", BIN / "chatgpt_capability_recovery.py")
CapabilityRuntimeError = SESSION.CapabilityRuntimeError
CapabilitySession = SESSION.CapabilitySession
LeaseProtocol = SESSION.LeaseProtocol
BaselineProtocol = SESSION.BaselineProtocol
capability_state_root = SESSION.capability_state_root
bind_prompt = SESSION.bind_prompt
_baseline_payload = SESSION.baseline_payload


def _open(
    contract_value,
    subject_id: str,
    state_root: Path,
    dry_run: bool,
    *,
    requested_concurrency: int = 1,
) -> CapabilitySession:
    contract = contract_value.as_dict()
    state = state_root.expanduser().resolve()
    POLICY.admit(contract, state, requested_concurrency=requested_concurrency)
    baseline = GIT.capture_baseline(contract)
    if dry_run:
        return CapabilitySession(
            contract_value.canonical_json,
            baseline.project_root,
            subject_id,
            None,
            state / "dry-run-no-lease",
            None,
            state,
            None,
            baseline,
        )
    lease = LEASE.acquire_lease(
        contract,
        state,
        [subject_id],
        git_baseline=_baseline_payload(baseline),
    )
    return CapabilitySession(
        contract_value.canonical_json,
        baseline.project_root,
        subject_id,
        lease.tokens[subject_id],
        lease.path,
        lease.lease_id,
        state,
        lease,
        baseline,
    )


def open_pro(
    project_root: Path,
    mission_path: Path,
    authority_path: Path,
    *,
    state_root: Path | None = None,
    dry_run: bool = False,
    subject_id: str = "pro",
) -> CapabilitySession:
    contract = PROJECT.compile_pro_contract(project_root, mission_path, authority_path)
    return _open(contract, subject_id, state_root or capability_state_root(), dry_run)


def open_read_only(
    project_root: Path,
    mission_path: Path,
    *,
    control_write_root: Path | None = None,
    state_root: Path | None = None,
    dry_run: bool = False,
    subject_id: str = "oracle",
) -> CapabilitySession:
    contract = PROJECT.compile_read_only_contract(
        project_root,
        mission_path,
        control_write_root=control_write_root,
    )
    return _open(contract, subject_id, state_root or capability_state_root(), dry_run)


def open_web_multi(
    project_root: Path,
    missions: list[tuple[str, Path]],
    merger_path: Path,
    *,
    max_concurrency: int,
    subjects: list[str],
    control_root: Path | None = None,
    state_root: Path | None = None,
    dry_run: bool = False,
) -> tuple[CapabilitySession, Mapping[str, str]]:
    contract_value = PROJECT.compile_web_multi_contract(
        project_root,
        missions,
        merger_path,
        max_concurrency=max_concurrency,
        control_root=control_root,
    )
    contract = contract_value.as_dict()
    state = (state_root or capability_state_root()).expanduser().resolve()
    POLICY.admit(contract, state, requested_concurrency=max_concurrency)
    baseline = GIT.capture_baseline(contract)
    if dry_run:
        session = CapabilitySession(
            contract_value.canonical_json,
            baseline.project_root,
            "web-multi-parent",
            None,
            state / "dry-run-no-lease",
            None,
            state,
            None,
            baseline,
        )
        return session, {}
    lease = LEASE.acquire_lease(
        contract,
        state,
        subjects,
        git_baseline=_baseline_payload(baseline),
    )
    session = CapabilitySession(
        contract_value.canonical_json,
        baseline.project_root,
        "web-multi-parent",
        None,
        lease.path,
        lease.lease_id,
        state,
        lease,
        baseline,
    )
    return session, lease.tokens


resume_single = RECOVERY.resume_single
resume_web_multi = RECOVERY.resume_web_multi
finish = RECOVERY.finish
