# Tmax Path-Market Fusion V2 Research Plan

Status: `current-plan`

Generated: `2026-07-11`

Scope: research and zero-notional shadow only. This plan does not change the active runner, live configuration, sizing, city pool, or order behavior.

## Bottom Line

Most of the proposed core weather features already exist somewhere in the repository, but they do not yet form one complete strategy model:

- path, peak clock, bracket boundary, humidity, dewpoint, wind speed, sky and regime are in the atlas/P3 probability stack;
- wind direction, coastal flow, cloud/warming and moisture/cloud interactions exist in the shared temperature-context layer and regime-routed work, but are not integrated into the Tmax probability head;
- multi-model forecast bias/spread and high-frequency observations exist mainly in research or telemetry, not in the trained probability model;
- full-ladder survival and position-aware target-book logic exist as separate research candidates, not as the current coherent execution model.

The clean next version is therefore not a larger list of regime labels. It is a single lineage:

```text
PIT weather/source state
  -> absolute settlement-ladder survival distribution
  -> market-residual calibration
  -> full-book expression EV
  -> position-aware target book
```

The alpha thesis is:

> Public market prices contain most public forecast information, but their conditional calibration can be improved using the realized temperature path, source reliability, forecast residuals, information arrival and physical mechanism context.

This is a market-calibration thesis, not a claim that one weather model globally beats the market.

## What Is Already Done

| Feature family | Current implementation | Current model use | V2 action |
|---|---|---|---|
| Local market distribution and quote geometry | P0/P3, coherent quote calibrator | Yes | Keep as the market prior; extend to full ladder, depth and quote age |
| Current temperature, running max, decline, 1h/3h trend, max freshness | Atlas/P3/live builder | Yes | Keep continuous; add event cadence, acceleration and path residual trend |
| Forecast max and peak clock | Atlas/P3/live builder | Yes, but some history uses backfill | Require forecast run time and as-of availability; add full hourly curve |
| Bracket boundary and fractional position | P3 | Yes | Retain only non-duplicative absolute-ladder geometry |
| Humidity, dewpoint depression, wind speed and sky | Atlas/P3 | Yes | Keep continuous; add change/trend and forecast-vs-observed mismatch |
| Regime labels | Atlas/shared regime layer | Yes | Keep as explanation/grouping; do not add more labels or gates |
| Wind direction, coastal flow and cloud/warming interactions | `weather_data_feed/weather_context.py` and regime-routed studies | Not in Tmax probability primary | Rebuild from continuous primitives and objective geography; labels stay diagnostic |
| City/source category | P3 comparison model | Excluded from current clean primary | Replace raw city memory with multi-axis groups and shrinkage |
| Multi-model forecast quality, bias and spread | July source studies | Research-only; incomplete PIT | Materialize per-run PIT features; use as residual/uncertainty, not direct source swap |
| High-frequency observation/source events | Data-feed and runner telemetry | Not in probability | Preserve immutable as-of event history and test source-speed value |
| Full-ladder monotone survival | Survival v2 research | Not promoted | Rebuild on absolute brackets and the V2 feature contract |
| Position-aware target book | Target-book research | Not active model behavior | Make it the only execution mapping after probability validation |

Two apparent coverage claims need to stay explicit:

1. A feature being present in an atlas CSV does not mean live can reproduce it as-of.
2. A source field being written to telemetry does not mean the probability model uses it.

## Model Contract

### Target

For each `city + target_date + decision_ts`, predict the final maximum on the event's absolute settlement ladder:

```text
P(Y = bracket_k | information available at decision_ts)
```

The ladder stays fixed throughout the city-day. `current/d1/d2/tail` become derived views, not the model coordinate system. This removes relative-bucket reanchoring and below-ladder mapping problems.

### Physical Distribution

For every still-reachable threshold `k`, estimate a conditional crossing hazard:

```text
h_k(t) = P(Y >= k | Y has not yet reached k, PIT state at t)
```

The hazards are converted into one coherent exact-bracket distribution. Mathematical constraints:

- probability mass sums to one;
- impossible brackets below the realized settlement-aligned maximum receive zero mass;
- cumulative crossing probability is non-increasing with threshold;
- all updates remain on the same absolute ladder.

