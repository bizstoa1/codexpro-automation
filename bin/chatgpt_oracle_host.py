from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, final


_HELD_PATHS: set[str] = set()
_HELD_PATHS_GUARD = threading.Lock()


@final
class OracleHostLeaseError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        evidence: dict[str, str | int] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


def host_state_root() -> Path:
    override = str(os.environ.get("CODEX_ORACLE_STATE_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".codex" / "state" / "chatgpt-oracle").resolve()




@final
class ExclusiveFileLease:
    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float,
        error_code: str,
        platform_name: str | None = None,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self.error_code = error_code
        self.platform_name = os.name
        self.handle: BinaryIO | None = None
        self.token = str(self.path).casefold()

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        with _HELD_PATHS_GUARD:
            if self.token in _HELD_PATHS:
                handle.close()
                return False
            _HELD_PATHS.add(self.token)
        try:
            if self.platform_name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    _ = handle.write(b"\0")
                    handle.flush()
                _ = handle.seek(0)
                _ = msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            with _HELD_PATHS_GUARD:
                _HELD_PATHS.discard(self.token)
            handle.close()
            return False
        self.handle = handle
        return True

    def __enter__(self) -> "ExclusiveFileLease":
        deadline = time.monotonic() + self.timeout_seconds
        while not self.try_acquire():
            if time.monotonic() >= deadline:
                raise OracleHostLeaseError(
                    self.error_code,
                    "Oracle host lease could not be acquired",
                    {"path": str(self.path)},
                )
            time.sleep(0.05)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self.handle
        if handle is None:
            return None
        if self.platform_name == "nt":
            import msvcrt

            _ = handle.seek(0)
            _ = msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        with _HELD_PATHS_GUARD:
            _HELD_PATHS.discard(self.token)
        self.handle = None
        return None


@final
class HostRunLease:
    def __init__(self, root: Path, count: int, timeout_seconds: float, platform_name: str | None) -> None:
        self.root = root.expanduser().resolve()
        self.count = count
        self.timeout_seconds = timeout_seconds
        self.platform_name = platform_name
        self.lease: ExclusiveFileLease | None = None

    def __enter__(self) -> "HostRunLease":
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            with ExclusiveFileLease(
                self.root / "locks" / "maintenance.lock",
                timeout_seconds=remaining,
                error_code="HOST_CAPACITY_TIMEOUT",
                platform_name=self.platform_name,
            ):
                for index in range(self.count):
                    lease = ExclusiveFileLease(
                        self.root / "locks" / "capacity" / f"slot-{index}.lock",
                        timeout_seconds=0,
                        error_code="HOST_CAPACITY_TIMEOUT",
                        platform_name=self.platform_name,
                    )
                    if lease.try_acquire():
                        self.lease = lease
                        return self
            if time.monotonic() >= deadline:
                raise OracleHostLeaseError(
                    "HOST_CAPACITY_TIMEOUT",
                    "all Oracle host capacity slots are occupied",
                    {"max_total_concurrency": self.count},
                )
            time.sleep(0.05)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.lease is not None:
            self.lease.__exit__(exc_type, exc, traceback)
        self.lease = None
        return None


def host_run_lease(
    *,
    state_root: Path,
    max_total_concurrency: int,
    timeout_seconds: float,
    platform_name: str | None = None,
) -> HostRunLease:
    return HostRunLease(state_root, max_total_concurrency, timeout_seconds, platform_name)


@contextmanager
def host_maintenance_lease(
    *, state_root: Path, max_total_concurrency: int, timeout_seconds: float,
    platform_name: str | None = None,
) -> Generator[None, None, None]:
    with ExitStack() as stack:
        _ = stack.enter_context(ExclusiveFileLease(
            state_root / "locks" / "maintenance.lock", timeout_seconds=timeout_seconds,
            error_code="HOST_MAINTENANCE_TIMEOUT", platform_name=platform_name,
        ))
        for index in range(max_total_concurrency):
            _ = stack.enter_context(ExclusiveFileLease(
                state_root / "locks" / "capacity" / f"slot-{index}.lock",
                timeout_seconds=timeout_seconds, error_code="HOST_MAINTENANCE_TIMEOUT",
                platform_name=platform_name,
            ))
        yield


def package_compatibility_mutex(
    package_root: Path, *, timeout_seconds: float = 90,
) -> ExclusiveFileLease:
    digest = hashlib.sha256(str(package_root.resolve()).casefold().encode("utf-8")).hexdigest()
    return ExclusiveFileLease(
        host_state_root() / "locks" / "compat" / f"{digest}.lock",
        timeout_seconds=timeout_seconds,
        error_code="PACKAGE_COMPATIBILITY_MUTEX_TIMEOUT",
    )
