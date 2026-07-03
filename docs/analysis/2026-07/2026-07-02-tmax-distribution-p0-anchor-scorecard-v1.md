# Tmax Distribution P0 Anchor Scorecard v1

> generated_at_utc: `2026-07-02T14:53:35+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: offline intraday local-distribution diagnostic only; no live runner/order behavior changed.

## 结论

- 这不是全新问题：6 月已经做过 city-day 全盘口分布质量研究，结论是 `market_norm` 通常强于 raw forecast/model 分布。
- 这次补的是当前策略缺的 P0-local：在 intraday atlas 的 current/d1/d2/tail 四桶上，同台比较 market、forecast anchor、running-max anchor。
- 全样本 6519 行 / 39 天 / 36 城：`market_local_norm` logloss `0.5789`，`forecast_anchor` `1.6303`，`runningmax_anchor` `1.6537`。
- date-block delta：forecast - market logloss = `1.0682` CI [`0.9147`, `1.2425`]；runningmax - market = `1.0648` CI [`0.9930`, `1.1472`]。
- 6/21+ forward slice 的最佳 logloss 方法是 `market_local_norm` (1025 行，logloss `0.5654`)。

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

- raw atlas rows: `13173`
- scored rows: `6519`
- date range: `2026-05-19` .. `2026-06-26`
- cities: `36`
- skipped invalid label/grid: `5106`
- skipped missing market local quote: `1548`

## Full Scorecard

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 6519 | 39 | 36 | 1.6303 | 0.6587 | 52.6% | 0.4121 | 0.643 |
| market_local_norm | 6519 | 39 | 36 | 0.5789 | 0.3154 | 76.5% | 0.6720 | 0.435 |
| runningmax_anchor | 6519 | 39 | 36 | 1.6537 | 0.7005 | 53.5% | 0.3643 | 0.669 |
| uniform | 6519 | 39 | 36 | 1.3863 | 0.7500 | 59.3% | 0.2500 | 1.000 |

## Train / Forward

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 5494 | 33 | 36 | 1.5581 | 0.6448 | 52.5% | 0.4288 | 0.613 |
| market_local_norm | 5494 | 33 | 36 | 0.5814 | 0.3179 | 76.0% | 0.6707 | 0.435 |
| runningmax_anchor | 5494 | 33 | 36 | 1.6625 | 0.6991 | 53.9% | 0.3649 | 0.669 |
| uniform | 5494 | 33 | 36 | 1.3863 | 0.7500 | 59.8% | 0.2500 | 1.000 |

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 1025 | 6 | 36 | 2.0172 | 0.7327 | 53.0% | 0.3227 | 0.805 |
| market_local_norm | 1025 | 6 | 36 | 0.5654 | 0.3022 | 79.0% | 0.6788 | 0.434 |
| runningmax_anchor | 1025 | 6 | 36 | 1.6063 | 0.7078 | 51.6% | 0.3611 | 0.668 |
| uniform | 1025 | 6 | 36 | 1.3863 | 0.7500 | 56.6% | 0.2500 | 1.000 |

## Day Regime Slice

| day_regime | method | n | logloss | brier | top1 | winner_p |
|---|---|---:|---:|---:|---:|---:|
| day_forecast_busted | forecast_anchor | 1559 | 0.8087 | 0.3877 | 82.7% | 0.6697 |
| day_forecast_busted | market_local_norm | 1559 | 0.3588 | 0.2051 | 85.4% | 0.7796 |
| day_forecast_busted | runningmax_anchor | 1559 | 0.8597 | 0.5152 | 76.4% | 0.4550 |
| day_forecast_busted | uniform | 1559 | 1.3863 | 0.7500 | 82.7% | 0.2500 |
| day_forecast_capped | forecast_anchor | 1614 | 1.0617 | 0.5933 | 61.7% | 0.4172 |
| day_forecast_capped | market_local_norm | 1614 | 0.4832 | 0.2570 | 81.2% | 0.7171 |
| day_forecast_capped | runningmax_anchor | 1614 | 1.0638 | 0.5933 | 63.8% | 0.4120 |
| day_forecast_capped | uniform | 1614 | 1.3863 | 0.7500 | 74.2% | 0.2500 |
| day_marginal_runway | forecast_anchor | 1195 | 1.5229 | 0.7667 | 26.6% | 0.2834 |
| day_marginal_runway | market_local_norm | 1195 | 0.7393 | 0.4100 | 68.7% | 0.5919 |
| day_marginal_runway | runningmax_anchor | 1195 | 1.5635 | 0.7238 | 45.4% | 0.3547 |
| day_marginal_runway | uniform | 1195 | 1.3863 | 0.7500 | 50.0% | 0.2500 |
| day_open_runway | forecast_anchor | 1648 | 3.1167 | 0.8728 | 32.5% | 0.3063 |
| day_open_runway | market_local_norm | 1648 | 0.7639 | 0.4101 | 68.8% | 0.5827 |
| day_open_runway | runningmax_anchor | 1648 | 3.1167 | 0.9672 | 28.6% | 0.2382 |
| day_open_runway | uniform | 1648 | 1.3863 | 0.7500 | 29.9% | 0.2500 |
| day_space_unknown | forecast_anchor | 503 | 1.3863 | 0.7500 | 57.5% | 0.2500 |
| day_space_unknown | market_local_norm | 503 | 0.5812 | 0.3097 | 77.7% | 0.6761 |
| day_space_unknown | runningmax_anchor | 503 | 1.4280 | 0.6900 | 50.7% | 0.3658 |
| day_space_unknown | uniform | 503 | 1.3863 | 0.7500 | 57.5% | 0.2500 |

## Pairwise Delta

Positive logloss delta means the left method is worse than the right method.

| slice_type | slice | left - right | n | logloss_delta | brier_delta |
|---|---|---|---:|---:|---:|
| all | all | forecast_anchor - market_local_norm | 6519 | 1.0514 | 0.3432 |
| all | all | forecast_anchor - runningmax_anchor | 6519 | -0.0234 | -0.0418 |
| all | all | market_local_norm - uniform | 6519 | -0.8074 | -0.4346 |
| all | all | runningmax_anchor - market_local_norm | 6519 | 1.0747 | 0.3851 |
| day_regime | day_forecast_busted | forecast_anchor - market_local_norm | 1559 | 0.4499 | 0.1827 |
| day_regime | day_forecast_busted | forecast_anchor - runningmax_anchor | 1559 | -0.0510 | -0.1275 |
| day_regime | day_forecast_busted | market_local_norm - uniform | 1559 | -1.0274 | -0.5449 |
| day_regime | day_forecast_busted | runningmax_anchor - market_local_norm | 1559 | 0.5009 | 0.3101 |
| day_regime | day_forecast_capped | forecast_anchor - market_local_norm | 1614 | 0.5786 | 0.3362 |
| day_regime | day_forecast_capped | forecast_anchor - runningmax_anchor | 1614 | -0.0020 | 0.0000 |
| day_regime | day_forecast_capped | market_local_norm - uniform | 1614 | -0.9031 | -0.4930 |
| day_regime | day_forecast_capped | runningmax_anchor - market_local_norm | 1614 | 0.5806 | 0.3362 |
| day_regime | day_marginal_runway | forecast_anchor - market_local_norm | 1195 | 0.7836 | 0.3567 |
| day_regime | day_marginal_runway | forecast_anchor - runningmax_anchor | 1195 | -0.0406 | 0.0429 |
| day_regime | day_marginal_runway | market_local_norm - uniform | 1195 | -0.6470 | -0.3400 |
| day_regime | day_marginal_runway | runningmax_anchor - market_local_norm | 1195 | 0.8242 | 0.3137 |
| day_regime | day_open_runway | forecast_anchor - market_local_norm | 1648 | 2.3528 | 0.4626 |
| day_regime | day_open_runway | forecast_anchor - runningmax_anchor | 1648 | 0.0000 | -0.0944 |
| day_regime | day_open_runway | market_local_norm - uniform | 1648 | -0.6224 | -0.3399 |
| day_regime | day_open_runway | runningmax_anchor - market_local_norm | 1648 | 2.3528 | 0.5570 |
| day_regime | day_space_unknown | forecast_anchor - market_local_norm | 503 | 0.8051 | 0.4403 |
| day_regime | day_space_unknown | forecast_anchor - runningmax_anchor | 503 | -0.0417 | 0.0600 |
| day_regime | day_space_unknown | market_local_norm - uniform | 503 | -0.8051 | -0.4403 |
| day_regime | day_space_unknown | runningmax_anchor - market_local_norm | 503 | 0.8468 | 0.3804 |
| split | forward_2026_06_21_plus | forecast_anchor - market_local_norm | 1025 | 1.4518 | 0.4305 |
| split | forward_2026_06_21_plus | forecast_anchor - runningmax_anchor | 1025 | 0.4109 | 0.0249 |
| split | forward_2026_06_21_plus | market_local_norm - uniform | 1025 | -0.8209 | -0.4478 |
| split | forward_2026_06_21_plus | runningmax_anchor - market_local_norm | 1025 | 1.0409 | 0.4056 |
| split | train_pre_2026_06_21 | forecast_anchor - market_local_norm | 5494 | 0.9767 | 0.3270 |
| split | train_pre_2026_06_21 | forecast_anchor - runningmax_anchor | 5494 | -0.1044 | -0.0543 |
| split | train_pre_2026_06_21 | market_local_norm - uniform | 5494 | -0.8049 | -0.4321 |
| split | train_pre_2026_06_21 | runningmax_anchor - market_local_norm | 5494 | 1.0811 | 0.3813 |

## 产物

- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/scored_rows.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/summary_by_slice.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/pairwise_deltas.csv`
- `docs/analysis/2026-07/2026-07-02-tmax-distribution-p0-anchor-scorecard-v1.json`
