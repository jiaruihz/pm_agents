# HeadA Shadow Distance-Enforcement Audit v1

Generated: 2026-07-24

## Verdict

`low_price_yes_lottery_tiny_live_v1` was not applying its approved `dist>0`
hot-tail boundary to live-snapshot candidates. The snapshot did not contain
geometry fields, and the runner neither calculated nor copied them into the
validation record. This is an implementation defect, not new alpha.

```text
affected would-live window = target_date 2026-07-16..2026-07-24
captured would-live entries = 86
incorrectly admitted dist<=0 = 18 (16 settled, 2 unsettled)
intended dist>0 entries = 68 (59 settled, 9 unsettled)

corrected settled performance = +12.1% ROI, 8/59 wins
target-date block-bootstrap 95% CI = [-55.7%, +70.1%]
conclusion = inconclusive
action = fix the implementation; stay zero-notional shadow; do not add a new alpha gate
```

The 16 settled incorrect admissions included 3 winners and 13 losers. Their
captured-only PnL was `+$3.13` on `$11.87` cost, which is why retaining the
75-row headline would overstate the intended strategy even though the defect
did not mechanically create a loss.

The runner fix calculates bracket geometry from the same snapshot's
`bracket` and `forecast_max_native` before validation. It also changes the
execution dedupe from `(city, date, bracket, condition)` to one HeadA entry
per `(city, target_date)`: a later bracket switch is a revision of one thesis,
not a second independent ticket.

## Why Recent Volume Rose

The frozen entry configuration has been unchanged since 2026-07-14: ask
`0.05..0.20`, raw edge `>=0.20`, fee-adjusted edge `>=0.15`, and snapshot
hours-to-settlement `22..24`. The increase is not a configuration widening.

| target date | captured entries | correctly hot `dist>0` | incorrectly admitted `dist<=0` |
|---|---:|---:|---:|
| 2026-07-16 | 8 | 8 | 0 |
| 2026-07-18 | 1 | 0 | 1 |
| 2026-07-19 | 5 | 5 | 0 |
| 2026-07-20 | 12 | 11 | 1 |
| 2026-07-21 | 14 | 9 | 5 |
| 2026-07-22 | 20 | 15 | 5 |
| 2026-07-23 | 15 | 11 | 4 |
| 2026-07-24 | 11 | 9 | 2 |

So the defect explains five of the 20 entries on 7/22, but not all of the
density. The remaining 15 are distinct city-day forecast-tail opportunities.
Their common driver is the frozen raw condition `model_p_yes - ask`: when the
uncalibrated model assigns a high probability to several cheap brackets on one
forecast cycle, all pass together.

There was also one captured same-city duplication: Mexico City on 7/22 changed
from bracket `28` to `29` between snapshots. The first was `dist=-0.4` and is
removed by the repaired boundary; the city-date dedupe still fixes the general
execution defect for future bracket changes.

## Corrected Same-Denominator Performance

Primary execution is a hypothetical taker fill at captured fresh best ask,
plus the official Weather fee `0.05 * price * (1-price)`. There are no actual
HeadA fills in this zero-notional window.

| metric | captured cohort | intended `dist>0` cohort |
|---|---:|---:|
| settled rows / dates / cities | 75 / 7 / 39 | 59 / 6 / 34 |
| wins / win rate | 11 / 14.7% | 8 / 13.6% |
| average ask | 12.2c | 11.6c |
| cost / fee / PnL | $47.56 / $1.97 / +$7.44 | $35.69 / $1.49 / +$4.31 |
| fee-adjusted ROI | +15.6% | +12.1% |
| target-date bootstrap CI | [-22.1%, +60.6%] | [-55.7%, +70.1%] |
| market-fee baseline ROI | -4.1% | -4.2% |
| excess ROI / CI | +19.8pp / [-18.0, +64.7] | +16.2pp / [-51.5, +74.2] |
| losing days / `<=-50%` days | 3 / 1 | 2 / 2 |
| maximum daily loss | -$3.78 | -$5.66 |
| top-ticket-removed ROI | +6.3% | -0.5% |

The corrected point estimate remains positive, but the confidence interval is
wide, only eight tickets won, and removing the largest winner removes the
positive ROI. It is not evidence to restore live sizing or to promote any
source filter.

| target date | rows | wins | PnL | ROI |
|---|---:|---:|---:|---:|
| 2026-07-16 | 8 | 2 | +$4.68 | +87.8% |
| 2026-07-19 | 5 | 1 | +$2.49 | +99.4% |
| 2026-07-20 | 11 | 3 | +$7.16 | +91.3% |
| 2026-07-21 | 9 | 0 | -$4.98 | -100.0% |
| 2026-07-22 | 15 | 2 | +$0.61 | +6.5% |
| 2026-07-23 | 11 | 0 | -$5.66 | -100.0% |

## What Is Worth Improving

### 1. Calibrate the probability before using it as an entry residual

On the corrected 59 settled rows, mean `model_p_yes` is 39.2% but realized win
rate is 13.6%. Market ask is 11.6%; it has better Brier (`0.113` vs `0.186`)
and log loss (`0.374` vs `0.559`). The current raw edge therefore makes a
large number of tickets look attractive whenever the model becomes confident.

This is the highest-value research head: freeze a target-date-safe calibration
fit on historical rows, compute `p_calibrated - ask - official_fee`, and write
it as a sibling shadow score. It must be evaluated against the identical
captured city-date snapshots, with date-block bootstrap and frozen forward.
It is not a new threshold to add now.

The small current slice supports the concern but not a cut: within the 59 rows,
raw fee-edge `>=0.35` had 0/10 wins, while the middle `0.25..0.35` range had
5/17. That is directionally inconsistent with treating raw model edge as an
amount of confidence, but it is still an after-the-fact diagnostic.

### 2. Keep source risk as a shadow explanation, not a GFS ban

Corrected ECMWF rows were 8/37 and +81.7% ROI; corrected GFS rows were 0/22
and -100.0%. This is a sharp warning, but it spans only six target dates and
is entangled with that week's weather. A GFS exclusion now would be a
post-hoc source whitelist. The runner should preserve the per-city assigned
source and write the existing source-quality/risk tags for a frozen forward
comparison.

### 3. Treat density as portfolio exposure, not an alpha gate

There is no daily cap because a cap would not explain which ticket is wrong.
The durable improvement is to journal per-target-date candidate count,
city-date dedupe status, forecast source, raw edge, calibrated sibling score,
and aggregate hypothetical notional. This makes clustered forecast cycles
visible without pretending that a hand-picked count cap creates alpha.

## Implementation And Evidence

- `scripts/ops/low_price_yes_lottery_tiny_live.py` now materializes shared
  `bracket_distance_features` before selection and blocks `dist<=0` before
  token/book requests.
- The same runner now dedupes one HeadA entry per city and target date across
  snapshots.
- Regression coverage verifies both a cold-ticket block and bracket-agnostic
  city-date dedupe.
- `scripts/analysis/forecast_quality/evaluate_heada_would_live_shadow_v1.py`
  now recomputes geometry from the recorded snapshot fields and reports both
  captured and intended-policy cohorts, so future audits cannot silently
  reproduce this denominator error.

Artifacts: `docs/analysis/2026-07/generated/heada_would_live_shadow_v1/`.
