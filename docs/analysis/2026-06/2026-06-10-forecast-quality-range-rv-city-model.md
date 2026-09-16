# Forecast Quality Range RV City/Model/Data-Depth Study

> generated_at_utc: `2026-06-09T17:34:55.052214+00:00`
> target_metric: `forecast_quality_range_rv_city_model_data_depth_value`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed; no live action.

## 一句话

- 现在最值得继续试的不是“严格 forecast quality 硬过滤”，而是 `adjacent3 + cost<=0.85 + medium_quality` 这个软过滤版本。
- 城市/模型确实有关系，但目前每个 city+model 的样本都很薄，不能只按 holdout winner 选城市。
- 数据积累有一点关系：有历史样本的 bucket 更容易解释，但当前样本太少，不能证明“样本越多越赚钱”。

## 数据快照

- fact_signal_candidates rows: `25117`
- fact_trades rows: `5473`
- decision_sets: `270`
- range rows: `526`
- candidate rows: `88`
- train: `2026-05-06` -> `2026-05-28` (21 dates)
- holdout: `2026-05-29` -> `2026-06-07` (9 dates)

## 候选版本 vs 不筛 baseline

| version | train rows | train ROI | train PnL | holdout rows | holdout ROI | holdout PnL |
|---|---:|---:|---:|---:|---:|---:|
| no_filter adjacent3 cost<=0.85 | 126 | +27.4% | +20.84 | 38 | +26.9% | +6.15 |
| medium_quality adjacent3 cost<=0.85 | 68 | +26.7% | +12.44 | 20 | +33.5% | +4.52 |
| train-selected city+model probe | 30 | +40.2% | +8.03 | 3 | +12.6% | +0.22 |

## Time-aligned orderbook 复核

这个表更接近真实可成交性：每条腿用 `orderbook_snapshot_ts <= decision_snapshot_ts` 的历史盘口 best ask。

| version | matched / rows | train rows | train ROI | train PnL | holdout rows | holdout ROI | holdout PnL |
|---|---:|---:|---:|---:|---:|---:|---:|
| no_filter adjacent3 cost<=0.85 | 87 / 164 | 49 | -3.0% | -1.51 | 38 | +10.4% | +3.57 |
| medium_quality adjacent3 cost<=0.85 | 46 / 88 | 26 | +8.5% | +2.03 | 20 | +17.8% | +3.02 |

## 按模型

| model_version | total | train | train_roi | train_pnl | holdout | holdout_roi | holdout_pnl | avg_hist_n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ecmwf | 53 | 41 | +28.3% | +7.73 | 12 | +40.5% | +3.17 | 4.1 |
| gfs | 35 | 27 | +24.4% | +4.71 | 8 | +23.9% | +1.35 | 7.0 |

## 按数据积累

| data_depth_bucket | total | train | train_roi | train_pnl | holdout | holdout_roi | holdout_pnl | avg_hist_n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| prior_3_5 | 22 | 14 | +14.1% | +1.36 | 8 | +46.7% | +2.55 | 4.0 |
| prior_6_10 | 12 | 8 | +1.2% | +0.06 | 4 | +28.7% | +0.89 | 7.0 |
| prior_1_2 | 34 | 30 | +34.1% | +7.12 | 4 | +23.9% | +0.58 | 1.8 |
| no_prior | 16 | 15 | +36.0% | +3.71 | 1 | +45.0% | +0.31 | 0.0 |
| prior_11_plus | 4 | 1 | +24.9% | +0.20 | 3 | +10.7% | +0.19 | 12.7 |

## 按城市 top30

