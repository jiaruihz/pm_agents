# HeadB METAR Reversal Daily Breakdown v1

Generated: `2026-07-05T05:06:32+00:00`

## Verdict

`metar_reversal` remains `shadow_candidate_keep_collecting`; do not start real-money live from this table alone.

The daily shape is bursty: many trigger days lose the full stake, while a small number of one-step reversal days carry the positive total. This is the core reason HeadB needs more fresh-forward shadow and depth-aware replay before live.

## Coverage And Cost Model

- expression matrix: `docs/analysis/2026-07/generated/metar_reversal_expression_matrix_v1/expression_matrix_rows.csv` rows=8,617, settled dates `2026-05-19`..`2026-07-02`.
- reversal shape leg matrix: `docs/analysis/2026-07/generated/hotter_tail_reversal_shapes_v1/leg_rows.csv` rows=26,974, settled dates `2026-05-19`..`2026-07-02`.
- executable-ish daily ROI uses `$1` per trigger, taker entry at `ask + 0.01`, and weather taker fee `shares * 0.05 * p * (1-p)`. For `$1` notional this is `0.05 * (1-p)` dollars.
- `raw_roi` is kept only to reconcile older no-fee historical reports.
- Although the underlying feature shards have some later unlabeled dates, the HeadB trigger masks have no unsettled trigger rows in the current generated matrices:

| strategy | trigger_rows_all_labels | trigger_rows_settled | trigger_rows_unsettled | trigger_date_min | trigger_date_max | unsettled_dates |
| --- | --- | --- | --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | 47 | 47 | 0 | 2026-05-20 | 2026-06-30 |  |
| rich_current_b4_d1_yes | 70 | 70 | 0 | 2026-05-20 | 2026-06-20 |  |

## Summary

| strategy | rows | dates | cities | win_rate | avg_entry_stress | raw_roi | exec_no_fee_roi | exec_fee_roi | exec_fee_ci_low | exec_fee_ci_high | top5_removed_exec_fee_roi | losing_days | le_minus50_days | max_daily_loss | recent_rows | recent_exec_fee_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | 47 | 25 | 18 | +29.8% | 0.155 | +61.0% | +51.4% | +47.1% | -36.4% | +152.8% | -24.9% | 15 | 15 | -5.22 | 1 | -104.9% |
| rich_current_b4_d1_yes | 70 | 29 | 26 | +32.9% | 0.227 | +37.8% | +30.8% | +26.9% | -35.7% | +100.6% | -21.0% | 17 | 17 | -5.22 | 0 |  |
| rich_current_b4_current_bracket_no | 70 | 29 | 26 | +32.9% | 0.279 | +14.0% | +9.3% | +5.7% | -45.9% | +65.8% | -33.3% | 18 | 17 | -5.22 | 0 |  |

## Period Split

| strategy | period | rows | dates | cities | win_rate | avg_entry_stress | exec_fee_pnl | exec_fee_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | 2026-05 | 16 | 9 | 10 | +43.8% | 0.175 | +23.94 | +149.6% |
| false_fade_reheat_conflict_d1_yes | 2026-06-01..06-20 | 30 | 15 | 13 | +23.3% | 0.149 | -0.73 | -2.4% |
| false_fade_reheat_conflict_d1_yes | >=2026-06-21 | 1 | 1 | 1 | +0.0% | 0.021 | -1.05 | -104.9% |
| rich_current_b4_current_bracket_no | 2026-05 | 26 | 11 | 17 | +34.6% | 0.322 | +6.12 | +23.5% |
| rich_current_b4_current_bracket_no | 2026-06-01..06-20 | 44 | 18 | 19 | +31.8% | 0.254 | -2.13 | -4.8% |
| rich_current_b4_d1_yes | 2026-05 | 26 | 11 | 17 | +34.6% | 0.234 | +12.81 | +49.3% |
| rich_current_b4_d1_yes | 2026-06-01..06-20 | 44 | 18 | 19 | +31.8% | 0.223 | +6.01 | +13.6% |

## Daily: false_fade_reheat_conflict -> d1 YES

