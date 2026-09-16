# HeadA Low-Price YES `dist<0` Branch Audit v1

Generated: 2026-07-04

Scope: only HeadA `forecast_tail_low_price_yes`. This is not a TP, METAR, or tmax-distribution study.

## Verdict

`dist<0` is a real drag and is semantically outside the HeadA thesis. The implemented selector change is now **remove `dist<=0`**, with separate blockers for `<0` and `=0` so forward telemetry can still audit them independently. `dist=0` is tiny, but 12/12 historical rows lost and it is still not above forecast.

Contract verdict for live action remains `shadow_candidate`: this was deployed as a tiny-live thesis-consistency removal, not as a size-up or confirmed alpha. The local `run_stack` refresh failed at a separate `fact_signal_candidates.candidate_id` uniqueness issue, so I am not calling this `confirmed` in this report.

Implementation note: `scripts/ops/low_price_yes_lottery_tiny_live.py` now blocks rows with `forecast_to_bracket_low_native <= 0`. `<0` rows use `dist_lt0_cold_or_inside_forecast_tail_v1`; exact-boundary rows use `dist_eq0_forecast_boundary_tail_v1`. Blocked rows still append to `shadow_decisions.jsonl` and `blocked_candidates.jsonl` with bracket-distance fields for forward review.

Plain English: `dist<0` means the ticket's lower bound is below the forecast max. Buying YES there is not "weather gets hotter than forecast"; it is "forecast was too high or the market underpriced a cooler/inside bracket." That may occasionally win, but it is a different bet and it has hurt this sleeve.

## Data Snapshot

