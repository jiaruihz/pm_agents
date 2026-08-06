# Current-YES No-Reheat Hazard Score v1

Status: `historical / superseded_for_decision_use`
Generated: 2026-06-24T03:40:18+00:00

> ⚠️ Historical negative experiment. The physical score was interpretable but
> substantially worse than market, and the report's proposed missing features
> were later tested in richer solar/runway/regime and native-lattice studies
> without stable incremental proper-score improvement. Those features may
> remain telemetry; this document must not be read as an unfinished positive
> model awaiting a few more conditions.

## 一句话结论

第一性原理 hazard score 能做出可解释排序，但当前 v1 没打赢 market baseline：holdout AUC 0.616 vs market 0.747，`edge>=2%` 选 584 rows，ROI -1.3%，CI [-11.8%, +9.3%]。

## 数据范围

- Feature rows: `docs/analysis/2026-06/generated/current_yes_future_break_hazard_v3/factory/reheat_feature_rows.csv`
- Feature target-date range: `2026-05-19`..`2026-06-20`
- Current-YES rows: 8907 / tradable rows: 2469
- Train: `<= 2026-05-31`; holdout: `2026-06-01`..`2026-06-20`
- DB fact refresh: `2026-06-24T03:35:05.597215+00:00`; CLOB gate not used for these replay rows.

## First-Principles Score

目标是连续估计 `p_future_break`，不是继续堆 hard filter。v1 只把方向体检后同号的物理成分放进 hazard：剩余加热时间、forecast ceiling gap、仍在升温、刚摸高、未充分 fade、plateau 时间短。`forecast_peak_ahead`、`plateau_not_confirmed`、`stale_obs`、`wide_spread` 在这批数据里方向弱或反向，不进物理 score。

```json
{
  "comp_remaining_heat": 1.15,
  "comp_forecast_gap": 1.35,
  "comp_warming_trend": 1.15,
  "comp_fresh_high": 0.35,
  "comp_not_faded": 0.25,
  "comp_short_plateau": 0.2
}
```

## Component Audit

| period | component | used | AUC | corr | mean |
|---|---|---:|---:|---:|---:|
| train | comp_remaining_heat | Y | 0.605 | 0.169 | 0.460 |
| train | comp_forecast_gap | Y | 0.571 | 0.097 | 0.447 |
| train | comp_forecast_peak_ahead | N | 0.392 | -0.197 | 0.120 |
| train | comp_plateau_not_confirmed | N | 0.475 | -0.029 | 0.702 |
| train | comp_short_plateau | Y | 0.515 | 0.029 | 0.767 |
| train | comp_fresh_high | Y | 0.522 | 0.039 | 0.495 |
| train | comp_warming_trend | Y | 0.633 | 0.203 | 0.283 |
| train | comp_not_faded | Y | 0.548 | 0.091 | 0.805 |
| train | comp_stale_obs | N | 0.470 | -0.059 | 0.759 |
| train | comp_wide_spread | N | 0.592 | 0.144 | 0.599 |
| holdout | comp_remaining_heat | Y | 0.567 | 0.106 | 0.483 |
| holdout | comp_forecast_gap | Y | 0.574 | 0.113 | 0.396 |
| holdout | comp_forecast_peak_ahead | N | 0.417 | -0.181 | 0.097 |
| holdout | comp_plateau_not_confirmed | N | 0.493 | -0.002 | 0.683 |
| holdout | comp_short_plateau | Y | 0.518 | 0.034 | 0.746 |
| holdout | comp_fresh_high | Y | 0.518 | 0.037 | 0.468 |
| holdout | comp_warming_trend | Y | 0.582 | 0.121 | 0.283 |
| holdout | comp_not_faded | Y | 0.532 | 0.069 | 0.812 |
| holdout | comp_stale_obs | N | 0.508 | 0.005 | 0.754 |
| holdout | comp_wide_spread | N | 0.623 | 0.196 | 0.583 |

## Probability Metrics

| period | model | rows | break | pred break | AUC | Brier | logloss |
|---|---|---:|---:|---:|---:|---:|---:|
| train | market_implied_break | 894 | +28.5% | +25.0% | 0.784 | 0.163 | 0.493 |
| train | first_principles_mechanism | 894 | +28.5% | +28.5% | 0.639 | 0.193 | 0.572 |
| holdout | market_implied_break | 1575 | +28.6% | +26.3% | 0.747 | 0.174 | 0.520 |
| holdout | first_principles_mechanism | 1575 | +28.6% | +27.9% | 0.616 | 0.197 | 0.582 |

