# Weather Probability Model Roadmap

Status: `current-reference`。本文描述概率模型改造路线；当前 edge engine 接手状态见 [WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md](WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md)。

Last updated: 2026-06-04

This document describes the staged path from the current baseline
`model_p_yes` to a better calibrated weather probability model. It is a design
roadmap, not a backtest report. Current-model audit and evidence live in
[WEATHER_PROBABILITY_MODEL_REVIEW.md](WEATHER_PROBABILITY_MODEL_REVIEW.md).

## Goal

The model should estimate the full final-settlement temperature distribution:

```text
P(final_settlement_temp = k | information visible at decision time)
```

Bracket probabilities should be derived from that distribution:

```text
P(YES for "70-71") = P(temp = 70) + P(temp = 71)
P(YES for "72+")   = SUM P(temp >= 72)
```

The trading layer should then decide whether to trade, size, shadow, or skip.
It should not treat a raw positive edge as sufficient when the underlying
forecast distribution is unstable.

## Non-Goals

- Do not jump directly to a complex ML model before the data contract and
  validation skeleton exist.
- Do not replace the current bootstrap baseline without a side-by-side shadow
  run and walk-forward validation.
- Do not mix model quality with execution quality. Probability evaluation uses
  calibrated forecast outcomes; PnL evaluation remains in `fact_trades`.
- Do not deploy N100 behavior changes through file copy. Production changes
  must follow the `weather-strategy-deploy` git-first workflow.

## Current Baseline

Production currently does:

```text
forecast_max_f + historical residual samples
→ round to integer temperature
→ count samples inside each Polymarket bracket
→ model_p_yes
```

This baseline is useful because it is simple, non-parametric, and easy to
audit. The main defects are not edge formula bugs; they are missing
conditioning, missing forecast version observability, and excessive sensitivity
for narrow brackets.

## Target Architecture

### Layer 1: Settlement Target

Define exactly what the model is predicting:

- settlement source or station,
- local date and cutoff window,
- max-temperature rule,
- unit conversion and rounding,
- mapping from final temperature to Polymarket bracket.

This is the highest-priority correctness dependency. If the training target and
Polymarket settlement target differ, every downstream probability can be well
calibrated to the wrong thing.

### Layer 2: Forecast Snapshot

Every model run must store enough raw forecast metadata to reproduce or audit
probability changes:

- `forecast_source`,
- real forecast model/version metadata if available,
- `forecast_values_hash`,
- `forecast_max_f`,
- `forecast_max_hour_local`,
- current observed max and latest observation timestamp,
- model spread fields when multiple forecasts are available,
- decision-time timestamps in UTC, Beijing, and local time.

The immediate fix is to store a hash and max-hour even before a full model
rewrite. This makes forecast jumps explainable.

### Layer 3: Temperature Distribution

The probability provider should output one calibrated distribution per
city-date-snapshot:

```json
{
  "city": "LA",
  "target_date": "2026-06-01",
  "distribution_unit": "F",
  "temp_probabilities": {
    "68": 0.04,
    "69": 0.12,
    "70": 0.23,
    "71": 0.28,
    "72": 0.19
  }
}
```

Downstream bracket probabilities are derived by summing this distribution. This
keeps all brackets coherent and makes city-day portfolio logic possible.

### Layer 4: Calibration and Validation

Every model version must have:

- walk-forward Brier score,
- calibration curves by probability bucket,
- city / season / lead-time slices,
- YES-vs-NO side reliability,
- stability metrics such as forecast jump and side flip rate,
- a frozen model version id.

The validation output should be stored as a model artifact, not a one-off
notebook result.

### Layer 5: Trading Guardrails

Execution should consume both probability and stability diagnostics:

- expected edge,
- probability confidence / calibration quality,
- forecast jump size,
- side flip history for the same bracket,
- multi-model spread,
- whether the bracket is narrow,
- existing city-day exposure.

The trading layer can then choose:

```text
TRADE_FULL / TRADE_REDUCED / SHADOW_ONLY / SKIP
```

## Staged Implementation

### M0: Observability Skeleton

Goal: make the current model auditable without changing trade decisions.

Changes:

- Add forecast hash and max-hour fields to market snapshots.
- Persist enough hourly forecast detail to explain why `forecast_max_f` moved.
- Add side-flip and forecast-jump diagnostics to local analysis.
- Add a shadow-only stability flag:
  - `forecast_jump_f`,
  - `side_flip_count_today`,
  - `probability_jump`,
  - `narrow_bracket`.

Acceptance:

