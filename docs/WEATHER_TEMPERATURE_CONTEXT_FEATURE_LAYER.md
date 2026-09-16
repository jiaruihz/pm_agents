# Weather Temperature Context Feature Layer

Status: current-reference
Updated: 2026-07-28 first-seen checkpoint lineage pointer
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

`weather_state_v4` inside the existing `feature_frame_v1` envelope. Live and
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

`weather_state_v4` adds continuous, strategy-neutral physical facts without a
new table or serialization format:

- observed METAR `precip_state`, intensity, thunder/freezing flags
- cloud layer count, lowest base, ceiling, and optional 1h changes
- wind direction plus circular sin/cos representation
- observation age/cadence ratio, next expected report, and source latency
- solar elevation now/+2h, elevation change, daylight remaining, heating potential
- forecast precipitation/cloud/wind summaries over decision-to-peak; legacy
  `forecast_*_remaining_3h` retains its current-floor-hour contract, while
  strict post-decision weather uses `forecast_*_future_3h`, both read from the
  canonical PIT `forecast_hourly_curve_v4`
- equal-high-safe path clocks: first/last running-max observation,
  minutes since the last strict new high, and same-max observation count;
  legacy `minutes_since_running_max` remains the last-equal-high clock
- current clear-sky and precipitation-free regime duration, temperature change,
  and left-censor flags; these express “conditions improved but no new high”
  without a rain/cloud hard gate
- 1h/3h dewpoint tendency and circular 1h wind-direction change
- forecast temperature at decision time, future-only remaining maximum,
  remaining peak clock, and remaining gap to current/running temperature; an
  already-passed daily forecast maximum is not treated as future runway
- PIT TAF transition windows (`FM/BECMG/TEMPO/PROB30/40`) plus observed
  precipitation/thunderstorm onset clocks. The frame records whether the
  transition is not due, pending, early, in-window, late, or overdue, and keeps
  occurrence hazard, timing uncertainty, temperature-impact weight and
  transition risk as separate continuous fields. TAF lineage is explicit in
  `taf_issue_time_utc`, `taf_first_seen_utc`, `taf_valid_from_utc`,
  `taf_valid_to_utc`, `taf_issue_age_minutes`, `taf_capture_age_minutes` and
  `taf_validity_state`. A fresh collector capture does not refresh the TAF's
  issue clock: an expired TAF becomes `taf_expired` coverage with null risk,
  while a newly issued next-cycle TAF is labeled `issued_future_validity`.
  Missing or expired TAF is coverage, not a strategy rejection.

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

`heating_exhaustion_index_v2`

An interpretable diagnostic index, not a calibrated probability. It is the
unweighted mean of the available continuous 0..1 components for peak clock,
future forecast runway, observed temperature path, strict-high age, declining
solar potential, and clear-sky time without a strict new high. Component values
are stored separately. `heating_done_bucket_v2` is only a display/grouping
label; strategies must not turn the component count or bucket into a stack of
eligibility gates.

The v2 index does not silently substitute the daily forecast maximum when the
future-only curve is missing, and does not substitute legacy
`minutes_since_running_max` when the strict-high clock is missing. The relevant
component is omitted and an explicit `*_status_v2` field records the gap.

The old `heating_done_score_v1` remains for replay compatibility. It uses the
legacy last-equal-high clock and daily forecast maximum, so it must not be used
to interpret a mature equal-high plateau.

Compatibility is field-level, not only schema-level:

- legacy `d_tmpf_1h/3h` remain fetch-time anchored; the corrected meteorological
  path is published as `temp_trend_report_anchored_1h_f/3h_f`;
- legacy `forecast_*_remaining_3h` retain the historical current-floor-hour
  window; strictly future weather is published as `forecast_*_future_3h`;
- v1 continues to read only legacy fields, while v2 prefers the explicit new
  fields. A `weather_state_v4` frame therefore gets a new lineage ID, but does
  not silently alter v1 selector inputs.

`weather_state_v4` is additive over v3. It also fixes a data-layer parsing bug:
`PROB30 TEMPO` and `PROB40 TEMPO` are one probability-bearing change group,
not two independent segments. Historical immutable raw TAF captures may be
reparsed PIT with the fixed parser; the resulting coverage status is
`ok_reparsed_raw_taf`. Existing H1/H2 live eligibility remains unchanged.

