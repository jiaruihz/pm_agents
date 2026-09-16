# Ladder Mass Transport v1 — time-aligned replay

Status: `REJECTED_FOR_EXPRESSION`

Run: `ladder_mass_transport_time_aligned_20260809`

Artifact: `/Volumes/jrs-archive/pm_agents/research/artifact_store/market_structure_edge/ladder_mass_transport_time_aligned_20260809`

## Conclusion

The May history correction changes the old conclusion. Cross-rung ladder-state
transition has a real 60-minute rung-relative markout increment on the frozen
historical validation denominator, but the residual does not survive executable
spread and fee costs. It is model evidence, not a runnable strategy.

- Probability/markout gate: **PASS**. Frozen `M2 ladder transition, Ridge
  alpha=100` beats M0, M1, and static-kink on the same 23,887 rungs / 3,174
  snapshots / 7 target dates / 47 cities. All target-date bootstrap MSE-delta
  CIs are below zero.
- Economic gate: **FAIL**. The fixed one-share, two-leg taker-entry/taker-exit
  expression loses `$53.1412`, ROI `-2.8035%`, target-date CI
  `[-$81.5042,-$29.3361]`; all 7 dates lose.
- Maker-only evidence is conditional, not executable. Double-maker is positive
  only under unobserved fills; future touch is never counted as a fill. No
  strategy, shadow process, order, or fill was created.

## Corrected time contract and denominator

| Phase | Window | Role | Fixed panel |
|---|---|---|---:|
| historical training | 2026-05-19..2026-07-10 | immutable archived paper ladders, fit only | 874,281 rungs / 53 dates |
| development | 2026-07-11..2026-07-21 | expanding-date OOF selection | 15,729 rungs / 11 dates |
| frozen historical validation | 2026-07-22..2026-07-28 | one frozen score; previously seen by kink research | 23,887 rungs / 7 dates |
| true untouched forward | 2026-08-11..2026-08-17 | not opened as of 2026-08-09 | not used |

The final fixed panel contains 913,897 rows, 111,912 snapshots, 71 dates, and
48 cities. The historical adapter materialized 178,810 sampled ladders from the
May–July archive and preserves feature and future-evaluation snapshot identity.
The original July-only run is superseded; it is not evidence for rejection.

Coverage gaps remain explicit: 1,376 fixed rows lack forecast, 893,321 lack an
intraday observation, and no row lacks settlement. Historical WS sequence
parity, trade prints, queue turnover, and reconstructable maker fills are absent,
so M4 order-flow remains blocked without blocking the M0–M3 snapshot test. This
also means the confirmed increment is specifically `M2 ladder transition`, not
a confirmed weather-event-response-lag effect; M3 was not selected.

## Development freeze

All five fixed blocks and three registered ridge alphas were compared on the
same 11 development dates. M2 alpha 100 was the best eligible mass-transport
arm (`MSE 0.00099469`); M1 alpha 100 was slightly better overall
(`0.00099090`) but is a baseline, not the candidate mechanism. M2-versus-M0
development delta was `-0.00001916`; the one-sided bootstrap p-value was
`0.02099`, Holm-adjusted `0.12596`. Selection therefore relied on the registered
mechanism and the one-shot frozen validation, not on a development significance
claim or validation slice search.

## Frozen probability and markout results

Primary 60-minute metrics:

| Model | relative MSE | direction Brier | direction logloss | direction AUC | settlement Brier | settlement logloss |
|---|---:|---:|---:|---:|---:|---:|
| M0 market-level | 0.00118955 | 0.235526 | 0.664047 | 0.47957 | 0.793910 | 1.866796 |
| M1 weather + market | 0.00116173 | 0.223630 | 0.642607 | 0.50901 | 0.779842 | 1.818626 |
| **M2 ladder transition** | **0.00115401** | **0.220988** | **0.635747** | **0.51288** | **0.775080** | **1.803193** |
| M3 response lag | 0.00115524 | 0.220169 | 0.633813 | 0.51767 | 0.772248 | 1.794870 |
| static kink control | 0.00118871 | 0.234521 | 0.662002 | 0.50350 | 0.791162 | 1.856610 |

