# Tmax Distribution P1 Fusion Scorecard v1

> generated_at_utc: `2026-07-09T03:38:34+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: offline proper-scoring diagnostic only; no live runner/order behavior changed.

## 结论

- P1 继续用 P0-local 四桶：`current / d1 / d2 / tail`，样本 8347 rows / 48 dates / 36 cities。
- C 和 market/model blend alpha 只在 `<2026-06-21` 的日期 walk-forward CV 里选择，然后再看 `2026-06-21+`。
- Fixed forward：market logloss `0.6489`；最佳非 market 是 `fusion_city_blend` `0.5835` (delta `-0.0653`, date-CI [`-0.1025`, `-0.0165`])。
- Expanding forward：market logloss `0.6489`；最佳非 market 是 `fusion_city_blend` `0.5855` (delta `-0.0633`, date-CI [`-0.1011`, `-0.0172`])。
- Verdict: `promising_shadow_candidate`。

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
| fusion_city | 0.03 | 0.50 | 0.6530 | 0.6232 | 0.6149 | 3743/19 |
| fusion_context | 0.1 | 0.50 | 0.6530 | 0.6262 | 0.6150 | 3743/19 |
| fusion_numeric | 0.1 | 0.50 | 0.6530 | 0.6216 | 0.6157 | 3743/19 |
| market_recal | 0.3 | 0.75 | 0.6530 | 0.6197 | 0.6189 | 3743/19 |
| weather_physical | 1 | 0.10 | 0.6530 | 0.7962 | 0.6238 | 3743/19 |

## Fixed Forward 2026-06-21+

| method | n | dates | logloss | delta_vs_market | brier | top1 | winner_p |
|---|---:|---:|---:|---:|---:|---:|---:|
| fusion_city_blend | 2048 | 15 | 0.5835 | -0.0653 | 0.3260 | 75.9% | 0.6596 |
| fusion_context_blend | 2048 | 15 | 0.5846 | -0.0643 | 0.3272 | 76.1% | 0.6603 |
| fusion_numeric_blend | 2048 | 15 | 0.5854 | -0.0634 | 0.3278 | 76.0% | 0.6593 |
| fusion_city_model | 2048 | 15 | 0.5870 | -0.0619 | 0.3287 | 76.0% | 0.6614 |
| fusion_numeric_model | 2048 | 15 | 0.5909 | -0.0579 | 0.3327 | 75.9% | 0.6609 |
| market_recal_blend | 2048 | 15 | 0.5918 | -0.0571 | 0.3301 | 75.4% | 0.6629 |
| fusion_context_model | 2048 | 15 | 0.5938 | -0.0551 | 0.3344 | 75.8% | 0.6629 |
| weather_physical_blend | 2048 | 15 | 0.6033 | -0.0455 | 0.3334 | 75.7% | 0.6403 |
| market_local_norm | 2048 | 15 | 0.6489 | +0.0000 | 0.3311 | 76.0% | 0.6578 |

## Expanding Forward 2026-06-21+

| method | n | dates | logloss | delta_vs_market | brier | top1 | winner_p |
|---|---:|---:|---:|---:|---:|---:|---:|
| fusion_city_blend | 2048 | 15 | 0.5855 | -0.0633 | 0.3265 | 75.8% | 0.6609 |
| fusion_numeric_blend | 2048 | 15 | 0.5866 | -0.0623 | 0.3283 | 76.0% | 0.6605 |
| fusion_context_blend | 2048 | 15 | 0.5888 | -0.0601 | 0.3287 | 75.8% | 0.6608 |
| market_recal_blend | 2048 | 15 | 0.5917 | -0.0571 | 0.3301 | 75.5% | 0.6639 |
| fusion_city_model | 2048 | 15 | 0.5920 | -0.0568 | 0.3303 | 75.5% | 0.6641 |
| fusion_numeric_model | 2048 | 15 | 0.5947 | -0.0542 | 0.3336 | 74.7% | 0.6632 |
| weather_physical_blend | 2048 | 15 | 0.5963 | -0.0525 | 0.3318 | 75.7% | 0.6449 |
| fusion_context_model | 2048 | 15 | 0.6033 | -0.0456 | 0.3371 | 75.0% | 0.6638 |
| market_local_norm | 2048 | 15 | 0.6489 | +0.0000 | 0.3311 | 76.0% | 0.6578 |

## Daily Sanity Check

Fixed forward 的 `fusion_city_blend` 5/6 天优于 market，唯一明显变差是 2026-06-24；expanding 也是 5/6 天优于 market，但 date-block CI 跨 0。

| date | n | market | fusion_city_blend | delta |
|---|---:|---:|---:|---:|
| 2026-06-21 | 201 | 0.6339 | 0.6264 | -0.0075 |
| 2026-06-22 | 215 | 0.4794 | 0.4413 | -0.0382 |
| 2026-06-23 | 146 | 0.5777 | 0.5699 | -0.0078 |
| 2026-06-25 | 222 | 0.7516 | 0.6668 | -0.0848 |
| 2026-06-26 | 235 | 0.5263 | 0.5220 | -0.0043 |
| 2026-06-27 | 202 | 0.7518 | 0.5680 | -0.1839 |
| 2026-06-28 | 28 | 0.7493 | 0.7072 | -0.0421 |
| 2026-06-29 | 81 | 0.5260 | 0.5364 | +0.0104 |
| 2026-06-30 | 155 | 0.9727 | 0.6566 | -0.3161 |
| 2026-07-01 | 132 | 0.5973 | 0.5995 | +0.0022 |
| 2026-07-02 | 32 | 0.5999 | 0.5622 | -0.0376 |
| 2026-07-03 | 80 | 0.5409 | 0.5538 | +0.0129 |
| 2026-07-05 | 155 | 0.7777 | 0.6715 | -0.1062 |
| 2026-07-06 | 106 | 0.6505 | 0.6506 | +0.0001 |
| 2026-07-07 | 58 | 0.4569 | 0.4513 | -0.0056 |

## Bucket Breakdown

`fusion_city_blend` 的主要改进来自 actual bucket = `current`；对 `d1/d2` 略差，tail 基本持平。这说明它更像是在纠正 market 对“当前档守住”的低估，而不是全面优于盘口。

| actual_bucket | n | market | fusion_city_blend | delta |
|---|---:|---:|---:|---:|
| current | 989 | 0.2471 | 0.2476 | +0.0005 |
| d1 | 465 | 0.9135 | 0.8827 | -0.0309 |
| d2 | 307 | 1.1340 | 1.1133 | -0.0207 |
| tail | 287 | 1.0857 | 0.6900 | -0.3957 |

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
