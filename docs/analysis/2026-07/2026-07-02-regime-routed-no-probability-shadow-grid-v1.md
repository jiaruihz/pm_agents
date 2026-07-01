# Regime-Routed NO Probability Shadow Grid V1

Verdict: `shadow_only_more_data_needed`. 这份报告只比较概率模型 overlay，不改变 live 下单、entry 或 sizing。

## 结论

- `frozen_live_like_route_price` 的 6/21+ forward 太薄：候选变体最多 5 笔，CI 全跨 0，不能证明概率模型优于当前 fixed-quality score。
- expanding walk-forward 上，部分概率 overlay 点估比当前 baseline 高，但样本仍只有 15-23 笔，并且有 4-6 个单日 -100% day blocks。
- 现在最合理的用法是：继续把 `p_no_wf_*` 作为 shadow telemetry / size 研究字段，不替换 live score。

## Shadow Variants

- `baseline_current_score_gate_size_score`: 当前 fixed-quality score/ask 口径，作为主对照。
- `prob_p_ge_ask_size_p`: 概率模型认为 `P(NO win) >= ask` 才入场，并按概率 size。
- `prob_p_ge_ask_size_current_score`: 用概率做 entry gate，但仍按当前 score size。
- `prob_p_ge_ask_plus05_size_p`: 概率至少高出 ask 5pct 才入场。
- `prob_ratio_ge125_size_p`: 概率/ask 至少 1.25。
- `agreement_current_and_prob_size_score`: 当前 score 和概率模型同时同意才入场。
- `current_gate_size_min_score_p`: 当前 entry 不变，但 size 取当前 score 和概率的较小值。
- `blend50` / `blend70score`: 当前 score 与概率做软融合。
- `diagnostic_current_gate_prob_disagrees`: 当前 score 想买、概率模型反对的 wrong-way detector；只做诊断，不参与候选排序。

## Frozen Forward Candidates

| model | shadow_variant | rows | dates | cities | win_rate | avg_ask | avg_cost | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| logit_market_route | current_gate_size_min_score_p | 5 | 4 | 5 | +60.0% | 0.342 | $+0.36 | +71.0% | -45.3% | +214.7% | 1 |
| logit_market_route | baseline_current_score_gate_size_score | 5 | 4 | 5 | +60.0% | 0.342 | $+0.43 | +69.5% | -45.0% | +203.6% | 1 |
| logit_mechanism_weather | baseline_current_score_gate_size_score | 5 | 4 | 5 | +60.0% | 0.342 | $+0.43 | +69.5% | -45.0% | +203.6% | 1 |
| logit_mechanism_weather_citybias | baseline_current_score_gate_size_score | 5 | 4 | 5 | +60.0% | 0.342 | $+0.43 | +69.5% | -45.0% | +203.6% | 1 |
| logit_mechanism_plus_heuristic_score | baseline_current_score_gate_size_score | 5 | 4 | 5 | +60.0% | 0.342 | $+0.43 | +69.5% | -45.0% | +203.6% | 1 |
| logit_market_route | blend70score_gate_size_blend70 | 5 | 4 | 5 | +60.0% | 0.342 | $+0.43 | +68.7% | -45.6% | +212.2% | 1 |
| logit_mechanism_plus_heuristic_score | current_gate_size_min_score_p | 5 | 4 | 5 | +60.0% | 0.342 | $+0.36 | +60.2% | -58.0% | +229.3% | 1 |
| logit_mechanism_weather_citybias | current_gate_size_min_score_p | 5 | 4 | 5 | +60.0% | 0.342 | $+0.35 | +58.8% | -59.7% | +233.6% | 1 |
| logit_mechanism_weather | blend70score_gate_size_blend70 | 4 | 3 | 4 | +50.0% | 0.310 | $+0.42 | +57.5% | -100.0% | +376.2% | 1 |
| logit_mechanism_weather | current_gate_size_min_score_p | 5 | 4 | 5 | +60.0% | 0.342 | $+0.33 | +55.1% | -65.7% | +240.7% | 1 |
| logit_mechanism_weather | prob_ratio_ge125_size_p | 3 | 3 | 3 | +33.3% | 0.263 | $+0.50 | +47.4% | -100.0% | +376.2% | 2 |
| logit_mechanism_weather_citybias | prob_p_ge_ask_size_p | 5 | 5 | 5 | +40.0% | 0.284 | $+0.48 | +45.5% | -100.0% | +234.0% | 3 |
| logit_market_route | blend50_gate_size_blend50 | 5 | 4 | 5 | +40.0% | 0.292 | $+0.38 | +37.3% | -100.0% | +221.9% | 2 |
| logit_market_route | prob_p_ge_ask_size_p | 5 | 5 | 5 | +40.0% | 0.284 | $+0.44 | +35.7% | -100.0% | +220.9% | 3 |
| logit_market_route | prob_p_ge_ask_plus05_size_p | 5 | 5 | 5 | +40.0% | 0.284 | $+0.44 | +35.7% | -100.0% | +220.9% | 3 |
| logit_market_route | prob_ratio_ge125_size_p | 5 | 5 | 5 | +40.0% | 0.284 | $+0.44 | +35.7% | -100.0% | +220.9% | 3 |
| logit_mechanism_weather | prob_p_ge_ask_size_p | 4 | 4 | 4 | +25.0% | 0.253 | $+0.44 | +24.6% | -100.0% | +248.6% | 3 |
| logit_mechanism_weather | prob_p_ge_ask_plus05_size_p | 4 | 4 | 4 | +25.0% | 0.253 | $+0.44 | +24.6% | -100.0% | +248.6% | 3 |
| logit_mechanism_weather_citybias | prob_p_ge_ask_plus05_size_p | 4 | 4 | 4 | +25.0% | 0.253 | $+0.50 | +24.5% | -100.0% | +251.6% | 3 |
| logit_mechanism_weather_citybias | prob_ratio_ge125_size_p | 4 | 4 | 4 | +25.0% | 0.253 | $+0.50 | +24.5% | -100.0% | +251.6% | 3 |

