# Reheat Risk Model

Status: current-reference
Created: 2026-06-16

## 2026-07-27 current correction

This file remains the family-level entry point, but most dated results below
describe the June research state and must not be used as current model-selection
evidence on their own.

Current decision:

- the active current-YES challenger is the frozen compact core carry; its live
  probe status and current sizing must be read from
  `WEATHER_STRATEGY_REGISTRY.md` and current runtime evidence;
- the June binary hazard, regime hazard and first-principles survival reports
  are historical experiments. Later fixed-denominator reruns corrected
  forecast lineage, city-day weighting, native settlement lattice, strict-high
  clocks, official fees and longer out-of-period coverage;
- after those corrections, hazard/remaining-heat/path/regime overlays did not
  stably improve proper score over market or the frozen core. They are not
  approved as filters, sizing overlays or a live V4;
- `hazard` remains a valid probability representation, not established alpha.
  A new hazard formula using the same information is only a
  reparameterization.

Current corrective references:

- `2026-07/2026-07-21-heat-death-overshoot-edge-strategy-v2.md`
- `2026-07/2026-07-22-current-yes-carry-mechanism-timing-audit-v1.md`
- `2026-07/2026-07-25-d1-bounded-reheat-overshoot-v2.md`
- `2026-07/2026-07-27-current-yes-core-carry-overshoot-missing-mechanisms-v2.md`

### 2026-08-07 Core Carry sample semantic audit

The corrected LLM audit is a per-checkpoint feature-alignment study, not a
selected-versus-rejected strategy comparison. The expanded preregistered audit
completed 120 weather-only PIT cards covering 40 cities and 110 city-days:
64 aligned with the sign of `Core p - market`, 48 had semantic tension and 8
were ambiguous. Production v3 only uses market logit, local hour, dewpoint
depression and wind speed; all 120 cards had available observation-transition
evidence that v3 does not consume. The clearest specification problem remains
unconditional wind speed: 93/120 cards were `mixing_only`, while speed alone
cannot distinguish maintenance from warming or cooling transport.

This is a confirmed feature-expression gap, not confirmed alpha. Among 60
settled cards, LLM `upward_exit` was correct only 6/14 times, while six actual
upward exits were called hold/fade because a forecast cap dominated despite
the cap transition not yet appearing in observations. Therefore LLM output is
not a veto. The specified next challenger is a same-denominator
`transition-confirmation residual`: settlement-native next-bracket margin,
fresh-high/rebound path, observed confirmation of forecast cloud/rain/cooling,
forecast innovation, remaining heat and directional transport role. Full report:
`2026-08/2026-08-07-core-carry-llm-transition-card-v1.md`.

### 2026-08-08 Core Carry full-ladder prior audit

The static complete exact-bracket ladder is not the missing Core Carry model
input.  A preregistered same-checkpoint audit resolved the exact archived book
named by each frozen parent row.  It scored 898/1,349 checkpoints across 29
target dates; the 451 coverage gaps were old snapshots that captured only two
or three local rungs, not missing files or later-quote substitutions.

On the historical eight-date secondary holdout, the Core-plus-ladder candidate
had small point improvements versus Core (Brier/logloss delta
`-0.000249/-0.002051`), but both date-block intervals crossed zero.  Development
OOF was worse, and the constrained ladder coefficients froze at exactly zero;
the secondary improvement came only from a calibration intercept.  The no-fit
feasible-ladder prior itself was worse than the raw current-rung midpoint.

The mechanism diagnosis is useful: already-impossible lower brackets carried
median market mass `0` (mean `0.000488`), while median full-ladder midpoint mass
was `1.0045`.  The market had already incorporated the observed running-max
constraint, so conditioning the static ladder did not reveal a new persistence
residual.  Reject this challenger, do not connect full-ladder shape to Core or
run an execution replay, and do not repair the failed coverage gate by
post-hoc loosening it to a three-rung model.  Full report:
`2026-08/2026-08-08-current-yes-core-carry-full-ladder-prior-v1.md`.

### 2026-08-09 Core Carry event-driven lifecycle audit

