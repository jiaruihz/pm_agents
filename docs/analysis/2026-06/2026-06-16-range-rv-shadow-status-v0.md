# Range RV Shadow Status v0

> generated_at_utc: `2026-06-15T16:37:57.147598+00:00`
> target_metric: `forecast_bounded_range_rv_shadow_forward_telemetry_v0`
> journal: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/remote_pm_agent/range_rv_shadow_v0/shadow_journal.jsonl`
> pm_history_dir: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/cache/pm_history`

## Data Snapshot

- Evidence layer: zero-notional shadow telemetry from `scripts/ops/range_rv_shadow_v0.py`, plus `pm_history` settlement truth when available.
- This report is not live PnL. Every journal row must keep `no_order_placed=true`.
- journal_rows: `632`.
- snapshot range: `2026-06-14T17:00:20Z` -> `2026-06-15T15:30:42Z`.
- event_dates: `2026-06-14, 2026-06-15, 2026-06-16`.
- all_no_order_placed: `True`.

### Mandatory SQL Self-Check

```json
{
  "candidate_coverage": {
    "eligible": 10366,
    "live_filled": 348,
    "paper_ordered": 3968,
    "rows": 30140
  },
  "max_fact_built_at_utc": "2026-06-15T16:31:04.483297+00:00",
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
| 2026-06-15 | 303 | 0 | 303 | 0 | 0 | +0.000 | NA | NA | {"missing_event": 303} |
| 2026-06-16 | 327 | 0 | 327 | 0 | 0 | +0.000 | NA | NA | {"missing_event": 327} |

## Dedup Latest Settled By City

| city | rows | groups | cities | dates | cost | pnl | roi | hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Miami | 1 | 1 | 1 | 1 | 0.896 | +0.104 | +11.6% | +100.0% |

## Dedup Latest Settled By Expression

| expression | rows | groups | cities | dates | cost | pnl | roi | hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| outside_no | 1 | 1 | 1 | 1 | 0.896 | +0.104 | +11.6% | +100.0% |

## Read-Me For Future Runs

- Primary unit for conclusions: `city + event_date + forecast_source + model_version`, using dedup latest and dedup first as timing sensitivity checks.
- Raw shadow rows are useful for telemetry and persistence, but not for strategy conclusions because the same city-day can trigger every 30 minutes.
- Actual orders remain zero unless a separate deploy changes the execution mode; this report must not be mixed with `live_real` PnL.
