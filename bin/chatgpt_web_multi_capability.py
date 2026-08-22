from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias


CONTRACT_SCHEMA = "codex.chatgpt.project-capability/v1"
LANE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PROFILE = importlib.import_module("chatgpt_capability_profile")
CapabilityError = PROFILE.CapabilityError

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class WebMultiRequest:
    project_root: Path
    missions: list[tuple[str, Path]]
    merger_path: Path
    max_concurrency: int
    parent_capability_id: str | None
    control_root: Path | None


def compile_web_multi_json(request: WebMultiRequest) -> str:
    root = request.project_root.expanduser().resolve(strict=True)
    profile, profile_path = PROFILE.load_profile(root)
    _pro, multi, protected, denied = PROFILE.profile_sections(profile)
    if multi.get("enabled") is not True:
        raise CapabilityError("CAPABILITY_WEB_MULTI_DISABLED", "Web Multi is disabled")
    if multi.get("access") != "read-only":
        raise CapabilityError("WEB_MULTI_WRITE_FORBIDDEN", "Web Multi must be read-only")
    if request.parent_capability_id:
        raise CapabilityError("WEB_MULTI_NESTING_FORBIDDEN", "nested Web Multi is forbidden")
    lane_min_raw = multi.get("min_lanes")
    lane_max_raw = multi.get("max_lanes")
    concurrency_max_raw = multi.get("max_concurrency")
    if (
        not isinstance(lane_min_raw, int)
        or isinstance(lane_min_raw, bool)
        or not isinstance(lane_max_raw, int)
        or isinstance(lane_max_raw, bool)
        or not isinstance(concurrency_max_raw, int)
        or isinstance(concurrency_max_raw, bool)
    ):
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", "Web Multi numeric limits are invalid")
    if (
        not lane_min_raw <= len(request.missions) <= lane_max_raw
        or not 1 <= request.max_concurrency <= concurrency_max_raw
    ):
        raise CapabilityError("WEB_MULTI_TOPOLOGY_INVALID", "Web Multi topology exceeds profile")
    if (
        multi.get("all_lanes_required") is not True
        or multi.get("merger_policy") != "exactly-one"
        or multi.get("nesting") != "forbidden"
    ):
        raise CapabilityError("CAPABILITY_SCHEMA_INVALID", "unsafe Web Multi profile")
    seen: set[str] = set()
    lanes: list[JsonValue] = []
    for lane, raw_path in request.missions:
        path = raw_path.expanduser().resolve(strict=True)
        if LANE_RE.fullmatch(lane) is None or lane in seen or root not in path.parents:
            raise CapabilityError("WEB_MULTI_TOPOLOGY_INVALID", "lane identity or path is invalid")
        seen.add(lane)
        lanes.append({"id": lane, "mission_path": str(path), "mission_sha256": PROFILE.sha256_file(path)})
    merger = request.merger_path.expanduser().resolve(strict=True)
    if root not in merger.parents:
        raise CapabilityError("WEB_MULTI_TOPOLOGY_INVALID", "merger path is outside project")
    controls: list[Path] = []
    if request.control_root is not None:
        control = request.control_root.expanduser().resolve(strict=False)
        if root not in control.parents:
            raise CapabilityError("WEB_MULTI_TOPOLOGY_INVALID", "control root is outside project")
        mission_files = [Path(str(item["mission_path"])) for item in lanes if isinstance(item, dict)]
        if any(control == path or control in path.parents for path in [*mission_files, merger]):
            raise CapabilityError("WEB_MULTI_TOPOLOGY_INVALID", "input missions overlap the control root")
        controls.append(control)
    read_denied = [
        (root / ".git").resolve(strict=False),
        (root / ".codex").resolve(strict=False),
    ]
    value: JsonObject = {
        "schema": CONTRACT_SCHEMA,
        "actor": "web-multi",
        "access": "read-only",
        "binding": {"project_root": str(root), "profile_path": str(profile_path), "profile_sha256": PROFILE.sha256_file(profile_path), "head_oid": PROFILE.git_output(root, "rev-parse", "HEAD"), "host_control_paths": [str(path) for path in controls], "required_reads": []},
        "paths": {"read_roots": [str(root)], "read_deny_roots": [str(path) for path in read_denied], "write_roots": [], "write_deny_roots": [str(PROFILE.inside_project(root, item, code="CAPABILITY_PATH_FORBIDDEN")) for item in denied]},
        "commands": {"mode": "none", "rules": []},
        "git": {"head_policy": "unchanged", "index_policy": "unchanged", "protected_refs": [f"refs/heads/{name}" for name in protected], "push_policy": "forbidden"},
        "topology": {"kind": "web-multi", "lanes": lanes, "merger": {"mission_path": str(merger), "mission_sha256": PROFILE.sha256_file(merger)}, "max_provider_concurrency": request.max_concurrency, "completion_policy": "all-lanes", "merger_policy": "exactly-one", "nesting": "forbidden"},
        "subjects": {
            "lanes": [
                {"id": str(item["id"]), "read_deny_roots": [str(path) for path in controls]}
                for item in lanes
                if isinstance(item, dict)
            ],
            "merger": {"id": "merger", "read_deny_roots": []},
        },
        "external_actions": [],
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
