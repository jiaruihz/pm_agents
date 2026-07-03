# Tmax Distribution P1 Fusion Scorecard v1

> generated_at_utc: `2026-07-02T14:58:18+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: offline proper-scoring diagnostic only; no live runner/order behavior changed.

## 结论

- P1 继续用 P0-local 四桶：`current / d1 / d2 / tail`，样本 6519 rows / 39 dates / 36 cities。
- C 和 market/model blend alpha 只在 `<2026-06-21` 的日期 walk-forward CV 里选择，然后再看 `2026-06-21+`。
- Fixed forward：market logloss `0.5654`；最佳非 market 是 `fusion_city_blend` `0.5169` (delta `-0.0485`, date-CI [`-0.0903`, `-0.0023`])。
- Expanding forward：market logloss `0.5654`；最佳非 market 是 `fusion_city_blend` `0.5212` (delta `-0.0442`, date-CI [`-0.0850`, `+0.0076`])。
- Verdict: `inconclusive_positive_signal`。

人话：这一步不是找到了可下单策略，而是在检验“天气融合层能不能比盘口更准”。本轮最像真东西的是 `market + physical path`：`fusion_numeric`、`fusion_context`、`fusion_city` 都接近，说明不是单纯靠 city id 记忆；但 forward 只有 6 天，expanding CI 仍跨 0，所以只能进入 P2 离线 EV / telemetry，不能算 shadow alpha 已验证，也不能 live approval。

## Feature Families

- `market_recal`: 只用 market-local 四桶概率，测试盘口校准。
- `weather_physical`: forecast/running/current path、趋势、wind/cloud/humidity、regime/source，不用 market 概率和精确 city id。
- `fusion_numeric`: market + 连续物理/路径特征，不用 regime/source/city 类别。
- `fusion_context`: market + weather/context，不用精确 city id。
- `fusion_city`: fusion_context + 精确 city id，用来测试 city/source bias 是否提供增量，同时观察过拟合风险。

未使用的字段：`final_max_native`、`forecast_error_native`、`future_break_*`、payoff/ROI、任何 settlement 结果列。

## Train-CV Selection

| spec | selected_C | alpha | cv_market | cv_model | cv_blend | cv_rows/dates |
|---|---:|---:|---:|---:|---:|---:|
| fusion_city | 0.03 | 1.00 | 0.5932 | 0.5165 | 0.5165 | 3217/19 |
| fusion_context | 0.03 | 1.00 | 0.5932 | 0.5170 | 0.5170 | 3217/19 |
| fusion_numeric | 0.1 | 1.00 | 0.5932 | 0.5179 | 0.5179 | 3217/19 |
| market_recal | 0.3 | 0.75 | 0.5932 | 0.5574 | 0.5568 | 3217/19 |
| weather_physical | 1 | 0.35 | 0.5932 | 0.6351 | 0.5474 | 3217/19 |

## Fixed Forward 2026-06-21+

| method | n | dates | logloss | delta_vs_market | brier | top1 | winner_p |
|---|---:|---:|---:|---:|---:|---:|---:|
| fusion_city_blend | 1025 | 6 | 0.5169 | -0.0485 | 0.2853 | 80.0% | 0.7053 |
| fusion_city_model | 1025 | 6 | 0.5169 | -0.0485 | 0.2853 | 80.0% | 0.7053 |
| fusion_context_blend | 1025 | 6 | 0.5190 | -0.0463 | 0.2872 | 79.2% | 0.7037 |
| fusion_context_model | 1025 | 6 | 0.5190 | -0.0463 | 0.2872 | 79.2% | 0.7037 |
| fusion_numeric_blend | 1025 | 6 | 0.5252 | -0.0401 | 0.2906 | 78.9% | 0.7049 |
| fusion_numeric_model | 1025 | 6 | 0.5252 | -0.0401 | 0.2906 | 78.9% | 0.7049 |
| market_recal_blend | 1025 | 6 | 0.5462 | -0.0192 | 0.3019 | 78.0% | 0.6882 |
| market_local_norm | 1025 | 6 | 0.5654 | +0.0000 | 0.3022 | 79.0% | 0.6788 |
| weather_physical_blend | 1025 | 6 | 0.5982 | +0.0328 | 0.3284 | 77.4% | 0.6360 |

## Expanding Forward 2026-06-21+

| method | n | dates | logloss | delta_vs_market | brier | top1 | winner_p |
|---|---:|---:|---:|---:|---:|---:|---:|
| fusion_city_blend | 1025 | 6 | 0.5212 | -0.0442 | 0.2865 | 78.8% | 0.7058 |
| fusion_city_model | 1025 | 6 | 0.5212 | -0.0442 | 0.2865 | 78.8% | 0.7058 |
| fusion_context_blend | 1025 | 6 | 0.5254 | -0.0400 | 0.2897 | 78.5% | 0.7035 |
| fusion_context_model | 1025 | 6 | 0.5254 | -0.0400 | 0.2897 | 78.5% | 0.7035 |
| fusion_numeric_blend | 1025 | 6 | 0.5314 | -0.0340 | 0.2922 | 78.1% | 0.7069 |
| fusion_numeric_model | 1025 | 6 | 0.5314 | -0.0340 | 0.2922 | 78.1% | 0.7069 |
| market_recal_blend | 1025 | 6 | 0.5463 | -0.0191 | 0.3018 | 77.7% | 0.6887 |
| market_local_norm | 1025 | 6 | 0.5654 | +0.0000 | 0.3022 | 79.0% | 0.6788 |
| weather_physical_blend | 1025 | 6 | 0.5736 | +0.0083 | 0.3127 | 79.3% | 0.6491 |

## Daily Sanity Check

Fixed forward 的 `fusion_city_blend` 5/6 天优于 market，唯一明显变差是 2026-06-24；expanding 也是 5/6 天优于 market，但 date-block CI 跨 0。

| date | n | market | fusion_city_blend | delta |
|---|---:|---:|---:|---:|
| 2026-06-21 | 168 | 0.5251 | 0.5110 | -0.0141 |
| 2026-06-22 | 182 | 0.4770 | 0.3456 | -0.1313 |
| 2026-06-23 | 142 | 0.5286 | 0.5236 | -0.0050 |
| 2026-06-24 | 144 | 0.6319 | 0.6616 | +0.0297 |
| 2026-06-25 | 182 | 0.7163 | 0.6179 | -0.0985 |
| 2026-06-26 | 207 | 0.5220 | 0.4780 | -0.0440 |

## Bucket Breakdown

`fusion_city_blend` 的主要改进来自 actual bucket = `current`；对 `d1/d2` 略差，tail 基本持平。这说明它更像是在纠正 market 对“当前档守住”的低估，而不是全面优于盘口。

| actual_bucket | n | market | fusion_city_blend | delta |
|---|---:|---:|---:|---:|
| current | 580 | 0.2556 | 0.1587 | -0.0969 |
| d1 | 203 | 0.8591 | 0.8697 | +0.0105 |
| d2 | 140 | 1.2632 | 1.2958 | +0.0326 |
| tail | 102 | 0.7844 | 0.7821 | -0.0023 |

## Notes

- `model` = 模型自己输出的四桶概率；`blend` = `(1-alpha)*market + alpha*model`。
- 负的 `delta_vs_market` 才表示打败 market-local。
- 这里仍是 local ladder proxy；不是完整 Polymarket bracket ladder。
- 本实验没有同步 N100 或发布 live_real PnL；只消费已生成 atlas CSV。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1/fixed_forward_scores.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1/expanding_forward_scores.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1/model_selection.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1/candidate_selection_grid.csv`
- `docs/analysis/2026-07/2026-07-02-tmax-distribution-p1-fusion-scorecard-v1.json`