| city | total | train | train_roi | train_pnl | holdout | holdout_roi | holdout_pnl | avg_hist_n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SaoPaulo | 3 | 1 | +21.2% | +0.18 | 2 | +69.6% | +0.82 | 1.5 |
| Chengdu | 2 | 0 | NA | +0.00 | 2 | +61.4% | +0.76 | 4.5 |
| Moscow | 1 | 0 | NA | +0.00 | 1 | +95.9% | +0.49 | 3.0 |
| Jeddah | 1 | 0 | NA | +0.00 | 1 | +61.3% | +0.38 | 2.0 |
| Shanghai | 5 | 4 | +74.6% | +1.71 | 1 | +50.4% | +0.33 | 5.0 |
| Singapore | 1 | 0 | NA | +0.00 | 1 | +45.0% | +0.31 | 0.0 |
| London | 3 | 2 | -33.9% | -0.51 | 1 | +43.6% | +0.30 | 11.0 |
| Ankara | 2 | 1 | +44.9% | +0.31 | 1 | +41.8% | +0.29 | 4.0 |
| Amsterdam | 2 | 1 | +58.0% | +0.37 | 1 | +40.1% | +0.29 | 4.0 |
| Warsaw | 3 | 2 | +25.4% | +0.40 | 1 | +35.2% | +0.26 | 8.0 |
| Paris | 3 | 2 | +39.2% | +0.56 | 1 | +31.7% | +0.24 | 7.0 |
| Atlanta | 3 | 2 | +23.1% | +0.38 | 1 | +31.6% | +0.24 | 6.0 |
| Istanbul | 4 | 3 | -13.4% | -0.31 | 1 | +24.2% | +0.19 | 3.0 |
| Seattle | 2 | 1 | +29.0% | +0.23 | 1 | +22.7% | +0.19 | 4.0 |
| Chicago | 4 | 3 | -13.7% | -0.32 | 1 | +17.6% | +0.15 | 7.0 |
| Beijing | 6 | 6 | +29.8% | +0.92 | 0 | NA | +0.00 | NA |
| BuenosAires | 2 | 2 | +39.9% | +0.57 | 0 | NA | +0.00 | NA |
| Busan | 2 | 2 | +43.4% | +0.60 | 0 | NA | +0.00 | NA |
| CapeTown | 1 | 1 | +63.1% | +0.39 | 0 | NA | +0.00 | NA |
| Chongqing | 3 | 3 | +71.7% | +1.25 | 0 | NA | +0.00 | NA |
| Dallas | 1 | 1 | +37.0% | +0.27 | 0 | NA | +0.00 | NA |
| Guangzhou | 1 | 1 | +27.4% | +0.21 | 0 | NA | +0.00 | NA |
| Houston | 3 | 3 | +33.3% | +0.75 | 0 | NA | +0.00 | NA |
| KualaLumpur | 1 | 1 | +33.3% | +0.25 | 0 | NA | +0.00 | NA |
| LA | 3 | 3 | +26.8% | +0.63 | 0 | NA | +0.00 | NA |
| Lagos | 2 | 2 | +72.4% | +0.84 | 0 | NA | +0.00 | NA |
| Lucknow | 1 | 1 | +25.0% | +0.20 | 0 | NA | +0.00 | NA |
| Madrid | 3 | 3 | +32.8% | +0.74 | 0 | NA | +0.00 | NA |
| Manila | 1 | 1 | +45.5% | +0.31 | 0 | NA | +0.00 | NA |
| Munich | 1 | 1 | +34.2% | +0.26 | 0 | NA | +0.00 | NA |

## train-only 选出来的 city+model

| city | model |
| --- | --- |
| Beijing | ecmwf |
| Chongqing | ecmwf |
| Houston | gfs |
| LA | gfs |
| Madrid | ecmwf |
| Miami | gfs |
| Seoul | ecmwf |
| Shanghai | gfs |

## 当前可试探版本

- `range_rv_forecast_quality_probe_v0`: buy YES on model-mode adjacent3 range。
- 条件：`range_width=3`, `market_yes_price_sum<=0.85`, `medium_quality=1`。
- 不按城市 hard allowlist 直接砍；城市/model 只作为 size/risk tag 记录，因为 city+model 样本还太少。
- orderbook holdout 目前仍是正的，但样本很小；所以合理动作是 shadow/paper 或极小额受控 probe，不是直接扩大 live。
- 若要 live，必须小额、maker/限价、单 city-day notional 很小，并且必须单独走 deploy 流程。本报告本身不改 live。

## 结论等级

`proxy=positive`, `city_model_evidence=thin`, `executable=small_positive`, `conclusion=shadow_or_tiny_probe_candidate_only`. No live action.