The earlier `+3c/share` post-entry capture idea is now rejected for the current
expression.  Replaying the current Core raw against the unified
`market_books/batches` source produced 34 settled, post-report executable
entries across 11 target dates: hold PnL was `+$15.37`, while the static exit
was `+$11.28`, a `-$4.09` delta with target-date CI `[-$7.47,-$1.36]`.
All 25 exits were final winners and no loss was saved.  The old positive point
estimate came from a narrower archived-book denominator and must not be used.

An independent market-free lifecycle head was also tested on the maintained
1,349-checkpoint actual-transport ledger.  The best development candidate
(shallow HGB using weather/path/forecast/transport only) remained worse than
Core and market on the last-eight secondary window, and both full-exit and
50%-reduce replay sold only eventual winners.  Only one of 82 selected
positions with a later checkpoint was a final loss, so post-entry loss evidence
is not sufficient to learn or validate an invalidation policy.  Keep the
current hold behavior; do not deploy profit capture, reduce, or weather exit.
The direction remains eligible only for zero-notional event-ledger collection.
Full report:
`2026-08/2026-08-09-current-yes-core-carry-event-lifecycle-v2.md`.

### Family synthesis after the July challenger sequence

The durable conclusion from the 80 dated current-YES reports is shorter than
their version history:

1. June `v9`/fade/peak-forming/hazard reports are historical lineage, not a
   current selector or live authorization.
2. `current_yes_core_carry_v2` is the frozen reference probability/expression
   for this family, but its historical positive 5-share replay did not by
   itself prove incremental probability alpha over market and does not make
   the strategy `confirmed`.
3. Overshoot sizing, semantic weather augmentation, synoptic/advection,
   late-plateau mixing and post-rebracket challengers all failed their
   same-denominator or frozen-forward promotion tests. Their mechanisms remain
   useful features/case labels; none is an approved hard filter, sizing overlay
   or replacement model.
4. Maker-first and larger taker sizing produced execution evidence, not model
   promotion. Queue, missed-fill and settled maker denominators remain distinct
   from taker replay.
5. New work should update a continuous exact-bracket posterior from market
   prior plus first-seen weather innovation, then compare on the same PIT
   checkpoints. Do not create another numbered strategy because one case was
   bad.

Current routing:

- model/strategy state and production-independent verdict:
  `WEATHER_STRATEGY_REGISTRY.md` rows for `current_yes_core_carry`;
- production process, notional and order state: production manifest + raw
  runtime + exchange evidence;
- physical semantics and failure cases:
  `WEATHER_INTRADAY_DECISION_CASEBOOK.md`;
- July challenger evidence:
  [v3 performance](2026-07/2026-07-29-current-yes-core-carry-v3-upgrade-and-performance-v1.md),
  [post-rebracket A/B](2026-07/2026-07-30-current-yes-core-carry-post-rebracket-event-ab-v1.md),
  and [maker lineage](2026-07/2026-07-29-current-yes-core-carry-maker-fill-lineage-v2.md).

The three small semantic reports are consolidated here rather than retained as
parallel dated references:

- precipitation/gust/pressure augmentation: overall Brier `0.07494` versus
  core `0.07445`; frozen `0.08811` versus `0.08749`; rejected;
- synoptic advection + vertical mixing: 1,349 checkpoints / 31 dates, overall
  Brier `0.07638` versus core `0.07445`, frozen `0.08837` versus `0.08749`;
  rejected, with partial PBL coverage retained as a mechanism clue;
- late semantic plateau: historical OOF improved (`0.0032` vs `0.0039`) but
  frozen worsened (`0.000037` vs `0.000029`); Wellington-only upward exits and
  report-time proxy prevent promotion.

The June Codex-preflight prompt experiments are also consolidated here. They
used only 24 settled matched rows from 2026-06-18..21; the best inspected prompt
kept 17 rows (15-2, estimated ROI `+15.31%`) but was chosen after comparing
three prompts, had no frozen forward denominator, and was never a live gate.
The durable result is `inconclusive / do not use LLM output as a blocking
label`; prompt transcripts and replay cache are machine artifacts, not two
separate strategy documents.

Historical Helsinki reversal incident: the branch bought 20°C YES and EFHK
printed 21°C two minutes later. The actionable bugs were fixed UTC+2 instead of
`Europe/Helsinki` DST and trading immediately before the next routine report
with stale observation context. The reusable outcome was the shared IANA
timezone/observation-clock contract; the old one-city guard values and runner
status are not current production configuration.

