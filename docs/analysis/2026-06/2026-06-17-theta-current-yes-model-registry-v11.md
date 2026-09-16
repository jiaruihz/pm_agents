# Theta Current YES Model Registry v11

Status: `historical model-governance reference / superseded_for_decision_use`
Generated: 2026-06-17T15:51:43+00:00
Target metric: `current_yes_probability_model_registry` = maintain comparable versions for `P(current running-max bracket wins)`.

> The same-denominator market-baseline principle remains valid. Model identities,
> data windows and deployment decisions in this June registry are historical;
> current model status is maintained in `WEATHER_STRATEGY_REGISTRY.md`.

## Data Snapshot

- Evidence layer: v8 time-aligned orderbook replay feature rows + canonical DB self-check. This is not live fill PnL.
- Row grain: one row = city / target_date / decision hour / current running-max bracket.
- Rows: 3239 total, 1527 train, 1712 holdout.
- Dates: 2026-05-19..2026-06-14; holdout dates=14.
- Live-like slice: 278 holdout rows / 13 dates.
- fact_built_at_utc: `2026-06-17T15:49:12.423102+00:00`.
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`.
- fact_trades settlement_status: `[{'settlement_status': None, 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`.
- fact_signal_candidates coverage: `{'rows': 31496, 'eligible': 10961, 'paper_ordered': 4274, 'live_filled': 348}`.
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`.

## Trading Action

No live model replacement from v11.

The new residual idea is correct as a framework, but on current data it does not beat the simple market baseline in the live-like slice. The registry should become the gate: a model is not eligible for live unless it beats `m0_market_ask` and `m1_market_iso` on live-like Brier/logloss with date-cluster CI not crossing zero.

## Model Registry

| version | family | status | deploy | feature summary |
|---|---|---|---|---|
| `m0_market_ask` | baseline | baseline | false | market YES ask only |
| `m1_market_iso` | baseline_calibrated | research_baseline | false | train-only isotonic calibration on market ask |
| `m2_live_v9_logit_artifact` | current_live | live_running | true | weather path + price + city/unit logistic artifact |
| `m3_weather_only_logit` | weather_only | research_rejected | false | METAR/ASOS path + city/unit, no price |
| `m4_market_residual_logit` | market_residual | new_candidate | false | market logit + no-reheat weather residual features + city/unit |
| `m5_market_residual_logit_iso` | market_residual_calibrated | new_candidate | false | market logit + weather residual + train-only isotonic calibration |
| `m6_hgb_iso_v10` | nonlinear_calibrated | research_candidate | false | nonlinear weather+price HistGradientBoosting + isotonic |
| `m7_hgb_residual_iso` | market_residual_nonlinear | new_candidate | false | market logit + residual features + nonlinear calibrated model |

## Holdout All

| version | rows/dates | AUC | Brier | LogLoss | Acc@0.5 | mean p | actual |
|---|---:|---:|---:|---:|---:|---:|---:|
| `m5_market_residual_logit_iso` | 1712/14 | 0.9322 | 0.0917 | 0.2964 | 87.6% | 68.3% | 68.8% |
| `m4_market_residual_logit` | 1712/14 | 0.9322 | 0.0921 | 0.2972 | 87.6% | 68.2% | 68.8% |
| `m6_hgb_iso_v10` | 1712/14 | 0.9262 | 0.0941 | 0.3069 | 87.5% | 68.6% | 68.8% |
| `m0_market_ask` | 1712/14 | 0.9288 | 0.0946 | 0.3042 | 86.9% | 70.7% | 68.8% |
| `m7_hgb_residual_iso` | 1712/14 | 0.9249 | 0.0952 | 0.3092 | 86.9% | 68.4% | 68.8% |
| `m2_live_v9_logit_artifact` | 1712/14 | 0.9246 | 0.0954 | 0.3130 | 87.3% | 67.9% | 68.8% |
| `m1_market_iso` | 1712/14 | 0.9267 | 0.0965 | 0.3106 | 87.0% | 68.4% | 68.8% |
| `m3_weather_only_logit` | 1712/14 | 0.8647 | 0.1356 | 0.4218 | 80.7% | 67.2% | 68.8% |

## Holdout Live-Like Slice

| version | rows/dates | AUC | Brier | LogLoss | Acc@0.5 | mean p | actual |
|---|---:|---:|---:|---:|---:|---:|---:|
| `m0_market_ask` | 278/13 | 0.7177 | 0.0659 | 0.2406 | 93.2% | 91.7% | 93.2% |
| `m2_live_v9_logit_artifact` | 278/13 | 0.7161 | 0.0688 | 0.2497 | 91.7% | 90.7% | 93.2% |
| `m4_market_residual_logit` | 278/13 | 0.7362 | 0.0694 | 0.2474 | 91.7% | 90.6% | 93.2% |
| `m5_market_residual_logit_iso` | 278/13 | 0.7374 | 0.0700 | 0.2516 | 92.4% | 90.0% | 93.2% |
| `m6_hgb_iso_v10` | 278/13 | 0.7147 | 0.0701 | 0.2547 | 92.1% | 89.9% | 93.2% |
| `m7_hgb_residual_iso` | 278/13 | 0.7196 | 0.0701 | 0.2535 | 91.7% | 89.5% | 93.2% |
| `m1_market_iso` | 278/13 | 0.7124 | 0.0723 | 0.2595 | 92.1% | 90.7% | 93.2% |
| `m3_weather_only_logit` | 278/13 | 0.6413 | 0.0867 | 0.3025 | 89.9% | 84.8% | 93.2% |

## Bootstrap Checks

Negative delta means the candidate is better.

- `m2_live_v9_logit_artifact` vs `m0_market_ask` on brier: delta 0.0029, CI95 [-0.0015076040635220544, 0.007125595800445155] over 278 rows / 13 dates.
- `m2_live_v9_logit_artifact` vs `m1_market_iso` on brier: delta -0.0035, CI95 [-0.009194831506214137, 0.0019082616368984895] over 278 rows / 13 dates.
- `m4_market_residual_logit` vs `m0_market_ask` on brier: delta 0.0036, CI95 [-0.0035021115146492187, 0.010073556000756368] over 278 rows / 13 dates.
- `m5_market_residual_logit_iso` vs `m1_market_iso` on brier: delta -0.0022, CI95 [-0.007888955513528964, 0.0033235324007799974] over 278 rows / 13 dates.
- `m7_hgb_residual_iso` vs `m1_market_iso` on brier: delta -0.0022, CI95 [-0.006163174897834329, 0.0019114692552627223] over 278 rows / 13 dates.
- `m6_hgb_iso_v10` vs `m2_live_v9_logit_artifact` on brier: delta -0.0012, CI95 [-0.005127372237957865, 0.002842404538502606] over 1712 rows / 14 dates.
- `m5_market_residual_logit_iso` vs `m2_live_v9_logit_artifact` on brier: delta -0.0037, CI95 [-0.0064600672204786925, -0.0010090545890562431] over 1712 rows / 14 dates.

## Human Conclusion

1. Maintaining model versions is necessary. Without a registry, it is too easy to celebrate a new model that only beats the old model but not the market baseline.
2. The current live v9 logistic is a reasonable calibrated model, but it is not clearly better than market ask in the live-like slice.
3. `m1_market_iso` is now the fair baseline: market ask after train-only calibration. New models should beat both raw market and market_iso.
4. The v11 residual models are conceptually right, but current evidence says they are not ready. They did not produce a live-slice improvement thick enough to justify deployment.
5. The next effective model work is feature creation, not another classifier sweep: minutes since max, first/last max touch, forecast peak hour, fresh book ask, snapshot age, and city-hour calibration.

## Proposed Governance Rule

Every future theta-current-YES probability model must add one registry row with:

- version id and family;
- frozen feature list;
- train/holdout split;
- holdout_all metrics;
- holdout_live_like metrics;
- bootstrap delta vs `m0_market_ask`, `m1_market_iso`, and current live model;
- verdict: `research_only`, `shadow_candidate`, or `live_candidate`.

Promotion gate:

```text
live_candidate only if:
  live-like Brier/logloss beats m0_market_ask and m1_market_iso
  date-cluster CI for delta is < 0
  at least 10 holdout dates and 30+ live-like rows
  calibration table has no obvious high-probability overconfidence
```

## Artifacts

- JSON: `docs/analysis/2026-06/2026-06-17-theta-current-yes-model-registry-v11.json`
- registry CSV: `docs/analysis/2026-06/generated/theta_current_yes_model_registry_v11/model_registry.csv`
- metrics CSV: `docs/analysis/2026-06/generated/theta_current_yes_model_registry_v11/model_metrics.csv`
- scored rows CSV: `docs/analysis/2026-06/generated/theta_current_yes_model_registry_v11/scored_rows.csv`
