# Convective Tail challenger preregistration v1

Status: `frozen before challenger evaluation`

Deliverable: a runnable `weather.convective_tail_distribution` zero-notional candidate, not a report-only conclusion.

## Fixed denominator and features

- Signal universe: every legacy HeadA BUY_YES candidate with PIT entry price 5–20c, selected and unselected.
- Probability universe: first PIT state per city/target-date/two-hour lifecycle bucket with at least 80% direct two-sided full-ladder coverage.
- Split: expanding target-date OOF, minimum five prior target dates.
- Baseline: normalized direct YES mids on the same complete ladder rows.
- Continuous features: PIT city/source bias, POP, cloud, wind, humidity plus missingness, forecast dispersion, target-day first-seen warming innovation, instant innovation, remaining forecast warming and local clock sin/cos.
- Training-date-only median imputation and scaling; no city/source/price/weather hard filter. `POP>=50` is descriptive only.

Historical humidity may be fully missing. Its value coefficient must remain neutral while the field and missingness are retained in the runtime contract for forward evidence.

## Challenger queue

Run every challenger in this fixed order; a failure or inconclusive result does not stop the queue.

1. `c1_market_power_temperature`: `q_i ∝ p_market_i^(1/tau(x))`; continuous `tau` bounded to `[0.67,2.5]`, L2 20.
2. `c2_adjacent_kernel_diffusion`: `q=(1-w(x))*p+w(x)*K(p)` with fixed adjacent kernel `[0.25,0.50,0.25]`, continuous `w∈[0,1]`, L2 20.
3. `c3_center_curvature_offset`: `log q_i=log p_i+a(x)z_i+b(x)(z_i²-E_market[z²])`; center bounded to ±2 logits, widening curvature to `[0,1.5]`, L2 30.

Primary metrics are multiclass Brier, logloss, RPS and calibration ECE with target-date block bootstrap and ECMWF/GFS stability.

Shadow selection rule: after all three run, choose the first challenger in fixed order with point improvement in both Brier and logloss and RPS deterioration no worse than 0.001. If none qualifies, freeze C3 only as an explicitly unconfirmed mechanism-complete collector candidate; do not terminate the program as `inconclusive`.

Formal alpha promotion is stricter: Holm-adjusted evidence must beat market on Brier and logloss without material RPS/calibration degradation. Historical selection never authorizes live.

## Runnable output contract

- Inputs: shared market ladder/books, strategy snapshot, forecast curves and observations only.
- Outputs: complete exact-bracket ModelOutput, continuous tail residual, feature/four-clock lineage, structured blockers, and all three predeclared expression candidate ledgers (`single YES`, adjacent strip, bounded three-rung basket).
- Expression ROI is not used for model selection and cannot choose a preferred expression ex post.
- Mode is zero-notional; `TradeIntent`, plan, order and fill outputs are forbidden.

Formal forward review remains frozen at at least 30 new PIT target dates, 80 model-qualified tail tickets and 90% ticket-grain fresh-book coverage, with date bootstrap, PnL concentration, remove-best-date, source stability and same-date frozen Core Carry correlation.
