from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias


AUTHORITY_SCHEMA = "codex.chatgpt.pro-mission-authority/v1"
CONTRACT_SCHEMA = "codex.chatgpt.project-capability/v1"
LANE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
BIN = Path(__file__).resolve().parent

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


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


PROFILE = _load("chatgpt_capability_profile", BIN / "chatgpt_capability_profile.py")
WEB_MULTI = _load("chatgpt_web_multi_capability", BIN / "chatgpt_web_multi_capability.py")
CapabilityError = PROFILE.CapabilityError
PROFILE_SCHEMA = PROFILE.PROFILE_SCHEMA
PROFILE_RELATIVE_PATH = PROFILE.PROFILE_RELATIVE_PATH
MANDATORY_WRITE_DENY_PATHS = PROFILE.MANDATORY_WRITE_DENY_PATHS
_object = PROFILE.object_value
_read_object = PROFILE.read_object
_exact_keys = PROFILE.require_exact_keys
_strings = PROFILE.string_list
_unique_strings = PROFILE.unique_string_list
_sha256 = PROFILE.sha256_file
_inside = PROFILE.inside_project
_git = PROFILE.git_output
_profile = PROFILE.load_profile
_profile_sections = PROFILE.profile_sections
_required_reads = PROFILE.required_reads
_clean_status = PROFILE.require_clean_status


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    canonical_json: str

    def as_dict(self) -> JsonObject:
        value: JsonValue = json.loads(self.canonical_json)
        return _object(value, "contract")


