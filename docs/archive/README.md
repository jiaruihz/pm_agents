# 历史资产索引

Status: `historical-index`
Source of truth: no
Current authority: `../PROJECT_STRUCTURE.md`, `../WEATHER_DOCS_INDEX.md`, and domain living docs

本目录保存已经退出当前入口、但仍有审计或复现价值的设计、执行记录和研究证据。
目录位置本身即表示“历史”：文件中的 host、路径、PID、启动命令、live 标签、参数和
PnL 数字都不能作为当前操作依据。当前生产事实必须回到 manifest、controller、raw
runtime 和 exchange evidence。

## 顶层文档归属

| 历史家族 | 文件 | 当前替代入口 | 处理结论 |
|---|---|---|---|
| PMM 架构与重构 | `CODE_REVIEW.md`, `REFACTOR_PLAN.md`, `EXECUTION_PLAN_REALDATA_MULTI_LEVEL.md`, `PMM_PYTHON_PLAN.md`, `PMM_JAVA_PLAN.md` | `../pmm/README.md`, `../pmm/ARCHITECTURE.md` | 同一演进链，保留审计；不再各自承担 roadmap |
| PMM skill 与路由 | `SKILL_ARCHITECTURE_PLAN.md`, `STRATEGY_KEY_ROUTING_PLAN.md` | `../../skills/catalog.yaml`, `../PROJECT_STRUCTURE.md` | 方案已被当前 skill/catalog 合同取代 |
| PMM/ARB 策略说明 | `PM_ARB_STRATEGY.md`, `PM_ARB_STRATEGY_CN.md`, `WEATHER_THETA_NO_PROGRESS.md` | `../../src/strategies/README.md`, `../WEATHER_STRATEGY_REGISTRY.md` | 中英文是翻译对，不按重复删除；旧 weather runner 命令不得执行 |
| Polymarket research platform | `POLYMARKET_RESEARCH_ARCHITECTURE.md`, `POLYMARKET_RESEARCH_CAPABILITIES.md`, `POLYMARKET_RESEARCH_IMPLEMENTATION_PLAN.md`, `POLYMARKET_RESEARCH_REFACTOR_LOG.md`, `RESEARCH_INTEGRATION.md` | `../RESEARCH_KNOWLEDGE_SYSTEM.md` and current domain skills | 同一阶段链；仅保留历史接口与决策证据 |
| Unified strategy/dashboard | `UNIFIED_STRATEGY_PLATFORM_REFACTOR_PLAN.md`, `UNIFIED_STRATEGY_PLATFORM_EXEC_LOG_2026-03-05.md`, `STRATEGY_DASHBOARD_PLAN.md`, `MONITORING_DASHBOARD_DESIGN.md`, `architecture_refactoring_plan.md` | `../WEATHER_ARCHITECTURE_SPINE.md`, `../WEATHER_DASHBOARD.md` | 旧 DB-first/platform 提案，不是当前控制面 |
| Weather dashboard lineage | `WEATHER_DASHBOARD_LINEAGE_REFACTOR_PLAN.md`, `WEATHER_DASHBOARD_CANONICAL_FINAL_STATE_PLAN.md` | `../WEATHER_SYSTEM_CONTRACT.md`, `../WEATHER_DASHBOARD.md` | 同一迁移链，实施历史保留；字段口径以当前合同为准 |
| Weather execution | `WEATHER_EXECUTION_CLEANUP_PLAN.md` | `../WEATHER_EXECUTION_ARCHITECTURE.md` | 旧 N100/早期 policy 计划，不是部署 runbook |

`analysis/2026-05` 与 `analysis/2026-06` 保存早期 weather 研究快照及配套机器证据。
耐久结论已经由 `../analysis/*.md` living docs 按 model、execution、timing、side、city、
sizing、live performance 和 data integrity 分工吸收；这里的旧数字不得重新抬升为当前结论。
`../dev_logs/` 的三份 2026-06 Weather Edge Engine PR 日志同样只是实现时点快照；保留原路径
是为了已有相对链接，不把它们当成第二份 current-state 文档。

## 已确认的合并候选

以下文件没有仓外消费者，且主题已被当前合同覆盖；物理删除仍需单独授权，并须在删除前
完成最终 link/consumer audit：

- `architecture_refactoring_plan.md`
- `STRATEGY_DASHBOARD_PLAN.md`
- `WEATHER_DASHBOARD_LINEAGE_REFACTOR_PLAN.md`
- `WEATHER_DASHBOARD_CANONICAL_FINAL_STATE_PLAN.md`
- `WEATHER_EXECUTION_CLEANUP_PLAN.md`

另外，仓库根目录 `FORECAST_TAIL_EXECUTABLE_VERSIONS_2026-07-03.md` 与若干带日期的
`docs/WEATHER_*_2026-05/06*.md` 已是历史 snapshot，下一批应迁入对应年月的 archive；
迁移前要统一更新相对链接，避免用移动文件制造断链。

## 保留与删除规则

- `inconclusive`, dormant 和失败实验都不等于可以删除；它们可能承担负证据或 lineage。
- 翻译版本、不同 denominator、不同数据合同和独立 immutable evidence 不是重复文件。
- 只有 owner 已吸收耐久结论、消费者为零、输入/producer 可复现且用户明确授权时才物理删除。
- 删除或迁移后必须跑结构检查、Markdown link 检查和相关测试；Git 历史不是 active consumer 的替代品。