The two July 30 Core Carry operational slices are consolidated here as well.
The live funnel had 324 completed checkpoints over three target dates: 42 had
positive model-minus-mid residual, but only 11 remained positive after the
full five-share ask ladder and official fee; all 11 became first-positive
city-day signals. This was expected selector/execution-cost attrition, not a
collector outage. In the complementary historical slice, all 318 checkpoints
with `market_mid < model_p <= taker_cost` lost 2.42% fee-adjusted ROI; even the
unexecutable midpoint/zero-fee upper bound returned only +0.18%. Therefore the
filtered delta is not missing taker alpha and does not justify a wider live
gate; maker-only evidence still requires actual fill/adverse-selection data.

This section replaces the deleted 2026-06-21 numbered model map; the version
sequence remains recoverable from git history but is no longer a current or
parallel documentation entrypoint.

### What first-seen transition research is for

`first_seen_at` answers when the strategy could actually know that a source
event or state transition had happened. It is distinct from:

```text
valid_time / observation_time  = when the weather state applies
issued_at                      = when the provider says it published the item
first_seen_at                  = when our collector first received it
decision_ts                    = when the strategy evaluated the market
```

The target is not “rain/cloud is good or bad.” It is the information surprise:

```text
expected transition has arrived early / on time / late / not yet
new forecast run moved the upward-exit probability
new observation changed the path state before the market fully repriced
```

It has three legitimate consumers:

1. **Current-YES carry probability challenger** — update remaining overshoot
   risk when an expected rain/cloud/wind transition arrives early, arrives
   late, or fails to arrive. Example: a PIT TAF expected rain at 13:00, but at
   14:00 the station is still CAVOK; that is additional heating-window
   evidence, not a permanent city/date gate.
2. **Source-event repricing research** — measure whether a genuinely new
   METAR/TAF/forecast/radar event reaches our collector before the book reprices.
   This is a latency/execution residual and must use the quote at or after the
   same `first_seen_at`.
3. **Forecast revision reliability** — compare successive forecast vintages
   relative to the settlement-native upward-exit boundary. Re-reading one
   cached run is not a revision.

First-seen transitions must remain continuous telemetry until they improve
V3/core proper score on the same checkpoints and survive frozen forward. They
must not become `rain=true`, `TAF transition missing`, support-count, or other
hard eligibility gates. Later observations may be labels, never retroactive
features.

Use `reheat_risk` for the shared physical model, and use strategy-specific
names for trade expressions.

For a human-readable map of how `current YES`, `higher NO carry`, and
`low-price YES reheat reversal` share the same data/model base but split at
label, edge, and PnL, read
[`2026-06-21-reheat-risk-yes-no-expression-map.md`](2026-06/2026-06-21-reheat-risk-yes-no-expression-map.md).

## One Shared Model, Multiple Expressions

The shared question is:

```text
Given the current intraday running max, will the target day reheat enough to
change the final winning bracket?
```

The base output should be a path/risk distribution, not a trade by itself:

```text
p_reheat_ge_0_5c
p_reheat_ge_1c
p_current_bracket_holds
p_target_bracket_hit
```

This model should use one shared intraday fact layer: METAR/current observation,
running max, decline from max, forecast peak clock, dew point/RH, wind, sky,
solar/local time, orderbook quote, snapshot age, and final settlement label.

## Current Shared Feature Layer

`reheat_feature_factory_v1` is the maintained first shared materializer for this
branch:

- Script: `scripts/analysis/reheat_risk/research_reheat_feature_factory_v1.py`
- Report: `docs/analysis/2026-06/2026-06-16-reheat-feature-factory-v1.md`
- Coverage CSV: `docs/analysis/2026-06/generated/reheat_feature_factory_v1/coverage_by_date_city_hour.csv`

Current row grain:

```text
city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket + outcome
```

Current conclusion:

- The shared layer is good enough to support downstream `current_yes_peak_forming`,
  `current_yes_fade_confirmed`, `higher_no_carry`, and
  `low_price_yes_reheat_reversal` research without each strategy rebuilding its
  own observed-max/orderbook/settlement facts.
- It materialized 88,621 feature rows and 8,696 date/city/hour state rows on the
  2026-05-19..2026-06-14 replay window.
- Observed path, METAR dewpoint/RH/wind/temp-trend, current YES, d1/d2 NO,
  target YES, and settlement labels are usable.
