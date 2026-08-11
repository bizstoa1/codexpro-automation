#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NewType, Sequence, TypedDict


LABEL = "com.ventianima.codexpro-automation.cloudflared-devspace"
ORIGIN = "http://127.0.0.1:7676"
TunnelId = NewType("TunnelId", str)


@dataclass(frozen=True, slots=True)
class TunnelConfigError(ValueError):
    field: str
    reason: str

    def __str__(self) -> str:
        return f"{self.field}: {self.reason}"


@dataclass(frozen=True, slots=True)
class TunnelSpec:
    hostname: str
    tunnel_id: TunnelId
    credentials_file: Path

    @classmethod
    def parse(cls, *, hostname: str, tunnel_id: str, credentials_file: Path) -> TunnelSpec:
        normalized_hostname = hostname.strip().lower().rstrip(".")
        labels = normalized_hostname.split(".")
        hostname_is_valid = (
            len(normalized_hostname) <= 253
            and len(labels) >= 2
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
        )
        if not hostname_is_valid:
            raise TunnelConfigError(field="hostname", reason="expected a valid DNS hostname")
        try:
            normalized_tunnel_id = TunnelId(str(uuid.UUID(tunnel_id)))
        except ValueError as exc:
            raise TunnelConfigError(field="tunnel_id", reason="expected a UUID") from exc
        resolved_credentials = credentials_file.expanduser().resolve()
        if not resolved_credentials.is_file():
            raise TunnelConfigError(field="credentials_file", reason="file does not exist")
        return cls(
            hostname=normalized_hostname,
            tunnel_id=normalized_tunnel_id,
            credentials_file=resolved_credentials,
        )


class ServicePlist(TypedDict):
    Label: str
    ProgramArguments: list[str]
    RunAtLoad: bool
    KeepAlive: bool
    ProcessType: str
    ThrottleInterval: int
    WorkingDirectory: str
    StandardOutPath: str
    StandardErrorPath: str
    CodexProManaged: bool


@dataclass(frozen=True, slots=True)
class InstallPaths:
    codex_home: Path
    launch_agents: Path
    project_root: Path
    cloudflared: Path


@dataclass(frozen=True, slots=True)
class ServiceRuntime:
    project_root: Path
    cloudflared: str
    config: Path
    logs: Path


class InstallResult(TypedDict):
    ok: bool
    config: str
    plist: str
    label: str
    loaded: bool


class DoctorResult(TypedDict):
    ok: bool
    installed: bool
    managed: bool
    loaded: bool
    config: str
    plist: str


def render_config(spec: TunnelSpec) -> str:
    credentials = json.dumps(str(spec.credentials_file), ensure_ascii=False)
    return (
        f"tunnel: {spec.tunnel_id}\n"
        f"credentials-file: {credentials}\n"
        "ingress:\n"
        f"  - hostname: {spec.hostname}\n"
        f"    service: {ORIGIN}\n"
        "  - service: http_status:404\n"
    )


def service_plist(*, runtime: ServiceRuntime, tunnel_id: TunnelId) -> ServicePlist:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            runtime.cloudflared,
            "tunnel",
            "--no-autoupdate",
            "--config",
            str(runtime.config),
            "run",
            str(tunnel_id),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 15,
        "WorkingDirectory": str(runtime.project_root),
        "StandardOutPath": str(runtime.logs / "cloudflared.out.log"),
        "StandardErrorPath": str(runtime.logs / "cloudflared.err.log"),
        "CodexProManaged": True,
    }


def _write_atomic(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_service(*, paths: InstallPaths, spec: TunnelSpec, load: bool) -> InstallResult:
    codex_home = paths.codex_home.expanduser().resolve()
    launch_agents = paths.launch_agents.expanduser().resolve()
    project_root = paths.project_root.expanduser().resolve()
    cloudflared = paths.cloudflared.expanduser().resolve()
    if not project_root.is_dir():
        raise TunnelConfigError(field="project_root", reason="directory does not exist")
    if not cloudflared.is_file():
        raise TunnelConfigError(field="cloudflared", reason="executable does not exist")

    state = codex_home / "state" / "codexpro-cloudflare"
    logs = codex_home / "logs" / "codexpro-automation"
    config = state / "config.yml"
    plist_path = launch_agents / f"{LABEL}.plist"
    if plist_path.exists():
        try:
            prior = plistlib.loads(plist_path.read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            raise TunnelConfigError(field="launch_agent", reason="existing plist is unreadable") from exc
        if prior.get("CodexProManaged") is not True or prior.get("Label") != LABEL:
            raise TunnelConfigError(field="launch_agent", reason="label is owned by another service")

    logs.mkdir(parents=True, exist_ok=True)
    runtime = ServiceRuntime(
        project_root=project_root,
        cloudflared=str(cloudflared),
        config=config,
        logs=logs,
    )
    plist = service_plist(runtime=runtime, tunnel_id=spec.tunnel_id)
    _write_atomic(config, render_config(spec).encode("utf-8"), 0o600)
    _write_atomic(plist_path, plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True), 0o644)

    if load:
        if sys.platform != "darwin":
            raise TunnelConfigError(field="platform", reason="launchd requires macOS")
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(plist_path)], capture_output=True, text=True, check=False)
        result = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise TunnelConfigError(field="launchctl", reason=result.stderr.strip() or "bootstrap failed")

    return {
        "ok": True,
        "config": str(config),
        "plist": str(plist_path),
        "label": LABEL,
        "loaded": load,
    }


def doctor_service(*, codex_home: Path, launch_agents: Path) -> DoctorResult:
    config = codex_home.expanduser().resolve() / "state" / "codexpro-cloudflare" / "config.yml"
    plist_path = launch_agents.expanduser().resolve() / f"{LABEL}.plist"
    installed = config.is_file() and plist_path.is_file()
    managed = False
    if plist_path.is_file():
        try:
            value = plistlib.loads(plist_path.read_bytes())
            managed = value.get("CodexProManaged") is True and value.get("Label") == LABEL
        except (OSError, plistlib.InvalidFileException):
            managed = False
    loaded = False
    if sys.platform == "darwin":
        current = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True,
            text=True,
            check=False,
        )
        loaded = current.returncode == 0
    return {
        "ok": installed and managed,
        "installed": installed,
        "managed": managed,
        "loaded": loaded,
        "config": str(config),
        "plist": str(plist_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--launch-agents", type=Path, default=Path.home() / "Library" / "LaunchAgents")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--project-root", type=Path, required=True)
    install.add_argument("--cloudflared", type=Path, required=True)
    install.add_argument("--hostname", required=True)
    install.add_argument("--tunnel-id", required=True)
    install.add_argument("--credentials-file", type=Path, required=True)
    install.add_argument("--load", action="store_true")
    commands.add_parser("doctor")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            spec = TunnelSpec.parse(
                hostname=args.hostname,
                tunnel_id=args.tunnel_id,
                credentials_file=args.credentials_file,
            )
            paths = InstallPaths(
                codex_home=args.codex_home,
                launch_agents=args.launch_agents,
                project_root=args.project_root,
                cloudflared=args.cloudflared,
            )
            result = install_service(paths=paths, spec=spec, load=args.load)
        else:
            result = doctor_service(codex_home=args.codex_home, launch_agents=args.launch_agents)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    except TunnelConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
