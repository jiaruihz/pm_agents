# Live Performance

> Living doc for modules [5][6]: live strategy realized performance, unsettled exposure, quasi-settlement, and strategy-instance attribution.
> Current status: `current-reference` only after CLOB coverage gate passes for the reported window.
> Last updated: 2026-06-09.

## Current Conclusion

Live performance reporting must be gated by fill coverage and must keep strategy_instance, city, side, target_date, fill_date, settlement state, and open mark separate. A single blended PnL number is not acceptable for decision-making.

This document is the landing page for live PnL evidence; account cashflow and wallet balance questions go to `account_reconcile.md`.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-07-live-strategy-period-slice-current.md` | 2026-06 | current live strategy period slice | snapshot |
| `docs/analysis/2026-06/2026-06-07-live-pnl-curve-0524-0606.md` | 2026-05/06 | live PnL curve | snapshot |
| `docs/analysis/2026-06/2026-06-07-live-pnl-15d-curve-after-history-rebuild.md` | 2026-06 | 15d curve after history rebuild | snapshot |
| `docs/analysis/2026-06/2026-06-06-three-strategy-instances-near-binary-reanalysis.md` | 2026-06 | three strategy instances after near-binary fix | snapshot |
| `docs/analysis/2026-05/2026-05-27-performance-live-full-research.md` | 2026-05 | early full live research | snapshot |

## Required Gates Before Publishing

| Gate | Required Evidence |
|---|---|
| Data self-check | Run the five SQL checks from `WEATHER_ANALYSIS_CONTRACT.md` |
| Fill coverage | `weather_clob_fill_coverage_gate.py gate_pass=true` |
| Attribution | Group by `strategy_instance`; do not infer current branch from `execution_policy` alone |
| Settlement | Split settled, quasi-settled, and still-open; include valuation timestamp for MTM |
| Cost labels | Separate submitted notional, posted notional, actual fill cost, open cost, and realized PnL |

## Open Work

1. Standardize one live-performance table consumed by future reports.
2. Mark old reports that predate near-binary or fill recovery fixes as snapshots only.