- Forecast peak fields are no longer a total research blocker. The
  `fact_signal_candidates` schema/builder still only fills 108 historical rows
  from mirrored hourly cache, but a reusable research backfill now exists:
  `scripts/analysis/reheat_risk/build_forecast_peak_clock_backfill_dataset_v1.py`
  materializes
  `runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv`
  for the current-YES replay universe. It covers 831 city-date rows / 36 cities
  / 2026-05-19..2026-06-14 with 100% GFS and ECMWF peak-clock coverage. This
  supports research and shadow telemetry; production snapshots still need native
  point-in-time peak fields before forecast-clock can be trusted for live
  promotion.
- Research-only forecast-clock backfill now exists in
  `docs/analysis/2026-06/2026-06-17-theta-current-yes-forecast-peak-clock-backfill-v3.md`:
  it uses Open-Meteo historical forecast to fill 3,239 replay rows / 27 dates.
  Result: fixed v9 fade-confirmed remains the strongest live candidate
  (holdout 31 orders / 11 dates, taker +2c ROI +16.2%, CI [+2.7%, +27.9%]);
  GFS forecast-clock fade has 10 orders / 6 dates and is low-sample; peak-forming
  variants have more rows but CI crosses 0. Production snapshots still need
  native forecast peak fields before live promotion.
- Model-level forecast-clock v12 is also complete in
  `docs/analysis/2026-06/2026-06-17-theta-current-yes-forecast-clock-model-v12.md`.
  Forecast-clock features slightly improve all-holdout Brier in backfill
  research, but fail the live-like slice: market ask Brier 0.0659, v9 0.0688,
  best forecast-clock HGB 0.0719. Model-selected live-rule ROI also falls
  versus v9 (+16.2% for v9 vs +9.1% to +12.2% for forecast-clock variants).
  Treat forecast-clock as telemetry/feature logging, not a live upgrade.
- Forecast peak scorecard v14 is complete in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-forecast-peak-scorecard-v14.md`.
  It reuses the shared `forecast_peak_clock_backfill_v1.csv` table instead of a
  one-off API join. Result: v9 fixed fade-confirmed remains the baseline
  (holdout 31 orders / 11 dates, YES ROI +16.2%, YES-over-d1-NO +4.1% CI
  [+1.3%, +7.2%]). Adding forecast-clock filters shrinks sample: both forecast
  peaks passed gives 14 orders / 9 dates, YES ROI +17.1%, but below sample
  gate; after-peak-agree gives 7 orders / 6 dates. Diagnostic bins show a real
  risk shape: GFS peak still >=2h ahead is bad in holdout (14 orders, ROI
  -27.8%), while +1h..+4h after peak is positive. Treat this as a model feature
  and forward telemetry target, not a live hard guard yet.
- Observation/execution freshness v13 is complete in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-observation-execution-guard-v13.md`.
  It joins v8 current-YES replay to deduped factory obs-clock telemetry, so the
  v9 baseline matches v12 again: 31 orders / 11 dates, 29 wins, taker +2c ROI
  +16.2%. Simple guards do not add alpha: `obs_age <= 20m` leaves no half-hour
  replay orders, `pre_update_blackout=6m` blocks none, and
  `minutes_since_running_max >= 30m/45m` misses both losing cases while lowering
  ROI. Treat obs clock as live risk telemetry / accident guard only; the missing
  evidence layer is minute-level forward would-order telemetry.
- Forward telemetry v0 is implemented locally in
  `scripts/ops/weather_theta_current_yes_tiny_live.py` and documented in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-forward-telemetry-v0.md`.
  It writes
  `runtime/weather_edge_v1/theta_current_yes_tiny_live_v1/forward_telemetry.jsonl`
  with planned, fresh-book rejected, snapshot-rule rejected, and obs/hour blocked
  would-order rows. This is not a live policy change; it is the evidence layer
  needed to measure forward hit rate and taker ROI after enough rows settle.
- Live readiness v15 is complete in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-live-readiness-v15.md`.
  Current gate state: historical v9 PASS (31 holdout orders / 11 days, YES ROI
  +16.2%, YES-over-d1-NO +4.1%), forecast-clock upgrade FAIL, forward telemetry
  FAIL (only 44 non-live smoke rows, planned=0), and production native peak
  fields FAIL. Therefore the branch is not ready for live upgrade; next required
  evidence is N100 forward telemetry activation plus native point-in-time
  forecast peak fields from `weather-predict`.
