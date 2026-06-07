# Blended Paper Profile Fill-Rate Estimate

> generated_at_utc: `2026-06-06T03:56:25+00:00`
> db: `/home/rui/projects/pm_agent/runtime/weather.db`

## Target Metric

`blended_e05_fill_rate_estimate` = replay the selected blended paper profiles on settled historical candidates, then estimate fills/PnL using historical CLOB fill rates.

This is not actual wallet PnL. It is a counterfactual opportunity replay plus fill-rate scaling.

## Data Self-Check

```json
{
  "fact_trades_freshness": [
    {
      "max_fact_built_at_utc": "2026-06-06T03:54:29.726938+00:00"
    }
  ],
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 855
    },
    {
      "trade_class": "live_simulated",
      "rows": 1032
    },
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    }
  ],
  "fact_trades_by_settlement": [
    {
      "settlement_status": null,
      "rows": 321
    },
    {
      "settlement_status": "settled",
      "rows": 4487
    }
  ],
  "fact_signal_candidates_coverage": [
    {
      "rows": 21845,
      "eligible": 6974,
      "paper_ordered": 2621,
      "live_filled": 464
    }
  ],
  "clob_orders_with_fills": [
    {
      "status": "error",
      "orders": 151,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 964,
      "with_fill": 855
    }
  ]
}
```

## Time Coverage

```json
{
  "candidate_all_range": [
    {
      "min_date": "2026-05-05",
      "max_date": "2026-06-06",
      "rows": 21845,
      "days": 33
    }
  ],
  "candidate_settled_complete_range": [
    {
      "min_date": "2026-05-06",
      "max_date": "2026-06-04",
      "rows": 2134,
      "days": 29
    }
  ],
  "live_real_settled_range": [
    {
      "min_date": "2026-05-16",
      "max_date": "2026-06-04",
      "fills": 756,
      "days": 18,
      "cost_usd": 2497.853469,
      "pnl_usd": 25.875553999999987
    }
  ]
}
```

## Glossary

- `full`: all settled candidates with complete decision-window fields in the local DB.
- `holdout_from_2026_05_26`: target dates from 2026-05-26 onward; used as a later-period out-of-sample check versus earlier tuning.
- `recent_from_2026_06_01`: target dates from 2026-06-01 onward; shortest and noisiest recent regime check.
- `live_filled_only`: only candidate rows that historical live actually filled; strict execution-quality overlap, not a full opportunity set.
- `e05`: blended side-aware edge threshold `0.05`; e.g. trade when `p_yes_used - price >= 0.05` for YES or `(1-p_yes_used)-price >= 0.05` for NO.

## Historical Fill Rates

- submitted order fill rate: `88.7%` (855/964)
- all order-attempt fill rate, including errors: `76.7%` (855/1115)

## Aligned Decision Table

Same denominator for old and new: settled complete `fact_signal_candidates`, `$1/leg`, same entry-window family, same submitted-order fill-rate estimate.

| family | slice | old profile | old legs | old ROI | old est PnL | new profile | new legs | new ROI | new est PnL | new-old est PnL | new top5-removed ROI | new P(ROI>0) |
|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| 25_75 | full | raw_current_25_75 | 884 | +4.41% | $+34.55 | blended_25_75_e05 | 532 | +11.31% | $+53.38 | $+18.83 | +8.62% | 99.2% |
| side_band | full | raw_current_side_band | 435 | +9.92% | $+38.26 | blended_side_band_e05 | 334 | +17.26% | $+51.12 | $+12.87 | +12.12% | 99.5% |
| 25_75 | holdout_from_2026_05_26 | raw_current_25_75 | 313 | -3.70% | $-10.27 | blended_25_75_e05 | 201 | +3.51% | $+6.26 | $+16.53 | -3.76% | 61.3% |
| side_band | holdout_from_2026_05_26 | raw_current_side_band | 161 | +12.03% | $+17.17 | blended_side_band_e05 | 130 | +14.21% | $+16.38 | $-0.79 | +0.63% | 84.9% |
| 25_75 | recent_from_2026_06_01 | raw_current_25_75 | 84 | +2.32% | $+1.73 | blended_25_75_e05 | 48 | +1.34% | $+0.57 | $-1.16 | -14.09% | 54.6% |
| side_band | recent_from_2026_06_01 | raw_current_side_band | 44 | +28.25% | $+11.02 | blended_side_band_e05 | 31 | +23.10% | $+6.35 | $-4.67 | -20.93% | 87.2% |
| 25_75 | live_filled_only | raw_current_25_75 | 235 | +7.67% | $+15.99 | blended_25_75_e05 | 152 | +7.33% | $+9.88 | $-6.11 | -1.88% | 76.7% |
| side_band | live_filled_only | raw_current_side_band | 114 | +19.00% | $+19.21 | blended_side_band_e05 | 94 | +21.21% | $+17.69 | $-1.52 | +3.97% | 92.8% |