- Input denominator: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv`.
- Rows: 476; dates 2026-05-06..2026-06-30; cities 48.
- Canonical join: `runtime/weather.db:fact_signal_candidates` for `unit`, `forecast_max_f`, `yes_spread`, `yes_depth_ask_5c`.
- DB fact build available in table: 2026-07-04T00:07:54.039821+00:00.
- Fee model: official Weather taker fee `shares * 0.05 * price * (1-price)`, fixed 8 shares, hold to settlement.
- Refresh caveat: `scripts/ops/sync_weather_remote.sh` succeeded, but `run_stack.sh` failed on a separate `fact_signal_candidates.candidate_id` uniqueness error after printing a 45,053-row candidate summary. This report uses the last available canonical table built at the timestamp above.

## `dist` Definition

```text
dist = (bracket_low_f - decision_forecast_max_f) / bracket_width_f
F markets: width = 2.0F
C markets: width = 1.8F
```

- `dist < 0`: bracket starts below forecast max. This is not a hot-tail ticket.
- `dist = 0`: bracket starts exactly at forecast max. Boundary case; not clearly hot-tail, but not the same as below-forecast.
- `dist > 0`: bracket starts above forecast max. This is the actual hotter-than-forecast lottery thesis.

## Distribution

| dist_sign | rows | wins | win_rate | avg_ask | avg_dist |
| --- | --- | --- | --- | --- | --- |
| eq0 | 12 | 0 | +0.0% | 9.5% | 0.000 |
| gt0 | 333 | 50 | +15.0% | 10.4% | 0.793 |
| lt0 | 131 | 15 | +11.5% | 10.9% | -0.499 |

## Train Window: <= 2026-06-20

| slice | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | losing_days | le_minus50pct_days | max_daily_loss_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_current_selector | 383 | 44 | 48 | +13.1% | 10.5% | +19.4% | -15.1% | +56.9% | 22 | 14 | -100.0% |
| remove_dist_lt0_keep_eq0 | 286 | 44 | 46 | +14.0% | 10.5% | +28.0% | -7.1% | +67.0% | 19 | 15 | -100.0% |
| remove_dist_le0_hot_only | 275 | 44 | 46 | +14.5% | 10.5% | +32.5% | -2.0% | +73.2% | 18 | 15 | -100.0% |
| dist_lt0_only | 97 | 39 | 23 | +10.3% | 10.5% | -5.7% | -67.5% | +66.0% | 32 | 32 | -100.0% |
| dist_eq0_only | 11 | 9 | 8 | +0.0% | 9.4% | -100.0% | -100.0% | -100.0% | 9 | 9 | -100.0% |
| dist_gt0_only | 275 | 44 | 46 | +14.5% | 10.5% | +32.5% | -2.9% | +72.9% | 18 | 15 | -100.0% |

Train paired delta versus current selector:

| window | policy | rows_removed | winner_rows_removed | roi_delta_vs_all | roi_delta_ci_low | roi_delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- |
| train_le_2026_06_20 | remove_dist_lt0_keep_eq0 | 97 | 10 | +8.5% | -8.0% | +26.1% |
| train_le_2026_06_20 | remove_dist_le0_hot_only | 108 | 10 | +13.1% | -3.4% | +30.4% |

## Recent Window: >= 2026-06-21

| slice | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | losing_days | le_minus50pct_days | max_daily_loss_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_current_selector | 93 | 9 | 35 | +16.1% | 10.7% | +44.8% | -3.3% | +106.5% | 4 | 0 | -30.0% |
| remove_dist_lt0_keep_eq0 | 59 | 9 | 28 | +16.9% | 9.9% | +63.6% | +16.4% | +134.9% | 2 | 1 | -100.0% |
| remove_dist_le0_hot_only | 58 | 9 | 28 | +17.2% | 9.9% | +66.8% | +18.2% | +135.9% | 1 | 1 | -100.0% |
| dist_lt0_only | 34 | 9 | 16 | +14.7% | 12.0% | +17.7% | -68.8% | +109.7% | 5 | 5 | -100.0% |
| dist_eq0_only | 1 | 1 | 1 | +0.0% | 11.0% | -100.0% | -100.0% | -100.0% | 1 | 1 | -100.0% |
| dist_gt0_only | 58 | 9 | 28 | +17.2% | 9.9% | +66.8% | +18.8% | +134.9% | 1 | 1 | -100.0% |

## Full Window

| slice | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | losing_days | le_minus50pct_days | max_daily_loss_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_current_selector | 476 | 53 | 48 | +13.7% | 10.5% | +24.5% | -5.3% | +57.5% | 26 | 14 | -100.0% |
| remove_dist_lt0_keep_eq0 | 345 | 53 | 47 | +14.5% | 10.4% | +33.8% | +3.7% | +67.4% | 21 | 16 | -100.0% |
| remove_dist_le0_hot_only | 333 | 53 | 47 | +15.0% | 10.4% | +38.2% | +5.9% | +73.1% | 19 | 16 | -100.0% |
| dist_lt0_only | 131 | 48 | 23 | +11.5% | 10.9% | +1.0% | -52.2% | +57.4% | 37 | 37 | -100.0% |
| dist_eq0_only | 12 | 10 | 9 | +0.0% | 9.5% | -100.0% | -100.0% | -100.0% | 10 | 10 | -100.0% |
| dist_gt0_only | 333 | 53 | 47 | +15.0% | 10.4% | +38.2% | +7.2% | +71.7% | 19 | 16 | -100.0% |

## Removed `dist<0` Branch: Contribution

Worst city contributions inside `dist<0`:

| city | rows | wins | win_rate | avg_ask | avg_dist | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Tokyo | 10 | 0 | +0.0% | 8.4% | -0.656 | $-6.99 | -100.0% |
| Moscow | 7 | 0 | +0.0% | 7.9% | -0.310 | $-4.61 | -100.0% |
| Miami | 11 | 1 | +9.1% | 13.2% | -0.491 | $-4.12 | -34.0% |
| Denver | 4 | 0 | +0.0% | 12.3% | -0.212 | $-4.11 | -100.0% |
| Busan | 5 | 0 | +0.0% | 7.7% | -0.444 | $-3.21 | -100.0% |
| Karachi | 2 | 0 | +0.0% | 17.8% | -0.278 | $-2.96 | -100.0% |
| Istanbul | 4 | 0 | +0.0% | 8.8% | -0.194 | $-2.94 | -100.0% |
| Madrid | 3 | 0 | +0.0% | 11.2% | -0.407 | $-2.80 | -100.0% |

By forecast source inside `dist<0`:

| forecast_source | rows | wins | win_rate | avg_ask | avg_dist | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| open_meteo_live_gfs | 77 | 8 | +10.4% | 11.2% | -0.558 | $-8.11 | -11.2% |
| open_meteo_live_ecmwf | 54 | 7 | +13.0% | 10.4% | -0.414 | $+9.29 | +19.9% |

Exact-boundary `dist=0` rows:

| target_date | city | bracket | forecast_source | entry | payoff | dist_br | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-10 | London | 17 | open_meteo_live_ecmwf | 0.070 | 0.000 | 0.000 | $-0.59 |
| 2026-05-16 | Denver | 80-81 | open_meteo_live_gfs | 0.190 | 0.000 | 0.000 | $-1.58 |
| 2026-05-22 | Moscow | 27 | open_meteo_live_ecmwf | 0.095 | 0.000 | 0.000 | $-0.79 |
| 2026-05-26 | Milan | 35 | open_meteo_live_ecmwf | 0.055 | 0.000 | 0.000 | $-0.46 |
| 2026-06-04 | Moscow | 22 | open_meteo_live_ecmwf | 0.175 | 0.000 | 0.000 | $-1.46 |
| 2026-06-12 | Chicago | 76-77 | open_meteo_live_gfs | 0.055 | 0.000 | 0.000 | $-0.46 |
| 2026-06-12 | NYC | 98-99 | open_meteo_live_gfs | 0.069 | 0.000 | 0.000 | $-0.57 |
| 2026-06-14 | Denver | 74-75 | open_meteo_live_gfs | 0.123 | 0.000 | 0.000 | $-1.03 |
| 2026-06-16 | CapeTown | 17 | open_meteo_live_ecmwf | 0.076 | 0.000 | 0.000 | $-0.64 |
| 2026-06-19 | Dallas | 90-91 | open_meteo_live_ecmwf | 0.066 | 0.000 | 0.000 | $-0.55 |
| 2026-06-19 | London | 28 | open_meteo_live_ecmwf | 0.055 | 0.000 | 0.000 | $-0.46 |
| 2026-06-21 | Lucknow | 39 | open_meteo_live_ecmwf | 0.110 | 0.000 | 0.000 | $-0.92 |

## Decision

The live selector patch is:

```text
if forecast_max_f is available and dist <= 0:
    block as dist_lt0_cold_or_inside_forecast_tail_v1
    or dist_eq0_forecast_boundary_tail_v1
else:
    keep existing selector behavior
```

Reason: this is a thesis-consistency removal, not a tuned ROI threshold. It removes below-forecast tickets and exact forecast-boundary tickets while keeping the two blocker labels separate for forward review.

Do not size up from this. The same audit still shows the larger uncertainty is book-state/stale-quote feasibility, not just `dist`.
