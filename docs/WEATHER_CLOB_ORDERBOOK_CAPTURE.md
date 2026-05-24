# Weather CLOB Orderbook Capture

Purpose: future weather snapshots should record executable CLOB market context so gamma / lottery research can use ask-entry and bid-exit assumptions instead of UI price or midpoint proxies.

## Current Design

`weather-predict/paper_snapshot.py` enriches each paper snapshot market with CLOB top-of-book and shallow depth fields.

Main snapshot record fields:

```text
yes_token_id
yes_best_bid
yes_best_ask
yes_spread
yes_bid_size
yes_ask_size
yes_depth_bid_5c
yes_depth_ask_5c
yes_depth_bid_10c
yes_depth_ask_10c
yes_book_status
yes_book_fetched_at_utc

no_token_id
no_best_bid
no_best_ask
no_spread
no_bid_size
no_ask_size
no_depth_bid_5c
no_depth_ask_5c
no_depth_bid_10c
no_depth_ask_10c
no_book_status
no_book_fetched_at_utc

clob_price_source
```

Full top-N orderbook rows are stored separately as compressed JSONL:

```text
weather-predict/output/orderbook_snapshots/YYYY-MM-DD/orderbook_snapshot_YYYYMMDD_HHMM.jsonl.gz
```

The local mirror syncs them to:

```text
pm_agent/runtime/weather_edge_v1/market_data/orderbook_snapshots/
```

## Research Usage

For future executable-style gamma research:

```text
entry = yes_best_ask
exit = later yes_best_bid
```

For NO-side research:

```text
entry = no_best_ask
exit = later no_best_bid
```

Depth fields are for liquidity filters and slippage checks, not price replacement.

## Operational Notes

- Do not try to backfill missing historical CLOB data when Polymarket archived weather markets make it unavailable.
- Let future scheduled snapshots accumulate these fields naturally.
- Keep full raw orderbook data outside the main snapshot record to avoid bloating the primary research surface.
- `scripts/ops/sync_weather_remote.sh` includes `output/orderbook_snapshots`.
- N100 `weather-predict` backup includes `output/orderbook_snapshots`.

## TODO

- When Polymarket weather markets return to normal availability, verify the next non-empty snapshot has non-null `yes_best_bid` / `yes_best_ask` for active markets.
- Confirm `orderbook_snapshots/YYYY-MM-DD/*.jsonl.gz` is created on N100 after a non-empty snapshot run.
- Run `scripts/ops/sync_weather_remote.sh` and confirm local mirror receives `runtime/weather_edge_v1/market_data/orderbook_snapshots/`.
- After several days of data, update low-price gamma research to use `best_ask` entry and later `best_bid` exit instead of `market_yes_price` proxy.

## Operational Check - 2026-05-19

N100 status at 2026-05-19 15:51 UTC:

- `weather-predict-snapshot.timer` is active.
- Latest snapshot service run completed successfully.
- Latest snapshot: `output/paper_snapshots/snapshot_20260519_2330.json`.
- Snapshot freshness is OK under the doctor threshold.
- The latest snapshot contains 437 CLOB summary field sets such as `yes_best_ask`, `yes_book_status`, and `clob_price_source`.
- Latest sidecar: `output/orderbook_snapshots/2026-05-19/orderbook_snapshot_20260519_2330.jsonl.gz`.
- Latest sidecar line count: 874 rows, matching YES/NO orderbook attempts.

Observed issue:

- Latest sidecar samples show `status=not_found` for weather token orderbooks.
- This matches the current Polymarket weather market archive/missing-market issue.
- Conclusion: scheduled capture is running and writing the new fields; executable CLOB values may remain empty until Polymarket weather markets recover.

## Operational Check - 2026-05-21

N100 status at 2026-05-20 16:46 UTC / 2026-05-21 00:46 Asia/Shanghai:

- `weather-predict-snapshot.timer` is active.
- Latest snapshot service run completed successfully.
- Latest snapshot: `output/paper_snapshots/snapshot_20260521_0030.json`.
- Snapshot freshness is OK under the doctor threshold.
- `paper_snapshot.err.log` and `daily_pipeline.err.log` are empty.
- Latest snapshot contains 611 CLOB-enriched market records.
- `yes_book_status`: 611 `ok`.
- `no_book_status`: 611 `ok`.
- Non-null top-of-book counts:
  - `yes_best_ask`: 609
  - `yes_best_bid`: 571
  - `no_best_ask`: 571
  - `no_best_bid`: 609
- Latest sidecar: `output/orderbook_snapshots/2026-05-21/orderbook_snapshot_20260521_0030.jsonl.gz`.
- Latest sidecar line count: 1222 rows.
- Latest sidecar status: 1222 `ok`.

Sample active top-of-book rows:

```text
Amsterdam 18 YES: bid=0.985 ask=0.991 spread=0.006 depth_bid_5c=94.74 depth_ask_5c=428.2
Amsterdam 19 YES: bid=0.010 ask=0.016 spread=0.006 depth_bid_5c=862.72 depth_ask_5c=345.14
Amsterdam 20 YES: bid=0.003 ask=0.005 spread=0.002 depth_bid_5c=328.11 depth_ask_5c=852.3
Atlanta 84-85 YES: bid=0.010 ask=0.054 spread=0.044 depth_bid_5c=136.85 depth_ask_5c=54.99
```

Local mirror check:

- Full `scripts/ops/sync_weather_remote.sh` hit an SSH broken pipe during large rsync transfer.
- Direct single-file `ssh cat` copy succeeded for the latest snapshot and latest sidecar.
- Local sidecar gzip validation passed.
- Local sidecar line count: 1222.
- Local snapshot JSON validation passed.

Conclusion:

- Polymarket weather CLOB availability has recovered for the checked snapshot.
- The CLOB capture fields and full sidecar orderbook archival are working on production.
- The remaining issue is sync robustness for larger transfers, not data capture.
