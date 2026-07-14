# Source-Event Expression-Specific Denominator v3

> 2026-07-14; research-only; corrects the full-ladder d2 requirement inherited by v1/v2.

## 数据快照

- canonical settlement: `2026-05-04..2026-07-12` / `70` dates / `49` cities.
- raw cache: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/raw_decision_groups.jsonl`; PIT observation events: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2/embedded_observation_events.csv`.
- source timing PIT log: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/source_orderbook_timing/sources.jsonl`; combined unique observations `26366`.
- strategy grain: every saved decision snapshot; quote must be from the same snapshot and observation must be first-seen by decision time.

## 纠错结论

`current 上方不足两档`不应排除 current YES/NO。那是 full-ladder current+d1+d2 概率模型的结构要求，不是 current token 的交易要求。
本轮改成 expression-specific：current 只要求 current bracket；d1 只要求 current+d1；不存在 d1 时仍保留 current state。
settlement winner 直接从 canonical `settlement_outcomes` 补，不要求 winner 必须出现在当时保存的 forward ladder；它只作为事后 label，不进入信号。
running max 是决策时刻已经 first-seen 的当日最高观测。优先从 embedded observation history PIT 重建；没有历史时才使用快照当时已保存的 `metar_current_max_f`；两者都没有就不能无泄漏补。

## Expression-specific funnel

| stage | rows |
| --- | --- |
| raw_decision_groups | 380576 |
| canonical_settlement_available | 380386 |
| missing_pit_running_max | 274858 |
| running_from_embedded_history | 105468 |
| running_outside_saved_ladder | 59542 |
| current_anchor_mapped | 45986 |
| d1_rung_absent_but_current_retained | 835 |
| running_from_saved_snapshot | 60 |
| missing_canonical_settlement | 190 |
| deduplicated_expression_states | 26383 |
| generic_cross_events | 1075 |

## Settlement coverage

- raw candidate city-days `2956`; canonical settled `2954`; missing `2`.
- missing by target_date: `{"2026-05-17": 1, "2026-07-09": 1}`.

## 全部 generic cross 结果

| expression | rows | dates | cities | avg_ask | win_rate | roi | roi_ci_low | roi_ci_high | date_equal_excess_roi | excess_ci_low | excess_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_yes | 426 | 25 | 40 | 0.3202 | 0.2958 | -0.0889 | -0.1732 | 0.0157 | -0.0775 | -0.2194 | 0.0399 |
| current_no | 366 | 22 | 39 | 0.7267 | 0.7049 | -0.0353 | -0.0919 | 0.0078 | -0.0219 | -0.0727 | 0.0218 |
| d1_yes | 297 | 16 | 39 | 0.2775 | 0.2189 | -0.2318 | -0.3028 | -0.1564 | -0.0172 | -0.2247 | 0.1660 |
| d1_no | 417 | 25 | 38 | 0.7737 | 0.7626 | -0.0226 | -0.0419 | -0.0016 | 0.0058 | -0.0229 | 0.0357 |

## Three Gates

- significance=FAIL; baseline=FAIL; forward=FAIL; conclusion=inconclusive.
- 本轮只修正分母，不批准 live；真正 fast-source event 仍需独立 forward。
