---
name: chatgpt-pro-browser
description: Use for an explicitly requested one-shot ChatGPT Pro plan, research, implementation, or review through Oracle. Qualified Pro uses read/write DevSpace; `pro-attachment` is an explicit immutable-evidence route. Return the Pro result only.
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

Pro is quota-limited. Invoke this skill only after an explicit user request;
never infer Pro from task difficulty, select it as an automatic upgrade, or use
it as a fallback from regular GPT. Qualified Pro uses Oracle with `GPT-5.6 Sol`
at the Pro effort and the manually registered DevSpace app. The mission must bind one
exact absolute project root. After one-time qualification, do not inspect,
register, repair, select, or otherwise verify ChatGPT app/settings state on
each run.

Before the first qualified Pro submission for a new project, the local runner
must verify that the normalized exact root is present in DevSpace
`allowedRoots`. A parent, child, or similarly named root is not sufficient.
The result is cached by exact config hash, so later questions in the same
project do not repeat endpoint/read probes; a changed config is revalidated.
Failure returns `DEVSPACE_EXACT_ROOT_UNAVAILABLE` before Oracle or a browser is
created and points to the complete root-preserving setup preview.

Pro reads the mission and applicable `AGENTS.md` chain completely. Within the
exact root it may inspect, create, edit, and remove mission-owned files and run
commands required by the mission. Repository safety rules remain authoritative.
It must not change accounts, app settings, or external state unless the mission
explicitly authorizes that action. It may not substitute a parent, child,
similarly named, active, or shell-boundary workspace, and may retry only the
same root once after a timeout.

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
3. Build a short UTF-8 mission that states the exact root, question, explicit
   file/command authority, and any evidence limitations. For `pro-attachment`,
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
transcript. The final nonempty line must also be
`TASK_OUTCOME: EXECUTED|NOT_EXECUTED|BLOCKED`; every citation, footnote, and
Markdown reference definition belongs before it. For bounded compatibility
with provider-rendered answers, the classifier also accepts exactly one marker
followed solely by single-line HTTP(S) Markdown reference definitions. Ordinary
trailing prose or another marker remains `unknown`. A terminal answer that reports
zero callable DevSpace tools or says the mission/root could not be read is
`NOT_EXECUTED`, never successful Pro work. When that exact terminal run is
durably captured, it releases same-project ownership and permits at most one
fresh retry with the same mission bytes and SHA-256. If the retry has the same
tool-exposure failure, stop with `attention_required`; do not loop, manipulate
ChatGPT app settings, or switch to attachments automatically. A nonzero exit
after submission is `attention_required`, not proof that the web session
failed.

## Recovery

Recover only the stored exact Oracle run directory and slug. `live` and
`harvest` may observe or collect that same session; they never restart,
resubmit, change route/model/effort, or create a replacement conversation.

When Oracle 0.17.1 reports the exact prompt-not-observed timeout, first run
exact-slug harvest. No live tab plus no recoverable conversation URL remains
submission-uncertain and needs explicit user confirmation before the
hash-bound `settle-no-submission` path can release a standalone qualified-Pro
run. Only then may an explicitly authorized single retry reuse the identical
mission bytes; no output, URL, mismatched hash, conflicting recovery state, or
ordinary trailing browser error may be treated as proof.

The same user-confirmed, fail-closed settlement is available to an explicit
`pro-attachment` run only when Oracle 0.17.1 reports the exact attachment-upload
timeout before prompt submission. It binds every attachment path, size, and
SHA-256; the source and transport mission copies; Oracle locator/version; exact
stdout/transcript and recovery bytes; and the absence of output and a
conversation URL. The user confirmation token is still mandatory. Any changed
attachment, live recovery state, URL, output, or unrecognized error keeps the
project locked.

For an already persisted agbrowse Pro run only, former recovery commands remain
available. They must never create a new run.
