# Hotter-Tail Reversal Shapes v1 (exploratory, path-aware)

Generated: 2026-07-03T14:24:18.214802+00:00

K = 36 pre-declared cells (all reported).  Hourly-sampled paths:
touch/TP metrics are conservative lower bounds; +1c taker entry, threshold-1c TP exit.
Conclusions limited to shadow_candidate / inconclusive; no live changes.

Strategy-family classification:

```text
Shape A cheap hotter lottery = Head A boundary only.
  It tests whether intraday 3c..10c hotter tickets have a pump/TP path.
  It does not replace or disprove the D-1 / early forecast-tail low-price YES sleeve.

Shape B rich current collapse = Head B main candidate.
  It is an intraday METAR/runway reversal shape, not a low-price lottery.
  It should be reviewed in the metar_reversal strategy window.
```

## Verdict

```text
broad_cheap_hotter_lottery (intraday 3-10c d1/d2/tail):  NEGATIVE, not rescueable
  all 7 Shape-A cells x 3 legs lose on hold AND on TP20/TP30; the hypothesized
  pump path barely exists (touch20 5-14%, pump-then-die 2-8%); conditioning on
  good physics makes it WORSE (what stays cheap when physics favor hotter is the
  leg beyond the move).  This is intraday-cheap-band specific; the D-1 low-price
  sleeve is a different denominator and keeps its own shadow_candidate status.

rich_current_collapse_reversal (Shape B4):  shadow_candidate
  state: current_high YES still >= 0.60 while obs warming (trend_1h >= +0.5F),
  forecast max lands >= 1 bracket above current (bracket-aware), forecast peak
  still ahead.  d1 YES hold: 59 rows / 28 dates, win 52.5% @ avg ask ~0.29,
  ROI +103.2% CI [+38.3%, +163.8%], top5-removed +50.8%.
  Convergent validity: union with the earlier anchored-conflict trigger (different
  thresholds: gap-based, d1-ask cap, current >= 0.40) = 69 rows / 29 dates /
  24 cities, +87.4% CI [+28.0%, +144.6%]; overlap only 23 rows, so the shape is
  not one threshold set.  Condition stack is monotone (B0 -40% -> B4 +103%).

execution for the reversal shape:  TAKER entry + HOLD to settlement
  TP20 = -35%, TP30 = -18% (kills the payoff; win rate is ~50%, not a lottery)
  maker-first entry = adversely selected: winners fill 12.9% vs losers 92.9%,
  maker ROI on filled -51%.  This is the sharpest execution result of the round.

expression ranking inside the shape:  d1 YES > current_bracket NO (+73.3%
  CI [+20.3%, +121.0%], lower carry) >> d2 YES (-94%; the collapse is exactly one
  bracket, never two).  Hotter baskets inherit the dead d2/tail legs -> skip.

attribution:  METAR-regime x market-anchoring (book lags obs+forecast).
  NOT forecast-bias (hot-city prior cells A4/A6 negative intraday);
  NOT cheap-band convexity (Shape A dead);  noise risk remains: every qualifying
  row is pre-2026-06-21 (state starvation afterwards), so fresh-forward shadow is
  the only path to upgrade.
```

## Shape A (cheap hotter lottery 0.03-0.10) and Shape B (rich current collapse)

