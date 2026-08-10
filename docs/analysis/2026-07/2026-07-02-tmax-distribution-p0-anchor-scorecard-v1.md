# Tmax Distribution P0 Anchor Scorecard v1

> generated_at_utc: `2026-07-09T03:37:39+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: offline intraday local-distribution diagnostic only; no live runner/order behavior changed.

## 结论

- 这不是全新问题：6 月已经做过 city-day 全盘口分布质量研究，结论是 `market_norm` 通常强于 raw forecast/model 分布。
- 这次补的是当前策略缺的 P0-local：在 intraday atlas 的 current/d1/d2/tail 四桶上，同台比较 market、forecast anchor、running-max anchor。
- 全样本 8347 行 / 48 天 / 36 城：`market_local_norm` logloss `0.6373`，`forecast_anchor` `2.0545`，`runningmax_anchor` `2.4308`。
- date-block delta：forecast - market logloss = `1.3918` CI [`1.1943`, `1.5793`]；runningmax - market = `1.7735` CI [`1.6193`, `1.9252`]。
- 6/21+ forward slice 的最佳 logloss 方法是 `market_local_norm` (2048 行，logloss `0.6489`)。

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

- raw atlas rows: `14368`
- scored rows: `8347`
- date range: `2026-05-19` .. `2026-07-07`
- cities: `36`
- skipped invalid label/grid: `4263`
- skipped missing market local quote: `1758`

## Full Scorecard

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 8347 | 48 | 36 | 2.0545 | 0.6813 | 49.2% | 0.4243 | 0.585 |
| market_local_norm | 8347 | 48 | 36 | 0.6373 | 0.3456 | 73.9% | 0.6473 | 0.458 |
| runningmax_anchor | 8347 | 48 | 36 | 2.4308 | 0.7328 | 51.2% | 0.3815 | 0.583 |
| uniform | 8347 | 48 | 36 | 1.3863 | 0.7500 | 51.2% | 0.2500 | 1.000 |

## Train / Forward

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 6299 | 33 | 36 | 2.1155 | 0.6698 | 49.4% | 0.4506 | 0.524 |
| market_local_norm | 6299 | 33 | 36 | 0.6335 | 0.3503 | 73.2% | 0.6438 | 0.461 |
| runningmax_anchor | 6299 | 33 | 36 | 2.3932 | 0.7254 | 52.2% | 0.3860 | 0.581 |
| uniform | 6299 | 33 | 36 | 1.3863 | 0.7500 | 52.2% | 0.2500 | 1.000 |

| method | n | dates | cities | logloss | brier | top1 | winner_p | entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| forecast_anchor | 2048 | 15 | 36 | 1.8667 | 0.7168 | 48.6% | 0.3432 | 0.772 |
| market_local_norm | 2048 | 15 | 36 | 0.6489 | 0.3311 | 76.0% | 0.6578 | 0.450 |
| runningmax_anchor | 2048 | 15 | 36 | 2.5463 | 0.7556 | 48.3% | 0.3676 | 0.589 |
| uniform | 2048 | 15 | 36 | 1.3863 | 0.7500 | 48.3% | 0.2500 | 1.000 |

## Day Regime Slice

| day_regime | method | n | logloss | brier | top1 | winner_p |
|---|---|---:|---:|---:|---:|---:|
| day_forecast_busted | forecast_anchor | 1975 | 1.2216 | 0.3965 | 78.4% | 0.7142 |
| day_forecast_busted | market_local_norm | 1975 | 0.3911 | 0.2226 | 84.1% | 0.7630 |
| day_forecast_busted | runningmax_anchor | 1975 | 0.8891 | 0.4812 | 78.4% | 0.5024 |
| day_forecast_busted | uniform | 1975 | 1.3863 | 0.7500 | 78.4% | 0.2500 |
| day_forecast_capped | forecast_anchor | 1582 | 1.3188 | 0.5761 | 58.7% | 0.4614 |
| day_forecast_capped | market_local_norm | 1582 | 0.6302 | 0.3502 | 73.6% | 0.6492 |
| day_forecast_capped | runningmax_anchor | 1582 | 1.3243 | 0.5887 | 59.4% | 0.4516 |
| day_forecast_capped | uniform | 1582 | 1.3863 | 0.7500 | 59.4% | 0.2500 |
| day_marginal_runway | forecast_anchor | 1444 | 1.5779 | 0.7540 | 28.5% | 0.2981 |
| day_marginal_runway | market_local_norm | 1444 | 0.7412 | 0.4014 | 69.2% | 0.5902 |
| day_marginal_runway | runningmax_anchor | 1444 | 2.3926 | 0.7893 | 43.1% | 0.3525 |
| day_marginal_runway | uniform | 1444 | 1.3863 | 0.7500 | 43.1% | 0.2500 |
| day_open_runway | forecast_anchor | 2378 | 3.7969 | 0.9159 | 31.8% | 0.3063 |
| day_open_runway | market_local_norm | 2378 | 0.7938 | 0.4136 | 68.0% | 0.5832 |
| day_open_runway | runningmax_anchor | 2378 | 4.4339 | 1.0023 | 29.4% | 0.2502 |
| day_open_runway | uniform | 2378 | 1.3863 | 0.7500 | 29.4% | 0.2500 |
| day_space_unknown | forecast_anchor | 968 | 1.3863 | 0.7500 | 47.9% | 0.2500 |
| day_space_unknown | market_local_norm | 968 | 0.6118 | 0.3386 | 75.1% | 0.6506 |
| day_space_unknown | runningmax_anchor | 968 | 2.5206 | 0.7353 | 47.9% | 0.3864 |
| day_space_unknown | uniform | 968 | 1.3863 | 0.7500 | 47.9% | 0.2500 |

## Pairwise Delta

Positive logloss delta means the left method is worse than the right method.

| slice_type | slice | left - right | n | logloss_delta | brier_delta |
|---|---|---|---:|---:|---:|
| all | all | forecast_anchor - market_local_norm | 8347 | 1.4171 | 0.3358 |
| all | all | forecast_anchor - runningmax_anchor | 8347 | -0.3763 | -0.0514 |
| all | all | market_local_norm - uniform | 8347 | -0.7490 | -0.4044 |
| all | all | runningmax_anchor - market_local_norm | 8347 | 1.7935 | 0.3872 |
| day_regime | day_forecast_busted | forecast_anchor - market_local_norm | 1975 | 0.8305 | 0.1739 |
| day_regime | day_forecast_busted | forecast_anchor - runningmax_anchor | 1975 | 0.3325 | -0.0847 |
| day_regime | day_forecast_busted | market_local_norm - uniform | 1975 | -0.9952 | -0.5274 |
| day_regime | day_forecast_busted | runningmax_anchor - market_local_norm | 1975 | 0.4980 | 0.2586 |
| day_regime | day_forecast_capped | forecast_anchor - market_local_norm | 1582 | 0.6886 | 0.2259 |
| day_regime | day_forecast_capped | forecast_anchor - runningmax_anchor | 1582 | -0.0055 | -0.0126 |
| day_regime | day_forecast_capped | market_local_norm - uniform | 1582 | -0.7560 | -0.3998 |
| day_regime | day_forecast_capped | runningmax_anchor - market_local_norm | 1582 | 0.6940 | 0.2385 |
| day_regime | day_marginal_runway | forecast_anchor - market_local_norm | 1444 | 0.8367 | 0.3526 |
| day_regime | day_marginal_runway | forecast_anchor - runningmax_anchor | 1444 | -0.8147 | -0.0354 |
| day_regime | day_marginal_runway | market_local_norm - uniform | 1444 | -0.6451 | -0.3486 |
| day_regime | day_marginal_runway | runningmax_anchor - market_local_norm | 1444 | 1.6514 | 0.3879 |
| day_regime | day_open_runway | forecast_anchor - market_local_norm | 2378 | 3.0031 | 0.5023 |
| day_regime | day_open_runway | forecast_anchor - runningmax_anchor | 2378 | -0.6370 | -0.0864 |
| day_regime | day_open_runway | market_local_norm - uniform | 2378 | -0.5925 | -0.3364 |
| day_regime | day_open_runway | runningmax_anchor - market_local_norm | 2378 | 3.6402 | 0.5887 |
| day_regime | day_space_unknown | forecast_anchor - market_local_norm | 968 | 0.7745 | 0.4114 |
| day_regime | day_space_unknown | forecast_anchor - runningmax_anchor | 968 | -1.1343 | 0.0147 |
| day_regime | day_space_unknown | market_local_norm - uniform | 968 | -0.7745 | -0.4114 |
| day_regime | day_space_unknown | runningmax_anchor - market_local_norm | 968 | 1.9087 | 0.3966 |
| split | forward_2026_06_21_plus | forecast_anchor - market_local_norm | 2048 | 1.2178 | 0.3857 |
| split | forward_2026_06_21_plus | forecast_anchor - runningmax_anchor | 2048 | -0.6796 | -0.0388 |
| split | forward_2026_06_21_plus | market_local_norm - uniform | 2048 | -0.7374 | -0.4189 |
| split | forward_2026_06_21_plus | runningmax_anchor - market_local_norm | 2048 | 1.8974 | 0.4245 |
| split | train_pre_2026_06_21 | forecast_anchor - market_local_norm | 6299 | 1.4819 | 0.3195 |
| split | train_pre_2026_06_21 | forecast_anchor - runningmax_anchor | 6299 | -0.2777 | -0.0556 |
| split | train_pre_2026_06_21 | market_local_norm - uniform | 6299 | -0.7528 | -0.3997 |
| split | train_pre_2026_06_21 | runningmax_anchor - market_local_norm | 6299 | 1.7597 | 0.3751 |

## 产物

- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/scored_rows.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/summary_by_slice.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/pairwise_deltas.csv`
- `docs/analysis/2026-07/2026-07-02-tmax-distribution-p0-anchor-scorecard-v1.json`