The first implementation should use regularized logistic hazards with spline-transformed continuous features. This is easier to inspect and ablate than a large tree/NN model.

### Market Fusion

Maintain three distributions on the same rows:

```text
P_weather  = physical/path model
P_market   = normalized full-ladder market distribution
P_fusion   = market prior + regularized physical/source residual
```

The residual model must be trained on proper scoring, not selected-trade ROI. The existing coherent quote calibrator remains a second-stage execution calibration candidate.

### Execution Mapping

For every available YES/NO expression:

```text
net_edge_lcb = probability_lower_bound
             - fresh executable price
             - official fee
             - expected slippage
             - adverse-selection allowance
```

The target-book solver compares the desired net shares with the actual position. It emits only the difference. A posterior change is therefore a revaluation of the same city-day book, not a new independent opportunity.

## Feature Contract V2

Every feature row needs:

```text
value
source_system
source_report_ts_utc
first_seen_at_utc
available_at_utc
missing_reason
feature_version
```

`available_at_utc <= decision_ts` is mandatory for model inputs. Historical archive retrieval time must not be presented as historical first-seen time.

### Required Now

- identity: city, target date, station/feed, settlement source, unit, decision timestamp;
- realized path: current, running max, running-max time, age, trends, decline, observation count/cadence;
- forecast: assigned source/model, model run time, forecast age, full hourly temperature curve, peak clock;
- meteo: dewpoint, RH, wind speed/direction, sky/cloud and their recent changes when available;
- market: complete ladder, direct bid/ask, size/depth, quote timestamp, spread and overround;
- model lineage: base probability version, calibrator version and feature artifact version.

### Collect Forward

- immutable high-frequency observation records with observation/report/detect timestamps;
- all forecast model runs rather than only latest forecast values;
- quote events around observation and forecast arrivals;
- station/source-basis estimate and version;
- pressure, gust, precipitation/cloud forecast, cloud change and forecast-observed mismatch;
- first-running-max time, plateau duration, pullback depth and repeated-high count;
- full-ladder depth and executable size at decision time.

### Research Only Until PIT Is Proven

- full-window best model or city bias;
- historical forecast fields reconstructed without an authentic run/as-of timestamp;
- future archive-filled wind/cloud/source fields;
- raw city one-hot performance effects;
- observed final maximum and settlement labels;
- outcome-derived slices such as actual overshoot or actual tail.

## Mechanism Grouping

Do not replace one crude `city_family` with another mutually exclusive label. Each city-state receives several independent axes.

### Dynamic Mechanism Axes

| Axis | Continuous primitives | Diagnostic states |
|---|---|---|
| Thermal stage | trends, acceleration, decline, max age, plateau duration | runway, peak-forming, plateau, pullback/reheat, mature fade |
| Remaining energy | hourly forecast curve above each threshold, peak clock, solar time | open, marginal, capped |
| Radiation/moisture | cloud/sky level and change, RH, dewpoint depression, precipitation risk | clear warming, cloud-limited, humid/convective, dry inertia |
| Flow/geography | wind direction/speed/change, coast-normal component, elevation/terrain | onshore cap, offshore warming, inland mixing, basin stagnation |
| Information reliability | observation age/cadence, source basis, HF-primary gap, forecast age/spread/bias | fresh-aligned, stale, source-conflicted, high-uncertainty |

The model consumes the continuous primitives. The named states are for reporting, interactions and error attribution.

### Static City Axes

The starting research panels are:

- coastal/marine: San Francisco, Seattle, Busan, Tokyo, Hong Kong, Singapore, Wellington, Cape Town;
- humid/convective or basin: Lucknow, Manila, Guangzhou, Wuhan, Chengdu, Chongqing, Kuala Lumpur;
- dry/continental/high elevation: Ankara, Madrid, Denver, Dallas, Austin;
- known forecast/source-bias diagnostics: Jeddah, Karachi, Lucknow, Munich, Ankara, San Francisco;
- low assigned-source-gap controls: Atlanta, LA, Miami, Houston, Shanghai, Taipei, Sao Paulo.

These are experiment panels, not allowlists. Final static features should be generated from station geography and source topology:

- latitude, longitude, elevation and hemisphere;
- distance/bearing to coast and coast-normal wind projection;
- terrain class: basin, plain, plateau, alpine edge;
- settlement source class and observation cadence class;
- assigned forecast model and regional-model availability.

