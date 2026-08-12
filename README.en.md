# Codex Web GPT Automation

English | [한국어](README.md)

A Windows and macOS automation toolkit that delegates planning, research, review, code
changes, and testing to web ChatGPT while keeping local Codex work focused on
transport, recovery, identity, hashes, and the final deterministic gate.

It connects two upstream tools:

- [Oracle](https://github.com/steipete/oracle) creates signed-in ChatGPT browser
  sessions, selects the model, waits for the response, and harvests the result.
- [DevSpace](https://github.com/Waishnav/devspace) lets ChatGPT read, edit, and
  run commands only inside project roots approved by the user.

Regular GPT runs send one line containing `@DevSpace` and the absolute UTF-8
mission-file path. Qualified Pro runs use `GPT-5.6 Sol` at the Pro effort with
read-only DevSpace. Explicit `pro-attachment` is only for immutable external
evidence or artifacts that DevSpace cannot read.

## What it provides

- Web GPT can inspect, change, and test a local project.
- Direct, plan, review, edit, orchestrator, deep-research, and Pro modes.
- Genuine Web Multi-GPT with independent ChatGPT sessions.
- Read-only Local Multi-GPT with parallel Codex lanes on the PC.
- Comprehensive workflows from planning through implementation and final gate.
- Per-project exclusion, immutable mission and attachment hashes, and exact
  session recovery.
- Isolated browser profiles so different projects can run concurrently.
- Automatic archive lifecycle for conversations owned by Oracle.
- Install receipts, backups, rollback, and uninstall support.
- OMO `ultrawork` todos with a GJC-style brownfield interview gate.
- A 75-minute checkpoint and exact 80-minute handoff without duplicate submission.

## How it works

```text
User request
    -> Codex writes a UTF-8 mission and manifest
    -> Oracle starts a signed-in ChatGPT session
       |-- regular GPT: @DevSpace + mission path
       `-- Pro: read-only @DevSpace by default, or explicit hash-frozen attachments
    -> web GPT explores, plans, edits, and tests
    -> Oracle saves the answer as a local artifact
    -> Codex checks identity, hashes, and one deterministic final gate
```

Host state and ChatGPT output are stored outside DevSpace projects under
`%USERPROFILE%\.codex\state\chatgpt-oracle` on Windows and
`~/.codex/state/chatgpt-oracle` on macOS.

## Modes and English invocation names

| Mode | CLI / natural-language name | Purpose | Transport |
|---|---|---|---|
| Regular GPT | `direct` / GPT | Questions, analysis, and small tasks | Oracle + DevSpace |
| Plan | `plan` / plan | Design before implementation | Oracle + DevSpace, read-only |
| Review | `review` / review | Independent code or plan review | Oracle + DevSpace, read-only |
| Edit | `edit` / edit | Scoped changes and tests | Oracle + DevSpace |
| Orchestrator | `orchestrator` / orchestrator | One GPT completes an already-scoped task | Oracle + DevSpace |
| Deep Research | `deep-research` / deep research | Public research plus project evidence | Oracle Deep Research + DevSpace |
| Web Multi-GPT | Web Multi-GPT | Independent parallel perspectives and merger | 2-25 Oracle sessions |
| Local Multi-GPT | Local Multi-GPT | Local advisory synthesis and counterexample search | Fixed `gpt-5.6-luna` + `max`, read-only |
| Comprehensive | comprehensive mode | Plan, optional Pro/Multi, review, implementation, gate | Staged Oracle workflow |
| Pro | `pro` / Pro | Independent final judgment or design review; result only | Oracle + read-only DevSpace by default; explicit `pro-attachment` |

Orchestrator mode is a single web submission. Comprehensive mode contains an
orchestrator-equivalent implementation stage plus planning, independent review,
optional Pro or Web Multi-GPT, and final gates.

Standalone Pro is a one-shot review route, separate from comprehensive mode. It
reviews the attached plan, code, or document, returns the durable result, and
stops; it never transitions automatically into implementation or another stage.
Use comprehensive mode only when the work must continue from planning through
implementation and gates.

Local Multi-GPT and Web Multi-GPT are separate paths. Local Multi-GPT is an
optional advisory tool that runs Codex child lanes on the PC. Every stage is
fixed to `gpt-5.6-luna` with `max` reasoning; any other model or effort is
rejected before a child process starts. Web Multi-GPT instead runs independent
ChatGPT web sessions through Oracle and merges their results.

## Requirements

- Windows 11 or macOS 12 or later (Apple Silicon supported)
- Python
- Node.js 22.19 or later and earlier than 27
- Git for Windows / Git Bash on Windows; `lsof` and `launchd` on macOS
- A stable HTTPS tunnel (Tailscale Funnel recommended; Cloudflare named tunnel,
  ngrok static domain, or a custom proxy are supported paths)
- An Oracle browser profile signed in to ChatGPT
- One manually registered DevSpace app in ChatGPT Developer Mode

The validated combination is Oracle `0.17.1` and DevSpace `1.0.4`. The installer
applies Windows compatibility patches only when exact upstream file hashes
match.

## Install

```powershell
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
.\install.ps1 -WhatIf
.\install.ps1
```

The installer backs up replaced files and writes durable install receipts under
`%USERPROFILE%\.codex\receipts`.

On the first interactive install it asks `Local Multi-GPT도 설치할까요? [y/N]`.
The default is No. Use `.\install.ps1 -EnableLocalMultiGpt` for an explicit or
unattended opt-in. The skill, local MCP server, and MCP registration are then
installed together; restart Codex afterward. See
[Optional Local Multi-GPT](docs/LOCAL_MULTI_GPT.md).

On macOS, use the shared Python lifecycle:

```bash
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python3 install.py --dry-run
python3 install.py
python3 doctor.py
```

Use `python3 install.py --enable-local-multi-gpt` only when the optional local
parallel-reasoning lane is wanted.

Receipts are stored under `~/.codex/receipts`. `python3 rollback.py` and
`python3 uninstall.py` perform an exact, receipt-backed inverse. See the
[macOS Ultrawork guide](docs/MACOS_ULTRAWORK.md) for OMO and launchd setup.

## First install and one-time DevSpace setup

Follow the [ordered first-install guide](docs/FIRST_INSTALL.md): lifecycle install,
stable public URL, DevSpace Owner password, reboot recovery, dedicated Oracle
browser login, and finally manual ChatGPT registration under the name `codex`.
Tailscale is the automated and reboot-tested route. A Cloudflare named tunnel,
ngrok static domain, or custom HTTPS proxy is usable when its stable URL and OS
startup service are already managed.

As an optional recommended step,
`bin/codex_global_agents_setup.py` atomically preserves and merges the user's
global configuration: a Sol High primary, cost-bounded Terra/Luna subagents,
two concurrent workers by policy, and a hard cap of three. It does not enable
the unstable `multi_agent_v2` feature. Restart Codex after applying it so new
tasks load the role registry.

You do not install one ChatGPT app per project. Register one DevSpace app and
add each permitted project as another `--root` argument.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup `
  --root C:\projects\alpha `
  --root C:\projects\beta `
  --hostname your-device.your-tailnet.ts.net `
  --dry-run
```

Review the output, then replace `--dry-run` with `--apply`. In ChatGPT Developer
Mode, manually register one app:

- Name: `codex`
- URL: `https://your-device.your-tailnet.ts.net/mcp`

After owner approval, the automation does not inspect or manipulate ChatGPT
settings, app lists, permissions, deletion, or picker UI per task. Adding a new
project only changes the DevSpace allowed roots.

Immediately after first registration or a requested reconnect, run the guide's
`post-register` command once, then validate the actual registered app with a
regular, non-Pro Oracle `@codex` read-only probe. Codex Desktop's built-in
`DevSpace` plugin is a separate connector and is not registration evidence; do
not spend a Pro session as the first connectivity test.

On macOS, omit `--hostname` to discover the signed-in Tailscale MagicDNS name.
Preview the exact, single approved root before applying it:

```bash
python3 skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup \
  --root "$PWD" --dry-run
```

See [DevSpace and Tailscale setup](docs/DEVSPACE_TAILSCALE_SETUP.md) for the
complete procedure.

## Regular GPT example

Create a UTF-8 mission file inside the project, then dry-run the manifest:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" `
  --mode orchestrator `
  --project-root C:\project `
  --mission-path C:\project\mission.md `
  --manifest-output C:\project\.ai-bridge\oracle.json `
  --reasoning-level "Very High" `
  --dry-run
```

Remove `--dry-run` only when the run is authorized.

## Pro example

Qualified Pro binds to the exact project root and uses DevSpace read-only. It
starts adaptive, decision-relevant discovery with the `read('.')` directory-list
compatibility path and may read broadly, but may not write, edit, invoke a
shell, or run commands. Use an explicit `pro-attachment` contract only for
immutable external evidence or artifacts that DevSpace cannot read.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" `
  --mode pro `
  --project-root C:\project `
  --mission-path C:\project\pro.md `
  --manifest-output C:\project\.ai-bridge\pro.json `
  --dry-run
```

## Execution and recovery rules

- One active or uncertain Oracle workflow is allowed per normalized project.
- Different projects can run concurrently through isolated profiles.
- Web Multi-GPT runs child sessions in waves of at most five.
- New web work is split into checkpointable episodes of no more than 70 minutes.
- At 75 minutes the harness prevents new fan-out and writes durable state; at
  80 minutes it evaluates an exact handoff.
- A live Oracle run is recovered by its existing slug and conversation URL. It
  is never submitted to a new session.
- A browser or local-process exit is not proof that the web task failed.
- Recovery uses only the persisted Oracle slug and exact conversation URL. It
  never resubmits the task.
- Completion requires Oracle exit code zero and a fresh, nonempty durable output.

Recover one exact run with:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_run.py" recover `
  --run-dir C:\exact\oracle-run `
  --action harvest
```

## Update, rollback, and uninstall

```powershell
.\install.ps1 -WhatIf
.\install.ps1
.\rollback.ps1
.\uninstall.ps1
```

Use `-InstallLegacyRecoveryDependency` only on a machine that must recover an
already persisted legacy run.

## Documentation

- [Global ChatGPT routing and mode selection](docs/GLOBAL_CHATGPT_ROUTING.md)
- [Ordered first install and onboarding](docs/FIRST_INSTALL.md)
- [DevSpace and Tailscale setup](docs/DEVSPACE_TAILSCALE_SETUP.md)
- [macOS Ultrawork and 75/80-minute recovery](docs/MACOS_ULTRAWORK.md)
- [Technical changelog](docs/CHANGELOG.md)
- [Frozen legacy recovery assets](docs/FROZEN_LEGACY.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)
- [Security policy](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## Legacy compatibility

The former CodexPro and agbrowse files remain only for exact recovery of already
persisted legacy runs. They are not a new-work route or fallback. See
[Frozen legacy assets](docs/FROZEN_LEGACY.md) for the inventory.

## License

MIT License. Third-party copyrights and licenses for Oracle, DevSpace, and other
components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
