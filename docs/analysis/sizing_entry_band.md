# Sizing And Entry Band

> Living doc for module [3]: entry price windows, side-specific bands, and position sizing.
> Current status: `design-draft` for future changes; current live sizing/bands remain defined by strategy entrypoint/config.
> Last updated: 2026-06-09 Phase 4D timing/sizing pilot.

## Current Conclusion

Entry band and sizing analysis should be treated as decision-layer research, not as a generic PnL slice. The core question is whether a band or size curve improves risk-adjusted executable edge after controlling for city, side, target-date, and price bucket.

Any proposed band or sizing change must separate reserved notional, raw fill cost, strict fill cost, realized PnL, and open exposure.

Phase 4D absorbed the May entry-band/sizing reports into the current rule:

1. **`entry_price_window` is a signal acceptance filter, not the CLOB posting price.** Old UNKNOWN vs mid evidence remains useful only for this mechanism: the 0.25-0.75 filter removed many low-quality lottery or over-expensive signals.
2. **Filled-only band tables are not enough.** May reports repeatedly show filled rows and opportunity rows can disagree; use `fact_signal_candidates` for opportunity/counterfactuals and `fact_trades` for realized fills.
3. **Side x price x edge beats a single global band.** The stable design lesson is not "move 0.25-0.75 to one new range"; it is to evaluate BUY_NO, BUY_YES, price bucket, and edge bucket separately.
4. **Sizing is currently a risk-budget problem, not a proven alpha multiplier.** EV-tier sizing may raise dollar return by taking more variance; Kelly-style concentration failed as a robust live rule in the May study.
5. **Execution size consistency is a first-order issue.** The May sizing study found actual fill cost was not clean flat $5 and losing fills received more capital; this must be separated from intentional sizing rules.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-05/2026-05-30-performance-entry-band-research.md` | 2026-05 | entry band side x price bucket EV | snapshot |
| `docs/analysis/2026-05/2026-05-30-performance-sizing-and-band-distribution.md` | 2026-05 | sizing x entry band distribution and anti-overfit check | snapshot |
| `docs/analysis/2026-05/2026-05-27-compare-execution-algorithm-window-filter.md` | 2026-05 | `entry_price_window` as pre-trade signal filter | historical-mechanism |
| `docs/analysis/2026-05/2026-05-29-strategy-entry-band-and-execution-quality.md` | 2026-05 | filled-vs-opportunity band disagreement and execution selection bias | historical-mechanism |
| `docs/analysis/2026-06/2026-06-07-v1-ecmwf-blocked-side-band-overlay.md` | 2026-06 | blocked ECMWF city side-band overlay; shadow only | snapshot |
| `docs/WEATHER_ENTRY_BAND_AND_SIZING_DESIGN.md` | design | target design for entry bands and sizing | design-draft |

## Absorbed Historical Claims

| Claim | Current handling |
|---|---|
| UNKNOWN underperformed partly because it lacked the 0.25-0.75 acceptance filter | Keep as mechanism evidence; old CSV/replay numbers are not current PnL |
| May live filled rows suggested some narrow price buckets looked excellent | Treat as execution-selected and low-sample unless confirmed in opportunity grain |
| Opportunity/candidate rows often favored different bands than filled rows | Use this as a required diagnostic before any band change |
| BUY_NO upper band around 0.65-0.70 looked more promising than 0.75 in May studies | Treat as design hypothesis; needs fresh gated rerun and forward validation |
| BUY_YES should be governed by price and edge jointly, not a narrow [0.25,0.30) band | Keep as research design principle, not live permission |
| EV-tier sizing increased return but not clearly Sharpe; Kelly concentrated too much and degraded live tail | Reject Kelly-style sizing; use hard notional caps and drawdown gates |
| Side-band overlay helped some blocked ECMWF city fills but was not a re-execution simulation | Only `shadow_candidate`; do not restore blocked cities directly to live |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Matched comparison | Same city/side/target-date/price bucket where possible |
| Risk | Report drawdown, tail loss, and concentration, not only ROI |
| Execution | Confirm candidate-to-fill behavior for the chosen band |
| Forward | Date-forward validation before live deployment |
| Grain split | Report candidate/opportunity band and realized fill band separately |
| Sizing mechanics | Split intended notional, submitted notional, actual fill cost, partial fills, and open exposure |
| Concentration | Report top city-day/side/bucket contribution and CVaR or worst-day impact |

## Open Work

1. Replace one-off band tables with a standard band x side x city x timing report.
2. Tie any live proposal to explicit notional caps and rollback triggers.
3. Build a fresh gated rerun before turning any May side x band finding into a deploy candidate.