## Replay Summary ($1 Notional Per Leg)

| slice | profile | date range | legs | days | cost | pnl | ROI | top5-removed ROI | day win | old-live overlap | overlap ROI | est fills @submitted | est pnl @submitted |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | raw_current_25_75 | 2026-05-06 -> 2026-06-04 | 884 | 29 | $884 | $+38.95 | +4.41% | +2.75% | 62.1% | 235/884 (26.6%) | +7.67% | 784.0 | $+34.55 |
| full | blended_25_75_e05 | 2026-05-06 -> 2026-06-04 | 532 | 28 | $532 | $+60.18 | +11.31% | +8.62% | 67.9% | 152/532 (28.6%) | +7.33% | 471.8 | $+53.38 |
| full | raw_current_side_band | 2026-05-06 -> 2026-06-04 | 435 | 28 | $435 | $+43.14 | +9.92% | +5.92% | 67.9% | 114/435 (26.2%) | +19.00% | 385.8 | $+38.26 |
| full | blended_side_band_e05 | 2026-05-06 -> 2026-06-04 | 334 | 28 | $334 | $+57.64 | +17.26% | +12.12% | 71.4% | 94/334 (28.1%) | +21.21% | 296.2 | $+51.12 |
| holdout_from_2026_05_26 | raw_current_25_75 | 2026-05-26 -> 2026-06-04 | 313 | 10 | $313 | $-11.58 | -3.70% | -8.44% | 40.0% | 173/313 (55.3%) | -0.01% | 277.6 | $-10.27 |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-05-26 -> 2026-06-04 | 201 | 10 | $201 | $+7.05 | +3.51% | -3.76% | 40.0% | 112/201 (55.7%) | -0.11% | 178.3 | $+6.26 |
| holdout_from_2026_05_26 | raw_current_side_band | 2026-05-26 -> 2026-06-04 | 161 | 10 | $161 | $+19.36 | +12.03% | +1.07% | 70.0% | 83/161 (51.6%) | +13.78% | 142.8 | $+17.17 |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-26 -> 2026-06-04 | 130 | 10 | $130 | $+18.47 | +14.21% | +0.63% | 60.0% | 70/130 (53.8%) | +12.28% | 115.3 | $+16.38 |
| recent_from_2026_06_01 | raw_current_25_75 | 2026-06-01 -> 2026-06-04 | 84 | 4 | $84 | $+1.95 | +2.32% | -8.42% | 50.0% | 73/84 (86.9%) | -0.03% | 74.5 | $+1.73 |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-01 -> 2026-06-04 | 48 | 4 | $48 | $+0.64 | +1.34% | -14.09% | 50.0% | 39/48 (81.2%) | -4.29% | 42.6 | $+0.57 |
| recent_from_2026_06_01 | raw_current_side_band | 2026-06-01 -> 2026-06-04 | 44 | 4 | $44 | $+12.43 | +28.25% | -0.44% | 100.0% | 36/44 (81.8%) | +32.73% | 39.0 | $+11.02 |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-01 -> 2026-06-04 | 31 | 4 | $31 | $+7.16 | +23.10% | -20.93% | 75.0% | 25/31 (80.6%) | +24.67% | 27.5 | $+6.35 |
| live_filled_only | raw_current_25_75 | 2026-05-16 -> 2026-06-04 | 235 | 18 | $235 | $+18.03 | +7.67% | +1.79% | 61.1% | 235/235 (100.0%) | +7.67% | 208.4 | $+15.99 |
| live_filled_only | blended_25_75_e05 | 2026-05-16 -> 2026-06-04 | 152 | 18 | $152 | $+11.14 | +7.33% | -1.88% | 44.4% | 152/152 (100.0%) | +7.33% | 134.8 | $+9.88 |
| live_filled_only | raw_current_side_band | 2026-05-16 -> 2026-06-04 | 114 | 18 | $114 | $+21.66 | +19.00% | +5.21% | 66.7% | 114/114 (100.0%) | +19.00% | 101.1 | $+19.21 |
| live_filled_only | blended_side_band_e05 | 2026-05-16 -> 2026-06-04 | 94 | 18 | $94 | $+19.94 | +21.21% | +3.97% | 55.6% | 94/94 (100.0%) | +21.21% | 83.4 | $+17.69 |

