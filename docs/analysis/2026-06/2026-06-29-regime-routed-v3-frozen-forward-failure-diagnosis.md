# Regime-Routed V3 Frozen Forward Failure Diagnosis

## Conclusion

6/21+ 的失败不能归因于单纯的训练/验证日期身份，也不能靠全局 entry timing 改动直接解释。固定 6/21 前已经会选出的 live-like candidate `fixed_noon_priority + no_pullback_yes` 后，6/21-6/26 forward 仍为负；坏点主要集中在 route 机制层，尤其是 `fresh_runway_current_no` 和 `capped_d2_no`。

Verdict: `inconclusive_shadow_only`，live_ready=`False`。

## Evidence Snapshot

- Source: `docs/analysis/2026-06/generated/regime_routed_expression_router_v3_live_like_entry_v1/trade_details.csv`
- Denominator: fixed_noon_priority rows from the live-like entry sensitivity output, limited to no_pullback_yes routes and target_date <= 2026-06-26
- Settled target dates used: `2026-05-20`..`2026-06-26`

## Frozen Split At 2026-06-21

| window | rows | dates | cities | win_rate | avg_ask | ROI | weighted ROI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 245 | 32 | 35 | +53.9% | 0.534 | +7.6% | +20.3% |
| forward | 28 | 6 | 17 | +46.4% | 0.585 | -13.0% | +1.0% |

## Route Breakdown

| window | route | rows | dates | win_rate | avg_ask | ROI | weighted ROI |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | `capped_d2_no` | 76 | 29 | +63.2% | 0.614 | +5.1% | +8.5% |
| train | `cheap_stale_tail_current_no` | 7 | 7 | +42.9% | 0.315 | +51.2% | +43.5% |
| train | `false_fade_reheat_current_no` | 24 | 16 | +62.5% | 0.504 | +26.9% | +47.8% |
| train | `fresh_runway_current_no` | 138 | 31 | +47.8% | 0.507 | +3.4% | +17.2% |
| forward | `capped_d2_no` | 8 | 5 | +50.0% | 0.655 | -25.0% | -27.0% |
| forward | `cheap_stale_tail_current_no` | 2 | 2 | +50.0% | 0.320 | +138.1% | +177.2% |
| forward | `false_fade_reheat_current_no` | 4 | 4 | +50.0% | 0.623 | -16.0% | +11.8% |
| forward | `fresh_runway_current_no` | 14 | 4 | +42.9% | 0.572 | -26.8% | -26.5% |

## Feature Drift

| feature | train mean | forward mean | delta |
| --- | ---: | ---: | ---: |
| `router_ask` | 0.534 | 0.585 | +0.051 |
| `forecast_error_native` | 0.093 | 0.917 | +0.824 |
| `remaining_heat_native` | 1.118 | 0.909 | -0.210 |
| `forecast_peak_delta_hours_local` | -1.424 | -1.857 | -0.433 |
| `relative_humidity_pct` | 59.288 | 63.428 | +4.140 |
| `wind_speed_kt` | 8.880 | 9.777 | +0.898 |
| `temp_trend_1h_f` | 1.054 | 0.946 | -0.107 |
| `temp_trend_3h_f` | 4.355 | 4.304 | -0.052 |
| `minutes_since_running_max` | 57.392 | 87.173 | +29.782 |

## Forward Daily

| target_date | rows | win_rate | ROI | weighted ROI | PnL | weighted PnL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-06-21 | 8 | +37.5% | -36.0% | -28.1% | $-14.41 | $-3.00 |
| 2026-06-22 | 3 | +0.0% | -100.0% | -100.0% | $-15.00 | $-2.91 |
| 2026-06-23 | 6 | +50.0% | +33.1% | +66.8% | $+9.94 | $+6.55 |
| 2026-06-24 | 2 | +50.0% | -12.6% | +19.8% | $-1.26 | $+0.55 |
| 2026-06-25 | 4 | +75.0% | +16.3% | -8.1% | $+3.26 | $-0.30 |
| 2026-06-26 | 5 | +60.0% | -2.7% | -4.9% | $-0.67 | $-0.49 |

## Interpretation

- `entry timing` 是必须继续研究的执行层问题，因为旧 `best_ask` 有后视择时；但去掉 `best_ask` 后，6/21+ 仍弱，说明它不是唯一根因。
- 6/21 前训练样本里四个 route 都为正；6/21+ forward 中 `fresh_runway_current_no` 和 `capped_d2_no` 同时转负，且 forward 的 ask 更高、forecast overestimate 更大、remaining heat 更低、running max 更老。
- 6/23+ 看起来修复只是 split-sensitive：少量 `cheap_stale_tail` / `false_fade` 抵消了损失，但核心 fresh route 仍弱。
- 下一步应做 route-specific entry timing candidate menu，再用 nested walk-forward 选择；不能只看一个全局 ROI 或挑 6/23+ 作为新切分。
