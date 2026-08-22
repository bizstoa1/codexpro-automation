from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import TypeAlias


PROFILE_SCHEMA = "codex.chatgpt.project-capability-profile/v1"
PROFILE_RELATIVE_PATH = Path(".codex/project-capabilities.json")
MANDATORY_WRITE_DENY_PATHS = {".git", ".codex", ".ai-bridge", "AGENTS.md"}

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class CapabilityError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: JsonObject | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


def object_value(value: JsonValue, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", f"{label} must be an object")
    return value


def read_object(path: Path, *, missing_code: str, label: str) -> JsonObject:
    if path.is_symlink() or not path.is_file():
        raise CapabilityError(missing_code, f"{label} is required", {"path": str(path)})
    try:
        value: JsonValue = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", f"{label} is invalid") from exc
    return object_value(value, label)


def require_exact_keys(value: JsonObject, required: set[str], label: str) -> None:
    if set(value) != required:
        evidence: JsonObject = {
            "missing": [item for item in sorted(required - set(value))],
            "unknown": [item for item in sorted(set(value) - required)],
        }
        raise CapabilityError(
            "CAPABILITY_SCHEMA_INVALID",
            f"{label} fields are invalid",
            evidence,
        )


def string_list(value: JsonValue, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", f"{label} must be a string array")
    return [item for item in value if isinstance(item, str)]


def unique_string_list(value: JsonValue, label: str, *, required: bool = False) -> list[str]:
    items = string_list(value, label)
    if (required and not items) or len(set(items)) != len(items):
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", f"{label} must contain unique values")
    return items


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside_project(root: Path, raw: str, *, code: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() or "\0" in raw:
        raise CapabilityError(code, "capability paths must be relative", {"path": raw})
    target = (root / candidate).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise CapabilityError(code, "capability path escapes the project", {"path": raw}) from exc
    return target


def git_output(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CapabilityError(
            "CAPABILITY_GIT_REQUIRED",
            "capability project must be a Git repository",
            {"stderr": completed.stderr.strip()[-1200:]},
        )
    return completed.stdout.strip()


def load_profile(root: Path) -> tuple[JsonObject, Path]:
    path = root / PROFILE_RELATIVE_PATH
    value = read_object(path, missing_code="CAPABILITY_PROFILE_REQUIRED", label="profile")
    require_exact_keys(
        value,
        {"schema", "pro", "web_multi", "protected_branches", "write_deny_paths", "external_actions"},
        "profile",
    )
    if value.get("schema") != PROFILE_SCHEMA or value.get("external_actions") != "deny":
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", "profile safety constants are invalid")
    return value, path


def profile_sections(value: JsonObject) -> tuple[JsonObject, JsonObject, list[str], list[str]]:
    pro = object_value(value.get("pro"), "profile.pro")
    multi = object_value(value.get("web_multi"), "profile.web_multi")
    require_exact_keys(
        pro,
        {"enabled", "write_root_ceiling", "commands", "require_clean_git", "require_nonprotected_branch"},
        "profile.pro",
    )
    require_exact_keys(
        multi,
        {"enabled", "access", "min_lanes", "max_lanes", "max_concurrency", "all_lanes_required", "merger_policy", "nesting"},
        "profile.web_multi",
    )
    ceiling = unique_string_list(pro.get("write_root_ceiling"), "write_root_ceiling")
    protected = unique_string_list(value.get("protected_branches"), "protected_branches", required=True)
    denied = unique_string_list(value.get("write_deny_paths"), "write_deny_paths", required=True)
    lane_min, lane_max, concurrency_max = multi.get("min_lanes"), multi.get("max_lanes"), multi.get("max_concurrency")
    if multi.get("access") != "read-only":
        raise CapabilityError("WEB_MULTI_WRITE_FORBIDDEN", "Web Multi must be read-only")
    if (
        not isinstance(pro.get("enabled"), bool)
        or pro.get("commands") != "none"
        or pro.get("require_clean_git") is not True
        or pro.get("require_nonprotected_branch") is not True
        or not isinstance(multi.get("enabled"), bool)
        or not isinstance(lane_min, int)
        or isinstance(lane_min, bool)
        or not isinstance(lane_max, int)
        or isinstance(lane_max, bool)
        or not isinstance(concurrency_max, int)
        or isinstance(concurrency_max, bool)
        or not 2 <= lane_min <= lane_max <= 25
        or not 1 <= concurrency_max <= 5
        or multi.get("all_lanes_required") is not True
        or multi.get("merger_policy") != "exactly-one"
        or multi.get("nesting") != "forbidden"
        or "main" not in protected
        or not MANDATORY_WRITE_DENY_PATHS.issubset(set(denied))
    ):
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", "profile safety constants are invalid")
    for item in [*ceiling, *denied]:
        candidate = Path(item)
        if candidate == Path(".") or candidate.is_absolute() or "\0" in item:
            raise CapabilityError("CAPABILITY_SCHEMA_INVALID", "project root cannot be a capability path")
    return pro, multi, protected, denied


def required_reads(root: Path, mission: Path, write_roots: list[Path]) -> tuple[list[JsonValue], list[Path]]:
    files: list[Path] = [mission]
    for write_root in write_roots:
        directories = [root]
        current = root
        for part in write_root.relative_to(root).parts:
            candidate = current / part
            if candidate.exists() and not candidate.is_dir():
                break
            current = candidate
            directories.append(current)
        for directory in directories:
            agents = directory / "AGENTS.md"
            if agents.is_symlink():
                raise CapabilityError("CAPABILITY_INSTRUCTION_UNSAFE", "applicable AGENTS.md must not be a symlink")
            if agents.exists() and not agents.is_file():
                raise CapabilityError("CAPABILITY_INSTRUCTION_UNSAFE", "applicable AGENTS.md must be a regular file")
            if agents.is_file():
                files.append(agents.resolve(strict=True))
    unique = list(dict.fromkeys(files))
    rows: list[JsonValue] = [
        {"path": str(path), "sha256": sha256_file(path)}
        for path in unique
    ]
    return rows, unique[1:]


def require_clean_status(root: Path, ignored: tuple[Path, ...]) -> None:
    arguments = ["status", "--porcelain=v1", "--untracked-files=all", "--", "."]
    for path in ignored:
        try:
            relative = path.resolve(strict=False).relative_to(root).as_posix()
        except ValueError:
            continue
        arguments.append(f":(exclude,literal){relative}")
    if git_output(root, *arguments):
        raise CapabilityError("CAPABILITY_GIT_BASELINE_DIRTY", "bounded Pro requires a clean Git baseline")