- Native forecast peak field audit v1 is complete in
  `docs/analysis/2026-06/2026-06-18-weather-predict-forecast-peak-native-field-audit-v1.md`.
  Local `/Users/deepsleep/projects/weather-predict/paper_snapshot.py` already
  contains and smoke-tests the native `forecast_peak_*` implementation, but N100
  `/home/jiarui/projects/weather-predict/paper_snapshot.py` does not. Both
  local and N100 `weather-predict` copies are not git worktrees, so this is a
  deploy/repo-state blocker rather than a modeling blocker. Do not claim
  production native peak fields are live until this is resolved.
- Historical v16-v21 telemetry rollout is retained only as incident history:
  the old runner added forecast-peak fallback, telemetry mode and a separate
  N100 runtime; the first remote cycles produced audit rows but no plans. A
  forecast hard guard was then briefly deployed and removed the next day by
  v22. Those N100 paths, PIDs and start commands are retired and are not a
  recovery contract; current process truth comes only from the Mac controller,
  manifest and raw/exchange evidence.
- Feature-factory forecast peak slices v18 are complete in
  `docs/analysis/2026-06/2026-06-18-reheat-feature-factory-forecast-peak-slices-v18.md`.
  The shared `reheat_feature_factory_v1` now consumes
  `forecast_peak_clock_backfill_v1.csv` when native fact-table peak fields are
  missing, so GFS/ECMWF peak clock coverage rises to 95.7% of feature rows and
  94.1% of date/city/hour states. Holdout confirms the feature is useful for
  risk labeling: `danger_gfs_peak_still_2h_ahead` has current-YES win rate
  24.6% and ROI -18.0%, while `diagnostic_gfs_1_to_4h_after_peak` has win rate
  80.8% and ROI +0.8% with CI still crossing zero. This keeps forecast-clock in
  the model/telemetry layer, not as a live hard gate.
- Peak-forming hazard v1 is complete in
  `docs/analysis/2026-06/2026-06-19-current-yes-peak-forming-hazard-v1.md`.
  It trains a dedicated peak-forming model on the shared feature factory instead
  of only adding live if/else filters. Holdout result: market ask is already a
  very strong survival proxy (AUC 0.946/Brier 0.092), weather/forecast-only has
  real but weaker signal (AUC 0.905/Brier 0.124), and weather+price hazard v1
  only slightly improves Brier to 0.091. The live-like hazard rule has 233
  holdout rows / 14 dates / ROI +1.6% with date-bootstrap CI [-7.4%, +10.9%],
  so this remains research/shadow only and does not replace live. Data layer
  note: after sync + DB rebuild, orderbook/pm_history are newer, but the shared
  observed-detail input still materializes only through 2026-06-14; next work is
  extending official observation/observed-detail coverage before retraining.
- Peak-forming hazard v2 added plateau duration/count semantics on the same
  early-window denominator, but failed its forward gate: the primary rule was
  283 rows / 17 dates / ROI -0.9% with date-bootstrap CI [-8.9%, +7.5%]. Its
  reproducible report now belongs under
  `docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v2/report.md`;
  it is historical lineage, not a current shadow candidate.
- Future-break V3/V3.1 are consolidated into this family record. V3's primary
  rule was ROI +0.2% with date CI [-7.5%, +7.9%], and its positive tail had
  only three forward dates. V3.1's residual rule was 141 rows / ROI -1.6% with
  CI [-9.8%, +5.7%]. Both are `superseded`, and their rerunnable reports now
  route to the matching `generated/current_yes_future_break_hazard_v3*`
  artifact directories rather than human-facing dated documents.
- Data freshness audit v1 is complete in
  `docs/analysis/2026-06/2026-06-19-reheat-feature-data-freshness-v1.md`.
  It explains why the 2026-06-19 run still trained on a feature table ending
  2026-06-14: orderbook snapshots are present through 2026-06-19 and
  `settlement_outcomes` through 2026-06-17, but `wu_obs` production mirror stops
  at 2026-06-09 UTC, the one-off IEM ext patch stops at 2026-06-13 UTC, and both
  observed-detail plus forecast peak backfill stop at target_date 2026-06-14.
  Future reheat/current-YES/NO-carry/reversal research must run this audit or an
  equivalent max-date check before interpreting model results.