| target_date | rows | cities | wins | win_rate | avg_entry_stress | avg_fee_per_1 | exec_fee_pnl | exec_fee_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 3 | 2 | 3 | +100.0% | 0.190 | 0.041 | +15.58 | +519.3% |
| 2026-05-21 | 1 | 1 | 0 | +0.0% | 0.100 | 0.045 | -1.04 | -104.5% |
| 2026-05-23 | 1 | 1 | 0 | +0.0% | 0.040 | 0.048 | -1.05 | -104.8% |
| 2026-05-24 | 2 | 2 | 1 | +50.0% | 0.180 | 0.041 | +3.47 | +173.7% |
| 2026-05-25 | 1 | 1 | 0 | +0.0% | 0.150 | 0.043 | -1.04 | -104.2% |
| 2026-05-27 | 5 | 4 | 0 | +0.0% | 0.179 | 0.041 | -5.21 | -104.1% |
| 2026-05-28 | 1 | 1 | 1 | +100.0% | 0.280 | 0.036 | +2.54 | +253.5% |
| 2026-05-29 | 1 | 1 | 1 | +100.0% | 0.300 | 0.035 | +2.30 | +229.8% |
| 2026-05-31 | 1 | 1 | 1 | +100.0% | 0.106 | 0.045 | +8.39 | +838.9% |
| 2026-06-01 | 1 | 1 | 0 | +0.0% | 0.070 | 0.047 | -1.05 | -104.7% |
| 2026-06-03 | 2 | 2 | 0 | +0.0% | 0.107 | 0.045 | -2.09 | -104.5% |
| 2026-06-05 | 5 | 3 | 0 | +0.0% | 0.119 | 0.044 | -5.22 | -104.4% |
| 2026-06-06 | 2 | 2 | 0 | +0.0% | 0.150 | 0.043 | -2.08 | -104.2% |
| 2026-06-07 | 2 | 1 | 2 | +100.0% | 0.285 | 0.036 | +5.00 | +250.0% |
| 2026-06-08 | 1 | 1 | 0 | +0.0% | 0.080 | 0.046 | -1.05 | -104.6% |
| 2026-06-09 | 1 | 1 | 0 | +0.0% | 0.077 | 0.046 | -1.05 | -104.6% |
| 2026-06-10 | 2 | 2 | 0 | +0.0% | 0.083 | 0.046 | -2.09 | -104.6% |
| 2026-06-11 | 2 | 2 | 1 | +50.0% | 0.165 | 0.042 | +4.58 | +229.2% |
| 2026-06-12 | 2 | 2 | 0 | +0.0% | 0.140 | 0.043 | -2.09 | -104.3% |
| 2026-06-13 | 3 | 2 | 2 | +66.7% | 0.196 | 0.040 | +6.36 | +211.8% |
| 2026-06-14 | 2 | 2 | 0 | +0.0% | 0.095 | 0.045 | -2.09 | -104.5% |
| 2026-06-15 | 2 | 1 | 1 | +50.0% | 0.295 | 0.035 | +1.26 | +63.1% |
| 2026-06-16 | 1 | 1 | 1 | +100.0% | 0.250 | 0.038 | +2.96 | +296.2% |
| 2026-06-19 | 2 | 2 | 0 | +0.0% | 0.088 | 0.046 | -2.09 | -104.6% |
| 2026-06-30 | 1 | 1 | 0 | +0.0% | 0.021 | 0.049 | -1.05 | -104.9% |

## Daily: rich_current B4 -> d1 YES

