# Reheat Feature Factory Forecast Peak Slices v18

## Target Metric

`forecast_peak_clock_model_incremental_value` = using the shared reheat feature table, does forecast peak clock separate current-YES states with different hold probability and executable ROI proxy?

This report is an opportunity/replay slice. It is not live fill PnL and it does not change N100 behavior.

## Data Snapshot

- Generated at UTC: `2026-06-17T17:20:34+00:00`.
- Feature table: `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv`.
- DB fact built at UTC: `2026-06-17T17:09:13.232107+00:00`.
- CLOB gate pass: `True`.
- State rows: 8696; clocked current-YES rows: 3612.

## Human Summary

The forecast peak clock is now useful as a shared model feature, but it still has not earned the right to become a live hard gate.

The clearest practical use is risk labeling: states where GFS still places the peak at least two local hours ahead are bad for current YES in holdout. The more selective profitable bins exist, but they are too small to promote by themselves.

## Mandatory SQL Self-Check

```json
{
  "fact_trades_max_built_at_utc": "2026-06-17T17:09:13.232107+00:00",
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 855
    },
    {
      "trade_class": "live_simulated",
      "rows": 624
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
  "fact_trades_by_settlement_status": [
    {
      "settlement_status": "",
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4250
    }
  ],
  "fact_signal_candidate_coverage": {
    "rows": 31499,
    "eligible": 10961,
    "paper_ordered": 4274,
    "live_filled": 348
  },
  "clob_order_fill_join": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 961,
      "with_fill": 855
    }
  ]
}
```

## Holdout Success And ROI

| Slice | Rows | Dates | YES win | YES ROI | YES 95% CI | d1 NO ROI | YES - d1 NO |
|---|---:|---:|---:|---:|---:|---:|---:|
| `v9_like_fade_confirmed_physical` | 174 | 13 | 79.9% | +0.1% | [-7.3%, +6.3%] | -0.4% | +0.3% [-2.9%, +2.9%] |
| `forecast_fade_both_peaks_passed` | 100 | 12 | 85.0% | -4.4% | [-15.6%, +6.1%] | -6.8% | +1.7% [-2.4%, +4.0%] |
| `forecast_fade_passed_low_gap` | 50 | 11 | 82.0% | -7.9% | [-23.6%, +5.8%] | -9.1% | -0.4% [-7.4%, +3.2%] |
| `peak_forming_near_agree` | 420 | 14 | 61.4% | -6.7% | [-15.1%, +0.4%] | -10.5% | +4.4% [+0.3%, +8.7%] |
| `danger_gfs_peak_still_2h_ahead` | 828 | 14 | 24.6% | -18.0% | [-29.9%, -5.5%] | -7.0% | -1.6% [-11.3%, +8.0%] |
| `diagnostic_gfs_1_to_4h_after_peak` | 334 | 14 | 80.8% | +0.8% | [-4.5%, +6.0%] | -1.6% | +2.2% [+0.1%, +4.5%] |
| `paired_current_yes_vs_d1_no_clocked` | 1802 | 14 | 47.4% | -6.3% | [-12.3%, -0.4%] | -6.6% | +2.6% [-1.9%, +6.9%] |
| `all_clocked_current_yes` | 1869 | 14 | 48.1% | -5.9% | [-11.9%, -0.1%] | -6.6% | +2.6% [-1.9%, +6.9%] |

## Verdict

For live promotion, the fixed v9 current-YES rule remains the reference. Forecast peak clock now belongs in the shared model layer and forward telemetry, not in the live rule as a hard filter.

```text
significance=FAIL for forecast-clock hard-gate promotion
baseline=PASS only for the existing fixed v9 current-YES reference
forward=FAIL until N100 forward telemetry produces settled rows
conclusion=inconclusive for live gating; continue telemetry/modeling
```

## Output Files

- JSON: `docs/analysis/2026-06/2026-06-18-reheat-feature-factory-forecast-peak-slices-v18.json`
- CSV: `docs/analysis/2026-06/generated/reheat_feature_factory_forecast_peak_slices_v18/rule_summary.csv`