## Blended Vs Current Raw Gate

| slice | pair | shared | blend_only | raw_only | net PnL delta | bootstrap ROI p05/p50/p95 | P(ROI>0) |
|---|---|---:|---:|---:|---:|---:|---:|
| full | blended_25_75_e05_vs_raw_current_25_75 | 532 | 0 | 352 | $+21.23 | +3.7% / +11.3% / +19.1% | 99.2% |
| full | blended_side_band_e05_vs_raw_current_side_band | 307 | 27 | 128 | $+14.51 | +6.2% / +17.2% / +28.6% | 99.5% |
| holdout_from_2026_05_26 | blended_25_75_e05_vs_raw_current_25_75 | 201 | 0 | 112 | $+18.64 | -12.3% / +3.0% / +23.1% | 61.3% |
| holdout_from_2026_05_26 | blended_side_band_e05_vs_raw_current_side_band | 122 | 8 | 39 | $-0.89 | -6.7% / +13.8% / +40.4% | 84.9% |
| recent_from_2026_06_01 | blended_25_75_e05_vs_raw_current_25_75 | 48 | 0 | 36 | $-1.31 | -22.4% / +1.3% / +33.5% | 54.6% |
| recent_from_2026_06_01 | blended_side_band_e05_vs_raw_current_side_band | 30 | 1 | 14 | $-5.27 | -0.4% / +23.1% / +72.5% | 87.2% |
| live_filled_only | blended_25_75_e05_vs_raw_current_25_75 | 152 | 0 | 83 | $-6.89 | -8.8% / +7.4% / +24.2% | 76.7% |
| live_filled_only | blended_side_band_e05_vs_raw_current_side_band | 89 | 5 | 25 | $-1.71 | -2.2% / +21.3% / +46.2% | 92.8% |

## Mid Price V1 Actual Live Baseline

| baseline | fills | dates | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| all_mid_price_core_v1 | 430 | 2026-05-16 -> 2026-06-04 | $1467 | $+60 | +4.07% | 57.9% |
| combined_side_band_windows | 48 | 2026-06-01 -> 2026-06-04 | $135 | $+25 | +18.76% | 60.4% |
| mid_v1 window 0.20-0.45 | 16 | 2026-06-01 -> 2026-06-04 | $26 | $+6 | +23.36% | 37.5% |
| mid_v1 window 0.25-0.75 | 382 | 2026-05-16 -> 2026-06-04 | $1332 | $+34 | +2.58% | 57.6% |
| mid_v1 window 0.35-0.65 | 32 | 2026-06-01 -> 2026-06-04 | $108 | $+19 | +17.64% | 71.9% |

## Tail Dependence Drilldown

| slice | profile | total cost | total pnl | max win | top5 pnl | top5 pnl / total pnl | after top5 cost | after top5 pnl | after top5 ROI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | blended_25_75_e05 | $532 | $+60.18 | $+3.00 | $+14.76 | +24.5% | $527 | $+45.42 | +8.62% |
| full | blended_side_band_e05 | $334 | $+57.64 | $+3.88 | $+17.78 | +30.8% | $329 | $+39.86 | +12.12% |
| holdout_from_2026_05_26 | blended_25_75_e05 | $201 | $+7.05 | $+3.00 | $+14.41 | +204.3% | $196 | $-7.36 | -3.76% |
| holdout_from_2026_05_26 | blended_side_band_e05 | $130 | $+18.47 | $+3.88 | $+17.69 | +95.8% | $125 | $+0.78 | +0.63% |
| recent_from_2026_06_01 | blended_25_75_e05 | $48 | $+0.64 | $+2.57 | $+6.70 | +1044.2% | $43 | $-6.06 | -14.09% |
| recent_from_2026_06_01 | blended_side_band_e05 | $31 | $+7.16 | $+3.88 | $+12.60 | +176.0% | $26 | $-5.44 | -20.93% |
| live_filled_only | blended_25_75_e05 | $152 | $+11.14 | $+3.00 | $+13.91 | +124.8% | $147 | $-2.77 | -1.88% |
| live_filled_only | blended_side_band_e05 | $94 | $+19.94 | $+3.88 | $+16.41 | +82.3% | $89 | $+3.53 | +3.97% |

