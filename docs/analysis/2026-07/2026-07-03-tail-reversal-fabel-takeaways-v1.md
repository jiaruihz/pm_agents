# Tail Reversal Fabel Takeaways v1

Generated: 2026-07-03

## One-Line Read

Fabel's review should be absorbed back into the original `runway` idea, not treated as a new `anchoring` strategy. The useful takeaway is: **runway is not enough by itself; the expression depends on whether the market has already priced the runway**. If the market still treats current as live, runway may favor one-step `d1 YES`. If the market has already abandoned current, `d1 YES` can be the wrong expression, and the research should move to `d2/high-tail/basket/avoid`.

Scope note: this document is supporting material for Head B (`metar_reversal` /
runway d1 YES reversal). The current cross-family map is
`docs/analysis/2026-07/2026-07-03-tail-strategy-family-map-v1.md`. Do not use
this note to merge Head B into the Head A forecast-tail low-price YES + TP20
lottery sleeve.

## What To Keep

### 1. Upgrade The Original Runway Idea

The original idea remains the center:

```text
runway exists
+ market has not fully priced the break
+ d1 YES is still cheap
=> runway_d1_yes_reversal
```

Fabel's contribution is not a new strategy family. It is a refinement of runway:

```text
physical runway:
  forecast/METAR still allow a higher bracket

market runway pricing:
  has the orderbook already conceded that higher bracket?

path shape:
  one-step / skip-over / fake runway

expression:
  d1 YES / d2 YES / high-tail YES / current bracket NO / basket / avoid
```

This is the research axis to preserve.

### 2. "Anchored" Is Just One Runway Sub-State

When Fabel says `current_high_yes_ask >= 0.40` matters, read it as:

```text
runway exists
+ market still assigns meaningful probability to current high
=> market may only be underpricing a one-bracket break
=> d1 YES can be a clean expression
```

So the branch name should be `runway_one_step_reversal` or `runway_d1_yes_reversal`, not `anchoring strategy`.

### 3. Conceded Current Tells Us "Not d1", Not "No Tail"

The most useful negative result is:

```text
physical runway exists
+ current_high already cheap / market has conceded current
+ buy d1 YES
= bad
```

Interpretation: in conceded runway states, `d1 YES` is probably the wrong expression. The market may be pricing a skip-over path, where final max jumps to d2 or beyond. That means the next question is not "is tail reversal dead?", but:

```text
In runway states where current is already conceded, is d2 YES, high-tail YES, or a hotter basket priced better than d1 YES?
```

Fabel's result narrows an expression, not the full alpha search.

### 4. Trigger Frequency Is A Real Forward Metric

The "trigger hunger" point is useful. If a historically good state stops appearing, we should not loosen conditions just to create trades. We should record state frequency:

```text
runway_one_step candidate count per day
runway_conceded / skip-over candidate count per day
d2/high-tail candidate count per day
fake-warming count per day
```

This tells us whether a strategy is dormant because the weather/market state disappeared, or whether the alpha itself failed.

### 5. TP20 Needs Regret Accounting

For forecast-tail low-price YES, TP20 remains an execution overlay, not entry-alpha proof. The useful Fabel addition is to record:

```text
saved_loss:
  sold at 0.20, final settled 0

capped_winner_regret:
  sold at 0.20, final settled 1

missed_touch:
  snapshot/fresh book touched 0.20 but maker did not fill
```

This directly answers whether TP20 improves the convex sleeve or just sells away the few big winners.

### 6. Execution Is Part Of The Research, Not Afterthought

For both heads, record:

```text
fresh book ask/bid vs research snapshot
best ask size
spread
maker fill / no fill
taker fallback price
```

This matters because tail tickets often look great in replay but live fills may be thin or stale.

## What Not To Over-Absorb

### 1. Do Not Turn 0.40 Into A Sacred Gate Or A New Direction

The exact `0.40` threshold is not sacred. It can be frozen for shadow comparison, but future research should prefer a continuous `market_current_state` / `runway_pricing_state` score over a permanent hard gate:

```text
current_high_yes ask
current_high_yes bid
spread
depth
recent price drift
```

This belongs inside runway research as a market-pricing feature.

### 2. Do Not Declare Skip-Over Tail Dead

The tested expression was mostly `d1 YES`. A bad `d1 YES` result in conceded states does not disprove:

```text
d2 YES
high-tail YES
multi-hotter basket
avoid / no-trade classifier
```

So the right label is:

```text
runway_conceded_d1_yes: weak / likely bad expression
runway_conceded_hotter_tail: still open
```

### 3. Do Not Let Head B Replace Head A

Forecast-tail low-price YES and METAR tail reversal are related but separate:

| Head | Timing | Core alpha hypothesis | Current role |
|---|---|---|---|
| Forecast-tail low-price YES | pre-position / earlier decision | station-basis + model tail probability + cheap convex ask | tiny live + TP20 overlay |
| METAR runway/tail reversal | intraday | observed path conflicts with market-implied heat path | shadow research |

One should not silently become a filter for the other until same-denominator forward data proves that relationship.

### 4. Correct The Data Coverage Note

The statement "2026-07-01 has no orderbook snapshots" is obsolete. The missing data was caused by syncing the legacy `weather-predict` source. After syncing from `weather_data_feed_service_runtime`, 2026-07-01 has local orderbook/paper snapshots and 7/1 feature rows.

Remaining limitation:

```text
2026-07-01 can enter expression matrix / telemetry.
2026-07-01 cannot be counted as canonical settled PnL until settlement_outcomes is complete.
```

## Research Frame Going Forward

The tail reversal map should be:

| State | Description | Candidate expression | Status |
|---|---|---|---|
| forecast-tail pre-position | Before/in early day, model/station basis says cheap hotter YES has convex value | low-price hotter YES | tiny live / shadow_candidate |
| runway one-step reversal | Physical runway exists and market has not fully priced the one-bracket break | d1 YES, maybe current bracket NO | shadow_candidate |
| runway skip-over / conceded current | Physical runway exists but market has already killed current; path may jump multiple brackets | d2 YES / high-tail YES / hotter basket / avoid | open research |
| fake runway | METAR trend or forecast runway looks hot but remaining heat/regime cannot sustain break | avoid or sell/TP existing lottery | open research |

This keeps the original idea intact: **find when hotter outcomes are underpriced, then choose the expression that matches the path shape**.

## Next Durable Experiment

Run a corrected `tail_reversal_expression_matrix` on the expanded data-feed mirror:

```text
same city-date-snapshot denominator
classify market-belief state:
  current_live / current_conceded / neutral
classify runway state:
  no_runway / one_step_runway / skip_over_runway / fake_runway
classify realized path:
  no break / d1 / d2 / high-tail
compare expressions:
  d1 YES
  d2 YES
  high-tail YES
  current bracket NO
  hotter basket if available
separate canonical-settled PnL from unsettled telemetry
```

This is the clean next step. It absorbs Fabel's useful state/expression insight without shrinking the project into a new anchoring rule.
