# Helsinki city-probability zero-notional shadow v1

## Status

`deployed zero-notional shadow / forward evidence collecting / no live change`

This is the forward evidence chain for the frozen Helsinki remaining-heat market
expression. It evaluates both the incumbent offset-fade artifact and the research
challenger on every newly archived FMI/current-bracket book checkpoint. It records all
evaluations, including negative-edge and dust-price rows; the first positive edge per
`city × target_date × bracket × model` is recorded as a paper intent with zero shares
and zero notional.

The runtime is deliberately model-agnostic. `ShadowRuntime` owns deduplication, official
weather fee treatment, signal/evidence journals and the zero-order invariant. A city
adapter owns source clock, settlement lattice, physical features and artifact inference.
Adding another city therefore requires a new adapter/profile, not another execution
chain; model features are not forced to be identical across cities.

## Helsinki inputs and known parity gap

- FMI airport observations: 10-minute first-seen journal.
- EFHK METAR: settlement-facing running maximum and current official state.
- Forecast: collector-exact ECMWF hourly curve available before the decision.
- Market: continuous active-ladder current-bracket NO direct CLOB book.
- Frozen artifacts: v5 coherent weather hazard, fade morphology, incumbent
  `offset_fade_v1`, and challenger `market_expression_v2`; every artifact is SHA-locked.
- The current FMI live collector does not provide radiation. The runtime records
  `global_radiation_slope_30m` as missing and reports expression feature coverage instead
  of claiming full feature parity. Frozen median handling remains unchanged.

## First smoke checkpoint

At the 2026-07-31 12:20 UTC FMI checkpoint, the current official bracket was 26°C and
the direct 26-NO book was 0.001/0.008. The incumbent/challenger estimated 0.0034% and
0.1447%; after the official fee both edges were negative, so neither produced a paper
intent. Two evaluations were written, zero orders were submitted, and the unit suite
passed 3/3.

The four pre-deployment smoke evaluations are explicitly excluded by
`data_quality_adjustments.jsonl`: two were produced before the direct active-book fix and
two were valid smoke checks but preceded the freeze. Frozen forward begins at
2026-07-31 12:43 UTC. The adapter now also fails closed when the active book is over 15
minutes old or when the official observation clock is later than the book clock.

Promotion remains governed by the frozen-forward requirements in the Helsinki model
reports. Shadow collection does not authorize live trading.

## 2026-08-01 audit of the first frozen-forward target date

Audit grain: `Helsinki × target_date=2026-07-31 × first-seen FMI observation checkpoint
× model`. Frozen forward starts at 2026-07-31 12:43 UTC. The raw source/book window is
audited separately from successful model evaluations; retry errors are not counted as
signal rows.

Production identity at 2026-08-01 04:01 UTC was `status=warning` with a healthy
canonical DB route. The warning was an unregistered tmux-session inventory issue, not a
split DB. The current shadow process remained zero-notional. The focused runtime tests
passed 8/8.

### Result

The source and direct-book collectors are sufficiently continuous, and the six written
evaluations are PIT-consistent. The end-to-end frozen-forward evidence chain is **not yet
complete**: after the current-bracket NO book entered its normal near-binary one-sided
state, the adapter failed before writing a checkpoint-level coverage/evaluation row. This
is not a missing/corrupt book: at 13:06 UTC the paired books were `26 YES bid=0.998 / no
ask` and `26 NO no bid / ask=0.002`, with the same 978.75-share size; the later NO ask of
0.001 corresponds to YES bid 0.999. The fail-closed behavior is safe for trading but
misclassifies an informative market state and loses most of the intended ten-minute
research denominator.

Signal funnel:

- 38 FMI checkpoint states were available to the active-book chain from the carried
  12:40 UTC observation through 18:50 UTC; 37 were newly first-seen after the freeze.
- The active-book journal contains 323 valid 26-NO book rows spanning all 38 source
  checkpoints.
