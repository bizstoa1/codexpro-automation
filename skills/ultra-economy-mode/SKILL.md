---
name: ultra-economy-mode
description: Run 초절약모드 for expensive or long Codex tasks by keeping the local commander and every native subagent on exact gpt-5.6-luna with max reasoning, assigning architecture to qualified ChatGPT Pro, and moving implementation and review into separate web sessions. Use when the user says 초절약모드, Ultra Economy Mode, or explicitly requests a Luna Max local commander with web-first execution.
---

# Ultra Economy Mode

Minimize local model cost without treating the small local model as the main
reasoning surface. Use the existing Oracle comprehensive engine with the
`ultra-economy` profile.

## Activation gate

1. Read the **current task runtime** model and reasoning effort from explicit
   runtime metadata. Do not infer them from `~/.codex/config.toml`, an agent
   role file, a previous task, or the user's statement.
2. Continue only when the current task is exactly `gpt-5.6-luna` with `max`
   reasoning.
3. If either value differs or is not observable, stop before creating a
   subagent, browser, Oracle, Pro, or web session. Ask the user to select Luna
   and Max for this task, then invoke 초절약모드 again.
4. Never rewrite the user's global model defaults to activate this mode.

## Local commander contract

- Keep the commander to routing, compact mission creation, durable receipt
  reading, exact-session monitoring, hash checks, and one deterministic gate.
- For every substantive local semantic task, spawn one fresh `default`
  subagent with explicit model `gpt-5.6-luna`, reasoning effort `max`, and a
  minimal history fork. Do not use the globally configured scout,
  implementer, or verifier roles because their model contracts may differ.
- Give a subagent only the bounded objective, exact artifact paths, current
  stage receipt, authority boundary, and success criteria. Never forward the
  full conversation or a growing transcript.
- Prefer one worker at a time. Use at most two only for genuinely independent
  read-only work; never exceed the global cap of three spawned threads.
- Deterministic host scripts and simple status polling remain commander work;
  they do not require model delegation.

## Web-first stage graph

Run separate sessions so each semantic boundary can inspect the prior durable
artifact:

```text
one-time exact-root qualification
  -> qualified Pro design (read-only)
  -> regular web design review and implementation-mission authoring
  -> regular web implementation and project tests
  -> separate regular web final verification or repair handoff
  -> one local deterministic gate
```

Use `bin/chatgpt_oracle_comprehensive.py` with these manifest fields:

```json
{
  "schema": "codex.chatgpt.oracle-comprehensive/v1",
  "workflow_profile": "ultra-economy",
  "initial_stage": "pro"
}
```

Add the normal absolute project, workflow, mission, app, and local gate fields.
The engine resolves `CODEX_THREAD_ID` to the matching Codex rollout and reads
the latest runtime-authored `turn_context`. A manifest, environment-only model
claim, `config.toml`, prompt, or child-agent report is not accepted as proof.

The engine must fail closed before submission when the profile, Pro-first
stage, trusted Luna Max runtime evidence, exact root qualification, or minimum four-stage
budget is missing. Do not substitute an attachment for readable DevSpace, and
do not use Pro as the first connector-health probe.

## Failure and residual work

- Recover only the exact persisted Oracle stage. Never create a replacement
  submission from an ambiguous or possibly submitted failure.
- If web work reaches a genuine local-only boundary, give that one bounded
  residual task to a fresh Luna Max subagent, then return to a separate web
  verification stage when semantic review is still needed.
- Do not repeat app/settings checks or endpoint probes after the project's
  exact-root qualification while the DevSpace config hash is unchanged.
- Completion requires the final web PASS receipt and a zero-exit local
  deterministic gate. Local Luna judgment is not release authority.
