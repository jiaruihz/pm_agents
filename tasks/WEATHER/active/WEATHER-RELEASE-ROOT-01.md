# WEATHER-RELEASE-ROOT-01

TASK_ID: WEATHER-RELEASE-ROOT-01
PROJECT_ID: WEATHER
WORKSTREAM: RELEASE-ROOT
STATUS: READY_FOR_REVIEW
ROLE: COORDINATOR
REPO_ROOT: /Users/deepsleep/projects/pm_agents
BASE_COMMIT: 924b5e059f027baf6709e0f6cc99f71fb5d3ebc4
OWNER: owner
CREATED_AT: 2026-08-27T22:40:00+08:00
HANDOFF_PATH: tasks/WEATHER/handoffs/WEATHER-RELEASE-ROOT-01.md
STARTED_AT: 2026-08-27T22:40:00+08:00
COMPLETED_AT: 2026-08-27T23:35:00+08:00

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

## Completion evidence

- 23/23 release checkout 位于统一 managed root，HEAD 全部匹配，Git common dir 均为 operational repo；旧 `/Users/deepsleep/projects/pm_agents*` release checkout 为 0。
- 31 个 release-backed desired-running runtime 已通过 controller 重启到新路径；JRS keeper 未迁移，2 个 dispute runtime 按 owner state 保持 paused。
- strict manifest、controller health、storage identity 均为 `healthy`，findings/critical runtimes/pre-change missing sessions 均为 0；focused tests `99 passed`；task protocol `PASS: 3 projects; 1 tasks`。
- fill gate PASS：1,567 fills / 1,116 executions，DB/cache/fact delta 均为 0；迁移前后 authenticated balance 均为 `162513747`，32 个 open order payload 完全相同。
- 唯一 legacy feature-store 数据已逐树 hash 验证并保存至 `/Volumes/jrs-archive/pm_agents/research/artifact_store/release_root_legacy/20260827/`；manifest 记录 7,404 files、2,337,579,262 bytes。
- 迁移中 zero-byte split DB 与 release-relative fill-cache 两个根因均已修正并写入 incident ledger；量化影响为 extra/missed orders `0/0`、fills `0`、canonical pollution `0`。
- 独立只读 reviewer 提出的 WCIR/KNMI fallback 与 managed-root stale SHA 扫描缺口均已修复。

Handoff: `tasks/WEATHER/handoffs/WEATHER-RELEASE-ROOT-01.md`
