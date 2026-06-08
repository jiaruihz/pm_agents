# Sizing And Entry Band

> Living doc for module [3]: entry price windows, side-specific bands, and position sizing.
> Current status: `design-draft` for future changes; current live sizing/bands remain defined by strategy entrypoint/config.
> Last updated: 2026-06-09.

## Current Conclusion

Entry band and sizing analysis should be treated as decision-layer research, not as a generic PnL slice. The core question is whether a band or size curve improves risk-adjusted executable edge after controlling for city, side, target-date, and price bucket.

Any proposed band or sizing change must separate reserved notional, raw fill cost, strict fill cost, realized PnL, and open exposure.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-05/2026-05-30-performance-entry-band-research.md` | 2026-05 | entry band side x price bucket EV | snapshot |
| `docs/analysis/2026-05/2026-05-30-performance-sizing-and-band-distribution.md` | 2026-05 | sizing x entry band distribution and anti-overfit check | snapshot |
| `docs/WEATHER_ENTRY_BAND_AND_SIZING_DESIGN.md` | design | target design for entry bands and sizing | design-draft |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Matched comparison | Same city/side/target-date/price bucket where possible |
| Risk | Report drawdown, tail loss, and concentration, not only ROI |
| Execution | Confirm candidate-to-fill behavior for the chosen band |
| Forward | Date-forward validation before live deployment |

## Open Work

1. Replace one-off band tables with a standard band x side x city x timing report.
2. Tie any live proposal to explicit notional caps and rollback triggers.
