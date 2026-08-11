from __future__ import annotations

import importlib.util
import json
import plistlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "codexpro_cloudflared_launchd.py"


def load_module():
    assert MODULE_PATH.is_file()
    spec = importlib.util.spec_from_file_location("codexpro_cloudflared_launchd_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cloudflared_artifacts_are_isolated_and_route_only_devspace(tmp_path: Path) -> None:
    module = load_module()
    project_root = tmp_path / "project"
    project_root.mkdir()
    credentials = tmp_path / "credentials.json"
    credentials.touch()
    config = tmp_path / "config.yml"

    spec = module.TunnelSpec.parse(
        hostname="devspace.example.com",
        tunnel_id="44ba10ff-1b67-47eb-a10c-9ec085647d98",
        credentials_file=credentials,
    )
    config_text = module.render_config(spec)
    runtime = module.ServiceRuntime(
        project_root=project_root.resolve(),
        cloudflared="/opt/homebrew/bin/cloudflared",
        config=config,
        logs=tmp_path / "logs",
    )
    plist = module.service_plist(runtime=runtime, tunnel_id=spec.tunnel_id)

    assert module.LABEL == "com.ventianima.codexpro-automation.cloudflared-devspace"
    assert "hostname: devspace.example.com" in config_text
    assert "service: http://127.0.0.1:7676" in config_text
    assert config_text.rstrip().endswith("- service: http_status:404")
    assert plist["CodexProManaged"] is True
    assert plist["ProgramArguments"] == [
        "/opt/homebrew/bin/cloudflared",
        "tunnel",
        "--no-autoupdate",
        "--config",
        str(config),
        "run",
        str(spec.tunnel_id),
    ]
    assert plistlib.loads(plistlib.dumps(plist))["Label"] == module.LABEL


def test_install_writes_private_config_and_managed_launchagent(tmp_path: Path) -> None:
    module = load_module()
    codex_home = tmp_path / "codex"
    launch_agents = tmp_path / "LaunchAgents"
    project_root = tmp_path / "project"
    project_root.mkdir()
    credentials = tmp_path / "credentials.json"
    credentials.touch()
    cloudflared = tmp_path / "cloudflared"
    cloudflared.touch(mode=0o755)
    spec = module.TunnelSpec.parse(
        hostname="devspace.example.com",
        tunnel_id="44ba10ff-1b67-47eb-a10c-9ec085647d98",
        credentials_file=credentials,
    )

    paths = module.InstallPaths(
        codex_home=codex_home,
        launch_agents=launch_agents,
        project_root=project_root,
        cloudflared=cloudflared,
    )
    result = module.install_service(paths=paths, spec=spec, load=False)

    config = Path(result["config"])
    plist_path = Path(result["plist"])
    assert config.parent == codex_home / "state" / "codexpro-cloudflare"
    assert config.stat().st_mode & 0o777 == 0o600
    assert plistlib.loads(plist_path.read_bytes())["CodexProManaged"] is True
    assert result["loaded"] is False


def test_doctor_accepts_installed_managed_artifacts(tmp_path: Path) -> None:
    module = load_module()
    codex_home = tmp_path / "codex"
    launch_agents = tmp_path / "LaunchAgents"
    project_root = tmp_path / "project"
    project_root.mkdir()
    credentials = tmp_path / "credentials.json"
    credentials.touch()
    cloudflared = tmp_path / "cloudflared"
    cloudflared.touch(mode=0o755)
    spec = module.TunnelSpec.parse(
        hostname="devspace.example.com",
        tunnel_id="44ba10ff-1b67-47eb-a10c-9ec085647d98",
        credentials_file=credentials,
    )
    paths = module.InstallPaths(codex_home, launch_agents, project_root, cloudflared)
    module.install_service(paths=paths, spec=spec, load=False)

    result = module.doctor_service(codex_home=codex_home, launch_agents=launch_agents)

    assert result["ok"] is True
    assert result["managed"] is True
    assert result["installed"] is True


def test_cli_installs_artifacts_without_loading_launchd(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    launch_agents = tmp_path / "LaunchAgents"
    project_root = tmp_path / "project"
    project_root.mkdir()
    credentials = tmp_path / "credentials.json"
    credentials.touch()
    cloudflared = tmp_path / "cloudflared"
    cloudflared.touch(mode=0o755)

    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--codex-home",
            str(codex_home),
            "--launch-agents",
            str(launch_agents),
            "install",
            "--project-root",
            str(project_root),
            "--cloudflared",
            str(cloudflared),
            "--hostname",
            "devspace.example.com",
            "--tunnel-id",
            "44ba10ff-1b67-47eb-a10c-9ec085647d98",
            "--credentials-file",
            str(credentials),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["label"] == "com.ventianima.codexpro-automation.cloudflared-devspace"
