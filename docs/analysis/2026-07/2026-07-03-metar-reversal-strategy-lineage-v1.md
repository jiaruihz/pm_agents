# METAR Reversal Strategy Lineage v1

Generated: 2026-07-03

## Verdict

`metar_reversal` should be promoted from an experiment name into a strategy-family
research head. The old numbered experiment labels are retired; the durable
branch names are `heat_death` and `false_fade_reheat_conflict` inside the same
intraday METAR expression matrix.

Current status:

```text
family: metar_reversal / intraday expression matrix
live action: none
shadow action: collect zero-notional fresh-forward rows
best branch so far: rich_current_collapse_d1_yes (Shape B4) and sibling false_fade_reheat_conflict -> d1_yes
conclusion: shadow_candidate for rich-current one-step reversal, inconclusive for family-level live trading
```

This family is separate from the earlier D-1 / early-entry forecast-tail sleeve,
but it shares forecast/context features with it. The difference is not the raw
weather data source; the difference is the decision timing and alpha mechanism.

2026-07-03 taxonomy cleanup:

```text
Head A: forecast-tail low-price YES lottery + TP20
  early low-price convex sleeve; not owned by this document

Head B: METAR rich-current collapse / runway d1 YES reversal
  intraday medium-priced one-step reversal; owned by this document
```

The rich-current branch is not a TP20 lottery. Historical replay says this
branch wants taker entry and hold-to-settlement; maker-first and TP20/TP30 are
diagnostic only and should not be copied from Head A.

## Data To Strategy Chain

```text
canonical weather/context facts
  -> intraday city-date-hour state
  -> sibling expression matrix at the same snapshot
  -> METAR/path conflict signals
  -> branch-specific expression choice
  -> zero-notional shadow replay
  -> possible future strategy family or selector
```

### 1. Data Layer

Use the same canonical feature/fact layer where possible:

- `settlement_outcomes` / final winning bracket for labels.
- intraday weather regime atlas rows from
  `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/`.
- point-in-time market ladders / paper snapshots for sibling expression asks.
- observation cache / METAR-derived current temp, running max, trend, obs age,
  and minutes since running max.
- forecast path fields: `forecast_max_native`,
  `forecast_peak_delta_hours_local`, and gap to running max.

The grain is not a low-price ticket. The clean research grain is:

```text
city + target_date + decision_hour_local + decision_snapshot_ts_utc
```

That row then fans out into expressions:

```text
current_high_yes
current_bracket_no
d1_yes
d1_no
d2_yes
d2_no
hotter_tail_yes
```

## Signal Branches

### Branch A: Rich-Current Collapse / Runway D1 YES

Question:

```text
Does the market still price current as likely while the live obs path and
forecast ladder both point one bracket higher?
```

Canonical experimental trigger from
`2026-07-03-hotter-tail-reversal-shapes-v1`:

```text
rich_current_collapse_d1_yes
current_high_yes ask >= 0.60
temp_trend_1h_f >= +0.5F
forecast_target_steps >= 1
forecast_peak_delta_hours_local <= 0
```

Primary expression:

- `d1_yes`: one bracket hotter than current high.

Sibling checks:

- `current_bracket_no`: same directional break, lower carry.
- `d2_yes` / `hotter basket`: historically bad in this shape; do not route by
  default.

Current evidence:

```text
rich_current_collapse_d1_yes: 59 rows / 28 dates
avg ask about 0.29, win 52.5%, ROI +103.2%
date-block CI [+38.3%, +163.8%]
top5-removed ROI +50.8%
union with older anchored/false-fade trigger: 69 rows / 29 dates, ROI +87.4%, CI > 0
conclusion: shadow_candidate
```

Execution read:

```text
taker entry + hold-to-settle
TP20/TP30 rejected for this branch
maker-first entry rejected because historical fills are adversely selected
```

Main caveat:

```text
All qualifying historical rows are before 2026-06-21. The next question is
state frequency and live fill feasibility, not another in-sample threshold.
```

### Branch B: Heat-Death

Question:

```text
Has the day likely stopped making new highs, while the market still misprices
the current/high expression?
```

Canonical experimental trigger:

```text
heat_death
minutes_since_running_max >= 90
temp_trend_1h_f <= 0
forecast peak already passed
current_high_yes ask <= 0.85
```

Candidate expressions:

- `current_high_yes`
- `current_bracket_no`
- `d1_no`

Current evidence from
`2026-07-03-metar-reversal-expression-matrix-v1`:

```text
heat_death current_high_yes: 80 rows / 28 dates / 22 cities
avg ask 0.672, win 71.3%, ROI +14.5%
date-block CI [-20.5%, +56.8%]
conclusion: inconclusive, telemetry only
```

Interpretation: this branch may be real, but the first frozen rule is not strong
enough. It should remain a diagnostic branch, not a live selector.

### Branch C: False-Fade / Reheat Conflict

Question:

```text
Does the market think the day is done, while METAR and forecast path still
point to another bracket higher?
```

Canonical experimental trigger:

```text
false_fade_reheat_conflict
temp_trend_1h_f >= +0.5F
forecast_max_native - running_native >= 1.0
forecast_peak_delta_hours_local <= 0
d1_yes ask <= 0.30
current_high_yes ask >= 0.40
```

Primary expression:

- `d1_yes`: one bracket hotter than current high.

Sibling checks:

