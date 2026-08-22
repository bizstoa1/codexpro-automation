<!-- BEGIN CODEX WEB GPT SUBAGENT POLICY -->
## Codex native subagent policy

- The primary commander uses GPT-5.6 Sol at high reasoning. Default subagents use GPT-5.6 Terra at medium reasoning; role files may narrow this further.
- Use subagents actively when the user, applicable repository rules, or a selected skill asks for delegation and the work is independently bounded.
- Do not blanket-fan-out. Start with no more than two concurrent workers in normal operation; the global hard cap is three spawned threads.
- Prefer `scout` for narrow repetitive read-only discovery, `implementer` only when the parent supplies an explicit non-overlapping file list, and `verifier` for independent read-only validation.
- Never assign overlapping write ownership. The primary agent integrates results and remains responsible for final deterministic verification.
- Keep `multi_agent_v2` disabled while it is unstable; the supported `[agents]` settings and standalone role files are sufficient.

## Filesystem hygiene

- Never create test output, temporary directories, logs, downloaded archives, or dependency checkouts directly under a drive root such as `C:\` or `D:\`.
- Use the operating-system temp directory under a task-specific `Codex` child first. If Windows path length requires a shorter location, use the active repository's gitignored `.codex-tmp\<task>` directory, never `D:\pytest-*` or another drive-root scratch path.
- Put reusable third-party source checkouts under `%LOCALAPPDATA%\Codex\Sources`. Keep explicit user project roots separate and never repurpose them as scratch space.
- Before cleanup, verify ownership and active references. Preserve user projects, system folders, credentials, and ambiguous items; move confirmed automation artifacts to a recoverable archive instead of deleting them.

## Oracle long-run observation

- Treat 80 minutes as a caution/status-audit threshold, never as a forced stop, failure, handoff, ownership release, or replacement-submission deadline.
- At the threshold inspect the exact run's process liveness, response/log/output progress, known conversation binding, and provider terminal evidence. If it is live, streaming, progressing, or uncertain, continue the same process or exact-slug live recovery.
- If a host observer must return, preserve the Oracle process/session and automatically continue observation through the same exact slug. Never create a fresh prompt or release the project lock because elapsed time alone.
- Only a real provider hard limit, explicit terminal evidence, an explicit user stop, or verified inability may end observation. Keep prompt-not-observed fail-closed and no-duplicate rules unchanged.

## Web GPT model and Pro authority

- Default ordinary web work to `gpt-5.6` with `extra-high`, the highest supported non-Pro reasoning tier. Never select or upgrade to Pro automatically.
- Treat Pro as quota-limited and explicit-only. Use `GPT-5.6 Sol` at the Pro effort only after the user explicitly requests Pro; a standard comprehensive workflow additionally requires `allow_pro: true`.
- New explicit Pro runs use the `pro-devspace` route. They require an exact-root
  tracked capability profile, host-bound profile SHA-256, immutable mission,
  current HEAD, clean non-protected branch, and explicit relative write paths.
  Pro may edit only those paths under the applicable `AGENTS.md` chain.
- Capability v1 grants no shell, Git mutation, push, merge, tag, deploy,
  account, ChatGPT app-setting, or external-state authority. `pro-attachment`
  remains a separate explicit immutable-evidence route, never an automatic fallback.
- Web Multi v2 is read-only all-of-N, forbids nested Multi and worktree writers,
  runs at most five provider sessions concurrently, and invokes exactly one
  merger only after every terminal lane handoff exists.
- Preserve persisted `pro-devspace-readonly` runs with their original read-only meaning during exact recovery; never reinterpret historical authority.
<!-- END CODEX WEB GPT SUBAGENT POLICY -->
