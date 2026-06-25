# Current-YES Peak-YES First-Principles v3

Status: research-only
Generated: 2026-06-25T05:13:12+00:00

## 一句话结论

First-principles physics has real ordering signal, but it is still weaker than market pricing for peak YES: holdout physics AUC 0.639, market AUC 0.749, market+physics AUC 0.752. `physics edge>=2%` holdout ROI +1.3%, CI [-10.0%, +12.1%], forward ROI -1.7% on 67 rows. `market+physics edge>=0` is positive but thin: holdout 101 rows ROI +5.2%, forward 7 rows.

## 数据范围

- Atlas rows: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
- Atlas date range: `2026-05-19`..`2026-06-23`
- Tradable peak-YES rows: 2786 / dates 36 / cities 36
- Train: `<= 2026-05-31`; holdout: `2026-06-01`..`2026-06-20`; forward: `2026-06-21`..`2026-06-23`
- DB fact refresh: `2026-06-25T05:08:33.713141+00:00`

Peak YES means buying the current running-max bracket YES at quote ask; payoff is 1 if no later bracket bust occurs. This is opportunity replay, not live fill PnL.

Leakage guard: final max, realized remaining heat, forecast error, payoffs, and ROI fields are excluded from features.

## Mechanism

The physical score estimates future-break hazard from remaining solar window, forecast runway, forecast peak still ahead, warming momentum, freshness of the high, fade confirmation, dry/clear reheat support, and atlas regime priors. Regimes are continuous priors, not trading gates.

Weights:

| component | weight |
|---|---:|
| comp_forecast_runway | 1.30 |
| comp_forecast_peak_ahead | 0.75 |
| comp_solar_remaining | 0.80 |
| comp_warming_momentum | 1.05 |
| comp_fresh_high | 0.55 |
| comp_not_faded | 0.45 |
| comp_dry_clear_reheat | 0.55 |
| comp_day_regime_prior | 1.00 |
| comp_intraday_prior | 1.10 |
| comp_moisture_prior | 0.40 |
| comp_running_max_prior | 0.55 |

## Model Metrics

| period | model | rows | break | pred break | AUC | Brier | logloss |
|---|---|---:|---:|---:|---:|---:|---:|
| train | market_implied_break | 935 | +28.6% | +25.2% | 0.780 | 0.165 | 0.497 |
| train | first_principles_physics | 935 | +28.6% | +28.6% | 0.655 | 0.191 | 0.567 |
| train | market_plus_physics | 935 | +28.6% | +28.6% | 0.783 | 0.162 | 0.491 |
| holdout | market_implied_break | 1632 | +28.6% | +26.3% | 0.749 | 0.173 | 0.519 |
| holdout | first_principles_physics | 1632 | +28.6% | +28.2% | 0.639 | 0.194 | 0.574 |
| holdout | market_plus_physics | 1632 | +28.6% | +29.6% | 0.752 | 0.172 | 0.515 |
| forward | market_implied_break | 219 | +27.9% | +25.0% | 0.757 | 0.176 | 0.512 |
| forward | first_principles_physics | 219 | +27.9% | +30.5% | 0.634 | 0.193 | 0.570 |
| forward | market_plus_physics | 219 | +27.9% | +28.8% | 0.763 | 0.173 | 0.507 |

## EV Rules

