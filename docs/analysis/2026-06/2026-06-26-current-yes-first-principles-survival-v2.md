# Current-YES First-Principles Survival V2

Status: `research_only_not_live`
Generated UTC: `2026-06-25T17:01:50+00:00`

## Human Summary

这版不假设一定能打败市场，而是把问题拆成：天气机制能不能解释 `current bracket survives`，
以及这些机制在扣掉盘口以后还剩多少 residual。输入是 v4 已物化的 PIT 物理特征：METAR core、forecast peak clock、
太阳高度、观测 cadence、forecast slope、云/风/湿度变化和 intraday regime；后验字段不进训练。

- Rows: `2786` / dates `2026-05-19`..`2026-06-23` / cities `36`.
- Train: `<= 2026-05-31`; holdout: `2026-06-01`..`2026-06-20`; forward-like: `>= 2026-06-21`.
- Source rows: `docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_scored_rows.csv`.

## Model Metrics

| period | market raw | weather core | mechanism full | market + mechanism |
|---|---|---|---|---|
| holdout | AUC 0.749, Brier 0.173, edge +0.0% | AUC 0.667, Brier 0.200, edge -4.2% | AUC 0.665, Brier 0.213, edge -3.2% | AUC 0.717, Brier 0.196, edge -2.8% |
| forward | AUC 0.757, Brier 0.176, edge +0.0% | AUC 0.684, Brier 0.194, edge +2.7% | AUC 0.714, Brier 0.181, edge +0.8% | AUC 0.777, Brier 0.169, edge +1.4% |

## Trading Sanity Check

| period | rule | rows | dates | avg ask | win | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---|
| holdout | `components_only_edge_ge_0.02` | 660 | 20 | 0.656 | +68.6% | +4.7% | [-4.8%, +12.2%] |
| holdout | `weather_core_edge_ge_0.02` | 622 | 20 | 0.638 | +66.4% | +4.1% | [-4.6%, +11.5%] |
| holdout | `market_plus_components_edge_ge_0.02` | 677 | 20 | 0.723 | +75.0% | +3.8% | [-3.1%, +9.7%] |
| holdout | `mechanism_full_edge_ge_0.05` | 629 | 20 | 0.654 | +67.2% | +2.8% | [-7.3%, +11.2%] |
| holdout | `mechanism_full_edge_ge_0.00` | 831 | 20 | 0.704 | +72.0% | +2.2% | [-5.1%, +8.8%] |
| holdout | `mechanism_full_edge_ge_0.02` | 753 | 20 | 0.686 | +69.9% | +1.8% | [-6.6%, +9.2%] |
| holdout | `physics_market_agree_positive_edge` | 738 | 20 | 0.728 | +73.7% | +1.2% | [-5.8%, +7.7%] |
| forward | `mechanism_full_edge_ge_0.05` | 90 | 3 | 0.633 | +72.2% | +14.1% | [+10.5%, +16.5%] |
| forward | `components_only_edge_ge_0.02` | 84 | 3 | 0.643 | +72.6% | +12.9% | [+10.9%, +15.6%] |
| forward | `market_plus_components_edge_ge_0.02` | 95 | 3 | 0.720 | +80.0% | +11.1% | [+7.3%, +14.6%] |
| forward | `mechanism_full_edge_ge_0.02` | 114 | 3 | 0.676 | +72.8% | +7.7% | [+1.8%, +11.1%] |
| forward | `physics_market_agree_positive_edge` | 108 | 3 | 0.703 | +75.0% | +6.7% | [+2.3%, +10.0%] |
| forward | `market_plus_mechanism_edge_ge_0.02` | 123 | 3 | 0.725 | +77.2% | +6.5% | [+3.4%, +10.2%] |
| forward | `mechanism_full_edge_ge_0.00` | 120 | 3 | 0.685 | +72.5% | +5.9% | [-1.0%, +8.5%] |

## Mechanism Diagnostics