M2 paired 60-minute MSE deltas:

| Control | delta | target-date 95% CI |
|---|---:|---:|
| M0 | -0.00003554 | [-0.00005365, -0.00002017] |
| M1 | -0.00000772 | [-0.00001226, -0.00000173] |
| static kink | -0.00003470 | [-0.00005351, -0.00001903] |

Direction Brier/logloss and settlement Brier/logloss deltas are also negative
against all three controls. The M2-versus-M1 increments are
`-0.002642/-0.006859` for direction and `-0.004762/-0.015433` for settlement.
The 30-minute secondary head improves clearly versus M0/static kink, while its
MSE delta versus M1 crosses zero; it is not used to promote the strategy.

Robustness is not driven by an allowlist: daily MSE delta is negative on 7/7
validation dates, per-city delta is negative in 47/47 cities, every leave-one-city-out
pooled sensitivity remains negative, and all nine spread×depth regimes have a
negative delta. No city was selected after validation.

## Fixed execution replay

Selector: within each snapshot, take the maximum minus minimum M2 predicted
rung-relative markout; when positive, pair long YES on the max rung with long
NO on the min rung, and take the first non-overlapping cross per
city-date-event. Hold 60 minutes, one share per leg. Signal funnel:

`3,174 snapshots → 2,926 constructed/positive pairs → 1,958 non-overlapping
signals → 1,958 four-style quote rows → 1,872 rows with one-share depth on all
legs`.

Official taker fee is `0.05 × price × (1-price)` per share per taker fill.

| Expression | PnL | ROI | target-date 95% CI | Fill evidence |
|---|---:|---:|---:|---|
| taker entry + taker exit | -$53.1412 | -2.8035% | [-$81.5042,-$29.3361] | executable top-of-book replay |
| maker entry + taker exit | +$5.4733 | +0.2980% | [-$1.6142,+$13.3300] | unknown; median break-even fill 105.7% |
| taker entry + maker exit | -$2.0596 | -0.1087% | [-$13.7165,+$10.5563] | unknown; median break-even fill 107.6% |
| maker entry + maker exit | +$56.5550 | +3.0787% | [+$29.6581,+$86.3084] | unknown both sides; median joint break-even 53.5%, p90 89.7% |

Taker/taker needs 2.839 cents of joint pair improvement per expression to break
even (1.419 cents per entry-only or exit-only leg; 0.710 cents per leg if all
four fills improve). A 0.5-cent adverse-selection penalty per maker leg reduces
double-maker PnL to `$19.1150`; a 1-cent penalty makes it `-$18.3250`.
Unfilled maker-exit fallback is not rescued: taker unwind equals the
`-$53.1412` primary result and hold-to-settlement is `-$23.9510`.

The same-snapshot generic ladder-maker control loses `$4.9599` under conditional
maker-entry/taker-exit pricing. Candidate selection is directionally better than
generic making, but its `+$5.4733` CI crosses zero and the required fills are
unobserved. Double-maker profitability alone is explicitly insufficient.

Taker losses are broad rather than winner noise: 7/7 dates lose; 46/47 cities
lose; largest absolute date and city shares are 33.4% and 5.14%.

## Final action

Stop strategy creation for this expression. Preserve M2 as model evidence and,
only after real trade/queue/order-lifecycle capture is ready, run one same-fill
comparison of candidate-selected passive quotes versus the generic ladder-maker
baseline. Do not create `SignalCandidate`, `TradeIntent`, shadow, or live
configuration from future-touch maker prices.

Reproduce through the existing runner:

```bash
.venv/bin/python weather_model_evaluation/lmvm_repricing_challenger.py \
  --mass-transport-db runtime/weather.db \
  --mass-transport-history-snapshot-dir /Volumes/jrs-archive/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots \
  --mass-transport-history-cache /tmp/ladder_mass_transport_time_aligned_20260809.sqlite \
  --output-dir /Volumes/jrs-archive/pm_agents/research/artifact_store/market_structure_edge/ladder_mass_transport_time_aligned_20260809 \
  --draws 3000
```
