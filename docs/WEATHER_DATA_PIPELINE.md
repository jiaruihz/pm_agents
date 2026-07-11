# Weather Data Pipeline

Status: current-source
Updated: 2026-07-04 Mac temporary production handoff + forecast curve lineage
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed

Last updated: 2026-06-06

> 2026-06-05 更新: 同步覆盖扩展（7 个 weather model cache 家族 + output/logs + pm_agent runtime/logs + N100 tar backups），删除两个 legacy DB（weather_v2.db / weather_edge_v1_weather.db），新增 `scripts/ops/sync_n100_backups.sh`。详见 §2.3、§7。
>
> 2026-06-06 更新: 修正 settlement near-binary 口径。`pm_history` raw `0.9995 / 0.0005` 现在归一化为已结算 `1 / 0`；旧口径导致 `missing_bracket` 大量误标，相关历史报告需重算。
>
> 2026-06-07 更新: 修正 CLOB fill recovery 口径。public activity 不是逐 order 权威来源；旧 fallback 在 split child order / partial fill 场景会少算或多算。新增 `weather_clob_fill_coverage_gate.py`，refresh/rebuild 后必须 fail-closed 校验 order_id、order cap、DB/cache/fact 成本一致性。
>
> 2026-07-04 更新: N100 7/1 发生 ext4 emergency read-only / IO error 事故后，Mac 临时接管生产。当前 market snapshot/orderbook 源为 `/Users/deepsleep/projects/weather_data_feed_service_runtime/targeted_output/`，live order 源为 `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/live/` 和 active strategy runtime dirs。同步当前生产 market data 用 `scripts/ops/sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only`。
>
> 2026-07-06 更新: Mac data-feed runtime 和本地 `runtime/weather_edge_v1/market_data` mirror 已迁到 APFS 外置盘 `/Volumes/jrs`。旧路径 `/Users/deepsleep/projects/weather_data_feed_service_runtime` 和 `runtime/weather_edge_v1/market_data` 保留为 symlink。macOS LaunchAgent 对外置卷写入会触发 `Operation not permitted`，所以 data-feed 当前由 `tmux -L weather-data-feed-jrs` session `weather_data_feed_jrs` 常驻；重启/拔插盘后用 `scripts/ops/start_mac_weather_data_feed_jrs_tmux.sh` 恢复。
>
> 2026-07-11 更新: full snapshot producer now persists immutable decision-time hourly forecast curves as `targeted_output/forecast_hourly_curves/YYYY-MM-DD/forecast_hourly_curves_*.jsonl`, one row per city/target_date/snapshot. Corrected `forecast_hourly_curve_v3` rows separate source-response `forecast_detected_at_utc` from capture publication-boundary `available_at_utc`; a new exact hash first appears at detected time, or at available time only when no reliable detected time exists, and later captures preserve the earliest reliable first-seen. `available_at_utc` is sampled immediately before final serialization/fsync and atomic link, while file mtime is the external completion evidence. Rows also record explicit model fallback and an honest `forecast_run_lineage_status` when the upstream live API does not expose a run timestamp. `weather_data_feed_prod_health_check.py` fails when the latest curve capture is stale, misaligned with the latest snapshot, incomplete, missing lineage, or has impossible detected/first-seen/available ordering. `build_weather_signal_candidates.py` mirrors them into `runtime/weather.db.fact_forecast_hourly_curves`; `fact_signal_candidates.forecast_values_hash` is the join key.

Single source of truth for **where weather strategy data lives, who produces
it, who consumes it, and how PnL is computed**. Read this before touching
ingest scripts, the dashboard DB, or any "why doesn't the curve update"
question.

For live-rollout operations, read [`WEATHER_STRATEGY_ENTRYPOINT.md`](WEATHER_STRATEGY_ENTRYPOINT.md).
For field-name contracts, [`WEATHER_SYSTEM_CONTRACT.md`](WEATHER_SYSTEM_CONTRACT.md).

---

## 1. TL;DR

There are **two active roles** during the 2026-07-04 incident handoff:

```
                              Mac (temporary production)
                              ├── weather_data_feed_service_runtime
                              │   ├── targeted_output/paper_snapshots
                              │   ├── targeted_output/orderbook_snapshots
                              │   └── targeted_output/forecast_hourly_curves
                              └── pm_agents runtime
                                  ├── live order logs
                                  └── dashboard / fact rebuild
                                       │
                     sync_weather_remote.sh --market-source=mac-weather-data-feed
                                       ▼
                              pm_agents canonical mirror
                              ├── runtime/weather_edge_v1/market_data
                              └── runtime/weather.db (dashboard DB)
```

N100 remains the historical source and recovery target, but after the 2026-07-01
disk incident it is not the current production truth until disk health and
backup integrity are verified.

The unified PnL caliber is:

> **one strategy identity → all its orders → all matching fills → settle
> each fill by token via the settlements table → sum**.

