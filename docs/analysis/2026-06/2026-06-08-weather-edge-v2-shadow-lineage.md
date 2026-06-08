# Weather Edge V2 Shadow Lineage - 2026-06-08

## 数据快照

- 数据源: `runtime/weather.db` (`fact_signal_candidates`, `fact_trades` for live reference)
- generated_at_utc: `2026-06-08T01:28:11+00:00`
- DB mtime BJ: `2026-06-08T01:03:02+08:00`
- MAX fact_built_at_utc: `2026-06-07T17:02:43.560681+00:00`
- CLOB gate: `gate_pass=True`; live_real fill_id DB `1320`, raw CLOB `1320`
- operational base: exclude `Ankara, BuenosAires, Jeddah, Karachi, Moscow, Munich`; `decision_hours_to_settle <= 28`
- lineage records: `6012`; city-days: `668`

## Target Metric

`weather_edge_v2_shadow_lineage` = 对每个 operational-base city-day 输出每个候选 rule 的 selected/rejected legs、market distribution、EV、CVaR20、leave-best-out EV、worst-case payoff、actual settled PnL、以及相对 current-like `legacy_side_band_raw` 的 missed/avoided attribution。

这不是 live 下单记录；它是用于 forward settled shadow 的 lineage artifact。

## Current Live Reference

- recent live_real operational-base ROI: `-8.67%`; top5 ROI `-15.59%`; fills `351`。

## Rule Summary

| rule | active city-days | n | pnl | ROI | top5 ROI | missed | avoided |
|---|---:|---:|---:|---:|---:|---:|---:|
| legacy_side_band_raw | 325 | 393 | $+197.27 | +10.04% | +5.61% | $+0.00 | $+0.00 |
| combo_market_risk_entry | 153 | 162 | $+173.42 | +22.06% | +4.19% | $+761.76 | $+705.00 |
| combo_market_tail_entry | 45 | 53 | $+31.47 | +14.70% | -19.79% | $+1008.42 | $+845.00 |
| robust_single_best_entry | 44 | 44 | $+17.95 | +10.14% | -38.61% | $+1012.33 | $+840.00 |
| tail_balanced_combo_entry | 51 | 59 | $+34.44 | +12.90% | -25.13% | $+975.49 | $+820.00 |
| no_hit_guard_combo_entry | 50 | 56 | $+31.56 | +12.73% | -28.46% | $+985.29 | $+825.00 |
| compact_diversified_combo_entry | 46 | 48 | $+27.78 | +13.62% | -27.76% | $+998.18 | $+835.00 |
| live_trigger_tail_combo | 20 | 24 | $+127.09 | +67.96% | +5.35% | $+923.79 | $+810.00 |
| live_trigger_no_hit_guard_combo | 20 | 23 | $+119.09 | +66.53% | -0.06% | $+928.79 | $+810.00 |

## Highest-Risk City-Day Records

| city | date | rule | n | actual pnl | EV | CVaR20 | leave-best EV | worst |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Atlanta | 2026-05-25 | combo_market_risk_entry | 2 | $+8.24 | $+6.59 | $-16.00 | $+0.87 | $-16.00 |
| Atlanta | 2026-05-25 | combo_market_tail_entry | 2 | $+8.24 | $+6.59 | $-16.00 | $+0.87 | $-16.00 |
| Atlanta | 2026-05-25 | tail_balanced_combo_entry | 2 | $+8.24 | $+6.59 | $-16.00 | $+0.87 | $-16.00 |
| Atlanta | 2026-05-25 | no_hit_guard_combo_entry | 2 | $+8.24 | $+6.59 | $-16.00 | $+0.87 | $-16.00 |
| Atlanta | 2026-05-25 | live_trigger_tail_combo | 2 | $+8.24 | $+6.59 | $-16.00 | $+0.87 | $-16.00 |
| Atlanta | 2026-05-25 | live_trigger_no_hit_guard_combo | 2 | $+8.24 | $+6.59 | $-16.00 | $+0.87 | $-16.00 |
| Atlanta | 2026-05-26 | combo_market_risk_entry | 2 | $+12.07 | $+2.90 | $-16.00 | $-1.65 | $-16.00 |
| Atlanta | 2026-05-26 | combo_market_tail_entry | 2 | $+12.07 | $+2.90 | $-16.00 | $-1.65 | $-16.00 |
| Atlanta | 2026-05-26 | tail_balanced_combo_entry | 2 | $+12.07 | $+2.90 | $-16.00 | $-1.65 | $-16.00 |
| Atlanta | 2026-05-26 | no_hit_guard_combo_entry | 2 | $+12.07 | $+2.90 | $-16.00 | $-1.65 | $-16.00 |
| Atlanta | 2026-05-26 | live_trigger_tail_combo | 2 | $+12.07 | $+2.90 | $-16.00 | $-1.65 | $-16.00 |
| Atlanta | 2026-05-26 | live_trigger_no_hit_guard_combo | 2 | $+12.07 | $+2.90 | $-16.00 | $-1.65 | $-16.00 |
| Atlanta | 2026-05-27 | combo_market_risk_entry | 2 | $-11.00 | $+2.10 | $-11.00 | $-4.68 | $-11.00 |
| Atlanta | 2026-05-27 | combo_market_tail_entry | 2 | $-11.00 | $+2.10 | $-11.00 | $-4.68 | $-11.00 |
| Atlanta | 2026-05-27 | tail_balanced_combo_entry | 2 | $-11.00 | $+2.10 | $-11.00 | $-4.68 | $-11.00 |
| Atlanta | 2026-05-27 | no_hit_guard_combo_entry | 2 | $-11.00 | $+2.10 | $-11.00 | $-4.68 | $-11.00 |
| Amsterdam | 2026-05-14 | legacy_side_band_raw | 2 | $-0.48 | $-0.01 | $-10.00 | $-6.17 | $-10.00 |
| Amsterdam | 2026-05-23 | legacy_side_band_raw | 2 | $-1.74 | $+0.80 | $-10.00 | $-10.00 | $-10.00 |
| Amsterdam | 2026-06-06 | legacy_side_band_raw | 2 | $-1.67 | $+0.75 | $-10.00 | $-10.00 | $-10.00 |
| Atlanta | 2026-05-25 | legacy_side_band_raw | 2 | $+5.15 | $+4.12 | $-10.00 | $+0.55 | $-10.00 |

## 使用方式

- 人看摘要: 本 Markdown。
- 程序/后续 forward settled: `/home/rui/projects/pm_agent/docs/analysis/2026-06/2026-06-08-weather-edge-v2-shadow-lineage.jsonl`。
- 看板展示应挂在 Weather Research，而不是 Strategies/Live 绩效卡；当前仍是 shadow/offline evidence。

## 结论

- 可以开始记录 shadow lineage；不要 canary。
- 下一步 forward settled 时，把新增 settled city-day append 到同一 lineage schema，再看 recent/holdout/top5-removed 是否同时改善。
