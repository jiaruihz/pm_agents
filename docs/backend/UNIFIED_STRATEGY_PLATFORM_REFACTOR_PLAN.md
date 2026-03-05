# Unified Strategy Platform: Database + API Specification

Last Updated: 2026-03-05

## 1. Scope

This document defines the implemented runtime model for all strategies across domains.

- Removed: `research_tasks` table and async task APIs.
- Added: global strategy catalog under `src/strategies/`.
- Added: platform runtime DB `runtime/strategy_runtime.db`.
- Added: unified BFF server `src/interfaces/web/strategy_dashboard_server.py`.
- Removed: legacy PMM-only runtime tables and old web UIs/servers.

## 2. Project Layout

### 2.1 Source of truth for strategy metadata

- `src/strategies/registry.py`
- `src/strategies/schema.py`
- `src/strategies/<strategy_key>/manifest.yaml`
- `src/strategies/<strategy_key>/README.md`
- `src/strategies/<strategy_key>/params.example.json`
- `src/strategies/<strategy_key>/run.sh`

### 2.2 Runtime infrastructure

- `src/platform/strategy_runtime/store.py`
- `runtime/strategy_runtime.db`

### 2.3 Dashboard API server

- `src/interfaces/web/strategy_dashboard_server.py`

## 3. Database Specification

Database path:

- `runtime/strategy_runtime.db` (default)
- env: `STRATEGY_RUNTIME_DB_PATH`
- compatibility fallback env: `PMM_INSTANCE_DB_PATH` (deprecated)

### 3.1 Table: `strategies`

Purpose: global strategy catalog.

- `strategy_key` TEXT PRIMARY KEY
- `strategy_name` TEXT NOT NULL
- `strategy_group` TEXT NOT NULL
- `strategy_family` TEXT NOT NULL
- `domain` TEXT NOT NULL
- `is_active` INTEGER NOT NULL
- `runner_module` TEXT NOT NULL
- `description` TEXT NOT NULL
- `meta_json` TEXT NOT NULL
- `created_at_utc` TEXT NOT NULL
- `updated_at_utc` TEXT NOT NULL

### 3.2 Table: `strategy_instances`

Purpose: one row per runtime instance.

- `instance_id` TEXT PRIMARY KEY
- `strategy_key` TEXT NOT NULL (logical FK to `strategies.strategy_key`)
- `label` TEXT NOT NULL
- `status` TEXT NOT NULL (`running|stopped|error|stale`)
- `execution_mode` TEXT NOT NULL (`paper|live|backtest`)
- `market_data_source` TEXT NOT NULL
- `account_id` TEXT NOT NULL
- `wallet_address` TEXT NOT NULL
- `token_ids_json` TEXT NOT NULL
- `max_position` REAL NOT NULL
- `telegram_enabled` INTEGER NOT NULL
- `pid` INTEGER NOT NULL
- `host` TEXT NOT NULL
- `log_file` TEXT NOT NULL
- `metrics_path` TEXT NOT NULL
- `cwd` TEXT NOT NULL
- `run_params_json` TEXT NOT NULL
- `runtime_paths_json` TEXT NOT NULL
- `started_at_utc` TEXT NOT NULL
- `updated_at_utc` TEXT NOT NULL
- `stopped_at_utc` TEXT NOT NULL
- `notes` TEXT NOT NULL

### 3.3 Table: `strategy_instance_state`

Purpose: latest mutable state (1:1 by instance_id).

- `instance_id` TEXT PRIMARY KEY
- `heartbeat_at_utc` TEXT NOT NULL
- `last_tick` INTEGER NOT NULL
- `last_pnl` REAL NOT NULL
- `last_equity` REAL NOT NULL
- `last_usdc` REAL NOT NULL
- `open_orders` INTEGER NOT NULL
- `fills_total` INTEGER NOT NULL
- `placed_total` INTEGER NOT NULL
- `canceled_total` INTEGER NOT NULL
- `errors_total` INTEGER NOT NULL
- `state_json` TEXT NOT NULL

### 3.4 Table: `strategy_instance_snapshots`

Purpose: historical time series snapshots for charting/replay/debug.

- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `instance_id` TEXT NOT NULL
- `ts_utc` TEXT NOT NULL
- `tick` INTEGER NOT NULL
- `pnl` REAL NOT NULL
- `equity` REAL NOT NULL
- `usdc` REAL NOT NULL
- `open_orders` INTEGER NOT NULL
- `fills_total` INTEGER NOT NULL
- `placed_total` INTEGER NOT NULL
- `canceled_total` INTEGER NOT NULL
- `errors_total` INTEGER NOT NULL
- `state_json` TEXT NOT NULL

### 3.5 Indexes

- `idx_instances_updated` on `strategy_instances(updated_at_utc DESC)`
- `idx_instances_status` on `strategy_instances(status)`
- `idx_instances_strategy` on `strategy_instances(strategy_key, updated_at_utc DESC)`
- `idx_instances_account` on `strategy_instances(account_id, wallet_address)`
- `idx_state_heartbeat` on `strategy_instance_state(heartbeat_at_utc DESC)`
- `idx_snapshots_instance_ts` on `strategy_instance_snapshots(instance_id, ts_utc DESC)`

### 3.6 Snapshot retention

- Snapshot interval: default `60s`
- Retention: default `30 days`
- Cleanup: purge old rows at store init; periodic purge supported by `purge_snapshots_older_than(days)`.

## 4. Write Semantics

- `strategies`: upsert on startup from `src/strategies/*/manifest.yaml`.
- `strategy_instances`: upsert when instance starts; update status on stop/error.
- `strategy_instance_state`: updated on heartbeat.
- `strategy_instance_snapshots`: periodic sampling + terminal snapshot on stop/error.

## 5. HTTP API Specification (`/api/v1`)

Unified response rules:

- timestamps are UTC ISO8601 strings.
- errors: `{code,message,details,request_id,timestamp_utc}`.
- list endpoints use `limit/offset` where applicable.

### 5.1 Strategies / Instances / Accounts

- `GET /api/v1/strategies`
- `GET /api/v1/instances?status=&strategy_key=&execution_mode=&account_id=&wallet_address=&limit=&offset=&stale_after_sec=`
- `GET /api/v1/instances/{instance_id}`
- `GET /api/v1/instances/{instance_id}/history?limit=`
- `GET /api/v1/accounts?limit=&offset=`

Account aggregation key precedence:

1. `account_id`
2. `wallet_address`
3. `unknown_account`

### 5.2 Research (synchronous only)

- `GET /api/v1/research/markets?page=&page_size=`
- `GET /api/v1/research/markets/{market_id}`
- `POST /api/v1/research/actions/filter`
- `POST /api/v1/research/actions/parse`
- `POST /api/v1/research/actions/prompt`
- `POST /api/v1/research/actions/run_all`

No task queue table is used in v1.

### 5.3 Backtest + Ops

- `GET /api/v1/backtests/runs`
- `GET /api/v1/backtests/table?run=&q=`
- `GET /api/v1/backtests/scenario?run=&scenario_run_id=`
- `GET /api/v1/ops/status`
- `GET /api/v1/ops/logs?name=&lines=`
- `GET /api/v1/supervisor/sessions?limit=`
- `GET /api/v1/health`

## 6. Removed Components

- `src/domains/pmm/ops/*`
- `src/domains/pmm/strategy_packs/*`
- `src/domains/pmm/backtest/web_server.py`
- `src/domains/research/web_server.py`
- `web_ui/backtest/*`
- `web_ui/research/*`
- `scripts/python/pmm_strategy_packs.py`
- `pmm_backtest.py web` command
- `research cli web` command

## 7. Runtime Migration Policy

- old DB `runtime/pmm_instances.db` is deprecated and not migrated.
- new runtime data is written to `runtime/strategy_runtime.db`.
- stale old DB file can be archived or deleted safely.
- runtime artifacts (`runtime/*.pid`, `runtime/*.json`, `runtime/logs/*.log`) are treated as local artifacts and excluded from release change scope.

## 8. Release and Rollback

### 8.1 Recommended release commit split

1. `feat(strategy-runtime): add platform runtime store + /api/v1 dashboard server`
2. `refactor(strategy-catalog): move strategy metadata to src/strategies`
3. `chore(cleanup): remove legacy pmm ops/strategy_packs/web_ui/web servers`
4. `docs(runbook): unify db/api paths and add execution log`

### 8.2 Rollback notes

1. If rollback is required, disable new BFF entrypoint and restore old runtime readers.
2. Since no data migration from `pmm_instances.db` was performed, rollback has no dual-write consistency burden.
3. Rollback will lose continuity for data written only into `strategy_runtime.db` after cutover.
