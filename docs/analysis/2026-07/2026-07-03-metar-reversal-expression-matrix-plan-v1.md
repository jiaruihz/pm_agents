# METAR Reversal Expression Matrix Plan v1

Generated: 2026-07-03

## Verdict

`metar_reversal_expression_matrix_v1` is a new independent research head.  It is
not a low-price YES filter and it does not change live trading.

The core lesson from the review is that previous METAR tests were not clean
tests of METAR alpha.  They mostly asked whether intraday METAR/regime can
improve a forecast-tail lottery sleeve.  The better question is:

```text
At the same city-date-decision snapshot, given current METAR state and bracket
ladder prices, which expression is mispriced?
```

Initial conclusion:

```text
significance=NA
baseline=NA
forward=NA
conclusion=inconclusive; build shadow telemetry and offline matrix first
```

## Strategy Boundary

This head is separate from:

- `low_price_yes_lottery_tiny_live_v1`: forecast-bias / low-price YES convex
  sleeve, now maker-first tiny live.
- `low_price_yes_integrated_tail_shadow_v2`: diagnostic tail telemetry for the
  low-price sleeve.
- regime-routed NO runner: current NO / current YES expression router lineage.

If the research shows the edge is only better expression selection for an
existing runner, it can later become a shadow selector.  If the edge comes from
METAR print timing, source-basis, or bracket-ladder repricing, register it as a
separate strategy family.

## Why Previous METAR Work Was Inconclusive

1. The same-bracket METAR join in integrated tail v2 covered only 3.8% of the
   V1 denominator and selected for tickets that stayed cheap intraday.  That is
   a negative-selection sample, not a true METAR test.
2. METAR was tested mostly as a low-price YES entry filter.  The more plausible
   alpha is expression switching: current-high YES, current-bracket NO, d1/d2,
   hotter bracket YES, or high-tail YES.
3. Expression A/B was not fully same-denominator.  Some earlier high-tail legs
   used weak proxy pricing, so their failure does not disprove the matrix idea.
4. Execution was under-modeled.  Historical ask backtests do not answer whether
   maker-first fills capture the edge or miss the winners.

## First-Principles Hypothesis

METAR can create edge when observation state, settlement source, forecast path,
and market bracket ladder disagree.

The three alpha families must stay separate:

| Family | Question | Expected evidence |
|---|---|---|
| Forecast bias | Did a city/source/model systematically under- or over-forecast final max? | Station-vs-forecast as-of features improve forward outcomes versus same-price baseline |
| METAR reversal | Did live observation path imply the market expression was wrong? | METAR features add incremental lift after forecast and market baseline |
| Market microstructure | Did market reprice slowly or overreact after prints? | Orderbook path shows executable lag or maker fills before repricing |

## Denominator

Use two fixed denominators and do not condition on V1 low-price candidates.

| Denominator | Grain | Purpose |
|---|---|---|
| D-1 evening | `city_date_decision_snapshot`, local 18-24h | Compare with existing early forecast-tail sleeve and time-zone attention effects |
| Intraday hourly | `city_date_hour_snapshot`, local 10-21h | Test METAR state and expression switching after observations print |

Required filters:

- city/date has a final settlement label from `settlement_outcomes` or audited
  observed-label extension.
- snapshot has point-in-time market prices for at least K expression legs.
- all local-time calculations use city timezone.
- final label is final winning bracket only; running max touching a bracket is
  never a win label.

## Expression Matrix

At each snapshot, build a sibling expression row set:

| Expression | Meaning |
|---|---|
| `current_high_yes` | YES on the current running-max bracket |
| `current_bracket_yes` | YES on latest-observed-temp bracket |
| `current_bracket_no` | NO on latest-observed-temp bracket |
| `d1_yes` | YES one bracket hotter than current-high |
| `d1_no` | NO one bracket hotter than current-high |
| `d2_yes` | YES two brackets hotter than current-high |
| `d2_no` | NO two brackets hotter than current-high |
| `hotter_tail_yes` | cheapest valid YES at d3+ or nearest high-tail bracket |
| `current_ladder_best` | best EV expression from calibrated distribution, diagnostic only |

Every expression row must carry:

- point-in-time bid/ask/spread/depth
- payoff from final winning bracket
- ask ROI and fee-adjusted taker ROI
- maker-first replay fields
- expression rank within the same snapshot

## Feature Set

### METAR / Observation

- latest obs temp in market units and native units
- running max and current-high bracket
- minutes since running max
- trend over 30/60/180 minutes
- observation age
- station cadence class
- source type: routine METAR, SPECI, MADIS/HF, WU/hourly, settlement source
- main temp vs RMK tenth temp where available
- boundary ambiguity flag

### Physical State

- remaining solar window
- forecast peak local time and delta from decision time
- forecast max minus running max
- forecast max minus candidate bracket
- humidity/dewpoint regime
- cloud regime
- wind / mixing regime
- day regime and intraday_state from the temperature context feature layer

### Market / Microstructure

