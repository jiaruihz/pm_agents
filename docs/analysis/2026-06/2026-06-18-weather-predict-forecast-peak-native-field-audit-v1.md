# Weather Predict Forecast Peak Native Field Audit v1

Status: deploy_blocked_by_repo_state
Generated: 2026-06-17T16:58:00+00:00

Target metric: `forecast_peak_native_snapshot_fields` = production paper snapshots should emit point-in-time forecast peak clock fields, not only historical research backfill.

## Human Conclusion

The native snapshot-field implementation already exists in the local `weather-predict` development copy, but it is not running on N100 production.

This is now a deployment/repo-boundary problem, not a feature-design problem:

- Local `/Users/deepsleep/projects/weather-predict/paper_snapshot.py` has `_forecast_details_from_open_meteo`, `forecast_peak_hour_local`, `forecast_peak_time_local`, `forecast_peak_hour_utc`, `forecast_peak_time_utc`, `forecast_hourly_count`, `forecast_values_hash`, `forecast_peak_source`, `forecast_timezone`, `forecast_utc_offset_seconds`, and `forecast_peak_delta_hours_local`.
- Local `paper_snapshot.py` compiles, and a function-level smoke test produced a sane Helsinki-style peak clock payload.
- N100 `/home/jiarui/projects/weather-predict/paper_snapshot.py` does not contain those fields.
- Both local and N100 `weather-predict` directories are currently not git worktrees, so the normal git-first deploy path cannot be completed from this state.

## Evidence

Local compile:

```text
python3 -m py_compile paper_snapshot.py scripts/ops/fill_t2_weather_cache.py city_pools.py
passed
```

Local smoke:

```text
_forecast_details_from_open_meteo(...)
=> {
  'max_f': 69.8,
  'peak_time_local': '2026-06-16T13:00',
  'peak_hour_local': 13,
  'peak_time_utc': '2026-06-16T10:00:00Z',
  'peak_hour_utc': 10,
  'hourly_count': 4,
  'values_hash': '36e9da202f61c58a',
  'source_model': 'ecmwf',
  'source_api': 'open_meteo_live_ecmwf',
  'timezone': 'Europe/Helsinki',
  'utc_offset_seconds': 10800
}
```

N100 field check:

```text
forecast_peak_hour_local False
forecast_values_hash False
_forecast_details_from_open_meteo False
```

N100 repo check:

```text
cd /home/jiarui/projects/weather-predict && git rev-parse --is-inside-work-tree
=> not_git
```

## Required Next Step

Do not claim production native peak fields are live yet.

To move this to production safely, choose one of two paths:

1. Git-first repair: create or locate a git-backed `weather-predict` source of truth, commit the local `paper_snapshot.py` native peak-field implementation, then deploy to N100 by checkout/pull/bundle.
2. Explicit fallback deploy: only with user approval, backup N100 `paper_snapshot.py`, copy the local implementation, run N100 `py_compile`, run a no-orderbook snapshot smoke, then `doctor_restart.sh`.

Given the project deploy rules, path 1 is preferred. Path 2 changes production behavior and should not happen silently.

## Trading Meaning

Until this is deployed and snapshots are regenerated:

- historical `forecast_peak_clock_backfill_v1.csv` can support research and scorecards;
- current-YES forward telemetry can include forecast peak fields only when the runner has such fields available in snapshots or derives them separately;
- forecast peak clock remains `not_live_ready_upgrade` even though v14 found a useful risk shape.