The 2026-05-19..2026-06-17 clock-only ablation used 6,493 states across 36
cities and local hours 13..20. Replacing the v1 clock globally improved AUC on
the 1,561 equal-high target states from `0.7781` to `0.7836`, but reduced AUC on
the 4,932-state complement from `0.9287` to `0.9258` and overall from `0.9003`
to `0.8960`. This is why strict-high age is additive v2 evidence rather than a
silent replacement inside v1. A separate compatibility replay found zero v1
score changes across all 9,800 historical states and zero legacy forecast-field
changes across 2,000 curve/decision comparisons.

`weather_regime_v2` follows the same boundary. Legacy `running_max_state`,
`intraday_state`, `solar_window`, and `moisture_cloud_regime` remain unchanged
for existing selectors. New consumers use:

- `running_max_state_v2` / `intraday_state_v2`: strict-high clock and
  report-anchored temperature trend; equal highs do not become fresh highs;
- `solar_phase_v2`: actual solar geometry (`rising`, `declining`, or below
  horizon), not a fixed local-hour proxy;
- `moisture_cloud_regime_v2`: humidity alone is
  `humid_clear_or_mixed`, not evidence of convection;
- `composite_regime_v2`: composes only the corrected labels above.

On the same 6,493-state historical denominator, v2 changes 3,700 running-state
labels and 2,122 intraday-state labels. The principal intended migrations are
1,555 legacy `fresh_running_high -> equal_high_plateau` rows and 1,178 legacy
`fresh_high -> equal_high_plateau` rows. These are new diagnostic columns; the
legacy columns and live selectors that consume them are unchanged.

## Process Taxonomy And State-Transition Card

The external ten-process framing reviewed on 2026-07-21—solar mixing, persistent
low cloud/fog, clearing rebound, persistent rain, convective cold pool, sea
breeze, fronts/advection, foehn/downslopes, basin inversion and land-surface
control—is not a replacement for `day_regime` or `intraday_state`. It is a
human-facing dynamic-process taxonomy over the existing continuous axes.

The mapping is:

```text
site baseline axes
  + thermal stage / observed path
  + remaining energy / peak clock
  + radiation / moisture
  + flow / geography
  + information reliability
  -> diagnostic process probabilities
  -> next-state probabilities + transition-time distribution
```

These process labels are multi-label and time-varying. A coastal day may move
from `persistent_low_cloud` to `clearing_rebound` to `sea_breeze_cap`; it must
not be forced into one permanent city/day class. The model consumes continuous
facts. Named processes are for reporting, interactions and error attribution,
not direct YES/NO routes.

The material gap relative to the current regime layer is the future transition
state:

- `next_state_probs`
- `transition_window_local`
- `transition_time_quantiles`
- `stable_or_transition`
- supporting, conflicting and missing source evidence

METAR/SPECI mainly anchors the observed state. TAF, forecast curves, radar,
satellite, lightning and upstream stations may support a transition outlook,
but only when their immutable PIT snapshots and first-seen timestamps exist.
Missing historical radar/TAF/satellite evidence remains missing; it is not
backfilled from a later web page.

The proposed `intraday_state_transition_card_v0` is a versioned derived record
at `city + target_date + decision_ts`. It references the canonical
`feature_frame_ref`, input hashes, feature versions and source availability.
It records the full city-day checkpoint denominator, including
`observed/selected/blocked`, rather than only states later judged tradable.

For event-driven collection, the canonical checkpoint grain adds the triggering
information event:

```text
city + target_date + trigger_event_id + as_of_ts_utc
```

The feature store remains unchanged; the checkpoint is only a canonical index
to `feature_frame_ref`, and its feature-row key includes `trigger_event_id`.
Event identity, first-seen clocks, PIT evidence classes and candidate linkage
are defined in
[WEATHER_FIRST_SEEN_INFORMATION_LINEAGE.md](WEATHER_FIRST_SEEN_INFORMATION_LINEAGE.md).

An LLM may synthesize that card after deterministic parsing and feature
construction, before a strategy probability head. It may summarize source
conflicts, scenarios and invalidation signals; it must not overwrite facts,
invent missing evidence, emit an uncalibrated `p_win` as strategy truth, or
place orders. Exact-bracket probabilities and expression edges shown in the
final human report are injected later by the calibrated model and execution
evaluator; they are not authored by the LLM. Persist `prompt_version`,
`llm_model_version`, input hashes and the structured output so the contribution
can be ablated against deterministic transition features on identical rows.

