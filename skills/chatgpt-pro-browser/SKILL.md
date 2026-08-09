---
name: chatgpt-pro-browser
description: Use for a one-shot ChatGPT Pro plan, research, or review through Oracle. Qualified Pro uses read-only DevSpace; `pro-attachment` is an explicit immutable-evidence route. Return the Pro result only.
---

# ChatGPT Pro through Oracle

## Standalone scope

This is the standalone, one-shot Pro route. It may produce a plan, research
finding, review, or decision, but it returns that durable Pro result to Codex
and stops. It never starts a review-to-implementation chain, authors a
follow-on implementation stage, or invokes `chatgpt-pro-plan-handoff` on its
own. If the user asks for comprehensive mode, use `chatgpt-pro-plan-handoff`
instead.

Oracle is the only backend for a new Pro run. There is no new agbrowse,
CodexPro, in-app Browser, custom CDP/Playwright, or `@chrome` fallback.

## Qualified default route

Qualified Pro uses Oracle with `GPT-5.6 Sol` at the Pro effort and the
manually registered DevSpace app in read-only mode. The mission must bind one
exact absolute project root. After one-time qualification, do not inspect,
register, repair, select, or otherwise verify ChatGPT app/settings state on
each run.

Pro reads the mission and applicable `AGENTS.md` chain completely, then begins
with the `read('.')` directory-list compatibility call. It may discover and
read broadly and adaptively within that exact root: current Git state, project
rules, mission artifacts, source and configuration, failures, logs, prior
decisions, tests, and results whenever they are decision-relevant. A narrow
preselected evidence allowlist is not required and must not conceal relevant
contradictory evidence.

Read-only is absolute: Pro must not write or edit files, invoke a shell, or run
commands. It may not substitute a parent, child, similarly named, active, or
shell-boundary workspace. It may retry only the same root once after a timeout.

## Explicit attachment route

`pro-attachment` is attachment-only through Oracle. Use it only when the
question depends on immutable/external evidence or artifacts that DevSpace
cannot read. Its mission and every attachment are exact regular non-symlink
files with frozen SHA-256 values. It is an explicit evidence contract, never an
automatic fallback from qualified Pro DevSpace.

Build this route with the repository's
`scripts/build_project_context_packet.py` helper. Preview and validate the
packet before launch, preserve the generated manifest and hashes, and attach
only the mission plus the explicitly frozen evidence packet. Do not scrape
the project into an ad-hoc ZIP or infer attachments from prose.

## Required Web Multi decision

Every standalone Pro result ends with this exact decision block:

```text
WEB_MULTI_NEEDED: YES|NO
WEB_MULTI_REASON: evidence-based reason tied to the decision and alternatives
```

Pro chooses `YES` only when three to five materially independent regular GPT
sessions are likely to add decision-relevant alternatives or evidence. Their
mission carries the same project maximum-context evidence and the durable Pro answer,
assigns stable lane order, and synthesis/judge criteria. After a durable Pro
answer says `WEB_MULTI_NEEDED: YES`, Codex starts that ready-to-run Web Multi-GPT Very
High mission automatically without a routine user
choice. It waits for the exact Pro session to be terminal first and preserves
the same-project serialization contract. Choose `NO` for a trivial, single-answer, or purely mechanical question. This optional advisory handoff
does not turn the standalone Pro result into a review-to-implementation chain.

## Preflight and completion

1. Resolve and hash-validate the tested Oracle compatibility contract.
2. Bind the same normalized-project mutex used by regular Oracle work.
3. Build a short UTF-8 mission that states the exact root, question,
   read-only authority, and any evidence limitations. For `pro-attachment`,
   freeze the required attachments and their hashes instead.
4. Use a fresh Oracle slug and require Oracle model and transport evidence
   before accepting a send.

The public dispatcher entry points are:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" --mode pro --project-root <ROOT> --mission-path <MISSION> --manifest-output <MANIFEST> --dry-run
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" --mode pro-attachment --project-root <ROOT> --mission-path <MISSION> --attachment <PACKET> --manifest-output <MANIFEST> --dry-run
```

Remove `--dry-run` only after the manifest, project mutex, Oracle version, and
compatibility hashes pass preflight. The default `pro` command never accepts
attachments; `pro-attachment` never invokes DevSpace.

Completion requires the requested Pro model/effort evidence, exit zero, fresh
nonempty host-only `output.md`, immutable run identity, and a refreshed
transcript. A nonzero exit after submission is `attention_required`, not proof
that the web session failed.

## Recovery

Recover only the stored exact Oracle run directory and slug. `live` and
`harvest` may observe or collect that same session; they never restart,
resubmit, change route/model/effort, or create a replacement conversation.

For an already persisted agbrowse Pro run only, former recovery commands remain
available. They must never create a new run.
