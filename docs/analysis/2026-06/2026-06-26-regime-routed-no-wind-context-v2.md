# Regime-Routed NO Wind Context V2

Generated: `2026-06-26T12:36:13+00:00`

## Verdict

This is a research-only wind-context test.  It adds point-in-time IEM wind direction, a hand-labeled city geography class, and a coastal onshore/offshore approximation to the existing regime-routed NO denominator.

Main read: a full wind model is scientifically cleaner than raw wind speed, but the current evidence still supports only telemetry/shadow sizing.  The direction/geography labels are first-principles approximations, not a confirmed edge.

Coverage: `243` / `271` rows matched an as-of IEM wind direction.

## Policy Summary

| weight_policy | rows | dates | cities | exec_rows | exec_dates | cost_usd | pnl_usd | roi | exec_cost_usd | exec_pnl_usd | exec_roi | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_size | 271 | 35 | 35 | 271 | 35 | +1355.00 | +155.73 | +11.5% | +1355.00 | +155.73 | +11.5% | +100.0% |
| soft_balanced | 271 | 35 | 35 | 77 | 31 | +469.32 | +123.21 | +26.3% | +212.38 | +148.45 | +69.9% | +34.6% |
| soft_wind_only | 271 | 35 | 35 | 76 | 31 | +457.29 | +119.05 | +26.0% | +204.88 | +146.14 | +71.3% | +33.7% |
| soft_wind_context | 271 | 35 | 35 | 75 | 31 | +453.50 | +117.24 | +25.9% | +201.35 | +146.92 | +73.0% | +33.5% |

## Context Slices

| group_col | group_value | rows | dates | cities | exec_rows | cost_usd | pnl_usd | roi | exec_roi | hit_rate | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| city_family | humid_low_latitude | 103 | 34 | 13 | 29 | +167.49 | +25.09 | +15.0% | +31.8% | +53.4% | +32.5% |
| city_family | southern_or_maritime | 78 | 30 | 10 | 25 | +145.18 | +27.55 | +19.0% | +67.6% | +44.9% | +37.2% |
| city_family | continental_dry_hot | 63 | 30 | 8 | 15 | +100.68 | +54.72 | +54.3% | +161.9% | +55.6% | +32.0% |
| city_family | europe_cloud_break | 27 | 20 | 4 | 6 | +40.16 | +9.88 | +24.6% | +69.8% | +55.6% | +29.8% |
| coastal_flow_state | onshore_marine_flow | 89 | 33 | 20 | 23 | +143.21 | +44.36 | +31.0% | +77.2% | +58.4% | +32.2% |
| coastal_flow_state | flow_unknown | 71 | 31 | 18 | 21 | +125.53 | -28.16 | -22.4% | -17.9% | +38.0% | +35.4% |
| coastal_flow_state | non_coastal_flow | 64 | 29 | 13 | 19 | +107.78 | +50.73 | +47.1% | +116.9% | +53.1% | +33.7% |
| coastal_flow_state | offshore_or_parallel_flow | 47 | 28 | 15 | 12 | +76.98 | +50.30 | +65.4% | +158.9% | +57.4% | +32.8% |
| geo_context | coastal_humid | 76 | 30 | 9 | 21 | +120.93 | +31.34 | +25.9% | +63.5% | +53.9% | +31.8% |
| geo_context | coastal_marine | 69 | 28 | 10 | 19 | +123.00 | +56.86 | +46.2% | +126.3% | +53.6% | +35.7% |
| geo_context | coastal_desert | 44 | 28 | 3 | 11 | +68.19 | -14.57 | -21.4% | -26.0% | +43.2% | +31.0% |
| geo_context | inland_plain | 32 | 21 | 5 | 13 | +58.73 | +45.26 | +77.1% | +156.2% | +53.1% | +36.7% |
| geo_context | inland_humid | 15 | 14 | 2 | 7 | +33.11 | -5.24 | -15.8% | -29.2% | +46.7% | +44.2% |
| geo_context | basin_inland | 12 | 12 | 2 | 1 | +13.45 | -1.02 | -7.6% | -100.0% | +58.3% | +22.4% |
| geo_context | inland_alpine_edge | 10 | 10 | 1 | 0 | +11.91 | +0.35 | +3.0% | NA | +60.0% | +23.8% |
| geo_context | unknown_geo | 6 | 6 | 1 | 1 | +9.71 | +0.48 | +5.0% | +222.6% | +50.0% | +32.4% |
| wind_sector | unknown | 71 | 31 | 18 | 21 | +125.53 | -28.16 | -22.4% | -17.9% | +38.0% | +35.4% |
| wind_sector | W | 57 | 28 | 17 | 13 | +90.52 | +42.42 | +46.9% | +157.2% | +54.4% | +31.8% |
| wind_sector | S | 34 | 23 | 17 | 10 | +58.68 | +22.47 | +38.3% | +85.4% | +61.8% | +34.5% |
| wind_sector | NE | 22 | 18 | 11 | 5 | +37.07 | -6.17 | -16.7% | -35.3% | +50.0% | +33.7% |
| wind_sector | E | 19 | 13 | 13 | 6 | +28.05 | +2.66 | +9.5% | +57.5% | +42.1% | +29.5% |
| wind_sector | NW | 19 | 17 | 13 | 3 | +27.87 | +7.98 | +28.6% | +180.0% | +47.4% | +29.3% |
| wind_sector | SE | 18 | 15 | 13 | 7 | +32.21 | +19.46 | +60.4% | +81.2% | +55.6% | +35.8% |
| wind_sector | SW | 18 | 12 | 12 | 6 | +33.32 | +24.17 | +72.5% | +135.5% | +66.7% | +37.0% |

## Boundary

- Static onshore sectors are hand-labeled mechanism approximations.  They now live in `weather_data_feed.weather_context` so research and runner telemetry share the same definitions.
- `soft_wind_context` is intentionally conservative and exploratory; it is not optimized and not live-approved.
- Runner integration should remain telemetry/shadow-only until frozen replay confirms that live-available wind direction coverage is stable.
