# Weather Repo Boundary

Status: current-source
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed

Last updated: 2026-06-19（三模块边界 + 数据层 + live 实例对齐）

This document defines the runtime boundary between the weather modules. It is
meant to prevent agents from treating similar file names as shared runtime code,
and to keep the **data layer / collection / execution** separate.

模块边界（按职责，不按机器）：`weather_data_feed/`（数据逻辑包）· weather-predict（采集运行，
调用 feed）· pm_agent（消费 → 策略/执行）· Mac pm_agents（分析/看板）。采集运行正按
[WEATHER_DATA_FEED_STEP3_MIGRATION_PLAN.md](WEATHER_DATA_FEED_STEP3_MIGRATION_PLAN.md)
从 weather-predict 迁到独立 `weather_data_feed_service/`，迁完 weather-predict 转 dormant；
**迁移期 weather-predict 仍在采集**。模块设计见 [WEATHER_DATA_FEED_MODULE.md](WEATHER_DATA_FEED_MODULE.md)。

## Runtime Roles

| Repo / host path | Runtime role | Owns | Must not own |
|---|---|---|---|
| `weather_data_feed/`（pm_agents 包，vendored 到 N100） | Data layer (逻辑) | city calendar, source profiles, observation parsers, snapshot protocol normalization | strategy/sizing/order/wallet/dashboard 逻辑 |
| N100 `weather_data_feed_service/`（step-3 后新建） | Data collection runtime（迁移目标） | 跑 snapshot + daily-pipeline，产标准数据产物 | 策略/下单 |
| N100 `/home/jiarui/projects/weather-predict` | 采集运行（**迁移期仍在跑**，调用 weather_data_feed；step-3 后退役 dormant） | market snapshots, orderbook snapshots, paper ledger, city pools, weather caches, settlement history | live CLOB execution, pm_agent dashboard DB |
| N100 `/home/jiarui/projects/pm_agent` | Production live execution | live signal files, trade plans, real CLOB order submissions, strategy instances, pause state, Telegram/live doctor | weather model cache generation, paper snapshot timer |
| Local Mac `/Users/deepsleep/projects/pm_agents` | Analysis, dashboard, and deployment staging | dashboard DB, ingest/migration, fact tables, strategy research, local code staging for N100 `pm_agent` | direct production data collection |
| Local Mac `/Users/deepsleep/projects/weather-predict` | Development copy for weather-predict | local edits/tests for N100 `weather-predict` scripts | production truth |
| Historical WSL `/home/rui/projects/pm_agent` | Legacy analysis path, only when the actual shell is WSL/Linux | same local-analysis role as above | direct production data collection |

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

Shared weather data primitives live in the local `pm_agents` git worktree under:

- `weather_data_feed/`

This package is the staging source for cross-repo data logic: city timezone and
target-date calendar, source profiles, bracket parsing, observation clock
guards, and snapshot protocol normalization. It must not contain strategy
selection, sizing, live order submission, wallet logic, or dashboard PnL logic.

`pm_agent` may call weather-predict only through explicit bridge tooling:

- `scripts/ops/weather_predict_bridge.py`
- `src/strategies/weather_edge_v1/tools/weather_predict_bridge.py`

That bridge is for local research/data-cache operations. The normal N100 live
execution loop does not import weather-predict modules.

`weather-predict` should not import pm_agent code. It writes files that pm_agent
later consumes.

The temporary strategy-path observation modules under
`src/strategies/weather_edge_v1/official_observation_feed/` are compatibility
re-exports of `weather_data_feed`. New shared data code should be added to
`weather_data_feed`, not under a strategy directory.

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

Active pm_agent live strategy instances（2026-06-19 更新，权威以
[WEATHER_STRATEGY_ENTRYPOINT.md](WEATHER_STRATEGY_ENTRYPOINT.md) /
[WEATHER_STRATEGY_REGISTRY.md](WEATHER_STRATEGY_REGISTRY.md) 为准）：

- **current YES tiny-live**：`weather_theta_current_yes_tiny_live.py` 的 `fade_confirmed` + `peak_forming_micro`（$5 微仓）。
- **metar-cross prev-NO**：`weather_metar_cross_prev_no_shadow.py --live`（$10/单·$50/天）。
- shadow（零 notional）：station-basis、range-rv、higher-no-carry。

旧 `mid_price_core_v1_25_75 / _side_band / v2_25_75` 已**因实盘亏损停用**（live_real 成交停在 2026-06-11），
转历史；不是证伪，是停用决策。

Historical dashboard migrations may still recognize `maker_queue_v1` so old
rows remain readable. That is not permission to generate new rows.
