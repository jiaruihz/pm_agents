# Current-YES future-break observation precision v1

Created: 2026-06-18T16:36:35+00:00

## Target

Research-only.  Rename the bad event from narrow `reheat` to `future_break`: after the current running max is visible, does a later official observation print a higher bracket?  This can be ordinary continued warming, a forecast peak that was too low, or a true late re-warm.

## Data self-check

- `fact_trades` max built at: `2026-06-17T17:09:13.232107+00:00`
- `fact_signal_candidates`: `{'rows': 31499, 'eligible': 10961, 'paper_ordered': 4274, 'live_filled': 348}`
- CLOB order/fill join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`

## Sample funnel

| slice | rows | city_days | dates | future_break_rate | median_obs_age_min | p90_obs_age_min |
|---|---:|---:|---:|---:|---:|---:|
| all_reheat_factory_states | 11512 | 914 | 27 | 43.2% | 30.88 | 40.88 |
| current_yes_like_states | 9619 | 879 | 27 | 39.6% | 30.88 | 40.88 |
| fade_like_current_yes_states | 3038 | 745 | 26 | 10.6% | 30.88 | 40.88 |
| fade_like_celsius_markets | 2446 | 570 | 25 | 9.6% | 30.88 | 40.88 |
| fade_like_fahrenheit_markets | 592 | 175 | 26 | 14.4% | 37.88 | 39.88 |

## Observation precision by market unit

| market_unit | stations | obs_rows | integer_temp_share | median_obs_per_day | median_gap_min | gap<=35m_share | gap>65m_share |
|---|---:|---:|---:|---:|---:|---:|---:|
| C | 26 | 804942 | 100.0% | 48.00 | 30.00 | 79.2% | 0.2% |
| F | 10 | 213590 | 93.0% | 25.50 | 60.00 | 17.0% | 0.2% |

## Future-break feature bins

| feature | bucket | rows | future_break_rate | avg_current_yes_ask | median_obs_age_min |
|---|---:|---:|---:|---:|---:|
| decision_obs_age_min | <=5m | 494 | 4.5% | 0.934 | 0.88 |
| decision_obs_age_min | 5-15m | 283 | 7.1% | 0.930 | 9.88 |
| decision_obs_age_min | 15-30m | 1 | 0.0% | 0.996 | 19.85 |
| decision_obs_age_min | 30-45m | 2253 | 12.3% | 0.874 | 30.88 |
| decision_obs_age_min | 45-65m | 5 | 40.0% | 0.638 | 50.08 |
| minutes_since_running_max | <15m | 133 | 49.6% | 0.560 | 31.88 |
| minutes_since_running_max | 15-30m | 3 | 33.3% | 0.633 | 37.88 |
| minutes_since_running_max | 30-60m | 79 | 16.5% | 0.859 | 5.88 |
| minutes_since_running_max | 60-120m | 760 | 9.3% | 0.905 | 30.88 |
| minutes_since_running_max | 120m+ | 1926 | 8.7% | 0.901 | 30.88 |
| gfs_peak_clock_delta | forecast_peak_future>1h | 484 | 52.1% | 0.503 | 30.88 |
| gfs_peak_clock_delta | future0-1h | 237 | 13.1% | 0.865 | 30.88 |
| gfs_peak_clock_delta | now_or_past0-1h | 374 | 2.7% | 0.949 | 30.88 |
| gfs_peak_clock_delta | past1-2h | 498 | 1.6% | 0.973 | 30.88 |
| gfs_peak_clock_delta | past2h+ | 1402 | 1.1% | 0.979 | 30.88 |
| temp_trend_1h_f | rise>2F | 83 | 42.2% | 0.564 | 30.88 |
| temp_trend_1h_f | rise0.5-2F | 254 | 30.7% | 0.707 | 30.88 |
| temp_trend_1h_f | flat | 1086 | 10.0% | 0.887 | 30.88 |
| temp_trend_1h_f | fall0.5-2F | 1178 | 6.8% | 0.925 | 30.88 |

## Readout

- The right target is not strictly `reheat`; it is `future_break`.  A loss can happen because the day was still climbing, because the forecast peak was low, or because a late secondary warm-up occurred.
- The observation layer is good enough to support a probabilistic model, but not a deterministic one-degree call.  The public METAR body is whole-degree Celsius while the sensor/remarks layer can be tenths; for 1C brackets, the rounding boundary is material.
- Cadence is modelable but not free.  A decision made just after a stale or just-crossed observation has much higher uncertainty than one made after the peak clock has passed and the temperature has been flat/falling for a while.
- Practical implication: live signals should log both `p_hold` and `p_future_break`, plus observation age, station cadence class, and forecast peak clock.  Use these as a risk overlay/veto before trying a standalone specialist model.

## External standard notes

- FMH-1: temperature is observed to nearest tenth Celsius, but METAR body reporting resolution is whole Celsius; remarks can carry tenths at designated stations.
- ASOS User Guide: ASOS computes short averaged temperature values frequently; local dissemination/cache may still expose only METAR cadence.
- NCEI: ASOS stations operate continuously and archives include one-/five-minute, hourly, daily summary and special observations.

## Artifacts

- `sample_funnel.csv`
- `observation_precision_by_station.csv`
- `observation_precision_by_unit.csv`
- `future_break_feature_bins.csv`
