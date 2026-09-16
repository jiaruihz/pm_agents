# All-YES Underround Snapshot Persistence v0

> generated_at_utc: `2026-06-13T19:23:04.673029+00:00`
> target_metric: `all_yes_underround_snapshot_persistence`
> verdict: `PAPER_SHADOW_FLICKERY_OPPORTUNITY`
> Scope: local research only; no N100/live config changed and no orders placed.

## Data Snapshot

- DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- Snapshot date folder: `2026-06-14`; files scanned `7`.
- Latest snapshot: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/orderbook_snapshots/2026-06-14/orderbook_snapshot_20260614_0300.jsonl.gz`; ts `2026-06-13T19:00:53Z`; current candidates `1`.
- CLOB fill coverage gate: `gate_pass=True`; fail_reasons `[]`.

### Mandatory 5-Line SQL Self-Check

```text
MAX(fact_built_at_utc) = 2026-06-13T19:19:44.825906+00:00
fact_trades by trade_class = [{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]
fact_trades by settlement_status = [{'settlement_status': None, 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]
fact_signal_candidates coverage = {'rows': 28492, 'eligible': 9697, 'paper_ordered': 3701, 'live_filled': 348}
CLOB orders with fills = [{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]
```

## Persistence Summary

- Snapshots with candidates: `3` / `7`.
- Total candidate observations: `4`.
- Unique candidate events: `3`.
- Max observations for one event: `2` snapshots.
- Latest scanner state: `CURRENT_EXECUTABLE_BASKET`.

| snapshot_ts_utc | rows | yes_events | candidates | candidate cities |
|---|---:|---:|---:|---|
| `2026-06-13T16:00:53Z` | 1174 | 77 | 0 | `` |
| `2026-06-13T16:30:53Z` | 1150 | 74 | 0 | `` |
| `2026-06-13T17:00:53Z` | 1124 | 72 | 0 | `` |
| `2026-06-13T17:30:53Z` | 1096 | 72 | 0 | `` |
| `2026-06-13T18:00:53Z` | 1096 | 72 | 1 | `Busan(+2.0%)` |
| `2026-06-13T18:30:53Z` | 1076 | 72 | 2 | `Busan(+3.0%), MexicoCity(+2.5%)` |
| `2026-06-13T19:00:53Z` | 1044 | 69 | 1 | `Denver(+2.8%)` |

## Candidate Sequences

| city | event_date | observations | first | last | max underround | min cost | max min-size |
|---|---|---:|---|---|---:|---:|---:|
| `Busan` | 2026-06-14 | 2 | `2026-06-13T18:00:53Z` | `2026-06-13T18:30:53Z` | +3.0% | 0.970 | 13.97 |
| `Denver` | 2026-06-14 | 1 | `2026-06-13T19:00:53Z` | `2026-06-13T19:00:53Z` | +2.8% | 0.972 | 7.33 |
| `MexicoCity` | 2026-06-14 | 1 | `2026-06-13T18:30:53Z` | `2026-06-13T18:30:53Z` | +2.5% | 0.975 | 10.00 |

## Verdict

- `all_yes_underround_basket_v0` remains the leading market-structure direction, but this scan still does not approve live trading.
- The latest snapshot has 1 guard-passing candidate(s), led by Denver +2.8%
- Candidate recurrence is real but sparse across the sampled snapshots, so future evidence must come from fresh live-equivalent paper capture rather than delayed local sync observations.
- Any future live implementation must be snapshot-driven and all-leg-or-none; stale baskets must not be carried forward into live orders.
- Continue low-latency paper monitoring until enough TTL-valid baskets settle.
