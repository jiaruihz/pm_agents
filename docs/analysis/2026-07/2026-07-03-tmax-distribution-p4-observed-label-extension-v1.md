# Tmax Distribution P4 Observed-Label Extension v1

> generated_at_utc: `2026-07-09T03:40:36+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: research-only observed-max label extension; no canonical settlement/live behavior changed.

## 结论

- 本轮补的是效果验证，不是 live 改动：用 `final_max_native` 推导 6/27+ 的 local bucket label，明确标记为 `observed_max_derived`。
- 可评分行扩到 `2026-05-19`..`2026-07-08`，其中 raw label sources: `{'settlement_outcomes': 10105, 'missing': 4231, 'observed_max_derived': 32}`。
- 在 6/21-6/26 verified settlement 上，P3 机制版继续优于 market。
- extension 部分只在存在 `observed_max_derived` 行时生成；若最新 settlement 已覆盖这些行，extension 为空是正常结果。

## Proper Scoring

| scope | method | n | dates | cities | logloss | logloss_delta_vs_market | brier | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | loo_no_regime_blend | 29 | 2 | 8 | 0.6354 | -0.0189 | 0.3386 | 75.9% | 0.6525 |
| extension_forward | mkt_city_source_blend | 29 | 2 | 8 | 0.6469 | -0.0074 | 0.3415 | 75.9% | 0.6532 |
| extension_forward | loo_no_city_source_blend | 29 | 2 | 8 | 0.6518 | -0.0025 | 0.3388 | 75.9% | 0.6550 |
| extension_forward | mkt_regime_blend | 29 | 2 | 8 | 0.6518 | -0.0025 | 0.3388 | 75.9% | 0.6550 |
| extension_forward | market_local_norm | 29 | 2 | 8 | 0.6543 | +0.0000 | 0.3408 | 75.9% | 0.6496 |
| verified_forward | mkt_city_source_blend | 2048 | 15 | 36 | 0.5866 | -0.0623 | 0.3271 | 75.6% | 0.6606 |
| verified_forward | loo_no_city_source_blend | 2048 | 15 | 36 | 0.5878 | -0.0611 | 0.3282 | 75.7% | 0.6596 |
| verified_forward | mkt_regime_blend | 2048 | 15 | 36 | 0.5878 | -0.0611 | 0.3282 | 75.7% | 0.6596 |
| verified_forward | loo_no_regime_blend | 2048 | 15 | 36 | 0.5881 | -0.0608 | 0.3282 | 75.3% | 0.6600 |
| verified_forward | market_local_norm | 2048 | 15 | 36 | 0.6489 | +0.0000 | 0.3311 | 76.0% | 0.6578 |

## EV Shadow, Edge >= 0.02, One Expression Per State

| scope | method | rows | dates | cost | pnl | roi | roi_ci_low | roi_ci_high | mix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extension_forward | loo_no_city_source_blend | 10 | 2 | 5.9100 | 0.0900 | +1.5% | n/a | n/a | YES 1 / curNO 2 / d1NO 3 / d2NO 4 |
| extension_forward | mkt_regime_blend | 10 | 2 | 5.9100 | 0.0900 | +1.5% | n/a | n/a | YES 1 / curNO 2 / d1NO 3 / d2NO 4 |
| extension_forward | loo_no_regime_blend | 3 | 2 | 1.0630 | -0.0630 | -5.9% | n/a | n/a | YES 1 / curNO 1 / d1NO 0 / d2NO 1 |
| extension_forward | mkt_city_source_blend | 7 | 2 | 4.2600 | -1.2600 | -29.6% | n/a | n/a | YES 0 / curNO 2 / d1NO 1 / d2NO 4 |
| extension_forward | market_local_norm | 1 | 1 | 0.1700 | -0.1700 | -100.0% | n/a | n/a | YES 0 / curNO 1 / d1NO 0 / d2NO 0 |
| verified_forward | loo_no_city_source_blend | 511 | 15 | 231.4770 | 8.5230 | +3.7% | -10.7% | +16.9% | YES 102 / curNO 173 / d1NO 146 / d2NO 90 |
| verified_forward | mkt_regime_blend | 511 | 15 | 231.4770 | 8.5230 | +3.7% | -10.7% | +16.9% | YES 102 / curNO 173 / d1NO 146 / d2NO 90 |
| verified_forward | mkt_city_source_blend | 541 | 15 | 253.0020 | 6.9980 | +2.8% | -10.4% | +15.8% | YES 116 / curNO 157 / d1NO 170 / d2NO 98 |
| verified_forward | loo_no_regime_blend | 494 | 15 | 227.4190 | 5.5810 | +2.5% | -11.2% | +15.7% | YES 106 / curNO 132 / d1NO 159 / d2NO 97 |
| verified_forward | market_local_norm | 87 | 14 | 20.1470 | -1.1470 | -5.7% | -37.9% | +39.9% | YES 1 / curNO 32 / d1NO 51 / d2NO 3 |

## Data Boundary

- `fact_signal_candidates`: `{'rows': 48857, 'min_date': '2026-05-05', 'max_date': '2026-07-09', 'max_built_at_utc': '2026-07-08T09:18:35.918613+00:00'}`
- `fact_trades`: `{'rows': 4533, 'min_date': '2026-05-06', 'max_date': '2026-07-07', 'max_built_at_utc': '2026-07-08T09:17:24.051488+00:00'}`
- `settlement_outcomes`: `{'rows': 29715, 'min_date': '2026-05-04', 'max_date': '2026-07-07', 'cities': 49}`

observed-derived 部分只回答“如果 observed max 口径成立，模型新日期表现如何”，不替代结算；若 extension 为空，说明当前可评分行已全部落在 verified settlement 口径或被标签规则排除。

## Verdict

conclusion=`research_observed_label_extension_only`; no live action.

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p4_observed_label_extension_v1/score_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p4_observed_label_extension_v1/ev_deduped_summary.csv`
- `docs/analysis/2026-07/2026-07-03-tmax-distribution-p4-observed-label-extension-v1.json`