Every agent and every endpoint must use this caliber. It is implemented in
`weather_dashboard.api.routers.configs._PNL_CASE` + `_base_joins()` and
must not be reimplemented elsewhere.

For live CLOB performance, the dashboard DB already has real fill-level data:

- Source: `runtime/weather.db` → `fills` joined to `orders` where
  `orders.venue = 'polymarket_clob'` and `fills.status = 'filled'`.
- API: `weather_dashboard/api/routers/live.py`
  (`/api/live/summary`, `/api/live/positions`, `/api/live/execution-gap`).
- Sync: `weather_dashboard.ingest.clob_fill_sync` writes matched real CLOB
  fills into `fills`.

Do **not** infer live realized PnL from
`runtime/weather_edge_v1/remote_pm_agent/live/live_*_orders.jsonl` alone.
Those files are order submission/error records. A `status='submitted'` row is
not proof of fill, and its `posted_price`/`size` is only an order-level proxy
unless matched by a real row in `fills`.

---

## 2. Data sources

### 2.1 Mac temporary production (`/Users/deepsleep/projects`)

| Path | Producer | Refresh | What it is |
|---|---|---|---|
| `/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/paper_snapshots/snapshot_*.json` | Mac tmux `weather_data_feed_jrs` | full snapshot cadence | current production market snapshots |
| `/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/orderbook_snapshots/YYYY-MM-DD/orderbook_snapshot_*.jsonl.gz` | Mac tmux `weather_data_feed_jrs` | full snapshot cadence | current production orderbook history; not backfillable if missed |
| `/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/forecast_hourly_curves/YYYY-MM-DD/forecast_hourly_curves_*.jsonl` | Mac tmux `weather_data_feed_jrs` | full snapshot cadence | point-in-time hourly forecast curve, one row per city/target_date/snapshot |
| `pm_agents/runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl` | Mac LaunchAgent `com.pm-agents.low-price-yes-lottery-live` | live strategy cadence | current BUY_YES lottery CLOB order submissions |
| `pm_agents/runtime/weather_edge_v1/live/low_price_yes_take_profit_exit_v1_orders.jsonl` | Mac LaunchAgent `com.pm-agents.low-price-yes-take-profit-exit` | live strategy cadence | current SELL_YES TP exit CLOB order submissions |
| `pm_agents/runtime/weather_edge_v1/regime_routed_no_tiny_live_v1/live_orders.jsonl` | Mac LaunchAgent `com.pm-agents.regime-routed-no-live` | live strategy cadence | current regime-routed NO live order submissions |
| `pm_agents/runtime/weather_edge_v1/tmax_distribution_edge_live_candidate_v1/live_orders.jsonl` | Mac strategy runtime | live/shadow strategy cadence | current tmax distribution candidate live order submissions |

These live JSONL sources are materialized into `runtime/weather.db.orders`.
Use `orders` for live order-event questions such as city/date/bracket, YES/NO,
sizing policy, score tier, maker/taker/lifecycle action, blocked/error state,
price, shares, notional and execution policy. Strategy-specific fields are kept
as original JSON in `orders.order_payload`, so active heads such as forecast
tail, regime-routed NO, tmax distribution and exit overlays can share one order
lineage table without per-strategy event tables. It remains submitted-order
lineage; realized cash/PnL still comes from `fills` and `fact_trades`.
`weather_dashboard_refresh.sh` and `run_stack.sh` explicitly ingest the active
Mac live order files and then run
`weather_dashboard.cli.check_strategy_runtime_order_coverage`; if a raw
strategy-runtime order `execution_id` is missing from canonical `orders`, the
refresh fails closed instead of producing a DB where submitted orders disappear
from fill sync.

Runtime boundary: strategy runners own candidate selection, risk decision and
sizing; shared execution glue lives in
`src/strategies/weather_edge_v1/runtime/order_runtime.py`. Active live runners
should use that helper for JSON/JSONL serialization and
`weather_order_executor.py` invocation so order-field and persistence contract
changes do not need to be duplicated per strategy. `weather_order_executor.py`
still owns the actual paper/live CLOB order submission, while
`weather_dashboard.cli.ingest_strategy_runtime_orders` owns canonical DB ingest.

Health gate:

```bash
.venv/bin/python scripts/ops/weather_data_feed_prod_health_check.py
```

Sync current market data before rebuilding facts:

```bash
scripts/ops/sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only
```

`runtime/weather_edge_v1/market_data` is a symlink to `/Volumes/jrs/pm_agents/runtime/weather_edge_v1/market_data`, so the sync writes the canonical local mirror to the external disk as well.

### 2.2 N100 historical production / recovery (`192.168.0.200`, user `jiarui`)

As of 2026-07-04 this section is historical/recovery context, not current
production. N100 had an ext4 emergency read-only / IO error incident on
2026-07-01; do not restore it to production by just restarting services.

