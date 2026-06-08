# City Selection

> Living doc for module [3]: city pool, city-day basket, and region/forecast-source selection.
> Current status: `shadow_candidate` for research-only basket ideas; live city pool remains governed by `WEATHER_CITY_POOL_DECISIONS.md`.
> Last updated: 2026-06-09.

## Current Conclusion

City selection is the largest source of historical analysis churn. The current live allowlist and T1/T2 status are not defined here; they are defined in `WEATHER_CITY_POOL_DECISIONS.md`. This document tracks the evidence that may later justify changing those decisions.

City and city-day basket rules are especially prone to overfitting because many variants were tested on overlapping small windows. No city selection change should enter live without pre-registered target metric, holdout validation, and a rollback condition.

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-08-city-day-basket-vs-legacy-baselines.md` | 2026-06 | basket vs legacy baseline refresh | snapshot |
| `docs/analysis/2026-06/2026-06-08-city-day-basket-walkforward.md` | 2026-06 | walk-forward check, overfit risk | snapshot |
| `docs/analysis/2026-06/2026-06-08-city-day-distribution-quality.md` | 2026-06 | distribution quality and normalized market distribution | snapshot |
| `docs/analysis/2026-06/2026-06-08-city-model-conditional-edge.md` | 2026-06 | city x model x side conditional edge | snapshot |
| `docs/analysis/2026-06/2026-06-06-near-binary-city-reanalysis.md` | 2026-06 | near-binary fixed city reanalysis | snapshot |

## Required Gates Before Live Use

| Gate | Required Evidence |
|---|---|
| Pre-registration | State target metric, denominator, city universe, and maximum variants tried |
| Forward | Selected city rule must pass a date-based holdout |
| Baseline | Compare to current T1 live pool and same side/price controls |
| Risk | Report city-day correlation and concentration risk before increasing size |
| Governance | Final live action must be recorded in `WEATHER_CITY_POOL_DECISIONS.md` |

## Open Work

1. Convert city-day basket research into one evidence table with accepted/rejected/shadow states.
2. Keep research-only PR2b/basket parameters out of production config until gates pass.
