# Weather Dashboard — Quantitative Blood Lineage Design

**Date:** 2026-05-21  
**Author:** Claude (Anthropic)  
**Status:** Implemented

---

## Goal

A clean, extensible quantitative dashboard for the weather Polymarket strategy that makes the full **blood lineage** of every trade visible: from `Config → Signal → Plan → Order × N venues → Fill → Settlement → PnL`.

Designed to be extended to other strategies beyond weather without architectural changes.

---

## Architecture

```
n100 (192.168.0.200)              Local machine (WSL2)
├── pm_agent/runtime/             ← rsync → remote_pm_agent/
│   └── weather_edge_v1/
│       ├── live_cycle/*.json     (30-min loop JSONL)
│       ├── signals/*.jsonl
│       ├── plans/*.jsonl
│       ├── live/*.jsonl          (real CLOB orders)
│       └── paper/*.jsonl
│
└── weather-predict/              ← rsync → market_data/
    └── output/ + cache/          (research CSVs, obs cache)

              ↓ ingest_live_cycle.py
         runtime/weather.db       (canonical SQLite, append-only)
              ↓ clob_fill_sync.py
         Polymarket activity API  (fill price, shares from on-chain)
              ↓ metrics-refresh
         runs.metrics JSON blob   (cached per-run metrics)
              ↓ FastAPI (port 8000)
         Frontend (Vite, port 5173)
```

---

## Data Pipeline

### Two-source sync

**1. market_data** (weather-predict machine, `~/projects/weather-predict`):
- `output/paper_snapshots`, `output/research`, `cache/` → local `market_data/`
- SSH key: `~/.ssh/id_ed25519_weather_deploy`

**2. pm_agent runtime** (n100, `jiarui@192.168.0.200`):
- `runtime/weather_edge_v1/{live_cycle,signals,plans,live,paper}/` → local `remote_pm_agent/`
- Standard SSH (BatchMode=yes, StrictHostKeyChecking=no)
- Excludes `.pid`, `.out` files

### Ingest

`ingest_live_cycle.py` scans both `runtime/weather_edge_v1/` and `runtime/weather_edge_v1/remote_pm_agent/` for JSONL files, inserts into canonical schema via `INSERT OR IGNORE` (idempotent).

**Append-only design:** All write operations use triggers that prevent UPDATE/DELETE on core tables (`orders`, `fills`, `settlements`, `signals`, `plans`). This prevents accidental corruption and provides a clean audit trail.

### CLOB fill sync

`clob_fill_sync.py` polls `data-api.polymarket.com/activity?user={funder}` (public, no auth required) using the known weather funder address `0x76c7ad96e789e3995046af3bb13218f5f1a2860e`. Matches activity events to local orders by `order_id`. Inserts fills into `fills` table.

**Key invariant:** `fees_usd` must be a float (not NULL) — the column has a NOT NULL constraint.

### Refresh pipeline

`scripts/ops/weather_dashboard_refresh.sh` — idempotent, can be run anytime:
1. `sync_weather_remote.sh --live-only` — pull from n100
2. `make migrate-live-cycle` — ingest JSONL
3. `clob_fill_sync.py` — pull real fills
4. `make metrics-refresh` — recompute all run metrics

---

## Database Schema (canonical, weather.db)

```
runs          — one row per live cycle or paper/explore run
signals       — one signal per market/bracket/date combo
plans         — execution plan derived from signal
orders        — one per venue (paper + clob per signal)
fills         — one per filled order (real: from CLOB API, paper: simulated)
settlements   — final_price per condition_id/bracket/target_date
run_artifacts — CSV/JSON artifacts attached to runs
```

All tables have `created_at_utc` and append-only triggers. `runs.metrics` stores a JSON blob of precomputed aggregate metrics (updated by metrics-refresh or on-demand via GET /runs/{id}).

---

## API Design (FastAPI, port 8000)

### Routers

| Router     | Prefix          | Purpose                              |
|------------|-----------------|--------------------------------------|
| `runs`     | `/api/runs`     | Run list, detail, trades, equity     |
| `live`     | `/api/live`     | Live positions, execution gap        |
| `compare`  | `/api/compare`  | Side-by-side run comparison          |
| `configs`  | `/api/configs`  | Strategy configs and universe lists  |

### Live endpoints

**GET /api/live/summary** — Status card: last cycle time, capital deployed, realized PnL, pending orders, paper baseline metrics.

**GET /api/live/positions** — Panel A. Joined `fills ⋈ orders ⋈ signals ⋈ settlements` for all CLOB fills. Filterable by `status=open|settled|all`. Returns per-position PnL when settled.

