# Convective Tail challenger preregistration v1

Status: `frozen contract / C1-C5 evaluated / retained for audit`

Deliverable: a runnable `weather.convective_tail_distribution` zero-notional candidate, not a report-only conclusion.

## Fixed denominator and features

- Signal universe: every legacy HeadA BUY_YES candidate with PIT entry price 5–20c, selected and unselected.
- Probability universe: first PIT state per city/target-date/two-hour lifecycle bucket with at least 80% direct two-sided full-ladder coverage.
- Split: expanding target-date OOF, minimum five prior target dates.
- Baseline: normalized direct YES mids on the same complete ladder rows.
- Continuous features: PIT city/source bias, POP, cloud, wind, humidity plus missingness, forecast dispersion, target-day first-seen warming innovation, instant innovation, remaining forecast warming and local clock sin/cos.
- Training-date-only median imputation and scaling; no city/source/price/weather hard filter. `POP>=50` is descriptive only.

Historical weather fields may be partially missing. Missingness remains explicit and no future archive weather may be used as PIT replacement.

## Challenger queue

Run every challenger in this fixed order; a failure or inconclusive result does not stop the queue.

1. `c1_market_power_temperature`: `q_i ∝ p_market_i^(1/tau(x))`; continuous `tau` bounded to `[0.67,2.5]`, L2 20.
2. `c2_adjacent_kernel_diffusion`: `q=(1-w(x))*p+w(x)*K(p)` with fixed adjacent kernel `[0.25,0.50,0.25]`, continuous `w∈[0,1]`, L2 20.
3. `c3_center_curvature_offset`: `log q_i=log p_i+a(x)z_i+b(x)(z_i²-E_market[z²])`; center bounded to ±2 logits, widening curvature to `[0,1.5]`, L2 30.

## Directional continuation frozen after the C1–C3 result

The May-long audit showed that C2 applies nearly universal symmetric widening,
while the hypothesized mechanism is directional: warming innovations should be
able to move probability into the hotter tail without forcing the same change
into the colder tail.  It also showed that policy-specific first-positive
selectors do not provide a same-entry-state expression A/B.  Before evaluating
another outcome, freeze the next challenger and selector contract as follows.

4. `c4_directional_split_tail_offset`:
   `log q_i = log p_i + a(x) z_i + b_hot(x) (z_i^+)^2 + b_cold(x) (z_i^-)^2`,
   where `z_i` is settlement-native bracket distance in ladder steps.  `a(x)`
   is bounded to ±2 logits; both tail terms are non-negative and bounded to
   `[0,1.5]`.  The model uses a low-dimensional physical projection rather
   than a separate coefficient for every raw field: PIT bias/innovation and
   local clock drive center; observed convective intensity, weather-field
   coverage, forecast dispersion and signed innovation drive the two tail
   heads.  Missing weather never removes a row and maps to a zero residual
   contribution plus explicit coverage/reliability inputs.  L2 is 30.

C4 is compared with raw market, C2 and a C4 intercept-only calibration on the
same expanding-OOF states.  It is eligible as the next frozen zero-notional
artifact only when every fold converges.  Historical ROI cannot select C4.

For expression comparison, construct one common entry state per city/target
date: the first state where any registered expression has positive C4 net edge,
provided all three expressions are executable at that state.  Score single
YES, adjacent strip and bounded three-rung basket on that exact shared state,
including expressions with non-positive edge.  The three policy-specific
first-positive ledgers remain diagnostic only and may not be used to rank the
expressions.  This common-entry rule is frozen before the C4 replay.

## C5 continuation frozen after the numerical C4 failure

C4 converged on only five of nine expanding folds and is therefore not a
runnable artifact.  Its full-ladder point estimates also did not beat market.
Do not tune its coefficients, weather thresholds or city/source slices.  The
next mechanism reduces the directional tail hypothesis to one-step probability
transport:

5. `c5_directional_neighbor_transport`:
   `q = (1-w(x)) p + w(x) [(1-s(x)) K_cold(p) + s(x) K_hot(p)]`, where
   `w(x) in [0,1]`, `s(x) in [0,1]`, and `K_hot` / `K_cold` each retain half
   the mass at the original rung and move half by one settlement-native ladder
   step in the named direction, with boundary mass retained.  The width head
   uses the same seven-dimensional convective/coverage/dispersion projection as
   C4; the direction head uses the same eight-dimensional PIT bias/innovation/
   clock projection.  L2 is 20.  This is not a threshold model: C2 is recovered
   when directional share is 0.5, and missing weather rows remain in the full
   denominator with zero physical residual plus explicit coverage.

C5 must converge on every fold and is evaluated on the unchanged OOF states,
raw market baseline and common-entry expression contract.  No C4 ROI or slice
is used to define C5.

Primary metrics are multiclass Brier, logloss, RPS and calibration ECE with target-date block bootstrap and ECMWF/GFS stability.

Shadow selection rule: after all three run, choose the first challenger in fixed order with point improvement in both Brier and logloss and RPS deterioration no worse than 0.001. If none qualifies, do not terminate the program as `inconclusive`.

Post-fit execution-safety amendment: a runnable artifact must have converged on every expanding-date fold. The original mechanism-complete C3 fallback is therefore ineligible when its solver does not converge; among fully converged challengers, freeze the best aggregate Brier/logloss/RPS rank as an explicitly unconfirmed collector candidate. This amendment cannot use ROI and does not change the probability denominator or challenger results. In the May-long run C3 converged on 0/9 folds, while C1/C2 converged on 9/9, so C2 is the safe fallback.

Formal alpha promotion is stricter: Holm-adjusted evidence must beat market on Brier and logloss without material RPS/calibration degradation. Historical selection never authorizes live.

## Runnable output contract

- Inputs: shared market ladder/books, strategy snapshot, forecast curves and observations only.
- Outputs: complete exact-bracket ModelOutput, continuous tail residual, feature/four-clock lineage, structured blockers, and all three predeclared expression candidate ledgers (`single YES`, adjacent strip, bounded three-rung basket).
- Expression ROI is not used for model selection and cannot choose a preferred expression ex post.
- Mode is zero-notional; `TradeIntent`, plan, order and fill outputs are forbidden.

Formal forward review remains frozen at at least 30 new PIT target dates, 80 model-qualified tail tickets and 90% ticket-grain fresh-book coverage, with date bootstrap, PnL concentration, remove-best-date, source stability and same-date frozen Core Carry correlation.
