# Current-YES Forecast Guard Live Upgrade v21

Date: 2026-06-18
Status: superseded on 2026-06-19 by `2026-06-19-theta-current-yes-filter-simplification-v22.md`

Supersession note: this document records the 2026-06-18 deployment state. On
2026-06-19 the forecast peak hard guard was removed from live planning and kept
only as model/telemetry context.

## What Changed

The current-YES tiny-live rule now requires forecast peak context before a row
can become a live plan.

New default gates:

```text
allow_missing_forecast_peak = false
min_forecast_peak_delta_hours = -1.999
```

Meaning:

```text
Do not buy current-YES if forecast peak is missing.
Do not buy current-YES if the forecast peak is still about 2h or more ahead.
```

This is a small reliability filter, not a new model. It keeps the existing v9
rule but blocks the clearest historical danger bin from v18:

```text
danger_gfs_peak_still_2h_ahead:
  holdout current-YES win rate = 24.6%
  holdout current-YES ROI = -18.0%
```

## Deployment

N100 `/home/jiarui/projects/pm_agent` is on:

```text
ab7e8a16 strategy: require current yes forecast peak guard
```

The old live process was stopped and replaced by one live process:

```text
pid=903814
strategy_instance=theta_current_yes_tiny_live_v1
mode=--live --confirm-live
min_forecast_peak_delta_hours=-1.999
```

Parallel telemetry was also restarted on the same code:

```text
pid=903819
strategy_instance=theta_current_yes_forecast_telemetry_v1
mode=telemetry only
min_forecast_peak_delta_hours=-1.999
```

## First Remote Check

First live summary after restart:

```text
generated_at_utc=2026-06-17T18:07:59+00:00
snapshot_ts_utc=2026-06-17T17:30:53Z
current_rows=0
candidate_rows=0
plans=0
live_enabled=True
forward_telemetry_rows=42
```

Live order file remained at 5 rows after the restart, so no duplicate order was
created during deployment.

## Tonight's Operating Interpretation

The runner is now allowed to submit tiny live orders during active US local
windows, but only when all of these are true:

- local hour is within the configured current-YES window;
- observed temperature has faded from the running max;
- the current high bracket still has sufficient model edge and liquidity;
- fresh CLOB ask passes the taker cushion check;
- observation clock guards pass;
- forecast peak is present and is not still far ahead.

This is still capped at the existing tiny-live sizing:

```text
max_order_notional = 5
max_city_day_notional = 10
```

The next evaluation should compare submitted/planned rows against the parallel
telemetry rows and later settlements.
