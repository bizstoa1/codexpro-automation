# Legacy goal-supervisor architecture

This document name is preserved for compatibility. The former
CodexPro/agbrowse goal-supervisor submission path is frozen and cannot start
new work.

New comprehensive work uses the Oracle comprehensive workflow. Regular stages
default to the highest supported non-Pro tier through DevSpace; an optional Pro
stage requires explicit opt-in and uses read/write DevSpace. `pro-attachment`
is the separate explicit immutable-evidence route. Existing persisted legacy
state may still be recovered by its exact original runner.

See [GLOBAL_CHATGPT_ROUTING.md](GLOBAL_CHATGPT_ROUTING.md).

The exact frozen inventory and its boundary are listed in
[FROZEN_LEGACY.md](FROZEN_LEGACY.md).