## Top Winners

| slice | profile | city | date | bracket | side | entry | pnl | edge | p_used | market_p_yes |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|
| holdout_from_2026_05_26 | blended_25_75_e05 | NYC | 2026-05-26 | 80-81 | BUY_YES | 0.250 | $+3.00 | 0.063 | 0.313 | 0.250 |
| holdout_from_2026_05_26 | blended_25_75_e05 | Istanbul | 2026-05-29 | 19 | BUY_YES | 0.250 | $+3.00 | 0.056 | 0.306 | 0.250 |
| holdout_from_2026_05_26 | blended_25_75_e05 | LA | 2026-05-27 | 64-65 | BUY_YES | 0.255 | $+2.92 | 0.106 | 0.361 | 0.255 |
| holdout_from_2026_05_26 | blended_25_75_e05 | Atlanta | 2026-05-29 | 82-83 | BUY_YES | 0.255 | $+2.92 | 0.101 | 0.356 | 0.255 |
| holdout_from_2026_05_26 | blended_25_75_e05 | LA | 2026-06-03 | 68-69 | BUY_YES | 0.280 | $+2.57 | 0.114 | 0.394 | 0.280 |
| holdout_from_2026_05_26 | blended_side_band_e05 | Madrid | 2026-06-04 | 30 | BUY_YES | 0.205 | $+3.88 | 0.080 | 0.285 | 0.205 |
| holdout_from_2026_05_26 | blended_side_band_e05 | London | 2026-06-01 | 23 | BUY_YES | 0.210 | $+3.76 | 0.067 | 0.277 | 0.210 |
| holdout_from_2026_05_26 | blended_side_band_e05 | Miami | 2026-05-30 | 86-87 | BUY_YES | 0.225 | $+3.44 | 0.114 | 0.339 | 0.225 |
| holdout_from_2026_05_26 | blended_side_band_e05 | Miami | 2026-05-26 | 86-87 | BUY_YES | 0.230 | $+3.35 | 0.106 | 0.336 | 0.230 |
| holdout_from_2026_05_26 | blended_side_band_e05 | Warsaw | 2026-05-30 | 22 | BUY_YES | 0.235 | $+3.26 | 0.067 | 0.302 | 0.235 |
| recent_from_2026_06_01 | blended_25_75_e05 | LA | 2026-06-03 | 68-69 | BUY_YES | 0.280 | $+2.57 | 0.114 | 0.394 | 0.280 |
| recent_from_2026_06_01 | blended_25_75_e05 | Madrid | 2026-06-04 | 29 | BUY_NO | 0.415 | $+1.41 | 0.089 | 0.496 | 0.585 |
| recent_from_2026_06_01 | blended_25_75_e05 | Madrid | 2026-06-03 | 32 | BUY_NO | 0.505 | $+0.98 | 0.055 | 0.440 | 0.495 |
| recent_from_2026_06_01 | blended_25_75_e05 | LA | 2026-06-03 | 70-71 | BUY_NO | 0.530 | $+0.89 | 0.095 | 0.375 | 0.470 |
| recent_from_2026_06_01 | blended_25_75_e05 | Karachi | 2026-06-02 | 35 | BUY_NO | 0.540 | $+0.85 | 0.092 | 0.368 | 0.460 |
| recent_from_2026_06_01 | blended_side_band_e05 | Madrid | 2026-06-04 | 30 | BUY_YES | 0.205 | $+3.88 | 0.080 | 0.285 | 0.205 |
| recent_from_2026_06_01 | blended_side_band_e05 | London | 2026-06-01 | 23 | BUY_YES | 0.210 | $+3.76 | 0.067 | 0.277 | 0.210 |
| recent_from_2026_06_01 | blended_side_band_e05 | LA | 2026-06-03 | 68-69 | BUY_YES | 0.280 | $+2.57 | 0.114 | 0.394 | 0.280 |
| recent_from_2026_06_01 | blended_side_band_e05 | Madrid | 2026-06-04 | 29 | BUY_NO | 0.415 | $+1.41 | 0.089 | 0.496 | 0.585 |
| recent_from_2026_06_01 | blended_side_band_e05 | Madrid | 2026-06-03 | 32 | BUY_NO | 0.505 | $+0.98 | 0.055 | 0.440 | 0.495 |
| live_filled_only | blended_25_75_e05 | Istanbul | 2026-05-29 | 19 | BUY_YES | 0.250 | $+3.00 | 0.056 | 0.306 | 0.250 |
| live_filled_only | blended_25_75_e05 | LA | 2026-05-27 | 64-65 | BUY_YES | 0.255 | $+2.92 | 0.106 | 0.361 | 0.255 |
| live_filled_only | blended_25_75_e05 | Miami | 2026-05-25 | 86-87 | BUY_YES | 0.260 | $+2.85 | 0.093 | 0.353 | 0.260 |
| live_filled_only | blended_25_75_e05 | Warsaw | 2026-05-24 | 28 | BUY_YES | 0.280 | $+2.57 | 0.052 | 0.332 | 0.280 |
| live_filled_only | blended_25_75_e05 | LA | 2026-06-03 | 68-69 | BUY_YES | 0.280 | $+2.57 | 0.114 | 0.394 | 0.280 |
| live_filled_only | blended_side_band_e05 | Madrid | 2026-06-04 | 30 | BUY_YES | 0.205 | $+3.88 | 0.080 | 0.285 | 0.205 |
| live_filled_only | blended_side_band_e05 | London | 2026-06-01 | 23 | BUY_YES | 0.210 | $+3.76 | 0.067 | 0.277 | 0.210 |
| live_filled_only | blended_side_band_e05 | Istanbul | 2026-05-29 | 19 | BUY_YES | 0.250 | $+3.00 | 0.056 | 0.306 | 0.250 |
| live_filled_only | blended_side_band_e05 | LA | 2026-05-27 | 64-65 | BUY_YES | 0.255 | $+2.92 | 0.106 | 0.361 | 0.255 |
| live_filled_only | blended_side_band_e05 | Miami | 2026-05-25 | 86-87 | BUY_YES | 0.260 | $+2.85 | 0.093 | 0.353 | 0.260 |