### Hierarchical Shrinkage

Primary structure:

```text
global mechanism effect
  + climate/geography group deviation
  + source-topology deviation
  + forecast-reliability deviation
  + strongly shrunk city residual
```

The first implementation can use out-of-fold empirical-Bayes shrinkage instead of a large Bayesian stack:

```text
city_residual_shrunk
  = n_city / (n_city + lambda) * city_residual
  + lambda / (n_city + lambda) * parent_group_residual
```

`lambda` is selected on pre-cutoff proper scoring, never on selected-trade ROI. New or thin cities naturally fall back to their parent group. Compare global-only, unshrunk city and shrunk hierarchy on the same outer folds.

## Research Work Packages

### Block A: Canonical PIT State And Parity

Goal: one state builder shared by historical replay, shadow and live.

Actions:

1. Define the V2 feature schema and deterministic `tmax_state_id`.
2. Materialize complete ladder and source lineage into the canonical state/candidate layer, not another standalone CSV truth.
3. Preserve first-seen source and forecast-run records; prohibit `latest.json` from historical reconstruction.
4. Run historical-vs-live feature parity on overlapping timestamps.
5. Produce coverage by field, city, source and date.

Completion evidence:

- identical shared builder and artifact version in replay/shadow/live;
- no unknown backfill origin in required features;
- complete selected and blocked candidate payloads;
- explicit forward-only fields where history cannot be recovered.

### Block B: Absolute-Ladder Physical Baseline

Goal: establish whether the clean hazard structure improves probability quality before adding new alpha features.

Models on one denominator:

1. market full-ladder baseline;
2. current `coherent_cal_quote` baseline;
3. absolute-ladder weather hazard, global only;
4. market + global hazard residual.

Primary metrics:

- date-equal exact-bracket logloss;
- Brier score and calibration curve;
- crossing-hazard monotonicity and cross-time stability.

Kill condition: the new structure cannot beat the current coherent probability model on untouched outer folds, or gains exist only in outcome-defined slices.

### Block C: Mechanism And Hierarchy

Goal: add mechanism information without memorizing cities.

Same-denominator variants:

1. global primitives only;
2. global + five mechanism axes;
3. global + mechanism + group deviations;
4. global + mechanism + empirical-Bayes city/source residual;
5. unshrunk city one-hot as an overfit control, never as primary.

Required robustness:

- leave-one-city-out;
- leave-one-mechanism-family-out;
- leave-one-source-class-out;
- date-block bootstrap and fold-by-fold sign stability.

Kill condition: the shrunk version does not beat global-only, or behaves like unshrunk city memory in unseen families.

### Block D: Forecast Residual And Multi-Source Information

Goal: test whether the edge comes from correcting public forecast anchors rather than globally replacing GFS/ECMWF.

Features:

- rolling signed assigned-model error using only prior target dates;
- forecast error dispersion and uncertainty;
- model consensus spread/skew;
- regional-model availability and disagreement;
- today's realized path minus the assigned hourly forecast curve and its trend.

Experiments:

1. assigned model unchanged;
2. rolling bias residual only;
3. model spread/uncertainty only;
4. bias + spread;
5. direct best-model swap as a negative/control arm.

Falsification: public-private divergence does not produce monotonic residual calibration improvement, or the result disappears in leave-family-out/true PIT forward.

### Block E: Source-Speed And Market Reaction

Goal: determine whether fast observations create alpha or reveal that specialists already moved the book.

Event-study timeline:

```text
source report -> first detected -> market repricing -> hypothetical fill at 0/5/10/20 minutes -> settlement
```

Compare:

- primary observation state;
- high-frequency augmented state;
- stale-primary/fast-source disagreement;
- same-station fast source vs proxy-source fast observation.

Primary outcomes:

- bucket logloss and d1/d2 crossing error;
- quote reaction size and latency;
- fee-adjusted executable EV after realistic delays.

Falsification: the market reprices before our executable timestamp, or fast-source augmentation does not improve settled probability quality.

### Block F: Wind, Cloud And Solar Mechanisms

Goal: turn the existing coarse weather context into transferable physical interactions.

Pre-registered hypotheses:

