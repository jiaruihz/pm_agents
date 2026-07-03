# Tmax Distribution P4 Observed-Label Extension v1

> generated_at_utc: `2026-07-03T14:01:51+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: research-only observed-max label extension; no canonical settlement/live behavior changed.

## 结论

- 本轮补的是效果验证，不是 live 改动：用 `final_max_native` 推导 6/27+ 的 local bucket label，明确标记为 `observed_max_derived`。
- 可评分行扩到 `2026-05-19`..`2026-07-01`，其中 raw label sources: `{'settlement_outcomes': 8132, 'missing': 3946, 'observed_max_derived': 1435, 'unmapped': 9}`。
- 在 6/21-6/26 verified settlement 上，P3 机制版继续优于 market。
- 在 6/27-6/29 extension 上，结果只能看方向和压力，不能当正式 PnL。

## Proper Scoring

| scope | method | n | dates | cities | logloss | logloss_delta_vs_market | brier | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | loo_no_city_source_blend | 396 | 5 | 36 | 0.8696 | -0.1815 | 0.4624 | 64.9% | 0.5909 |
| extension_forward | mkt_regime_blend | 396 | 5 | 36 | 0.8696 | -0.1815 | 0.4624 | 64.9% | 0.5909 |
| extension_forward | mkt_city_source_blend | 396 | 5 | 36 | 1.0031 | -0.0480 | 0.4769 | 66.4% | 0.5897 |
| extension_forward | loo_no_regime_blend | 396 | 5 | 36 | 1.0079 | -0.0433 | 0.4791 | 66.2% | 0.5880 |
| extension_forward | market_local_norm | 396 | 5 | 36 | 1.0511 | +0.0000 | 0.4422 | 67.7% | 0.5912 |
| verified_forward | mkt_city_source_blend | 1083 | 7 | 36 | 0.5128 | -0.0909 | 0.2805 | 78.9% | 0.7127 |
| verified_forward | loo_no_city_source_blend | 1083 | 7 | 36 | 0.5132 | -0.0906 | 0.2796 | 78.6% | 0.7094 |
| verified_forward | mkt_regime_blend | 1083 | 7 | 36 | 0.5132 | -0.0906 | 0.2796 | 78.6% | 0.7094 |
| verified_forward | loo_no_regime_blend | 1083 | 7 | 36 | 0.5186 | -0.0852 | 0.2831 | 78.6% | 0.7114 |
| verified_forward | market_local_norm | 1083 | 7 | 36 | 0.6037 | +0.0000 | 0.2957 | 79.0% | 0.6898 |

## EV Shadow, Edge >= 0.02, One Expression Per State

| scope | method | rows | dates | cost | pnl | roi | roi_ci_low | roi_ci_high | mix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | market_local_norm | 13 | 3 | 4.1010 | 0.8990 | +21.9% | -6.4% | +194.1% | YES 0 / curNO 6 / d1NO 7 / d2NO 0 |
| extension_forward | mkt_city_source_blend | 247 | 5 | 126.0290 | 6.9710 | +5.5% | -3.0% | +25.7% | YES 73 / curNO 53 / d1NO 86 / d2NO 35 |
| extension_forward | loo_no_regime_blend | 249 | 5 | 122.7380 | 6.2620 | +5.1% | -6.7% | +25.6% | YES 84 / curNO 49 / d1NO 83 / d2NO 33 |
| extension_forward | loo_no_city_source_blend | 207 | 5 | 110.6870 | 3.3130 | +3.0% | -17.5% | +28.4% | YES 58 / curNO 38 / d1NO 75 / d2NO 36 |
| extension_forward | mkt_regime_blend | 207 | 5 | 110.6870 | 3.3130 | +3.0% | -17.5% | +28.4% | YES 58 / curNO 38 / d1NO 75 / d2NO 36 |
| verified_forward | loo_no_city_source_blend | 389 | 5 | 211.1240 | 30.8760 | +14.6% | +2.4% | +24.9% | YES 160 / curNO 79 / d1NO 119 / d2NO 31 |
| verified_forward | mkt_regime_blend | 389 | 5 | 211.1240 | 30.8760 | +14.6% | +2.4% | +24.9% | YES 160 / curNO 79 / d1NO 119 / d2NO 31 |
| verified_forward | mkt_city_source_blend | 479 | 5 | 256.0680 | 30.9320 | +12.1% | +5.9% | +18.9% | YES 204 / curNO 87 / d1NO 141 / d2NO 47 |
| verified_forward | loo_no_regime_blend | 463 | 5 | 235.5380 | 22.4620 | +9.5% | +6.1% | +12.7% | YES 225 / curNO 66 / d1NO 133 / d2NO 39 |
| verified_forward | market_local_norm | 51 | 5 | 9.8340 | -2.8340 | -28.8% | -72.4% | +21.6% | YES 1 / curNO 22 / d1NO 27 / d2NO 1 |

## Data Boundary

- `fact_signal_candidates`: `{'rows': 44597, 'min_date': '2026-05-05', 'max_date': '2026-07-05', 'max_built_at_utc': '2026-07-03T14:00:48.998468+00:00'}`
- `fact_trades`: `{'rows': 4414, 'min_date': '2026-05-06', 'max_date': '2026-07-01', 'max_built_at_utc': '2026-07-03T13:52:30.252994+00:00'}`
- `settlement_outcomes`: `{'rows': 25084, 'min_date': '2026-05-04', 'max_date': '2026-06-30', 'cities': 49}`

6/27+ 的正式 settlement 仍未完整进入 `settlement_outcomes`。本报告的 observed-derived 部分只回答“如果 observed max 口径成立，模型新日期表现如何”，不替代结算。

## Verdict

conclusion=`research_observed_label_extension_only`; no live action.

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p4_observed_label_extension_v1/score_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p4_observed_label_extension_v1/ev_deduped_summary.csv`
- `docs/analysis/2026-07/2026-07-03-tmax-distribution-p4-observed-label-extension-v1.json`
