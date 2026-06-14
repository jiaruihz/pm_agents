# Forecast Quality BUY_NO Shadow Journal v0

> generated_at_utc: `2026-06-13T11:08:31.098163+00:00`
> journal_schema_version: `forecast_quality_buy_no_shadow_journal_v0`
> scope: local zero-notional shadow only; no N100/live config changed; no live orders.

## 数据快照

- DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- DB last_modified_utc: `2026-06-13T02:33:01.796274+00:00`
- fact_trades MAX(fact_built_at_utc): `2026-06-13T02:32:52.534281+00:00`
- fact_signal_candidates MAX(fact_built_at_utc): `2026-06-13T02:33:01.564137+00:00`
- CLOB coverage gate: `False`; live_real PnL/ROI/rank/curve not published.

### 5 行 SQL 自检

```json
{
  "fact_trades_max_built_at_utc": "2026-06-13T02:32:52.534281+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "rows": 856
    },
    {
      "trade_class": "live_simulated",
      "rows": 624
    },
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": "",
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4251
    }
  ],
  "candidate_coverage": {
    "rows": 28197,
    "eligible": 9583,
    "paper_ordered": 3591,
    "live_filled": 348
  },
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 962,
      "with_fill": 856
    }
  ]
}
```

## Shadow Rule

```text
ecmwf_buy_no_exclude_low_edge010_cost40_75_cityday_top1_v0
BUY_NO only; model_version=ecmwf; forecast_quality_low=0;
0.40<=no_cost<=0.75; no_edge>=0.10; city-date top1; hypothetical $5/order;
order_type=zero_notional_shadow
```

## Journal Summary

- new rows this run: `3`
- journal rows after merge: `3`
- active dates: `1`
- cities: `Amsterdam, Lucknow, Moscow`
- max daily hypothetical notional: `$15.00`
- min shares @ $5/order: `6.99`

| event_date | rows | cities | hypothetical notional |
| --- | --- | --- | --- |
| 2026-06-13 | 3 | Amsterdam,Lucknow,Moscow | $15.00 |

## Readiness

- 这是 forward shadow journal 的第一批记录，不构成 live edge 结论。
- 因上一轮 fresh rerun 是 `shadow_only` 且 CLOB gate=false，当前只记录，不进入 $5/order tiny live。

## Files

- JSON summary: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-13-forecast-quality-buy-no-shadow-journal-v0.json`
- JSONL journal: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-13-forecast-quality-buy-no-shadow-journal-v0.jsonl`
- source CSV: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-06/2026-06-13-forecast-quality-shadow-candidates-v0.csv`
