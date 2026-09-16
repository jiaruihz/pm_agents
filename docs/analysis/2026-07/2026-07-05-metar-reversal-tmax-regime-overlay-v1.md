# HeadB METAR Reversal Tmax/Regime Overlay v1

Generated: `2026-07-05T05:10:50+00:00`

## Verdict

`metar_reversal` remains research/shadow-only. The overlays are useful as telemetry and mechanism ranking, not live approval.

Main read:

- Overall HeadB point estimates are positive, but date-block CIs still cross 0 and top-5-winner removal stays negative.
- Regime overlays help explain which rows are physically cleaner, but they do not fix the main blocker: recent state frequency is near zero and entry-time depth is thin.
- Clean tmax probability overlap starts at 2026-06-02, so tmax overlays are a smaller denominator. Treat them as mechanism diagnostics, not an in-sample selector.

## Data Snapshot

- `runtime/weather.db` mtime: `2026-07-05T05:05:38.595568+00:00`.
- CLOB coverage gate file: `runtime/_dashboard_logs/clob_fill_coverage_gate.json`.
- HeadB trade rows: `docs/analysis/2026-07/generated/metar_reversal_daily_breakdown_v1/headb_trade_rows.csv` rows=187.
- Regime atlas join coverage: 187/187 rows.

## Current Overall Performance

| strategy | rows | dates | cities | win_rate | avg_entry_stress | exec_fee_pnl | exec_fee_roi | ci_low | ci_high | top5_removed_exec_fee_roi | max_daily_loss | recent_rows | recent_exec_fee_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | 47 | 25 | 18 | +29.8% | 0.155 | +22.16 | +47.1% | -36.4% | +152.8% | -24.9% | -5.22 | 1 | -104.9% |
| rich_current_b4_d1_yes | 70 | 29 | 26 | +32.9% | 0.227 | +18.82 | +26.9% | -35.7% | +100.6% | -21.0% | -5.22 | 0 |  |
| rich_current_b4_current_bracket_no | 70 | 29 | 26 | +32.9% | 0.279 | +3.99 | +5.7% | -45.9% | +65.8% | -33.3% | -5.22 | 0 |  |

## Market / Ask Distribution

| strategy | ask_band | rows | dates | win_rate | avg_entry_raw | exec_fee_roi | ci_low | ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | 0-5c | 7 | 6 | +0.0% | 0.024 | -104.8% | -104.9% | -104.8% |
| false_fade_reheat_conflict_d1_yes | 10-20c | 17 | 10 | +29.4% | 0.141 | +95.4% | -62.6% | +271.5% |
| false_fade_reheat_conflict_d1_yes | 20-30c | 10 | 7 | +50.0% | 0.264 | +77.2% | -30.5% | +226.9% |
| false_fade_reheat_conflict_d1_yes | 30-40c | 3 | 3 | +100.0% | 0.300 | +219.1% | +219.1% | +219.1% |
| false_fade_reheat_conflict_d1_yes | 5-10c | 10 | 8 | +10.0% | 0.074 | -10.2% | -104.6% | +209.9% |
| rich_current_b4_current_bracket_no | 0-5c | 6 | 6 | +0.0% | 0.019 | -104.9% | -104.9% | -104.8% |
| rich_current_b4_current_bracket_no | 10-20c | 13 | 10 | +38.5% | 0.157 | +133.3% | -45.0% | +305.6% |
| rich_current_b4_current_bracket_no | 20-30c | 7 | 7 | +28.6% | 0.260 | -6.8% | -103.7% | +93.3% |
| rich_current_b4_current_bracket_no | 30-40c | 12 | 10 | +66.7% | 0.358 | +78.2% | +12.0% | +158.0% |
| rich_current_b4_current_bracket_no | 40c+ | 19 | 13 | +42.1% | 0.509 | -12.4% | -72.9% | +48.2% |
| rich_current_b4_current_bracket_no | 5-10c | 13 | 10 | +0.0% | 0.069 | -104.6% | -104.7% | -104.6% |
| rich_current_b4_d1_yes | 0-5c | 8 | 7 | +0.0% | 0.026 | -104.8% | -104.9% | -104.8% |
| rich_current_b4_d1_yes | 10-20c | 16 | 11 | +25.0% | 0.141 | +68.9% | -104.2% | +255.4% |
| rich_current_b4_d1_yes | 20-30c | 11 | 8 | +54.5% | 0.259 | +95.7% | +2.6% | +222.6% |
| rich_current_b4_d1_yes | 30-40c | 14 | 12 | +50.0% | 0.341 | +47.5% | -28.0% | +129.8% |
| rich_current_b4_d1_yes | 40c+ | 9 | 7 | +55.6% | 0.473 | +17.3% | -69.5% | +92.8% |
| rich_current_b4_d1_yes | 5-10c | 12 | 9 | +8.3% | 0.070 | -21.3% | -104.6% | +195.4% |