- sibling ladder bid/ask/depth
- ask move since previous observation print
- bid/ask move in 1/5/15/30 minutes after print
- maker `bid+1tick` would-fill by future orderbook path
- taker fee-adjusted edge
- spread/depth filters reported as diagnostics, not live gates

## Evaluation Plan

### P0: Telemetry Spec And Shadow Recorder

Deliverables:

- `scripts/ops/metar_reversal_expression_matrix_shadow_v1.py`
- runtime output:
  `runtime/weather_edge_v1/metar_reversal_expression_matrix_shadow_v1/snapshots.jsonl`
- summary:
  `runtime/weather_edge_v1/metar_reversal_expression_matrix_shadow_v1/latest_summary.json`

The shadow recorder only writes zero-notional rows.  It should run hourly or
near observation-print cadence and collect sibling expression prices plus METAR
state.  It must not submit orders.

Minimum row fields:

```text
snapshot_id, city, target_date, decision_ts_utc, decision_hour_local,
timezone, icao, unit, expression, bracket, side, bid, ask, spread, depth,
latest_obs_ts_utc, latest_obs_native, running_max_native,
current_high_bracket, current_obs_bracket, minutes_since_running_max,
trend_1h_native, obs_age_min, source_type, metar_raw,
metar_main_c, metar_rmk_tenth_c, boundary_ambiguity_flag,
forecast_max_native, forecast_peak_time_local, forecast_gap_to_running_native,
day_regime, intraday_state, wind_regime, moisture_cloud_regime,
ask_move_since_last_print, maker_bid_plus_1tick_price,
guarded_taker_would_fill, live_disabled_reason
```

### P1: Offline Same-Denominator Matrix

Deliverables:

- `scripts/analysis/reheat_risk/research_metar_reversal_expression_matrix_v1.py`
- report:
  `docs/analysis/2026-07/YYYY-MM-DD-metar-reversal-expression-matrix-v1.md`
- generated CSV/JSON under:
  `docs/analysis/2026-07/generated/metar_reversal_expression_matrix_v1/`

Metrics:

- rows / dates / cities
- win rate
- avg ask
- ROI
- daily PnL distribution
- losing days
- <= -50% days
- max daily loss
- target-date block bootstrap CI
- forward/recent window
- expression contribution
- city/source/regime contribution
- selected-vs-complement same-denominator checks

Models / scorecards:

1. market-only baseline
2. forecast-bias features only
3. forecast-bias + METAR features
4. forecast-bias + METAR + market microstructure

Promotion test is incremental lift, not headline ROI alone.

### P2: Execution And Repricing Replay

Deliverables:

- orderbook replay table with `snapshot_ts_utc <= decision_ts_utc`
- maker-fill sensitivity:
  - historical ask taker
  - fee-adjusted taker
  - maker `bid+1tick` fill if later ask/bid path crosses
  - missed-winner stress at 0% / 50% / 100%

Key question:

```text
Does METAR edge exist before the market reprices, and can maker-first actually
capture it without missing most winners?
```

### P3: Research Verdict

Verdict can only be:

- `confirmed`
- `shadow_candidate`
- `inconclusive`

Default action remains no live change.

## First Three Triggers To Test

### 1. Boundary Source-Basis Reversal

Mechanism:

```text
coarse feed appears to cross a bracket, settlement-grade source does not,
market sells the YES or over-buys the NO, then price reverts.
```

Fields needed:

- `metar_main_c`
- `metar_rmk_tenth_c`
- settlement source temp
- boundary ambiguity flag
- price path before and after print

Initial action: shadow only.

### 2. Mature Heat-Death Expression Switch

Mechanism:

```text
current high printed, trend fades, peak time passed, remaining solar window is
small, but current_high_yes is still underpriced or d1/d2 NO is mispriced.
```

Candidate expressions:

- `current_high_yes`
- `d1_no`
- `d2_no`

Initial action: same-snapshot A/B, no live action.

### 3. False-Fade / Reheat Conflict

Mechanism:

```text
market prices the day as capped, but METAR trend and forecast peak clock still
show reheat runway.
```

Candidate expressions:

- `d1_yes`
- `d2_yes`
- `hotter_tail_yes`
- avoid stale current-bracket NO if revisit/overshoot risk is high

Initial action: zero-notional shadow, then orderbook replay.

## Fresh Forward Rule

Any rule chosen using data through 2026-07-02 is hypothesis generation only.
Promotion evidence must use fresh rows from 2026-07-03 onward, with the rule
frozen before evaluation.

No size-up and no live routing until the matrix passes:

```text
significance=PASS
baseline=PASS
forward=PASS
```

## Immediate Next Work

1. Build the zero-notional shadow recorder with sibling expression capture.
2. Run it on Mac alongside the current low-price YES V1 and integrated-tail
   shadow.
3. Backfill a historical matrix from existing atlas/orderbook where possible,
   but mark missing sibling legs explicitly.
4. After 3-5 fresh target dates, run the first P1 report.  If fresh coverage is
   thin, extend collection rather than adding gates.

