# Blended Paper Profile Fill-Rate Estimate

> generated_at_utc: `2026-06-07T05:30:21+00:00`
> db: `/home/rui/projects/pm_agent/runtime/weather.db`

## Target Metric

`blended_e05_fill_rate_estimate` = replay the selected blended paper profiles on settled historical candidates, then estimate fills/PnL using historical CLOB fill rates.

This is not actual wallet PnL. It is a counterfactual opportunity replay plus fill-rate scaling.

## Data Self-Check

```json
{
  "fact_trades_freshness": [
    {
      "max_fact_built_at_utc": "2026-06-07T05:29:22.795919+00:00"
    }
  ],
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 1302
    },
    {
      "trade_class": "live_simulated",
      "rows": 1077
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
      "rows": 388
    },
    {
      "settlement_status": "settled",
      "rows": 4912
    }
  ],
  "fact_signal_candidates_coverage": [
    {
      "rows": 23299,
      "eligible": 7567,
      "paper_ordered": 2795,
      "live_filled": 503
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
      "orders": 1524,
      "with_fill": 1302
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
      "max_date": "2026-06-08",
      "rows": 23299,
      "days": 35
    }
  ],
  "candidate_settled_complete_range": [
    {
      "min_date": "2026-05-06",
      "max_date": "2026-06-05",
      "rows": 2175,
      "days": 30
    }
  ],
  "live_real_settled_range": [
    {
      "min_date": "2026-05-16",
      "max_date": "2026-06-05",
      "fills": 1121,
      "days": 19,
      "cost_usd": 3015.685813,
      "pnl_usd": -43.33001300000001
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

- submitted order fill rate: `85.4%` (1302/1524)
- all order-attempt fill rate, including errors: `77.7%` (1302/1675)

## Aligned Decision Table

Same denominator for old and new: settled complete `fact_signal_candidates`, `$1/leg`, same entry-window family, same submitted-order fill-rate estimate.

| family | slice | old profile | old legs | old ROI | old est PnL | new profile | new legs | new ROI | new est PnL | new-old est PnL | new top5-removed ROI | new P(ROI>0) |
|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| 25_75 | full | raw_current_25_75 | 905 | +3.54% | $+27.35 | blended_25_75_e05 | 547 | +9.71% | $+45.39 | $+18.04 | +7.08% | 97.8% |
| side_band | full | raw_current_side_band | 448 | +8.24% | $+31.52 | blended_side_band_e05 | 345 | +15.03% | $+44.30 | $+12.78 | +10.02% | 98.6% |
| 25_75 | holdout_from_2026_05_26 | raw_current_25_75 | 334 | -5.55% | $-15.82 | blended_25_75_e05 | 216 | +0.00% | $+0.00 | $+15.83 | -6.83% | 47.7% |
| side_band | holdout_from_2026_05_26 | raw_current_side_band | 174 | +7.54% | $+11.21 | blended_side_band_e05 | 141 | +9.00% | $+10.84 | $-0.38 | -3.68% | 73.0% |
| 25_75 | recent_from_2026_06_01 | raw_current_25_75 | 105 | -4.75% | $-4.26 | blended_25_75_e05 | 63 | -10.17% | $-5.47 | $-1.21 | -22.60% | 28.3% |
| side_band | recent_from_2026_06_01 | raw_current_side_band | 57 | +10.86% | $+5.29 | blended_side_band_e05 | 42 | +3.26% | $+1.17 | $-4.12 | -30.35% | 60.3% |
| 25_75 | live_filled_only | raw_current_25_75 | 256 | +4.33% | $+9.47 | blended_25_75_e05 | 167 | +2.45% | $+3.50 | $-5.98 | -6.06% | 59.1% |
| side_band | live_filled_only | raw_current_side_band | 127 | +12.14% | $+13.17 | blended_side_band_e05 | 105 | +13.48% | $+12.09 | $-1.08 | -2.26% | 82.0% |

## Replay Summary ($1 Notional Per Leg)

| slice | profile | date range | legs | days | cost | pnl | ROI | top5-removed ROI | day win | old-live overlap | overlap ROI | est fills @submitted | est pnl @submitted |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | raw_current_25_75 | 2026-05-06 -> 2026-06-05 | 905 | 30 | $905 | $+32.01 | +3.54% | +1.92% | 60.0% | 256/905 (28.3%) | +4.33% | 773.2 | $+27.35 |
| full | blended_25_75_e05 | 2026-05-06 -> 2026-06-05 | 547 | 29 | $547 | $+53.13 | +9.71% | +7.08% | 65.5% | 167/547 (30.5%) | +2.45% | 467.3 | $+45.39 |
| full | raw_current_side_band | 2026-05-06 -> 2026-06-05 | 448 | 29 | $448 | $+36.90 | +8.24% | +4.34% | 65.5% | 127/448 (28.3%) | +12.14% | 382.7 | $+31.52 |
| full | blended_side_band_e05 | 2026-05-06 -> 2026-06-05 | 345 | 29 | $345 | $+51.85 | +15.03% | +10.02% | 69.0% | 105/345 (30.4%) | +13.48% | 294.7 | $+44.30 |
| holdout_from_2026_05_26 | raw_current_25_75 | 2026-05-26 -> 2026-06-05 | 334 | 11 | $334 | $-18.52 | -5.55% | -10.01% | 36.4% | 194/334 (58.1%) | -3.58% | 285.3 | $-15.82 |
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-05-26 -> 2026-06-05 | 216 | 11 | $216 | $+0.01 | +0.00% | -6.83% | 36.4% | 127/216 (58.8%) | -5.64% | 184.5 | $+0.00 |
| holdout_from_2026_05_26 | raw_current_side_band | 2026-05-26 -> 2026-06-05 | 174 | 11 | $174 | $+13.12 | +7.54% | -2.70% | 63.6% | 96/174 (55.2%) | +5.42% | 148.7 | $+11.21 |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-26 -> 2026-06-05 | 141 | 11 | $141 | $+12.68 | +9.00% | -3.68% | 54.5% | 81/141 (57.4%) | +3.47% | 120.5 | $+10.84 |
| recent_from_2026_06_01 | raw_current_25_75 | 2026-06-01 -> 2026-06-05 | 105 | 5 | $105 | $-4.99 | -4.75% | -14.69% | 40.0% | 93/105 (88.6%) | -6.41% | 89.7 | $-4.26 |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-01 -> 2026-06-05 | 63 | 5 | $63 | $-6.41 | -10.17% | -22.60% | 40.0% | 54/63 (85.7%) | -16.16% | 53.8 | $-5.47 |
| recent_from_2026_06_01 | raw_current_side_band | 2026-06-01 -> 2026-06-05 | 57 | 5 | $57 | $+6.19 | +10.86% | -12.33% | 80.0% | 49/57 (86.0%) | +11.32% | 48.7 | $+5.29 |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-01 -> 2026-06-05 | 42 | 5 | $42 | $+1.37 | +3.26% | -30.35% | 60.0% | 36/42 (85.7%) | +1.05% | 35.9 | $+1.17 |
| live_filled_only | raw_current_25_75 | 2026-05-16 -> 2026-06-05 | 256 | 19 | $256 | $+11.09 | +4.33% | -1.12% | 57.9% | 256/256 (100.0%) | +4.33% | 218.7 | $+9.47 |
| live_filled_only | blended_25_75_e05 | 2026-05-16 -> 2026-06-05 | 167 | 19 | $167 | $+4.09 | +2.45% | -6.06% | 42.1% | 167/167 (100.0%) | +2.45% | 142.7 | $+3.50 |
| live_filled_only | raw_current_side_band | 2026-05-16 -> 2026-06-05 | 127 | 19 | $127 | $+15.42 | +12.14% | -0.46% | 63.2% | 127/127 (100.0%) | +12.14% | 108.5 | $+13.17 |
| live_filled_only | blended_side_band_e05 | 2026-05-16 -> 2026-06-05 | 105 | 19 | $105 | $+14.15 | +13.48% | -2.26% | 52.6% | 105/105 (100.0%) | +13.48% | 89.7 | $+12.09 |

## Blended Vs Current Raw Gate

| slice | pair | shared | blend_only | raw_only | net PnL delta | bootstrap ROI p05/p50/p95 | P(ROI>0) |
|---|---|---:|---:|---:|---:|---:|---:|
| full | blended_25_75_e05_vs_raw_current_25_75 | 547 | 0 | 358 | $+21.12 | +1.8% / +9.7% / +17.8% | 97.8% |
| full | blended_side_band_e05_vs_raw_current_side_band | 318 | 27 | 130 | $+14.96 | +3.5% / +15.2% / +26.5% | 98.6% |
| holdout_from_2026_05_26 | blended_25_75_e05_vs_raw_current_25_75 | 216 | 0 | 118 | $+18.53 | -16.3% / -0.7% / +18.9% | 47.7% |
| holdout_from_2026_05_26 | blended_side_band_e05_vs_raw_current_side_band | 133 | 8 | 41 | $-0.44 | -12.4% / +8.3% / +34.2% | 73.0% |
| recent_from_2026_06_01 | blended_25_75_e05_vs_raw_current_25_75 | 63 | 0 | 42 | $-1.42 | -33.0% / -10.2% / +21.4% | 28.3% |
| recent_from_2026_06_01 | blended_side_band_e05_vs_raw_current_side_band | 41 | 1 | 16 | $-4.82 | -23.9% / +3.3% / +50.4% | 60.3% |
| live_filled_only | blended_25_75_e05_vs_raw_current_25_75 | 167 | 0 | 89 | $-7.00 | -14.0% / +2.4% / +20.0% | 59.1% |
| live_filled_only | blended_side_band_e05_vs_raw_current_side_band | 100 | 5 | 27 | $-1.26 | -10.1% / +13.5% / +39.8% | 82.0% |

## Mid Price V1 Actual Live Baseline

| baseline | fills | dates | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| all_mid_price_core_v1 | 811 | 2026-05-16 -> 2026-06-05 | $2192 | $+35 | +1.59% | 52.2% |
| combined_side_band_windows | 104 | 2026-06-01 -> 2026-06-05 | $242 | $+25 | +10.24% | 47.1% |
| mid_v1 window 0.20-0.45 | 48 | 2026-06-01 -> 2026-06-05 | $79 | $-1 | -0.96% | 22.9% |
| mid_v1 window 0.25-0.75 | 707 | 2026-05-16 -> 2026-06-05 | $1950 | $+10 | +0.51% | 52.9% |
| mid_v1 window 0.35-0.65 | 56 | 2026-06-01 -> 2026-06-05 | $162 | $+26 | +15.72% | 67.9% |

## Tail Dependence Drilldown

| slice | profile | total cost | total pnl | max win | top5 pnl | top5 pnl / total pnl | after top5 cost | after top5 pnl | after top5 ROI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | blended_25_75_e05 | $547 | $+53.13 | $+3.00 | $+14.76 | +27.8% | $542 | $+38.37 | +7.08% |
| full | blended_side_band_e05 | $345 | $+51.85 | $+3.88 | $+17.78 | +34.3% | $340 | $+34.07 | +10.02% |
| holdout_from_2026_05_26 | blended_25_75_e05 | $216 | $+0.01 | $+3.00 | $+14.41 | +268827.4% | $211 | $-14.41 | -6.83% |
| holdout_from_2026_05_26 | blended_side_band_e05 | $141 | $+12.68 | $+3.88 | $+17.69 | +139.5% | $136 | $-5.00 | -3.68% |
| recent_from_2026_06_01 | blended_25_75_e05 | $63 | $-6.41 | $+2.57 | $+6.70 | -104.6% | $58 | $-13.11 | -22.60% |
| recent_from_2026_06_01 | blended_side_band_e05 | $42 | $+1.37 | $+3.88 | $+12.60 | +919.0% | $37 | $-11.23 | -30.35% |
| live_filled_only | blended_25_75_e05 | $167 | $+4.09 | $+3.00 | $+13.91 | +339.9% | $162 | $-9.82 | -6.06% |
| live_filled_only | blended_side_band_e05 | $105 | $+14.15 | $+3.88 | $+16.41 | +115.9% | $100 | $-2.26 | -2.26% |

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
| full | blended_25_75_e05 | top/bottom | Tokyo | 18 | 13 | $18 | $+5.78 | +32.11% | 72.2% |
| full | blended_25_75_e05 | top/bottom | Chengdu | 8 | 6 | $8 | $+4.45 | +55.60% | 87.5% |
| full | blended_25_75_e05 | top/bottom | London | 28 | 18 | $28 | $+4.10 | +14.65% | 67.9% |
| full | blended_25_75_e05 | top/bottom | Jeddah | 9 | 8 | $9 | $-1.63 | -18.10% | 55.6% |
| full | blended_25_75_e05 | top/bottom | Jakarta | 5 | 4 | $5 | $-2.07 | -41.41% | 40.0% |
| full | blended_25_75_e05 | top/bottom | CapeTown | 7 | 7 | $7 | $-2.20 | -31.42% | 42.9% |
| full | blended_25_75_e05 | top/bottom | Shenzhen | 9 | 8 | $9 | $-3.33 | -36.99% | 44.4% |
| full | blended_25_75_e05 | top/bottom | Wellington | 12 | 11 | $12 | $-5.71 | -47.59% | 33.3% |
| full | blended_side_band_e05 | top/bottom | Warsaw | 13 | 9 | $13 | $+15.24 | +117.20% | 92.3% |
| full | blended_side_band_e05 | top/bottom | LA | 23 | 16 | $23 | $+12.09 | +52.56% | 56.5% |
| full | blended_side_band_e05 | top/bottom | Tokyo | 13 | 12 | $13 | $+6.51 | +50.10% | 76.9% |
| full | blended_side_band_e05 | top/bottom | Atlanta | 15 | 10 | $15 | $+6.14 | +40.93% | 53.3% |
| full | blended_side_band_e05 | top/bottom | Miami | 33 | 22 | $33 | $+5.14 | +15.57% | 48.5% |
| full | blended_side_band_e05 | top/bottom | CapeTown | 6 | 6 | $6 | $-2.69 | -44.87% | 33.3% |
| full | blended_side_band_e05 | top/bottom | BuenosAires | 9 | 7 | $9 | $-4.00 | -44.39% | 33.3% |
| full | blended_side_band_e05 | top/bottom | NYC | 21 | 14 | $21 | $-4.04 | -19.25% | 33.3% |
| full | blended_side_band_e05 | top/bottom | Seoul | 6 | 6 | $6 | $-4.40 | -73.33% | 16.7% |
| full | blended_side_band_e05 | top/bottom | Wellington | 9 | 9 | $9 | $-7.02 | -78.00% | 11.1% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | LA | 13 | 7 | $13 | $+10.95 | +84.20% | 76.9% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Chengdu | 2 | 2 | $2 | $+2.20 | +109.81% | 100.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Houston | 3 | 2 | $3 | $+1.98 | +65.98% | 66.7% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | SanFrancisco | 3 | 3 | $3 | $+1.86 | +61.91% | 100.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Warsaw | 3 | 2 | $3 | $+1.55 | +51.61% | 100.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Guangzhou | 2 | 2 | $2 | $-2.00 | -100.00% | 0.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Munich | 6 | 5 | $6 | $-2.59 | -43.19% | 33.3% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | NYC | 10 | 6 | $10 | $-3.24 | -32.41% | 30.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Jeddah | 5 | 4 | $5 | $-3.51 | -70.15% | 20.0% |
| holdout_from_2026_05_26 | blended_25_75_e05 | top/bottom | Miami | 13 | 9 | $13 | $-4.16 | -31.98% | 38.5% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | LA | 11 | 6 | $11 | $+8.66 | +78.72% | 63.6% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Madrid | 5 | 3 | $5 | $+4.27 | +85.36% | 60.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Warsaw | 2 | 1 | $2 | $+4.07 | +203.68% | 100.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | London | 6 | 6 | $6 | $+2.02 | +33.69% | 50.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Istanbul | 2 | 2 | $2 | $+2.00 | +100.00% | 50.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | CapeTown | 2 | 2 | $2 | $-2.00 | -100.00% | 0.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Guangzhou | 2 | 2 | $2 | $-2.00 | -100.00% | 0.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | Munich | 4 | 4 | $4 | $-2.10 | -52.38% | 25.0% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | BuenosAires | 7 | 5 | $7 | $-3.73 | -53.35% | 28.6% |
| holdout_from_2026_05_26 | blended_side_band_e05 | top/bottom | NYC | 10 | 6 | $10 | $-6.00 | -60.00% | 10.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Madrid | 2 | 2 | $2 | $+2.39 | +119.49% | 100.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | LA | 8 | 4 | $8 | $+1.75 | +21.82% | 62.5% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Karachi | 6 | 5 | $6 | $+0.99 | +16.53% | 66.7% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Shanghai | 2 | 2 | $2 | $+0.96 | +48.03% | 100.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Tokyo | 3 | 3 | $3 | $+0.54 | +18.14% | 66.7% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Seattle | 1 | 1 | $1 | $-1.00 | -100.00% | 0.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Ankara | 6 | 5 | $6 | $-1.02 | -17.02% | 50.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Jeddah | 4 | 3 | $4 | $-2.51 | -62.69% | 25.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | NYC | 4 | 2 | $4 | $-2.60 | -65.03% | 25.0% |
| recent_from_2026_06_01 | blended_25_75_e05 | top/bottom | Miami | 5 | 4 | $5 | $-3.25 | -64.91% | 20.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Madrid | 3 | 2 | $3 | $+6.27 | +208.93% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | London | 4 | 4 | $4 | $+2.31 | +57.81% | 50.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Karachi | 6 | 5 | $6 | $+0.99 | +16.53% | 66.7% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Manila | 1 | 1 | $1 | $+0.71 | +70.94% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Moscow | 1 | 1 | $1 | $+0.68 | +68.07% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Ankara | 4 | 4 | $4 | $-0.55 | -13.69% | 50.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | BuenosAires | 1 | 1 | $1 | $-1.00 | -100.00% | 0.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Guangzhou | 1 | 1 | $1 | $-1.00 | -100.00% | 0.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | NYC | 4 | 2 | $4 | $-4.00 | -100.00% | 0.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | top/bottom | Miami | 6 | 4 | $6 | $-4.25 | -70.76% | 16.7% |

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
| holdout_from_2026_05_26 | blended_25_75_e05 | 2026-06-05 | 15 | $15 | $-7.05 | -47.00% | 33.3% |
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
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-06-05 | 11 | $11 | $-5.79 | -52.63% | 27.3% |
| holdout_from_2026_05_26 | blended_side_band_e05 | 2026-05-28 | 22 | $22 | $-7.37 | -33.49% | 36.4% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-03 | 9 | $9 | $+6.02 | +66.94% | 88.9% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-02 | 13 | $13 | $+1.35 | +10.36% | 69.2% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-01 | 9 | $9 | $-1.57 | -17.40% | 55.6% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-04 | 17 | $17 | $-5.16 | -30.38% | 41.2% |
| recent_from_2026_06_01 | blended_25_75_e05 | 2026-06-05 | 15 | $15 | $-7.05 | -47.00% | 33.3% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-03 | 5 | $5 | $+5.72 | +114.38% | 100.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-01 | 5 | $5 | $+1.53 | +30.64% | 40.0% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-02 | 7 | $7 | $+1.47 | +20.94% | 71.4% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-04 | 14 | $14 | $-1.56 | -11.12% | 35.7% |
| recent_from_2026_06_01 | blended_side_band_e05 | 2026-06-05 | 11 | $11 | $-5.79 | -52.63% | 27.3% |

## Current Live Actual Filled Baseline

| strategy_instance | fills | dates | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| t1_trading_maker_queue_v1_notional_$5.0_shares_10.0_entry_0.25-0.75_mqe_0.03 | 76 | 2026-05-24 -> 2026-06-01 | $213 | $-28 | -13.28% | 53.9% |
| t1_trading_maker_queue_v2_notional_$5.0_shares_10.0_entry_0.25-0.75 | 59 | 2026-05-27 -> 2026-05-30 | $188 | $+23 | +12.21% | 52.5% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.20-0.45 | 47 | 2026-06-01 -> 2026-06-05 | $92 | $+31 | +33.14% | 40.4% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | 707 | 2026-05-16 -> 2026-06-05 | $1950 | $+10 | +0.51% | 52.9% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.35-0.65 | 57 | 2026-06-01 -> 2026-06-05 | $150 | $-6 | -3.87% | 52.6% |
| t1_trading_mid_price_core_v2_notional_$5.0_shares_10.0_entry_0.25-0.75 | 175 | 2026-05-29 -> 2026-06-05 | $423 | $-73 | -17.21% | 38.9% |

## Read

- Use the submitted-order fill-rate estimate as the normal case if the branch can submit orders cleanly.
- Use the all-attempt estimate as the conservative case because it includes historical order errors.
- The old-live overlap column is stricter: it only counts rows that historical live actually filled. It can understate a new paper strategy because many selected rows were never attempted live by the old strategy.
- ROI does not change under simple fill-rate scaling; only estimated fills, cost, and absolute PnL change.
- The bootstrap rows resample by target date, not by individual leg, so they are a rough guard against one lucky day dominating the result.
