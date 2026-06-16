# Range RV Shadow Status v0

> generated_at_utc: `2026-06-16T14:05:28.761024+00:00`
> target_metric: `forecast_bounded_range_rv_shadow_forward_telemetry_v0`
> journal: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/remote_pm_agent/range_rv_shadow_v0/shadow_journal.jsonl`
> pm_history_dir: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/cache/pm_history`

## Data Snapshot

- Evidence layer: zero-notional shadow telemetry from `scripts/ops/range_rv_shadow_v0.py`, plus `pm_history` settlement truth when available.
- This report is not live PnL. Every journal row must keep `no_order_placed=true`.
- journal_rows: `1274`.
- snapshot range: `2026-06-14T17:00:20Z` -> `2026-06-16T13:00:51Z`.
- event_dates: `2026-06-14, 2026-06-15, 2026-06-16, 2026-06-17`.
- all_no_order_placed: `True`.

### Mandatory SQL Self-Check

```json
{
  "candidate_coverage": {
    "eligible": 10658,
    "live_filled": 348,
    "paper_ordered": 4066,
    "rows": 30865
  },
  "max_fact_built_at_utc": "2026-06-16T09:47:20.726087+00:00",
  "order_fill_coverage": [
    {
      "orders": 33,
      "status": "error",
      "with_fill": 0
    },
    {
      "orders": 961,
      "status": "submitted",
      "with_fill": 855
    }
  ],
  "settlement_status_distribution": [
    {
      "rows": 150,
      "settlement_status": ""
    },
    {
      "rows": 4250,
      "settlement_status": "settled"
    }
  ],
  "trade_class_distribution": [
    {
      "rows": 855,
      "trade_class": "live_real"
    },
    {
      "rows": 624,
      "trade_class": "live_simulated"
    },
    {
      "rows": 2285,
      "trade_class": "paper"
    },
    {
      "rows": 636,
      "trade_class": "snapshot_replay"
    }
  ]
}
```

## Current Verdict

`inconclusive / keep collecting shadow data`: the N100 runner is healthy, but settled forward evidence is still too thin. Do not convert this to paper/live until the deduped settled sample passes the support, baseline, forward, and capacity gates defined in the live-standard report.

## Event-Date Funnel

| event_date | shadow_rows | settled_rows | unsettled_rows | dedup_latest_rows | dedup_groups | dedup_pnl | dedup_roi | dedup_hit | unsettled_status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-14 | 2 | 2 | 0 | 1 | 1 | +0.104 | +11.6% | +100.0% | {} |
| 2026-06-15 | 310 | 310 | 0 | 16 | 16 | -0.394 | -3.5% | +68.8% | {} |
| 2026-06-16 | 722 | 0 | 722 | 0 | 0 | +0.000 | NA | NA | {"missing_event": 722} |
| 2026-06-17 | 240 | 0 | 240 | 0 | 0 | +0.000 | NA | NA | {"missing_event": 240} |

## Dedup Latest Settled By City

| city | rows | groups | cities | dates | cost | pnl | roi | hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Miami | 2 | 2 | 1 | 2 | 1.799 | -0.799 | -44.4% | +50.0% |
| Karachi | 1 | 1 | 1 | 1 | 0.529 | +0.471 | +89.0% | +100.0% |
| Singapore | 1 | 1 | 1 | 1 | 0.550 | +0.450 | +81.8% | +100.0% |
| Munich | 1 | 1 | 1 | 1 | 0.735 | +0.265 | +36.1% | +100.0% |
| Lucknow | 1 | 1 | 1 | 1 | 0.770 | +0.230 | +29.9% | +100.0% |
| Shanghai | 1 | 1 | 1 | 1 | 0.781 | +0.219 | +28.0% | +100.0% |
| Guangzhou | 1 | 1 | 1 | 1 | 0.790 | +0.210 | +26.6% | +100.0% |
| Warsaw | 1 | 1 | 1 | 1 | 0.845 | +0.155 | +18.3% | +100.0% |
| LA | 1 | 1 | 1 | 1 | 0.867 | +0.133 | +15.3% | +100.0% |
| Madrid | 1 | 1 | 1 | 1 | 0.926 | +0.074 | +8.0% | +100.0% |
| Seattle | 1 | 1 | 1 | 1 | 0.939 | +0.061 | +6.5% | +100.0% |
| Jeddah | 1 | 1 | 1 | 1 | 0.950 | +0.050 | +5.3% | +100.0% |
| Manila | 1 | 1 | 1 | 1 | 0.010 | -0.010 | -100.0% | +0.0% |
| Chengdu | 1 | 1 | 1 | 1 | 0.239 | -0.239 | -100.0% | +0.0% |
| NYC | 1 | 1 | 1 | 1 | 0.727 | -0.727 | -100.0% | +0.0% |
| Ankara | 1 | 1 | 1 | 1 | 0.833 | -0.833 | -100.0% | +0.0% |

## Dedup Latest Settled By Expression

| expression | rows | groups | cities | dates | cost | pnl | roi | hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| outside_no | 12 | 12 | 11 | 2 | 9.215 | -1.215 | -13.2% | +66.7% |
| inside_yes | 5 | 5 | 5 | 1 | 3.075 | +0.925 | +30.1% | +80.0% |

## Read-Me For Future Runs

- Primary unit for conclusions: `city + event_date + forecast_source + model_version`, using dedup latest and dedup first as timing sensitivity checks.
- Raw shadow rows are useful for telemetry and persistence, but not for strategy conclusions because the same city-day can trigger every 30 minutes.
- Actual orders remain zero unless a separate deploy changes the execution mode; this report must not be mixed with `live_real` PnL.
