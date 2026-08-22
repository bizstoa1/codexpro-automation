from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TypeAlias


SCHEMA = "codex.chatgpt.capability-host-policy/v1"
PROFILE_PATH = Path(".codex/project-capabilities.json")
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class CapabilityPolicyError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: JsonObject | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


@dataclass(frozen=True, slots=True)
class ProjectAdmission:
    project_root: Path
    pro: bool
    oracle_read: bool
    web_multi: bool


def _object(value: JsonValue, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise CapabilityPolicyError("CAPABILITY_HOST_POLICY_INVALID", f"{label} must be an object")
    return value


def _exact(value: JsonObject, keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise CapabilityPolicyError(
            "CAPABILITY_HOST_POLICY_INVALID",
            f"{label} fields are invalid",
            {
                "missing": [item for item in sorted(keys - set(value))],
                "unknown": [item for item in sorted(set(value) - keys)],
            },
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def policy_path(state_root: Path) -> Path:
    return state_root.expanduser().resolve() / "host-policy.json"


def _write(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def compile_host_policy(
    state_root: Path,
    admissions: list[ProjectAdmission],
    *,
    max_web_multi_concurrency: int,
) -> JsonObject:
    if not 1 <= max_web_multi_concurrency <= 5 or not admissions:
        raise CapabilityPolicyError("CAPABILITY_HOST_POLICY_INVALID", "host limits or projects are invalid")
    state = state_root.expanduser().resolve()
    rows: list[JsonValue] = []
    seen: set[Path] = set()
    for admission in admissions:
        root = admission.project_root.expanduser().resolve(strict=True)
        if root in seen:
            raise CapabilityPolicyError("CAPABILITY_HOST_POLICY_INVALID", "project roots must be unique")
        seen.add(root)
        if _within(root, state) or _within(state, root):
            raise CapabilityPolicyError(
                "CAPABILITY_HOST_STATE_OVERLAP",
                "capability state must be disjoint from every project",
            )
        profile = root / PROFILE_PATH
        if profile.is_symlink() or not profile.is_file():
            raise CapabilityPolicyError(
                "CAPABILITY_PROFILE_REQUIRED",
                "every admitted root requires an exact project profile",
                {"project_root": str(root)},
            )
        rows.append(
            {
                "project_root": str(root),
                "profile_path": str(profile),
                "profile_sha256": _sha256(profile),
                "pro": admission.pro,
                "oracle_read": admission.oracle_read,
                "web_multi": admission.web_multi,
            }
        )
    return {
        "schema": SCHEMA,
        "max_web_multi_concurrency": max_web_multi_concurrency,
        "external_actions": "deny",
        "projects": sorted(rows, key=lambda item: str(item["project_root"]) if isinstance(item, dict) else ""),
    }


def install_host_policy(
    state_root: Path,
    admissions: list[ProjectAdmission],
    *,
    max_web_multi_concurrency: int,
) -> Path:
    value = compile_host_policy(
        state_root,
        admissions,
        max_web_multi_concurrency=max_web_multi_concurrency,
    )
    state = state_root.expanduser().resolve()
    target = policy_path(state)
    _write(target, value)
    return target


def load_host_policy(state_root: Path) -> JsonObject:
    path = policy_path(state_root)
    if path.is_symlink() or not path.is_file():
        raise CapabilityPolicyError("CAPABILITY_HOST_POLICY_REQUIRED", "capability host policy is required")
    try:
        value: JsonValue = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityPolicyError("CAPABILITY_HOST_POLICY_INVALID", "capability host policy is unreadable") from exc
    policy = _object(value, "host policy")
    _exact(policy, {"schema", "max_web_multi_concurrency", "external_actions", "projects"}, "host policy")
    concurrency = policy.get("max_web_multi_concurrency")
    projects = policy.get("projects")
    if (
        policy.get("schema") != SCHEMA
        or policy.get("external_actions") != "deny"
        or not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or not 1 <= concurrency <= 5
        or not isinstance(projects, list)
        or not projects
    ):
        raise CapabilityPolicyError("CAPABILITY_HOST_POLICY_INVALID", "host policy constants are invalid")
    return policy


def admit(
    contract: JsonObject,
    state_root: Path,
    *,
    requested_concurrency: int = 1,
) -> JsonObject:
    policy = load_host_policy(state_root)
    binding = _object(contract.get("binding"), "contract.binding")
    root_raw = binding.get("project_root")
    profile_raw = binding.get("profile_path")
    profile_hash = binding.get("profile_sha256")
    actor = contract.get("actor")
    if not all(isinstance(item, str) for item in (root_raw, profile_raw, profile_hash, actor)):
        raise CapabilityPolicyError("CAPABILITY_SCHEMA_INVALID", "capability binding is invalid")
    root = Path(str(root_raw)).expanduser().resolve(strict=True)
    profile = Path(str(profile_raw)).expanduser().resolve(strict=True)
    rows = policy.get("projects")
    maximum = policy.get("max_web_multi_concurrency")
    if not isinstance(rows, list) or not isinstance(maximum, int) or isinstance(maximum, bool):
        raise CapabilityPolicyError("CAPABILITY_HOST_POLICY_INVALID", "host policy rows are invalid")
    matches = [item for item in rows if isinstance(item, dict) and item.get("project_root") == str(root)]
    if len(matches) != 1:
        raise CapabilityPolicyError("CAPABILITY_ROOT_NOT_QUALIFIED", "exact project root is not admitted")
    row = matches[0]
    if (
        profile != root / PROFILE_PATH
        or row.get("profile_path") != str(profile)
        or row.get("profile_sha256") != profile_hash
        or _sha256(profile) != profile_hash
    ):
        raise CapabilityPolicyError("CAPABILITY_PROFILE_NOT_QUALIFIED", "project profile hash is not admitted")
    mode = {"pro": "pro", "oracle": "oracle_read", "web-multi": "web_multi"}.get(str(actor))
    if mode is None or row.get(mode) is not True:
        code = "CAPABILITY_PRO_DISABLED" if actor == "pro" else "CAPABILITY_MODE_DISABLED"
        raise CapabilityPolicyError(code, "capability mode is disabled by host policy")
    if actor == "web-multi" and (
        not isinstance(requested_concurrency, int)
        or isinstance(requested_concurrency, bool)
        or not 1 <= requested_concurrency <= maximum
    ):
        raise CapabilityPolicyError(
            "CAPABILITY_HOST_CONCURRENCY_MISMATCH",
            "Web Multi concurrency exceeds host policy",
        )
    return {**row, "max_web_multi_concurrency": maximum}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure exact-root ChatGPT capability admission.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure = subparsers.add_parser("configure")
    configure.add_argument("--state-root", type=Path, required=True)
    configure.add_argument("--project-root", type=Path, action="append", required=True)
    configure.add_argument("--enable-pro-root", type=Path, action="append", default=[])
    configure.add_argument("--max-web-multi-concurrency", type=int, default=5)
    configure.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    projects = [path.expanduser().resolve(strict=True) for path in args.project_root]
    try:
        pro_roots = {path.expanduser().resolve(strict=True) for path in args.enable_pro_root}
    except OSError as exc:
        raise CapabilityPolicyError(
            "CAPABILITY_HOST_POLICY_INVALID",
            "every Pro root must be an existing admitted project",
        ) from exc
    if not pro_roots.issubset(set(projects)):
        raise CapabilityPolicyError(
            "CAPABILITY_HOST_POLICY_INVALID",
            "Pro roots must be an exact subset of project roots",
        )
    admissions = [
        ProjectAdmission(project, project in pro_roots, True, True)
        for project in projects
    ]
    policy = compile_host_policy(
        args.state_root,
        admissions,
        max_web_multi_concurrency=args.max_web_multi_concurrency,
    )
    target = policy_path(args.state_root)
    if not args.dry_run:
        _write(target, policy)
    print(json.dumps({
        "ok": True,
        "dry_run": bool(args.dry_run),
        "policy_path": str(target),
        "policy": policy,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