The recommended capture cadence is `morning_map`, `state_confirmed`,
`pre_transition_or_key_report` and `post_update/end_of_day`, plus event-driven
cards after material observation, forecast or source-conflict changes. One
static daily narrative is not enough for transition-timed research.

Full review, card schema and acceptance design:
[2026-07-21-intraday-state-transition-agent-review-v1.md](analysis/2026-07/2026-07-21-intraday-state-transition-agent-review-v1.md).

PIT visibility is enforced inside the shared builder, not delegated to a
caller: observations with known `available_at/first_seen/detect/fetched/ingest`
later than `as_of_ts_utc` are excluded. Observation path functions also reject
reports or ingests later than their `as_of`. Forecast remaining-path features
ignore hourly points whose local date differs from `target_date`.

If a live observation refresh fails, the previous facts may remain visible for
diagnosis, but the cache row becomes `reused_after_fetch_error`, keeps its last
successful fetch timestamp, and recomputes age/clock fields. It must never
remain `status=ok` while silently stale.

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

## Settlement-Aligned Interpretation

Weather context is not comparable with a market bracket until the observation
has been transformed onto the settlement source's native-unit lattice. Keep the
raw source value/unit and the settlement-native value/bucket separately. A
decimal Celsius reference source must not be compared directly with a WU
native-F exact bracket.

The Ankara 2026-07-20 case fixes the interpretation of several existing feature
families:

- a one-step `+0.1°C` move is weak positive path evidence, neither confirmed
  reheat nor automatically noise;
- falling dewpoint with flat temperature means dry mixing occurred without a
  new high in that window, but falling dewpoint alone is not bearish;
- wind speed/direction needs station geometry, elevation, and upwind alignment;
  it is a continuous advection/mixing feature, not a direction hard gate;
- neighbor absolute temperature is low-confidence until station bias,
  elevation, and upwind weighting are applied;
- forecast peak clock supplies remaining-window prior; absolute model bias and
  forecast ceiling margin remain separate evidence.

These fields should update a continuous remaining-heat/exact-bracket
probability head. Do not turn them into `support>=N` or a multi-condition AND
eligibility funnel. The frozen Ankara evidence and reliability assessment are
in [WEATHER_INTRADAY_DECISION_CASEBOOK.md](WEATHER_INTRADAY_DECISION_CASEBOOK.md).

### Observation-minus-model innovation

The shared physical layer also emits
`forecast_temperature_innovation_f/native = observed_temperature -
forecast_temperature_at_decision`. The forecast temperature is linearly
interpolated to the true local decision minute; it is not the prior whole-hour
point. Missing observations or forecast curves remain explicit in
`forecast_temperature_innovation_status`.

This is a raw PIT feature, not an adjusted Tmax and not an eligibility gate.
The persistence coefficient β belongs to each frozen probability model. The
2026-07-28 checkpoint study found no reliable incremental value at 03:00/06:00
local, but useful signal at 09:00 and stronger signal at 12:00; therefore
consumers should retain the clock interaction rather than apply a fixed
overnight correction.

## Boundary

- This layer is point-in-time context only. Do not fill missing live fields from future archive data.
- Missing wind direction must remain explicit: `flow_unknown` / `coastal_direction_unknown_mixing`.
- `marine_thermal_state` is a geographic prior, not causal proof. Use current
  and prior direction plus continuous wind fields; do not hard-filter on the
  categorical label.
- Clear-sky and precipitation-free duration can be left-censored by source
  history. Consumers must retain the censor flags instead of interpreting the
  visible duration as the true regime start.
- Current full-history feature rows mostly have wind speed, not wind direction. Live/shadow runners are now starting to record wind direction for forward replay.
- Hard filters should not be created directly from these labels unless they represent a real mechanism boundary or data-quality constraint.

## Next Steps

1. Keep old generated atlas paths as historical artifacts; new studies consume
   the shared frame/store or reference it through `feature_frame_ref`.
2. Accumulate forward `weather_state_v4` coverage before fitting a residual
   model; do not backfill absent weather fields with future source data.
3. Test `market probability + weather residual correction` on a frozen forward
   split and promote only if it beats raw market proper score and fee-adjusted
   executable ROI.
4. The zero-notional `current_yes_heat_death_shadow_v1` collector records the
   full same-day denominator and compares executable current-bracket YES with
   d1 NO inside a pre-registered 13:00-17:00 local research window. Physical
   confirmation is telemetry until forward outcomes support calibration.
