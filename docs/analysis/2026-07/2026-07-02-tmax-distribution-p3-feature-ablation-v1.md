# Tmax Distribution P3 Feature Ablation v1

> generated_at_utc: `2026-07-09T03:39:42+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: offline mechanism/proper-scoring diagnostic only; no live runner/order behavior changed.

## 结论

P2 没有把 regime 丢掉；P3 的结果更准确地说是：`regime` 现在应作为概率模型里的机制特征，而不是直接写成买/不买规则。

- 可评分分母仍是 8347 rows / 48 dates / 36 cities，日期 `2026-05-19`..`2026-07-07`。
- Expanding forward 上 market-local logloss `0.6489`；最佳非 market 是 `loo_no_boundary_blend` `0.5864`，delta `-0.0625`，date-CI [`-0.0995`, `-0.0162`]。
- 路径/边界/天气/regime/city-source 的逐层 ablation 说明：信号不是单一 regime 标签贡献，也不是单纯 city id 记忆；但 forward 仍只有 6 天，所以结论维持 `inconclusive_positive_signal`。
- 7/1 不能直接并进 PnL/score：canonical candidates/trades 已更新，但 atlas/state/final-winner/orderbook 分布评估层只完整结算到 6/26，6/27+ 多数 state 没 label。

## 人话解释

之前的策略像是“先认出天气形态，再按形态买某一档”。P2/P3 换成“先估 Tmax 分布，再看盘口是否便宜”。regime 不是没用了，而是从方向盘变成传感器：它帮模型判断分布，但不能单独决定交易。

## Feature Stack

- `market_recal`: 只看盘口局部分布，作为市场校准基线。
- `mkt_path_core`: 加已实现路径、forecast peak clock、升温趋势和 running-max freshness。
- `mkt_boundary`: 加 bracket 边界/小数位置，例如当前温度在当前档内靠近下沿还是上沿。
- `mkt_meteo`: 加 wind/cloud/humidity/dewpoint 数值。
- `mkt_regime`: 加 atlas 的 day/intraday/moisture/wind/running-max/solar regime。
- `mkt_city_source`: 加 city、city_family、forecast_source，测试城市/源偏移。

## Fixed Forward 2026-06-21+

| method | n | dates | logloss | delta_vs_market | ci_low | ci_high | brier | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| market_local_norm | 2048 | 15 | 0.6489 | +0.0000 | +0.0000 | +0.0000 | 0.3311 | 76.0% | 0.6578 |
| market_recal_blend | 2048 | 15 | 0.5918 | -0.0571 | -0.0920 | -0.0103 | 0.3301 | 75.4% | 0.6629 |
| mkt_path_core_blend | 2048 | 15 | 0.5913 | -0.0576 | -0.0953 | -0.0090 | 0.3306 | 75.7% | 0.6583 |
| mkt_boundary_blend | 2048 | 15 | 0.5925 | -0.0564 | -0.0943 | -0.0070 | 0.3308 | 75.6% | 0.6568 |
| mkt_meteo_blend | 2048 | 15 | 0.5885 | -0.0604 | -0.0968 | -0.0099 | 0.3290 | 75.6% | 0.6579 |
| mkt_regime_blend | 2048 | 15 | 0.5869 | -0.0620 | -0.0984 | -0.0114 | 0.3281 | 75.6% | 0.6581 |
| mkt_city_source_blend | 2048 | 15 | 0.5854 | -0.0635 | -0.1014 | -0.0141 | 0.3271 | 75.7% | 0.6594 |

## Expanding Forward 2026-06-21+

| method | n | dates | logloss | delta_vs_market | ci_low | ci_high | brier | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| market_local_norm | 2048 | 15 | 0.6489 | +0.0000 | +0.0000 | +0.0000 | 0.3311 | 76.0% | 0.6578 |
| market_recal_blend | 2048 | 15 | 0.5917 | -0.0571 | -0.0932 | -0.0102 | 0.3301 | 75.5% | 0.6639 |
| mkt_path_core_blend | 2048 | 15 | 0.5918 | -0.0571 | -0.0945 | -0.0106 | 0.3305 | 75.8% | 0.6593 |
| mkt_boundary_blend | 2048 | 15 | 0.5915 | -0.0574 | -0.0944 | -0.0105 | 0.3300 | 75.8% | 0.6587 |
| mkt_meteo_blend | 2048 | 15 | 0.5887 | -0.0602 | -0.0970 | -0.0108 | 0.3289 | 75.9% | 0.6596 |
| mkt_regime_blend | 2048 | 15 | 0.5878 | -0.0611 | -0.0970 | -0.0125 | 0.3282 | 75.7% | 0.6595 |
| mkt_city_source_blend | 2048 | 15 | 0.5866 | -0.0623 | -0.1008 | -0.0164 | 0.3271 | 75.6% | 0.6606 |

## Leave-One-Family-Out

这里看的是 expanding forward：相对 full `mkt_city_source_blend`，删掉某一族特征后 logloss 变好还是变差。正数表示删掉后更差，该特征族有帮助；负数表示删掉后反而更好，该族可能噪声/过拟合。

| method | logloss | delta_vs_full |
| --- | --- | --- |
| mkt_city_source_blend | 0.5866 | +0.0000 |
| loo_no_boundary_blend | 0.5864 | -0.0002 |
| loo_no_meteo_blend | 0.5879 | +0.0013 |
| loo_no_regime_blend | 0.5881 | +0.0015 |
| loo_no_city_source_blend | 0.5878 | +0.0012 |

## Daily Check

| date | n | market | loo_no_boundary_blend | delta |
|---|---:|---:|---:|---:|
| 2026-06-21 | 201 | 0.6339 | 0.6241 | -0.0097 |
| 2026-06-22 | 215 | 0.4794 | 0.4505 | -0.0289 |
| 2026-06-23 | 146 | 0.5777 | 0.5689 | -0.0088 |
| 2026-06-25 | 222 | 0.7516 | 0.6701 | -0.0815 |
| 2026-06-26 | 235 | 0.5263 | 0.5245 | -0.0017 |
| 2026-06-27 | 202 | 0.7518 | 0.5654 | -0.1865 |
| 2026-06-28 | 28 | 0.7493 | 0.7171 | -0.0322 |
| 2026-06-29 | 81 | 0.5260 | 0.5366 | +0.0105 |
| 2026-06-30 | 155 | 0.9727 | 0.6641 | -0.3087 |
| 2026-07-01 | 132 | 0.5973 | 0.6054 | +0.0081 |
| 2026-07-02 | 32 | 0.5999 | 0.5426 | -0.0572 |
| 2026-07-03 | 80 | 0.5409 | 0.5543 | +0.0134 |
| 2026-07-05 | 155 | 0.7777 | 0.6927 | -0.0850 |
| 2026-07-06 | 106 | 0.6505 | 0.6398 | -0.0107 |
| 2026-07-07 | 58 | 0.4569 | 0.4485 | -0.0084 |

## Bucket Check

| actual_bucket | n | market | loo_no_boundary_blend | delta |
|---|---:|---:|---:|---:|
| current | 989 | 0.2471 | 0.2465 | -0.0006 |
| d1 | 465 | 0.9135 | 0.9010 | -0.0125 |
| d2 | 307 | 1.1340 | 1.1168 | -0.0172 |
| tail | 287 | 1.0857 | 0.6804 | -0.4053 |

## EV Shadow Check

同一批 P3 predictions 接 P2 的真实 ask 表达选择，`model_edge >= 0.02`，每个 city-date-hour 只保留一个最高 edge 表达：

| method | rows | dates | cost | pnl | ROI | CI | mix |
|---|---:|---:|---:|---:|---:|---|---|
| market_recal_blend | 303 | 15 | 150.53 | +7.47 | +5.0% | [-6.4%, +15.1%] | YES 54 / curNO 53 / d1NO 96 / d2NO 100 |
| loo_no_city_source_blend | 511 | 15 | 231.48 | +8.52 | +3.7% | [-10.7%, +16.9%] | YES 102 / curNO 173 / d1NO 146 / d2NO 90 |
| mkt_regime_blend | 511 | 15 | 231.48 | +8.52 | +3.7% | [-10.7%, +16.9%] | YES 102 / curNO 173 / d1NO 146 / d2NO 90 |
| mkt_city_source_blend | 541 | 15 | 253.55 | +7.45 | +2.9% | [-10.3%, +15.9%] | YES 116 / curNO 158 / d1NO 169 / d2NO 98 |
| loo_no_regime_blend | 495 | 15 | 227.60 | +5.40 | +2.4% | [-11.3%, +15.7%] | YES 106 / curNO 133 / d1NO 159 / d2NO 97 |

## Data Inventory / 7.1 Boundary

- `fact_signal_candidates`: `{'rows': 48857, 'min_date': '2026-05-05', 'max_date': '2026-07-09', 'max_built_at_utc': '2026-07-08T09:18:35.918613+00:00'}`
- `fact_trades`: `{'rows': 4533, 'min_date': '2026-05-06', 'max_date': '2026-07-07', 'max_built_at_utc': '2026-07-08T09:17:24.051488+00:00'}`
- `settlement_outcomes`: `{'rows': 29715, 'min_date': '2026-05-04', 'max_date': '2026-07-07', 'cities': 49}`

Recent atlas labeled-state coverage:

| target_date | states | cities | labeled |
| --- | --- | --- | --- |
| 2026-06-29 | 166 | 24 | 166 |
| 2026-06-30 | 252 | 32 | 252 |
| 2026-07-01 | 229 | 32 | 229 |
| 2026-07-02 | 53 | 10 | 53 |
| 2026-07-03 | 150 | 22 | 150 |
| 2026-07-04 | 135 | 15 | 127 |
| 2026-07-05 | 229 | 26 | 229 |
| 2026-07-06 | 156 | 19 | 151 |
| 2026-07-07 | 79 | 10 | 79 |
| 2026-07-08 | 44 | 9 | 0 |

Recent orderbook snapshot file counts:

| date | files |
| --- | --- |
| 2026-07-02 | 81 |
| 2026-07-03 | 83 |
| 2026-07-04 | 68 |
| 2026-07-05 | 87 |
| 2026-07-06 | 76 |
| 2026-07-07 | 96 |
| 2026-07-08 | 91 |
| 2026-07-09 | 44 |

解释：7/1 的机会和少量 trade 行存在，但 P3 这种 distribution scoring 需要逐 city-hour 的 final winner 和同一时刻 ladder quote。现在 6/27 起 state rows 多数没有 final label，6/30 也只有极少 orderbook 文件，所以 7/1 暂时只能作为 forward telemetry/pending，不应算 ROI 或 logloss。

## Verdict

significance=FAIL/NA; baseline=PARTIAL_PASS; forward=FAIL/THIN; conclusion=`inconclusive_positive_signal`。

下一步不是再加硬 gate，而是补齐 6/27+ 的 state label/orderbook first-seen，然后把 P2 EV shadow 接真实 depth/fill/size 约束。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p3_feature_ablation_v1/fixed_forward_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p3_feature_ablation_v1/expanding_forward_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p3_feature_ablation_v1/model_selection.csv`
- `docs/analysis/2026-07/2026-07-02-tmax-distribution-p3-feature-ablation-v1.json`
