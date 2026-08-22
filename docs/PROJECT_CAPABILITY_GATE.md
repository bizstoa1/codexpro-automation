# Project capability gate

This is the authoritative operating contract for mission-scoped Pro writes,
regular comprehensive handoffs, and Oracle Web Multi v2.

## Authority layers

Authority is the intersection of four independent layers. A broader value in
one layer never overrides a narrower value in another.

1. DevSpace `allowedRoots` contains the exact canonical project root.
2. The project tracks `.codex/project-capabilities.json`.
3. The host policy binds that exact root and the exact profile SHA-256.
4. A live run holds one signed, durable, exact-root capability lease.

Pro additionally requires a mission-authority document bound to the exact
mission bytes, current Git HEAD, and requested relative write paths. The
DevSpace compatibility guard revalidates the active lease on every tool call.
Before the first write, the same subject must successfully read the exact
mission and every applicable root-to-write-path `AGENTS.md`. The guard records
those successful reads in memory and re-hashes each file immediately before
every write; a missing attestation or changed byte fails closed. Raw subject
tokens and read attestations are never persisted.

## Project profile

Track this shape in each admitted repository and choose a narrow
`write_root_ceiling` for that project:

```json
{
  "schema": "codex.chatgpt.project-capability-profile/v1",
  "pro": {
    "enabled": true,
    "write_root_ceiling": ["src", "tests", "docs"],
    "commands": "none",
    "require_clean_git": true,
    "require_nonprotected_branch": true
  },
  "web_multi": {
    "enabled": true,
    "access": "read-only",
    "min_lanes": 2,
    "max_lanes": 25,
    "max_concurrency": 5,
    "all_lanes_required": true,
    "merger_policy": "exactly-one",
    "nesting": "forbidden"
  },
  "protected_branches": ["main", "master", "production"],
  "write_deny_paths": [
    ".git",
    ".codex",
    ".ai-bridge",
    ".github",
    "AGENTS.md",
    ".env",
    "credentials",
    "secrets"
  ],
  "external_actions": "deny"
}
```

The profile is a ceiling, not a grant. Pro remains unavailable on a protected
branch, with a dirty Git baseline, without explicit user selection, or when the
host policy has not enabled Pro for that exact root.

## Host admission

Preview the host policy before installing it. `--enable-pro-root` values must
be an exact subset of `--project-root` values. Roots omitted from
`--enable-pro-root` still receive regular read/control-handoff and Web Multi
admission, but not Pro.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_capability_policy.py" configure `
  --state-root "$env:USERPROFILE\.codex\state\chatgpt-capabilities" `
  --project-root C:\projects\one `
  --project-root C:\projects\two `
  --enable-pro-root C:\projects\two `
  --max-web-multi-concurrency 5 `
  --dry-run
```

Run the identical command without `--dry-run` only after reviewing every root,
profile hash, and Pro boolean. Changing a tracked profile invalidates the host
binding until this qualification is deliberately repeated. Do not hand-edit
the generated host policy.

## Pro mission authority

The explicit Pro caller creates one document for one immutable mission and
current HEAD. Paths are project-relative and must be below the profile ceiling.

```json
{
  "schema": "codex.chatgpt.pro-mission-authority/v1",
  "project_root": "C:\\projects\\two",
  "mission_path": "C:\\projects\\two\\.ai-bridge\\mission.md",
  "mission_sha256": "<64 lowercase hex>",
  "expected_head": "<current Git HEAD>",
  "allowed_write_paths": ["src/feature", "tests/feature"],
  "allowed_command_ids": [],
  "external_actions": "deny"
}
```

Pass it with `chatgpt_oracle_dispatch.py --mode pro
--mission-authority <AUTHORITY>`. The dispatcher never derives Pro from task
difficulty and never widens these paths. Capability v1 exposes no shell, Git,
push, merge, tag, deploy, account, application-setting, or external-action
authority. Capability manifests always use the canonical host-only Oracle run
namespace; a caller-supplied `run_root` is rejected during manifest parsing.

Terminal success releases the lease only after Git postflight proves unchanged
HEAD, branch, index, protected refs, and no changed path outside the authority.
An escape quarantines the lease and preserves exact-project ownership.

## Comprehensive control write

Regular comprehensive stages do not receive project-source write authority.
They may write only the exact stage handoff directory containing their output,
receipt, and next mission. The augmented mission, profile, `AGENTS.md`, Git
metadata, commands, and every other project path remain protected. An explicit
Pro stage uses the workflow manifest's `pro_write_paths` to create a fresh
mission authority for that exact stage.

## Web Multi v2

Use schema `codex.chatgpt.oracle-multi/v2`. Every solver has
`access: read-only`; nested Multi, write worktrees, partial-success merger, and
more than five concurrent provider sessions are invalid.

```json
{
  "schema": "codex.chatgpt.oracle-multi/v2",
  "project_root": "C:\\projects\\one",
  "output_dir": "C:\\projects\\one\\.ai-bridge\\multi-run",
  "max_concurrency": 2,
  "completion_policy": "all-lanes",
  "merger_policy": "exactly-one",
  "nesting": "forbidden",
  "solvers": [
    {"id": "architecture", "mission_path": "C:\\projects\\one\\missions\\architecture.md", "access": "read-only"},
    {"id": "security", "mission_path": "C:\\projects\\one\\missions\\security.md", "access": "read-only"}
  ],
  "merger_mission_path": "C:\\projects\\one\\missions\\merge.md"
}
```

Dry-run compiles the profile, host admission, topology, Git baseline, and
prompt binding without creating a lease, output directory, browser, session,
or replacement workflow. Live execution writes a schema-valid durable result
ledger before lane submission and after each transition. Live lanes receive
independent signed subjects and cannot read host handoffs. Exactly one merger
subject may read the stable handoff list after every lane is terminal and
nonempty. Any uncertain lane or merger transitions the ledger to
`attention_required`, retains the lease, and forbids a replacement submission.

## Recovery

An uncertain post-submit run retains the same lease and project owner. Recovery
may only observe or harvest the recorded exact Oracle slug. A proven
pre-submit failure may abort its lease before one identity-preserving retry.
Web Multi recovery never creates a replacement lane or second merger.
