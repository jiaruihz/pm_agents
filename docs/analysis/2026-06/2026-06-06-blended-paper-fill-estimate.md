# Blended Paper Profile Fill-Rate Estimate

> generated_at_utc: `2026-06-06T01:07:56+00:00`
> db: `/home/rui/projects/pm_agent/runtime/weather.db`

## Target Metric

`blended_e05_fill_rate_estimate` = replay the selected blended paper profiles on settled historical candidates, then estimate fills/PnL using historical CLOB fill rates.

This is not actual wallet PnL. It is a counterfactual opportunity replay plus fill-rate scaling.

## Data Self-Check

```json
{
  "fact_trades_freshness": [
    {
      "max_fact_built_at_utc": "2026-06-05T18:31:00.166369+00:00"
    }
  ],
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 844
    },
    {
      "trade_class": "live_simulated",
      "rows": 1021
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
      "rows": 299
    },
    {
      "settlement_status": "missing_bracket",
      "rows": 734
    },
    {
      "settlement_status": "settled",
      "rows": 3753
    }
  ],
  "fact_signal_candidates_coverage": [
    {
      "rows": 21797,
      "eligible": 6963,
      "paper_ordered": 2570,
      "live_filled": 455
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
      "orders": 953,
      "with_fill": 844
    }
  ]
}
```

## Historical Fill Rates

- submitted order fill rate: `88.6%` (844/953)
- all order-attempt fill rate, including errors: `76.4%` (844/1104)

## Replay Summary ($1 Notional Per Leg)

| slice | profile | legs | days | cost | pnl | ROI | win_rate | old-live overlap | overlap ROI | est fills @submitted | est pnl @submitted | est fills @all-attempt | est pnl @all-attempt |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | raw_current_25_75 | 790 | 29 | $790 | $+39.12 | +4.95% | 61.1% | 206/790 (26.1%) | +5.74% | 699.6 | $+34.64 | 603.9 | $+29.90 |
| full | blended_25_75_e05 | 468 | 28 | $468 | $+48.49 | +10.36% | 64.5% | 127/468 (27.1%) | +4.69% | 414.5 | $+42.95 | 357.8 | $+37.07 |
| full | raw_current_side_band | 385 | 28 | $385 | $+26.18 | +6.80% | 56.4% | 99/385 (25.7%) | +17.88% | 341.0 | $+23.19 | 294.3 | $+20.02 |
| full | blended_side_band_e05 | 290 | 28 | $290 | $+41.54 | +14.32% | 55.5% | 78/290 (26.9%) | +21.77% | 256.8 | $+36.79 | 221.7 | $+31.75 |
| holdout_from_2026_05_26 | raw_current_25_75 | 235 | 10 | $235 | $-13.21 | -5.62% | 56.2% | 144/235 (61.3%) | -4.31% | 208.1 | $-11.70 | 179.7 | $-10.10 |
| holdout_from_2026_05_26 | blended_25_75_e05 | 147 | 10 | $147 | $-5.23 | -3.56% | 56.5% | 87/147 (59.2%) | -6.09% | 130.2 | $-4.63 | 112.4 | $-4.00 |
| holdout_from_2026_05_26 | raw_current_side_band | 119 | 10 | $119 | $+4.24 | +3.56% | 52.1% | 68/119 (57.1%) | +11.00% | 105.4 | $+3.76 | 91.0 | $+3.24 |
| holdout_from_2026_05_26 | blended_side_band_e05 | 91 | 10 | $91 | $+3.83 | +4.21% | 48.4% | 54/91 (59.3%) | +10.44% | 80.6 | $+3.39 | 69.6 | $+2.93 |
| recent_from_2026_06_01 | raw_current_25_75 | 84 | 4 | $84 | $+1.95 | +2.32% | 60.7% | 73/84 (86.9%) | -0.03% | 74.4 | $+1.73 | 64.2 | $+1.49 |
| recent_from_2026_06_01 | blended_25_75_e05 | 48 | 4 | $48 | $+0.64 | +1.34% | 60.4% | 39/48 (81.2%) | -4.29% | 42.5 | $+0.57 | 36.7 | $+0.49 |
| recent_from_2026_06_01 | raw_current_side_band | 44 | 4 | $44 | $+12.43 | +28.25% | 63.6% | 36/44 (81.8%) | +32.73% | 39.0 | $+11.01 | 33.6 | $+9.50 |
| recent_from_2026_06_01 | blended_side_band_e05 | 31 | 4 | $31 | $+7.16 | +23.10% | 54.8% | 25/31 (80.6%) | +24.67% | 27.5 | $+6.34 | 23.7 | $+5.47 |
| live_filled_only | raw_current_25_75 | 206 | 18 | $206 | $+11.82 | +5.74% | 60.2% | 206/206 (100.0%) | +5.74% | 182.4 | $+10.47 | 157.5 | $+9.04 |
| live_filled_only | blended_25_75_e05 | 127 | 18 | $127 | $+5.96 | +4.69% | 58.3% | 127/127 (100.0%) | +4.69% | 112.5 | $+5.28 | 97.1 | $+4.56 |
| live_filled_only | raw_current_side_band | 99 | 18 | $99 | $+17.70 | +17.88% | 58.6% | 99/99 (100.0%) | +17.88% | 87.7 | $+15.67 | 75.7 | $+13.53 |
| live_filled_only | blended_side_band_e05 | 78 | 18 | $78 | $+16.98 | +21.77% | 55.1% | 78/78 (100.0%) | +21.77% | 69.1 | $+15.04 | 59.6 | $+12.98 |

## Current Live Actual Filled Baseline

| strategy_instance | fills | dates | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| t1_trading_maker_queue_v1_notional_$5.0_shares_10.0_entry_0.25-0.75_mqe_0.03 | 65 | 2026-05-24 -> 2026-06-01 | $251 | $-17 | -6.73% | 55.4% |
| t1_trading_maker_queue_v2_notional_$5.0_shares_10.0_entry_0.25-0.75 | 54 | 2026-05-27 -> 2026-05-30 | $203 | $+20 | +9.69% | 59.3% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.20-0.45 | 20 | 2026-06-01 -> 2026-06-04 | $44 | $+33 | +74.45% | 65.0% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.25-0.75 | 331 | 2026-05-16 -> 2026-06-04 | $1152 | $+34 | +2.95% | 57.7% |
| t1_trading_mid_price_core_v1_notional_$5.0_shares_10.0_entry_0.35-0.65 | 28 | 2026-06-01 -> 2026-06-04 | $90 | $-8 | -8.70% | 57.1% |
| t1_trading_mid_price_core_v2_notional_$5.0_shares_10.0_entry_0.25-0.75 | 132 | 2026-05-29 -> 2026-06-04 | $367 | $-43 | -11.73% | 42.4% |

## Read

- Use the submitted-order fill-rate estimate as the normal case if the branch can submit orders cleanly.
- Use the all-attempt estimate as the conservative case because it includes historical order errors.
- The old-live overlap column is stricter: it only counts rows that historical live actually filled. It can understate a new paper strategy because many selected rows were never attempted live by the old strategy.
- ROI does not change under simple fill-rate scaling; only estimated fills, cost, and absolute PnL change.
