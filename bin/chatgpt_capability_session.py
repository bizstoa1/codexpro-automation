from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class LeaseProtocol(Protocol):
    lease_id: str
    path: Path
    capability_path: Path
    tokens: Mapping[str, str]


class BaselineProtocol(Protocol):
    project_root: Path
    access: str
    head_oid: str
    branch: str
    index_oid: str
    protected_refs: tuple[tuple[str, str | None], ...]
    status_sha256: str
    ignored_paths: tuple[Path, ...]


class CapabilityRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: JsonObject | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


@dataclass(frozen=True, slots=True)
class CapabilitySession:
    contract_json: str
    project_root: Path
    subject_id: str
    token: str | None = field(repr=False)
    lease_path: Path
    lease_id: str | None
    state_root: Path
    lease: LeaseProtocol | None = field(repr=False)
    baseline: BaselineProtocol = field(repr=False)

    def contract(self) -> JsonObject:
        value: JsonValue = json.loads(self.contract_json)
        if not isinstance(value, dict):
            raise CapabilityRuntimeError("CAPABILITY_SCHEMA_INVALID", "capability contract is invalid")
        return value


def capability_state_root() -> Path:
    override = str(os.environ.get("CODEX_CAPABILITY_STATE_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".codex" / "state" / "chatgpt-capabilities").resolve()


def bind_prompt(
    base_prompt: str,
    project_root: Path,
    token: str,
    *,
    mission_path: Path | None = None,
) -> str:
    normalized = token.strip()
    if (
        not normalized
        or len(normalized) > 4096
        or normalized != token
        or any(character.isspace() for character in normalized)
    ):
        raise CapabilityRuntimeError("CAPABILITY_TOKEN_INVALID", "capability token is invalid")
    app, separator, instruction = base_prompt.partition(" ")
    if not separator or not app.startswith("@") or not instruction:
        raise CapabilityRuntimeError("CAPABILITY_PROMPT_INVALID", "DevSpace prompt has no app prefix")
    root = project_root.expanduser().resolve(strict=True)
    read_instruction = "call read_file on the exact mission"
    if mission_path is not None:
        mission = mission_path.expanduser().resolve(strict=True)
        try:
            relative = mission.relative_to(root).as_posix()
        except ValueError as exc:
            raise CapabilityRuntimeError("CAPABILITY_ROOT_MISMATCH", "mission path is outside project") from exc
        read_instruction = f"call read_file(path={json.dumps(relative, ensure_ascii=False)})"
    return (
        f'{app} First call open_workspace(path="{root}", mode="checkout", '
        f'capabilityToken="{normalized}") and reuse its workspaceId. Before any write, '
        f"{read_instruction} and every applicable AGENTS.md file; the capability gate "
        f"will reject missing or changed reads. {instruction}"
    )


def baseline_payload(baseline: BaselineProtocol) -> JsonObject:
    return {
        "schema": "codex.chatgpt.capability-git-baseline/v1",
        "project_root": str(baseline.project_root),
        "access": baseline.access,
        "head_oid": baseline.head_oid,
        "branch": baseline.branch,
        "index_oid": baseline.index_oid,
        "protected_refs": [
            {"ref": ref, "oid": oid}
            for ref, oid in baseline.protected_refs
        ],
        "status_sha256": baseline.status_sha256,
        "ignored_paths": [str(path) for path in baseline.ignored_paths],
    }
