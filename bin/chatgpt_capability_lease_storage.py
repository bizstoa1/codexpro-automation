from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import time
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class CapabilityLeaseError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: JsonObject | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


class TransactionLock:
    def __init__(self, path: Path, timeout_seconds: float = 30) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.handle: BinaryIO | None = None

    def __enter__(self) -> TransactionLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    if self.path.stat().st_size == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.handle = handle
                return self
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise CapabilityLeaseError(
                        "CAPABILITY_LEASE_LOCK_TIMEOUT",
                        "capability transaction lock timed out",
                    ) from exc
                time.sleep(0.05)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self.handle
        if handle is None:
            return
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self.handle = None


def object_value(value: JsonValue, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise CapabilityLeaseError("CAPABILITY_SCHEMA_INVALID", f"{label} must be an object")
    return value


def canonical_bytes(value: JsonObject) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_json(path: Path) -> JsonObject:
    if path.is_symlink() or not path.is_file():
        raise CapabilityLeaseError("CAPABILITY_LEASE_UNRESOLVED", "capability state is missing or unsafe")
    try:
        value: JsonValue = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityLeaseError("CAPABILITY_LEASE_UNRESOLVED", "capability state is unreadable") from exc
    return object_value(value, "capability state")


def write_json(path: Path, value: JsonObject) -> None:
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
        temporary.unlink(missing_ok=True)


def secret(state_root: Path) -> bytes:
    path = state_root / "capability-secret.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        descriptor: int | None
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(secrets.token_bytes(32))
                handle.flush()
                os.fsync(handle.fileno())
    if path.is_symlink():
        raise CapabilityLeaseError("CAPABILITY_SECRET_INVALID", "capability secret must not be a symlink")
    identity = path.stat()
    if not stat.S_ISREG(identity.st_mode) or (os.name != "nt" and stat.S_IMODE(identity.st_mode) & 0o077):
        raise CapabilityLeaseError(
            "CAPABILITY_SECRET_INVALID",
            "capability secret must be a private regular file",
        )
    value = path.read_bytes()
    if len(value) != 32:
        raise CapabilityLeaseError("CAPABILITY_SECRET_INVALID", "capability secret has an invalid length")
    return value


def lease_paths(state_root: Path, project_root: Path) -> tuple[Path, Path]:
    key = hashlib.sha256(str(project_root).lower().encode("utf-8")).hexdigest()[:24]
    directory = state_root / "projects" / key / "capabilities"
    return directory, directory / "active-lease.json"
