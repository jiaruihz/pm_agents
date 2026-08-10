# Weather Mac Mini Runbook

> **状态：`superseded-for-now`，仅保留历史背景。** 本文原先的业务
> LaunchAgent、旧 Mac stack、live switch 和外置盘迁移命令不是
> 当前生产入口。当前事实以 `production.yaml` + `weather_production_ctl.py` +
> production manifest 为准；JRS runtime 已固定到 `/Volumes/jrs`，不要按本文再次迁盘。

This is the Mac-side fallback runbook for weather data collection and the
regime-routed NO strategy shadow runner. It exists because N100 normally owns
the production systemd path, but N100 is currently unsafe for write-heavy
runtime work after disk I/O errors and an ext4 emergency read-only remount.

## Current Shape

- Data collection runs from the standalone repo:
  `/Users/deepsleep/projects/weather_data_feed_service`
- Strategy/shadow/dashboard runs from:
  `/Users/deepsleep/projects/pm_agents`
- Runtime data is written to:
  `~/projects/weather_data_feed_service_runtime`
- Strategy runtime is written to:
  `runtime/weather_edge_v1/regime_routed_no_tiny_live_v1`
- Polymarket market traffic resolves the controller-owned endpoint from
  `production.yaml` and `market_proxy.json`; consumer-local endpoints are forbidden.
- Weather source traffic stays direct unless a separate weather proxy is
  explicitly configured.

The Mac path is intentionally narrower than N100: start data and shadow first,
then explicitly switch live only when the evidence is clean.

Current Mac data-feed scope is observation cache plus targeted market data. The
Mac LaunchAgent `com.pm-agents.weather-data-feed` does not produce
`output/source_events/latest.json`; the N100 `weather-data-feed-source-events`
timer remains historical/recovery context unless a separate Mac source-events
job is reviewed and installed.

## Current Replacement Commands

```bash
.venv/bin/python scripts/ops/weather_production_ctl.py health --json
.venv/bin/python scripts/ops/weather_production_ctl.py plan --json
.venv/bin/python scripts/ops/weather_production_ctl.py reconcile
.venv/bin/python scripts/ops/weather_production_ctl.py restart --instance INSTANCE --json
```

Only `reconcile/restart/recover-jrs-context` with their explicit `--apply`, reason,
and live confirmation contracts may change production. The old business
LaunchAgents are disabled at both filename and launchd override layers.

## Verification Expectations

`verify` should show:

- mihomo `🙂 TAGSS` selected to a cheap node such as `🇯🇵 日本 01丨1x JP`
- any historical N100 reverse tunnel is outside the current production contract
- observation cache with about `34/34 OK`
- latest targeted snapshot with hundreds of rows
- strategy `latest_summary.json`
- `live_enabled=false` for the shadow runner

Current healthy example:

- targeted snapshot: `751 rows`
- observation cache: `34 OK / 0 non_ok`
- strategy shadow: `routed_candidates=2`, `execution_eligible=0`
- no live orders placed

## Proxy Policy

Do not configure consumer-local proxy endpoints in `.env`. Read or switch the
single controller-owned endpoint with:

```bash
.venv/bin/python scripts/ops/weather_market_proxy_ctl.py status
.venv/bin/python scripts/ops/weather_market_proxy_ctl.py switch URL --reason REASON
```

Live order submission uses the same market proxy contract. The common executor
`scripts/ops/weather_order_executor.py` maps `WEATHER_DATA_FEED_MARKET_PROXY`
into `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` before constructing the CLOB
client, so strategy runners must not carry their own unrelated proxy path.
Set `WEATHER_EXECUTOR_MARKET_PROXY=direct` only for an intentional direct CLOB
test.

The selected node was controlled by mihomo/TAG. The old stack/proxy-failover entrypoints have been deleted;
use the controller health and production manifest network evidence instead:

```bash
.venv/bin/python scripts/ops/weather_production_ctl.py health --json
```

Do not run the collector if the latest snapshot has `total_records=0`. The
collector now refuses to write empty paper snapshots by default; existing empty
files should be quarantined rather than used as latest market state.

## External Disk Plan

This section is historical only. The canonical physical DB is now
`/Volumes/jrs/pm_agents/runtime/weather.db`, and the data-feed runtime is
`/Volumes/jrs/weather_data_feed_service_runtime`. Do not run the migration
commands below against current production.

The high-write paths are:

- `~/projects/weather_data_feed_service_runtime`
- `runtime/weather_edge_v1/regime_routed_no_tiny_live_v1`
- optionally `runtime/n100_emergency_backup`

Use an APFS-formatted external SSD. Avoid exFAT for this workload. Recommended
mount path:

```text
/Volumes/WeatherRuntime
```

The old migration script and Mac stack have been deleted. Their commands must not be replayed against the current
NVMe contract; historical implementation remains recoverable from git history.

## N100 Current State

N100 should not run write-heavy weather production right now.

Observed state on 2026-07-01 UTC:

- `/` is mounted with `emergency_ro`
- normal write probe fails with `Read-only file system`
- kernel logs show:
  - `Medium Error`
  - `Unrecovered read error - auto reallocate failed`
  - `I/O error, dev sda`
  - `Aborting journal`
  - `EXT4-fs ... Remounting filesystem read-only`

This is a disk/media or block-device reliability incident, not a normal service
restart issue. Do not online `remount,rw` for production. Safe roles until
maintenance:

- SSH endpoint
- read-only inspection
- reverse proxy endpoint if needed

Unsafe roles until repair:

- market/orderbook collector
- weather data feed
- live strategy runner
- dashboard refresh jobs
- anything writing SQLite, JSONL, caches, or logs

Required maintenance before N100 returns to production:

1. Boot into rescue/live environment or maintenance mode.
2. Run SMART/drive health check.
3. Replace disk if SMART or kernel media errors confirm failure.
4. Run offline `fsck` on the ext4/LVM filesystem.
5. Only after a clean filesystem and write probe, re-enable systemd user timers.