## Tmax Coverage

| strategy | rows | tmax_overlap | tmax_overlap_rate |
| --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | 47 | 25 | +53.2% |
| rich_current_b4_current_bracket_no | 70 | 38 | +54.3% |
| rich_current_b4_d1_yes | 70 | 38 | +54.3% |

## Clean Overlay Comparison

| strategy | variant | rows | retained_vs_base | dates | win_rate | avg_entry_stress | exec_fee_roi | ci_low | ci_high | top5_removed_exec_fee_roi | avg_p_current | avg_p_d1 | avg_tmax_d1_yes_edge_exec | avg_tmax_current_overprice |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | trend3h_positive | 33 | +70.2% | 22 | +39.4% | 0.168 | +99.3% | -17.3% | +228.9% | +0.6% | 0.813 | 0.159 | -1.0% | +4.3% |
| false_fade_reheat_conflict_d1_yes | day_open_runway | 29 | +61.7% | 19 | +37.9% | 0.165 | +89.8% | -8.3% | +198.4% | -15.8% | 0.806 | 0.166 | -1.1% | +4.2% |
| false_fade_reheat_conflict_d1_yes | tmax_overlap_base | 25 | +53.2% | 13 | +28.0% | 0.162 | +18.0% | -65.8% | +107.1% | -72.0% | 0.834 | 0.143 | -1.9% | +3.8% |
| false_fade_reheat_conflict_d1_yes | trend3h_positive_and_day_open_runway | 21 | +44.7% | 16 | +47.6% | 0.173 | +144.7% | +16.9% | +281.8% | +3.4% | 0.792 | 0.177 | -0.6% | +5.0% |
| false_fade_reheat_conflict_d1_yes | tmax_d1_edge_exec_gt0 | 7 | +14.9% | 6 | +57.1% | 0.204 | +139.9% | -48.7% | +330.3% | -104.4% | 0.727 | 0.227 | +2.2% | +8.2% |
| false_fade_reheat_conflict_d1_yes | tmax_d1_edge_and_current_overprice_gt0 | 7 | +14.9% | 6 | +57.1% | 0.204 | +139.9% | -48.7% | +330.3% | -104.4% | 0.727 | 0.227 | +2.2% | +8.2% |
| rich_current_b4_current_bracket_no | trend3h_positive | 51 | +72.9% | 27 | +37.3% | 0.320 | +15.1% | -38.8% | +81.8% | -29.9% | 0.700 | 0.254 | -6.3% | +7.3% |
| rich_current_b4_current_bracket_no | day_open_runway | 37 | +52.9% | 19 | +43.2% | 0.300 | +36.3% | -29.9% | +104.7% | -19.8% | 0.702 | 0.256 | -5.0% | +7.0% |
| rich_current_b4_current_bracket_no | trend3h_positive_and_day_open_runway | 28 | +40.0% | 18 | +50.0% | 0.331 | +61.3% | -14.1% | +147.9% | -11.3% | 0.670 | 0.282 | -4.9% | +8.2% |
| rich_current_b4_d1_yes | trend3h_positive | 51 | +72.9% | 27 | +37.3% | 0.258 | +36.6% | -29.9% | +119.6% | -16.7% | 0.700 | 0.254 | -2.5% | +7.3% |
| rich_current_b4_d1_yes | tmax_overlap_base | 38 | +54.3% | 16 | +36.8% | 0.245 | +32.3% | -54.9% | +122.0% | -33.0% | 0.744 | 0.218 | -2.7% | +6.1% |
| rich_current_b4_d1_yes | day_open_runway | 37 | +52.9% | 19 | +43.2% | 0.256 | +51.5% | -23.0% | +126.8% | -9.2% | 0.702 | 0.256 | -2.0% | +7.0% |
| rich_current_b4_d1_yes | trend3h_positive_and_day_open_runway | 28 | +40.0% | 18 | +50.0% | 0.285 | +76.7% | -5.8% | +170.5% | -0.4% | 0.670 | 0.282 | -1.7% | +8.2% |
| rich_current_b4_d1_yes | tmax_d1_edge_exec_gt0 | 12 | +17.1% | 9 | +41.7% | 0.236 | +70.5% | -56.1% | +233.1% | -103.9% | 0.679 | 0.264 | +2.8% | +9.6% |
| rich_current_b4_d1_yes | tmax_d1_edge_and_current_overprice_gt0 | 12 | +17.1% | 9 | +41.7% | 0.236 | +70.5% | -56.1% | +233.1% | -103.9% | 0.679 | 0.264 | +2.8% | +9.6% |