| Path | Producer | Refresh | What it is |
|---|---|---|---|
| `weather-predict/output/paper_snapshots/snapshot_*.json` | live cycle | every 30 min | full market snapshot per cycle (561 records) |
| `weather-predict/output/paper_trades/paper_orders.jsonl` | live cycle | every 30 min | append-only paper ledger of all T1+T2 paper orders |
| `weather-predict/cache/pm_history/{City}_{date}.json` | daily_pipeline | daily | per-bracket final_price + token_id at resolution **← settlement truth** |
| `weather-predict/cache/wu_obs/wu_obs_{ICAO}.csv` | t2 fill timer | daily | Wunderground observed temps |
| `weather-predict/cache/iem_v2_*.csv` | t2 fill timer | daily | IEM ASOS observed temps |
| `weather-predict/output/research/t24_paper_ledger_trades.csv` | `settle_t24_paper.py --source ledger` | **manual** ⚠ | derived: paper ledger settled into per-trade rows |
| `weather-predict/output/research/t24_paper_snapshot_replay_trades.csv` | `settle_t24_paper.py --source snapshots` | **manual** ⚠ | derived: snapshot-replay backtest trades |
| `pm_agent/runtime/weather_edge_v1/live/live_*_orders.jsonl` | weather_live_cycle | every 30 min | real CLOB order submissions/errors; not fill/PnL truth by itself |
| `pm_agent/runtime/weather_edge_v1/plans/live_*_trade_plans.jsonl` | weather_trade_planner | every 30 min | plans the live executor considered |
| `pm_agent/runtime/weather_edge_v1/signals/live_*_signals.jsonl` | weather_snapshot_signal_builder | every 30 min | signals fed into the planner |
| `pm_agent/runtime/weather_edge_v1/live_cycle/{cycle_id}.json` | weather_live_cycle | every 30 min | cycle summary (config, alerts, executor result) |
| `weather_data_feed_service_runtime/output/source_events/latest.json` | weather-data-feed-source-events.timer | every completed run + 2 min | latest city/source observation events for latency and crossing signals |
| `weather_data_feed_service_runtime/output/source_events/sources.jsonl` | weather-data-feed-source-events.timer | append-only | source-event history with report_ts, detect_ts, payload hashes, raw METAR |
| `weather_data_feed_service_runtime/output/observations/latest.json` | weather-data-feed-observations.timer | ~5 min | shared fast observation cache for strategy feature/state inputs |

