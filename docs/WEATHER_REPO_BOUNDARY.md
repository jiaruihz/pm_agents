# Weather Repo Boundary

Last updated: 2026-06-01

This document defines the runtime boundary between the two active weather
repositories. It is meant to prevent agents from treating similar file names as
shared runtime code.

## Runtime Roles

| Repo / host path | Runtime role | Owns | Must not own |
|---|---|---|---|
| N100 `/home/jiarui/projects/weather-predict` | Production market data and paper research collector | market snapshots, orderbook snapshots, paper ledger, city pools, weather caches, settlement history | live CLOB execution, pm_agent dashboard DB |
| N100 `/home/jiarui/projects/pm_agent` | Production live execution | live signal files, trade plans, real CLOB order submissions, strategy instances, pause state, Telegram/live doctor | weather model cache generation, paper snapshot timer |
| Local `/home/rui/projects/pm_agent` | Analysis, dashboard, and deployment staging | dashboard DB, ingest/migration, fact tables, strategy research, local code staging for N100 `pm_agent` | direct production data collection |
| Local `/home/rui/projects/weather-predict` | Development copy for weather-predict | local edits/tests for N100 `weather-predict` scripts | production truth |

## Data Boundary

`weather-predict` is the source for market-data truth:

- `output/paper_snapshots/`
- `output/orderbook_snapshots/`
- `output/paper_trades/paper_orders.jsonl`
- `cache/pm_history/`
- `cache/wu_obs/`
- `cache/iem_v2_*.csv`

`pm_agent` is the source for live-execution lineage:

- `runtime/weather_edge_v1/signals/`
- `runtime/weather_edge_v1/plans/`
- `runtime/weather_edge_v1/live/`
- `runtime/weather_edge_v1/live_cycle/`

Local dashboard analysis consumes both sides through
`scripts/ops/sync_weather_remote.sh`:

```text
N100 weather-predict/*              -> runtime/weather_edge_v1/market_data/
N100 pm_agent/runtime/weather_edge_v1 -> runtime/weather_edge_v1/remote_pm_agent/
```

The local dashboard DB is derived from these mirrors. It is not a production
writer.

## Code Boundary

`pm_agent` may call weather-predict only through explicit bridge tooling:

- `scripts/ops/weather_predict_bridge.py`
- `src/strategies/weather_edge_v1/tools/weather_predict_bridge.py`

That bridge is for local research/data-cache operations. The normal N100 live
execution loop does not import weather-predict modules.

`weather-predict` should not import pm_agent code. It writes files that pm_agent
later consumes.

## Deployment Boundary

Use `weather-strategy-deploy` for any production behavior change.

- Changes under N100 `pm_agent` must be deployed git-first to
  `/home/jiarui/projects/pm_agent`.
- Changes under N100 `weather-predict` must be committed locally first. If the
  remote repo is still not a git worktree, backup + rsync is only a temporary
  fallback and must be reported as such.
- Do not deploy a weather-predict code change by editing only pm_agent docs, and
  do not deploy a pm_agent live-execution change by copying files into
  weather-predict.

## Known Duplication To Treat Carefully

`weather-predict` currently has a production paper runner plus a compatibility
wrapper:

```text
paper_policy.py                         # wrapper for historical top-level imports
scripts/analysis/paper_policy.py        # active policy implementation
scripts/analysis/paper_trigger_runner.py # active runner
```

The systemd paper snapshot service runs:

```text
scripts/ops/run_paper_snapshot.sh
  -> paper_snapshot.py
  -> scripts/analysis/paper_trigger_runner.py
```

Because `scripts/analysis/paper_trigger_runner.py` imports `paper_policy` from
its own script directory first, the active production paper policy is
`scripts/analysis/paper_policy.py`.

The root `paper_policy.py` is intentionally only a wrapper around the active
implementation. Do not add strategy logic there.

## Current Strategy Ownership

Retired:

- `maker_queue_v1` is historical only. New production paper/live runners should
  not generate or accept it.

Active weather-predict paper policy:

- `mid_price_core_v1`

Active pm_agent live strategy instances:

- `mid_price_core_v1_25_75`
- `mid_price_core_v1_side_band`
- `mid_price_core_v2_25_75`

Historical dashboard migrations may still recognize `maker_queue_v1` so old
rows remain readable. That is not permission to generate new rows.
