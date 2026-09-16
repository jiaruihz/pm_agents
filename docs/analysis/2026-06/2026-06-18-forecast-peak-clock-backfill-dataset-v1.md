# Forecast Peak Clock Backfill Dataset v1

Status: research_data_layer / not_live_ready_by_itself
Generated: 2026-07-03T22:42:59+00:00
Forecast source: Open-Meteo Single Runs fixed run: D-1 12:00 UTC

Target metric: `forecast_peak_clock_backfill_v1` = one reusable city-date table with GFS/ECMWF expected daily high time, expected high temperature, hourly-vector hash, and timezone metadata.

## Human Conclusion

这次补的是数据层，不是又调一个交易规则。结果是：current-YES 研究窗口里的 forecast peak clock 已经从一次性回测缓存，升级成 `runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv` 这张共享表。

它能解决模型层最大的口径缺口：以后判断“现在是不是接近当天预报最高温出现时间”，不必写死本地 13-15 点，也不必每个策略脚本各自去抓一次历史预报。

默认口径已改成 Open-Meteo Single Runs 的固定 D-1 12:00 UTC model run；这比 stitched historical forecast 更接近 PIT，因为每个 city-date 都绑定到目标日前已经发布的完整模型 run。

但它仍然是研究 backfill，不等于生产当时 snapshot 已经原生落盘；所以它能支持研究和 shadow telemetry，不能单独把策略推到 live 放大。

## Coverage

- universe: `csv`
- city-date rows: `1536` across `48` cities
- date range: `2026-05-19` .. `2026-06-20`
- GFS peak coverage: `1536` / `1536` = `100.0%`
- ECMWF peak coverage: `1536` / `1536` = `100.0%`
- both-model coverage: `1536` / `1536` = `100.0%`
- GFS/ECMWF peak agree <= 1h: `1026` / `1536` = `66.8%`

## Cache / Fetch

- api_source: `single_runs`
- run_day_offset: `1`
- run_day_offsets: `1,2,3`
- run_hour_utc: `12`
- fetch_missing: `True`
- promote_cache: `False`
- fetch_stats: `{'single_runs_runtime_cache': 3025, 'single_runs_fetched': 47}`
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