## Strategy Heads

### `current_yes_peak_forming`

Trade: buy current running-max bracket YES while the temperature is still at or
near the high.

Question:

```text
Is the current bracket already forming the final winning high?
```

This is the earlier, more aggressive no-reheat expression. It can catch a better
price before visible fade, but it needs stronger evidence that the remaining
heating path is exhausted.

### `current_yes_fade_confirmed`

Trade: buy current running-max bracket YES after the temperature has already
fallen from the high.

Question:

```text
After visible fade, is the current bracket now stable enough to buy?
```

This is the later, more conservative no-reheat expression. The price may already
have repriced upward, so the edge is more execution-sensitive.

### `higher_no_carry`

Trade: buy NO on higher brackets above the current running max.

Question:

```text
Are higher brackets still overpriced relative to reheat risk?
```

This shares the same no-reheat base model as current YES, but the payoff is not
identical. A d1 NO can still win when the final max skips over the adjacent
bracket, while current YES only wins when the final bracket equals the current
running-max bracket. Therefore it belongs in the same thesis family, not as a
separate alpha source.

### `low_price_yes_reheat_reversal`

Trade: buy a low-price YES target bracket that requires later reheating.

Question:

```text
Will the day reheat into this target bracket?
```

This is the opposite side of the same physical base. It should reuse
`reheat_risk` features, but it needs a separate label and model head because the
target is `target_yes_wins`, not `current_bracket_holds`.

## Project Structure

Preferred structure:

```text
shared layer:
  reheat_risk_features
  reheat_risk_model

strategy heads:
  current_yes_peak_forming
  current_yes_fade_confirmed
  higher_no_carry
  low_price_yes_reheat_reversal

expression layer:
  compare current YES vs d1/d2 NO vs low-price YES on the same city-hour state
```

Do not let each strategy materialize its own observed-max/orderbook/settlement
facts. They should share the same row grain, then branch only at label, model
head, and payoff/EV calculation.

New scripts should live under:

```text
scripts/analysis/reheat_risk/
```

Historical scripts under `scripts/analysis/observed_max/` remain archival until
they are intentionally migrated. Do not move old scripts only to rename them;
move a script only when it becomes the maintained entrypoint for a new result.

## Research Queue

1. `reheat_feature_factory`: v1 completed and now forecast-clock enriched via
   the documented backfill table. Keep downstream strategy heads on the shared
   factory output instead of private joins. Before rerunning model heads, check
   `2026-06-19-reheat-feature-data-freshness-v1.md` or rerun the freshness audit
   so the feature layer does not silently lag behind raw orderbook/settlement
   data.
2. `intraday_weather_regime_atlas`: v1 completed in
   `docs/analysis/2026-06/2026-06-24-intraday-weather-regime-atlas-v1.md`.
   It materializes reusable city/date/hour mechanism labels
   (`day_regime`, `intraday_state`, `moisture_cloud_regime`,
   `running_max_state`, `composite_regime`) plus expression payoff matrices for
   current YES, current-bracket NO, d1/d2 NO, and low-price higher YES. Treat
   these labels as weather structure and forward telemetry fields, not hard
   gates or live approval.
3. `forecast_peak_clock_data_fill`: pm_agent fact builder now derives
   `forecast_peak_*`/`forecast_values_hash` from mirrored hourly cache when
   present. Research backfill v3 proves the feature can be joined and measured,
   and dataset v1 promotes the historical current-YES replay universe into a
   shared research table. Forecast-clock itself is still not live-ready; next
   step is N100 forward telemetry activation with the pm_agent fallback, plus
   upstream `weather-predict` snapshot producer/cache deployment so native
   point-in-time fields eventually replace the fallback.
4. `current_yes_peak_forming` vs `current_yes_fade_confirmed`: factory-backed
   v1 completed in
   `docs/analysis/2026-06/2026-06-16-current-yes-peak-vs-fade-v1.md`.
   Current conclusion is fade-first shadow only: fixed holdout favors
   fade-confirmed, peak-forming remains a narrow early shadow sleeve, and there
   is no N100/live change. Forecast-clock v3/v12 did not overturn this.
   Peak-forming hazard v1 confirms the same direction with a dedicated model:
   it is a useful research probability layer, but it fails the significance gate
   as a live replacement.
