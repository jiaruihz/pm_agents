# Tmax Distribution P4 Observed-Label Extension v1

> generated_at_utc: `2026-07-05T14:15:28+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: research-only observed-max label extension; no canonical settlement/live behavior changed.

## 结论

- 本轮补的是效果验证，不是 live 改动：用 `final_max_native` 推导 6/27+ 的 local bucket label，明确标记为 `observed_max_derived`。
- 可评分行扩到 `2026-05-19`..`2026-07-03`，其中 raw label sources: `{'settlement_outcomes': 9744, 'missing': 4116}`。
- 在 6/21-6/26 verified settlement 上，P3 机制版继续优于 market。
- extension 部分只在存在 `observed_max_derived` 行时生成；若最新 settlement 已覆盖这些行，extension 为空是正常结果。

## Proper Scoring

| scope | method | n | dates | cities | logloss | logloss_delta_vs_market | brier | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | mkt_city_source_blend | 1729 | 12 | 36 | 0.5779 | -0.0658 | 0.3207 | 76.5% | 0.6651 |
| verified_forward | loo_no_city_source_blend | 1729 | 12 | 36 | 0.5791 | -0.0645 | 0.3218 | 76.7% | 0.6642 |
| verified_forward | mkt_regime_blend | 1729 | 12 | 36 | 0.5791 | -0.0645 | 0.3218 | 76.7% | 0.6642 |
| verified_forward | loo_no_regime_blend | 1729 | 12 | 36 | 0.5805 | -0.0632 | 0.3225 | 76.0% | 0.6643 |
| verified_forward | market_local_norm | 1729 | 12 | 36 | 0.6437 | +0.0000 | 0.3271 | 76.7% | 0.6614 |

## EV Shadow, Edge >= 0.02, One Expression Per State

| scope | method | rows | dates | cost | pnl | roi | roi_ci_low | roi_ci_high | mix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verified_forward | loo_no_city_source_blend | 300 | 8 | 120.1080 | 7.8920 | +6.6% | -5.0% | +16.7% | YES 73 / curNO 101 / d1NO 80 / d2NO 46 |
| verified_forward | mkt_regime_blend | 300 | 8 | 120.1080 | 7.8920 | +6.6% | -5.0% | +16.7% | YES 73 / curNO 101 / d1NO 80 / d2NO 46 |
| verified_forward | mkt_city_source_blend | 331 | 8 | 140.5900 | 5.4100 | +3.8% | -9.3% | +17.5% | YES 79 / curNO 102 / d1NO 95 / d2NO 55 |
| verified_forward | loo_no_regime_blend | 305 | 8 | 126.1100 | 1.8900 | +1.5% | -17.0% | +18.9% | YES 80 / curNO 77 / d1NO 91 / d2NO 57 |
| verified_forward | market_local_norm | 59 | 8 | 12.1810 | -1.1810 | -9.7% | -53.2% | +68.2% | YES 1 / curNO 23 / d1NO 33 / d2NO 2 |

## Data Boundary

- `fact_signal_candidates`: `{'rows': 46253, 'min_date': '2026-05-05', 'max_date': '2026-07-07', 'max_built_at_utc': '2026-07-05T14:02:36.365295+00:00'}`
- `fact_trades`: `{'rows': 4484, 'min_date': '2026-05-06', 'max_date': '2026-07-05', 'max_built_at_utc': '2026-07-05T14:02:09.027295+00:00'}`
- `settlement_outcomes`: `{'rows': 28164, 'min_date': '2026-05-04', 'max_date': '2026-07-04', 'cities': 49}`

observed-derived 部分只回答“如果 observed max 口径成立，模型新日期表现如何”，不替代结算；若 extension 为空，说明当前可评分行已全部落在 verified settlement 口径或被标签规则排除。

## Verdict

conclusion=`research_observed_label_extension_only`; no live action.

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p4_observed_label_extension_v1/score_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p4_observed_label_extension_v1/ev_deduped_summary.csv`
- `docs/analysis/2026-07/2026-07-03-tmax-distribution-p4-observed-label-extension-v1.json`
