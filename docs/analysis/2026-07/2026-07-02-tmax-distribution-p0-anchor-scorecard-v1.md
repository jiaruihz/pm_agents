# Tmax Distribution P0 Anchor Scorecard v1

> generated_at_utc: `2026-07-04T15:56:54+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: offline intraday local-distribution diagnostic only; no live runner/order behavior changed.

## 结论

- 这不是全新问题：6 月已经做过 city-day 全盘口分布质量研究，结论是 `market_norm` 通常强于 raw forecast/model 分布。
- 这次补的是当前策略缺的 P0-local：在 intraday atlas 的 current/d1/d2/tail 四桶上，同台比较 market、forecast anchor、running-max anchor。
- 全样本 7586 行 / 41 天 / 36 城：`market_local_norm` logloss `0.6339`，`forecast_anchor` `2.0726`，`runningmax_anchor` `2.3932`。
- date-block delta：forecast - market logloss = `1.4160` CI [`1.2230`, `1.6384`]；runningmax - market = `1.7175` CI [`1.5616`, `1.8586`]。
- 6/21+ forward slice 的最佳 logloss 方法是 `market_local_norm` (1287 行，logloss `0.6355`)。

交易含义：P0 没有证明“只靠 forecast 或 running max 的朴素锚”能打败盘口。下一步如果继续这条线，应该做的是融合模型：
`P(Tmax bucket | realized path, forecast curve, bracket fractional position, solar clock, city/source basis)`，
然后用 proper scoring rule 先打败 market-local，再谈 route/gate/ROI。当前结论不支持直接改 live。

## 方法

- `current/d1/d2/tail` 是 atlas 当前行可观察到的局部分布，不是完整 bracket ladder。
- `market_local_norm`：尽量用 current/d1/d2 的 bid/ask midpoint 推 implied YES，再加 residual tail 后归一化。
- 同时落 `market_raw_local_mass`、`market_tail_residual_raw`、`market_local_overround_raw`，供 P2 检查 proxy 是否失真；这些字段不参与 P0 scoring。
- `forecast_anchor`：把 `forecast_max_native` 到 current/d1/d2/tail interval 的距离做 Gaussian soft anchor。
- `runningmax_anchor`：同上，但锚点换成 `running_native`。
- 指标：logloss / multiclass brier 越低越好；top1 / winner probability 越高越好。
- 没有同步 N100 或重建 fact 表；本实验只消费已生成 atlas CSV，不发布 live_real PnL。

## 数据覆盖

- raw atlas rows: `13725`
- scored rows: `7586`
- date range: `2026-05-19` .. `2026-07-02`
- cities: `36`
- skipped invalid label/grid: `4516`
- skipped missing market local quote: `1623`

## Full Scorecard

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 7586 | 41 | 36 | 2.0726 | 0.6798 | 49.4% | 0.4282 | 0.575 |
| market_local_norm | 7586 | 41 | 36 | 0.6339 | 0.3455 | 73.9% | 0.6474 | 0.458 |
| runningmax_anchor | 7586 | 41 | 36 | 2.3932 | 0.7268 | 51.9% | 0.3847 | 0.582 |
| uniform | 7586 | 41 | 36 | 1.3863 | 0.7500 | 51.9% | 0.2500 | 1.000 |

## Train / Forward

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 6299 | 33 | 36 | 2.1155 | 0.6698 | 49.4% | 0.4506 | 0.524 |
| market_local_norm | 6299 | 33 | 36 | 0.6335 | 0.3503 | 73.2% | 0.6438 | 0.461 |
| runningmax_anchor | 6299 | 33 | 36 | 2.3932 | 0.7254 | 52.2% | 0.3860 | 0.581 |
| uniform | 6299 | 33 | 36 | 1.3863 | 0.7500 | 52.2% | 0.2500 | 1.000 |

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 1287 | 8 | 36 | 1.8624 | 0.7289 | 49.3% | 0.3183 | 0.825 |
| market_local_norm | 1287 | 8 | 36 | 0.6355 | 0.3222 | 77.4% | 0.6649 | 0.445 |
| runningmax_anchor | 1287 | 8 | 36 | 2.3934 | 0.7341 | 50.3% | 0.3783 | 0.589 |
| uniform | 1287 | 8 | 36 | 1.3863 | 0.7500 | 50.3% | 0.2500 | 1.000 |

## Day Regime Slice

| day_regime | method | n | logloss | brier | top1 | winner_p |
|---|---|---:|---:|---:|---:|---:|
| day_forecast_busted | forecast_anchor | 1857 | 1.2223 | 0.3942 | 78.6% | 0.7154 |
| day_forecast_busted | market_local_norm | 1857 | 0.3913 | 0.2221 | 84.1% | 0.7637 |
| day_forecast_busted | runningmax_anchor | 1857 | 0.8863 | 0.4782 | 78.6% | 0.5050 |
| day_forecast_busted | uniform | 1857 | 1.3863 | 0.7500 | 78.6% | 0.2500 |
| day_forecast_capped | forecast_anchor | 1491 | 1.3090 | 0.5724 | 59.0% | 0.4626 |
| day_forecast_capped | market_local_norm | 1491 | 0.6208 | 0.3501 | 73.6% | 0.6476 |
| day_forecast_capped | runningmax_anchor | 1491 | 1.3153 | 0.5852 | 59.6% | 0.4528 |
| day_forecast_capped | uniform | 1491 | 1.3863 | 0.7500 | 59.6% | 0.2500 |
| day_marginal_runway | forecast_anchor | 1324 | 1.5866 | 0.7560 | 28.5% | 0.2974 |
| day_marginal_runway | market_local_norm | 1324 | 0.7383 | 0.4060 | 68.9% | 0.5870 |
| day_marginal_runway | runningmax_anchor | 1324 | 2.3951 | 0.7855 | 43.6% | 0.3551 |
| day_marginal_runway | uniform | 1324 | 1.3863 | 0.7500 | 43.6% | 0.2500 |
| day_open_runway | forecast_anchor | 2187 | 3.8374 | 0.9262 | 30.8% | 0.2992 |
| day_open_runway | market_local_norm | 2187 | 0.7927 | 0.4152 | 67.6% | 0.5817 |
| day_open_runway | runningmax_anchor | 2187 | 4.3544 | 1.0000 | 29.8% | 0.2511 |
| day_open_runway | uniform | 2187 | 1.3863 | 0.7500 | 29.8% | 0.2500 |
| day_space_unknown | forecast_anchor | 727 | 1.3863 | 0.7500 | 49.1% | 0.2500 |
| day_space_unknown | market_local_norm | 727 | 0.6126 | 0.3314 | 76.5% | 0.6578 |
| day_space_unknown | runningmax_anchor | 727 | 2.5504 | 0.7236 | 49.1% | 0.3938 |
| day_space_unknown | uniform | 727 | 1.3863 | 0.7500 | 49.1% | 0.2500 |

## Pairwise Delta

Positive logloss delta means the left method is worse than the right method.

| slice_type | slice | left - right | n | logloss_delta | brier_delta |
|---|---|---|---:|---:|---:|
| all | all | forecast_anchor - market_local_norm | 7586 | 1.4387 | 0.3343 |
| all | all | forecast_anchor - runningmax_anchor | 7586 | -0.3207 | -0.0470 |
| all | all | market_local_norm - uniform | 7586 | -0.7524 | -0.4045 |
| all | all | runningmax_anchor - market_local_norm | 7586 | 1.7594 | 0.3813 |
| day_regime | day_forecast_busted | forecast_anchor - market_local_norm | 1857 | 0.8310 | 0.1721 |
| day_regime | day_forecast_busted | forecast_anchor - runningmax_anchor | 1857 | 0.3361 | -0.0840 |
| day_regime | day_forecast_busted | market_local_norm - uniform | 1857 | -0.9950 | -0.5279 |
| day_regime | day_forecast_busted | runningmax_anchor - market_local_norm | 1857 | 0.4949 | 0.2561 |
| day_regime | day_forecast_capped | forecast_anchor - market_local_norm | 1491 | 0.6882 | 0.2223 |
| day_regime | day_forecast_capped | forecast_anchor - runningmax_anchor | 1491 | -0.0063 | -0.0127 |
| day_regime | day_forecast_capped | market_local_norm - uniform | 1491 | -0.7655 | -0.3999 |
| day_regime | day_forecast_capped | runningmax_anchor - market_local_norm | 1491 | 0.6945 | 0.2351 |
| day_regime | day_marginal_runway | forecast_anchor - market_local_norm | 1324 | 0.8483 | 0.3500 |
| day_regime | day_marginal_runway | forecast_anchor - runningmax_anchor | 1324 | -0.8086 | -0.0296 |
| day_regime | day_marginal_runway | market_local_norm - uniform | 1324 | -0.6480 | -0.3440 |
| day_regime | day_marginal_runway | runningmax_anchor - market_local_norm | 1324 | 1.6568 | 0.3796 |
| day_regime | day_open_runway | forecast_anchor - market_local_norm | 2187 | 3.0448 | 0.5109 |
| day_regime | day_open_runway | forecast_anchor - runningmax_anchor | 2187 | -0.5170 | -0.0739 |
| day_regime | day_open_runway | market_local_norm - uniform | 2187 | -0.5936 | -0.3348 |
| day_regime | day_open_runway | runningmax_anchor - market_local_norm | 2187 | 3.5617 | 0.5848 |
| day_regime | day_space_unknown | forecast_anchor - market_local_norm | 727 | 0.7737 | 0.4186 |
| day_regime | day_space_unknown | forecast_anchor - runningmax_anchor | 727 | -1.1641 | 0.0264 |
| day_regime | day_space_unknown | market_local_norm - uniform | 727 | -0.7737 | -0.4186 |
| day_regime | day_space_unknown | runningmax_anchor - market_local_norm | 727 | 1.9379 | 0.3922 |
| split | forward_2026_06_21_plus | forecast_anchor - market_local_norm | 1287 | 1.2268 | 0.4067 |
| split | forward_2026_06_21_plus | forecast_anchor - runningmax_anchor | 1287 | -0.5310 | -0.0051 |
| split | forward_2026_06_21_plus | market_local_norm - uniform | 1287 | -0.7507 | -0.4278 |
| split | forward_2026_06_21_plus | runningmax_anchor - market_local_norm | 1287 | 1.7578 | 0.4119 |
| split | train_pre_2026_06_21 | forecast_anchor - market_local_norm | 6299 | 1.4819 | 0.3195 |
| split | train_pre_2026_06_21 | forecast_anchor - runningmax_anchor | 6299 | -0.2777 | -0.0556 |
| split | train_pre_2026_06_21 | market_local_norm - uniform | 6299 | -0.7528 | -0.3997 |
| split | train_pre_2026_06_21 | runningmax_anchor - market_local_norm | 6299 | 1.7597 | 0.3751 |

## 产物

- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/scored_rows.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/summary_by_slice.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/pairwise_deltas.csv`
- `docs/analysis/2026-07/2026-07-02-tmax-distribution-p0-anchor-scorecard-v1.json`