- `current_high_yes`: same snapshot, but wrong expression in this branch.
- `current_bracket_no`: also positive in sample, useful sibling baseline.
- `d2_yes` / `hotter_tail_yes`: too far out in this first matrix.

Current evidence:

```text
false_fade_reheat_conflict d1_yes: 32 rows / 22 dates / 15 cities
avg ask 0.175, win 43.8%, ROI +119.5%
date-block CI [+29.8%, +216.2%]
top5-removed ROI +27.2%
+1c taker stress ROI +107.6%
conclusion: shadow_candidate
```

The main caveat is time stability:

```text
May: 13 rows, ROI +237.8%, CI [+113.2%, +367.0%]
June: 19 rows, ROI +38.6%, CI [-49.3%, +148.7%]
```

Interpretation: `false_fade_reheat_conflict` is the best current branch, but it
is still a small-sample shadow branch until fresh-forward rows settle and
live-book depth/fill behavior are measured.

### Sizing / Trade Count Expectation

Using the frozen `false_fade_reheat_conflict -> d1_yes` rows and a small-live
style sizing rule:

```text
if d1_yes ask <= 0.30:
  notional = min(max($1.00, 5 * ask), $1.50)
```

historical settled replay gives:

```text
rows: 32 / 38 calendar dates / 22 active dates / 15 cities
avg ask: 0.175
avg notional: $1.13
calendar frequency: 0.84 trades/day
active-day frequency: 1.45 trades/day
max trades/day: 3
avg cost/day over all calendar dates: $0.95
avg cost on active days: $1.64
weighted ROI: +125.1%
PnL/day over all calendar dates: +$1.19
losing active days: 10
<= -50% active days: 10
max daily loss: -$3.65
May ROI: +241.4%
June ROI: +48.1%
```

If we require strict $1 tickets and `ask <= 0.20`, the branch becomes thinner:
18 rows / 14 active dates, 0.47 trades/calendar day, ROI +73.0%, but June is
negative. The practical read is: do not expect 5-6 trades/day from this branch;
expect many zero-trade days, sometimes 1-3 trades when the intraday conflict
appears.

## Relationship To Forecast-Tail

These are two related but distinct strategy families.

| Layer | Forecast-tail / low-price YES | METAR reversal / expression matrix |
|---|---|---|
| Decision timing | usually D-1 / early city-date snapshot | intraday after observations print |
| Main grain | one low-price YES candidate per city-date | one city-date-hour state with sibling expressions |
| Core question | is the market underpricing station/model-adjusted tail probability? | is the market choosing the wrong bracket expression given live observation path? |
| Primary data | forecast probability, station-basis, ask band, source/context tags | METAR/running max/trend, forecast path, bracket ladder, orderbook movement |
| Expression | usually low-price hotter YES | expression can be current YES, current NO, d1 YES, d1 NO, d2, tail |
| Alpha hypothesis | convex market underpricing of forecast/station-basis tail | observation-led path conflict or repricing lag |
| Current status | V1 tiny live plus shadow telemetry | zero-notional shadow only |

So the answer is:

```text
They are two strategy families, not one.
They should share canonical features and expression-matrix infrastructure.
They should not share the same live runner until fresh evidence says the edge is
just expression selection for an existing runner.
```

## Naming Convention

Use mechanism names in future code, docs, and runtime outputs:

```text
family: metar_reversal
branch: false_fade_reheat_conflict
frozen_trigger: false_fade_reheat_conflict_d1yes_v1
expression: d1_yes
status: shadow_candidate
```

Similarly:

```text
family: metar_reversal
branch: heat_death
frozen_trigger: heat_death_current_high_yes_v1
expression: current_high_yes / sibling matrix
status: inconclusive_telemetry
```

## Implementation Boundary

Current artifacts:

- `scripts/analysis/reheat_risk/research_metar_reversal_expression_matrix_v1.py`
- `scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py`
- `docs/analysis/2026-07/2026-07-03-metar-reversal-expression-matrix-plan-v1.md`
- `docs/analysis/2026-07/2026-07-03-metar-reversal-expression-matrix-v1.md`
- `docs/analysis/2026-07/generated/metar_reversal_expression_matrix_v1/`

Needed cleanup:

1. Keep `metar_reversal_false_fade_reheat_shadow_v1.py` as a branch-specific
   zero-notional recorder.
2. Add or evolve a family-level shadow recorder that journals all sibling
   expressions and all branch signals, not only triggered false-fade/reheat rows.
3. Keep low-price YES V1 live separate; only record cross-family tags such as
   forecast-basis, p_cal, and intraday path state.
4. Re-evaluate after fresh-forward settlements using target-date block bootstrap,
   fill feasibility, maker/taker replay, and selected-vs-complement same
   denominator checks.

## Current Working Model

The clean architecture is:

```text
shared canonical feature layer
  forecast-basis features
  METAR/path features
  market ladder features
  settlement labels

strategy family A: forecast_tail_low_price_yes
  D-1 / early-entry convex sleeve
  V1 tiny live, p_cal/source/station tags in shadow

strategy family B: metar_reversal_expression_matrix
  intraday expression switching
  heat_death branch: inconclusive telemetry
  false_fade_reheat_conflict branch: shadow_candidate

future bridge:
  only if forward evidence shows the same calibrated distribution/EV selector
  explains both families should they be merged into a unified expression engine.
```

Default action remains no live change.
