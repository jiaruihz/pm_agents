# Theta NO Data Expansion Audit v1

Status: snapshot
Generated: 2026-06-15T16:16:25.370534+00:00
Target metric: `theta_no_data_expansion_gap` = 为什么 high-ask NO carry 样本少，以及下一步该补哪一层数据。

## 数据快照

- 数据源: 本轮已运行 `scripts/ops/sync_weather_remote.sh`；`run_stack.sh` 已完成 DB rebuild，但 FE 因 5174 端口仍忙启动失败。
- fact_built_at_utc: `2026-06-15T16:15:20.000141+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30140, 'eligible': 10366, 'paper_ordered': 3968, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

可以补历史，而且已经同步到了更多 raw 数据；但当前瓶颈不是天气历史，而是“已物化到 theta carry replay 的可成交盘口历史”。

本机 raw orderbook 现在有 29 个日期目录，范围 2026-05-19 到 2026-06-16，共 1338 个 snapshot 文件。pm_history city-date 文件覆盖到 2026-06-14。但当前 `calibrated_quotes.csv` 仍只覆盖 2026-05-20 到 2026-06-09，21 个 target dates。

也就是说，数据已经拉下来了，但 NO carry 研究样本还没吃到 2026-05-19, 2026-06-10, 2026-06-11, 2026-06-12, 2026-06-13, 2026-06-14 这些既有 orderbook 又有 pm_history 的新日期。下一步最有价值的是把这些日期重新物化进 reheat-risk/v2 quote replay，而不是继续只扩天气缓存。

当前 walk-forward 的样本基线是 disciplined selector 40 行/11 天，strict high-ask 20 行/9 天。粗略说，每多物化 5 个完整 settled target days，可能只多几十个 high-ask carry quote，真正要达到稳定结论需要持续 forward 或补更早 orderbook。

## 覆盖表

| layer | coverage | why it matters |
|---|---:|---|
| raw orderbook snapshots | 29 dates / 1338 files | 可成交 best ask/size，是 ROI 的关键层 |
| pm_history city-date settlements | 42 dates / 1887 files | 决定最终 winner/payoff |
| weather cache | IEM 90 files, WU 52 files | 训练 no-reheat 物理模型 |
| calibrated theta replay | 21 dates / 10275 quote rows | 当前 NO carry 回测实际使用层 |

## Replay 缺口

- orderbook exists but replay missing: `['2026-05-19', '2026-06-10', '2026-06-11', '2026-06-12', '2026-06-13', '2026-06-14', '2026-06-15', '2026-06-16']`
- pm_history exists but replay missing: `['2026-05-05', '2026-05-06', '2026-05-07', '2026-05-08', '2026-05-09', '2026-05-10', '2026-05-11', '2026-05-12', '2026-05-13', '2026-05-14', '2026-05-15', '2026-05-16', '2026-05-17', '2026-05-18', '2026-05-19', '2026-06-10', '2026-06-11', '2026-06-12', '2026-06-13', '2026-06-14']`
- both orderbook and pm_history exist but replay missing: `['2026-05-19', '2026-06-10', '2026-06-11', '2026-06-12', '2026-06-13', '2026-06-14']`

## 建议的补全顺序

1. 先重跑/改造 reheat-risk quote materializer，让 `calibrated_quotes.csv` 吃到 2026-06-10 之后已有 orderbook + pm_history 的日期。
2. 再做 current YES / NO d1 / NO d2 ladder 的同窗 expression selector，而不是只补 NO d1。
3. 若要扩到 5 月 19 日之前，需要找 N100 备份或外部历史盘口；只有天气/settlement 没有盘口时，只能训练 no-reheat，不能算 executable ROI。
4. 从现在开始可加 zero-notional forward telemetry：每天记录 high-ask carry + current YES sibling quotes，用于累积最干净的前瞻样本。

## 三道门 verdict

本报告是数据覆盖审计，不给 live 动作。结论是：当前 no-live 不是因为物理逻辑缺，而是 executable replay 样本少；数据补全应优先补 quote materialization 和 forward telemetry。

## 输出文件

- JSON: `docs/analysis/2026-06/2026-06-16-theta-no-data-expansion-audit-v1.json`
- generated CSV dir: `docs/analysis/2026-06/generated/theta_no_data_expansion_audit_v1`
- Script: `scripts/analysis/reheat_risk/research_theta_no_data_expansion_audit_v1.py`
