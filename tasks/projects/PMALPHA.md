# PMALPHA

PROJECT_ID: PMALPHA
TITLE: Polymarket alpha research and orchestration platform
REPO_ROOT: /Users/deepsleep/projects/pm_agents
SOURCE_STATE_BASE_COMMIT: 673767a1
PROTOCOL_INSTALL_COMMIT: 0f18eb409466b1af13cb44690fa06b004ec63197
LAST_RECONCILED_AT: 2026-08-27T22:01:38+08:00
ACTIVE_TASKS: NONE

## Current state

CURRENT_DISPOSITION: UNKNOWN_REQUIRES_RECONCILIATION
CURRENT_CANDIDATE_STAGE: P1 automation work visible in dirty checkout
CURRENT_ACCEPTED_STAGE: UNKNOWN_REQUIRES_OWNER_CONFIRMATION
LAST_OWNER_DECISION: UNKNOWN_REQUIRES_OWNER_CONFIRMATION

当前 checkout 存在 `src/polymarket_alpha`、tests 和 artifact 相关的未提交工作。本页不推断其执行者、完成度或 accepted stage，也不把工作树状态当作 owner decision。

## Capability boundary

- NEXT_ALLOWED_ACTION: 由原 write owner 收口当前 diff、tests、handoff 与 owner decision 后再更新本页。
- EXPLICITLY_NOT_AUTHORIZED: 未经 task/owner 明确批准的 runtime、order、live 或 production promotion。

## Canonical pointers

- `docs/design/polymarket_alpha_p0/`
- `src/polymarket_alpha/`
- `tests/polymarket_alpha/`
