# Current YES Peak-Forming Hazard Model v2

Status: `historical / superseded_for_decision_use`
Generated: 2026-06-21T05:18:27+00:00

> ⚠️ Historical result only. Plateau features made this version semantically
> richer than v1, but it remains an early-window experiment and already failed
> its forward gate. Later canonical, longer-window reruns corrected forecast
> lineage, city-day weighting, native settlement lattice and clock semantics;
> they still found no stable hazard increment over market/frozen core. Do not
> use this report to justify a live filter, V4 model or sizing change.

Target metric: `current_yes_peak_forming_hazard_v2` = 当前温度仍在 running max 附近时，预测当前最高温 bracket 是否最终守住；等价地，预测后面会不会出现更高温打穿。

## Human Summary

这版不是把旧模型再套几条 if/else。它单独训练 peak-forming 状态，用盘口、METAR 温湿风云/温度趋势、forecast peak clock、GFS/ECMWF 最高温缺口，以及 `plateau_obs_count_at_high / plateau_duration_min / running_max_first_obs_hour_local` 一起估计 `p_survive`。

结果的重点比较清楚：模型判别力有提升，但交易层仍没有到可以替换 live 的程度。尤其是高 ask 区域市场本来就接近校准，真正能留下 edge 的样本变薄。

## 数据快照

- 数据源: `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv` + `runtime/weather.db` self-check
- 数据快照时间: fact_trades max built at `2026-06-21T05:04:12.724103+00:00`
- 特征产出日期: `2026-05-19`..`2026-06-17`；记录行数: source rows 99819, current-YES peak rows 4871
- unsettled 占比: feature factory peak rows are settled-only; fact self-check `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`
- missing_bracket 数: feature factory reads `settlement_outcomes`; no missing rows in this peak slice.
- 数据范围: 本报告使用 `reheat_feature_factory_v1` 当前物化范围 `2026-05-19`..`2026-06-17`；holdout peak states 为 `2026-06-01`..`2026-06-17` 内可形成 peak slice 的行。

## Funnel

| step | rows | dates | cities |
|---|---:|---:|---:|
| all reheat feature rows | 99819 | 30 | 36 |
| current YES states | 8095 | 30 | 36 |
| peak-forming states | 4871 | 30 | 36 |
| train peak states | 2055 | 13 | 36 |
| holdout peak states | 2816 | 17 | 36 |

Row grain: one row is one city + target date + orderbook snapshot + current running-max bracket, not one fill.

## Holdout Model Metrics

| model | rows | actual survive | mean p | AUC | Brier | avg edge vs ask |
|---|---:|---:|---:|---:|---:|---:|
| market_price_as_probability | 2816 | 41.1% | 43.7% | 0.945 | 0.094 | 0.0% |
| weather_forecast_only_v1 | 2816 | 41.1% | 42.0% | 0.906 | 0.123 | -1.7% |
| base_current_yes_v9 | 2816 | 41.1% | 40.8% | 0.935 | 0.099 | -2.9% |
| peak_forming_hazard_v2 | 2816 | 41.1% | 42.1% | 0.942 | 0.095 | -1.6% |

## Holdout Trading Rules

| rule | orders | dates | cities | avg ask | win rate | ROI | 95% date bootstrap ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_price_only_tradable | 559 | 17 | 36 | 0.826 | 78.4% | -5.2% | [-11.5%, 1.7%] |
| base_v9_peak_rule | 234 | 17 | 29 | 0.803 | 79.5% | -1.0% | [-12.1%, 9.5%] |
| base_v9_peak_rule_plus_approx_guard | 36 | 13 | 9 | 0.772 | 72.2% | -6.5% | [-28.0%, 11.9%] |
| hazard_v2_peak_rule | 283 | 17 | 35 | 0.813 | 80.6% | -0.9% | [-8.9%, 7.5%] |
| hazard_v2_peak_rule_plus_approx_guard | 22 | 11 | 7 | 0.787 | 72.7% | -7.6% | [-38.4%, 19.7%] |
| hazard_v2_stalled_peak_rule | 133 | 17 | 30 | 0.800 | 79.7% | -0.3% | [-12.1%, 10.5%] |
| hazard_v2_stalled_peak_rule_plus_approx_guard | 17 | 10 | 7 | 0.790 | 64.7% | -18.1% | [-52.0%, 16.3%] |
| train_selected_grid_h14_p0.70_edge0.08 | 102 | 17 | 26 | 0.760 | 75.5% | -0.7% | [-16.5%, 13.9%] |
| train_selected_grid_h13_p0.70_edge0.08 | 125 | 17 | 28 | 0.761 | 76.0% | -0.1% | [-15.9%, 14.0%] |
| train_selected_grid_h13_p0.75_edge0.08 | 121 | 17 | 28 | 0.767 | 77.7% | 1.3% | [-14.6%, 15.1%] |

## Verdict

significance=FAIL / baseline=PASS / forward=FAIL / conclusion=inconclusive

在 2026-06-01..2026-06-17 holdout，peak-forming hazard v2 的主交易规则 ROI 为 -0.9%，95% 日期 bootstrap CI [-8.9%, 7.5%]；approx guard 后 ROI 为 -7.6%。它可以继续作为 shadow/研究概率层，但还没有满足显著性和前瞻门，暂不替换 live。

## Outputs

- artifact: `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v2/peak_forming_hazard_plateau_model.json`
- scored rows: `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v2/peak_forming_plateau_scored_rows.csv`
- metrics: `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v2/model_metrics.csv`
- rule comparison: `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v2/rule_comparison.csv`
- json: `docs/analysis/2026-06/2026-06-21-current-yes-peak-forming-hazard-v2.json`