**GET /api/live/execution-gap** — Panel B. Aggregates paper fills vs CLOB fills per signal. Computes `price_slippage` (CLOB - paper), `pnl_gap_usd` (real PnL - paper PnL), classifies each signal as `both | clob_only | paper_only_clob_rejected | paper_only`.

---

## Frontend Design

### Navigation: two-tab sidebar

- **Weather tab** (default): Runs · Live · Compare — new canonical system
- **Legacy tab**: 总览/策略/账户/Research/Backtests — deprecated, grayed out with warning banner

Route detection: `/weather/*` → Weather tab, others → Legacy tab.

### Pages

| Route                           | Component              | Purpose                         |
|---------------------------------|------------------------|---------------------------------|
| `/weather/runs`                 | WeatherRunsPage        | Run list with PnL/Win%/ROI cols |
| `/weather/history/:runId`       | WeatherHistoryPage     | Per-run trades + slice breakdown|
| `/weather/history/:id/trade/:s` | WeatherTradeDrilldown  | Full blood lineage for 1 signal |
| `/weather/live`                 | WeatherLivePage        | Panel A + Panel B               |
| `/weather/compare`              | WeatherComparePage     | Multi-run comparison            |

### WeatherLivePage layout

```
[Status bar: polling indicator · last update · ← All runs]
[Summary cards: last cycle · positions · open/settled · capital · PnL · paper baseline ROI]
[Panel tabs: Panel A — CLOB Positions (N) | Panel B — Execution Gap (N)]

Panel A:
  [Filter: all · open · settled] [Aggregate: N positions · $X PnL · K/N wins]
  [Table: Date · City · Bracket · Pool · Side · Fill Price · Shares · Cost · Edge · Settled? · PnL]

Panel B:
  [Stats: Paired N · CLOB Rejected N · Avg PnL Gap · Avg Slippage]
  [Filter: All · Paired only · CLOB Rejected]
  [Table: Target · City · Bracket · Side · Model P · Paper Fill · CLOB Fill · Slippage · Shares × 2 · PnL × 2 · Gap · Type]
```

---

## Extensibility

To add a new strategy (e.g. "crypto_mm"):
1. Add `ingest_crypto_mm.py` that writes to same `runs/signals/plans/orders/fills` schema
2. Create `crypto_mm/api/routers/` with strategy-specific endpoints
3. Add nav section to PageFrame (copy Weather nav pattern)
4. Frontend pages use same `weatherApi`-style HTTP client pattern

The canonical DB schema is strategy-agnostic. `config_id` and `universe_id` differentiate strategies within runs. `producer_system` field identifies which system generated a run.

---

## Known Limitations & TODOs

- **Settlements missing**: 27 CLOB fills from 2026-05-16 have no `final_price` yet. Need to ingest settlement data from Polymarket resolution API.
- **n100 data freshness**: DB last synced from n100 through 2026-05-17. Run `weather_dashboard_refresh.sh` to pull latest.
- **Live market prices**: Live page shows fill prices but not current mark prices (no market price feed integrated yet).
- **Fee accounting**: `fees_usd` stored as 0.0 (actual fees not yet fetched from activity API).

---

## Work Record

| Date       | Change                                                                 |
|------------|------------------------------------------------------------------------|
| 2026-05-21 | Fixed `sync_weather_remote.sh` — added n100 pm_agent runtime sync     |
| 2026-05-21 | Created `weather_dashboard_refresh.sh` — full pipeline script          |
| 2026-05-21 | Created `live.py` API router — `/live/summary`, `/positions`, `/gap`   |
| 2026-05-21 | Registered live router in `app.py`                                     |
| 2026-05-21 | Updated `weather-types.ts` — LivePosition, ExecutionGapRow, LiveSummary|
| 2026-05-21 | Updated `weather-http.ts` — getLivePositions, getExecutionGap          |
| 2026-05-21 | Rewrote `WeatherLivePage.tsx` — Panel A + Panel B design               |
| 2026-05-21 | Rewrote `PageFrame.tsx` — two-tab nav: Weather / Legacy                |
| Earlier    | Fixed CLOB fill sync (fees_usd NOT NULL, append-only trigger)          |
| Earlier    | Extended metrics (9 new fields: drawdown, expectancy, fees, etc.)      |
| Earlier    | Fixed CORS (allow_origin_regex for Vite port auto-increment)           |
| Earlier    | Added SliceBreakdown (7 dimensions) to WeatherHistoryPage              |
| Earlier    | Full blood lineage TradeDrilldown (Signal→Plan→Order→Fill→Settlement)  |
