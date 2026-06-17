# Theta Current YES Forecast Peak Live Fallback v16

Status: code_ready_not_runtime_active
Generated: 2026-06-17T17:05:00+00:00

Target metric: `current_yes_forecast_peak_forward_telemetry_completeness` = forward telemetry should have forecast peak clock fields even when production paper snapshots do not yet emit native `forecast_peak_*`.

## Human Conclusion

This closes one practical data gap on the `pm_agent` side.

Before this patch, current-YES telemetry could only carry forecast peak fields if `weather-predict` snapshots already had them. N100 production snapshots do not. Now the current-YES runner can derive them itself from Open-Meteo hourly forecast, cache the payload under the strategy runtime directory, and write an explicit `forecast_peak_fetch_status` into trade plans and forward telemetry.

This does not change the v9 trading rule. It only makes the evidence layer richer:

- if snapshot has native peak fields: `forecast_peak_fetch_status=snapshot_native`;
- if snapshot lacks peak fields but has forecast source/max/model: fetch Open-Meteo hourly forecast and write `forecast_peak_fetch_status=fetched` or `cache`;
- if no forecast source is present: `forecast_peak_fetch_status=snapshot_missing_no_forecast_source`;
- if fetch fails: record `forecast_peak_fetch_status=fetch_failed:<type>` and the error.

## Validation

Local validation passed:

```text
.venv/bin/python -m py_compile scripts/ops/weather_theta_current_yes_tiny_live.py
.venv/bin/pytest tests/pmm_tests/test_theta_current_yes_live_guards.py tests/pmm_tests/test_build_weather_signal_candidates_forecast_peak.py tests/pmm_tests/test_official_observation_clock.py
=> 15 passed
```

No-live smoke with an artificial snapshot missing native peak fields:

```text
{
  "result": 1,
  "status": "planned",
  "forecast_peak_fetch_status": "fetched",
  "forecast_peak_hour_local": 13,
  "forecast_peak_delta_hours_local": 0.16666666666666607
}
```

## Trading Meaning

This is still not a live upgrade.

It enables the next forward evidence step: once the N100 current-YES process loads this code, every would-order can carry forecast peak clock fields even before `weather-predict` native snapshot fields are deployed. After enough rows settle, v15 can be rerun with real forward hit rate and taker ROI by `forecast_peak_fetch_status`, peak delta, obs age, and fresh-book state.

Current blockers remain:

1. N100 current-YES loop is still running an older loaded process until explicitly restarted.
2. N100 `weather-predict` snapshots still do not natively emit peak fields.
3. Forward telemetry currently has only the old non-live smoke rows; no planned forward sample has settled yet.

## Code Paths

- Runner: `scripts/ops/weather_theta_current_yes_tiny_live.py`
- Tests: `tests/pmm_tests/test_theta_current_yes_live_guards.py`
