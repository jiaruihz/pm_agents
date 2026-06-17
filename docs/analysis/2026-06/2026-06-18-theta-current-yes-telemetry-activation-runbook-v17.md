# Current-YES Forecast Peak Telemetry Activation Runbook v17

Date: 2026-06-18
Status: implementation-ready, not runtime-active
Evidence layer: forward would-order telemetry

## Target Metric

This slice is not a new live trading rule. The target metric is:

```text
For every current-YES would-order state, record point-in-time observation clock,
fresh orderbook, and forecast peak clock fields so later settled outcomes can
score whether forecast peak timing improves current-bracket hold prediction.
```

The trading question remains split into two expressions:

- `current_yes_peak_forming`: buy the current high while temperature is still at
  or near the running max.
- `current_yes_fade_confirmed`: buy the current high after visible fade.

Both expressions should share the same no-reheat model features. Forecast peak
clock is one of those features, not a standalone edge.

## What Changed

`scripts/ops/start_weather_theta_current_yes_tiny_live.sh` now supports two
runtime modes:

```bash
THETA_CURRENT_YES_MODE=live       # default, preserves existing live behavior
THETA_CURRENT_YES_MODE=telemetry  # forward telemetry only, no live flags
```

The default remains `live` so existing production behavior is not silently
changed. Telemetry mode omits `--live --confirm-live`, so it can accumulate
would-order rows without submitting orders.

`scripts/ops/status_weather_theta_current_yes_tiny_live.sh` was added as a
read-only status helper. It reports:

- whether the loop process is running;
- latest summary status and row counts;
- forward telemetry row count and decision status distribution;
- forecast peak fetch status distribution.

## Data Integrity Snapshot

Local `runtime/weather.db` self-check before this runbook:

```text
MAX(fact_built_at_utc) = 2026-06-17T16:09:29.241520+00:00
fact_trades trade_class = live_real 855, live_simulated 624, paper 2285, snapshot_replay 636
fact_trades settlement_status = settled 4250, NULL 150
fact_signal_candidates = rows 31496, eligible 10961, paper_ordered 4274, live_filled 348
orders/fills = error 33 with_fill 0; submitted 961 with_fill 855
```

This is enough for research/report continuity. It is not proof that N100 has
loaded the new runner code.

## How To Run On N100

Use the git-first deploy path before running these commands on N100. The local
code must be present in `/home/jiarui/projects/pm_agent`.

Telemetry-only mode:

```bash
THETA_CURRENT_YES_MODE=telemetry NO_TELEGRAM=1 \
  scripts/ops/start_weather_theta_current_yes_tiny_live.sh
```

Live default mode:

```bash
scripts/ops/start_weather_theta_current_yes_tiny_live.sh
```

Read status:

```bash
scripts/ops/status_weather_theta_current_yes_tiny_live.sh
```

Stop the loop:

```bash
scripts/ops/stop_weather_theta_current_yes_tiny_live.sh
```

## Current Verdict

Forecast peak clock is now usable as a data field for forward measurement, but
it is not yet a live hard guard.

The reason is simple: historical scorecards show a real shape, but not enough
front-facing evidence. The bad bin is meaningful: when GFS says the peak is
still at least two hours ahead, holdout current-YES ROI was negative. But the
profitable filtered subsets are too small after historical backfill, and
forward telemetry currently has no real production rows from the new fallback.

Conclusion:

```text
significance=FAIL
baseline=PASS for fixed v9 only
forward=FAIL for forecast-clock upgrade
conclusion=inconclusive for forecast-clock live gating
```

Next required evidence is N100 forward telemetry with forecast peak fields
recorded on every would-order. After enough rows settle, the next report should
score:

- planned vs rejected rows;
- current-YES hit rate by `forecast_peak_delta_hours_local`;
- taker +2c ROI proxy by the same bins;
- `current_yes_peak_forming` vs `current_yes_fade_confirmed`;
- current YES vs d1/d2 higher NO expression on the same city-hour state.