| cell | leg | rows | dates | avg_ask | settle_win | touch20 | pump_die | hold_roi | hold_ci_lo | hold_ci_hi | tp20_roi | tp20_ci_lo | tp20_ci_hi | tp30_roi | top5_rm_hold | recent_hold_roi | recent_rows |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A0_broad_cheap | d1_yes | 1085 | 39 | +0.061 | +0.041 | +0.059 | +0.023 | -0.452 | -0.636 | -0.239 | -0.800 | -0.867 | -0.717 | -0.745 | -0.555 | -0.527 | 189 |
| A0_broad_cheap | d2_yes | 975 | 39 | +0.061 | +0.042 | +0.072 | +0.033 | -0.432 | -0.656 | -0.187 | -0.773 | -0.855 | -0.673 | -0.729 | -0.533 | +0.108 | 131 |
| A0_broad_cheap | tail_yes | 182 | 36 | +0.057 | +0.033 | +0.071 | +0.038 | -0.610 | -0.923 | -0.169 | -0.830 | -0.927 | -0.695 | -0.855 | -0.948 | -1.000 | 10 |
| A1_warming | d1_yes | 383 | 39 | +0.062 | +0.026 | +0.047 | +0.023 | -0.613 | -0.839 | -0.346 | -0.852 | -0.923 | -0.768 | -0.821 | -0.865 | -0.439 | 72 |
| A1_warming | d2_yes | 396 | 39 | +0.062 | +0.040 | +0.076 | +0.038 | -0.447 | -0.704 | -0.157 | -0.767 | -0.868 | -0.628 | -0.712 | -0.673 | +0.013 | 51 |
| A1_warming | tail_yes | 98 | 31 | +0.058 | +0.041 | +0.082 | +0.041 | -0.552 | -1.000 | +0.120 | -0.820 | -0.957 | -0.635 | -0.843 | -1.000 | -1.000 | 4 |
| A2_peak_ahead | d1_yes | 454 | 36 | +0.064 | +0.053 | +0.075 | +0.033 | -0.317 | -0.655 | +0.108 | -0.688 | -0.832 | -0.514 | -0.645 | -0.533 | -1.000 | 6 |
| A2_peak_ahead | d2_yes | 607 | 35 | +0.063 | +0.041 | +0.076 | +0.036 | -0.443 | -0.714 | -0.129 | -0.783 | -0.877 | -0.652 | -0.742 | -0.595 | -1.000 | 6 |
| A2_peak_ahead | tail_yes | 158 | 30 | +0.057 | +0.038 | +0.082 | +0.044 | -0.551 | -0.916 | -0.065 | -0.804 | -0.917 | -0.659 | -0.833 | -0.940 |  | 0 |
| A3_conflict | d1_yes | 430 | 38 | +0.063 | +0.040 | +0.077 | +0.044 | -0.461 | -0.694 | -0.176 | -0.727 | -0.837 | -0.591 | -0.684 | -0.691 | -0.653 | 32 |
| A3_conflict | d2_yes | 220 | 35 | +0.063 | +0.023 | +0.095 | +0.073 | -0.666 | -0.942 | -0.294 | -0.784 | -0.887 | -0.660 | -0.762 | -1.000 | -1.000 | 17 |
| A3_conflict | tail_yes | 11 | 6 | +0.064 | +0.000 | +0.091 | +0.091 | -1.000 | -1.000 | -1.000 | -0.760 | -1.000 | -0.118 | -1.000 | -1.000 |  | 0 |
| A4_hot_city | d1_yes | 377 | 39 | +0.060 | +0.058 | +0.074 | +0.027 | -0.231 | -0.615 | +0.204 | -0.699 | -0.862 | -0.478 | -0.599 | -0.472 | -0.569 | 58 |
| A4_hot_city | d2_yes | 323 | 39 | +0.060 | +0.043 | +0.065 | +0.025 | -0.355 | -0.714 | +0.065 | -0.774 | -0.913 | -0.564 | -0.742 | -0.637 | -0.683 | 35 |
| A4_hot_city | tail_yes | 65 | 28 | +0.056 | +0.031 | +0.046 | +0.015 | -0.663 | -1.000 | +0.114 | -0.876 | -1.000 | -0.703 | -0.902 | -1.000 | -1.000 | 4 |
| A5_phys_core(warm+peak+conflict) | d1_yes | 174 | 33 | +0.062 | +0.023 | +0.046 | +0.029 | -0.702 | -0.947 | -0.345 | -0.826 | -0.936 | -0.675 | -0.838 | -1.000 | -1.000 | 1 |
| A5_phys_core(warm+peak+conflict) | d2_yes | 113 | 32 | +0.064 | +0.027 | +0.106 | +0.080 | -0.586 | -1.000 | -0.093 | -0.749 | -0.882 | -0.597 | -0.708 | -1.000 | -1.000 | 1 |
| A6_A5_hot_city | d1_yes | 60 | 26 | +0.061 | +0.033 | +0.017 | +0.000 | -0.682 | -1.000 | -0.168 | -0.805 | -1.000 | -0.432 | -0.789 | -1.000 | -1.000 | 1 |
| A6_A5_hot_city | d2_yes | 43 | 27 | +0.063 | +0.023 | +0.140 | +0.116 | -0.668 | -1.000 | +0.071 | -0.694 | -0.925 | -0.378 | -0.773 | -1.000 | -1.000 | 1 |
| B0_rich_current | current_bracket_no | 3036 | 40 | +0.136 | +0.102 | +0.137 | +0.047 | -0.499 | -0.600 | -0.387 | -0.816 | -0.866 | -0.746 | -0.783 | -0.553 | -0.546 | 542 |
| B0_rich_current | d1_yes | 2765 | 40 | +0.130 | +0.103 | +0.141 | +0.044 | -0.396 | -0.523 | -0.254 | -0.812 | -0.866 | -0.728 | -0.766 | -0.463 | -0.505 | 486 |
| B0_rich_current | d2_yes | 1794 | 40 | +0.038 | +0.013 | +0.020 | +0.008 | -0.832 | -0.943 | -0.676 | -0.939 | -0.975 | -0.892 | -0.923 | -0.901 | -0.526 | 312 |
| B1_warming | current_bracket_no | 676 | 40 | +0.195 | +0.170 | +0.228 | +0.077 | -0.360 | -0.489 | -0.221 | -0.787 | -0.830 | -0.736 | -0.741 | -0.463 | -0.166 | 112 |
| B1_warming | d1_yes | 646 | 40 | +0.175 | +0.169 | +0.226 | +0.065 | -0.241 | -0.413 | -0.052 | -0.784 | -0.828 | -0.730 | -0.731 | -0.386 | -0.093 | 112 |
| B1_warming | d2_yes | 468 | 40 | +0.046 | +0.013 | +0.026 | +0.013 | -0.804 | -1.000 | -0.532 | -0.938 | -0.983 | -0.881 | -0.913 | -0.993 | -0.350 | 85 |
| B2_conflict_no | current_bracket_no | 634 | 38 | +0.183 | +0.169 | +0.215 | +0.060 | -0.367 | -0.557 | -0.140 | -0.787 | -0.875 | -0.673 | -0.737 | -0.460 | -0.496 | 27 |
| B2_conflict_no | d1_yes | 611 | 38 | +0.164 | +0.159 | +0.211 | +0.064 | -0.271 | -0.486 | -0.025 | -0.754 | -0.846 | -0.631 | -0.698 | -0.381 | -0.472 | 26 |
| B2_conflict_no | d2_yes | 451 | 38 | +0.049 | +0.022 | +0.031 | +0.009 | -0.806 | -0.947 | -0.626 | -0.949 | -0.980 | -0.912 | -0.935 | -0.945 | -0.850 | 23 |
| B3_warm+conflict | current_bracket_no | 133 | 35 | +0.247 | +0.301 | +0.338 | +0.068 | -0.042 | -0.384 | +0.298 | -0.689 | -0.824 | -0.529 | -0.619 | -0.275 | +0.370 | 4 |
| B3_warm+conflict | d1_yes | 133 | 35 | +0.208 | +0.286 | +0.308 | +0.045 | +0.100 | -0.309 | +0.512 | -0.652 | -0.817 | -0.457 | -0.571 | -0.179 | +0.151 | 4 |
| B3_warm+conflict | d2_yes | 103 | 33 | +0.066 | +0.019 | +0.039 | +0.019 | -0.937 | -1.000 | -0.827 | -0.962 | -1.000 | -0.901 | -0.942 | -1.000 | -0.138 | 4 |
| B4_B3+peak_ahead | current_bracket_no | 59 | 28 | +0.345 | +0.542 | +0.559 | +0.085 | +0.733 | +0.203 | +1.210 | -0.424 | -0.682 | -0.158 | -0.276 | +0.298 |  | 0 |
| B4_B3+peak_ahead | d1_yes | 59 | 28 | +0.286 | +0.525 | +0.508 | +0.034 | +1.032 | +0.383 | +1.638 | -0.350 | -0.675 | -0.001 | -0.176 | +0.508 |  | 0 |
| B4_B3+peak_ahead | d2_yes | 53 | 26 | +0.084 | +0.019 | +0.057 | +0.038 | -0.943 | -1.000 | -0.811 | -0.938 | -1.000 | -0.818 | -0.905 | -1.000 |  | 0 |

