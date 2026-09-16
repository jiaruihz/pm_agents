# Current-YES Peak-YES Mechanism v4

Status: research-only
Generated: 2026-07-01T02:13:33+00:00

## 一句话结论

Mechanism v4 adds the missing physical features, but the hand-weighted score does not beat v3 or market and still does not create tradable peak-YES edge: holdout physics AUC 0.630 vs market 0.749; market+mechanism AUC 0.750 holdout and 0.794 forward. Learned components do not rescue it: holdout component AUC 0.675, market+components AUC 0.747 holdout / 0.779 forward. `physics edge>=2%` holdout ROI +0.8%, CI [-10.2%, +11.8%], forward ROI -17.6%. `market+mechanism edge>=0` holdout is 51 rows ROI +7.7%; `market+components edge>=0` is 778 rows ROI +2.6%.

## 数据范围

- Atlas rows: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
- Feature factory rows: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260519_20260620/reheat_feature_rows.csv, docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260621_20260623/reheat_feature_rows.csv, docs/analysis/2026-06/generated/current_bracket_no_20260624_feature_factory/reheat_feature_rows.csv, docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260625_20260628/reheat_feature_rows.csv, docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260629_20260630/reheat_feature_rows.csv`
- IEM ext cache: `docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v7_20260617, docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v9_20260623, docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v10_20260624, docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v11_20260628, docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v12_20260630`
- Tradable peak-YES rows: 2997 / dates 39 / cities 36
- DB fact refresh: `2026-07-01T02:11:30.274017+00:00`

V4 adds solar geometry, observation cadence, forecast slope-to-peak proxy, and cloud/wind/drying tendency features. Regimes remain continuous priors, not hard gates.

## Model Metrics

| period | model | rows | break | pred break | AUC | Brier | logloss |
|---|---|---:|---:|---:|---:|---:|---:|
| train | market_implied_break | 935 | +28.6% | +25.2% | 0.780 | 0.165 | 0.497 |
| train | mechanism_v4_physics | 935 | +28.6% | +28.6% | 0.656 | 0.192 | 0.568 |
| train | market_plus_mechanism_v4 | 935 | +28.6% | +28.6% | 0.783 | 0.162 | 0.491 |
| train | learned_components_l2 | 935 | +28.6% | +28.6% | 0.791 | 0.154 | 0.472 |
| train | market_plus_components_l2 | 935 | +28.6% | +28.6% | 0.839 | 0.140 | 0.431 |
| holdout | market_implied_break | 1632 | +28.6% | +26.3% | 0.749 | 0.173 | 0.519 |
| holdout | mechanism_v4_physics | 1632 | +28.6% | +28.4% | 0.630 | 0.196 | 0.577 |
| holdout | market_plus_mechanism_v4 | 1632 | +28.6% | +29.7% | 0.750 | 0.172 | 0.517 |
| holdout | learned_components_l2 | 1632 | +28.6% | +30.0% | 0.675 | 0.199 | 0.593 |
| holdout | market_plus_components_l2 | 1632 | +28.6% | +29.2% | 0.747 | 0.179 | 0.537 |
| forward | market_implied_break | 430 | +32.8% | +26.4% | 0.792 | 0.178 | 0.521 |
| forward | mechanism_v4_physics | 430 | +32.8% | +29.8% | 0.623 | 0.213 | 0.614 |
| forward | market_plus_mechanism_v4 | 430 | +32.8% | +30.1% | 0.794 | 0.173 | 0.510 |
| forward | learned_components_l2 | 430 | +32.8% | +32.1% | 0.625 | 0.222 | 0.643 |
| forward | market_plus_components_l2 | 430 | +32.8% | +30.0% | 0.779 | 0.176 | 0.528 |

## EV Rules

| period | rule | rows | dates | win | avg ask | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|
| forward | buy_all_peak_yes | 430 | 6 | +67.2% | 0.736 | -8.7% | [-16.7%, -0.1%] |
| forward | components_l2_edge_ge_0.02 | 158 | 6 | +55.1% | 0.602 | -8.5% | [-28.4%, +6.6%] |
| forward | market_components_l2_edge_ge_0.00 | 187 | 6 | +68.4% | 0.714 | -4.1% | [-17.0%, +8.0%] |
| forward | market_components_l2_edge_ge_0.02 | 151 | 6 | +66.9% | 0.688 | -2.8% | [-16.4%, +9.5%] |
| forward | market_physics_edge_ge_0.00 | 4 | 2 | +100.0% | 0.883 | +13.3% | [+11.1%, +14.1%] |
| forward | market_physics_edge_ge_0.02 | 0 | 0 | NA | NA | NA | [NA, NA] |
| forward | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | [NA, NA] |
| forward | physics_edge_ge_0.02 | 142 | 6 | +42.3% | 0.513 | -17.6% | [-33.7%, -1.7%] |
| forward | physics_edge_ge_0.02_ask_50_70 | 60 | 6 | +41.7% | 0.583 | -28.5% | [-42.0%, -17.1%] |
| forward | physics_edge_ge_0.05 | 124 | 6 | +41.1% | 0.492 | -16.3% | [-37.9%, +0.1%] |
| holdout | buy_all_peak_yes | 1632 | 20 | +71.4% | 0.737 | -3.0% | [-8.4%, +2.2%] |
| holdout | components_l2_edge_ge_0.02 | 638 | 20 | +67.7% | 0.648 | +4.5% | [-3.5%, +11.5%] |
| holdout | market_components_l2_edge_ge_0.00 | 778 | 20 | +77.1% | 0.752 | +2.6% | [-2.7%, +7.7%] |
| holdout | market_components_l2_edge_ge_0.02 | 639 | 20 | +75.3% | 0.726 | +3.7% | [-2.3%, +9.5%] |
| holdout | market_physics_edge_ge_0.00 | 51 | 18 | +90.2% | 0.837 | +7.7% | [-4.8%, +17.0%] |
| holdout | market_physics_edge_ge_0.02 | 0 | 0 | NA | NA | NA | [NA, NA] |
| holdout | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | [NA, NA] |
| holdout | physics_edge_ge_0.02 | 577 | 20 | +53.6% | 0.531 | +0.8% | [-10.2%, +11.8%] |
| holdout | physics_edge_ge_0.02_ask_50_70 | 240 | 20 | +59.2% | 0.587 | +0.8% | [-11.6%, +13.4%] |
| holdout | physics_edge_ge_0.05 | 501 | 20 | +50.9% | 0.509 | -0.1% | [-10.5%, +10.7%] |
| train | buy_all_peak_yes | 935 | 13 | +71.4% | 0.748 | -4.5% | [-9.7%, +1.2%] |
| train | components_l2_edge_ge_0.02 | 354 | 13 | +72.3% | 0.663 | +9.1% | [+3.6%, +14.8%] |
| train | market_components_l2_edge_ge_0.00 | 452 | 13 | +82.3% | 0.768 | +7.2% | [+3.6%, +11.1%] |
| train | market_components_l2_edge_ge_0.02 | 368 | 13 | +82.1% | 0.744 | +10.2% | [+6.8%, +13.8%] |
| train | market_physics_edge_ge_0.00 | 33 | 10 | +100.0% | 0.888 | +12.6% | [+8.7%, +17.0%] |
| train | market_physics_edge_ge_0.02 | 0 | 0 | NA | NA | NA | [NA, NA] |
| train | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | [NA, NA] |
| train | physics_edge_ge_0.02 | 315 | 13 | +49.5% | 0.535 | -7.3% | [-19.3%, +4.6%] |
| train | physics_edge_ge_0.02_ask_50_70 | 140 | 12 | +57.1% | 0.588 | -2.9% | [-12.0%, +6.5%] |
| train | physics_edge_ge_0.05 | 276 | 13 | +47.8% | 0.514 | -7.0% | [-20.6%, +6.2%] |

## Feature Coverage

| period | feature | coverage |
|---|---|---:|
| forward | solar_altitude_deg | +100.0% |
| forward | decision_obs_age_min | +100.0% |
| forward | obs_per_elapsed_hour | +100.0% |
| forward | forecast_slope_to_peak_native_per_h | +50.0% |
| forward | d_sky_3h | +79.3% |
| forward | d_sknt_3h | +99.8% |
| forward | d_relh_3h | +99.8% |
| forward | cloud_clearing_x_solar | +79.3% |
| holdout | solar_altitude_deg | +100.0% |
| holdout | decision_obs_age_min | +100.0% |
| holdout | obs_per_elapsed_hour | +100.0% |
| holdout | forecast_slope_to_peak_native_per_h | +100.0% |
| holdout | d_sky_3h | +67.4% |
| holdout | d_sknt_3h | +87.3% |
| holdout | d_relh_3h | +87.3% |
| holdout | cloud_clearing_x_solar | +67.4% |
| train | solar_altitude_deg | +100.0% |
| train | decision_obs_age_min | +100.0% |
| train | obs_per_elapsed_hour | +100.0% |
| train | forecast_slope_to_peak_native_per_h | +100.0% |
| train | d_sky_3h | +73.6% |
| train | d_sknt_3h | +99.8% |
| train | d_relh_3h | +99.8% |
| train | cloud_clearing_x_solar | +73.6% |

## Component Audit

| period | component | rows | AUC break | corr | mean |
|---|---|---:|---:|---:|---:|
| forward | comp_moisture_prior | 430 | 0.626 | 0.233 | 0.486 |
| forward | physics_break_score_raw | 430 | 0.623 | 0.207 | 0.455 |
| forward | comp_warming_momentum | 430 | 0.601 | 0.141 | 0.270 |
| forward | comp_drying_solar | 430 | 0.579 | 0.130 | 0.140 |
| forward | comp_forecast_slope_to_peak | 430 | 0.570 | 0.098 | 0.444 |
| forward | comp_dry_clear_reheat | 430 | 0.565 | 0.106 | 0.559 |
| forward | comp_intraday_prior | 430 | 0.563 | 0.126 | 0.582 |
| forward | comp_solar_remaining | 430 | 0.563 | 0.112 | 0.484 |
| forward | comp_solar_geometry | 430 | 0.559 | 0.108 | 0.567 |
| forward | comp_day_regime_prior | 430 | 0.552 | 0.092 | 0.546 |
| forward | comp_forecast_runway | 430 | 0.546 | 0.068 | 0.446 |
| forward | comp_cloud_clearing_solar | 430 | 0.530 | 0.060 | 0.107 |
| forward | comp_fresh_high | 430 | 0.530 | 0.060 | 0.654 |
| forward | comp_forecast_peak_ahead | 430 | 0.529 | 0.049 | 0.230 |
| forward | comp_running_max_prior | 430 | 0.527 | 0.039 | 0.525 |
| forward | comp_not_faded | 430 | 0.516 | 0.029 | 0.804 |
| forward | comp_forecast_disagreement | 430 | 0.500 | 0.000 | 0.450 |
| forward | comp_obs_cadence_risk | 430 | 0.463 | -0.034 | 0.444 |

## Verdict

significance=FAIL / baseline=FAIL / forward=FAIL / conclusion=inconclusive

The added mechanism features help diagnosis but do not overturn the v3 conclusion. A learned component model has a better point estimate than the hand-weighted score, but holdout ROI confidence intervals still cross zero and forward support is only three dates. Solar/cadence/cloud-wind features should stay in forward logging; peak-YES live entry still needs either better price/timing execution or a materially stronger residual signal than current PIT data provides.

## Outputs

- scored_rows: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_scored_rows.csv`
- model_metrics: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_model_metrics.csv`
- ev_rules: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_ev_rules.csv`
- component_audit: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_component_audit.csv`
- score_bins: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_score_bins.csv`
- feature_coverage: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_feature_coverage.csv`
- json: `docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-mechanism-v4.json`
- markdown: `docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-mechanism-v4.md`
