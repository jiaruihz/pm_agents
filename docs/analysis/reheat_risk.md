# Reheat Risk Model

Status: current-reference
Created: 2026-06-16

Use `reheat_risk` for the shared physical model, and use strategy-specific
names for trade expressions.

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
- Current-YES forecast peak live fallback v16 is complete in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-forecast-peak-live-fallback-v16.md`.
  The `pm_agent` runner can now derive forecast peak fields itself from
  Open-Meteo hourly forecast when snapshots lack native `forecast_peak_*`,
  cache the payload, and log `forecast_peak_fetch_status` into plans/forward
  telemetry. This does not change the v9 trading rule; it only makes forward
  telemetry complete enough to score forecast-clock features before
  `weather-predict` native fields are deployed. Runtime activation still
  requires the N100 current-YES loop to load the new code.
- Telemetry activation runbook v17 is complete in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-telemetry-activation-runbook-v17.md`.
  The start script now has an explicit `THETA_CURRENT_YES_MODE=telemetry` path
  that omits live flags and can accumulate forward would-order rows with
  forecast peak fields. The default remains `live`, so this is not a silent
  production behavior change. A read-only status helper now reports loop state,
  latest summary counts, telemetry decision-status distribution, and forecast
  peak fetch status. N100 activation still needs explicit deploy/start
  confirmation.
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
- Parallel forecast telemetry rollout v19 is deployment-ready in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-parallel-telemetry-rollout-v19.md`.
  The runner and wrappers now support a separate
  `theta_current_yes_forecast_telemetry_v1` runtime via
  `THETA_CURRENT_YES_RUNTIME_DIR` and `THETA_CURRENT_YES_STRATEGY_INSTANCE`.
  This allows no-live forward telemetry to run alongside the existing
  `theta_current_yes_tiny_live_v1` loop instead of sharing its PID/output files.
- Parallel telemetry deploy v20 is complete in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-parallel-telemetry-deploy-v20.md`.
  N100 now runs `theta_current_yes_forecast_telemetry_v1` as telemetry-only
  alongside the old live loop. First remote cycle wrote 42 audit rows with
  `live_enabled=False` and `plans=0`; useful forecast-peak rows require future
  active local windows.
- Forecast guard live upgrade v21 is deployed in
  `docs/analysis/2026-06/2026-06-18-theta-current-yes-forecast-guard-live-upgrade-v21.md`.
  The default live rule now rejects missing forecast peak and rejects forecast
  peak delta `< -1.999h`, blocking the clearest v18 danger bucket before any
  tiny-live order. The old live process was replaced by a single new
  `theta_current_yes_tiny_live_v1` process on N100; first post-restart cycle had
  `plans=0`, and the live order file stayed at 5 historical rows.

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
   factory output instead of private joins.
2. `forecast_peak_clock_data_fill`: pm_agent fact builder now derives
   `forecast_peak_*`/`forecast_values_hash` from mirrored hourly cache when
   present. Research backfill v3 proves the feature can be joined and measured,
   and dataset v1 promotes the historical current-YES replay universe into a
   shared research table. Forecast-clock itself is still not live-ready; next
   step is N100 forward telemetry activation with the pm_agent fallback, plus
   upstream `weather-predict` snapshot producer/cache deployment so native
   point-in-time fields eventually replace the fallback.
3. `current_yes_peak_forming` vs `current_yes_fade_confirmed`: factory-backed
   v1 completed in
   `docs/analysis/2026-06/2026-06-16-current-yes-peak-vs-fade-v1.md`.
   Current conclusion is fade-first shadow only: fixed holdout favors
   fade-confirmed, peak-forming remains a narrow early shadow sleeve, and there
   is no N100/live change. Forecast-clock v3/v12 did not overturn this.
4. `higher_no_carry_expression`: factory-backed v1 completed in
   `docs/analysis/2026-06/2026-06-16-higher-no-carry-expression-selector-v1.md`.
   NO carry/ladder did not prove stable positive excess ROI versus same-window
   current YES, so this remains shadow-only expression telemetry.
5. `low_price_yes_reheat_reversal`: use the same physical base for the opposite
   reheat/convexity expression.
6. `execution_freshness_gate`: v13 says not to promote a new historical alpha
   guard from half-hour replay. Next priority is production telemetry: fresh CLOB
   ask, snapshot age, obs age, minutes-to-next official observation,
   minutes-since-running-max, and source profile on every would-order before
   taker conversion. Local v0 implementation is complete; N100 parallel
   telemetry deployment and settled forward analysis remain.

## Naming Rules

- New reports, scripts, modules, strategy instances, and docs should use
  `reheat_risk` or a strategy-head name.
- Historical files with old observed-max names remain archival evidence only.
- Current reports should say `reheat risk`, `no-reheat`, `current YES`, `higher
  NO carry`, or `reheat reversal`, depending on the actual target metric.
