# Tmax Distribution P6 Shadow Telemetry v1

> generated_at_utc: `2026-07-05T14:16:38+00:00`
> Scope: zero-notional shadow telemetry pack; no live runner/order behavior changed.

## 结论

- P6 没有改模型，也没有改 live；它把 P5 的表达选择结果整理成未来 shadow runner 应该写出的事件格式。
- 每个 config x city-date-hour 都保留一行，但主 `selected` 口径是 live-like：每个 config x scope x city-day 只取第一条 edge-pass 机会。
- 同一 city-day 后续再次触发的小时信号不会丢，标成 `blocked/city_day_after_first_selected`，用于复盘“如果重复买会怎样”。

## Candidate Configs

| config | method | threshold | role |
|---|---|---:|---|
| tmax_dist_clean_edge02 | loo_no_city_source_blend | 0.02 | clean_mechanism_primary |
| tmax_dist_city_source_edge02 | mkt_city_source_blend | 0.02 | capacity_and_city_source_comparison |
| tmax_dist_clean_edge10 | loo_no_city_source_blend | 0.10 | dev_cv_high_edge_pressure_test |

## Forward Summary

| scope | shadow_config_id | candidate_rows | selected_rows | blocked_rows | dates | cities | avg_ask | avg_edge | win_rate | cost | pnl | roi | roi_ci_low | roi_ci_high | daily_win_rate | current_yes_rows | current_no_rows | d1_no_rows | d2_no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | tmax_dist_city_source_edge02 | 1729 | 236 | 1493 | 12 | 36 | 0.491 | 0.052 | 54.7% | 115.946 | 13.054 | +11.3% | -1.7% | +24.3% | 75.0% | 44 | 54 | 71 | 67 |
| verified_forward | tmax_dist_clean_edge02 | 1729 | 226 | 1503 | 12 | 36 | 0.465 | 0.056 | 51.3% | 104.981 | 11.019 | +10.5% | +0.8% | +20.0% | 66.7% | 41 | 61 | 63 | 61 |
| verified_forward | tmax_dist_clean_edge10 | 1729 | 33 | 1696 | 10 | 18 | 0.248 | 0.215 | 33.3% | 8.193 | 2.807 | +34.3% | -18.7% | +92.6% | 80.0% | 4 | 7 | 22 | 0 |

## Event Schema

Future live/shadow runner should write these fields per cycle. This report backfills them from P5 opportunities for validation.

| field | meaning |
|---|---|
| `shadow_event_id` | deterministic id for config x state x chosen expression x selection policy |
| `shadow_schema_version` | event schema version |
| `shadow_config_id` | candidate policy identity |
| `selection_policy` | first eligible per config x scope x city-day |
| `zero_notional` | always true for P6 |
| `no_order_placed` | always true for P6 |
| `selection_status` | selected or blocked |
| `selection_reason` | edge_pass_first_city_day, city_day_after_first_selected, or below_edge_threshold |
| `edge_threshold` | config threshold |
| `city_day_eligible_rank` | 1 for the first edge-pass row in a city-day; later edge-pass rows are blocked telemetry |
| `scope` | dev_cv / verified_forward / extension_forward |
| `city` | market city |
| `target_date` | weather contract date |
| `decision_hour_local` | local hour from state row |
| `method` | probability model method |
| `chosen_expression` | best expression by model_edge |
| `ask` | observed ask |
| `p_win` | model probability expression wins |
| `model_edge` | p_win - ask |
| `actual_bucket` | settlement bucket when available |
| `label_source` | settlement_outcomes or observed_max_derived |
| `day_regime` | atlas day regime |
| `intraday_state` | atlas intraday state |
| `running_max_state` | atlas running max state |

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_summary.csv`
- `docs/analysis/2026-07/2026-07-03-tmax-distribution-p6-shadow-telemetry-v1.json`

## Verdict

conclusion=`shadow_telemetry_contract_ready`; live_action=`none`.
