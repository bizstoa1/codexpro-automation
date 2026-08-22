from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class CapabilityGitError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: JsonObject | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


@dataclass(frozen=True, slots=True)
class GitBaseline:
    project_root: Path
    access: str
    head_oid: str
    branch: str
    index_oid: str
    protected_refs: tuple[tuple[str, str | None], ...]
    status_sha256: str
    ignored_paths: tuple[Path, ...]


def _object(value: JsonValue, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise CapabilityGitError("CAPABILITY_SCHEMA_INVALID", f"{label} must be an object")
    return value


def _strings(value: JsonValue, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise CapabilityGitError("CAPABILITY_SCHEMA_INVALID", f"{label} must be a string array")
    return [item for item in value if isinstance(item, str)]


def _run(root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise CapabilityGitError(
            "CAPABILITY_GIT_REQUIRED",
            "capability Git inspection failed",
            {"stderr": completed.stderr.decode("utf-8", errors="replace")[-1200:]},
        )
    return completed


def _text(root: Path, *args: str) -> str:
    return _run(root, list(args)).stdout.decode("utf-8", errors="strict").strip()


def _ref_oid(root: Path, ref: str) -> str | None:
    completed = _run(root, ["rev-parse", "--verify", "--quiet", ref], check=False)
    return completed.stdout.decode("ascii", errors="strict").strip() or None


def _contract_parts(contract: JsonObject) -> tuple[Path, str, JsonObject, JsonObject, JsonObject]:
    binding = _object(contract.get("binding"), "contract.binding")
    paths = _object(contract.get("paths"), "contract.paths")
    git = _object(contract.get("git"), "contract.git")
    root_raw, access = binding.get("project_root"), contract.get("access")
    if not isinstance(root_raw, str) or access not in {"read-only", "control-write", "bounded-write"}:
        raise CapabilityGitError("CAPABILITY_SCHEMA_INVALID", "capability binding is invalid")
    root = Path(root_raw).expanduser().resolve(strict=True)
    return root, str(access), binding, paths, git


def _ignored(root: Path, binding: JsonObject) -> tuple[Path, ...]:
    result: list[Path] = []
    for key in ("mission_path", "profile_path", "authority_path"):
        raw = binding.get(key)
        if not isinstance(raw, str):
            continue
        path = Path(raw).expanduser().resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError:
            continue
        result.append(path)
    for raw in _strings(binding.get("host_control_paths", []), "host_control_paths"):
        path = Path(raw).expanduser().resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CapabilityGitError(
                "CAPABILITY_SCHEMA_INVALID",
                "host control path is outside project",
            ) from exc
        result.append(path)
    return tuple(result)


def _status(root: Path, ignored: tuple[Path, ...]) -> tuple[bytes, list[str]]:
    args = ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."]
    for path in ignored:
        args.append(f":(exclude,literal){path.relative_to(root).as_posix()}")
    raw = _run(root, args).stdout
    records = raw.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise CapabilityGitError("CAPABILITY_GIT_STATUS_INVALID", "Git status record is malformed")
        status = record[:2]
        paths.append(os.fsdecode(record[3:]))
        if b"R" in status or b"C" in status:
            if index >= len(records) or not records[index]:
                raise CapabilityGitError("CAPABILITY_GIT_STATUS_INVALID", "Git rename status is incomplete")
            paths.append(os.fsdecode(records[index]))
            index += 1
    return raw, sorted(set(paths))


def capture_baseline(contract: JsonObject) -> GitBaseline:
    root, access, binding, _paths, git = _contract_parts(contract)
    if git.get("head_policy") != "unchanged" or git.get("index_policy") != "unchanged" or git.get("push_policy") != "forbidden":
        raise CapabilityGitError("CAPABILITY_SCHEMA_INVALID", "unsafe Git capability policy")
    ignored = _ignored(root, binding)
    status, _changed = _status(root, ignored)
    refs = tuple((ref, _ref_oid(root, ref)) for ref in _strings(git.get("protected_refs"), "protected_refs"))
    return GitBaseline(
        root,
        access,
        _text(root, "rev-parse", "HEAD"),
        _text(root, "symbolic-ref", "--short", "HEAD"),
        _text(root, "write-tree"),
        refs,
        hashlib.sha256(status).hexdigest(),
        ignored,
    )


def _within(target: Path, roots: list[Path]) -> bool:
    return any(target == root or root in target.parents for root in roots)


def _assert_no_symlink(root: Path, relative: str) -> None:
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise CapabilityGitError(
                "CAPABILITY_SYMLINK_FORBIDDEN",
                "capability changes must not contain symlinks",
                {"path": relative},
            )


def verify_postflight(contract: JsonObject, baseline: GitBaseline) -> JsonObject:
    root, access, _binding, paths, git = _contract_parts(contract)
    if root != baseline.project_root or access != baseline.access:
        raise CapabilityGitError("CAPABILITY_GIT_BASELINE_DRIFT", "capability baseline identity mismatch")
    if _text(root, "rev-parse", "HEAD") != baseline.head_oid or _text(root, "symbolic-ref", "--short", "HEAD") != baseline.branch:
        raise CapabilityGitError("CAPABILITY_GIT_BASELINE_DRIFT", "HEAD or branch changed during capability execution")
    if _text(root, "write-tree") != baseline.index_oid:
        raise CapabilityGitError("CAPABILITY_GIT_INDEX_CHANGED", "Git index changed during capability execution")
    expected_refs = dict(baseline.protected_refs)
    current_refs = {ref: _ref_oid(root, ref) for ref in _strings(git.get("protected_refs"), "protected_refs")}
    if current_refs != expected_refs:
        raise CapabilityGitError("CAPABILITY_PROTECTED_REF_CHANGED", "a protected Git ref changed")
    status, changed = _status(root, baseline.ignored_paths)
    if access in {"read-only", "control-write"} and hashlib.sha256(status).hexdigest() != baseline.status_sha256:
        drift_evidence: JsonObject = {"changed_paths": [item for item in changed]}
        raise CapabilityGitError("CAPABILITY_READ_ONLY_DRIFT", "read-only capability changed project Git state", drift_evidence)
    write_roots = [Path(item).resolve(strict=False) for item in _strings(paths.get("write_roots"), "write_roots")]
    deny_roots = [Path(item).resolve(strict=False) for item in _strings(paths.get("write_deny_roots"), "write_deny_roots")]
    if access == "bounded-write":
        for relative in changed:
            _assert_no_symlink(root, relative)
            target = (root / relative).resolve(strict=False)
            if _within(target, deny_roots) or not _within(target, write_roots):
                raise CapabilityGitError("CAPABILITY_DIFF_OUT_OF_SCOPE", "changed path exceeds capability scope", {"path": relative})
    evidence: JsonObject = {
        "schema": "codex.chatgpt.capability-git-postflight/v1",
        "status": "passed",
        "changed_paths": [item for item in changed],
        "head_unchanged": True,
        "index_unchanged": True,
        "protected_refs_unchanged": True,
        "status_sha256": hashlib.sha256(status).hexdigest(),
    }
    return evidence