5. `higher_no_carry_expression`: factory-backed v1 completed in
   `docs/analysis/2026-06/2026-06-16-higher-no-carry-expression-selector-v1.md`.
   NO carry/ladder did not prove stable positive excess ROI versus same-window
   current YES, so this remains shadow-only expression telemetry.
6. `low_price_yes_reheat_reversal`: use the same physical base for the opposite
   reheat/convexity expression.
7. `execution_freshness_gate`: v13 says not to promote a new historical alpha
   guard from half-hour replay. Next priority is production telemetry: fresh CLOB
   ask, snapshot age, obs age, minutes-to-next official observation,
   minutes-since-running-max, and source profile on every would-order before
   taker conversion. Local v0 implementation is complete; N100 parallel
   telemetry deployment and settled forward analysis remain.
8. `intraday_forecast_curve_morphology`: v1/v2 separate named full-day shapes
   (double peak, overnight peak with afternoon lobe, broad plateau, late peak)
   from the primary continuous state: every future local heat lobe's margin and
   heat area relative to the current exact upward-exit boundary. The old 32/33
   forward headline is withdrawn: seven rows had a future-hour boundary bug and
   25 reused stale observations. On the rebuilt 1,508-city-day/50-date evidence
   denominator there are 90 strict double-lobe city-days, 203 unusual-shape
   city-days and 90 first peak-clock aliases, so the pattern is common rather
   than Chengdu-only. It is not independent alpha: alias current NO wins 9/90
   with fee-adjusted ROI -16.0%; d1 YES wins 7/85 with ROI -24.1%. Keep the
   checkpoint collector and Chengdu-style forecast-revision interaction, freeze
   evaluation from 2026-07-29 for 15 target dates, and do not turn a named shape
   into a live gate. Evidence:
   `docs/analysis/2026-07/2026-07-29-intraday-forecast-curve-morphology-v2.md`.
9. `current_yes_core_carry` descriptive baseline: frozen 136 first-positive
   entries average 4.53 signals/target-date, model p 93.58%, market mid 89.26%
   and fee/depth-adjusted cost 91.33%.  The selected slice has positive average
   residual, but on the same 1,350 carry PIT states core-vs-market Brier and
   logloss deltas remain statistically inconclusive; clock and wind ablations
   also cross zero. Current 10+5 raw telemetry is dominated by mature-fade or
   plateau/heating-done states, but rain/cloud/advection/path regimes are not
   direct frozen-model inputs. Keep fixed 10 and treat richer weather semantics
   as a probability challenger, not a new gate. Evidence:
   `docs/analysis/2026-08/2026-08-06-current-yes-core-carry-descriptive-microstructure-v1.md`.
10. `current_yes_core_carry_semantic_challenger_v3`: preregistered four compact
    probability challengers on the unchanged 1,349-checkpoint PIT ledger. The
    development OOF selected calibration-only rather than richer semantic
    linear, interaction, or spline residuals. On the untouched final eight
    target dates calibration-only was worse than frozen core (Brier delta
    +0.00055, logloss delta +0.00241; both CIs cross zero), while its point
    estimates versus market were better but also inconclusive. This rejects the
    v3 candidate, not the idea of collecting richer first-seen weather state;
    do not change live or add gates. Evidence:
    `docs/analysis/2026-08/2026-08-06-current-yes-core-carry-semantic-challenger-v3.md`.
11. `current_yes_core_carry_overshoot_survival_v1`: same-expression challenger
    that converts same-state market hold probability to cumulative overshoot
    hazard, then applies a non-negative remaining-heat/path exposure multiplier.
    The fixed 1,349-checkpoint parent has 95 upward overshoots and the final
    eight target dates remain frozen forward. On primary state-entry grain the
    challenger was worse than core (Brier/logloss delta +0.00130/+0.01219;
    both CIs cross zero), and fixed-10 forward replay was -$3.11 versus core
    +$9.53. Development was also worse, so this scalar-exposure v1 is rejected.
    The fully time-varying hazard idea remains untested because PIT hourly curve
    files are currently permission-blocked; do not treat scalar max/peak buckets
    as an adequate substitute. Evidence:
    `docs/analysis/2026-08/2026-08-06-current-yes-core-carry-overshoot-survival-v1.md`.
