# Live Performance

> Living doc for modules [5][6]: live strategy realized performance, unsettled exposure, quasi-settlement, and strategy-instance attribution.
> Current status: `current-reference` only after CLOB coverage gate passes for the reported window.
> Last updated: 2026-06-09 Phase 4D pilot.

## Current Conclusion

Live performance reporting must be gated by fill coverage and must keep strategy_instance, city, side, target_date, fill_date, settlement state, and open mark separate. A single blended PnL number is not acceptable for decision-making.

This document is the landing page for live PnL evidence; account cashflow and wallet balance questions go to `account_reconcile.md`.

Phase 4D absorbed the 2026-06-03 to 2026-06-08 live-performance snapshots into the current rule:

1. **Only post-gate snapshots can be cited for current live performance.** Reports before the near-binary settlement and partial-fill recovery fixes can be cited only as failure history or action-history context.
2. **Row counts drift as new fills and settlements arrive.** Do not hard-code `live_real` row totals from old reports; quote `fact_built_at_utc`, coverage-gate result, and the exact report timestamp.
3. **`target_date` and `fill_date_bj` answer different questions.** Use `target_date` for strategy/weather attribution and `fill_date_bj` for wallet cashflow timing. Do not compare either directly to UI account equity.
4. **Recent loss attribution is slice evidence, not broad model proof.** The 2026-06-07 loss report supports cohort/city/side triage and V2/YES caution, but not global claims like "all cities failed" or "BUY_YES must be permanently banned".
5. **Instance attribution is mandatory.** `mid_price_core_v1_25_75`, `mid_price_core_v2_25_75`, `mid_price_core_v1_side_band`, and legacy maker_queue rows must not be blended into one action number.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-07-live-strategy-period-slice-current.md` | 2026-06 | current live strategy period slice | snapshot |
| `docs/analysis/2026-06/2026-06-07-live-pnl-curve-0524-0606.md` | 2026-05/06 | live PnL curve | snapshot |
| `docs/analysis/2026-06/2026-06-07-live-pnl-15d-curve-after-history-rebuild.md` | 2026-06 | 15d curve after history rebuild | snapshot |
| `docs/analysis/2026-06/2026-06-06-three-strategy-instances-near-binary-reanalysis.md` | 2026-06 | three strategy instances after near-binary fix | snapshot |
| `docs/analysis/2026-06/2026-06-07-recent-live-loss-attribution-target-0531-0606.md` | 2026-06 | post-fix recent loss attribution by cohort/city/side/instance | snapshot |
| `docs/analysis/2026-06/2026-06-08-decisive-experiment-scripts-audit-and-handoff.md` | 2026-06 | 2026-06-08 DB/gate audit and no-live-expansion conclusion | snapshot |
| `docs/analysis/2026-05/2026-05-27-performance-live-full-research.md` | 2026-05 | early full live research | snapshot |

## Absorbed Historical Claims

| Claim | Current handling |
|---|---|
| 2026-06-03 three-instance PnL used old `missing_bracket=725` and incomplete side-band mapping | Mark numeric PnL/ROI/win rate invalidated; keep only as why V2 was investigated/stopped |
| 2026-06-06 near-binary reanalysis produced `missing_bracket=0` and raw/DB fill reconciliation at that snapshot | Keep as post-fix action-history evidence, but prefer newer gated snapshots for current numbers |
| 2026-06-06 live period slice correctly separated UI equity from target-date strategy PnL | Claim absorbed here and in `account_reconcile.md`; numbers superseded by later fill-recovery snapshots |
| 2026-06-07 period slices and PnL curves show realized-only PnL plus open cost/MTM by date lens | Use as current snapshot family only with timestamp and coverage-gate context |
| 2026-06-07 recent loss attribution points to expanded-city cohorts, specific city x side rows, and V2/YES weakness | Use for triage; do not convert directly into permanent live city-pool or side bans without deploy process and fresh gates |
| 2026-06-08 audit says 6月窗口转负 and all decisive gates remain inconclusive | Use as live risk posture: no add size, no city-pool expansion, no confirmed edge claim |

## Required Gates Before Publishing

| Gate | Required Evidence |
|---|---|
| Data self-check | Run the five SQL checks from `WEATHER_ANALYSIS_CONTRACT.md` |
| Fill coverage | `weather_clob_fill_coverage_gate.py gate_pass=true` |
| Attribution | Group by `strategy_instance`; do not infer current branch from `execution_policy` alone |
| Settlement | Split settled, quasi-settled, and still-open; include valuation timestamp for MTM |
| Cost labels | Separate submitted notional, posted notional, actual fill cost, open cost, and realized PnL |

## Standard Live Report Shape

Every future live-performance report should include these fields before any conclusion:

1. DB/fact timestamp, CLOB coverage gate result, and five SQL preflight rows.
2. Window definition and date lens: `target_date`, `fill_date_bj`, or both.
3. `strategy_instance` split before city/side/price-bucket slices.
4. Settled realized PnL, settled cost, open cost, MTM, missing-MTM rows, and valuation timestamp.
5. Explicit action label: `hold`, `reduce`, `shadow`, `stop`, or `inconclusive`.

## Open Work

1. Standardize one live-performance table consumed by future reports.
2. Mark old reports that predate near-binary or fill recovery fixes as snapshots only.
3. Merge the 2026-06-07 period-slice duplicate family after link checks confirm no unique claim is lost.
