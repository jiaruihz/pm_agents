# WEATHER-RELEASE-ROOT-01

STATUS: ACTIVE
PROJECT_ID: WEATHER
ROLE: COORDINATOR/INTEGRATOR
REPO_ROOT: /Users/deepsleep/projects/pm_agents
BASE_COMMIT: 924b5e059f027baf6709e0f6cc99f71fb5d3ebc4
STARTED_AT: 2026-08-27T22:40:00+08:00

## Goal

把 `/Users/deepsleep/projects` 顶层的 23 个 weather production release checkout 迁到统一的 `/Users/deepsleep/.local/share/pm_agents/releases/<release_id>/<expected_repo_sha>`，保持 release SHA、策略参数、caps、source/signal/execution policy、JRS runtime root、订单和 fill 状态不变，并补齐可审计的 checkout lifecycle。

## Owner authorization

用户已明确授权生产维护：允许停止、迁移并恢复现有 weather managed runtimes；要求不得破坏现有链路，并避免未来再次因 release path 漂移报错。授权不包含 N100、策略参数变更、资金转移或扩大 live capability。

## Write scope

- `src/strategies/runtime/production.yaml`
- release checkout lifecycle / audit implementation under `scripts/ops/`
- focused tests under `tests/pmm_tests/`
- `docs/WEATHER_JRS_RUNTIME_INCIDENTS.md` and existing workspace inventory
- this task and matching handoff
- production release checkout paths under `/Users/deepsleep/projects/pm_agents*_prod`, `/Users/deepsleep/projects/pm_agents_knmi_recovery`, and `/Users/deepsleep/.local/share/pm_agents/releases`

## Non-goals

- 不修改 strategy eligibility、city/source policy、sizing、entry、fee、caps 或 live/shadow mode。
- 不迁移 JRS raw/canonical/runtime 数据，不触碰 N100。
- 不把 permission-host recovery 描述为 macOS TCC 根因永久消除。

## Acceptance

1. 保存 pre-change manifest、PID/session、release SHA、raw freshness、authenticated open orders/fills 与 live journal 证据。
2. JRS canonical write probe 恢复；迁移前后 canonical DB device/inode 不变。
3. 23 个 release checkout 均位于统一 release root，HEAD 与 `expected_repo_sha` 一致；Helsinki 不再保留独立 Git object pack。
4. 所有迁移前存在的 managed sessions 均恢复；desired-running missing sessions 由 controller 按合同恢复，paused dispute runtimes保持 paused。
5. 每个 live/collector/shadow 从新 checkout path 运行；raw、API、orders/fills、dedupe/maker state 连续，无 duplicate execution。
6. `/Users/deepsleep/projects` 顶层不再存在旧实体 release checkout 或遗留 compatibility symlink。
7. focused tests、manifest post-compare、storage audit、fill coverage gate 和 task protocol check 全部通过；独立 reviewer findings 已处理。

## Rollback

迁移时旧路径先保留为指向新 checkout 的 compatibility symlink；新路径或 post-check 失败时，在 writer 停止后把 checkout 移回原路径并恢复原 production spec。只有全链路验收通过后才移除旧 symlink。
