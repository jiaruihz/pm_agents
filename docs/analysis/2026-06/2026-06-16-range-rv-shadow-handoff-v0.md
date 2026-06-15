# Forecast-Bounded Range RV Shadow Handoff v0

> status: current-handoff
> last_updated: 2026-06-16
> live_action: none

## Current Strategy Line

The active Range RV line is not a broad threshold search anymore. It is a
single forward shadow for:

```text
strategy_id = forecast_bounded_w3_cheaper_default_wu_edge002_shadow_v0
algorithm   = forecast_bounded_w3_cheaper
filter      = default_wu/no_filter
edge gate   = orderbook_native_edge >= 0.02
capacity    = every selected leg has at least 5 shares at top ask
mode        = zero_notional_shadow
```

This line consumes the new forecast-quality/source-aware base. Generic claims
must stay on the grain:

```text
city + event_date + forecast_source/model_version + decision_snapshot_ts_utc
```

`source_bucket=default_wu` is the only generic denominator. HK, Jakarta,
station-diff, blocked, missing-registry, and other source-sensitive cities are
diagnostic only unless their source features are rebuilt.

## File Inventory

### Research Reports

| File | Role |
|---|---|
| `docs/analysis/2026-06/2026-06-15-forecast-quality-source-adjusted-v0.md` | Forecast-quality/source-aware base used by this line |
| `docs/analysis/2026-06/2026-06-15-forecast-bounded-range-rv-source-aware-v0.md` | Source-aware forecast-bounded Range RV rerun |
| `docs/analysis/2026-06/2026-06-15-forecast-bounded-range-rv-live-standard-v1.md` | Live-standard hardening that picked the current shadow candidate |
| `docs/analysis/market_structure_edge.md` | Living market-structure/RV index and current verdict |
| `docs/analysis/model_vs_market.md` | Forecast-quality base consumer rule |

### Research Scripts

| File | Role |
|---|---|
| `scripts/analysis/forecast_quality/research_forecast_quality_source_adjusted_v0.py` | Source-bucket-aware forecast-quality base |
| `scripts/analysis/market_structure_edge/research_forecast_bounded_range_rv_source_aware_v0.py` | Source-aware Range RV opportunity/replay study |
| `scripts/analysis/market_structure_edge/research_forecast_bounded_range_rv_live_standard_v1.py` | Orderbook-native live-standard gate |
| `scripts/analysis/market_structure_edge/evaluate_range_rv_shadow_v0.py` | Current shadow settlement evaluator |

### N100 Runtime Scripts

| File | Role |
|---|---|
| `scripts/ops/range_rv_shadow_v0.py` | Reads latest weather-predict snapshot and appends zero-notional selected baskets |
| `scripts/ops/range_rv_shadow_loop.sh` | Runs the shadow runner every 30 minutes |
| `scripts/ops/start_range_rv_shadow_loop.sh` | Starts the loop with PID/log files |

### Runtime Data

| Path | Role |
|---|---|
| N100 `/home/jiarui/projects/pm_agent/runtime/weather_edge_v1/range_rv_shadow_v0/shadow_journal.jsonl` | Canonical N100 shadow journal |
| N100 `/home/jiarui/projects/pm_agent/runtime/weather_edge_v1/range_rv_shadow_v0/latest_summary.json` | Latest one-shot/loop summary |
| local `runtime/weather_edge_v1/remote_pm_agent/range_rv_shadow_v0/` | Synced local copy after `scripts/ops/sync_weather_remote.sh --live-only` |
| local `runtime/weather_edge_v1/market_data/cache/pm_history/` | Settlement truth after market-data sync |

## Commands

Sync runtime shadow data locally:

```bash
scripts/ops/sync_weather_remote.sh --live-only
```

Sync settlement truth locally:

```bash
scripts/ops/sync_weather_remote.sh --market-only
```

Evaluate current shadow status:

```bash
.venv/bin/python scripts/analysis/market_structure_edge/evaluate_range_rv_shadow_v0.py
```

Default outputs:

```text
docs/analysis/2026-06/2026-06-16-range-rv-shadow-status-v0.json
docs/analysis/2026-06/2026-06-16-range-rv-shadow-status-v0.md
```

Check N100 loop:

```bash
ssh jiarui@192.168.0.200 'cd /home/jiarui/projects/pm_agent && cat runtime/weather_edge_v1/live_cycle/range_rv_shadow_v0.pid && tail -20 runtime/weather_edge_v1/live_cycle/range_rv_shadow_v0.log'
```

## Current Evidence Status

As of the 2026-06-16 handoff, N100 shadow telemetry is running and writing
`no_order_placed=true` records. The settled forward sample is still too thin.
The correct action is to collect data, not tune thresholds or enable paper/live.

Primary conclusion grain is not raw journal rows. Raw rows repeat the same
city-day every 30 minutes. Use:

```text
city + event_date + forecast_source + model_version
```

Then compare `dedup_latest` and `dedup_first` as timing sensitivity checks.

## What To Observe Next

1. Settled dedup sample size: wait for at least 10 settled event dates and at
   least 30 dedup decision groups before making any live-oriented claim.
2. Baseline excess: compare against same-city/date/model no-filter or same-cost
   dumb range baseline before calling it alpha.
3. Timing policy: compare first trigger, latest trigger, and persistence filter
   such as two consecutive snapshots above threshold.
4. Expression stability: split `inside_yes` and `outside_no`; do not let cheaper
   expression selection hide one unstable leg type.
5. Capacity: every selected leg must keep 5-share top ask capacity; top5-removed
   and min-depth stress remain blockers.
6. Source discipline: source-sensitive cities remain excluded from generic
   conclusions.

## Do Not Do

- Do not call proxy results live edge.
- Do not count raw half-hour rows as independent trades.
- Do not mix this shadow journal with `live_real` PnL or CLOB account balances.
- Do not broaden the Range RV search while this forward shadow is still
  collecting data.
- Do not change N100 live ordering behavior from this line without a separate
  git-first deploy review.
