# DevSpace + Tailscale Funnel setup

This repository does not modify DevSpace upstream and does not automate the ChatGPT settings UI. DevSpace is a local MCP server; it can read, edit, and run commands inside the roots you approve, so choose narrow project directories rather than an entire drive.

## Prerequisites

- Node.js 22.19–26.x, npm, and Git Bash on Windows.
- Tailscale with MagicDNS, HTTPS, and Funnel permission enabled for this device.
- A stable MagicDNS hostname, for example `your-device.your-tailnet.ts.net`.

## First connection (explicit and interactive)

From this repository, preview the plan and check the roots:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\one --root C:\projects\two --hostname your-device.your-tailnet.ts.net --dry-run
```

After reviewing the plan, use `--apply`. It invokes `devspace init` through Git Bash, then starts `devspace serve` and configures a Tailscale HTTPS Funnel to the local default port (7676). DevSpace asks you to select roots and enter the public origin. Enter exactly the reviewed roots and `https://your-device.your-tailnet.ts.net`, without `/mcp`.

The helper will not overwrite an existing Funnel mapping. If port 443 is
already owned by another local service, choose an unused supported Funnel port
explicitly, for example `--public-port 8443`; the registration URL then becomes
`https://your-device.your-tailnet.ts.net:8443/mcp`.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\one --root C:\projects\two --hostname your-device.your-tailnet.ts.net --apply
```

DevSpace prints an Owner password during initialization and stores it in its standard local configuration. Do not put that password in a script, manifest, issue, or repository.

The managed service is launched with `DEVSPACE_TOOL_MODE=full`, which enables
read-only workspace discovery (`grep`, `glob`, and `ls`) without expanding the
approved roots. Keep the root list in DevSpace's configuration; the launch
environment only selects the tool mode.

The managed service also advertises `offline_access` together with the
`devspace` OAuth scope so ChatGPT can renew its authorization instead of losing
the connector after the one-hour access token expires. After upgrading an
existing setup from metadata that omitted `offline_access`, recreate or
reconnect the app once so ChatGPT reads the corrected OAuth metadata.

## Manual ChatGPT registration

Enable Developer Mode in ChatGPT and manually create the connector:

- Name: `DevSpace`
- MCP URL: `https://your-device.your-tailnet.ts.net/mcp`

To use a different display name, store the identical name in
`%USERPROFILE%\.codex\chatgpt-workspace.json`, for example
`{"app_name":"codex"}`. New Oracle manifests then mention that registered app;
the default remains `DevSpace`.

Approve the initial Owner-password page when DevSpace asks. This tooling never opens settings, creates/deletes apps, picks permissions, inspects app lists, or selects an app in the composer.

## Read-only diagnosis

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py doctor --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

Diagnosis checks local DevSpace `/mcp`, then `tailscale funnel status --json`, then the public `/mcp` endpoint. If the endpoint is healthy but a ChatGPT tool call fails, keep the server running and re-check the same manual connector URL; do not automate deletion or re-registration.

## Idempotent service/Funnel recovery

After a DevSpace or Tailscale restart, restore only the already-approved public
route with `ensure`. It first proves that the local MCP endpoint is healthy,
then reuses a matching Funnel or recreates the missing exact mapping. A port
listener alone is not accepted as DevSpace health, and a conflicting Funnel is
never overwritten.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py ensure --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

Run this command from the hidden startup wrapper after DevSpace becomes
healthy. It does not touch ChatGPT settings or app registration.

For login-time recovery, use `recover` from a hidden per-user startup entry.
Unlike `ensure`, it starts the exact hash-validated DevSpace service when the
local MCP endpoint is unavailable, then verifies or restores the same Funnel:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py recover --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

The command is idempotent, never overwrites a conflicting Funnel mapping, and
does not contain or print the DevSpace Owner credential.

The shipped `scripts/start_devspace_bootstrap.ps1` wrapper reads the non-secret
host, root, port, and Python path from
`%CODEX_HOME%\config\codexpro-devspace-bootstrap.json`, retries while the
Tailscale service is still settling after login, and writes monthly logs under
`%CODEX_HOME%\logs\codexpro-devspace`. Register that wrapper as a hidden
per-user login command; do not place the Owner credential in its config.

It also reports the required managed tool mode (`full`) and any persisted
`toolMode`. A configured non-`full` value is advisory failure because a
manually started service may not inherit the managed launch environment.

Tailscale Funnel makes the endpoint public. It requires Tailnet permissions and uses the device's stable MagicDNS name. Review Tailscale's policy and exposure rules before `--apply`.
