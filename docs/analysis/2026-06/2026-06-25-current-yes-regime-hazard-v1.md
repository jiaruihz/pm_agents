# Current-YES Regime Hazard V1

Status: `historical / superseded_for_decision_use`

> ⚠️ Historical branch prototype. The structure “regime as a continuous model
> feature, not a hard gate” remains valid, but the three-date forward-like
> slice was not independent evidence. Later D1/current-YES same-denominator
> studies found that adding regime to a market residual was neutral or worse.
> Do not restore this shadow branch or build a live route from its headline
> forward numbers.

## Human Summary

这版是 current-YES 的重构原型：不再把 `day_regime` / `intraday_state` 当 hard gate，
而是把它们作为 PIT 特征进入 `P(current bracket survives)` 模型。交易判断只在最后用 `p_survive - current_yes_ask` 做 EV。

- Generated UTC: `2026-06-25T16:00:19+00:00`.
- Input rows: `8625` atlas state rows / `36` dates / `36` cities.
- Train cutoff: `<= 2026-05-31`; holdout: `2026-06-01`..`2026-06-20`; forward-like: `>= 2026-06-21` with available labels.

## Model Metrics

| period | market | weather+regime only | market+weather+regime |
|---|---|---|---|
| holdout | AUC 0.955, Brier 0.078, mean edge +0.0% | AUC 0.950, Brier 0.081, mean edge -1.3% | AUC 0.956, Brier 0.077, mean edge -1.5% |
| forward | AUC 0.964, Brier 0.072, mean edge +0.0% | AUC 0.968, Brier 0.072, mean edge +3.4% | AUC 0.971, Brier 0.067, mean edge +2.1% |

## Best Rule Snapshots

| slice | rule | rows | dates | avg ask | win | ROI | CI |
|---|---|---:|---:|---:|---:|---:|---|
| holdout | `market_plus_regime_edge_ge_0.05` | 503 | 20 | 0.701 | +73.2% | +4.4% | [-4.4%, +12.3%] |
| holdout | `market_plus_regime_edge_ge_0.02` | 717 | 20 | 0.751 | +78.1% | +4.0% | [-2.6%, +9.4%] |
| holdout | `market_plus_regime_edge_ge_0.00` | 919 | 20 | 0.774 | +79.2% | +2.4% | [-3.4%, +7.5%] |
| holdout | `weather_regime_only_edge_ge_0.05` | 602 | 20 | 0.734 | +74.3% | +1.2% | [-6.9%, +8.4%] |
| holdout | `weather_regime_only_edge_ge_0.02` | 823 | 20 | 0.768 | +77.4% | +0.7% | [-5.4%, +6.6%] |
| forward | `market_plus_regime_edge_ge_0.02` | 128 | 3 | 0.739 | +77.3% | +4.7% | [-5.9%, +13.7%] |
| forward | `market_plus_regime_edge_ge_0.05` | 90 | 3 | 0.692 | +72.2% | +4.3% | [-11.0%, +19.6%] |
| forward | `market_plus_regime_edge_ge_0.00` | 153 | 3 | 0.770 | +79.7% | +3.5% | [-5.7%, +11.5%] |
| forward | `weather_regime_only_edge_ge_0.00` | 170 | 3 | 0.775 | +79.4% | +2.4% | [-10.5%, +12.4%] |
| forward | `weather_regime_only_edge_ge_0.02` | 150 | 3 | 0.757 | +77.3% | +2.2% | [-10.5%, +12.5%] |

## Regime Diagnostics

| dimension | bucket | rows | survive | avg ask | model p | edge | buy-all ROI |
|---|---|---:|---:|---:|---:|---:|---:|
| `intraday_state` | `slow_warming` | 22 | +63.6% | 0.794 | 0.816 | +2.3% | -19.8% |
| `intraday_state` | `fresh_high` | 848 | +61.7% | 0.631 | 0.640 | +0.9% | -2.3% |
| `intraday_state` | `mature_fade` | 1225 | +96.5% | 0.966 | 0.969 | +0.3% | -0.1% |
| `day_regime` | `day_forecast_busted` | 1566 | +86.5% | 0.860 | 0.860 | -0.1% | +0.5% |
| `day_regime` | `day_forecast_capped` | 1418 | +76.9% | 0.768 | 0.762 | -0.6% | +0.1% |
| `day_regime` | `day_open_runway` | 923 | +40.8% | 0.444 | 0.431 | -1.2% | -7.9% |
| `intraday_state` | `pullback_uncertain` | 488 | +95.9% | 0.953 | 0.937 | -1.6% | +0.7% |
| `intraday_state` | `active_warming` | 1442 | +40.0% | 0.430 | 0.406 | -2.4% | -6.9% |
| `intraday_state` | `reheating_after_dip` | 154 | +85.1% | 0.893 | 0.857 | -3.6% | -4.7% |
| `intraday_state` | `false_fade_risk` | 266 | +56.8% | 0.588 | 0.543 | -4.5% | -3.5% |
| `day_regime` | `day_marginal_runway` | 975 | +51.0% | 0.567 | 0.513 | -5.5% | -10.1% |
| `intraday_state` | `plateau_near_high` | 433 | +61.9% | 0.646 | 0.584 | -6.2% | -4.2% |

## Verdict

- 这是模型路线分支原型，不接 live，不恢复 current-YES 实盘。
- 这版的结构是对的：regime/state 进入模型层，hard gate 只留给数据/执行安全。
- 是否值得继续，要看 holdout/forward 的 `market+weather+regime` 是否稳定超过 market baseline，且 EV 规则的日期 bootstrap CI 是否不跨 0。

## Files

- Summary JSON: `docs/analysis/2026-06/generated/current_yes_regime_hazard_v1/summary.json`
- Scored rows: `docs/analysis/2026-06/generated/current_yes_regime_hazard_v1/current_yes_regime_hazard_v1_scored_rows.csv`
- Metrics: `docs/analysis/2026-06/generated/current_yes_regime_hazard_v1/current_yes_regime_hazard_v1_model_metrics.csv`
- Rule comparison: `docs/analysis/2026-06/generated/current_yes_regime_hazard_v1/current_yes_regime_hazard_v1_rule_comparison.csv`
- Regime slices: `docs/analysis/2026-06/generated/current_yes_regime_hazard_v1/current_yes_regime_hazard_v1_regime_slices.csv`