## City Robustness

| slice | profile | row set | city | legs | days | cost | pnl | ROI | win_rate |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| full | blended_25_75_e05 | top/bottom | Warsaw | 16 | 11 | $16 | $+12.22 | +76.38% | 87.5% |
| full | blended_25_75_e05 | top/bottom | LA | 30 | 19 | $30 | $+9.31 | +31.04% | 60.0% |
| full | blended_25_75_e05 | top/bottom | London | 27 | 17 | $27 | $+5.10 | +18.89% | 70.4% |
| full | blended_25_75_e05 | top/bottom | Tokyo | 17 | 12 | $17 | $+4.94 | +29.09% | 70.6% |
| full | blended_25_75_e05 | top/bottom | Chengdu | 8 | 6 | $8 | $+4.45 | +55.60% | 87.5% |
| full | blended_25_75_e05 | top/bottom | Istanbul | 14 | 11 | $14 | $-1.63 | -11.61% | 42.9% |
| full | blended_25_75_e05 | top/bottom | Jakarta | 5 | 4 | $5 | $-2.07 | -41.41% | 40.0% |
| full | blended_25_75_e05 | top/bottom | CapeTown | 7 | 7 | $7 | $-2.20 | -31.42% | 42.9% |
| full | blended_25_75_e05 | top/bottom | Shenzhen | 9 | 8 | $9 | $-3.33 | -36.99% | 44.4% |
| full | blended_25_75_e05 | top/bottom | Wellington | 12 | 11 | $12 | $-5.71 | -47.59% | 33.3% |
| full | blended_side_band_e05 | top/bottom | Warsaw | 13 | 9 | $13 | $+15.24 | +117.20% | 92.3% |
| full | blended_side_band_e05 | top/bottom | LA | 23 | 16 | $23 | $+12.09 | +52.56% | 56.5% |
| full | blended_side_band_e05 | top/bottom | Miami | 31 | 21 | $31 | $+7.14 | +23.03% | 51.6% |
| full | blended_side_band_e05 | top/bottom | Atlanta | 15 | 10 | $15 | $+6.14 | +40.93% | 53.3% |
| full | blended_side_band_e05 | top/bottom | Tokyo | 12 | 11 | $12 | $+5.68 | +47.32% | 75.0% |
| full | blended_side_band_e05 | top/bottom | Ankara | 8 | 8 | $8 | $-2.48 | -30.98% | 37.5% |
| full | blended_side_band_e05 | top/bottom | CapeTown | 6 | 6 | $6 | $-2.69 | -44.87% | 33.3% |
| full | blended_side_band_e05 | top/bottom | BuenosAires | 8 | 6 | $8 | $-3.00 | -37.44% | 37.5% |
| full | blended_side_band_e05 | top/bottom | Seoul | 6 | 6 | $6 | $-4.40 | -73.33% | 16.7% |
| full | blended_side_band_e05 | top/bottom | Wellington | 9 | 9 | $9 | $-7.02 | -78.00% | 11.1% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | LA | 13 | 7 | $13 | $+10.95 | +84.20% | 76.9% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Chengdu | 2 | 2 | $2 | $+2.20 | +109.81% | 100.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Houston | 3 | 2 | $3 | $+1.98 | +65.98% | 66.7% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | SanFrancisco | 3 | 3 | $3 | $+1.86 | +61.91% | 100.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | London | 9 | 6 | $9 | $+1.55 | +17.26% | 77.8% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Guangzhou | 2 | 2 | $2 | $-2.00 | -100.00% | 0.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Ankara | 10 | 7 | $10 | $-2.14 | -21.40% | 50.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Miami | 11 | 8 | $11 | $-2.16 | -19.62% | 45.5% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | NYC | 9 | 5 | $9 | $-2.24 | -24.90% | 33.3% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Munich | 6 | 5 | $6 | $-2.59 | -43.19% | 33.3% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | LA | 11 | 6 | $11 | $+8.66 | +78.72% | 63.6% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Madrid | 5 | 3 | $5 | $+4.27 | +85.36% | 60.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Warsaw | 2 | 1 | $2 | $+4.07 | +203.68% | 100.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Miami | 13 | 8 | $13 | $+3.11 | +23.91% | 46.2% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | London | 5 | 5 | $5 | $+3.02 | +60.43% | 60.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Guangzhou | 2 | 2 | $2 | $-2.00 | -100.00% | 0.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Munich | 4 | 4 | $4 | $-2.10 | -52.38% | 25.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Ankara | 6 | 6 | $6 | $-2.56 | -42.69% | 33.3% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | BuenosAires | 6 | 4 | $6 | $-2.73 | -45.58% | 33.3% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | NYC | 8 | 5 | $8 | $-4.00 | -50.00% | 12.5% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Karachi | 4 | 4 | $4 | $+2.99 | +74.79% | 100.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Madrid | 2 | 2 | $2 | $+2.39 | +119.49% | 100.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | LA | 8 | 4 | $8 | $+1.75 | +21.82% | 62.5% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Shanghai | 2 | 2 | $2 | $+0.96 | +48.03% | 100.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | London | 4 | 3 | $4 | $+0.54 | +13.42% | 75.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Manila | 1 | 1 | $1 | $-1.00 | -100.00% | 0.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Seattle | 1 | 1 | $1 | $-1.00 | -100.00% | 0.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Miami | 3 | 3 | $3 | $-1.25 | -41.52% | 33.3% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | NYC | 3 | 1 | $3 | $-1.60 | -53.38% | 33.3% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Ankara | 5 | 4 | $5 | $-1.69 | -33.75% | 40.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Madrid | 3 | 2 | $3 | $+6.27 | +208.93% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | London | 3 | 3 | $3 | $+3.31 | +110.41% | 66.7% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Karachi | 4 | 4 | $4 | $+2.99 | +74.79% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Moscow | 1 | 1 | $1 | $+0.68 | +68.07% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Shanghai | 1 | 1 | $1 | $+0.60 | +60.00% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Amsterdam | 2 | 2 | $2 | $-0.40 | -20.00% | 50.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Guangzhou | 1 | 1 | $1 | $-1.00 | -100.00% | 0.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Ankara | 3 | 3 | $3 | $-1.21 | -40.48% | 33.3% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | NYC | 2 | 1 | $2 | $-2.00 | -100.00% | 0.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Miami | 4 | 3 | $4 | $-2.25 | -56.14% | 25.0% |