| period | rule | rows | dates | win | avg ask | avg edge | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| forward | buy_all_peak_yes | 219 | 3 | +72.1% | 0.750 | -0.0% | -3.8% | [-16.3%, +8.7%] |
| forward | market_physics_edge_ge_0.00 | 7 | 2 | +100.0% | 0.883 | +0.3% | +13.3% | [+12.7%, +14.1%] |
| forward | market_physics_edge_ge_0.02 | 0 | 0 | NA | NA | NA | NA | [NA, NA] |
| forward | market_physics_edge_ge_0.02_ask_50_70 | 0 | 0 | NA | NA | NA | NA | [NA, NA] |
| forward | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | NA | [NA, NA] |
| forward | physics_edge_ge_0.02 | 67 | 3 | +50.7% | 0.516 | +16.9% | -1.7% | [-16.2%, +7.9%] |
| forward | physics_edge_ge_0.02_ask_50_70 | 28 | 3 | +35.7% | 0.586 | +10.4% | -39.1% | [-61.8%, -14.2%] |
| forward | physics_edge_ge_0.05 | 61 | 3 | +50.8% | 0.502 | +18.2% | +1.2% | [-4.3%, +4.3%] |
| holdout | buy_all_peak_yes | 1632 | 20 | +71.4% | 0.737 | -0.0% | -3.0% | [-8.4%, +2.2%] |
| holdout | market_physics_edge_ge_0.00 | 101 | 20 | +86.1% | 0.819 | +0.5% | +5.2% | [-7.3%, +14.6%] |
| holdout | market_physics_edge_ge_0.02 | 2 | 2 | +100.0% | 0.482 | +2.5% | +107.7% | [+92.3%, +125.7%] |
| holdout | market_physics_edge_ge_0.02_ask_50_70 | 1 | 1 | +100.0% | 0.520 | +2.5% | +92.3% | [NA, NA] |
| holdout | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | NA | [NA, NA] |
| holdout | physics_edge_ge_0.02 | 591 | 20 | +54.5% | 0.538 | +17.1% | +1.3% | [-10.0%, +12.1%] |
| holdout | physics_edge_ge_0.02_ask_50_70 | 244 | 20 | +60.2% | 0.588 | +12.5% | +2.4% | [-9.8%, +14.3%] |
| holdout | physics_edge_ge_0.05 | 508 | 20 | +51.6% | 0.515 | +19.2% | +0.2% | [-9.9%, +10.7%] |
| train | buy_all_peak_yes | 935 | 13 | +71.4% | 0.748 | -0.0% | -4.5% | [-9.7%, +1.2%] |
| train | market_physics_edge_ge_0.00 | 45 | 11 | +91.1% | 0.856 | +0.5% | +6.4% | [-4.2%, +14.1%] |
| train | market_physics_edge_ge_0.02 | 0 | 0 | NA | NA | NA | NA | [NA, NA] |
| train | market_physics_edge_ge_0.02_ask_50_70 | 0 | 0 | NA | NA | NA | NA | [NA, NA] |
| train | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | NA | [NA, NA] |
| train | physics_edge_ge_0.02 | 318 | 13 | +49.4% | 0.538 | +16.2% | -8.2% | [-20.6%, +4.1%] |
| train | physics_edge_ge_0.02_ask_50_70 | 138 | 12 | +55.8% | 0.587 | +12.0% | -5.0% | [-16.4%, +6.1%] |
| train | physics_edge_ge_0.05 | 280 | 13 | +49.3% | 0.518 | +18.0% | -4.9% | [-18.3%, +8.2%] |

## Component Audit

| period | component | rows | AUC break | corr | mean |
|---|---|---:|---:|---:|---:|
| forward | comp_moisture_prior | 219 | 0.669 | 0.277 | 0.479 |
| forward | comp_warming_momentum | 219 | 0.652 | 0.208 | 0.263 |
| forward | physics_break_score_raw | 219 | 0.634 | 0.217 | 0.475 |
| forward | comp_intraday_prior | 219 | 0.621 | 0.195 | 0.580 |
| forward | comp_dry_clear_reheat | 219 | 0.591 | 0.151 | 0.553 |
| forward | comp_fresh_high | 219 | 0.590 | 0.148 | 0.595 |
| forward | comp_forecast_peak_ahead | 219 | 0.584 | 0.152 | 0.203 |
| forward | comp_running_max_prior | 219 | 0.575 | 0.137 | 0.501 |
| forward | comp_not_faded | 219 | 0.557 | 0.120 | 0.800 |
| forward | comp_solar_remaining | 219 | 0.532 | 0.070 | 0.475 |
| forward | comp_forecast_disagreement | 219 | 0.500 | -0.000 | 0.450 |
| forward | comp_forecast_runway | 219 | 0.497 | -0.035 | 0.451 |

## Score Bins

| period | bin | rows | break | p_physics | market break | avg ask | ROI all |
|---|---:|---:|---:|---:|---:|---:|---:|
| holdout | 0 | 327 | +14.7% | +17.3% | +16.3% | 0.837 | +2.0% |
| holdout | 1 | 326 | +25.5% | +22.6% | +22.9% | 0.771 | -3.3% |
| holdout | 2 | 326 | +23.9% | +27.4% | +25.4% | 0.746 | +1.9% |
| holdout | 3 | 326 | +36.8% | +32.5% | +29.9% | 0.701 | -9.8% |
| holdout | 4 | 327 | +41.9% | +41.4% | +37.0% | 0.630 | -7.8% |
| forward | 0 | 44 | +13.6% | +19.5% | +18.0% | 0.820 | +5.3% |
| forward | 1 | 44 | +22.7% | +26.2% | +20.5% | 0.795 | -2.7% |
| forward | 2 | 43 | +23.3% | +30.5% | +22.5% | 0.775 | -1.0% |
| forward | 3 | 44 | +43.2% | +35.4% | +30.3% | 0.697 | -18.5% |
| forward | 4 | 44 | +36.4% | +41.0% | +33.7% | 0.663 | -4.1% |

## Regime Diagnostics

