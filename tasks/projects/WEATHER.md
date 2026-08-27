# WEATHER

PROJECT_ID: WEATHER
TITLE: Weather strategy research, runtime and execution system
REPO_ROOT: /Users/deepsleep/projects/pm_agents
SOURCE_STATE_BASE_COMMIT: 673767a1
PROTOCOL_INSTALL_COMMIT: 0f18eb409466b1af13cb44690fa06b004ec63197
CURRENT_BRANCH_AT_RECONCILIATION: codex/market-ladder-kink-v1
LAST_RECONCILED_AT: 2026-08-27T22:01:38+08:00
ACTIVE_TASKS: WEATHER-RELEASE-ROOT-01

## Current state

CURRENT_DISPOSITION: ACTIVE_MAINLINE
CURRENT_ACCEPTED_STAGE: DYNAMIC_BY_WORKSTREAM
LAST_OWNER_DECISION: SEE_CANONICAL_POINTERS

天气温度策略是当前仓库主线。研究状态不能代替生产状态：研究接受状态以 registry/freeze evidence 为准，生产状态必须动态核对 manifest、process、raw runtime 和 exchange evidence。

## Capability boundary

- NEXT_ALLOWED_ACTION: 由具体 workstream task 与适用 weather skill 决定。
- EXPLICITLY_NOT_AUTHORIZED_BY_THIS_FILE: live/order/deploy/transfer/risk-limit 变更。
- 任何生产动作仍须按根 `AGENTS.md`、对应 skill 和 production manifest 执行。

## Canonical pointers

- `docs/WEATHER_STRATEGY_REGISTRY.md`
- `docs/WEATHER_DOCS_INDEX.md`
- `docs/WEATHER_STRATEGY_ENTRYPOINT.md`
- `src/strategies/runtime/production.yaml`
