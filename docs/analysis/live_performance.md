# Live Performance

> Living doc for modules [5][6]: live strategy realized performance, unsettled exposure, quasi-settlement, and strategy-instance attribution.
> Current status: `current-reference` only after CLOB coverage gate passes for the reported window.
> Last updated: 2026-08-12 CrossNO recent-accuracy attribution.

## Current Conclusion

Live performance reporting must be gated by fill coverage and must keep strategy_instance, city, side, target_date, fill_date, settlement state, and open mark separate. A single blended PnL number is not acceptable for decision-making.

This document is the landing page for live PnL evidence; account cashflow and wallet balance questions go to `account_reconcile.md`.

Phase 4D absorbed the 2026-06-03 to 2026-06-08 live-performance snapshots into the current rule:

1. **Only post-gate snapshots can be cited for current live performance.** Reports before the near-binary settlement and partial-fill recovery fixes can be cited only as failure history or action-history context.
2. **Row counts drift as new fills and settlements arrive.** Do not hard-code `live_real` row totals from old reports; quote `fact_built_at_utc`, coverage-gate result, and the exact report timestamp.
3. **`target_date` and `fill_date_bj` answer different questions.** Use `target_date` for strategy/weather attribution and `fill_date_bj` for wallet cashflow timing. Do not compare either directly to UI account equity.
4. **Recent loss attribution is slice evidence, not broad model proof.** The 2026-06-07 loss report supports cohort/city/side triage and V2/YES caution, but not global claims like "all cities failed" or "BUY_YES must be permanently banned".
5. **Instance attribution is mandatory.** `mid_price_core_v1_25_75`, `mid_price_core_v2_25_75`, `mid_price_core_v1_side_band`, and legacy maker_queue rows must not be blended into one action number.
6. **CrossNO recent accuracy did not improve on the full 10-day denominator.** `2026-08-02..11` live expressions were
   `32/39=82.05%`, below `2026-07-23..08-01` at `27/32=84.38%`. The last-five-date `19/21=90.48%`
   spike is an inconclusive weather/short-window cluster, not a live promotion result. CrossNO rows from target date
   2026-08-05 onward currently require stable `strategy_id=live_weather_edge_v1_c16645cc1165` because 60 fact rows
   through 2026-08-12 have `instance_id=NULL`; instance-only reports silently omit them.
7. **CrossNO's recent order-volume increase is city-concentrated, not a broad execution recovery.** On matched
   ten-date windows, Busan triggers/fills rose `34/13 → 46/22` and Seoul `25/2 → 39/3`, while Tokyo was
   `28/7 → 22/7` and Helsinki `21/8 → 20/5`. Existing live cities gained seven fills: a two-factor decomposition
   attributes about `5.4` to more weather/source cross triggers and `1.6` to fill conversion. Explicit execution
   failures explain only isolated expressions; ask above cap or an HTTP-200 book with no NO ask explains most misses.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-07-live-strategy-period-slice-current.md` | 2026-06 | current live strategy period slice | snapshot |
| `docs/analysis/2026-06/2026-06-07-live-pnl-15d-curve-after-history-rebuild.md` | 2026-05-24..06-07 | retained 15d curve after history rebuild; includes the removed 05/24..06/06 partial view | historical snapshot |
| `docs/analysis/2026-06/2026-06-06-three-strategy-instances-near-binary-reanalysis.md` | 2026-06 | three strategy instances after near-binary fix | snapshot |
| `docs/analysis/2026-06/2026-06-07-recent-live-loss-attribution-target-0531-0606.md` | 2026-06 | post-fix recent loss attribution by cohort/city/side/instance | snapshot |
| `docs/analysis/2026-08/2026-08-12-performance-cross-no-recent-accuracy-attribution-v1.md` | 2026-07-09..08-11 | CrossNO signal/fill accuracy, engineering/data/weather attribution, and instance metadata gap | current snapshot |
| `docs/analysis/2026-08/2026-08-12-performance-cross-no-city-trigger-order-attribution-v1.md` | 2026-07-23..08-12 | CrossNO per-city trigger, executable-book, order, fill, and weather-vs-engineering attribution | current snapshot |
| `docs/archive/analysis/2026-05/2026-05-27-performance-live-full-research.md` | 2026-05 | early full live research | snapshot |

## Absorbed Historical Claims

| Claim | Current handling |
|---|---|
| 2026-06-03 three-instance PnL used old `missing_bracket=725` and incomplete side-band mapping | Mark numeric PnL/ROI/win rate invalidated; keep only as why V2 was investigated/stopped |
| 2026-06-06 near-binary reanalysis produced `missing_bracket=0` and raw/DB fill reconciliation at that snapshot | Keep as post-fix action-history evidence, but prefer newer gated snapshots for current numbers |
| 2026-06-06 live period slice correctly separated UI equity from target-date strategy PnL | Claim absorbed here and in `account_reconcile.md`; numbers superseded by later fill-recovery snapshots |
| 2026-06-07 period slices and PnL curves show realized-only PnL plus open cost/MTM by date lens | Use as current snapshot family only with timestamp and coverage-gate context |
| 2026-06-07 recent loss attribution points to expanded-city cohorts, specific city x side rows, and V2/YES weakness | Use for triage; do not convert directly into permanent live city-pool or side bans without deploy process and fresh gates |
| 2026-06-08 gated audit says 6月窗口转负 and all decisive gates remain inconclusive | Conclusion absorbed here; underlying market-structure and executable-edge snapshots remain in the docs index |

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
