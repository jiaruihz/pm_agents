# Weather Data Protocol And Collection Unification Plan（历史 tombstone）

Status: `superseded / implemented`
Source of truth: no
Current authority: `WEATHER_SYSTEM_CONTRACT.md`; `WEATHER_DATA_PIPELINE.md`; `WEATHER_REPO_BOUNDARY.md`

本文原是 2026-06 的 `weather-predict` → canonical protocol 分阶段迁移计划。计划中的主要目标已经落地：

- `EventEnvelope → DecisionContext → ModelOutput → SignalCandidate → TradeIntent → plan → order → fill → settlement`；
- opportunity 与 fill 分别进入 `fact_signal_candidates`、`fact_trades`；
- raw producer、策略消费视图、canonical DB 和 dashboard/read model 分层；
- 当前 mutable target 只有一个 owner，路径由 `production.yaml` 解析。

旧文中的 Option A/B、N100 producer、`weather-predict` 输出目录、手工 settlement refresh 和迁移 phase
不再是待执行工作，也不能用来恢复平行 producer。当前采集边界是独立 forecast owner、
`weather_market_books` raw owner 与只读 join；当前状态以 controller/manifest/raw/exchange 为准。

完整旧 coverage table、字段映射与 rollout 计划可从本文件的 git history 恢复。
