# Theta Current YES Model Accuracy v10

Status: research_only
Generated: 2026-06-16T09:00:05+00:00
Target metric: `current_max_is_final_high_accuracy` = for a city/hour snapshot, predict whether the current running-max bracket is the final winning highest-temperature bracket.

## Data Snapshot

- Evidence layer: v8 historical orderbook replay feature rows + local canonical DB self-check; not live fill PnL.
- Row grain: one row = one city / target_date / decision hour / current bracket decision snapshot.
- Feature rows: 3239 rows, 1527 train / 1712 holdout, dates 2026-05-19..2026-06-14.
- Holdout dates: 14; holdout label rate: 68.8%.
- fact_built_at_utc: `2026-06-15T16:31:04.483297+00:00`.
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`.
- fact_trades settlement_status: `[{'settlement_status': None, 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`.
- fact_signal_candidates coverage: `{'rows': 30140, 'eligible': 10366, 'paper_ordered': 3968, 'live_filled': 348}`.
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`.

## Trading Action

Do not upgrade live from this report alone. This is model research only.

Best broad-holdout model candidate is `weather_plus_price_hgb_iso`: it improves probability calibration/Brier versus the current v8-style logistic on all holdout rows, but the improvement is small and should be treated as a research upgrade, not a live deploy. In the actual live-like slice, market ask remains the strongest probability baseline. The practical lesson is that weather features help most as calibration/risk filters around market price, not as a standalone oracle.

## Holdout Model Accuracy

| model | rows | dates/rate | AUC | Brier | LogLoss | Acc@0.5 | mean p |
|---|---:|---:|---:|---:|---:|---:|---:|
| weather_plus_price_hgb_iso | 1712 | 68.8% | 0.9262 | 0.0941 | 0.3069 | 87.5% | 68.6% |
| weather_plus_price_inter_logit_iso | 1712 | 68.8% | 0.9237 | 0.0942 | 0.3170 | 87.7% | 67.8% |
| weather_plus_price_logit_iso | 1712 | 68.8% | 0.9239 | 0.0943 | 0.3168 | 87.7% | 67.8% |
| market_yes_ask | 1712 | 68.8% | 0.9288 | 0.0946 | 0.3042 | 86.9% | 70.7% |
| weather_plus_price_logit | 1712 | 68.8% | 0.9246 | 0.0954 | 0.3130 | 87.3% | 67.9% |
| weather_only_logit | 1712 | 68.8% | 0.8647 | 0.1356 | 0.4218 | 80.7% | 67.2% |

## Live-Slice Accuracy

Live-slice here means h13-15, decline>=0.5, yes_ask>=0.55, and d1 sibling visible. It is the model-facing universe, not actual live fills.

| model | rows | dates/rate | AUC | Brier | LogLoss | Acc@0.5 | mean p |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_yes_ask | 278 | 93.2% | 0.7177 | 0.0659 | 0.2406 | 93.2% | 91.7% |
| weather_plus_price_logit | 278 | 93.2% | 0.7161 | 0.0688 | 0.2497 | 91.7% | 90.7% |
| weather_plus_price_logit_iso | 278 | 93.2% | 0.7020 | 0.0697 | 0.2873 | 92.1% | 89.5% |
| weather_plus_price_inter_logit_iso | 278 | 93.2% | 0.7033 | 0.0697 | 0.2880 | 91.7% | 89.7% |
| weather_plus_price_hgb_iso | 278 | 93.2% | 0.7147 | 0.0701 | 0.2547 | 92.1% | 89.9% |
| weather_only_logit | 278 | 93.2% | 0.6413 | 0.0867 | 0.3025 | 89.9% | 84.8% |

## Cluster Bootstrap Deltas

Negative Brier/logloss delta means the candidate is better than baseline.

- HGB isotonic vs current logistic, holdout Brier delta: -0.0012, CI95 [-0.005008466460892081, 0.003003551306627277].
- HGB isotonic vs market ask, holdout Brier delta: -0.0005, CI95 [-0.0034945397680527217, 0.0029499144558501173].
- Current logistic vs market ask, holdout Brier delta: 0.0008, CI95 [-0.004417407564335115, 0.006250263656782847].
- HGB isotonic vs current logistic, live-slice Brier delta: 0.0013, CI95 [-0.003511420694484966, 0.005983596171573402].

## Human Summary

1. The pure weather model is not enough: weather-only holdout AUC is around 0.865, far below the market ask baseline around 0.929.
2. The current weather+price logistic is reasonable, but not clearly better than market ask on ranking. Its role is mostly to smooth/adjust market probability using METAR path features.
3. The best tested broad-holdout upgrade is a nonlinear weather+price model with train-only isotonic calibration. It gives the best Brier on all holdout rows.
4. In the live-like slice, that nonlinear model does not beat market ask or the current logistic. This slice is only 278 rows / 13 dates and is already very high base-rate, so it needs more forward data before changing the live probability model.
5. For model accuracy, the next real improvement is likely feature quality: fresh book price at decision, snapshot age, minutes since running max, solar/local time geometry, and city-specific calibration.

## Proposed v10 Model Upgrade

- Keep label: `current bracket wins`.
- Keep universe: source-aligned cities.
- Use two probability layers:
  - `p_final_current`: weather+price calibrated model.
  - `p_executable_edge`: separate execution survival model, because today's live issue was stale executable price, not just no-reheat probability.
- Test `weather_plus_price_hgb_iso` in research/shadow only for broad probability calibration, but do not replace the live-slice probability model yet.
- If deployment later needs no sklearn on N100, export either a calibrated logistic or a compact tree/lookup artifact; do not install dependencies casually on N100.

## Artifacts

- JSON: `docs/analysis/2026-06/2026-06-16-theta-current-yes-model-accuracy-v10.json`
- metrics CSV: `docs/analysis/2026-06/generated/theta_current_yes_model_accuracy_v10/model_metrics.csv`
- calibration CSV: `docs/analysis/2026-06/generated/theta_current_yes_model_accuracy_v10/calibration_hgb_iso_holdout.csv`
- slice CSV: `docs/analysis/2026-06/generated/theta_current_yes_model_accuracy_v10/slice_calibration_hgb_iso_holdout.csv`