## Daily Rows

| strategy | target_date | rows | cities | wins | win_rate | avg_entry_raw | exec_fee_pnl | exec_fee_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| false_fade_reheat_conflict_d1_yes | 2026-05-20 | 3 | 2 | 3 | +100.0% | 0.180 | +15.58 | +519.3% |
| false_fade_reheat_conflict_d1_yes | 2026-05-21 | 1 | 1 | 0 | +0.0% | 0.090 | -1.04 | -104.5% |
| false_fade_reheat_conflict_d1_yes | 2026-05-23 | 1 | 1 | 0 | +0.0% | 0.030 | -1.05 | -104.8% |
| false_fade_reheat_conflict_d1_yes | 2026-05-24 | 2 | 2 | 1 | +50.0% | 0.170 | +3.47 | +173.7% |
| false_fade_reheat_conflict_d1_yes | 2026-05-25 | 1 | 1 | 0 | +0.0% | 0.140 | -1.04 | -104.2% |
| false_fade_reheat_conflict_d1_yes | 2026-05-27 | 5 | 4 | 0 | +0.0% | 0.169 | -5.21 | -104.1% |
| false_fade_reheat_conflict_d1_yes | 2026-05-28 | 1 | 1 | 1 | +100.0% | 0.270 | +2.54 | +253.5% |
| false_fade_reheat_conflict_d1_yes | 2026-05-29 | 1 | 1 | 1 | +100.0% | 0.290 | +2.30 | +229.8% |
| false_fade_reheat_conflict_d1_yes | 2026-05-31 | 1 | 1 | 1 | +100.0% | 0.096 | +8.39 | +838.9% |
| false_fade_reheat_conflict_d1_yes | 2026-06-01 | 1 | 1 | 0 | +0.0% | 0.060 | -1.05 | -104.7% |
| false_fade_reheat_conflict_d1_yes | 2026-06-03 | 2 | 2 | 0 | +0.0% | 0.097 | -2.09 | -104.5% |
| false_fade_reheat_conflict_d1_yes | 2026-06-05 | 5 | 3 | 0 | +0.0% | 0.109 | -5.22 | -104.4% |
| false_fade_reheat_conflict_d1_yes | 2026-06-06 | 2 | 2 | 0 | +0.0% | 0.140 | -2.08 | -104.2% |
| false_fade_reheat_conflict_d1_yes | 2026-06-07 | 2 | 1 | 2 | +100.0% | 0.275 | +5.00 | +250.0% |
| false_fade_reheat_conflict_d1_yes | 2026-06-08 | 1 | 1 | 0 | +0.0% | 0.070 | -1.05 | -104.6% |
| false_fade_reheat_conflict_d1_yes | 2026-06-09 | 1 | 1 | 0 | +0.0% | 0.067 | -1.05 | -104.6% |
| false_fade_reheat_conflict_d1_yes | 2026-06-10 | 2 | 2 | 0 | +0.0% | 0.073 | -2.09 | -104.6% |
| false_fade_reheat_conflict_d1_yes | 2026-06-11 | 2 | 2 | 1 | +50.0% | 0.155 | +4.58 | +229.2% |
| false_fade_reheat_conflict_d1_yes | 2026-06-12 | 2 | 2 | 0 | +0.0% | 0.130 | -2.09 | -104.3% |
| false_fade_reheat_conflict_d1_yes | 2026-06-13 | 3 | 2 | 2 | +66.7% | 0.186 | +6.36 | +211.8% |
| false_fade_reheat_conflict_d1_yes | 2026-06-14 | 2 | 2 | 0 | +0.0% | 0.085 | -2.09 | -104.5% |
| false_fade_reheat_conflict_d1_yes | 2026-06-15 | 2 | 1 | 1 | +50.0% | 0.285 | +1.26 | +63.1% |
| false_fade_reheat_conflict_d1_yes | 2026-06-16 | 1 | 1 | 1 | +100.0% | 0.240 | +2.96 | +296.2% |
| false_fade_reheat_conflict_d1_yes | 2026-06-19 | 2 | 2 | 0 | +0.0% | 0.079 | -2.09 | -104.6% |
| false_fade_reheat_conflict_d1_yes | 2026-06-30 | 1 | 1 | 0 | +0.0% | 0.011 | -1.05 | -104.9% |
| rich_current_b4_current_bracket_no | 2026-05-20 | 3 | 3 | 3 | +100.0% | 0.250 | +10.52 | +350.7% |
| rich_current_b4_current_bracket_no | 2026-05-21 | 2 | 2 | 0 | +0.0% | 0.514 | -2.05 | -102.4% |
| rich_current_b4_current_bracket_no | 2026-05-23 | 2 | 2 | 1 | +50.0% | 0.210 | +0.42 | +21.1% |
| rich_current_b4_current_bracket_no | 2026-05-24 | 2 | 2 | 1 | +50.0% | 0.195 | +3.18 | +159.2% |
| rich_current_b4_current_bracket_no | 2026-05-25 | 1 | 1 | 0 | +0.0% | 0.170 | -1.04 | -104.1% |
| rich_current_b4_current_bracket_no | 2026-05-26 | 1 | 1 | 1 | +100.0% | 0.348 | +1.76 | +176.1% |
| rich_current_b4_current_bracket_no | 2026-05-27 | 7 | 5 | 1 | +14.3% | 0.265 | -4.55 | -65.0% |
| rich_current_b4_current_bracket_no | 2026-05-28 | 2 | 1 | 1 | +50.0% | 0.149 | +1.25 | +62.5% |
| rich_current_b4_current_bracket_no | 2026-05-29 | 3 | 3 | 1 | +33.3% | 0.427 | -0.31 | -10.2% |
| rich_current_b4_current_bracket_no | 2026-05-30 | 2 | 2 | 0 | +0.0% | 0.590 | -2.04 | -102.0% |
| rich_current_b4_current_bracket_no | 2026-05-31 | 1 | 1 | 0 | +0.0% | 0.390 | -1.03 | -103.0% |
| rich_current_b4_current_bracket_no | 2026-06-01 | 2 | 2 | 0 | +0.0% | 0.070 | -2.09 | -104.6% |
| rich_current_b4_current_bracket_no | 2026-06-02 | 1 | 1 | 0 | +0.0% | 0.460 | -1.03 | -102.6% |
| rich_current_b4_current_bracket_no | 2026-06-03 | 2 | 2 | 0 | +0.0% | 0.100 | -2.09 | -104.5% |
| rich_current_b4_current_bracket_no | 2026-06-05 | 5 | 3 | 0 | +0.0% | 0.113 | -5.22 | -104.4% |
| rich_current_b4_current_bracket_no | 2026-06-06 | 2 | 2 | 0 | +0.0% | 0.170 | -2.08 | -104.1% |
| rich_current_b4_current_bracket_no | 2026-06-07 | 5 | 3 | 2 | +40.0% | 0.300 | +0.91 | +18.1% |
| rich_current_b4_current_bracket_no | 2026-06-08 | 1 | 1 | 0 | +0.0% | 0.070 | -1.05 | -104.6% |
| rich_current_b4_current_bracket_no | 2026-06-09 | 1 | 1 | 0 | +0.0% | 0.080 | -1.05 | -104.6% |
| rich_current_b4_current_bracket_no | 2026-06-10 | 5 | 4 | 0 | +0.0% | 0.295 | -5.17 | -103.5% |
| rich_current_b4_current_bracket_no | 2026-06-11 | 2 | 2 | 1 | +50.0% | 0.155 | +4.58 | +229.2% |
| rich_current_b4_current_bracket_no | 2026-06-12 | 2 | 2 | 0 | +0.0% | 0.305 | -2.07 | -103.4% |
| rich_current_b4_current_bracket_no | 2026-06-13 | 2 | 1 | 2 | +100.0% | 0.425 | +2.62 | +130.8% |
| rich_current_b4_current_bracket_no | 2026-06-14 | 1 | 1 | 0 | +0.0% | 0.010 | -1.05 | -104.9% |
| rich_current_b4_current_bracket_no | 2026-06-15 | 3 | 1 | 2 | +66.7% | 0.363 | +1.79 | +59.6% |
| rich_current_b4_current_bracket_no | 2026-06-16 | 5 | 4 | 5 | +100.0% | 0.368 | +12.02 | +240.3% |
| rich_current_b4_current_bracket_no | 2026-06-17 | 1 | 1 | 0 | +0.0% | 0.050 | -1.05 | -104.7% |
| rich_current_b4_current_bracket_no | 2026-06-19 | 2 | 2 | 0 | +0.0% | 0.075 | -2.09 | -104.6% |
| rich_current_b4_current_bracket_no | 2026-06-20 | 2 | 1 | 2 | +100.0% | 0.490 | +1.99 | +99.5% |
| rich_current_b4_d1_yes | 2026-05-20 | 3 | 3 | 3 | +100.0% | 0.190 | +15.30 | +509.9% |
| rich_current_b4_d1_yes | 2026-05-21 | 2 | 2 | 0 | +0.0% | 0.295 | -2.07 | -103.5% |
| rich_current_b4_d1_yes | 2026-05-23 | 2 | 2 | 1 | +50.0% | 0.190 | +0.70 | +34.9% |
| rich_current_b4_d1_yes | 2026-05-24 | 2 | 2 | 1 | +50.0% | 0.170 | +3.47 | +173.7% |
| rich_current_b4_d1_yes | 2026-05-25 | 1 | 1 | 0 | +0.0% | 0.140 | -1.04 | -104.2% |
| rich_current_b4_d1_yes | 2026-05-26 | 1 | 1 | 1 | +100.0% | 0.320 | +2.00 | +199.7% |
| rich_current_b4_d1_yes | 2026-05-27 | 7 | 5 | 1 | +14.3% | 0.197 | -4.15 | -59.3% |
| rich_current_b4_d1_yes | 2026-05-28 | 2 | 1 | 1 | +50.0% | 0.140 | +1.49 | +74.3% |
| rich_current_b4_d1_yes | 2026-05-29 | 3 | 3 | 1 | +33.3% | 0.310 | +0.23 | +7.7% |
| rich_current_b4_d1_yes | 2026-05-30 | 2 | 2 | 0 | +0.0% | 0.275 | -2.07 | -103.6% |
| rich_current_b4_d1_yes | 2026-05-31 | 1 | 1 | 0 | +0.0% | 0.340 | -1.03 | -103.2% |
| rich_current_b4_d1_yes | 2026-06-01 | 2 | 2 | 0 | +0.0% | 0.059 | -2.09 | -104.7% |
| rich_current_b4_d1_yes | 2026-06-02 | 1 | 1 | 0 | +0.0% | 0.340 | -1.03 | -103.2% |
| rich_current_b4_d1_yes | 2026-06-03 | 2 | 2 | 0 | +0.0% | 0.097 | -2.09 | -104.5% |
| rich_current_b4_d1_yes | 2026-06-05 | 5 | 3 | 0 | +0.0% | 0.109 | -5.22 | -104.4% |
| rich_current_b4_d1_yes | 2026-06-06 | 2 | 2 | 0 | +0.0% | 0.140 | -2.08 | -104.2% |
| rich_current_b4_d1_yes | 2026-06-07 | 5 | 3 | 2 | +40.0% | 0.254 | +1.89 | +37.8% |
| rich_current_b4_d1_yes | 2026-06-08 | 1 | 1 | 0 | +0.0% | 0.070 | -1.05 | -104.6% |
| rich_current_b4_d1_yes | 2026-06-09 | 1 | 1 | 0 | +0.0% | 0.067 | -1.05 | -104.6% |
| rich_current_b4_d1_yes | 2026-06-10 | 5 | 4 | 0 | +0.0% | 0.269 | -5.18 | -103.6% |
| rich_current_b4_d1_yes | 2026-06-11 | 2 | 2 | 1 | +50.0% | 0.155 | +4.58 | +229.2% |
| rich_current_b4_d1_yes | 2026-06-12 | 2 | 2 | 0 | +0.0% | 0.260 | -2.07 | -103.7% |
| rich_current_b4_d1_yes | 2026-06-13 | 2 | 1 | 2 | +100.0% | 0.360 | +3.49 | +174.4% |
| rich_current_b4_d1_yes | 2026-06-14 | 1 | 1 | 0 | +0.0% | 0.009 | -1.05 | -104.9% |
| rich_current_b4_d1_yes | 2026-06-15 | 3 | 1 | 2 | +66.7% | 0.330 | +2.56 | +85.3% |
| rich_current_b4_d1_yes | 2026-06-16 | 5 | 4 | 5 | +100.0% | 0.358 | +14.98 | +299.7% |
| rich_current_b4_d1_yes | 2026-06-17 | 1 | 1 | 0 | +0.0% | 0.041 | -1.05 | -104.7% |
| rich_current_b4_d1_yes | 2026-06-19 | 2 | 2 | 0 | +0.0% | 0.079 | -2.09 | -104.6% |
| rich_current_b4_d1_yes | 2026-06-20 | 2 | 1 | 2 | +100.0% | 0.300 | +4.55 | +227.7% |

