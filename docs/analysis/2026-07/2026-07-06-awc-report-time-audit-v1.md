# AWC reportTime Timestamp Audit v1

Date: 2026-07-06

Scope: read-only audit of locally persisted AviationWeather-like rows. No live runner behavior was changed, no network fetch was performed, and no source_events service was started.

## Verdict

- Persisted `source_report_ts_utc` is consistent with raw METAR `DDHHMMZ`: `283754` matches, `43` diffs.
- source_report-vs-DDHHMMZ diffs appeared only in: `/Users/deepsleep/projects/pm_agents/runtime/analysis_inputs/forecast_update_time_repricing_v0/sources.jsonl`.
- No persisted rows carried raw API `reportTime`/`obsTime`, so local disk evidence cannot directly prove whether the AviationWeather API field ever equals fetch/detect time.
- Detection/fetch time is materially later than observation time: detect lag summary `n=283797, min=0.3, p50=22.8, p90=46.4, p95=55.6, p99=63.4, max=1474.0`.
- Contract conclusion: carry both observation time and detection time. Observation time = `source_report_ts_utc` derived from raw METAR `DDHHMMZ`; detection time = `local_detect_ts_utc` / `source_fetch_end_utc`.

## Impact Read

This audit does not prove an active live bug in theta/metar_cross direct AWC fetches, because the local historical payloads have already been normalized and do not preserve the original API `reportTime`. It does prove the contract requirement: `source_report_ts_utc` must come from raw METAR `DDHHMMZ`, while `local_detect_ts_utc`/`source_fetch_end_utc` are separate detection times. If a caller feeds detect/fetch time into `reportTime`, freshness age is understated by the detect lag below.

Affected code surfaces to review before any live fix:

- `weather_data_feed.observation_sources.parse_aviationweather_records` already prefers raw METAR `DDHHMMZ` over API `reportTime`.
- `weather_source_orderbook_timing_monitor.py` already uses the shared AWC parser.
- `weather_theta_current_yes_tiny_live.py::aviationweather_obs`, `weather_metar_cross_prev_no_shadow.py::parse_metar_records`, and `regime_routed_no_tiny_live.py` still consume `reportTime` directly when they live-fetch AviationWeather JSON.
- `metar_cross` source-events mode is not affected by this specific parser issue because it consumes normalized `source_report_ts_utc`.

## Input Summary

| Input | AWC rows | OK rows | raw METAR rows | raw API reportTime rows | source_report==DDHHMMZ | source_report diffs | detect lag min |
|---|---:|---:|---:|---:|---:|---:|---|
| `/Users/deepsleep/projects/pm_agents/runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/source_events/sources.jsonl` | 71680 | 68730 | 68730 | 0 | 68730 | 0 | n=68730, min=0.3, p50=24.7, p90=53.1, p95=59.1, p99=66.9, max=579.4 |
| `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/source_orderbook_timing/sources.jsonl` | 164928 | 150896 | 150896 | 0 | 150896 | 0 | n=150896, min=0.3, p50=20.7, p90=34.2, p95=38.8, p99=58.5, max=124.5 |
| `/Users/deepsleep/projects/pm_agents/runtime/analysis_inputs/forecast_update_time_repricing_v0/sources.jsonl` | 71801 | 64171 | 64171 | 0 | 64128 | 43 | n=64171, min=0.3, p50=27.5, p90=55.6, p95=60.3, p99=68.7, max=1474.0 |

## Diff Examples

- `{"city": "Singapore", "diff_min": 1440.0, "line": 17868, "path": "/Users/deepsleep/projects/pm_agents/runtime/analysis_inputs/forecast_update_time_repricing_v0/sources.jsonl", "raw_ddhhmm_ts_utc": "2026-06-23T03:30:00Z", "raw_metar": "METAR WSSS 230330Z 18004KT 140V240 9999 FEW018 SCT280 30/25 Q1011 NOSIG", "source": "aviationweather_cache_csv", "source_report_ts_utc": "2026-06-24T03:30:00Z", "station": "WSSS"}`
- `{"city": "Singapore", "diff_min": 1440.0, "line": 17934, "path": "/Users/deepsleep/projects/pm_agents/runtime/analysis_inputs/forecast_update_time_repricing_v0/sources.jsonl", "raw_ddhhmm_ts_utc": "2026-06-23T03:30:00Z", "raw_metar": "METAR WSSS 230330Z 18004KT 140V240 9999 FEW018 SCT280 30/25 Q1011 NOSIG", "source": "aviationweather_cache_csv", "source_report_ts_utc": "2026-06-24T03:30:00Z", "station": "WSSS"}`
- `{"city": "Singapore", "diff_min": 1440.0, "line": 18000, "path": "/Users/deepsleep/projects/pm_agents/runtime/analysis_inputs/forecast_update_time_repricing_v0/sources.jsonl", "raw_ddhhmm_ts_utc": "2026-06-23T03:30:00Z", "raw_metar": "METAR WSSS 230330Z 18004KT 140V240 9999 FEW018 SCT280 30/25 Q1011 NOSIG", "source": "aviationweather_cache_csv", "source_report_ts_utc": "2026-06-24T03:30:00Z", "station": "WSSS"}`
- `{"city": "Singapore", "diff_min": 1440.0, "line": 18067, "path": "/Users/deepsleep/projects/pm_agents/runtime/analysis_inputs/forecast_update_time_repricing_v0/sources.jsonl", "raw_ddhhmm_ts_utc": "2026-06-23T03:30:00Z", "raw_metar": "METAR WSSS 230330Z 18004KT 140V240 9999 FEW018 SCT280 30/25 Q1011 NOSIG", "source": "aviationweather_cache_csv", "source_report_ts_utc": "2026-06-24T03:30:00Z", "station": "WSSS"}`
- `{"city": "Singapore", "diff_min": 1440.0, "line": 18133, "path": "/Users/deepsleep/projects/pm_agents/runtime/analysis_inputs/forecast_update_time_repricing_v0/sources.jsonl", "raw_ddhhmm_ts_utc": "2026-06-23T03:30:00Z", "raw_metar": "METAR WSSS 230330Z 18004KT 140V240 9999 FEW018 SCT280 30/25 Q1011 NOSIG", "source": "aviationweather_cache_csv", "source_report_ts_utc": "2026-06-24T03:30:00Z", "station": "WSSS"}`

## Notes

- The largest local datasets are normalized source-event histories, not original AviationWeather API JSON payload archives. They preserve `raw_metar`, `source_report_ts_utc`, and detect/fetch timestamps, but not the original raw `reportTime` field.
- Therefore this report is a contract/impact audit, not a direct proof that the live AviationWeather API currently emits detect-time `reportTime`.
- Follow-up fix, if approved, should centralize direct AWC JSON parsing on `parse_aviationweather_records` and keep `source_report_ts_utc` and detect/fetched timestamps explicit in caller outputs.
