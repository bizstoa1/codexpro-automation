from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast, final


HOST_POLICY_SCHEMA: Final = "codex.chatgpt.oracle-host-policy/v1"
COPY_PER_RUN_MODE: Final = "copy-per-run"
MAX_HOST_CONCURRENCY: Final = 5


@final
class OracleHostPolicyError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        evidence: dict[str, str | int] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


@dataclass(frozen=True, slots=True)
class HostPolicy:
    path: Path
    profile_seed: Path
    profile_mode: str
    max_total_concurrency: int
    sha256: str


class HostRuntime(Protocol):
    def host_maintenance_lease(
        self,
        *,
        state_root: Path,
        max_total_concurrency: int,
        timeout_seconds: float,
        platform_name: str | None = None,
    ) -> AbstractContextManager[None]: ...


def host_state_root() -> Path:
    override = str(os.environ.get("CODEX_ORACLE_STATE_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".codex" / "state" / "chatgpt-oracle").resolve()


def host_policy_path(state_root: Path | None = None) -> Path:
    override = str(os.environ.get("CODEX_ORACLE_HOST_POLICY") or "").strip()
    if override:
        return Path(override).expanduser()
    root = host_state_root() if state_root is None else state_root.expanduser().resolve()
    return root / "host-policy.json"


def load_host_policy(state_root: Path | None = None) -> HostPolicy:
    return load_host_policy_from_path(host_policy_path(state_root))


def load_host_policy_from_path(path: Path) -> HostPolicy:
    path = path.expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise OracleHostPolicyError(
            "HOST_POLICY_REQUIRED",
            "Oracle host policy must be a regular non-symlink file",
            {"path": str(path)},
        )
    try:
        raw = path.read_bytes()
        value = cast(object, json.loads(raw.decode("utf-8", errors="strict")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OracleHostPolicyError(
            "HOST_POLICY_INVALID",
            "Oracle host policy must be readable UTF-8 JSON",
            {"path": str(path)},
        ) from exc
    if not isinstance(value, dict):
        raise OracleHostPolicyError(
            "HOST_POLICY_INVALID", "Oracle host policy must be a JSON object"
        )
    payload = cast(dict[str, object], value)
    allowed = {"schema", "profile_seed", "profile_mode", "max_total_concurrency"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise OracleHostPolicyError(
            "HOST_POLICY_INVALID",
            "Oracle host policy contains unknown fields",
            {"fields": ",".join(unknown)},
        )
    if payload.get("schema") != HOST_POLICY_SCHEMA:
        raise OracleHostPolicyError(
            "HOST_POLICY_INVALID",
            f"Oracle host policy schema must be {HOST_POLICY_SCHEMA}",
        )
    profile_seed_raw = Path(str(payload.get("profile_seed") or "")).expanduser()
    if not profile_seed_raw.is_absolute() or profile_seed_raw.is_symlink():
        raise OracleHostPolicyError(
            "HOST_PROFILE_SEED_INVALID",
            "profile_seed must be an absolute non-symlink directory",
            {"profile_seed": str(profile_seed_raw)},
        )
    try:
        profile_seed = profile_seed_raw.resolve(strict=True)
    except OSError as exc:
        raise OracleHostPolicyError(
            "HOST_PROFILE_SEED_INVALID",
            "profile_seed does not exist",
            {"profile_seed": str(profile_seed_raw)},
        ) from exc
    if not profile_seed.is_dir():
        raise OracleHostPolicyError(
            "HOST_PROFILE_SEED_INVALID",
            "profile_seed must identify a directory",
            {"profile_seed": str(profile_seed)},
        )
    if payload.get("profile_mode") != COPY_PER_RUN_MODE:
        raise OracleHostPolicyError(
            "HOST_PROFILE_MODE_INVALID",
            f"profile_mode must be {COPY_PER_RUN_MODE}",
        )
    concurrency = payload.get("max_total_concurrency")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int):
        raise OracleHostPolicyError(
            "HOST_CONCURRENCY_INVALID", "max_total_concurrency must be an integer"
        )
    if not 1 <= concurrency <= MAX_HOST_CONCURRENCY:
        raise OracleHostPolicyError(
            "HOST_CONCURRENCY_INVALID",
            f"max_total_concurrency must be within 1..{MAX_HOST_CONCURRENCY}",
        )
    return HostPolicy(
        path=path,
        profile_seed=profile_seed,
        profile_mode=COPY_PER_RUN_MODE,
        max_total_concurrency=concurrency,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _load_host_runtime() -> HostRuntime:
    path = Path(__file__).resolve().with_name("chatgpt_oracle_host.py")
    spec = importlib.util.spec_from_file_location("chatgpt_oracle_policy_host_runtime", path)
    if spec is None or spec.loader is None:
        raise OracleHostPolicyError(
            "HOST_RUNTIME_UNAVAILABLE", "Oracle host lease runtime is unavailable"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(HostRuntime, cast(object, module))


def configure_host_policy(
    profile_seed: Path,
    *,
    max_total_concurrency: int,
    state_root: Path | None = None,
    timeout_seconds: float = 300,
) -> HostPolicy:
    root = host_state_root() if state_root is None else state_root.expanduser().resolve()
    target = host_policy_path(root) if state_root is None else root / "host-policy.json"
    seed_raw = profile_seed.expanduser()
    if not target.is_absolute() or target.is_symlink():
        raise OracleHostPolicyError(
            "HOST_POLICY_INVALID", "host policy path must be absolute and non-symlink"
        )
    if not seed_raw.is_absolute() or seed_raw.is_symlink():
        raise OracleHostPolicyError(
            "HOST_PROFILE_SEED_INVALID",
            "profile_seed must be an absolute non-symlink directory",
        )
    seed = seed_raw.resolve(strict=True)
    if not seed.is_dir():
        raise OracleHostPolicyError(
            "HOST_PROFILE_SEED_INVALID", "profile_seed must identify a directory"
        )
    if not 1 <= max_total_concurrency <= MAX_HOST_CONCURRENCY:
        raise OracleHostPolicyError(
            "HOST_CONCURRENCY_INVALID",
            f"max_total_concurrency must be within 1..{MAX_HOST_CONCURRENCY}",
        )
    raw = (
        json.dumps(
            {
                "schema": HOST_POLICY_SCHEMA,
                "profile_seed": str(seed),
                "profile_mode": COPY_PER_RUN_MODE,
                "max_total_concurrency": max_total_concurrency,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )
    runtime = _load_host_runtime()
    try:
        with runtime.host_maintenance_lease(
            state_root=root,
            max_total_concurrency=MAX_HOST_CONCURRENCY,
            timeout_seconds=timeout_seconds,
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise OracleHostPolicyError(
                    "HOST_POLICY_INVALID", "host policy path must not be a symlink"
                )
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as stream:
                    _ = stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    except RuntimeError as exc:
        if isinstance(exc, OracleHostPolicyError):
            raise
        raise OracleHostPolicyError(
            str(getattr(exc, "code", "HOST_MAINTENANCE_FAILED")),
            str(exc),
            getattr(exc, "evidence", {}),
        ) from exc
    return load_host_policy_from_path(target)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Configure the host-only Oracle profile policy")
    _ = parser.add_argument("configure", choices=("configure",))
    _ = parser.add_argument("--profile-seed", type=Path, required=True)
    _ = parser.add_argument("--max-total-concurrency", type=int, default=5)
    _ = parser.add_argument("--state-root", type=Path)
    values = cast(dict[str, object], vars(parser.parse_args()))
    profile_seed = values["profile_seed"]
    max_total_concurrency = values["max_total_concurrency"]
    state_root = values["state_root"]
    if not isinstance(profile_seed, Path) or not isinstance(max_total_concurrency, int):
        raise RuntimeError("argparse returned invalid Oracle host policy values")
    if state_root is not None and not isinstance(state_root, Path):
        raise RuntimeError("argparse returned an invalid Oracle host state root")
    try:
        policy = configure_host_policy(
            profile_seed,
            max_total_concurrency=max_total_concurrency,
            state_root=state_root,
        )
        result = {
            "ok": True,
            "path": str(policy.path),
            "profile_seed": str(policy.profile_seed),
            "profile_mode": policy.profile_mode,
            "max_total_concurrency": policy.max_total_concurrency,
            "sha256": policy.sha256,
        }
    except (OSError, OracleHostPolicyError) as exc:
        result = {
            "ok": False,
            "error": {
                "code": str(getattr(exc, "code", "HOST_POLICY_WRITE_FAILED")),
                "message": str(exc),
            },
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