| period | kind | regime | rows | break | avg ask | ROI all | selected rows | selected ROI |
|---|---|---|---:|---:|---:|---:|---:|---:|
| forward | day_regime | day_space_unknown | 99 | +35.4% | 0.745 | -13.2% | 0 | NA |
| forward | day_regime | day_open_runway | 39 | +15.4% | 0.704 | +20.3% | 0 | NA |
| forward | day_regime | day_marginal_runway | 30 | +26.7% | 0.764 | -4.0% | 0 | NA |
| forward | day_regime | day_forecast_busted | 27 | +22.2% | 0.769 | +1.2% | 0 | NA |
| forward | day_regime | day_forecast_capped | 24 | +25.0% | 0.808 | -7.1% | 0 | NA |
| forward | intraday_state | active_warming | 89 | +38.2% | 0.714 | -13.5% | 0 | NA |
| forward | intraday_state | fresh_high | 49 | +26.5% | 0.752 | -2.4% | 0 | NA |
| forward | moisture_cloud_regime | mixed_moisture | 99 | +22.2% | 0.743 | +4.6% | 0 | NA |
| forward | moisture_cloud_regime | dry_heat_inertia | 49 | +46.9% | 0.752 | -29.4% | 0 | NA |
| forward | moisture_cloud_regime | humid_convective_risk | 30 | +23.3% | 0.746 | +2.7% | 0 | NA |
| forward | moisture_cloud_regime | humid_overcast_suppression | 22 | +0.0% | 0.803 | +24.5% | 0 | NA |
| forward | running_max_state | fresh_running_high | 118 | +32.2% | 0.740 | -8.3% | 0 | NA |
| forward | running_max_state | running_max_clock_unknown | 42 | +28.6% | 0.757 | -5.6% | 0 | NA |
| forward | running_max_state | mature_fade | 28 | +10.7% | 0.799 | +11.8% | 0 | NA |
| holdout | day_regime | day_forecast_capped | 556 | +23.4% | 0.746 | +2.8% | 0 | NA |
| holdout | day_regime | day_forecast_busted | 544 | +22.8% | 0.768 | +0.5% | 2 | +107.7% |
| holdout | day_regime | day_marginal_runway | 335 | +40.9% | 0.700 | -15.6% | 0 | NA |
| holdout | day_regime | day_open_runway | 197 | +38.1% | 0.689 | -10.1% | 0 | NA |
| holdout | intraday_state | active_warming | 570 | +33.3% | 0.702 | -5.0% | 0 | NA |
| holdout | intraday_state | fresh_high | 425 | +26.8% | 0.738 | -0.8% | 0 | NA |
| holdout | intraday_state | plateau_near_high | 224 | +28.6% | 0.735 | -2.8% | 0 | NA |
| holdout | intraday_state | false_fade_risk | 147 | +27.9% | 0.731 | -1.3% | 0 | NA |
| holdout | intraday_state | mature_fade | 128 | +19.5% | 0.828 | -2.8% | 2 | +107.7% |
| holdout | intraday_state | pullback_uncertain | 78 | +14.1% | 0.815 | +5.4% | 0 | NA |

## Verdict

significance=FAIL / baseline=FAIL / forward=FAIL / conclusion=inconclusive

The improved first-principles score is useful as a diagnostic and shadow feature, especially for rejecting open-runway/active-warming peak YES. It does not yet justify live trading because market remains the stronger probability baseline and holdout EV confidence intervals cross zero. The next improvement should target missing mechanism features, not more hard filters: true solar altitude, intra-hour observation cadence, forecast curve slope, and cloud/wind change after the high.

## Outputs

- scored_rows: `docs/analysis/2026-06/generated/current_yes_peak_yes_first_principles_v3/peak_yes_first_principles_v3_scored_rows.csv`
- model_metrics: `docs/analysis/2026-06/generated/current_yes_peak_yes_first_principles_v3/peak_yes_first_principles_v3_model_metrics.csv`
- ev_rules: `docs/analysis/2026-06/generated/current_yes_peak_yes_first_principles_v3/peak_yes_first_principles_v3_ev_rules.csv`
- component_audit: `docs/analysis/2026-06/generated/current_yes_peak_yes_first_principles_v3/peak_yes_first_principles_v3_component_audit.csv`
- score_bins: `docs/analysis/2026-06/generated/current_yes_peak_yes_first_principles_v3/peak_yes_first_principles_v3_score_bins.csv`
- regime_diagnostics: `docs/analysis/2026-06/generated/current_yes_peak_yes_first_principles_v3/peak_yes_first_principles_v3_regime_diagnostics.csv`
- json: `docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-first-principles-v3.json`
- markdown: `docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-first-principles-v3.md`