| target_date | rows | cities | wins | win_rate | avg_entry_stress | avg_fee_per_1 | exec_fee_pnl | exec_fee_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 3 | 3 | 3 | +100.0% | 0.200 | 0.040 | +15.30 | +509.9% |
| 2026-05-21 | 2 | 2 | 0 | +0.0% | 0.305 | 0.035 | -2.07 | -103.5% |
| 2026-05-23 | 2 | 2 | 1 | +50.0% | 0.200 | 0.040 | +0.70 | +34.9% |
| 2026-05-24 | 2 | 2 | 1 | +50.0% | 0.180 | 0.041 | +3.47 | +173.7% |
| 2026-05-25 | 1 | 1 | 0 | +0.0% | 0.150 | 0.043 | -1.04 | -104.2% |
| 2026-05-26 | 1 | 1 | 1 | +100.0% | 0.330 | 0.033 | +2.00 | +199.7% |
| 2026-05-27 | 7 | 5 | 1 | +14.3% | 0.207 | 0.040 | -4.15 | -59.3% |
| 2026-05-28 | 2 | 1 | 1 | +50.0% | 0.150 | 0.042 | +1.49 | +74.3% |
| 2026-05-29 | 3 | 3 | 1 | +33.3% | 0.320 | 0.034 | +0.23 | +7.7% |
| 2026-05-30 | 2 | 2 | 0 | +0.0% | 0.285 | 0.036 | -2.07 | -103.6% |
| 2026-05-31 | 1 | 1 | 0 | +0.0% | 0.350 | 0.032 | -1.03 | -103.2% |
| 2026-06-01 | 2 | 2 | 0 | +0.0% | 0.069 | 0.047 | -2.09 | -104.7% |
| 2026-06-02 | 1 | 1 | 0 | +0.0% | 0.350 | 0.032 | -1.03 | -103.2% |
| 2026-06-03 | 2 | 2 | 0 | +0.0% | 0.107 | 0.045 | -2.09 | -104.5% |
| 2026-06-05 | 5 | 3 | 0 | +0.0% | 0.119 | 0.044 | -5.22 | -104.4% |
| 2026-06-06 | 2 | 2 | 0 | +0.0% | 0.150 | 0.043 | -2.08 | -104.2% |
| 2026-06-07 | 5 | 3 | 2 | +40.0% | 0.264 | 0.037 | +1.89 | +37.8% |
| 2026-06-08 | 1 | 1 | 0 | +0.0% | 0.080 | 0.046 | -1.05 | -104.6% |
| 2026-06-09 | 1 | 1 | 0 | +0.0% | 0.077 | 0.046 | -1.05 | -104.6% |
| 2026-06-10 | 5 | 4 | 0 | +0.0% | 0.279 | 0.036 | -5.18 | -103.6% |
| 2026-06-11 | 2 | 2 | 1 | +50.0% | 0.165 | 0.042 | +4.58 | +229.2% |
| 2026-06-12 | 2 | 2 | 0 | +0.0% | 0.270 | 0.037 | -2.07 | -103.7% |
| 2026-06-13 | 2 | 1 | 2 | +100.0% | 0.370 | 0.032 | +3.49 | +174.4% |
| 2026-06-14 | 1 | 1 | 0 | +0.0% | 0.019 | 0.049 | -1.05 | -104.9% |
| 2026-06-15 | 3 | 1 | 2 | +66.7% | 0.340 | 0.033 | +2.56 | +85.3% |
| 2026-06-16 | 5 | 4 | 5 | +100.0% | 0.368 | 0.032 | +14.98 | +299.7% |
| 2026-06-17 | 1 | 1 | 0 | +0.0% | 0.051 | 0.047 | -1.05 | -104.7% |
| 2026-06-19 | 2 | 2 | 0 | +0.0% | 0.088 | 0.046 | -2.09 | -104.6% |
| 2026-06-20 | 2 | 1 | 2 | +100.0% | 0.310 | 0.035 | +4.55 | +227.7% |

## Same-Denominator Sibling: B4 -> current bracket NO

