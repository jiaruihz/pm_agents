# Current-YES Parallel Forecast Telemetry Rollout v19

Date: 2026-06-18
Status: deployment-ready
Evidence layer: forward telemetry, no live orders

## Target

Run a new forward-telemetry collector in parallel with the existing
`theta_current_yes_tiny_live_v1` process:

```text
strategy_instance = theta_current_yes_forecast_telemetry_v1
runtime_dir = runtime/weather_edge_v1/theta_current_yes_forecast_telemetry_v1
mode = telemetry only
live flags = omitted
```

This lets us collect forecast peak clock, observation-clock, fresh-book, and
decision-status rows without stopping or replacing the existing tiny-live
process.

## Code Change

The current-YES runner and wrapper scripts now support:

```bash
THETA_CURRENT_YES_RUNTIME_DIR=runtime/weather_edge_v1/theta_current_yes_forecast_telemetry_v1
THETA_CURRENT_YES_STRATEGY_INSTANCE=theta_current_yes_forecast_telemetry_v1
THETA_CURRENT_YES_MODE=telemetry
```

Default behavior is unchanged. If no environment variables are set, the runner
still uses:

```text
strategy_instance = theta_current_yes_tiny_live_v1
runtime_dir = runtime/weather_edge_v1/theta_current_yes_tiny_live_v1
```

## Local Smoke

Command:

```bash
THETA_CURRENT_YES_RUNTIME_DIR=runtime/weather_edge_v1/theta_current_yes_forecast_telemetry_v1 \
THETA_CURRENT_YES_STRATEGY_INSTANCE=theta_current_yes_forecast_telemetry_v1 \
.venv/bin/python scripts/ops/weather_theta_current_yes_tiny_live.py run \
  --no-telegram \
  --max-snapshot-age-min 100000 \
  --max-obs-age-min 20 \
  --pre-metar-update-blackout-min 6 \
  --min-gap-to-next-bracket-c 0
```

Result:

```text
status=planned
live_enabled=false
strategy_instance=theta_current_yes_forecast_telemetry_v1
forward_telemetry_rows=45
plans=0
```

The smoke ran outside the active local trading window, so rows were audit rows
and did not need forecast peak fetches. During active windows, current rows
should carry `forecast_peak_fetch_status` and related peak-clock fields.

## N100 Start Command

After the remote checkout is moved to this commit:

```bash
THETA_CURRENT_YES_RUNTIME_DIR=runtime/weather_edge_v1/theta_current_yes_forecast_telemetry_v1 \
THETA_CURRENT_YES_STRATEGY_INSTANCE=theta_current_yes_forecast_telemetry_v1 \
THETA_CURRENT_YES_MODE=telemetry \
NO_TELEGRAM=1 \
scripts/ops/start_weather_theta_current_yes_tiny_live.sh
```

Status:

```bash
THETA_CURRENT_YES_RUNTIME_DIR=runtime/weather_edge_v1/theta_current_yes_forecast_telemetry_v1 \
scripts/ops/status_weather_theta_current_yes_tiny_live.sh
```

Stop:

```bash
THETA_CURRENT_YES_RUNTIME_DIR=runtime/weather_edge_v1/theta_current_yes_forecast_telemetry_v1 \
scripts/ops/stop_weather_theta_current_yes_tiny_live.sh
```

## Live Upgrade Gate

This rollout does not promote a new live rule. A reliable live upgrade needs:

```text
1. Forward telemetry rows across multiple active local windows.
2. Settled outcomes for those would-orders.
3. Forecast-clock bins that improve current-YES ROI or avoid clear danger bins
   without shrinking the sample below the support gate.
4. A paired expression check versus d1/d2 higher NO on the same city-hour state.
5. A small live policy diff with the telemetry result frozen before deployment.
```