## Date Robustness

| slice | profile | date | legs | cost | pnl | ROI | win_rate |
|---|---|---|---:|---:|---:|---:|---:|
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-05-29 | 29 | $29 | $+17.69 | +61.01% | 79.3% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-06-03 | 9 | $9 | $+6.02 | +66.94% | 88.9% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-05-27 | 28 | $28 | $+2.59 | +9.25% | 57.1% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-06-02 | 13 | $13 | $+1.35 | +10.36% | 69.2% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-05-26 | 25 | $25 | $-1.35 | -5.38% | 48.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-06-01 | 9 | $9 | $-1.57 | -17.40% | 55.6% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-05-31 | 7 | $7 | $-1.95 | -27.80% | 42.9% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-05-30 | 31 | $31 | $-2.52 | -8.14% | 61.3% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-06-04 | 17 | $17 | $-5.16 | -30.38% | 41.2% |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-05-28 | 33 | $33 | $-8.05 | -24.41% | 45.5% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-29 | 20 | $20 | $+16.54 | +82.72% | 80.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-06-03 | 5 | $5 | $+5.72 | +114.38% | 100.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-30 | 14 | $14 | $+1.68 | +11.99% | 42.9% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-26 | 18 | $18 | $+1.55 | +8.61% | 38.9% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-06-01 | 5 | $5 | $+1.53 | +30.64% | 40.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-06-02 | 7 | $7 | $+1.47 | +20.94% | 71.4% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-27 | 19 | $19 | $-0.15 | -0.77% | 42.1% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-31 | 6 | $6 | $-0.95 | -15.77% | 50.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-06-04 | 14 | $14 | $-1.56 | -11.12% | 35.7% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-28 | 22 | $22 | $-7.37 | -33.49% | 36.4% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-03 | 9 | $9 | $+6.02 | +66.94% | 88.9% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-02 | 13 | $13 | $+1.35 | +10.36% | 69.2% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-01 | 9 | $9 | $-1.57 | -17.40% | 55.6% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-04 | 17 | $17 | $-5.16 | -30.38% | 41.2% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-03 | 5 | $5 | $+5.72 | +114.38% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-01 | 5 | $5 | $+1.53 | +30.64% | 40.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-02 | 7 | $7 | $+1.47 | +20.94% | 71.4% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-04 | 14 | $14 | $-1.56 | -11.12% | 35.7% |

