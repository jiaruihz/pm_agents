# Current-YES Parallel Forecast Telemetry Deploy v20

Date: 2026-06-18
Status: deployed on N100, telemetry-only

## What Is Running

N100 `/home/jiarui/projects/pm_agent` was moved to:

```text
d01ba9bd ops: allow parallel current yes telemetry runtime
```

Two current-YES processes are now visible:

```text
old live:
  pid=880777
  strategy_instance=theta_current_yes_tiny_live_v1
  mode=--live --confirm-live

parallel telemetry:
  pid=902958
  strategy_instance=theta_current_yes_forecast_telemetry_v1
  runtime_dir=runtime/weather_edge_v1/theta_current_yes_forecast_telemetry_v1
  mode=telemetry only
```

The new telemetry process omits `--live --confirm-live`, so it does not submit
orders.

## First Remote Status

First status check:

```text
generated_at_utc=2026-06-17T18:02:30+00:00
snapshot_ts_utc=2026-06-17T17:30:53Z
status=planned
current_rows=0
candidate_rows=0
plans=0
live_enabled=False
forward_telemetry_rows=42
decision_status_counts={'outside_hour': 25, 'stale_obs': 5, 'target_date_not_local_date': 12}
forecast_peak_fetch_status_counts={'': 42}
```

This first cycle was outside most active local city windows, so it only emitted
audit rows. The useful test is the next active windows, where current rows
should include forecast peak fetch status and peak-clock fields.

## Next Gate

Do not upgrade this to live yet. The next gate is:

```text
collect forward telemetry -> settle outcomes -> score hold/ROI by forecast
peak delta, obs age, minutes-to-next-obs, and current YES vs d1/d2 NO sibling
expression.
```

Only after those forward rows beat the current v9 reference should we freeze a
new policy and consider tiny-live promotion.