1. coast-normal onshore wind change lowers remaining crossing hazard in coastal groups;
2. offshore/parallel flow preserves warming conditional on solar energy;
3. observed clearing relative to forecast raises the crossing hazard;
4. cloud persistence plus flat/cooling path lowers it;
5. morning observed-minus-forecast residual persists into final Tmax.

Use continuous inputs and spline/interactions. Existing categorical labels remain diagnostics. A mechanism survives only if the direction generalizes in leave-city-out tests; a single profitable city is not evidence.

### Block G: Market Residual, Timing And Target Book

Goal: convert probability skill into executable EV without hindsight timing.

Actions:

1. Decompose the gain from physics, quote geometry and their interaction.
2. Evaluate against bid/ask probability envelopes, not only normalized mid.
3. Compare first-lock with event-driven updates using only contemporaneous fresh books.
4. Reconcile desired and actual net positions on the complete event ladder.
5. Charge opening/closing spread, official fee, slippage and realistic depth.
6. Record turnover, reversal count, adverse-selection cost and city-day net exposure.

Sizing remains fixed-share for live evidence. A probability/uncertainty sizing ledger can run in shadow only:

```text
shadow_size proportional to positive lower-confidence-bound EV / payoff variance
```

No global daily cap may select cities merely by time-zone order; portfolio limits must be applied to correlated city-day risk after ranking all contemporaneous opportunities.

## Validation Design

### Data Universes

- weather-state universe: all eligible city-time states, not selected trades;
- market universe: all full-ladder snapshots with valid as-of lineage;
- execution universe: all selected and blocked opportunities with fresh executable quotes;
- realized universe: actual fills, evaluated separately through canonical facts.

### Nested Walk-Forward

```text
outer calendar block: untouched evaluation
inner expanding dates: feature/model/regularization selection
final frozen forward: never inspected during model design
```

All bootstrap and uncertainty calculations cluster by target date. City and hour rows on the same date are not treated as independent evidence.

### Selection Discipline

- primary model selected by proper scoring on all state rows;
- trade policy evaluated only after the probability model is frozen;
- same candidate denominator and expression set for all policy comparisons;
- YES/NO and each expression reported separately;
- multiple mechanism hypotheses reported with FDR or a frozen primary ordering;
- no post-hoc city whitelist, bad-day gate or ask/edge sweep may be promoted;
- required fresh-forward duration is set by a power calculation from date-level variance, not an arbitrary number of days.

### Promotion Sequence

```text
feature contract parity
  -> probability score improvement
  -> full-ladder calibration
  -> executable target-book replay
  -> zero-notional forward shadow
  -> tiny-live review through the normal deploy contract
```

Passing an earlier stage does not imply permission for a later stage.

## Ordered Execution

### First Wave

Run in parallel where possible:

1. Block A feature/PIT contract and coverage audit;
2. static city/source/geography profile v2;
3. immutable multi-model forecast and high-frequency source-event materialization.

### Second Wave

1. Block B absolute-ladder baseline;
2. Block C mechanism/hierarchy comparison;
3. Block D rolling forecast residual and model-spread ablation.

### Third Wave

1. Block E source-speed event study;
2. Block F wind/cloud/solar leave-city-out tests;
3. Block G fresh-book target-book replay.

### Forward Wave

Freeze one primary model and one execution policy. Dual-write all probabilities, selected/blocked reasons, desired/actual position and counterfactual sizes. Do not change live behavior during evidence accumulation.

## Immediate Deliverables

1. `tmax_feature_contract_v2`: machine-readable field/source/PIT requirements.
2. `city_mechanism_profiles_v2`: multi-axis geography, source and forecast topology; no allowlist semantics.
3. shared V2 state builder with research/shadow/live parity tests.
4. one experiment runner producing global, mechanism, hierarchy and source ablations on identical outer folds.
5. one target-book replay consuming frozen probabilities and fresh quote/depth inputs.
6. one synthesis report with proper score, fee-adjusted execution, daily stability, branch mix and failure attribution.

## Current Verdict

```text
existing_feature_base = useful_but_fragmented
new_model_direction = absolute_ladder_hazard_plus_market_residual
city_handling = mechanism_groups_plus_hierarchical_shrinkage
new_alpha_priority = forecast_residual_then_source_speed_then_physical_interactions
live_action = none_from_this_plan
```