- For any large `model_p_yes` jump, we can identify whether it came from
  forecast max, market price, model source, or data gap.
- No production order behavior changes yet.

### M1: Baseline Plus Stability Gate

Goal: reduce known bad behavior while keeping the current probability formula.

Changes:

- If `forecast_jump_f > 1.0` within the recent window, downgrade to reduced
  size or shadow.
- If the same city-date-bracket flips YES/NO in the same trading day, downgrade
  to shadow unless explicitly overridden.
- Use a stricter gate for narrow brackets that are within 1-2°F of the current
  forecast max boundary.

Acceptance:

- Side-flip trades are visible in shadow metrics.
- Live behavior changes are gated by config and can be rolled back.

### M2: Seasonal Residual Baseline

Goal: improve the baseline without new data dependencies.

Changes:

- Replace global residual sampling with per-city seasonal residual selection,
  using day-of-year nearest neighbors.
- Keep per-city switch:
  - strong seasonal cities use seasonal residuals,
  - weak or negative cities keep global residuals.
- Produce both old and new `model_p_yes` in shadow until validated.

Acceptance:

- Walk-forward Brier improves on the city set where enabled.
- Side-specific reliability does not worsen, especially for BUY_YES.

### M3: Lead-Time and Observation-Aware Distribution

Goal: condition uncertainty on decision context.

Inputs:

- hours to settlement,
- current observed max,
- latest observed temperature,
- whether peak heating hours remain,
- forecast max hour,
- local time and season.

Changes:

- Treat current observed max as a hard lower bound for final max.
- Use different residual distributions by lead-time bucket.
- Widen uncertainty when the peak hour is still ahead and narrow it when the
  day is nearly settled.

Acceptance:

- Better calibration in T-24 / T-12 / same-day slices.
- Lower late-day side flip rate.

### M4: Multi-Model Ensemble

Goal: use forecast disagreement as an uncertainty signal without discarding
city-specific model suitability.

Inputs:

- GFS / ECMWF / HRRR / other available forecast max,
- model spread,
- per-city historical model performance.

Changes:

- Keep a per-city primary model unless validation proves a better alternative.
- Shadow a weighted ensemble beside the per-city primary model.
- Use spread primarily to scale uncertainty and sizing, not as an automatic
  "switch to another model" rule.
- Downsize or shadow when models disagree strongly.

Acceptance:

- The ensemble beats the per-city best single-model baseline in walk-forward
  validation, not just a global GFS/ECMWF baseline.
- Spread improves sizing decisions, not just probability scores.
- Per-city regressions are reported explicitly; cities where ensemble hurts
  remain on their primary model.

### M5: Full Conditional or ML Distribution Model

Goal: replace hand-built residual selection only after the skeleton is proven.

Candidates:

- conditional empirical residual model,
- quantile regression,
- NGBoost or distributional gradient boosting,
- conformal prediction over final max temperature.

Required features:

- season,
- city,
- lead time,
- current observations,
- forecast max and max hour,
- model spread,
- forecast drift,
- humidity / wind / cloud fields if data collection is repaired.

Acceptance:

- Walk-forward calibration beats M2/M3/M4.
- Model artifacts are reproducible.
- Shadow PnL and live risk metrics support deployment.

## First Work Package

Start with M0. It is the smallest step that makes future analysis less
ambiguous and does not change production behavior.

Tasks:

1. Add forecast observability fields to the producer contract:
   - `forecast_values_hash`,
   - `forecast_max_hour_local`,
   - `forecast_hourly_count`,
   - optional `forecast_hourly_path`.
2. Add these fields to `weather-predict` snapshot output.
3. Mirror and ingest these fields into `pm_agent` raw/fact tables where
   practical.
4. Keep the existing `model_p_yes` formula unchanged.
5. Add a local diagnostic that reports large probability jumps with:
   - `d_forecast_max_f`,
   - `d_model_p_yes`,
   - `d_market_yes_price`,
   - model source,
   - forecast hash change,
   - side flip count.

This gives us the skeleton needed to decide whether M1 should be a sizing
gate, M2 should be seasonal residuals, or M3 should be lead-time conditioning.

## Open Questions

- What exact source and time window does Polymarket use for settlement in each
  city?
- Should late-day observed max be a hard lower bound for all cities, including
  non-US stations with weaker observation coverage?
- Should forecast jump gates be global or city-specific?
- How much can live order behavior change before we require a full shadow-only
  interval?
- Which city set should enable seasonal residuals first?
- For each city, which model should be primary, and where does a multi-model
  ensemble genuinely beat the city-specific primary model instead of adding
  noise?
