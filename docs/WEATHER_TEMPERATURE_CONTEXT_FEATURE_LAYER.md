# Weather Temperature Context Feature Layer

Status: current-reference
Updated: 2026-07-14
Source of truth: yes for temperature-context feature semantics
Used by: current YES, current-bracket NO, d1/d2 NO, Range RV, timing research

## What This Is

`reheat_feature_factory_v1` is no longer just a reheat-risk table in practice. It is the shared intraday temperature state layer: one city/date/hour state with observed temperature path, market brackets, forecast peak context, humidity, dewpoint, sky, wind, and trend fields.

This document names the shared context layer that should sit on top of it:

```text
temperature state facts -> temperature context labels -> strategy-specific probability heads -> expression / execution
```

The context labels are not trading rules. They are reusable mechanism features for separate strategy heads:

- current YES survive
- current-bracket NO pass-through
- d1/d2 NO escape
- post-cross repricing
- Range RV / city-day distribution work
- timing and execution diagnostics

## Canonical Inputs

Canonical frame builder:

`weather_feature_layer.builders.build_weather_state_frame()`

Canonical frame/store contract:

`weather_state_v2` inside the existing `feature_frame_v1` envelope. Live and
archive reconstruction use the same columns and `feature_version_manifest`;
the PIT difference is recorded only in `pit_provenance`.

Shared feature functions:

`weather_data_feed/weather_context.py` and
`weather_data_feed/physical_features.py`, re-exported through
`weather_feature_layer.state`. Strategies import the feature layer, not private
copies of parsers.

Latest generated state table:

`docs/analysis/2026-06/generated/temperature_context_feature_layer_v1/temperature_context_state_rows.csv`

Latest dated evidence report:

`docs/analysis/2026-06/2026-06-26-temperature-context-feature-layer-v1.md`

Latest temperature-path decomposition:

`docs/analysis/2026-07/2026-07-05-temperature-path-mechanism-decomposition-v1.md`

## Feature Families

`weather_state_v2` adds continuous, strategy-neutral physical facts without a
new table or serialization format:

- observed METAR `precip_state`, intensity, thunder/freezing flags
- cloud layer count, lowest base, ceiling, and optional 1h changes
- wind direction plus circular sin/cos representation
- observation age/cadence ratio, next expected report, and source latency
- solar elevation now/+2h, elevation change, daylight remaining, heating potential
- forecast precipitation/cloud/wind summaries over decision-to-peak, read from
  the canonical PIT `forecast_hourly_curve_v4`

Missing inputs remain explicit through `solar_geometry_status`,
`forecast_weather_window_status`, and null continuous fields. There is no
silent city-coordinate, forecast, or observation fallback.

`temperature_path_state`

Turns recent observed temperature path into reusable context labels:

- `trend3h_bucket`: `cooling_lt_neg0_5`, `flat_abs_lt0_5`,
  `warming_0_5_to_2`, `strong_warming_ge2`
- `trend3h_warming_ge0_5`
- `sustained_warming_1h3h`
- `one_hour_warm_without_3h`
- `runway_sustained_warming`
- `solar_runway_sustained_warming`
- `late_reheat_after_dip`
- `exclude_trend3h_flat` for route-specific selector experiments only

`cloud_warming_interaction`

Combines sky cover and recent warming:

- `clear_solar_warming`
- `clear_but_not_warming`
- `warming_through_cloud`
- `cloud_limited_flat_or_cooling`
- `mixed_sky_warming`
- `mixed_sky_flat_or_cooling`
- `cloud_warming_unknown`

`moisture_cloud_interaction`

Combines humidity, dewpoint depression, and sky:

- `humid_cloud_suppression`
- `humid_convective_risk`
- `cloud_suppression`
- `dry_heat_inertia`
- `mixed_moisture_cloud`
- `moisture_cloud_unknown`

`marine_thermal_state`

Combines city geography, wind speed, and wind direction when available:

- `onshore_marine_cooling_risk`
- `offshore_or_parallel_warming_risk`
- `coastal_direction_unknown_mixing`
- `coastal_light_or_unclear_flow`
- `inland_wind_mixing`
- `marine_wind_unknown`

`forecast_peak_clock_state`

Turns forecast peak timing into reusable context:

- `forecast_peak_2h_plus_ahead`
- `forecast_peak_0_to_2h_ahead`
- `forecast_peak_passed_0_to_1h`
- `forecast_peak_passed_1_to_2h`
- `forecast_peak_passed_2h_plus`
- `forecast_peak_unknown`

`temperature_context_regime`

A composite string joining peak clock, warming state, cloud/warming, moisture/cloud, and marine/wind context. Use it for diagnostics and grouping, not as a live gate by itself.

## Current Evidence Snapshot

Latest v1 coverage:

- `9,800` city-date-hour state rows
- `36` cities
- `2026-05-19` through `2026-06-17`

Selected mechanism sanity checks:

| context | state rows | current YES win | current NO pass-through | d1 hit | d2 hit |
|---|---:|---:|---:|---:|---:|
| `clear_solar_warming` | 1,794 | 32.9% | 67.1% | 24.2% | 16.1% |
| `cloud_limited_flat_or_cooling` | 670 | 71.5% | 28.5% | 11.6% | 6.0% |
| `forecast_peak_2h_plus_ahead` | 3,453 | 13.6% | 86.4% | 21.9% | 22.8% |
| `forecast_peak_passed_2h_plus` | 2,787 | 87.9% | 12.1% | 3.4% | 0.8% |

These numbers are mechanism checks, not approval to trade. A strategy must still evaluate real ask, depth, settlement source, same-price baseline, holdout, and forward evidence.

Latest temperature-path decomposition v1 coverage:

- `13,725` atlas state rows, `12,972` labelled mechanism rows
- `36` cities
- atlas state rows `2026-05-19` through `2026-07-03`; labelled rows through `2026-07-02`
- `trend3h_warming_ge0_5`: future break `65.5%`, d1 hit `22.6%`
- `sustained_warming_1h3h`: future break `77.7%`, d1 hit `22.8%`
- `one_hour_warm_without_3h`: future break `17.1%`, d1 hit `9.4%`

These labels are strong physical context features. They are not standalone live
gates; HeadB, tmax distribution, regime-routed NO, and current-YES heads must
still evaluate expression price, fresh-book depth, and forward settlement.

## Boundary

- This layer is point-in-time context only. Do not fill missing live fields from future archive data.
- Missing wind direction must remain explicit: `flow_unknown` / `coastal_direction_unknown_mixing`.
- Current full-history feature rows mostly have wind speed, not wind direction. Live/shadow runners are now starting to record wind direction for forward replay.
- Hard filters should not be created directly from these labels unless they represent a real mechanism boundary or data-quality constraint.

## Next Steps

1. Keep old generated atlas paths as historical artifacts; new studies consume
   the shared frame/store or reference it through `feature_frame_ref`.
2. Accumulate forward `weather_state_v2` coverage before fitting a residual
   model; do not backfill absent weather fields with future source data.
3. Test `market probability + weather residual correction` on a frozen forward
   split and promote only if it beats raw market proper score and fee-adjusted
   executable ROI.
