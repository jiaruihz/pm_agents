# Tmax Distribution P3 Feature Ablation v1

> generated_at_utc: `2026-07-02T15:16:47+00:00`
> atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: offline mechanism/proper-scoring diagnostic only; no live runner/order behavior changed.

## 结论

P2 没有把 regime 丢掉；P3 的结果更准确地说是：`regime` 现在应作为概率模型里的机制特征，而不是直接写成买/不买规则。

- 可评分分母仍是 6519 rows / 39 dates / 36 cities，日期 `2026-05-19`..`2026-06-26`。
- Expanding forward 上 market-local logloss `0.5654`；最佳非 market 是 `loo_no_city_source_blend` `0.5231`，delta `-0.0423`，date-CI [`-0.0756`, `+0.0010`]。
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
| market_local_norm | 1025 | 6 | 0.5654 | +0.0000 | +0.0000 | +0.0000 | 0.3022 | 79.0% | 0.6788 |
| market_recal_blend | 1025 | 6 | 0.5462 | -0.0192 | -0.0441 | +0.0038 | 0.3019 | 78.0% | 0.6882 |
| mkt_path_core_blend | 1025 | 6 | 0.5464 | -0.0190 | -0.0464 | +0.0145 | 0.3055 | 78.0% | 0.6998 |
| mkt_boundary_blend | 1025 | 6 | 0.5377 | -0.0276 | -0.0612 | +0.0070 | 0.2971 | 77.8% | 0.7016 |
| mkt_meteo_blend | 1025 | 6 | 0.5355 | -0.0299 | -0.0668 | +0.0101 | 0.2941 | 78.5% | 0.7027 |
| mkt_regime_blend | 1025 | 6 | 0.5224 | -0.0430 | -0.0772 | -0.0039 | 0.2869 | 79.2% | 0.6992 |
| mkt_city_source_blend | 1025 | 6 | 0.5235 | -0.0418 | -0.0813 | +0.0049 | 0.2875 | 78.7% | 0.7038 |

## Expanding Forward 2026-06-21+

| method | n | dates | logloss | delta_vs_market | ci_low | ci_high | brier | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| market_local_norm | 1025 | 6 | 0.5654 | +0.0000 | +0.0000 | +0.0000 | 0.3022 | 79.0% | 0.6788 |
| market_recal_blend | 1025 | 6 | 0.5463 | -0.0191 | -0.0443 | +0.0039 | 0.3018 | 77.7% | 0.6887 |
| mkt_path_core_blend | 1025 | 6 | 0.5502 | -0.0152 | -0.0416 | +0.0162 | 0.3050 | 77.7% | 0.6997 |
| mkt_boundary_blend | 1025 | 6 | 0.5421 | -0.0233 | -0.0567 | +0.0169 | 0.2984 | 77.6% | 0.7022 |
| mkt_meteo_blend | 1025 | 6 | 0.5410 | -0.0244 | -0.0659 | +0.0302 | 0.2954 | 78.1% | 0.7045 |
| mkt_regime_blend | 1025 | 6 | 0.5231 | -0.0423 | -0.0756 | +0.0010 | 0.2868 | 78.7% | 0.7001 |
| mkt_city_source_blend | 1025 | 6 | 0.5268 | -0.0386 | -0.0773 | +0.0160 | 0.2879 | 78.9% | 0.7043 |

## Leave-One-Family-Out

这里看的是 expanding forward：相对 full `mkt_city_source_blend`，删掉某一族特征后 logloss 变好还是变差。正数表示删掉后更差，该特征族有帮助；负数表示删掉后反而更好，该族可能噪声/过拟合。

| method | logloss | delta_vs_full |
| --- | --- | --- |
| mkt_city_source_blend | 0.5268 | +0.0000 |
| loo_no_boundary_blend | 0.5250 | -0.0018 |
| loo_no_meteo_blend | 0.5284 | +0.0017 |
| loo_no_regime_blend | 0.5341 | +0.0073 |
| loo_no_city_source_blend | 0.5231 | -0.0037 |

## Daily Check

| date | n | market | loo_no_city_source_blend | delta |
|---|---:|---:|---:|---:|
| 2026-06-21 | 168 | 0.5251 | 0.5177 | -0.0074 |
| 2026-06-22 | 182 | 0.4770 | 0.3736 | -0.1033 |
| 2026-06-23 | 142 | 0.5286 | 0.5093 | -0.0193 |
| 2026-06-24 | 144 | 0.6319 | 0.6690 | +0.0371 |
| 2026-06-25 | 182 | 0.7163 | 0.6261 | -0.0902 |
| 2026-06-26 | 207 | 0.5220 | 0.4761 | -0.0459 |