## Frozen Expanding Walk-Forward Candidates

| model | shadow_variant | rows | dates | cities | win_rate | avg_ask | avg_cost | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| logit_market_route | agreement_current_and_prob_size_score | 18 | 13 | 14 | +50.0% | 0.231 | $+0.42 | +137.0% | +31.4% | +254.0% | 4 |
| logit_mechanism_weather | prob_ratio_ge125_size_p | 15 | 12 | 10 | +53.3% | 0.252 | $+0.52 | +114.2% | +11.9% | +221.3% | 5 |
| logit_mechanism_weather | agreement_current_and_prob_size_score | 17 | 13 | 13 | +47.1% | 0.249 | $+0.45 | +113.9% | -2.8% | +247.7% | 5 |
| logit_mechanism_weather_citybias | prob_ratio_ge125_size_p | 16 | 14 | 12 | +50.0% | 0.274 | $+0.55 | +107.2% | +11.6% | +202.3% | 6 |
| logit_market_route | prob_p_ge_ask_size_p | 23 | 16 | 19 | +52.2% | 0.268 | $+0.44 | +105.2% | +31.4% | +179.9% | 6 |
| logit_mechanism_weather_citybias | agreement_current_and_prob_size_score | 18 | 14 | 14 | +44.4% | 0.254 | $+0.46 | +105.1% | -6.5% | +229.6% | 6 |
| logit_mechanism_plus_heuristic_score | prob_ratio_ge125_size_p | 16 | 14 | 12 | +50.0% | 0.270 | $+0.55 | +104.2% | +4.7% | +203.7% | 6 |
| logit_mechanism_weather_citybias | prob_p_ge_ask_size_p | 21 | 16 | 16 | +52.4% | 0.287 | $+0.51 | +102.3% | +21.6% | +179.2% | 6 |
| logit_mechanism_weather_citybias | prob_p_ge_ask_plus05_size_p | 18 | 14 | 13 | +50.0% | 0.284 | $+0.54 | +100.7% | +11.0% | +186.3% | 6 |
| logit_mechanism_plus_heuristic_score | agreement_current_and_prob_size_score | 18 | 14 | 13 | +44.4% | 0.264 | $+0.47 | +100.6% | -8.6% | +225.6% | 6 |
| logit_market_route | prob_p_ge_ask_plus05_size_p | 20 | 14 | 17 | +45.0% | 0.247 | $+0.44 | +99.1% | +11.8% | +187.3% | 6 |
| logit_market_route | prob_ratio_ge125_size_p | 20 | 14 | 17 | +45.0% | 0.247 | $+0.44 | +99.1% | +11.8% | +187.3% | 6 |
| logit_mechanism_plus_heuristic_score | prob_p_ge_ask_size_p | 20 | 15 | 15 | +50.0% | 0.277 | $+0.50 | +98.6% | +8.6% | +188.1% | 6 |
| logit_market_route | prob_p_ge_ask_size_current_score | 29 | 16 | 21 | +44.8% | 0.317 | $+0.34 | +94.8% | +14.4% | +178.2% | 5 |
| logit_market_route | baseline_current_score_gate_size_score | 25 | 17 | 16 | +52.0% | 0.306 | $+0.47 | +93.1% | +18.8% | +176.7% | 5 |
| logit_mechanism_weather | baseline_current_score_gate_size_score | 25 | 17 | 16 | +52.0% | 0.306 | $+0.47 | +93.1% | +18.8% | +176.7% | 5 |
| logit_mechanism_weather_citybias | baseline_current_score_gate_size_score | 25 | 17 | 16 | +52.0% | 0.306 | $+0.47 | +93.1% | +18.8% | +176.7% | 5 |
| logit_mechanism_plus_heuristic_score | baseline_current_score_gate_size_score | 25 | 17 | 16 | +52.0% | 0.306 | $+0.47 | +93.1% | +18.8% | +176.7% | 5 |
| logit_mechanism_plus_heuristic_score | prob_p_ge_ask_plus05_size_p | 15 | 14 | 11 | +46.7% | 0.280 | $+0.57 | +91.9% | -13.2% | +197.9% | 7 |
| logit_mechanism_weather | current_gate_size_min_score_p | 29 | 17 | 20 | +58.6% | 0.314 | $+0.40 | +91.0% | +14.3% | +166.4% | 5 |