- Only 3 source checkpoints produced model evaluations, two models per checkpoint, for
  6 evaluation rows.
- All 6 edges were negative; Helsinki produced 0 paper intents and 0 orders.

Evidence funnel:

- 323/323 book rows had a buyable NO ask.
- 9 rows across 3 source checkpoints were two-sided. The remaining 314 rows across 36
  source checkpoints were ask-only; the two sets overlap at the transition checkpoint.
- The runner emitted 447 one-sided-book errors from 13:10 through 20:59 UTC instead of
  one structured coverage row per new FMI checkpoint.
- `fact_signal_candidates` and canonical `settlement_outcomes` contained no Helsinki
  2026-07-31 rows at audit time. Official Polymarket market `3196720` was resolved YES=1,
  so the evaluated 26-NO label is 0, but this settlement had not entered the canonical
  shadow lineage.
- Actual fills remain 0 by design.

### Successful PIT checkpoints

Times below are UTC; Helsinki local time is UTC+3.

| FMI observation | decision/book time | NO bid/ask | incumbent p / edge after fee | challenger p / edge after fee |
|---|---|---:|---:|---:|
| 12:40 | 12:45:55 | 0.001 / 0.008 | 0.00243% / -0.83725pp | 0.10516% / -0.73452pp |
| 12:50 | 12:52:55 | 0.001 / 0.006 | 0.00158% / -0.62824pp | 0.07638% / -0.55344pp |
| 13:00 | 13:04:56 | 0.001 / 0.002 | 0.00066% / -0.20932pp | 0.03595% / -0.17403pp |

All three decisions used books after source first-seen, observations only 2.9–5.9 minutes
old, and an official METAR timestamp no later than the book. `decision_ts_utc` equals the
recorded book clock. Feature coverage was 92.31–94.74%; the only declared missing feature
was `global_radiation_slope_30m`. Four earlier smoke rows are explicitly excluded by
`data_quality_adjustments.jsonl`, and all six retained evaluation IDs are unique.

With final 26-NO label 0, the mechanical three-checkpoint scores were: market
Brier/logloss `0.00001158/0.00317247`, challenger `0.00000061/0.00072528`, and incumbent
`0.000000000295/0.00001558`. These are one target date and are operational smoke evidence,
not a model-quality or alpha conclusion.

### Lineage gaps to repair before this counts as a clean forward day

1. Treat one-sided near-binary books as a market-state feature, not a data error. A buyable
   ask should remain an executable-cost observation, while midpoint/market probability is
   explicitly interval-censored/unavailable. Fetching the paired YES token confirms the
   complement (`YES bid = 1 - NO ask`) but does not create the missing opposite quote. Do
   not synthesize a bid or midpoint. If a frozen market-offset model cannot score without a
   midpoint, write one structured `not_scorable_one_sided_near_binary` row per source
   checkpoint instead of a per-minute error storm.
2. Persist per-row source payload/hash, forecast run/availability/hash, model artifact SHA,
   config identity, runner code SHA, and stable book snapshot ID. They can currently be
   reconstructed from several raw/config files but are not self-contained in an evaluation.
3. Materialize shadow opportunities/evaluations into the canonical opportunity grain and
   join settlement. The current chain stops at raw `evaluation → paper_intent`; it does not
   yet reach canonical `fact_signal_candidates → settlement`.
4. Preserve the existing zero-order invariant and first-positive position deduplication.
   No live behavior change is authorized by this audit.

Qualification: collector continuity PASS; observed-row PIT parity PASS; zero-notional
safety PASS; full ten-minute evaluation coverage FAIL; canonical settlement lineage FAIL;
frozen-forward model/performance qualification FAIL. The 2026-07-31 date must be reported
as a partial-coverage operational day, not as a clean forward scoring day.

## 2026-08-01 implementation and counterfactual replay

