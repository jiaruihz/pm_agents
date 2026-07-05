# Mac Source-Events Gap Audit V1

Date: 2026-07-06
Status: current-reference

## Question

Mac temporary production currently has no
`/Users/deepsleep/projects/weather_data_feed_service_runtime/output/source_events/latest.json`.
This audit checks whether that is an intended Mac takeover boundary or a job
that should be running but is not.

## Verdict

The missing Mac `source_events/latest.json` is a current deployment/design
boundary of the Mac takeover path, not evidence of a failed Mac source-events
service.

Mac temporary production runs a narrower LaunchAgent stack than N100. The active
Mac data-feed LaunchAgent `com.pm-agents.weather-data-feed` executes
`scripts/ops/start_mac_weather_data_feed_loop.sh`, and that loop only runs:

- `weather_data_feed_service observations --output .../output/observations/latest.json`
- `weather_data_feed_service snapshot-targeted --output-root .../targeted_output`

It does not run `weather_data_feed_service source-events`, does not create
`output/source_events/`, and there is no Mac LaunchAgent for source-events in
`~/Library/LaunchAgents`.

## Evidence

- `docs/WEATHER_MAC_MINI_RUNBOOK.md` says the Mac path is intentionally narrower
  than N100 and starts data plus shadow first.
- `/Users/deepsleep/Library/LaunchAgents/com.pm-agents.weather-data-feed.plist`
  points to `scripts/ops/start_mac_weather_data_feed_loop.sh`.
- `scripts/ops/start_mac_weather_data_feed_loop.sh` creates and writes
  observation cache, targeted snapshots, orderbook snapshots, forecast curves,
  and cache state only. It has no `source-events` command or output path.
- `launchctl list` shows weather data-feed/API/runtime-monitor/frontend and
  `com.pm-agents.metar-reversal-shadow`; it shows no source-events or metar-cross
  LaunchAgent.
- `find /Users/deepsleep/projects/weather_data_feed_service_runtime/output`
  finds `observations/latest.json` but no `source_events/` directory.
- `scripts/ops/weather_data_feed_prod_health_check.py` currently checks the Mac
  snapshot/source-model path and observation cache, but has no
  `source_events` coverage.

## Current Consumer Impact

No currently active Mac LaunchAgent consumes `output/source_events/latest.json`.
The active METAR-related Mac LaunchAgent is
`com.pm-agents.metar-reversal-shadow`, which consumes
`output/observations/latest.json`, not source-events.

The local `runtime/weather_edge_v1/metar_cross_prev_no_shadow/` files are stale
manual/historical runtime output. Recent local cycle rows stop at
2026-06-23T14:42Z and used legacy live-fetch style fields such as
`obs_source=aviationweather_metar`, not current Mac source-events.

The N100 mirror under
`runtime/weather_edge_v1/remote_pm_agent/metar_cross_prev_no_shadow/` stops at
2026-06-26 and is recovery/historical context, not active Mac production.

## Documentation Delta

`docs/WEATHER_DATA_FEED_MODULE.md` and `docs/WEATHER_DATA_PIPELINE.md` still
describe the N100 source-events service as a standard data-feed output. That is
correct for N100 historical/recovery shape, but readers need the Mac exception:
the current Mac takeover LaunchAgent does not produce source-events unless a
separate reviewed source-events job is installed.

## No Runtime Change

This audit did not start a service, change LaunchAgents, change metar_cross, or
touch live order paths. If source-events is needed on Mac, it should be a
separate git-first deployment with health check coverage and consumer parity.
