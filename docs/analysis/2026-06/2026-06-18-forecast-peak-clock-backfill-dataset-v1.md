# Forecast Peak Clock Backfill Dataset v1

Status: research_data_layer / not_live_ready_by_itself
Generated: 2026-06-21T06:07:54+00:00

Target metric: `forecast_peak_clock_backfill_v1` = one reusable city-date table with GFS/ECMWF expected daily high time, expected high temperature, hourly-vector hash, and timezone metadata.

## Human Conclusion

这次补的是数据层，不是又调一个交易规则。结果是：current-YES 研究窗口里的 forecast peak clock 已经从一次性回测缓存，升级成 `runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv` 这张共享表。

它能解决模型层最大的口径缺口：以后判断“现在是不是接近当天预报最高温出现时间”，不必写死本地 13-15 点，也不必每个策略脚本各自去抓一次历史预报。

但它仍然是 historical forecast backfill，不等于生产当时 snapshot 已经原生落盘；所以它能支持研究和 shadow telemetry，不能单独把策略推到 live 放大。

## Coverage

- universe: `fact_signal_candidates`
- city-date rows: `1536` across `48` cities
- date range: `2026-05-19` .. `2026-06-20`
- GFS peak coverage: `1536` / `1536` = `100.0%`
- ECMWF peak coverage: `1536` / `1536` = `100.0%`
- both-model coverage: `1536` / `1536` = `100.0%`
- GFS/ECMWF peak agree <= 1h: `1063` / `1536` = `69.2%`

## Cache / Fetch

- fetch_missing: `True`
- promote_cache: `True`
- fetch_stats: `{'fetched': 94, 'runtime_cache': 2}`
- errors_kept: `0`

## Outputs

- CSV: `runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv`
- JSON: `runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1_summary.json`
- Script: `scripts/analysis/reheat_risk/build_forecast_peak_clock_backfill_dataset_v1.py`

## Trading Meaning

- current-YES: 可以把 `decision_hour_local - forecast_peak_hour_local` 当作模型特征，而不是硬写本地时间。
- higher-NO carry: 可以检查 NO carry 是否只在“预报峰值已过且预报最高温没有越过下一档”时成立。
- YES reversal: 可以把低价 YES 的反转条件改成“预报峰值尚未到/模型分歧大/forecast max 高于 running max”。

三道门：significance=NA, baseline=NA, forward=NA, conclusion=`research_data_layer`。这张表只是补数据口径，不直接给 live 动作。
