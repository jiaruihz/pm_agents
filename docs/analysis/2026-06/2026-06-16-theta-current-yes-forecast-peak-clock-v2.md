# Theta Current YES Forecast Peak Clock v2

Status: strategy_design / shadow_candidate_pending_data
Generated: 2026-06-16T09:51:14+00:00

Target metric: `forecast_peak_clock_current_yes` = buy the current running-max bracket YES while the temperature has not visibly faded, but only when the forecast peak clock says the day is already at or near its expected high.

## Data Self-Check

- fact_built_at_utc: `2026-06-16T09:47:20.726087+00:00`
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`
- settlement_status: `[{'settlement_status': None, 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`
- fact_signal_candidates coverage: `{'rows': 30865, 'eligible': 10658, 'paper_ordered': 4066, 'live_filled': 348}`
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`
- forecast-related fact columns: `['forecast_source']`

## Human Verdict

你这个方向是对的：`h13` 不应该是策略本体，它只是“接近预报峰值时段”的粗糙代理。真正的新策略应该看 forecast 给出的最高温小时和最高温 bracket。

但现有 6 月 current-YES replay 不能诚实回测这个字段：当时 snapshot 只保存了 `forecast_max_f`，没有保存 `forecast_peak_hour_local` 或 hourly forecast vector。历史 forecast cache 有小时曲线，但只覆盖到 2026-05-06；current-YES 回放从 2026-05-19 开始，所以 overlap=0。

因此这版结论不是 live-ready，而是一版清晰可执行的 shadow 策略定义：先把 `forecast_peak_hour_local` 落盘，再用同一套回放验证它是否真的优于固定 `h13`。

## Existing Proxy Evidence

| slice | orders | active days | orders/day | win | ROI +2c | model EV | avg ask | avg p | avg edge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| current_live_post_decline_proxy | 30 | 11 | 2.7 | +93.3% | +14.7% | $13.39 | 0.802 | 0.894 | +9.2% |
| old_fixed_h13_peak_forming_proxy | 19 | 12 | 1.6 | +89.5% | +17.1% | $11.71 | 0.762 | 0.873 | +11.1% |
| too_broad_peak_forming_proxy | 81 | 14 | 5.8 | +77.8% | -0.4% | $43.88 | 0.771 | 0.872 | +10.2% |

读法：`old_fixed_h13_peak_forming_proxy` 是旧的固定小时代理，不是最终规则。它说明“峰值形成中”可能有机会；`too_broad_peak_forming_proxy` 说明不能只看 `decline_c == 0`，太宽会把 h14/h15 的坏样本也吃进去。

## Forecast Cache Audit

- replay date range: `2026-05-19..2026-06-14`
- forecast cache date range: `2024-05-01..2026-05-06`
- replay/cache overlap dates: `0`
- can backtest v8 with forecast clock now: `False`

| source | files | hourly files | min date | max date | derived city-days | top UTC peak hours |
|---|---:|---:|---|---|---:|---|
| gfs_v4 | 67 | 67 | 2024-05-01 | 2026-05-06 | 29500 | 0:2185, 6:2002, 21:1772, 7:1707, 20:1669 |
| ecmwf_v4 | 52 | 52 | 2025-05-10 | 2026-04-29 | 18460 | 6:1887, 21:1545, 0:1252, 12:1065, 5:947 |
| hrrr_v5 | 14 | 14 | 2025-05-10 | 2026-04-29 | 4970 | 0:915, 21:877, 20:766, 22:763, 19:527 |

## Strategy v2 Rule

```text
branch: theta_current_yes_forecast_peak_clock_v2
side: BUY_YES current running-max bracket
state: decline_c <= 0.1C, i.e. still at observed running max / plateau
forecast clock: forecast_peak_hour_local is present
relative timing: -1 <= decision_hour_local - forecast_peak_hour_local <= +1
forecast bracket: forecast_max_native is inside current YES bracket, or forecast_max_native - running_native <= 0.5C equivalent
market: yes_current_ask >= 0.55 and available_notional_at_ask >= $5
model: p_yes_win >= 0.5 and p_yes_win - snapshot_ask >= 0.05
execution: before submit, refresh CLOB book; require fresh_ask <= snapshot_ask + 0.02 and top ask notional >= $5
dedupe: first eligible city-date-current bracket; max 2 brackets per city-date; max $5/order, $10/city-day in shadow only
```

Why these gates exist:

- `decline_c <= 0.1C` keeps this branch separate from the already-live post-decline branch.
- `decision_hour - forecast_peak_hour` replaces hardcoded local time. A Shanghai 13:00 and a Phoenix 15:00 can both be valid if they are near that city's forecast peak.
- `forecast_max_native inside current bracket` is the core physical condition: the forecast is not calling for a later bracket jump.
- The fresh-book guard handles the METAR update minute problem: if the quote reprices before we can hit it, the order is skipped.

## Required Data Change

每个 paper snapshot / live decision row 至少要新增这些字段：

- `forecast_peak_hour_local`
- `forecast_peak_hour_utc`
- `forecast_max_native`
- `forecast_peak_source` / `forecast_model_run_ts_utc`
- `forecast_values_hash`
- optional: `forecast_peak_hour_spread_minutes` for multi-model disagreement

没有这些字段，固定 h13 看起来赚钱也只能叫 proxy，不能叫 forecast-clock 策略过门。

## 8-Ring Coverage

- descriptive performance: partial, via fixed-hour proxy only
- statistical inference: not rerun here; use v1/v9 reports for fixed-hour proxy
- signal discrimination: partial, existing current-YES model only
- probability calibration: inherited from v10, not changed here
- execution microstructure: design includes fresh-book guard; no filled evidence yet
- capacity: shadow limit only, no live capacity claim
- portfolio correlation: not evaluated
- baseline/counterfactual: pending after forecast fields are logged

Conclusion gates: significance=NA, baseline=NA, forward=NA, conclusion=`inconclusive` for live; `shadow_candidate_pending_data` for telemetry.

## Output

- JSON: `docs/analysis/2026-06/2026-06-16-theta-current-yes-forecast-peak-clock-v2.json`
- Script: `scripts/analysis/reheat_risk/research_theta_current_yes_forecast_peak_clock_v2.py`
