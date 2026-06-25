# Current-YES Peak-YES Mechanism v4

Status: research-only
Generated: 2026-06-25T13:39:06+00:00

## 一句话结论

Mechanism v4 adds the missing physical features, but the hand-weighted score does not beat v3 or market and still does not create tradable peak-YES edge: holdout physics AUC 0.623 vs market 0.749; market+mechanism AUC 0.750 holdout and 0.759 forward. Learned components do not rescue it: holdout component AUC 0.672, market+components AUC 0.749 holdout / 0.804 forward. `physics edge>=2%` holdout ROI +0.3%, CI [-10.3%, +10.9%], forward ROI -1.7%. `market+mechanism edge>=0` holdout is 7 rows ROI -4.7%; `market+components edge>=0` is 788 rows ROI +2.9%.

## 数据范围

- Atlas rows: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
- Feature factory rows: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260519_20260620/reheat_feature_rows.csv, docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/feature_factory_20260621_20260623/reheat_feature_rows.csv`
- IEM ext cache: `docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v7_20260617, docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v9_20260623`
- Tradable peak-YES rows: 2786 / dates 36 / cities 36
- DB fact refresh: `2026-06-25T05:08:33.713141+00:00`

V4 adds solar geometry, observation cadence, forecast slope-to-peak proxy, and cloud/wind/drying tendency features. Regimes remain continuous priors, not hard gates.

## Model Metrics

| period | model | rows | break | pred break | AUC | Brier | logloss |
|---|---|---:|---:|---:|---:|---:|---:|
| train | market_implied_break | 935 | +28.6% | +25.2% | 0.780 | 0.165 | 0.497 |
| train | mechanism_v4_physics | 935 | +28.6% | +28.6% | 0.641 | 0.194 | 0.573 |
| train | market_plus_mechanism_v4 | 935 | +28.6% | +28.6% | 0.782 | 0.163 | 0.492 |
| train | learned_components_l2 | 935 | +28.6% | +28.6% | 0.781 | 0.156 | 0.481 |
| train | market_plus_components_l2 | 935 | +28.6% | +28.6% | 0.832 | 0.142 | 0.437 |
| holdout | market_implied_break | 1632 | +28.6% | +26.3% | 0.749 | 0.173 | 0.519 |
| holdout | mechanism_v4_physics | 1632 | +28.6% | +28.3% | 0.623 | 0.197 | 0.579 |
| holdout | market_plus_mechanism_v4 | 1632 | +28.6% | +29.7% | 0.750 | 0.172 | 0.517 |
| holdout | learned_components_l2 | 1632 | +28.6% | +29.9% | 0.672 | 0.199 | 0.592 |
| holdout | market_plus_components_l2 | 1632 | +28.6% | +29.2% | 0.749 | 0.179 | 0.536 |
| forward | market_implied_break | 219 | +27.9% | +25.0% | 0.757 | 0.176 | 0.512 |
| forward | mechanism_v4_physics | 219 | +27.9% | +30.0% | 0.616 | 0.195 | 0.576 |
| forward | market_plus_mechanism_v4 | 219 | +27.9% | +28.6% | 0.759 | 0.174 | 0.509 |
| forward | learned_components_l2 | 219 | +27.9% | +30.5% | 0.694 | 0.186 | 0.554 |
| forward | market_plus_components_l2 | 219 | +27.9% | +28.1% | 0.804 | 0.161 | 0.484 |

## EV Rules

| period | rule | rows | dates | win | avg ask | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|
| forward | buy_all_peak_yes | 219 | 3 | +72.1% | 0.750 | -3.8% | [-16.3%, +8.7%] |
| forward | components_l2_edge_ge_0.02 | 85 | 3 | +69.4% | 0.626 | +11.0% | [+7.0%, +18.9%] |
| forward | market_components_l2_edge_ge_0.00 | 105 | 3 | +77.1% | 0.735 | +5.0% | [-1.5%, +15.6%] |
| forward | market_components_l2_edge_ge_0.02 | 81 | 3 | +74.1% | 0.714 | +3.7% | [-7.0%, +19.4%] |
| forward | market_physics_edge_ge_0.00 | 0 | 0 | NA | NA | NA | [NA, NA] |
| forward | market_physics_edge_ge_0.02 | 0 | 0 | NA | NA | NA | [NA, NA] |
| forward | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | [NA, NA] |
| forward | physics_edge_ge_0.02 | 66 | 3 | +50.0% | 0.509 | -1.7% | [-16.2%, +5.1%] |
| forward | physics_edge_ge_0.02_ask_50_70 | 29 | 3 | +37.9% | 0.585 | -35.2% | [-52.0%, -21.2%] |
| forward | physics_edge_ge_0.05 | 62 | 3 | +48.4% | 0.497 | -2.7% | [-19.9%, +7.7%] |
| holdout | buy_all_peak_yes | 1632 | 20 | +71.4% | 0.737 | -3.0% | [-8.4%, +2.2%] |
| holdout | components_l2_edge_ge_0.02 | 623 | 20 | +67.6% | 0.642 | +5.3% | [-2.4%, +12.2%] |
| holdout | market_components_l2_edge_ge_0.00 | 788 | 20 | +77.0% | 0.749 | +2.9% | [-2.5%, +7.9%] |
| holdout | market_components_l2_edge_ge_0.02 | 641 | 20 | +74.7% | 0.719 | +3.9% | [-2.5%, +9.7%] |
| holdout | market_physics_edge_ge_0.00 | 7 | 6 | +85.7% | 0.899 | -4.7% | [-44.6%, +13.7%] |
| holdout | market_physics_edge_ge_0.02 | 0 | 0 | NA | NA | NA | [NA, NA] |
| holdout | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | [NA, NA] |
| holdout | physics_edge_ge_0.02 | 585 | 20 | +53.3% | 0.531 | +0.3% | [-10.3%, +10.9%] |
| holdout | physics_edge_ge_0.02_ask_50_70 | 250 | 20 | +59.2% | 0.588 | +0.7% | [-11.3%, +13.2%] |
| holdout | physics_edge_ge_0.05 | 505 | 20 | +51.5% | 0.509 | +1.2% | [-9.4%, +11.9%] |
| train | buy_all_peak_yes | 935 | 13 | +71.4% | 0.748 | -4.5% | [-9.7%, +1.2%] |
| train | components_l2_edge_ge_0.02 | 340 | 13 | +70.6% | 0.651 | +8.5% | [+3.7%, +13.3%] |
| train | market_components_l2_edge_ge_0.00 | 441 | 13 | +82.5% | 0.768 | +7.5% | [+3.5%, +11.1%] |
| train | market_components_l2_edge_ge_0.02 | 341 | 13 | +82.1% | 0.738 | +11.3% | [+7.3%, +15.1%] |
| train | market_physics_edge_ge_0.00 | 3 | 3 | +100.0% | 0.936 | +6.8% | [+4.3%, +9.9%] |
| train | market_physics_edge_ge_0.02 | 0 | 0 | NA | NA | NA | [NA, NA] |
| train | market_physics_edge_ge_0.05 | 0 | 0 | NA | NA | NA | [NA, NA] |
| train | physics_edge_ge_0.02 | 323 | 13 | +48.9% | 0.536 | -8.7% | [-20.0%, +2.8%] |
| train | physics_edge_ge_0.02_ask_50_70 | 146 | 12 | +54.8% | 0.588 | -6.9% | [-15.5%, +1.8%] |
| train | physics_edge_ge_0.05 | 277 | 13 | +48.0% | 0.513 | -6.4% | [-20.7%, +8.5%] |

## Feature Coverage

| period | feature | coverage |
|---|---|---:|
| forward | solar_altitude_deg | +100.0% |
| forward | decision_obs_age_min | +100.0% |
| forward | obs_per_elapsed_hour | +100.0% |
| forward | forecast_slope_to_peak_native_per_h | +54.8% |
| forward | d_sky_3h | +78.1% |
| forward | d_sknt_3h | +99.5% |
| forward | d_relh_3h | +99.5% |
| forward | cloud_clearing_x_solar | +78.1% |
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
| forward | comp_moisture_prior | 219 | 0.669 | 0.277 | 0.479 |
| forward | comp_warming_momentum | 219 | 0.652 | 0.208 | 0.263 |
| forward | comp_drying_solar | 219 | 0.626 | 0.182 | 0.138 |
| forward | comp_intraday_prior | 219 | 0.621 | 0.195 | 0.580 |
| forward | physics_break_score_raw | 219 | 0.616 | 0.185 | 0.448 |
| forward | comp_dry_clear_reheat | 219 | 0.591 | 0.151 | 0.553 |
| forward | comp_fresh_high | 219 | 0.590 | 0.148 | 0.595 |
| forward | comp_forecast_peak_ahead | 219 | 0.584 | 0.152 | 0.203 |
| forward | comp_running_max_prior | 219 | 0.575 | 0.137 | 0.501 |
| forward | comp_cloud_clearing_solar | 219 | 0.558 | 0.111 | 0.106 |
| forward | comp_not_faded | 219 | 0.557 | 0.120 | 0.800 |
| forward | comp_solar_geometry | 219 | 0.535 | 0.080 | 0.557 |
| forward | comp_solar_remaining | 219 | 0.532 | 0.070 | 0.475 |
| forward | comp_obs_cadence_risk | 219 | 0.507 | 0.049 | 0.445 |
| forward | comp_forecast_disagreement | 219 | 0.500 | -0.000 | 0.450 |
| forward | comp_forecast_runway | 219 | 0.497 | -0.035 | 0.451 |
| forward | comp_forecast_slope_to_peak | 219 | 0.488 | -0.046 | 0.462 |
| forward | comp_day_regime_prior | 219 | 0.464 | -0.044 | 0.547 |

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
