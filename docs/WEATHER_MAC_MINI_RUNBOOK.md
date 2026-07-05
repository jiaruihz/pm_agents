# Weather Mac Mini Runbook

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
- Polymarket market traffic uses an explicit, replaceable market proxy such as:
  `WEATHER_DATA_FEED_MARKET_PROXY=http://127.0.0.1:7890`
- Weather source traffic stays direct unless a separate weather proxy is
  explicitly configured.

The Mac path is intentionally narrower than N100: start data and shadow first,
then explicitly switch live only when the evidence is clean.

## Standard Commands

Install or refresh LaunchAgents:

```bash
scripts/ops/mac_weather_stack.sh install-launchagents
```

Start data-feed plus regime-routed shadow:

```bash
scripts/ops/mac_weather_stack.sh start
```

Check the whole stack:

```bash
scripts/ops/mac_weather_stack.sh status
scripts/ops/mac_weather_stack.sh verify
```

Stop Mac data-feed plus shadow:

```bash
scripts/ops/mac_weather_stack.sh stop
```

Start real live order runner only after explicit approval:

```bash
scripts/ops/mac_weather_stack.sh start-live --confirm-live
```

Stop real live order runner:

```bash
scripts/ops/mac_weather_stack.sh stop-live
```

Start the low-price YES lottery tiny-live runner only after explicit approval:

```bash
scripts/ops/mac_weather_stack.sh start-low-price-live --confirm-live
```

Stop the low-price YES lottery tiny-live runner:

```bash
scripts/ops/mac_weather_stack.sh stop-low-price-live
```

Start the low-price YES TP20 exit overlay only after explicit approval:

```bash
scripts/ops/mac_weather_stack.sh start-low-price-take-profit --confirm-live
```

Stop the low-price YES TP20 exit overlay:

```bash
scripts/ops/mac_weather_stack.sh stop-low-price-take-profit
```

`start` does not place real orders. It starts only:

- `com.pm-agents.weather-data-feed`
- `com.pm-agents.regime-routed-no-shadow`

## Verification Expectations

`verify` should show:

- mihomo `🙂 TAGSS` selected to a cheap node such as `🇯🇵 日本 01丨1x JP`
- N100 reverse tunnel, if enabled:
  `127.0.0.1:18089 -> Mac 127.0.0.1:7890`
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

Do not rely on global `HTTP_PROXY` / `HTTPS_PROXY` for the collector. The data
feed code uses `trust_env=false` in the market path to avoid accidental global
proxy burn. Configure market proxy explicitly in:

- `/Users/deepsleep/projects/weather_data_feed_service/.env`
- `/Users/deepsleep/projects/pm_agents/.env`

Required keys. The port is configurable; use whichever local proxy endpoint has
been verified on a cheap node and can reach Gamma/CLOB:

```bash
WEATHER_DATA_FEED_MARKET_PROXY=http://127.0.0.1:7890
WEATHER_PREDICT_MARKET_PROXY=http://127.0.0.1:7890
```

Live order submission uses the same market proxy contract. The common executor
`scripts/ops/weather_order_executor.py` maps `WEATHER_DATA_FEED_MARKET_PROXY`
into `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` before constructing the CLOB
client, so strategy runners should not carry their own unrelated proxy path.
Set `WEATHER_EXECUTOR_MARKET_PROXY=direct` only for an intentional direct CLOB
test.

The selected node is controlled by mihomo/TAG. Check it with:

```bash
scripts/ops/mac_weather_stack.sh proxy-status
```

Do not run the collector if the latest snapshot has `total_records=0`. The
collector now refuses to write empty paper snapshots by default; existing empty
files should be quarantined rather than used as latest market state.

## External Disk Plan

The high-write paths are:

- `~/projects/weather_data_feed_service_runtime`
- `runtime/weather_edge_v1/regime_routed_no_tiny_live_v1`
- optionally `runtime/n100_emergency_backup`

Use an APFS-formatted external SSD. Avoid exFAT for this workload. Recommended
mount path:

```text
/Volumes/WeatherRuntime
```

Dry run:

```bash
scripts/ops/prepare_mac_weather_external_runtime.sh --volume /Volumes/WeatherRuntime
```

Apply after checking the dry run:

```bash
scripts/ops/mac_weather_stack.sh stop
scripts/ops/prepare_mac_weather_external_runtime.sh --volume /Volumes/WeatherRuntime --apply
scripts/ops/mac_weather_stack.sh start
scripts/ops/mac_weather_stack.sh verify
```

Move emergency backups too:

```bash
scripts/ops/prepare_mac_weather_external_runtime.sh --volume /Volumes/WeatherRuntime --include-backups --apply
```

This script copies first, renames the old local path to
`.pre_external_<timestamp>`, then creates a symlink. It does not delete the old
copy.

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