def _canonical(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compile_pro_contract(project_root: Path, mission_path: Path, authority_path: Path) -> CapabilityContract:
    root = project_root.expanduser().resolve(strict=True)
    mission = mission_path.expanduser().resolve(strict=True)
    if root not in mission.parents:
        raise CapabilityError("CAPABILITY_ROOT_MISMATCH", "mission path is outside project")
    profile, profile_path = _profile(root)
    pro, _multi, protected, denied = _profile_sections(profile)
    if pro.get("enabled") is not True:
        raise CapabilityError("CAPABILITY_PRO_DISABLED", "Pro bounded write is disabled")
    if pro.get("commands") != "none" or pro.get("require_clean_git") is not True:
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", "unsafe Pro profile")
    authority = _read_object(authority_path, missing_code="CAPABILITY_AUTHORITY_REQUIRED", label="authority")
    authority_path = authority_path.expanduser().resolve(strict=True)
    _exact_keys(
        authority,
        {"schema", "project_root", "mission_path", "mission_sha256", "expected_head", "allowed_write_paths", "allowed_command_ids", "external_actions"},
        "authority",
    )
    if authority.get("schema") != AUTHORITY_SCHEMA or authority.get("external_actions") != "deny":
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", "authority safety constants are invalid")
    if authority.get("project_root") != str(root) or authority.get("mission_path") != str(mission):
        raise CapabilityError("CAPABILITY_ROOT_MISMATCH", "authority root or mission path mismatch")
    if authority.get("mission_sha256") != _sha256(mission):
        raise CapabilityError("CAPABILITY_MISSION_CHANGED", "mission bytes differ from authority")
    head = _git(root, "rev-parse", "HEAD")
    if authority.get("expected_head") != head:
        raise CapabilityError("CAPABILITY_GIT_BASELINE_DRIFT", "authority HEAD differs from repository")
    branch = _git(root, "symbolic-ref", "--short", "HEAD")
    if pro.get("require_nonprotected_branch") is True and branch in protected:
        raise CapabilityError("CAPABILITY_PROTECTED_BRANCH", "bounded Pro cannot run on a protected branch")
    requested = _unique_strings(
        authority.get("allowed_write_paths"),
        "allowed_write_paths",
        required=True,
    )
    if _strings(authority.get("allowed_command_ids"), "allowed_command_ids"):
        raise CapabilityError("CAPABILITY_COMMAND_FORBIDDEN", "capability v1 commands are disabled")
    ceiling = [_inside(root, item, code="CAPABILITY_WRITE_OUT_OF_SCOPE") for item in _strings(pro.get("write_root_ceiling"), "write_root_ceiling")]
    denied_roots = [_inside(root, item, code="CAPABILITY_PATH_FORBIDDEN") for item in denied]
    write_roots: list[Path] = []
    for item in requested:
        target = _inside(root, item, code="CAPABILITY_WRITE_OUT_OF_SCOPE")
        if target == root:
            raise CapabilityError("CAPABILITY_WRITE_OUT_OF_SCOPE", "project-root write authority is forbidden")
        if any(target == deny or deny in target.parents or target in deny.parents for deny in denied_roots):
            raise CapabilityError("CAPABILITY_PATH_FORBIDDEN", "requested path overlaps a denied path", {"path": item})
        if not any(target == allowed or allowed in target.parents for allowed in ceiling):
            raise CapabilityError("CAPABILITY_WRITE_OUT_OF_SCOPE", "requested path exceeds profile ceiling", {"path": item})
        write_roots.append(target)
    required_reads, instruction_files = _required_reads(root, mission, write_roots)
    denied_roots = list(dict.fromkeys([*denied_roots, mission, authority_path, *instruction_files]))
    read_denied = [
        (root / ".git").resolve(strict=False),
        (root / ".codex").resolve(strict=False),
    ]
    _clean_status(root, (profile_path, mission, authority_path))
    value: JsonObject = {
        "schema": CONTRACT_SCHEMA,
        "actor": "pro",
        "access": "bounded-write",
        "binding": {"project_root": str(root), "mission_path": str(mission), "mission_sha256": _sha256(mission), "profile_path": str(profile_path), "profile_sha256": _sha256(profile_path), "authority_path": str(authority_path), "authority_sha256": _sha256(authority_path), "head_oid": head, "required_reads": required_reads},
        "paths": {"read_roots": [str(root)], "read_deny_roots": [str(path) for path in read_denied], "write_roots": [str(path) for path in write_roots], "write_deny_roots": [str(path) for path in denied_roots]},
        "commands": {"mode": "none", "rules": []},
        "git": {"head_policy": "unchanged", "index_policy": "unchanged", "protected_refs": [f"refs/heads/{name}" for name in protected], "push_policy": "forbidden"},
        "topology": {"kind": "single", "nesting": "forbidden"},
        "external_actions": [],
    }
    return CapabilityContract(_canonical(value))


def compile_read_only_contract(
    project_root: Path,
    mission_path: Path,
    *,
    control_write_root: Path | None = None,
) -> CapabilityContract:
    root = project_root.expanduser().resolve(strict=True)
    mission = mission_path.expanduser().resolve(strict=True)
    if root not in mission.parents:
        raise CapabilityError("CAPABILITY_ROOT_MISMATCH", "mission path is outside project")
    profile, profile_path = _profile(root)
    _pro, multi, protected, denied = _profile_sections(profile)
    if multi.get("enabled") is not True:
        raise CapabilityError("CAPABILITY_READ_ONLY_DISABLED", "read-only Oracle is disabled")
    if (
        multi.get("access") != "read-only"
        or multi.get("all_lanes_required") is not True
        or multi.get("nesting") != "forbidden"
    ):
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", "unsafe read-only profile")
    control: Path | None = None
    if control_write_root is not None:
        control = control_write_root.expanduser().resolve(strict=True)
        if root not in control.parents or control not in mission.parents:
            raise CapabilityError(
                "CAPABILITY_ROOT_MISMATCH",
                "host control root must contain the exact mission inside the project",
            )
    head = _git(root, "rev-parse", "HEAD")
    denied_roots = [
        _inside(root, item, code="CAPABILITY_PATH_FORBIDDEN")
        for item in denied
    ]
    if control is not None:
        denied_roots = [path for path in denied_roots if path not in control.parents and path != control]
        denied_roots.extend((profile_path.resolve(), mission, (root / "AGENTS.md").resolve(strict=False)))
    required_reads: list[JsonValue] = []
    if control is not None:
        required_reads, instruction_files = _required_reads(root, mission, [control])
        denied_roots.extend(instruction_files)
    unique_denied = list(dict.fromkeys(denied_roots))
    value: JsonObject = {
        "schema": CONTRACT_SCHEMA,
        "actor": "oracle",
        "access": "control-write" if control is not None else "read-only",
        "binding": {
            "project_root": str(root),
            "mission_path": str(mission),
            "mission_sha256": _sha256(mission),
            "profile_path": str(profile_path),
            "profile_sha256": _sha256(profile_path),
            "head_oid": head,
            "host_control_paths": [str(control)] if control is not None else [],
            "required_reads": required_reads,
        },
        "paths": {
            "read_roots": [str(root)],
            "read_deny_roots": [
                str((root / ".git").resolve(strict=False)),
                str((root / ".codex").resolve(strict=False)),
            ],
            "write_roots": [str(control)] if control is not None else [],
            "write_deny_roots": [str(path) for path in unique_denied],
        },
        "commands": {"mode": "none", "rules": []},
        "git": {
            "head_policy": "unchanged",
            "index_policy": "unchanged",
            "protected_refs": [f"refs/heads/{name}" for name in protected],
            "push_policy": "forbidden",
        },
        "topology": {"kind": "single", "nesting": "forbidden"},
        "external_actions": [],
    }
    return CapabilityContract(_canonical(value))


def compile_web_multi_contract(
    project_root: Path,
    missions: list[tuple[str, Path]],
    merger_path: Path,
    *,
    max_concurrency: int,
    parent_capability_id: str | None = None,
    control_root: Path | None = None,
) -> CapabilityContract:
    request = WEB_MULTI.WebMultiRequest(
        project_root,
        missions,
        merger_path,
        max_concurrency,
        parent_capability_id,
        control_root,
    )
    return CapabilityContract(WEB_MULTI.compile_web_multi_json(request))
