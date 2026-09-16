# Current YES Peak-Forming Hazard Model v1

Status: `historical / superseded_for_decision_use`
Generated: 2026-06-18T16:53:07+00:00

> ⚠️ Historical result only. This report preserves the original experiment,
> but its headline metrics must not be used for current model selection. It
> predates the longer canonical reruns and the later corrections to assigned
> forecast lineage, city-day weighting, settlement-native lattice and physical
> clock semantics. Read the 2026-07-21 overshoot-edge v2 and 2026-07-27
> core-carry missing-mechanisms v2 reports for the current negative result:
> hazard/physics overlays did not stably beat market or the frozen core.

Target metric: `current_yes_peak_forming_hazard_v1` = 当前温度仍在 running max 附近时，预测当前最高温 bracket 是否最终守住；等价地，预测后面会不会出现更高温打穿。

## Human Summary

这版不是把旧模型再套几条 if/else。它单独训练 peak-forming 状态，用盘口、METAR 温湿风云/温度趋势、forecast peak clock 和 GFS/ECMWF 最高温缺口一起估计 `p_survive`。

结果的重点比较清楚：模型判别力有提升，但交易层仍没有到可以替换 live 的程度。尤其是高 ask 区域市场本来就接近校准，真正能留下 edge 的样本变薄。

## 数据快照

- 数据源: `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv` + `runtime/weather.db` self-check
- 数据快照时间: fact_trades max built at `2026-06-18T16:42:11.906813+00:00`
- 特征产出日期: `2026-05-19`..`2026-06-14`；记录行数: source rows 88621, current-YES peak rows 4243
- unsettled 占比: feature factory peak rows are settled-only; fact self-check `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`
- missing_bracket 数: feature factory reads `settlement_outcomes`; no missing rows in this peak slice.
- 数据缺口: 本机已同步 6/18 orderbook/pm_history 并重建 DB，但 `reheat_feature_factory_v1` 的 observed-detail 输入仍只物化到 6/14，所以本报告没有把 6/15..6/17 纳入训练/holdout。

## Funnel

| step | rows | dates | cities |
|---|---:|---:|---:|
| all reheat feature rows | 88621 | 27 | 36 |
| current YES states | 7104 | 27 | 36 |
| peak-forming states | 4243 | 27 | 36 |
| train peak states | 2055 | 13 | 36 |
| holdout peak states | 2188 | 14 | 36 |

Row grain: one row is one city + target date + orderbook snapshot + current running-max bracket, not one fill.

## Holdout Model Metrics

| model | rows | actual survive | mean p | AUC | Brier | avg edge vs ask |
|---|---:|---:|---:|---:|---:|---:|
| market_price_as_probability | 2188 | 41.8% | 43.5% | 0.946 | 0.092 | 0.0% |
| weather_forecast_only_v1 | 2188 | 41.8% | 41.6% | 0.905 | 0.124 | -2.0% |
| base_current_yes_v9 | 2188 | 41.8% | 40.6% | 0.938 | 0.097 | -2.9% |
| peak_forming_hazard_v1 | 2188 | 41.8% | 42.1% | 0.946 | 0.091 | -1.4% |

## Holdout Trading Rules

| rule | orders | dates | cities | avg ask | win rate | ROI | 95% date bootstrap ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_price_only_tradable | 435 | 14 | 36 | 0.828 | 80.5% | -2.9% | [-10.0%, 4.5%] |
| base_v9_peak_rule | 181 | 14 | 26 | 0.805 | 82.3% | 2.3% | [-10.4%, 13.3%] |
| base_v9_peak_rule_plus_approx_guard | 25 | 10 | 8 | 0.769 | 76.0% | -1.2% | [-34.3%, 25.1%] |
| hazard_v1_peak_rule | 233 | 14 | 33 | 0.807 | 82.0% | 1.6% | [-7.4%, 10.9%] |
| hazard_v1_peak_rule_plus_approx_guard | 27 | 11 | 8 | 0.796 | 70.4% | -11.6% | [-44.9%, 15.9%] |
| train_selected_grid_h13_p0.75_edge0.08 | 109 | 14 | 26 | 0.765 | 80.7% | 5.6% | [-8.3%, 17.6%] |
| train_selected_grid_h15_p0.55_edge0.08 | 59 | 14 | 20 | 0.766 | 72.9% | -4.9% | [-20.4%, 10.6%] |
| train_selected_grid_h15_p0.60_edge0.08 | 59 | 14 | 20 | 0.766 | 72.9% | -4.9% | [-20.4%, 10.6%] |

## Verdict

significance=FAIL / baseline=PASS / forward=PASS / conclusion=inconclusive

在 2026-06-01..2026-06-14 holdout，peak-forming hazard v1 的主交易规则 ROI 为 1.6%，95% 日期 bootstrap CI [-7.4%, 10.9%]；approx guard 后 ROI 为 -11.6%。它可以继续作为 shadow/研究概率层，但还没有满足显著性和前瞻门，暂不替换 live。

## Outputs

- artifact: `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v1/peak_forming_hazard_model.json`
- scored rows: `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v1/peak_forming_scored_rows.csv`
- metrics: `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v1/model_metrics.csv`
- rule comparison: `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v1/rule_comparison.csv`
- json: `docs/analysis/2026-06/2026-06-19-current-yes-peak-forming-hazard-v1.json`