## Mechanism Hazard Bins

| period | bin | rows | pred break | actual break | avg ask | buy-YES ROI all |
|---|---:|---:|---:|---:|---:|---:|
| train | 0 | 179 | +17.8% | +17.3% | 0.856 | -3.4% |
| train | 1 | 179 | +23.0% | +22.9% | 0.777 | -0.8% |
| train | 2 | 179 | +27.4% | +24.0% | 0.760 | +0.0% |
| train | 3 | 178 | +32.5% | +34.3% | 0.723 | -9.1% |
| train | 4 | 179 | +41.9% | +44.1% | 0.632 | -11.6% |
| holdout | 0 | 315 | +17.5% | +19.4% | 0.852 | -5.3% |
| holdout | 1 | 315 | +22.1% | +21.9% | 0.781 | -0.0% |
| holdout | 2 | 315 | +26.6% | +27.9% | 0.726 | -0.8% |
| holdout | 3 | 315 | +31.8% | +30.5% | 0.711 | -2.3% |
| holdout | 4 | 315 | +41.6% | +43.2% | 0.614 | -7.4% |

## EV Rules

| period | rule | rows | dates | win | avg ask | avg edge | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| holdout | mech_edge_ge_0.00 | 632 | 20 | +55.4% | 0.555 | +15.4% | -0.3% | [-10.9%, +10.0%] |
| holdout | mech_edge_ge_0.02 | 584 | 20 | +53.6% | 0.543 | +16.6% | -1.3% | [-11.8%, +9.3%] |
| holdout | mech_edge_ge_0.02_ask_0.50_0.70 | 239 | 20 | +58.2% | 0.591 | +12.4% | -1.6% | [-14.9%, +11.7%] |
| holdout | mech_edge_ge_0.05 | 487 | 20 | +51.3% | 0.513 | +19.2% | +0.2% | [-10.2%, +10.4%] |
| holdout | mech_edge_ge_0.08 | 425 | 20 | +48.9% | 0.492 | +21.0% | -0.6% | [-10.9%, +10.6%] |
| holdout | mech_edge_ge_0.10 | 375 | 20 | +47.5% | 0.478 | +22.6% | -0.7% | [-11.8%, +11.6%] |
| train | mech_edge_ge_0.00 | 318 | 13 | +50.0% | 0.548 | +15.4% | -8.7% | [-18.2%, +2.1%] |
| train | mech_edge_ge_0.02 | 294 | 13 | +48.3% | 0.534 | +16.5% | -9.5% | [-20.7%, +2.9%] |
| train | mech_edge_ge_0.02_ask_0.50_0.70 | 132 | 12 | +55.3% | 0.590 | +12.1% | -6.2% | [-16.6%, +5.1%] |
| train | mech_edge_ge_0.05 | 257 | 13 | +48.2% | 0.522 | +18.4% | -7.5% | [-20.5%, +7.4%] |
| train | mech_edge_ge_0.08 | 217 | 13 | +45.6% | 0.498 | +20.6% | -8.4% | [-24.1%, +8.8%] |
| train | mech_edge_ge_0.10 | 198 | 13 | +42.9% | 0.484 | +21.7% | -11.3% | [-26.2%, +5.8%] |

## Verdict

significance=FAIL / baseline=FAIL / forward=NA / conclusion=inconclusive

这说明方向应继续沿连续 hazard 信号改特征，而不是继续加切片门。当前 v1 的物理成分还不够，尤其缺真实太阳高度、小时级 forecast curve、云/风变化和城市 source cadence；交易上不改 live，只输出 scored rows 供 forward shadow 和失败机制复盘。

## Outputs

- scored rows: `docs/analysis/2026-06/generated/current_yes_no_reheat_hazard_score_v1/no_reheat_hazard_score_v1_scored_rows.csv`
- bins CSV: `docs/analysis/2026-06/generated/current_yes_no_reheat_hazard_score_v1/no_reheat_hazard_score_v1_bins.csv`
- rules CSV: `docs/analysis/2026-06/generated/current_yes_no_reheat_hazard_score_v1/no_reheat_hazard_score_v1_ev_rules.csv`
- json: `docs/analysis/2026-06/2026-06-24-current-yes-no-reheat-hazard-score-v1.json`
