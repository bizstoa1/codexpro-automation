---
name: chatgpt-workspace-setup
description: Part of the current Oracle path, perform the one-time, user-authorized DevSpace and Tailscale Funnel setup or read-only diagnosis for ChatGPT workspace access. Never use this during ordinary GPT runs and never automate ChatGPT settings or app selection.
---

# ChatGPT Workspace Setup

Use this skill only for a first connection, an explicitly requested DevSpace/Tailscale repair, or a read-only endpoint diagnosis. Ordinary ChatGPT modes must not call it.

## One-time setup

The user must provide every allowed project root and the Tailscale MagicDNS hostname. A drive root such as `C:\` is rejected. The setup process is intentionally interactive because DevSpace itself stores the Owner secret in its own standard location; never copy that secret into a manifest, log, or Git file.

Preview the exact setup plan first:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\example --hostname your-device.your-tailnet.ts.net --dry-run
```

Only after the user approves the interactive DevSpace initialization and public Funnel exposure:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\example --hostname your-device.your-tailnet.ts.net --apply
```

`--apply` runs DevSpace through Git Bash without a visible Windows console, starts `devspace serve`, and creates an HTTPS Funnel to `127.0.0.1:7676`. During `devspace init`, enter only the listed roots and the public origin `https://<hostname>` (without `/mcp`).

Before starting or restarting DevSpace 1.0.4, run the installed
`bin/chatgpt_devspace_compat.py`. It hash-validates the exact upstream
`dist/workspaces.js`, backs it up, and applies bounded concurrent discovery
that skips transient `.pytest-*` and cache trees. If it reports
`service_restart_required=true`, restart DevSpace before any Oracle
submission. Unknown versions or hashes fail closed.

On Windows, any Startup shortcut or service wrapper must read
`%USERPROFILE%\.devspace\config.json` at every launch and derive
`DEVSPACE_ALLOWED_ROOTS` from its current `allowedRoots`. Never hardcode a
second root list in the startup wrapper: DevSpace gives the environment
variable precedence over the persisted config, so a stale wrapper silently
removes newer projects after every reboot.

Every new or managed DevSpace service launch must set
`DEVSPACE_TOOL_MODE=full`. This retains the approved-root boundary while
making read-only workspace discovery tools such as `grep`, `glob`, and `ls`
available. Do not change ChatGPT connector settings to compensate for a tool
mode issue. `doctor` reports the managed launch setting and any persisted
`toolMode`; an explicitly non-`full` persisted mode requires service setup
review, while a running process environment is not inferred from an HTTP probe.

Managed launches also set
`DEVSPACE_OAUTH_SCOPES=devspace,offline_access`. DevSpace already issues refresh
tokens; advertising `offline_access` lets ChatGPT request and renew them. If an
older app registration was created before this metadata was exposed, the user
must reconnect or recreate that app once. Never automate that settings action.

The only app information to enter manually in ChatGPT Developer Mode is:

- Recommended app name: `DevSpace`
- URL: `https://<hostname>/mcp`
- Complete the first Owner-password approval page that DevSpace presents.

Never open ChatGPT settings, register/delete an app, change permissions, inspect app lists, select an app name, or press Tab in the ChatGPT UI.

## Diagnosis

This is read-only and checks only local DevSpace, then Funnel status, then the public `/mcp` endpoint:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py doctor --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

If the public endpoint is healthy but a ChatGPT call still fails, report the same registration URL and stop. Do not re-register the app automatically.

For an explicitly requested service/Funnel repair, use the idempotent `ensure`
command after DevSpace starts:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py ensure --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

`ensure` requires the actual local MCP endpoint to respond before it reasserts
the exact Funnel mapping. It refuses a conflicting mapping and never changes
ChatGPT settings or app registration.