## Interpretation

- `trend3h_positive` is the cleanest shared mechanism overlay to carry forward: it asks for sustained warming, not just a one-hour uptick.
- `day_open_runway` is interpretable, but on this HeadB denominator it is still mostly a sample-shape tag; do not make it a hard gate without forward rows.
- `tmax_d1_edge_exec_gt0` is the right probability confirmation idea, but it only covers the 2026-06-02+ subset. It should be written into the shadow journal as `p_d1`, `d1_yes_edge`, and `current_overprice`, then judged forward.
- Same-denominator `current_bracket_no` remains worse than d1 YES. The optimization direction should refine d1 YES entry quality, not switch HeadB into a NO route.

## Generated Artifacts

- `docs/analysis/2026-07/generated/metar_reversal_tmax_regime_overlay_v1/headb_enriched_trades.csv`
- `docs/analysis/2026-07/generated/metar_reversal_tmax_regime_overlay_v1/headb_base_summary.csv`
- `docs/analysis/2026-07/generated/metar_reversal_tmax_regime_overlay_v1/headb_ask_band_summary.csv`
- `docs/analysis/2026-07/generated/metar_reversal_tmax_regime_overlay_v1/headb_variant_summary.csv`
- `docs/analysis/2026-07/generated/metar_reversal_tmax_regime_overlay_v1/headb_daily_summary.csv`