## Current Live Actual Filled Baseline

| strategy_instance | fills | dates | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| t1_trading_maker_queue_v1_notional_$5.0_shares_10.0_entry_0.25-0.75_mqe_0.03 | 74 | 2026-05-24 -> 2026-06-01 | $285 | $-23 | -8.10% | 55.4% |
| t1_trading_maker_queue_v2_notional_$5.0_shares_10.0_entry_0.25-0.75 | 76 | 2026-05-27 -> 2026-05-30 | $271 | $+37 | +13.57% | 59.2% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.20-0.45 | 20 | 2026-06-01 -> 2026-06-04 | $44 | $+33 | +74.45% | 65.0% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | 382 | 2026-05-16 -> 2026-06-04 | $1332 | $+34 | +2.58% | 57.6% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.35-0.65 | 28 | 2026-06-01 -> 2026-06-04 | $90 | $-8 | -8.70% | 57.1% |
| t1_trading_mid_price_core_v2_notional_$5.0_shares_10.0_entry_0.25-0.75 | 176 | 2026-05-29 -> 2026-06-04 | $475 | $-48 | -10.01% | 44.3% |

## Read

- Use the submitted-order fill-rate estimate as the normal case if the branch can submit orders cleanly.
- Use the all-attempt estimate as the conservative case because it includes historical order errors.
- The old-live overlap column is stricter: it only counts rows that historical live actually filled. It can understate a new paper strategy because many selected rows were never attempted live by the old strategy.
- ROI does not change under simple fill-rate scaling; only estimated fills, cost, and absolute PnL change.
- The bootstrap rows resample by target date, not by individual leg, so they are a rough guard against one lucky day dominating the result.