## Current-Strategy Disagreement Diagnostic

Rows here are current heuristic entries where the probability model says `p_no < ask`. This is a wrong-way detector candidate, not a live veto yet.

| evidence_layer | scope | model | shadow_variant | rows | dates | cities | win_rate | avg_ask | avg_cost | roi | roi_ci_low | roi_ci_high | daily_negative_100pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| frozen_live_like_route_price | expanding_walk_forward | logit_market_route | diagnostic_current_gate_prob_disagrees | 11 | 11 | 9 | +54.5% | 0.452 | $+0.61 | +26.4% | -42.8% | +96.2% | 5 |
| frozen_live_like_route_price | expanding_walk_forward | logit_mechanism_plus_heuristic_score | diagnostic_current_gate_prob_disagrees | 11 | 10 | 8 | +45.5% | 0.412 | $+0.52 | +0.2% | -77.7% | +76.8% | 6 |
| frozen_live_like_route_price | expanding_walk_forward | logit_mechanism_weather | diagnostic_current_gate_prob_disagrees | 11 | 10 | 10 | +54.5% | 0.409 | $+0.50 | +40.1% | -45.9% | +110.3% | 5 |
| frozen_live_like_route_price | expanding_walk_forward | logit_mechanism_weather_citybias | diagnostic_current_gate_prob_disagrees | 11 | 10 | 8 | +45.5% | 0.412 | $+0.52 | +0.2% | -77.7% | +76.8% | 6 |
| frozen_live_like_route_price | forward_2026-06-21_plus | logit_market_route | diagnostic_current_gate_prob_disagrees | 2 | 2 | 2 | +100.0% | 0.460 | $+0.55 | +117.8% | NA | NA | 0 |
| frozen_live_like_route_price | forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | diagnostic_current_gate_prob_disagrees | 2 | 2 | 2 | +100.0% | 0.460 | $+0.55 | +117.8% | NA | NA | 0 |
| frozen_live_like_route_price | forward_2026-06-21_plus | logit_mechanism_weather | diagnostic_current_gate_prob_disagrees | 2 | 2 | 2 | +100.0% | 0.460 | $+0.55 | +117.8% | NA | NA | 0 |
| frozen_live_like_route_price | forward_2026-06-21_plus | logit_mechanism_weather_citybias | diagnostic_current_gate_prob_disagrees | 2 | 2 | 2 | +100.0% | 0.460 | $+0.55 | +117.8% | NA | NA | 0 |
| historical_best_ask_diagnostic | expanding_walk_forward | logit_market_route | diagnostic_current_gate_prob_disagrees | 7 | 7 | 5 | +42.9% | 0.484 | $+0.63 | -11.0% | -77.3% | +73.1% | 4 |
| historical_best_ask_diagnostic | expanding_walk_forward | logit_mechanism_plus_heuristic_score | diagnostic_current_gate_prob_disagrees | 12 | 11 | 10 | +41.7% | 0.379 | $+0.45 | +12.6% | -59.2% | +94.6% | 6 |
| historical_best_ask_diagnostic | expanding_walk_forward | logit_mechanism_weather | diagnostic_current_gate_prob_disagrees | 10 | 10 | 8 | +50.0% | 0.404 | $+0.49 | +26.3% | -52.0% | +112.8% | 5 |
| historical_best_ask_diagnostic | expanding_walk_forward | logit_mechanism_weather_citybias | diagnostic_current_gate_prob_disagrees | 11 | 10 | 9 | +36.4% | 0.386 | $+0.46 | -1.4% | -75.8% | +78.5% | 6 |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | logit_market_route | diagnostic_current_gate_prob_disagrees | 0 | 0 | 0 | NA | NA | NA | NA | NA | NA | 0 |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | logit_mechanism_plus_heuristic_score | diagnostic_current_gate_prob_disagrees | 0 | 0 | 0 | NA | NA | NA | NA | NA | NA | 0 |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | logit_mechanism_weather | diagnostic_current_gate_prob_disagrees | 1 | 1 | 1 | +100.0% | 0.470 | $+0.51 | +112.8% | NA | NA | 0 |
| historical_best_ask_diagnostic | forward_2026-06-21_plus | logit_mechanism_weather_citybias | diagnostic_current_gate_prob_disagrees | 0 | 0 | 0 | NA | NA | NA | NA | NA | NA | 0 |

## Files

- Summary: `docs/analysis/2026-07/generated/regime_routed_no_probability_shadow_grid_v1/summary.json`
- Grid CSV: `docs/analysis/2026-07/generated/regime_routed_no_probability_shadow_grid_v1/shadow_grid_summary.csv`
- Daily CSV: `docs/analysis/2026-07/generated/regime_routed_no_probability_shadow_grid_v1/shadow_grid_daily.csv`