The two CSVs marked ⚠ are the only N100-side artifacts without automation —
they go stale unless someone reruns `settle_t24_paper.py`. See
[§7 N100 automation gap](#7-n100-automation-gap).

### 2.3 Local pm_agent mirror

`scripts/ops/sync_weather_remote.sh` pulls:

| Remote | Local | Notes |
|---|---|---|
| `weather_data_feed_service_runtime/targeted_output/paper_snapshots/` | `runtime/weather_edge_v1/market_data/paper_snapshots/` | current Mac production snapshots |
| `weather_data_feed_service_runtime/targeted_output/orderbook_snapshots/` | `runtime/weather_edge_v1/market_data/orderbook_snapshots/` | current Mac production orderbook history |
| `weather_data_feed_service_runtime/targeted_output/forecast_hourly_curves/` | `runtime/weather_edge_v1/market_data/forecast_hourly_curves/` | current Mac production hourly forecast curves |
| `weather-predict/output/paper_snapshots/` | `runtime/weather_edge_v1/market_data/paper_snapshots/` | 30-min snapshots |
| `weather-predict/output/paper_trades/` | `runtime/weather_edge_v1/market_data/paper_trades/` | paper ledger |
| `weather-predict/output/research/` | `runtime/weather_edge_v1/market_data/research/` | derived CSVs + analysis reports |
| `weather-predict/cache/pm_history/` | `runtime/weather_edge_v1/market_data/cache/pm_history/` | **settlement truth** |
| `weather-predict/cache/wu_obs/` | `runtime/weather_edge_v1/market_data/cache/wu_obs/` | observed temps |
| `weather-predict/cache/iem_v2_*.csv` | `runtime/weather_edge_v1/market_data/cache/iem/` | IEM data |
| `weather-predict/output/logs/` | `runtime/weather_edge_v1/market_data/logs/` | live cycle process logs（2026-06-05 起加入 sync） |
| `weather-predict/cache/gfs_v4_*.json` | `runtime/weather_edge_v1/market_data/cache/gfs_v4/` | GFS hourly forecast cache（2026-06-05 起加入 sync）|
| `weather-predict/cache/gfs_daily_*.json` | `runtime/weather_edge_v1/market_data/cache/gfs_daily/` | GFS daily aggregate cache（2026-06-05 起）|
| `weather-predict/cache/ecmwf_v4_*.json` | `runtime/weather_edge_v1/market_data/cache/ecmwf_v4/` | ECMWF hourly forecast cache（2026-06-05 起）|
| `weather-predict/cache/jma_v5_*.json` | `runtime/weather_edge_v1/market_data/cache/jma_v5/` | JMA forecast cache（2026-06-05 起）|
| `weather-predict/cache/hrrr_v5_*.json` | `runtime/weather_edge_v1/market_data/cache/hrrr_v5/` | HRRR forecast cache（2026-06-05 起）|
| `weather-predict/cache/icon_eu_v5_*.json` | `runtime/weather_edge_v1/market_data/cache/icon_eu_v5/` | ICON-EU forecast cache（2026-06-05 起）|
| `weather-predict/cache/arome_v5_*.json` | `runtime/weather_edge_v1/market_data/cache/arome_v5/` | AROME forecast cache（2026-06-05 起）|
| `pm_agent/runtime/weather_edge_v1/{live,plans,signals,live_cycle,paper}/` | `runtime/weather_edge_v1/remote_pm_agent/...` | N100 live lineage |
| `pm_agent/runtime/logs/` | `runtime/weather_edge_v1/remote_pm_agent/logs/` | pm_agent live cycle process logs（2026-06-05 起）|

Run with `--dry-run` to preview, `--market-only` / `--live-only` to skip a half.

**Disaster-recovery mirror** — `scripts/ops/sync_n100_backups.sh` pulls
N100 `/home/jiarui/weather-predict-backups/*.tar.zst` to local
`runtime/_backups_n100/` and verifies sha256. Safe to run weekly; ~14 MB today
growing ~4 MB/day. Independent of the main sync above.

**Removed (legacy)** — `runtime/_legacy/weather_v2.db` and
`runtime/_legacy/weather_edge_v1_weather.db` were deleted 2026-06-05. Only
`runtime/_legacy/strategy_runtime.db` remains as historical artifact. The
canonical analysis DB is **only** `runtime/weather.db`. N100 has **no active
SQLite DB** — production data lives in files on N100, never in a DB.

### 2.4 Dashboard DB (`runtime/weather.db`)

Canonical schema in [`weather_dashboard/db/schema.sql`](../weather_dashboard/db/schema.sql).
All lineage tables (`signals`, `plans`, `orders`, `fills`, `strategy_config`,
`runs`, `settlements`) have `BEFORE UPDATE` and `BEFORE DELETE` triggers —
the DB is **append-only by design**. Reconciliations happen by adding new
rows, never by editing old ones.

This is the local analysis/dashboard source for live fill-level realized PnL.
The live CLOB path is:

```text
remote_pm_agent/live/*.jsonl
  -> legacy_migration/live_cycle.py imports submitted/error orders
  -> ingest/clob_fill_sync.py matches CLOB/Data API trade activity
  -> fills(status='filled') linked by execution_id/order_id
  -> settlements from pm_history
  -> /api/live/* and strategy detail endpoints compute realized PnL
```

When comparing paper vs live:

- Use `orders.venue='paper'` + `fills.status IN ('filled','simulated')` for
  the parallel paper shadow.
- Use `orders.venue='polymarket_clob'` + `fills.status='filled'` for real
  live CLOB fills.
- Use `orders.status='submitted'` only for pending/execution funnel analysis,
  not as a substitute for actual fills.

Side tables (mutable):

| Table | Purpose |
|---|---|
| `config_aliases(alias_config_id, canonical_config_id)` | maps fragmented config_ids back to one canonical row per strategy identity |
| `ingestion_log` | content-addressable idempotency for every ingest |

---

## 3. Pipeline flow (end-to-end)

```
┌─────────────────────────────────────────────────────────────────────┐
│ N100 weather_data_feed_service                                      │
│   source-events timer ─────▶ output/source_events/latest.json       │
│                         └──▶ output/source_events/sources.jsonl     │
│   observations timer ──────▶ output/observations/latest.json        │
│   full snapshot timer ─────▶ standard market/orderbook snapshots    │
│                                                                     │
│ N100 weather-predict (legacy/dormant-parallel during migration)     │
│   live cycle every 30 min ──▶ paper_snapshots, paper_orders.jsonl   │
│   daily_pipeline       ──────▶ pm_history/                          │
│   settle_t24_paper.py  ──MANUAL──▶ t24_paper_*.csv (derived)        │
│                                                                     │
│ N100 pm_agent                                                       │
│   weather_live_cycle.py every 30 min ──▶ signals/, plans/, live/    │
│                                       ──▶ live_cycle/{id}.json      │
│   latency/crossing runners consume weather_data_feed_service        │
│   source_events; they do not own default weather polling            │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                       sync_weather_remote.sh
                                 │
┌────────────────────────────────▼────────────────────────────────────┐
│ local pm_agent                                                      │
│   runtime/weather_edge_v1/market_data/  (read-only mirror)          │
│   runtime/weather_edge_v1/remote_pm_agent/  (read-only N100 mirror) │
│                                                                     │
│   scripts/weather_dashboard/run_stack.sh                            │
│     ├─ make db-canonical-rebuild        (clean schema)              │
│     ├─ make migrate-legacy-research     (legacy_migration/research_csv) │
│     │     reads:  t24_paper_*.csv                                   │
│     │     writes: signals, plans, orders, fills, settlements        │
│     │             tagged config_id='legacy_research_weather_edge'   │
│     │             state='explore' or 'paper'                        │
│     │                                                               │
│     ├─ make migrate-live-cycle          (legacy_migration/live_cycle) │
│     │     reads:  live_cycle/{id}.json + sibling signals/plans/live/ │
│     │     writes: signals, plans, orders, fills (simulated_open),   │
│     │             strategy_config (hashed identity)                 │
│     │             state='live'                                      │
│     │                                                               │
│     ├─ python -m weather_dashboard.ingest.pm_history_settlements    │
│     │     reads:  cache/pm_history/{City}_{date}.json               │
│     │     writes: settlements (condition_id join),                  │
│     │             settlement_outcomes (city/date/bracket source)    │
│     │                                                               │
│     ├─ python -m weather_dashboard.ingest.clob_fill_sync            │
│     │     reads:  exchange_response matched fills, authenticated    │
│     │             CLOB order/trade data, public activity fallback   │
│     │     writes: fills(status='filled') for matched CLOB trades    │
│     │                                                               │
│     ├─ python -m weather_dashboard.db.consolidate_configs           │
│     │     writes: config_aliases (fragments → canonical)            │
│     │                                                               │
│     ├─ scripts/etl/build_weather_fact_trades.py   (DERIVED)    │
│     │     reads:  orders/fills/plans/signals/settlements (canonical)│
│     │     writes: fact_trades (每 fill 一行宽表, 已成交 PnL 唯一源) │
│     │             + fact_trades.parquet                             │
│     │                                                               │
│     ├─ scripts/etl/build_weather_signal_candidates.py (DERIVED)│
│     │     reads:  paper_snapshots/*.json, paper_orders.jsonl,       │
│     │             fact_trades(live_real), settlements,              │
│     │             forecast_hourly_curves/*.jsonl                    │
│     │     writes: fact_signal_candidates (每机会一行, 全机会宇宙→   │
│     │             paper intended→live actual 对齐, 机会 alpha 唯一源)│
│     │             fact_forecast_hourly_curves (每 city/date/snapshot │
│     │             一行，用 forecast_values_hash 关联候选行)          │
│     │             + fact_signal_candidates.parquet                  │
│     │             window: decision hts_min/max 默认 [22,24]         │
│     │                                                               │
│     ├─ scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py          │
│     │     checks: order_id match, order cap, DB/cache/fact cost     │
│     │             consistency; fail closed before metrics/reports   │
│     │                                                               │
│     └─ make metrics-refresh             (per-run metrics cache)     │
│                                                                     │
│   FastAPI + React serve from runtime/weather.db                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. The unified PnL caliber

Implemented in `weather_dashboard.api.routers.configs`:

```python
_PNL_CASE = """
    CASE
        WHEN s.final_price IS NULL THEN 0
        WHEN o.order_side = 'BUY_YES'
            THEN CAST(f.filled_shares AS REAL)
               * (CAST(s.final_price AS REAL) - CAST(f.filled_price AS REAL))
        WHEN o.order_side = 'BUY_NO'
            THEN CAST(f.filled_shares AS REAL)
               * ((1 - CAST(s.final_price AS REAL)) - CAST(f.filled_price AS REAL))
        ELSE 0
    END
"""

# Standard join, alias-aware. State filter: all / live / paper / explore.
def _base_joins(state: str = "all") -> str:
    state_filter = "" if state == "all" else f" AND r.state = '{state}'"
    return f"""
        FROM config_aliases ca
        JOIN runs r ON r.config_id = ca.alias_config_id{state_filter}
        LEFT JOIN orders o ON o.run_id = r.run_id
        LEFT JOIN fills f  ON f.execution_id = o.execution_id AND f.status = 'filled'
        LEFT JOIN plans p  ON p.plan_id = o.plan_id
        LEFT JOIN signals sig ON sig.signal_id = p.signal_id
        LEFT JOIN settlements s ON sig.target_date = s.target_date
                               AND sig.condition_id = s.condition_id
                               AND sig.bracket = s.bracket
        WHERE ca.canonical_config_id = ?
    """
```

**Rules every consumer must follow:**

1. Use the canonical `config_id` (or any alias — resolved via `config_aliases`).
2. Join through `config_aliases`, never `WHERE r.config_id = ?` directly.
3. Use `status='filled'` for fills (excludes `simulated`, the parallel paper
   shadow that runs alongside live).
4. Settlement is per-token: BUY_YES profit = `shares × (final_price - fill_price)`;
   BUY_NO profit = `shares × ((1 - final_price) - fill_price)`.
5. **Always specify a `state` filter.** A curve that silently blends live +
   paper is the original bug this caliber was built to prevent. Equity
   endpoints accept `?state=live` / `?state=paper` / `?state=all`.
6. For live realized PnL, query the DB/API fill path first. If an analysis only
   reads `live_*_orders.jsonl`, label it explicitly as submitted-order or
   assumed-filled analysis, not real live PnL.

### 4.1 Live CLOB fill recovery caliber

N100 / 本机 `live/*.jsonl` 记录的是 CLOB 订单提交凭证，不是成交回报。`trade_class='live_real'` 只在真实成交进入 `fills(status='filled')` 后才成立。

真实 CLOB fill 的权威顺序：

1. `orders.exchange_response.place.status='matched'` 的即时成交回报，使用 `makingAmount` / `takingAmount` 算 fill cost / shares。
2. authenticated CLOB order / trade 数据。
3. Polymarket public activity / public trades API 只能作 fallback。

2026-06-07 事故复盘：旧 fallback 把 public activity 当成逐 order 权威 fill 来源，这是错的。public activity 是账户级成交活动，不可靠携带本地 CLOB `order_id` 粒度；在 split child order、同 token 多笔订单、partial fill 场景下会造成：

- 少算：只记录第一段 partial fill，漏掉同一 order 的后续成交。
- 多算：把账户级 public activity 分配到错误 child order，出现 `order_id` mismatch 或 fill cost/shares 超过订单 cap。

修正后，public fallback 必须同时满足 exact condition/token、side、limit price、placed time，并按 partial fill 逐条记录；任何一条 fill 都不能让同一 `(execution_id, order_id)` 超过订单 shares/cost cap。

refresh/rebuild 后必须跑：

```bash
python3 scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py
```

`gate_pass=false` 时禁止发布 live_real PnL、ROI、city/side rank、近 7/15 天曲线。当前 gate 会检查：

- DB `fills` 是否存在 mismatched/missing `order_id`；
- 任一 order 的 fill shares/cost 是否超过 order cap；
- `runtime/weather_edge_v1/clob_fills.jsonl` 与 DB fill_id 是否一致；
- `fact_trades live_real` cost 是否等于 DB `fills` cost。

2026-06-07 修正后验证状态（示例；行数会随新增真实成交变化，最终以 gate 为准）：

```text
gate_pass=true
missing_order_rows=0
over_order_keys=0
db_not_in_cache=0
cache_not_in_db=0
db_fill_cost_minus_fact_cost=0
```

如果看到旧报告或旧 DB 中 `live_real=1316` / raw CLOB cost 约 `$3499`，那是错误 fill recovery 口径，不得用于策略结论。后续新增成交会改变行数和成本，但不能绕过 gate。

### Strategy identity

`live_weather_edge_v1_<hash>`. The hash is over an **identity-only**
projection of params:

- Included: `strategy_family, algorithm_version, execution_policy,
  signal_builder_version, trade_planner_version, city_pool, universe_scope,
  sizing_mode, max_order_notional, fixed_order_shares, min_edge,
  min_entry_price, max_entry_price, entry_price_window` (+ active policy-specific
  params when applicable).
- **Excluded** (would cause fragmentation): `paper_enabled, live_enabled,
  source` (runtime toggles); `max_order_shares` (per-order safety cap, not
  strategy intent).
- Defaults normalized: absent `execution_policy` ≡ `"mid_price_core_v1"`,
  same for `sizing_mode`, `universe_scope`, etc.

See `weather_dashboard/legacy_migration/live_cycle.py::_strategy_identity`.

### Strategy parameter layers

Weather `signal_id` is the market/model opportunity and can be shared across
strategies. A strategy branch starts when a `strategy_config`/`run` consumes the
same signal and writes its own `plans`:

```text
same signal_id
  -> config/run A -> plan(execution_policy=mid_price_core_v1) -> order/fill
  -> config/run B -> plan(execution_policy=mid_price_core_v2) -> order/fill
```

Parameter ownership:

- Signal/model facts: `target_date, city, bracket, condition_id,
  model_version, forecast_source, model_p_yes, market_price, edge`.
  These define the opportunity and should be common in A/B execution tests.
- Filter/sizing strategy params: `entry_price_window, min_edge,
  sizing_mode, max_order_notional, fixed_order_shares`.
- Execution-policy params: `execution_policy` plus maker-queue controls such
  as `min_quote_edge, max_quote_spread, max_mid_drift,
  adverse_selection_spread_fraction`.

`execution_policy` is therefore a strategy parameter, but it does not create a
new signal. It creates a separate plan/order/fill branch from the same signal.

The legacy DB had 6 config_id rows for one strategy; running
`python -m weather_dashboard.db.consolidate_configs` builds `config_aliases`
so all 6 roll up to one canonical row, and new ingest produces the canonical
id directly.

---

## 5. Scripts inventory

### 5.1 N100 production scripts (in `~/projects/weather-predict` and `~/projects/pm_agent`)

| Script | Where | When |
|---|---|---|
| `scripts/ops/weather_live_cycle.py` | pm_agent | live timer, every 30 min |
| `scripts/ops/weather_snapshot_signal_builder.py` | pm_agent | inside the cycle |
| `scripts/ops/weather_trade_planner.py` | pm_agent | inside the cycle |
| `scripts/ops/weather_order_executor.py` | pm_agent | inside the cycle |
| `scripts/ops/weather_live_doctor.py` | pm_agent | manual / monitoring |
| `scripts/ops/weather_live_status.py` | pm_agent | pause/resume control |
| `scripts/ops/daily_pipeline.py` | weather-predict | daily timer |
| `scripts/analysis/settle_t24_paper.py` | weather-predict | **manual** ⚠ |
| `scripts/ops/fill_t2_weather_cache.py` | weather-predict | manual / weekly |
| `scripts/ops/backup_data.sh` | weather-predict | daily timer |

### 5.2 Local analysis / sync scripts (in `scripts/ops/`)

| Script | Purpose |
|---|---|
| `sync_weather_remote.sh` | pull N100 mirror (both weather-predict and pm_agent halves) |
| `weather_position_monitor.py` | query wallet positions + observed temps for decision journal |
| `weather_ledger_daily_analysis.py` | daily PnL report from the paper ledger |
| `weather_execution_policy_compare.py` | compare execution policies (mid_price vs maker_queue) |
| `weather_decision_journal.py` | record manual notes/observations |
| `weather_source_backtest.py` / `weather_source_probe.py` | data-source experiments |

### 5.3 Dashboard ingest (in `weather_dashboard/`)

| Module | Reads | Writes |
|---|---|---|
| `legacy_migration/research_csv.py` | `t24_paper_*.csv` | signals, plans, orders, fills, settlements (paper/explore state) |
| `legacy_migration/live_cycle.py` | `live_cycle/*.json` + siblings | signals, plans, orders, fills (live state), strategy_config |
| `ingest/pm_history_settlements.py` | `cache/pm_history/*.json` | `settlements` for condition_id trade joins; `settlement_outcomes` for city/date/bracket source-grain research; raw near-binary prices normalized to 1/0 |
| `ingest/clob_fill_sync.py` | Polymarket data-api or CLOB | fills (status='filled') for real on-chain matches |
| `db/consolidate_configs.py` | strategy_config | config_aliases |
| `metrics/save.py` | the canonical caliber above | per-run cached metrics on `runs.metrics` |

### 5.4 Dashboard API + UI

- `weather_dashboard/api/app.py` — FastAPI app
- `weather_dashboard/api/routers/{runs,configs,compare,live}.py` — endpoints
- `frontend/strategy_dashboard/` — Vite + React UI (`/weather/runs`, `/weather/live`)

### 5.5 Legacy / deprecated (do not use, gated behind `ALLOW_LEGACY_V1=1`)

- `weather_dashboard/db/apply_schema.py` (v1 schema) — superseded by `apply_schema_canonical.py`.
- `weather_dashboard/ingest/settlements.py` — writes `final_yes` column that doesn't exist in canonical schema. Dead code after pm_history_settlements.py.
- `weather_dashboard/ingest/real_ledger_adapter.py` — predecessor of `legacy_migration/research_csv.py`.
- `weather_dashboard/cli/ingest_run.py` — v1 single-run ingest.
- The `BFF on port 8011` in `run_stack.sh` — old PMM/ARB framework, unused by the weather dashboard. Safe to drop.

These can be removed in a follow-up cleanup; keeping them now to avoid breaking any leftover references.

---

## 6. Known data gaps

These are real upstream gaps, not pipeline bugs. Don't try to "fix" them
by re-ingesting:

| Gap | Where | Workaround |
|---|---|---|
| Polymarket no resolution on **2026-05-17** for ~half the cities (27/52) | pm_history files exist but contain `null` | settle by REDEEM events from wallet activity (see clob_fill_sync), or accept the hole |
| Polymarket no resolution on **2026-05-18** entirely (52/52 null) | same | same |
| Chronic null cities (Boston, Lagos, Minneapolis, Phoenix) | Polymarket never opened markets for them | exclude from analysis or treat as "no signal" |
| pm_history for "today" is absent until daily_pipeline runs | normal | wait until tomorrow |
| Old near-binary settlement rule | expired rows marked `missing_bracket`; reports show values like 725/734/28 | rebuild after the 2026-06-06 fix; raw `0.9995/0.0005` must normalize to `1/0` |

The `pm_history_settlements` ingest skips null files automatically.

---

## 7. N100 automation gap

### 7.1 settle_t24_paper.py manual rerun

`settle_t24_paper.py` is **manual** today. The derived CSVs
(`t24_paper_ledger_trades.csv`, `t24_paper_snapshot_replay_trades.csv`)
go stale whenever no one reruns them.

Recommended: add a systemd timer on N100:

```ini
# ~/.config/systemd/user/settle-t24-paper.timer
[Unit]
Description=Daily settle_t24_paper regenerate

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# ~/.config/systemd/user/settle-t24-paper.service
[Unit]
Description=Regenerate settle_t24_paper CSVs

[Service]
Type=oneshot
WorkingDirectory=/home/jiarui/projects/weather-predict
ExecStart=/usr/bin/python3 scripts/analysis/settle_t24_paper.py --source ledger
ExecStart=/usr/bin/python3 scripts/analysis/settle_t24_paper.py --source snapshots
```

Until this is installed, run by hand after major paper-ledger movements:

```bash
ssh 192.168.0.200 'cd ~/projects/weather-predict && \
  python3 scripts/analysis/settle_t24_paper.py --source ledger && \
  python3 scripts/analysis/settle_t24_paper.py --source snapshots'
```

> **Side note:** `pm_history_settlements.py` reads pm_history directly, so
> the settlements table stays current without the CSV refresh. The CSVs are
> still needed for the paper backtest equity curve (the legacy_research
> config). If you only care about live PnL, the CSV refresh is optional.

### 7.2 backup_data.sh has stalled

`scripts/ops/backup_data.sh` on N100 hasn't been rerun since **2026-05-12**.
The disaster-recovery tar archives in `/home/jiarui/weather-predict-backups/`
are 24+ days stale — cache and output growth since then has no backup.

Fix: install a weekly systemd timer on N100:

```ini
# ~/.config/systemd/user/backup-weather-data.timer
[Unit]
Description=Weekly weather-predict data backup

[Timer]
OnCalendar=weekly
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# ~/.config/systemd/user/backup-weather-data.service
[Unit]
Description=Tar weather-predict data + sha256

[Service]
Type=oneshot
WorkingDirectory=/home/jiarui/projects/weather-predict
ExecStart=/usr/bin/bash scripts/ops/backup_data.sh
```

After the timer runs, the local mirror picks up new tars automatically via
`scripts/ops/sync_n100_backups.sh` (safe to also schedule weekly).

---

## 8. Operations runbook

### 8.1 Refresh everything end-to-end

```bash
cd /home/rui/projects/pm_agent

# 1. Pull N100 mirror.
scripts/ops/sync_weather_remote.sh

# 2. Rebuild DB + run all ingests.
scripts/weather_dashboard/run_stack.sh

# 3. (Once-per-DB) consolidate fragmented config_ids.
.venv/bin/python -m weather_dashboard.db.consolidate_configs \
  --db-path runtime/weather.db

# 4. Ingest pm_history settlements (idempotent).
.venv/bin/python -m weather_dashboard.ingest.pm_history_settlements \
  --db-path runtime/weather.db
```

### 8.2 Just check status

```bash
scripts/weather_dashboard/run_stack.sh --status
```

### 8.3 Diagnose "curve doesn't extend to today"

In order:

1. **Mirror stale?** `ls -t runtime/weather_edge_v1/market_data/cache/pm_history/ | head`.
   If oldest dates only, run `scripts/ops/sync_weather_remote.sh`.
2. **Settlements stale?**
   ```sql
   SELECT MAX(target_date) FROM settlements;
   SELECT MAX(target_date), COUNT(*) FROM settlement_outcomes;
   ```
   If older than yesterday, run `python -m weather_dashboard.ingest.pm_history_settlements`.
3. **Fills stale or under-ingested?**
   ```sql
   SELECT MAX(filled_at_utc) FROM fills WHERE status='filled';
   SELECT COUNT(DISTINCT execution_id) FROM orders WHERE status='submitted';
   SELECT COUNT(DISTINCT f.execution_id) FROM fills f
     JOIN orders o ON o.execution_id=f.execution_id
     WHERE o.status='submitted' AND f.status='filled';
   ```
   If filled-fill count << submitted-order count, `clob_fill_sync` linking
   is broken — see [WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md](WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md).
4. **Config fragmented?**
   ```sql
   SELECT COUNT(*) FROM strategy_config
   WHERE config_id NOT IN (SELECT DISTINCT canonical_config_id FROM config_aliases);
   ```
   Non-zero means the consolidate migration hasn't run.

---

## 9. Cleanup follow-ups (not blocking, "顺手优化")

Items already filed for cleanup; track in a future commit:

1. Delete `weather_dashboard/db/apply_schema.py` and `weather_dashboard/ingest/settlements.py` (legacy v1, wrong schema, dead code).
2. Done: the `BFF on port 8011` block was removed from `scripts/weather_dashboard/run_stack.sh` (old PMM framework, not used by the weather dashboard).
3. Move `legacy_migration/` → `ingest/` (it's not legacy, it's the current path; the name is misleading).
4. Fold `weather_dashboard/ingest/canonical.py` + `weather_dashboard/contract/canonical.py` into one `weather_dashboard/contract/` module — they only differ by usage site.
5. Done: `scripts/weather_dashboard/run_stack.sh` now calls `pm_history_settlements` and `consolidate_configs` during rebuild.
6. `clob_fill_sync.py`: `DEFAULT_MAKER_ADDRESS` is hardcoded to a wallet that isn't the current live signer. Current mitigation: when authenticated CLOB returns zero maker trades, fall back to funder activity matching. Follow-up: auto-derive the signer/maker from the keystore or require `--maker-address`.