## Execution detail (touch ladder, worst day, maker-entry sensitivity)

| cell | leg | rows | touch15 | touch30 | worst_day_hold | maker_fill_rate_win | maker_fill_rate_lose | maker_roi_filled |
|---|---|---|---|---|---|---|---|---|
| A0_broad_cheap | d1_yes | 1085 | +0.074 | +0.053 | -34.000 | +0.182 | +0.929 | -0.855 |
| A0_broad_cheap | d2_yes | 975 | +0.093 | +0.058 | -39.000 | +0.195 | +0.957 | -0.850 |
| A0_broad_cheap | tail_yes | 182 | +0.110 | +0.044 | -16.000 | +0.667 | +0.983 | -0.631 |
| A1_warming | d1_yes | 383 | +0.070 | +0.039 | -18.000 | +0.100 | +0.895 | -0.934 |
| A1_warming | d2_yes | 396 | +0.104 | +0.063 | -16.000 | +0.250 | +0.934 | -0.775 |
| A1_warming | tail_yes | 98 | +0.102 | +0.051 | -11.000 | +0.500 | +0.979 | -0.701 |
| A2_peak_ahead | d1_yes | 454 | +0.106 | +0.059 | -24.000 | +0.208 | +0.947 | -0.776 |
| A2_peak_ahead | d2_yes | 607 | +0.105 | +0.059 | -28.000 | +0.120 | +0.955 | -0.884 |
| A2_peak_ahead | tail_yes | 158 | +0.127 | +0.051 | -14.000 | +0.667 | +1.000 | -0.581 |
| A3_conflict | d1_yes | 430 | +0.107 | +0.060 | -25.000 | +0.118 | +0.903 | -0.917 |
| A3_conflict | d2_yes | 220 | +0.141 | +0.068 | -11.000 | +0.000 | +0.940 | -1.000 |
| A3_conflict | tail_yes | 11 | +0.091 | +0.000 | -3.000 |  | +1.000 | -1.000 |
| A4_hot_city | d1_yes | 377 | +0.093 | +0.074 | -17.000 | +0.182 | +0.958 | -0.781 |
| A4_hot_city | d2_yes | 323 | +0.087 | +0.050 | -14.000 | +0.071 | +0.945 | -0.882 |
| A4_hot_city | tail_yes | 65 | +0.092 | +0.031 | -8.000 | +1.000 | +1.000 | -0.568 |
| A5_phys_core(warm+peak+conflict) | d1_yes | 174 | +0.098 | +0.029 | -10.000 | +0.000 | +0.929 | -1.000 |
| A5_phys_core(warm+peak+conflict) | d2_yes | 113 | +0.168 | +0.080 | -9.000 | +0.000 | +0.900 | -1.000 |
| A6_A5_hot_city | d1_yes | 60 | +0.083 | +0.017 | -6.000 | +0.000 | +0.983 | -1.000 |
| A6_A5_hot_city | d2_yes | 43 | +0.209 | +0.070 | -3.000 | +0.000 | +0.833 | -1.000 |
| B0_rich_current | current_bracket_no | 3036 | +0.162 | +0.109 | -89.558 | +0.255 | +0.931 | -0.864 |
| B0_rich_current | d1_yes | 2765 | +0.166 | +0.116 | -80.591 | +0.221 | +0.935 | -0.863 |
| B0_rich_current | d2_yes | 1794 | +0.024 | +0.017 | -82.000 | +0.208 | +0.933 | -0.970 |
| B1_warming | current_bracket_no | 676 | +0.256 | +0.176 | -22.000 | +0.235 | +0.934 | -0.827 |
| B1_warming | d1_yes | 646 | +0.255 | +0.184 | -24.000 | +0.202 | +0.935 | -0.818 |
| B1_warming | d2_yes | 468 | +0.028 | +0.021 | -19.000 | +0.167 | +0.944 | -0.958 |
| B2_conflict_no | current_bracket_no | 634 | +0.254 | +0.189 | -31.245 | +0.290 | +0.937 | -0.771 |
| B2_conflict_no | d1_yes | 611 | +0.250 | +0.182 | -27.307 | +0.237 | +0.924 | -0.811 |
| B2_conflict_no | d2_yes | 451 | +0.042 | +0.027 | -37.000 | +0.200 | +0.927 | -0.960 |
| B3_warm+conflict | current_bracket_no | 133 | +0.368 | +0.301 | -8.417 | +0.200 | +0.957 | -0.712 |
| B3_warm+conflict | d1_yes | 133 | +0.353 | +0.278 | -7.311 | +0.132 | +0.947 | -0.819 |
| B3_warm+conflict | d2_yes | 103 | +0.049 | +0.039 | -7.000 | +0.000 | +0.950 | -1.000 |
| B4_B3+peak_ahead | current_bracket_no | 59 | +0.627 | +0.525 | -2.417 | +0.219 | +0.926 | -0.206 |
| B4_B3+peak_ahead | d1_yes | 59 | +0.593 | +0.492 | -2.000 | +0.129 | +0.929 | -0.512 |
| B4_B3+peak_ahead | d2_yes | 53 | +0.075 | +0.057 | -5.000 | +0.000 | +0.923 | -1.000 |