| target_date | rows | cities | wins | win_rate | avg_entry_stress | avg_fee_per_1 | exec_fee_pnl | exec_fee_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 3 | 3 | 3 | +100.0% | 0.260 | 0.037 | +10.52 | +350.7% |
| 2026-05-21 | 2 | 2 | 0 | +0.0% | 0.524 | 0.024 | -2.05 | -102.4% |
| 2026-05-23 | 2 | 2 | 1 | +50.0% | 0.220 | 0.039 | +0.42 | +21.1% |
| 2026-05-24 | 2 | 2 | 1 | +50.0% | 0.205 | 0.040 | +3.18 | +159.2% |
| 2026-05-25 | 1 | 1 | 0 | +0.0% | 0.180 | 0.041 | -1.04 | -104.1% |
| 2026-05-26 | 1 | 1 | 1 | +100.0% | 0.358 | 0.032 | +1.76 | +176.1% |
| 2026-05-27 | 7 | 5 | 1 | +14.3% | 0.275 | 0.036 | -4.55 | -65.0% |
| 2026-05-28 | 2 | 1 | 1 | +50.0% | 0.160 | 0.042 | +1.25 | +62.5% |
| 2026-05-29 | 3 | 3 | 1 | +33.3% | 0.437 | 0.028 | -0.31 | -10.2% |
| 2026-05-30 | 2 | 2 | 0 | +0.0% | 0.600 | 0.020 | -2.04 | -102.0% |
| 2026-05-31 | 1 | 1 | 0 | +0.0% | 0.400 | 0.030 | -1.03 | -103.0% |
| 2026-06-01 | 2 | 2 | 0 | +0.0% | 0.080 | 0.046 | -2.09 | -104.6% |
| 2026-06-02 | 1 | 1 | 0 | +0.0% | 0.470 | 0.027 | -1.03 | -102.6% |
| 2026-06-03 | 2 | 2 | 0 | +0.0% | 0.110 | 0.044 | -2.09 | -104.5% |
| 2026-06-05 | 5 | 3 | 0 | +0.0% | 0.123 | 0.044 | -5.22 | -104.4% |
| 2026-06-06 | 2 | 2 | 0 | +0.0% | 0.180 | 0.041 | -2.08 | -104.1% |
| 2026-06-07 | 5 | 3 | 2 | +40.0% | 0.310 | 0.034 | +0.91 | +18.1% |
| 2026-06-08 | 1 | 1 | 0 | +0.0% | 0.080 | 0.046 | -1.05 | -104.6% |
| 2026-06-09 | 1 | 1 | 0 | +0.0% | 0.090 | 0.046 | -1.05 | -104.6% |
| 2026-06-10 | 5 | 4 | 0 | +0.0% | 0.305 | 0.035 | -5.17 | -103.5% |
| 2026-06-11 | 2 | 2 | 1 | +50.0% | 0.165 | 0.042 | +4.58 | +229.2% |
| 2026-06-12 | 2 | 2 | 0 | +0.0% | 0.316 | 0.034 | -2.07 | -103.4% |
| 2026-06-13 | 2 | 1 | 2 | +100.0% | 0.435 | 0.028 | +2.62 | +130.8% |
| 2026-06-14 | 1 | 1 | 0 | +0.0% | 0.020 | 0.049 | -1.05 | -104.9% |
| 2026-06-15 | 3 | 1 | 2 | +66.7% | 0.373 | 0.031 | +1.79 | +59.6% |
| 2026-06-16 | 5 | 4 | 5 | +100.0% | 0.378 | 0.031 | +12.02 | +240.3% |
| 2026-06-17 | 1 | 1 | 0 | +0.0% | 0.060 | 0.047 | -1.05 | -104.7% |
| 2026-06-19 | 2 | 2 | 0 | +0.0% | 0.085 | 0.046 | -2.09 | -104.6% |
| 2026-06-20 | 2 | 1 | 2 | +100.0% | 0.500 | 0.025 | +1.99 | +99.5% |

## Read

- `false_fade` is the cleaner but thinner slice. Fee-adjusted/stressed ROI is positive overall, but June flips slightly negative and the 6/21+ window has only one loser. That is not live evidence.
- `B4 d1 YES` has more rows and remains positive after +1c + weather fee, but top5-removed is negative. The shape exists historically, yet the edge is concentrated.
- `B4 current bracket NO` is the same denominator and wins on the same dates, but worse carry than d1 YES. It buys overshoot protection that was not valuable enough historically.
- Worst losing days are normal for this structure: when the one-step reversal does not happen, a trigger usually loses the entire $1 stake.
- This table does not solve the current blocker: top-of-book depth at trigger time. Before live, rerun B4/false_fade with entry-time full depth, partial fills, and fresh shadow settlement.

## Generated Artifacts

- `docs/analysis/2026-07/generated/metar_reversal_daily_breakdown_v1/headb_daily_breakdown.csv`
- `docs/analysis/2026-07/generated/metar_reversal_daily_breakdown_v1/headb_summary.csv`
- `docs/analysis/2026-07/generated/metar_reversal_daily_breakdown_v1/headb_periods.csv`
- `docs/analysis/2026-07/generated/metar_reversal_daily_breakdown_v1/headb_trade_rows.csv`