12. `intraday_state_transition_card_v1`: the 3-card mini seed was withdrawn for
    a bad denominator. The expanded run preserved all 1,178 Core-scored
    checkpoints and selected 120 weather-only `gpt-5.4` cards before reading
    settlement: 31 policy hits, 7 same-day near misses, 31 matched controls and
    51 semantic-diversity controls. It covered 40 cities/110 city-days; 64 were
    aligned, 48 had semantic tension and 8 were ambiguous. On the 60 settled
    cards, `upward_exit` was correct only 6/14 times, so the LLM is not a veto.
    Keep collector-only and build the deterministic transition-confirmation
    residual challenger on the full same-denominator PIT set. Evidence:
    `docs/analysis/2026-08/2026-08-07-core-carry-llm-transition-card-v1.md`.
13. `current_yes_core_carry_transition_confirmation_challenger_v1`: implemented
    that deterministic residual without adding a gate. It corrected the primary
    grain from city-day to 801 city-date-bracket state entries and added native
    upper-exit geometry, dip/rebound, remaining heat, peak clock, plateau and
    incumbent wind-boost × transition-risk features. On the last eight dates it
    was effectively tied on Brier (challenger−core `-0.000003`, CI
    `[-0.001151,+0.001128]`) but worse on logloss (`+0.000410`, CI
    `[-0.003645,+0.004540]`); development was also worse. Several fitted signs
    were not physically stable, so proxying the missing semantics from this old
    ledger is rejected. Keep Core unchanged and collect true first-seen
    cloud/rain transition, dewpoint trend and direction×terrain transport for a
    new forward model. Evidence:
    `docs/analysis/2026-08/2026-08-07-current-yes-core-carry-transition-confirmation-challenger-v1.md`.
14. `current_yes_core_carry_actual_transport_tail_v1`: backfilled 36 settlement
    stations with as-of IEM/METAR dewpoint trends, wind direction/persistence,
    cloud change, rain, gust and pressure, then fit one monotone Core-offset
    overshoot head. Coverage was adequate except gust, but the actual-feature
    challenger did not add tail information: on the last eight dates it and
    Core marked the same 32/138 state entries high-risk, caught the same 7/14
    overshoots, and produced the same six 10-to-5-share decisions. Development
    was worse (22/34 overshoots from 125 flags versus Core 23/34 from 119).
    Therefore do not keep another entry-time global residual or sizing overlay.
    Reuse these fields for post-entry event-driven invalidation when a new METAR
    changes the state; that is a different clock and remains untested. Evidence:
    `docs/analysis/2026-08/2026-08-07-current-yes-core-carry-actual-transport-tail-v1.md`.
15. `current_yes_core_carry_mid_floor_forward_v1`: the deployed model was
    trained across market mid 0.011–0.9895, so 0.80 is a live authorization
    boundary, not model support.  On 2026-07-31..08-07, 577 settled
    same-support probability checkpoints and 576 executable 10-share rows
    produced 21 first-positive entries at floor 0.80 (19 wins, PnL -$2.21,
    ROI -1.15%).  Lowering to 0.50 produced 26 entries (22 wins, +$0.54,
    +0.24%); all <0.80 first-positive opportunities together were 7/10 and
    -$1.55.  The post-hoc best tested floor was 0.90, but six-floor exact
    target-date sign-flip family-wise p=0.547; every forward proper-score band
    CI also crossed zero.  Historical 0.50–0.80 OOF remained positive but
    inconclusive, while Europe floor 0.70 was negative.  Keep 0.80 live as a
    conservative capital boundary, keep 0.50–0.80 continuous positive-net-EV
    scoring as shadow evidence, and do not convert any tested price or weather
    bucket into a new gate. Evidence:
    `docs/analysis/2026-08/2026-08-08-current-yes-core-carry-mid-floor-forward-v1.md`.

## Naming Rules

- New reports, scripts, modules, strategy instances, and docs should use
  `reheat_risk` or a strategy-head name.
- Historical files with old observed-max names remain archival evidence only.
- Current reports should say `reheat risk`, `no-reheat`, `current YES`, `higher
  NO carry`, or `reheat reversal`, depending on the actual target metric.
