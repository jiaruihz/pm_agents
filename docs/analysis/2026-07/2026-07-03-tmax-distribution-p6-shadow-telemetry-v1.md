# Tmax Distribution P6 Shadow Telemetry v1

> generated_at_utc: `2026-07-03T16:23:57+00:00`
> Scope: zero-notional shadow telemetry pack; no live runner/order behavior changed.

## 结论

- P6 没有改模型，也没有改 live；它把 P5 的表达选择结果整理成未来 shadow runner 应该写出的事件格式。
- 每个 config x city-date-hour 都保留一行：edge 通过就是 `selected`，没通过就是 `blocked/below_edge_threshold`。
- 这样以后能同时复盘“买了会怎样”和“没买的是否应该买”，避免只看 selected 样本。

## Candidate Configs

| config | method | threshold | role |
|---|---|---:|---|
| tmax_dist_clean_edge02 | loo_no_city_source_blend | 0.02 | clean_mechanism_primary |
| tmax_dist_city_source_edge02 | mkt_city_source_blend | 0.02 | capacity_and_city_source_comparison |
| tmax_dist_clean_edge10 | loo_no_city_source_blend | 0.10 | dev_cv_high_edge_pressure_test |

## Forward Summary

| scope | shadow_config_id | candidate_rows | selected_rows | blocked_rows | dates | cities | avg_ask | avg_edge | win_rate | cost | pnl | roi | roi_ci_low | roi_ci_high | daily_win_rate | current_yes_rows | current_no_rows | d1_no_rows | d2_no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | tmax_dist_city_source_edge02 | 397 | 248 | 149 | 6 | 36 | 0.512 | 0.145 | 54.0% | 126.979 | 7.021 | +5.5% | -2.9% | +25.2% | 66.7% | 73 | 53 | 86 | 36 |
| extension_forward | tmax_dist_clean_edge02 | 397 | 208 | 189 | 6 | 36 | 0.537 | 0.128 | 55.3% | 111.637 | 3.363 | +3.0% | -17.6% | +29.0% | 66.7% | 58 | 38 | 75 | 37 |
| extension_forward | tmax_dist_clean_edge10 | 397 | 89 | 308 | 5 | 24 | 0.431 | 0.229 | 46.1% | 38.385 | 2.615 | +6.8% | -21.8% | +63.9% | 60.0% | 31 | 10 | 33 | 15 |
| verified_forward | tmax_dist_city_source_edge02 | 1114 | 593 | 521 | 8 | 36 | 0.530 | 0.105 | 59.2% | 314.064 | 36.936 | +11.8% | +5.9% | +18.3% | 87.5% | 221 | 139 | 168 | 65 |
| verified_forward | tmax_dist_clean_edge02 | 1114 | 475 | 639 | 8 | 36 | 0.530 | 0.095 | 60.2% | 251.890 | 34.110 | +13.5% | +3.4% | +23.4% | 87.5% | 174 | 118 | 138 | 45 |
| verified_forward | tmax_dist_clean_edge10 | 1114 | 129 | 985 | 8 | 33 | 0.458 | 0.215 | 68.2% | 59.031 | 28.969 | +49.1% | +35.9% | +59.2% | 87.5% | 61 | 21 | 43 | 4 |

## Event Schema

Future live/shadow runner should write these fields per cycle. This report backfills them from P5 opportunities for validation.

| field | meaning |
|---|---|
| `shadow_event_id` | deterministic id for config x state x chosen expression |
| `shadow_config_id` | candidate policy identity |
| `zero_notional` | always true for P6 |
| `no_order_placed` | always true for P6 |
| `selection_status` | selected or blocked |
| `selection_reason` | edge_pass or below_edge_threshold |
| `edge_threshold` | config threshold |
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
