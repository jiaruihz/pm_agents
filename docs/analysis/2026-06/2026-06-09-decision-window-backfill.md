# Decision Window Backfill Report

> generated_at_utc: `2026-06-08T17:00:43.313973+00:00`
> db: `/home/rui/projects/pm_agent/runtime/weather.db`
> mode: `apply`

## Parameters

- max paper age min: `360.0`
- max orderbook age min: `180.0`
- wear cents added to selected best ask: `0.005`

## Backfill Result

- missing candidates scanned: `10091`
- backfillable: `2186`
- skipped no city/date anchor: `1754`
- skipped no near paper model: `4525`
- skipped no near orderbook: `1626`

## Before

| missing | rows | eligible | live_filled | paper_ordered |
|---:|---:|---:|---:|---:|
| `0` | 13456 | 4453 | 475 | 2753 |
| `1` | 10437 | 3388 | 45 | 133 |

## After

| missing | rows | eligible | live_filled | paper_ordered |
|---:|---:|---:|---:|---:|
| `0` | 15642 | 5328 | 511 | 2827 |
| `1` | 8251 | 2513 | 9 | 59 |

## Notes

- Backfilled rows are marked with `decision_window_source='orderbook_backfill'`.
- `decision_snapshot_ts_utc` is the city/date anchor from complete candidates; raw orderbook uses latest snapshot at or before that anchor.
- This is an analysis DB repair only and does not change N100 live behavior.