| component | rows | AUC future-break | corr break | corr market residual | mean |
|---|---:|---:|---:|---:|---:|
| `comp_forecast_peak_ahead` | 1632 | 0.626 | 0.223 | 0.089 | 0.188 |
| `physics_break_score_raw` | 1632 | 0.623 | 0.195 | 0.039 | 0.421 |
| `comp_day_regime_prior` | 1632 | 0.587 | 0.163 | 0.111 | 0.437 |
| `comp_warming_momentum` | 1632 | 0.580 | 0.114 | 0.015 | 0.268 |
| `comp_forecast_runway` | 1632 | 0.577 | 0.118 | 0.045 | 0.363 |
| `drying_x_solar` | 1425 | 0.575 | 0.117 | 0.002 | 4.456 |
| `comp_solar_geometry` | 1632 | 0.572 | 0.105 | -0.049 | 0.545 |
| `comp_solar_remaining` | 1632 | 0.566 | 0.104 | -0.078 | 0.484 |
| `comp_drying_solar` | 1632 | 0.559 | 0.092 | -0.011 | 0.174 |
| `comp_intraday_prior` | 1632 | 0.559 | 0.103 | 0.035 | 0.565 |
| `forecast_slope_to_peak_native_per_h` | 1632 | 0.548 | -0.029 | -0.017 | 0.886 |
| `comp_forecast_slope_to_peak` | 1632 | 0.543 | 0.028 | 0.006 | 0.380 |

## Physics Probability Buckets

| period | score | bucket | rows | survive | avg ask | model p | buy-all ROI |
|---|---|---|---:|---:|---:|---:|---:|
| holdout | `p_survive_mechanism_full` | `(0.00017, 0.453]` | 327 | +52.6% | 0.626 | 0.243 | -16.0% |
| holdout | `p_survive_mechanism_full` | `(0.453, 0.72]` | 326 | +68.1% | 0.696 | 0.608 | -2.1% |
| holdout | `p_survive_mechanism_full` | `(0.72, 0.86]` | 326 | +70.9% | 0.741 | 0.796 | -4.4% |
| holdout | `p_survive_mechanism_full` | `(0.86, 0.942]` | 326 | +80.7% | 0.787 | 0.906 | +2.6% |
| holdout | `p_survive_mechanism_full` | `(0.942, 0.999]` | 327 | +85.0% | 0.835 | 0.971 | +1.8% |

## Verdict

significance=FAIL / baseline=FAIL / forward=FAIL / conclusion=inconclusive

第一性原理特征确实能解释温度路径，但这次仍没有给出可恢复 live 的 residual edge。
如果要继续，最有价值的不是再加 hard gate，而是把这些机制作为 forward logging 和风险分层，
等更多 settled forward dates 后再检查 `market + mechanism` 是否稳定优于 raw market。

## 8-Ring Coverage

- Covered: signal discrimination, probability calibration, date bootstrap ROI sanity check, market-price baseline, target-date clustered CI.
- Partially covered: execution microstructure uses historical current YES ask only, not live maker/taker fill simulation.
- Not live-covered: capacity, real-time shadow fills, post-2026-06-23 settled labels for this feature layer.

## Outputs

- scored_rows: `docs/analysis/2026-06/generated/current_yes_first_principles_survival_v2/current_yes_first_principles_survival_v2_scored_rows.csv`
- metrics: `docs/analysis/2026-06/generated/current_yes_first_principles_survival_v2/current_yes_first_principles_survival_v2_model_metrics.csv`
- rules: `docs/analysis/2026-06/generated/current_yes_first_principles_survival_v2/current_yes_first_principles_survival_v2_rule_comparison.csv`
- bins: `docs/analysis/2026-06/generated/current_yes_first_principles_survival_v2/current_yes_first_principles_survival_v2_physics_bins.csv`
- components: `docs/analysis/2026-06/generated/current_yes_first_principles_survival_v2/current_yes_first_principles_survival_v2_component_audit.csv`
- summary: `docs/analysis/2026-06/generated/current_yes_first_principles_survival_v2/summary.json`
