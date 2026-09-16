# City Selection

> Living doc for module [3]: city pool, city-day basket, and region/forecast-source selection.
> Current status: `shadow_candidate` for research-only basket ideas; live city pool remains governed by `WEATHER_CITY_POOL_DECISIONS.md`.
> Last updated: 2026-08-13 basket evidence consolidation.

## Current Conclusion

City selection is the largest source of historical analysis churn. The current live allowlist and T1/T2 status are not defined here; they are defined in `WEATHER_CITY_POOL_DECISIONS.md`. This document tracks the evidence that may later justify changing those decisions.

City and city-day basket rules are especially prone to overfitting because many variants were tested on overlapping small windows. No city selection change should enter live without pre-registered target metric, holdout validation, top-winner stress, and a rollback condition.

The current city-selection state is:

1. **City pool governance**: live pool changes still belong in `WEATHER_CITY_POOL_DECISIONS.md`; this doc only records evidence.
2. **City x side first**: whole-city ROI is too coarse. New-city failures and NYC BUY_YES show that city x side x strategy_instance is the minimum review grain.
3. **Basket remains shadow**: PR2b/combo/basket variants can improve headline ROI on some slices, but walk-forward and top5-removed stress reject canary promotion.
4. **Distribution anchor**: market-normalized city-day distributions still dominate holdout/recent slices more often than raw/blend, so basket objectives should stay market-anchored until model distributions prove forward value.

## Consolidated Quantitative Evidence

| Check | Fixed denominator | Result | Decision |
|---|---|---|---|
| Initial PR2 replay | 1,737 settled candidate rows, 2026-05-06..06-01 | raw/blended/basket ROI `+6.84%/+15.33%/+1.24%`; basket top-5-removed ROI `-3.22%` | basket gate failed |
| Same-entry refresh | settled T-22..24 representative decisions through 2026-06-06 | positive full-window variants became negative or top-winner-dependent on the post-06-01 slice | no canary |
| Walk-forward | 5 folds, 14 train dates + 3 test dates | train-selected policy: 388 legs, ROI `+2.66%`, weighted top-5-removed ROI `-35.45%`, 40% positive folds | selector rejected |
| Distribution quality | 535 full / 232 holdout city-days | holdout market-normalized logloss/Brier `0.7647/0.4707`, versus blend `0.7935/0.4850` and raw `1.3957/0.6525` | keep market anchor |

Exact legacy machine results were content-address archived under manifest
`/Volumes/jrs-archive/pm_agents/research/artifact_store/manifests/basket_blender_legacy_cleanup_20260813.json`.
The dated Markdown renderings were removed after the durable claims above were absorbed; git history remains the human-readable audit trail.

## Absorbed Historical Claims

1. The pre-fix `city-alpha-framework` is invalid for current PnL/rank numbers because it carried old `missing_bracket` exposure, but its target metric framework remains useful: opportunity alpha, brier delta, live realizability, daily stability, and execution coverage.
2. The near-binary city reanalysis is the current post-fix city/side correction: not every city is bad; new T1 cohorts and BUY_YES side pollution were the main failure patterns, and `NYC BUY_YES` remains a specific warning.
3. The 2026-06-08 basket state doc is the current city-day basket handoff: first apply the filtered operational base, then rerun basket; do not pick combo rules from headline ROI alone.
4. Basket-vs-legacy and walk-forward reports show some positive point estimates, but tail dependency and train-selected fold weakness block live canary.
5. Distribution-quality research says basket risk/objective sanity should remain market-normalized unless raw/blend distributions beat market in forward slices.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/archive/analysis/2026-06/2026-06-06-city-alpha-framework.md` | 2026-06 pre near-binary fix | useful framework, invalidated numeric city ranks | invalidated-numbers |
| `docs/archive/analysis/2026-06/2026-06-06-city-day-basket-pr2b-sweep.md` | 2026-06 | offline PR2b parameter sweep and guard tests | superseded-evidence |
| `docs/archive/analysis/2026-06/2026-06-06-city-day-basket-pr2b-robustness.md` | 2026-06 | PR2b robustness and gate comparison | superseded-evidence |
| `docs/archive/analysis/2026-06/2026-06-06-city-day-basket-optimizer-research.md` | 2026-06 | first optimizer basket research; missed-profit and top5 weakness | superseded-evidence |
| `docs/archive/analysis/2026-06/2026-06-06-city-day-basket-vs-legacy-baselines.md` | 2026-06 | baseline-vs-basket comparison on older window | superseded-evidence |
| `docs/archive/analysis/2026-06/2026-06-06-city-day-basket-walkforward.md` | 2026-06 | city-day basket walk-forward; train-selected folds not robust | superseded-evidence |
| `docs/archive/analysis/2026-06/2026-06-06-city-day-distribution-quality.md` | 2026-06 | distribution diagnostics before basket objective refresh | superseded-evidence |
| JRS manifest `basket_blender_legacy_cleanup_20260813` | 2026-05-06..06-06 | exact basket baseline, walk-forward and distribution machine results | archived-machine-evidence |
| `docs/analysis/2026-06/2026-06-08-city-model-conditional-edge.md` | 2026-06 | city x model x side conditional edge | snapshot-evidence |
| `docs/analysis/2026-06/2026-06-06-near-binary-city-reanalysis.md` | 2026-06 | near-binary fixed city reanalysis | active-evidence |
| `docs/analysis/2026-06/2026-06-10-weather-strategy-live-test-selection.md` | 2026-06 | no new real live test candidate; adjacent3 only shadow | active-evidence |
| `docs/analysis/2026-06/2026-06-10-live-test-readiness-scoreboard-v0.md` | 2026-06 | readiness scoreboard; no city/basket live promotion | active-evidence |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Pre-registration | State target metric, denominator, city universe, and maximum variants tried |
| Forward | Selected city rule must pass a date-based holdout |
| Baseline | Compare to current T1 live pool and same side/price controls |
| Risk | Report city-day correlation and concentration risk before increasing size |
| Stress | Report drop-best-day / top5-removed ROI and missed-profit vs avoided-loss |
| Governance | Final live action must be recorded in `WEATHER_CITY_POOL_DECISIONS.md` |

## Open Work

1. Keep this table as the single city-day basket conclusion surface; dated reports remain reproducible evidence only.
2. Keep research-only PR2b/basket parameters out of production config until gates pass.
3. Rerun basket only on the filtered operational base before revisiting any canary proposal.
4. Keep city x side x strategy_instance as the default grain for future city promotion/demotion evidence.
