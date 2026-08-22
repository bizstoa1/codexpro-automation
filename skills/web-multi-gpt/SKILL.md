---
name: web-multi-gpt
description: Run genuine independent read-only ChatGPT sessions through Oracle, with all-of-N lanes, waves of at most five, isolated handoffs, and exactly one merger. No single-GPT role simulation and no new agbrowse runs.
---

# Oracle Web Multi-GPT

Use `bin/chatgpt_oracle_multi.py` with schema
`codex.chatgpt.oracle-multi/v2`. Required fields:

- absolute `project_root`, project-contained `output_dir`
- `solvers`: 2..25 unique safe lane IDs, absolute mission paths, and
  `access: read-only`
- `merger_mission_path`
- `max_concurrency`: 1..5
- `completion_policy: all-lanes`, `merger_policy: exactly-one`, and
  `nesting: forbidden`
- optional `next_stage_result_path` for comprehensive relay

Web Multi is an all-of-N advisory: every lane is read-only, nested Multi and
worktrees are forbidden, and exactly one merger is eligible only after every
lane has a nonempty terminal handoff. One failed or uncertain lane prevents
merger submission; partial success is never promoted.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_multi.py" --manifest C:\project\multi.json --dry-run
```

Before preview or launch, the exact project must have a tracked
`.codex/project-capabilities.json` and a host policy entry bound to that exact
root and profile SHA-256. Dry-run only compiles this contract; it writes no
lease, output tree, browser, session, or replacement workflow.

Each lane receives its own signed subject token, Oracle slug/run/output, and
only `@DevSpace` plus its mission path. The DevSpace tool boundary revalidates
the exact active lease for every read. Lane subjects cannot read the host
handoff/control tree; only the merger subject can consume handoffs in stable
lane order. Lanes run in stable waves of at most five, so a larger accepted
topology is not reduced. On Windows each lane uses a separate throwaway copy of
the signed-in Oracle profile, preventing one solver from closing or taking over
another solver's Chrome session.

The parent owns the exact-project lease until terminal harvest and Git
postflight. Recovery reconstructs only the same signed lease and recorded
subjects. A recovered merger records its one submission before launch; if that
attempt becomes uncertain, recovery must harvest the same slug and must not
submit a second merger. The durable v2 result ledger uses `running`,
`merger_ready`, `merger_submitting`, `complete`, `failed`, or
`attention_required`; every returned and persisted result validates against
`contracts/oracle-multi-result-v2.schema.json`.

No attachments, app/settings automation, broad tab cleanup, `--force`,
restart, or silent resubmission. Oracle owns one-shot tab archival. Existing
agbrowse Multi state is recovery-only. CodexPro is frozen and is never a solver
or merger transport.
