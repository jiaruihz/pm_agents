# City x Model Conditional Edge Research

> generated_at_utc: `2026-06-08T15:39:13.629442+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: H-C offline diagnostic. Source is `fact_signal_candidates`; no N100/live behavior changed.

## Data Snapshot

| field | value |
|---|---:|
| `max_fact_trades_built_at_utc` | `2026-06-08T01:32:33.618777+00:00` |
| `max_fact_signal_candidates_built_at_utc` | `2026-06-08T01:32:49.788100+00:00` |
| `fact_signal_candidates_rows` | `23893` |
| `eligible_rows` | `7841` |
| `decision_window_missing_rows` | `10437` |
| `usable_rows` | `854` |
| `usable_dates` | `24` |

## Split

| field | value |
|---|---:|
| `train_frac` | `0.7` |
| `train_dates` | `16` |
| `holdout_dates` | `8` |
| `cutoff_first_holdout_date` | `2026-05-30` |
| `min_date` | `2026-05-12` |
| `max_date` | `2026-06-06` |
| `train_rows` | `518` |
| `holdout_rows` | `336` |

## Gates

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `FAIL` |
| `verdict` | `inconclusive` |

Train-selected keys: `Madrid|ecmwf|BUY_NO, NYC|gfs|BUY_YES, Tokyo|gfs|BUY_YES, Warsaw|ecmwf|BUY_NO, Warsaw|ecmwf|BUY_YES`.

| holdout policy | n | dates | cost | pnl | ROI | ROI CI | Brier delta |
|---|---:|---:|---:|---:|---:|---:|---:|
| selected H-C pool | 46 | 8 | 18.94 | +10.61 | +56.0% | [-135.9%, +334.5%] | -0.009904951521739126 |
| matched side+price baseline | 324 | 8 | 169.37 | -43.69 | -25.8% | NA | -0.040701706141975304 |

Holdout excess ROI vs matched baseline: `+81.8%`, CI `[-113.5%, +293.7%]`.

## Selected Key Detail

| key | selected | train n | train excess ROI | holdout n | holdout ROI | holdout excess ROI | holdout Brier delta | low sample |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `Warsaw|ecmwf|BUY_YES` | True | 12 | +355.3% | 10 | +281.0% | +456.1% | 0.01695526000000002 | False |
| `Madrid|ecmwf|BUY_NO` | True | 14 | +47.7% | 9 | +327.0% | +320.4% | 0.06212909666666665 | False |
| `Warsaw|ecmwf|BUY_NO` | True | 18 | +78.2% | 12 | +100.9% | +85.6% | -0.015681102499999978 | False |
| `NYC|gfs|BUY_YES` | True | 20 | +24.8% | 13 | -645.8% | -522.6% | -0.0755904376923077 | False |
| `Tokyo|gfs|BUY_YES` | True | 12 | +656.1% | 2 | -1000.0% | -877.7% | -0.006746660000000003 | True |

## Model Overview

| model | n | ROI | Brier delta | live fill rate |
|---|---:|---:|---:|---:|
| `gfs` | 426 | +31.8% | -0.03169186776995305 | +45.8% |
| `ecmwf` | 428 | -13.6% | -0.049597494322429894 | +50.2% |

## Notes

- Positive `brier_delta_market_minus_model` means the model probability has lower Brier loss than market YES price.
- The baseline is all holdout candidates with the same selected `side+price_bucket`; this avoids mistaking BUY_NO base-rate or price-band structure for city-model alpha.
- `decision_window_missing` rows are excluded by contract; the data snapshot reports their scale.
- A failed gate means no live keep/cut/size action.
