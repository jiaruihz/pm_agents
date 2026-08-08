# Weather State Frame Parity v1

Status: snapshot
Generated: 2026-07-06
Scope: offline parity only; no live runner rewiring

## Verdict

`weather_feature_layer.builders.build_weather_state_frame()` is parity-safe for
the shared tmax state fields except one intentional time precision difference:
`decision_hour_local`.

On the N100 recovery sample
`snapshot_20260701_1556.json` + `observations/latest.json`:

- feature-layer state rows: 35
- legacy tmax state rows: 23
- common city/date rows compared: 23
- shared field comparisons: 851
- exact/normalized matches: 828
- mismatches: 23, all in `decision_hour_local`

## Sample And Harness

Harness:
`scripts/analysis/feature_layer/weather_state_frame_parity_v1.py`

Inputs:

- `runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/paper_snapshots/snapshot_20260701_1556.json`
- `runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/observations/latest.json`

Generated artifacts:

- `docs/analysis/2026-07/generated/weather_feature_layer_state_parity_v1/summary.json`
- `docs/analysis/2026-07/generated/weather_feature_layer_state_parity_v1/field_diff.csv`
- `docs/analysis/2026-07/generated/weather_feature_layer_state_parity_v1/mismatch_examples.csv`
- `docs/analysis/2026-07/generated/weather_feature_layer_state_parity_v1/feature_state_rows.csv`
- `docs/analysis/2026-07/generated/weather_feature_layer_state_parity_v1/legacy_tmax_state_rows.csv`

The harness imports the current tmax live-candidate `build_state_rows()` but
does not run `main()`, does not place orders, and does not write runtime state.

## Compared Fields

Included shared mechanism fields:

- identity/time/source: `decision_snapshot_ts_utc`, `decision_hour_local`,
  `unit`, `timezone`, `forecast_source`, `obs_source`
- forecast/runway: `forecast_max_f`, `forecast_max_native`,
  `forecast_peak_hour_local`, `forecast_peak_delta_hours_local`,
  `forecast_peak_hour_spread`, `forecast_gap_to_running_native`
- observation/path: `current_temp_c`, `running_max_c`, `current_native`,
  `running_native`, `decline_native`, `running_value`, `tmpf_now`, `dwpf_now`,
  `dewpoint_depression_f`, `relative_humidity_pct`, `wind_speed_kt`,
  `sky_cover_code`, `temp_trend_1h_f`, `temp_trend_3h_f`,
  `minutes_since_running_max`, `running_max_obs_utc`, `obs_age_min`
- shared labels: `city_family`, `solar_window`, `day_regime`,
  `moisture_cloud_regime`, `wind_regime`, `running_max_state`,
  `intraday_state`, `composite_regime`

Excluded as intentionally tmax-private:

- current/d1/d2 bracket ladder mapping
- bid/ask/depth fields
- distribution features
- selector, sizing, executor, and order fields

## Differences

The only remaining diff is `decision_hour_local`:

- feature layer: decimal local hour from the snapshot clock/peak delta, e.g.
  `09:56:37` -> `9.93`
- legacy tmax: integer local hour bucket from `ts_local[11:13]`, e.g.
  `09:56:37` -> `9`

This is not a temperature, observation, or regime-label drift. In this sample,
all shared labels match despite the precision difference. A future tmax consumer
switch must either:

- keep using a legacy integer hour bucket for behavior-equivalent tmax decisions,
  or
- explicitly accept the precision upgrade and run a separate decision replay.

Do not silently replace tmax `decision_hour_local` with the decimal field in a
live candidate runner.

## Fixes Made During Harnessing

Two non-semantic builder issues were fixed before the final run:

- `forecast_max_f` now preserves the snapshot field when present instead of
  recomputing from native C/F and introducing rounding drift.
- observation-cache aliases now include `sky_code_now`, `d_tmpf_1h`, and
  `d_tmpf_3h`; UTC timestamp comparison in the harness normalizes `Z` and
  `+00:00`.

## Recommendation

The shared state builder can be used for offline tmax parity expansion and for
new research materialization. It is not yet approved as a drop-in replacement
for live tmax decision rows until the hour-bucket adapter is added and replayed.

Theta/current and regime-routed consumer rewiring remains out of scope for this
report; their private freshness gates should get separate parity windows before
switching.
