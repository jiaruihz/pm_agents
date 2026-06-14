# Forecast Quality Shadow Candidates v0

> generated_at_utc: `2026-06-13T02:41:39.973414+00:00`
> order_type: `zero_notional_shadow`; no live order action.

## 数据快照

- DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- fact_trades MAX(fact_built_at_utc): `2026-06-13T02:32:52.534281+00:00`
- fact_signal_candidates MAX(fact_built_at_utc): `2026-06-13T02:33:01.564137+00:00`
- CLOB coverage gate: `False`; live_real PnL/ROI/rank/curve not published.

## Profile

```text
BUY_NO ecmwf, forecast_quality_low=0, 0.40<=no_cost<=0.75, no_edge>=0.10, city-date top1, $5/order
```

- fresh event_date min: `2026-06-13`
- orders: `3`
- active dates: `1`
- avg orders / active day: `3.00`
- max orders / day: `3`
- max daily notional if tiny-live sized later: `$15.00`
- min shares @ $5/order: `6.99`
- cities: `Amsterdam, Lucknow, Moscow`

## By Date

| date | orders | cities | direction | shadow notional |
| --- | --- | --- | --- | --- |
| 2026-06-13 | 3 | Amsterdam,Lucknow,Moscow | BUY_NO | $15.00 |

## Files

- CSV: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-13-forecast-quality-shadow-candidates-v0.csv`