## Bucket Check

| actual_bucket | n | market | loo_no_city_source_blend | delta |
|---|---:|---:|---:|---:|
| current | 580 | 0.2556 | 0.1897 | -0.0659 |
| d1 | 203 | 0.8591 | 0.8674 | +0.0083 |
| d2 | 140 | 1.2632 | 1.2675 | +0.0043 |
| tail | 102 | 0.7844 | 0.7112 | -0.0731 |

## EV Shadow Check

同一批 P3 predictions 接 P2 的真实 ask 表达选择，`model_edge >= 0.02`，每个 city-date-hour 只保留一个最高 edge 表达：

| method | rows | dates | cost | pnl | ROI | CI | mix |
|---|---:|---:|---:|---:|---:|---|---|
| loo_no_city_source_blend | 446 | 6 | 245.64 | +28.36 | +11.5% | [+1.0%, +23.0%] | YES 167 / curNO 98 / d1NO 137 / d2NO 44 |
| mkt_regime_blend | 446 | 6 | 245.64 | +28.36 | +11.5% | [+1.0%, +23.0%] | YES 167 / curNO 98 / d1NO 137 / d2NO 44 |
| mkt_city_source_blend | 561 | 6 | 301.24 | +33.76 | +11.2% | [+5.3%, +17.7%] | YES 215 / curNO 115 / d1NO 170 / d2NO 61 |
| loo_no_regime_blend | 535 | 6 | 277.20 | +24.80 | +8.9% | [+6.4%, +12.1%] | YES 236 / curNO 84 / d1NO 160 / d2NO 55 |
| market_recal_blend | 297 | 6 | 154.00 | -1.00 | -0.7% | [-11.8%, +12.7%] | YES 138 / curNO 7 / d1NO 107 / d2NO 45 |

## Data Inventory / 7.1 Boundary

- `fact_signal_candidates`: `{'rows': 42652, 'min_date': '2026-05-05', 'max_date': '2026-07-04', 'max_built_at_utc': '2026-07-02T15:12:46.393239+00:00'}`
- `fact_trades`: `{'rows': 4414, 'min_date': '2026-05-06', 'max_date': '2026-07-01', 'max_built_at_utc': '2026-07-02T15:12:24.249106+00:00'}`
- `settlement_outcomes`: `{'rows': 24050, 'min_date': '2026-05-04', 'max_date': '2026-06-28', 'cities': 49}`

Recent atlas labeled-state coverage:

| target_date | states | cities | labeled |
| --- | --- | --- | --- |
| 2026-06-20 | 249 | 32 | 249 |
| 2026-06-21 | 371 | 36 | 371 |
| 2026-06-22 | 323 | 31 | 323 |
| 2026-06-23 | 251 | 32 | 251 |
| 2026-06-24 | 243 | 32 | 243 |
| 2026-06-25 | 396 | 36 | 396 |
| 2026-06-26 | 368 | 36 | 368 |
| 2026-06-27 | 344 | 36 | 0 |
| 2026-06-28 | 30 | 12 | 0 |
| 2026-06-29 | 55 | 21 | 0 |

Recent orderbook snapshot file counts:

| date | files |
| --- | --- |
| 2026-06-23 | 48 |
| 2026-06-24 | 48 |
| 2026-06-25 | 48 |
| 2026-06-26 | 48 |
| 2026-06-27 | 48 |
| 2026-06-28 | 23 |
| 2026-06-29 | 3 |
| 2026-06-30 | 1 |

解释：7/1 的机会和少量 trade 行存在，但 P3 这种 distribution scoring 需要逐 city-hour 的 final winner 和同一时刻 ladder quote。现在 6/27 起 state rows 多数没有 final label，6/30 也只有极少 orderbook 文件，所以 7/1 暂时只能作为 forward telemetry/pending，不应算 ROI 或 logloss。

## Verdict

significance=FAIL/NA; baseline=PARTIAL_PASS; forward=FAIL/THIN; conclusion=`inconclusive_positive_signal`。

下一步不是再加硬 gate，而是补齐 6/27+ 的 state label/orderbook first-seen，然后把 P2 EV shadow 接真实 depth/fill/size 约束。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_distribution_p3_feature_ablation_v1/fixed_forward_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p3_feature_ablation_v1/expanding_forward_summary.csv`
- `docs/analysis/2026-07/generated/tmax_distribution_p3_feature_ablation_v1/model_selection.csv`
- `docs/analysis/2026-07/2026-07-02-tmax-distribution-p3-feature-ablation-v1.json`
