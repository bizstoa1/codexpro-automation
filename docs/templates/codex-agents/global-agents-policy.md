<!-- BEGIN CODEX WEB GPT SUBAGENT POLICY -->
## Codex native subagent policy

- The primary commander uses GPT-5.6 Sol at high reasoning. Default subagents use GPT-5.6 Terra at medium reasoning; role files may narrow this further.
- Use subagents actively when the user, applicable repository rules, or a selected skill asks for delegation and the work is independently bounded.
- Do not blanket-fan-out. Start with no more than two concurrent workers in normal operation; the global hard cap is three spawned threads.
- Prefer `scout` for narrow repetitive read-only discovery, `implementer` only when the parent supplies an explicit non-overlapping file list, and `verifier` for independent read-only validation.
- Never assign overlapping write ownership. The primary agent integrates results and remains responsible for final deterministic verification.
- Keep `multi_agent_v2` disabled while it is unstable; the supported `[agents]` settings and standalone role files are sufficient.
<!-- END CODEX WEB GPT SUBAGENT POLICY -->
