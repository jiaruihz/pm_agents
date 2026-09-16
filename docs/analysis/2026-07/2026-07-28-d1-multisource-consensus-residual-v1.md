# D-1 多模型 consensus residual：严格 PIT 单边 NO v1

## 数据快照

- 数据源：`/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/executable_baskets.csv` + `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_extreme_no_multisource_policy_v7/strict_pit_coverage_rows.csv` + frozen policy。
- 生成时间：2026-07-28T03:49:58.413768+00:00。
- 记录行数：23 baskets；settled=23；unsettled=0；missing_bracket=0。
- unit：basket/city-day；`trade_class=research_replay`，不是 actual fill。

## 结论

这次真正把 17 个模型用于交易表达：每城各模型先加 frozen rolling bias，取 corrected median；assigned ECMWF/GFS 偏热时选最高端 NO，偏冷时选最低端 NO。

| expression | rows / dates | wins | cost | PnL | fee-adjusted ROI (95% CI) |
|---|---:|---:|---:|---:|---:|
| consensus_selected_no | 23 / 2 | 23 | $22.9601 | $+0.0399 | +0.17% [n/a, n/a] |
| opposite_endpoint_no | 23 / 2 | 23 | $22.9648 | $+0.0352 | +0.15% [n/a, n/a] |
| mechanical_half_each | 23 / 2 | 23 | $22.9625 | $+0.0375 | +0.16% [n/a, n/a] |

- selected 相对 opposite 的 ROI delta：+0.02% [n/a, n/a]。
- 严格 PIT 历史只有 23 baskets / 2 target dates；少于3日，date-block CI 无法估计。
- 因此点估只用于验证实现与方向，不能作为 alpha 或 live 证据。

## 策略定义

```text
corrected_model = model_forecast + frozen_city_model_bias
consensus = median(corrected_models)
delta = assigned_corrected - consensus
delta > 0 -> BUY high endpoint NO
delta < 0 -> BUY low endpoint NO
cost = direct NO ask + 0.05 * ask * (1-ask)
```

- bias training cutoff：2026-07-07。
- spread 和 `abs(delta)/IQR` 全量记录，但不做事后阈值筛选。
- baseline 1：同 city-date 的相反端 NO；baseline 2：两端各半。

## Signal / Evidence Funnel

- signal：4,086 executable baskets → 23 strict-PIT coverage → 23 assigned+consensus 可评分 → 23 单边选择。
- evidence：23 direct asks → 23 settlements → 23 hypothetical taker expressions → 0 actual fills。

## 八环与 Gate

- 已覆盖：描述性绩效、信号方向、官方 fee、同分母反事实。
- 未覆盖：有效统计推断、概率 calibration、容量、组合相关性、frozen forward。
- significance=NA；baseline=NA；forward=NA；conclusion=inconclusive。
- 动作：继续 zero-notional forward shadow，不改 live。

## 产物

- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_multisource_consensus_residual_v1/scored_rows.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_multisource_consensus_residual_v1/summary.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_multisource_consensus_residual_v1/daily_curve.csv`
- `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/d1_multisource_consensus_residual_v1/cumulative_pnl.png`