The runner now treats an ask-only or bid-only near-binary book as a normal quote state.
It records executable ask cost independently from market probability, represents the
probability as an interval, and emits a structured `not_scorable` evaluation when a frozen
market-offset artifact requires an unavailable point midpoint. It neither synthesizes a
midpoint nor creates a paper intent from an unscored row. Evaluation rows now also carry
source/forecast payload hashes, artifact SHA, profile identity, and a stable book snapshot
ID.

Replaying the immutable 2026-07-31 journals from the 12:43 UTC freeze produced:

- 38/38 FMI source checkpoints replayed, with 76 unique model rows and zero errors;
- 3 two-sided checkpoints / 6 scored rows, exactly reproducing the original probabilities
  (`max_abs_probability_diff=0`);
- 35 one-sided near-binary ask checkpoints / 70 structured `not_scorable` rows;
- 76/76 PIT source, official-observation, and forecast clocks, and 76/76 complete lineage
  hashes;
- zero `would_enter`, zero paper intents, and zero orders.

Thus the affected window is 2026-07-31 13:10–20:59 UTC. The defect created 447 retry
errors and suppressed 35 checkpoint records, but changed no trade decision, fill, or PnL:
under the corrected code all 35 checkpoints are retained as valid interval-censored market
evidence and remain non-signals. The replay artifact is
`docs/analysis/2026-08/generated/helsinki_shadow_near_binary_replay_v1/`.

The standard incremental settlement refresh subsequently imported all 11 Helsinki
2026-07-31 brackets into canonical `settlement_outcomes`: bracket 26 has final YES=1 and
the other ten brackets have final YES=0. Shadow evaluations themselves are still sourced
from the immutable raw journal/replay artifact rather than materialized as canonical
`fact_signal_candidates`; this remaining canonical opportunity-ingest gap does not affect
the zero-order counterfactual above, but it prevents calling the storage lineage fully
closed.

## Full-day retrospective PIT audit

The late-afternoon forward window is a deployment/freeze boundary, not a full-day data
boundary. On 2026-07-31 the collectors actually retained 90 unique FMI checkpoints and
2,375 active-ladder book rows from 04:00–18:50 UTC (07:00–21:50 Helsinki time), covering
the observed 20→21→22→23→24→25→26 progression. The frozen shadow began only at 12:43 UTC,
after the official running maximum had already reached 26; therefore its 38 as-recorded
forward checkpoints test terminal remaining heat at 26, not the morning climb.

A retrospective replay using only each checkpoint's then-available FMI, METAR, ECMWF and
book state recovered 87/90 checkpoints. The first three lacked either four prior FMI
prints or an exact bracket because the market's lower bucket was `20 or below`. This
replay is PIT research evidence, not as-recorded forward evidence.

- Weather head, 87 checkpoints: accuracy 97.70%, Brier 0.02148, logloss 0.10156. It was
  correct at all 20–24 checkpoints, 8/10 at bracket 25, and 54/54 at bracket 26.
- Expression evidence: 39 two-sided checkpoints / 78 scored model rows and 48 one-sided
  checkpoints / 96 structured `not_scorable` rows. Incumbent accuracy/Brier/logloss was
  97.44%/0.03741/0.13408; challenger was 100%/0.04625/0.17059; same-row market was
  100%/0.05059/0.19011.
- The replay produced 43 positive-edge rows and 12 model-intents after per-model position
  deduplication. These are six economic bracket entries (21–26 NO), each recorded once for
  the incumbent and once for the challenger A/B; they must not be aggregated as 12 trades
  from one strategy. Each model has six intents, five wins and one 26-NO loss. At the
  standard five-share research size and official fee, either model separately produces
  +$0.23 on $24.77 cost (ROI 0.93%): the meaningful 25-NO gain is largely given back by
  the incorrect 26-NO entry. They are counterfactual, not actual orders or fills.

The full-day artifact is
`docs/analysis/2026-08/generated/helsinki_shadow_full_day_exact_bracket_pit_replay_v1/`.
