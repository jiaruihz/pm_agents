# Hotter-Tail Reversal Shapes v1 (exploratory, path-aware)

Generated: 2026-07-04T15:59:34.537668+00:00

K = 36 pre-declared cells (all reported).  Hourly-sampled paths:
touch/TP metrics are conservative lower bounds; +1c taker entry, threshold-1c TP exit.
Conclusions limited to shadow_candidate / inconclusive; no live changes.

## Verdict

```text
broad_cheap_hotter_lottery (intraday 3-10c d1/d2/tail):  NEGATIVE, not rescueable
  all 7 Shape-A cells x 3 legs lose on hold AND on TP20/TP30; the hypothesized
  pump path barely exists (touch20 5-14%, pump-then-die 2-8%); conditioning on
  good physics makes it WORSE (what stays cheap when physics favor hotter is the
  leg beyond the move).  This is intraday-cheap-band specific; the D-1 low-price
  sleeve is a different denominator and keeps its own shadow_candidate status.

rich_current_collapse_reversal (Shape B4):  inconclusive_positive_signal_keep_shadow
  state: current_high YES still >= 0.60 while obs warming (trend_1h >= +0.5F),
  forecast max lands >= 1 bracket above current (bracket-aware), forecast peak
  still ahead.  d1 YES hold: 70 rows / 29 dates,
  win +32.9% @ avg ask 0.217,
  ROI +30.8% CI [-33.0%, +105.7%],
  top5-removed -17.2%.
  After the Single Runs PIT backfill rejoin this no longer clears significance;
  keep as zero-notional forward shadow only.

execution for the reversal shape:  TAKER entry + HOLD to settlement
  TP20 = -35%, TP30 = -18% (kills the payoff; win rate is ~50%, not a lottery)
  maker-first entry = adversely selected: winners fill 12.9% vs losers 92.9%,
  maker ROI on filled -51%.  This is the sharpest execution result of the round.

expression ranking inside the shape:  d1 YES +30.8% vs
  current_bracket NO +9.3%; d2 YES remains weak. The collapse is usually one
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
| A2_peak_ahead | d1_yes | 473 | 36 | +0.064 | +0.049 | +0.072 | +0.032 | -0.368 | -0.692 | +0.055 | -0.726 | -0.860 | -0.556 | -0.679 | -0.576 | -1.000 | 6 |
| A2_peak_ahead | d2_yes | 602 | 35 | +0.063 | +0.043 | +0.076 | +0.035 | -0.412 | -0.682 | -0.090 | -0.778 | -0.874 | -0.640 | -0.737 | -0.569 | -1.000 | 6 |
| A2_peak_ahead | tail_yes | 160 | 31 | +0.057 | +0.037 | +0.081 | +0.044 | -0.556 | -0.916 | -0.074 | -0.806 | -0.915 | -0.652 | -0.835 | -0.941 |  | 0 |
| A3_conflict | d1_yes | 420 | 38 | +0.063 | +0.033 | +0.074 | +0.045 | -0.539 | -0.790 | -0.225 | -0.762 | -0.866 | -0.624 | -0.724 | -0.769 | -0.653 | 32 |
| A3_conflict | d2_yes | 281 | 36 | +0.062 | +0.021 | +0.096 | +0.075 | -0.700 | -0.920 | -0.403 | -0.768 | -0.874 | -0.643 | -0.718 | -0.955 | -1.000 | 17 |
| A3_conflict | tail_yes | 14 | 9 | +0.061 | +0.000 | +0.071 | +0.071 | -1.000 | -1.000 | -1.000 | -0.812 | -1.000 | -0.296 | -1.000 | -1.000 |  | 0 |
| A4_hot_city | d1_yes | 377 | 39 | +0.060 | +0.058 | +0.074 | +0.027 | -0.231 | -0.615 | +0.204 | -0.699 | -0.862 | -0.478 | -0.599 | -0.472 | -0.569 | 58 |
| A4_hot_city | d2_yes | 323 | 39 | +0.060 | +0.043 | +0.065 | +0.025 | -0.355 | -0.714 | +0.065 | -0.774 | -0.913 | -0.564 | -0.742 | -0.637 | -0.683 | 35 |
| A4_hot_city | tail_yes | 65 | 28 | +0.056 | +0.031 | +0.046 | +0.015 | -0.663 | -1.000 | +0.114 | -0.876 | -1.000 | -0.703 | -0.902 | -1.000 | -1.000 | 4 |
| A5_phys_core(warm+peak+conflict) | d1_yes | 175 | 33 | +0.062 | +0.011 | +0.034 | +0.029 | -0.813 | -1.000 | -0.487 | -0.848 | -0.959 | -0.695 | -0.870 | -1.000 | -1.000 | 1 |
| A5_phys_core(warm+peak+conflict) | d2_yes | 122 | 32 | +0.063 | +0.025 | +0.107 | +0.082 | -0.679 | -1.000 | -0.321 | -0.741 | -0.875 | -0.590 | -0.690 | -1.000 | -1.000 | 1 |
| A5_phys_core(warm+peak+conflict) | tail_yes | 8 | 8 | +0.063 | +0.000 | +0.125 | +0.125 | -1.000 | -1.000 | -1.000 | -0.670 | -1.000 | -0.010 | -1.000 | -1.000 |  | 0 |
| A6_A5_hot_city | d1_yes | 57 | 23 | +0.063 | +0.018 | +0.000 | +0.000 | -0.825 | -1.000 | -0.400 | -0.825 | -1.000 | -0.400 | -0.825 | -1.000 | -1.000 | 1 |
| A6_A5_hot_city | d2_yes | 51 | 26 | +0.062 | +0.039 | +0.118 | +0.078 | -0.478 | -1.000 | +0.351 | -0.730 | -0.929 | -0.467 | -0.790 | -1.000 | -1.000 | 1 |
| B0_rich_current | current_bracket_no | 3053 | 41 | +0.135 | +0.102 | +0.137 | +0.047 | -0.502 | -0.604 | -0.389 | -0.817 | -0.867 | -0.752 | -0.784 | -0.556 | -0.560 | 559 |
| B0_rich_current | d1_yes | 2765 | 40 | +0.130 | +0.103 | +0.141 | +0.044 | -0.396 | -0.523 | -0.254 | -0.812 | -0.866 | -0.728 | -0.766 | -0.463 | -0.505 | 486 |
| B0_rich_current | d2_yes | 1794 | 40 | +0.038 | +0.013 | +0.020 | +0.008 | -0.832 | -0.943 | -0.676 | -0.939 | -0.975 | -0.892 | -0.923 | -0.901 | -0.526 | 312 |
| B1_warming | current_bracket_no | 681 | 41 | +0.195 | +0.169 | +0.226 | +0.076 | -0.364 | -0.495 | -0.226 | -0.788 | -0.830 | -0.737 | -0.743 | -0.467 | -0.201 | 117 |
| B1_warming | d1_yes | 646 | 40 | +0.175 | +0.169 | +0.226 | +0.065 | -0.241 | -0.413 | -0.052 | -0.784 | -0.828 | -0.730 | -0.731 | -0.386 | -0.093 | 112 |
| B1_warming | d2_yes | 468 | 40 | +0.046 | +0.013 | +0.026 | +0.013 | -0.804 | -1.000 | -0.532 | -0.938 | -0.983 | -0.881 | -0.913 | -0.993 | -0.350 | 85 |
| B2_conflict_no | current_bracket_no | 686 | 38 | +0.177 | +0.143 | +0.190 | +0.063 | -0.464 | -0.651 | -0.231 | -0.808 | -0.891 | -0.685 | -0.778 | -0.553 | -0.496 | 27 |
| B2_conflict_no | d1_yes | 658 | 38 | +0.157 | +0.137 | +0.181 | +0.056 | -0.365 | -0.590 | -0.101 | -0.791 | -0.871 | -0.662 | -0.743 | -0.470 | -0.472 | 26 |
| B2_conflict_no | d2_yes | 490 | 38 | +0.044 | +0.016 | +0.035 | +0.018 | -0.849 | -0.972 | -0.681 | -0.931 | -0.970 | -0.877 | -0.906 | -0.975 | -0.850 | 23 |
| B3_warm+conflict | current_bracket_no | 154 | 35 | +0.231 | +0.253 | +0.292 | +0.065 | -0.140 | -0.492 | +0.243 | -0.726 | -0.850 | -0.565 | -0.672 | -0.380 | +0.370 | 4 |
| B3_warm+conflict | d1_yes | 155 | 35 | +0.190 | +0.239 | +0.252 | +0.032 | -0.028 | -0.430 | +0.399 | -0.705 | -0.854 | -0.505 | -0.629 | -0.305 | +0.151 | 4 |
| B3_warm+conflict | d2_yes | 117 | 33 | +0.054 | +0.017 | +0.051 | +0.034 | -0.945 | -1.000 | -0.854 | -0.912 | -0.995 | -0.778 | -0.865 | -1.000 | -0.138 | 4 |
| B4_B3+peak_ahead | current_bracket_no | 70 | 29 | +0.269 | +0.329 | +0.429 | +0.129 | +0.093 | -0.427 | +0.698 | -0.600 | -0.797 | -0.331 | -0.549 | -0.298 |  | 0 |
| B4_B3+peak_ahead | d1_yes | 70 | 29 | +0.217 | +0.329 | +0.371 | +0.057 | +0.308 | -0.330 | +1.057 | -0.586 | -0.821 | -0.250 | -0.496 | -0.172 |  | 0 |
| B4_B3+peak_ahead | d2_yes | 57 | 28 | +0.057 | +0.000 | +0.070 | +0.070 | -1.000 | -1.000 | -1.000 | -0.840 | -1.000 | -0.555 | -0.756 | -1.000 |  | 0 |

## Execution detail (touch ladder, worst day, maker-entry sensitivity)

| cell | leg | rows | touch15 | touch30 | worst_day_hold | maker_fill_rate_win | maker_fill_rate_lose | maker_roi_filled |
|---|---|---|---|---|---|---|---|---|
| A0_broad_cheap | d1_yes | 1085 | +0.074 | +0.053 | -34.000 | +0.182 | +0.929 | -0.855 |
| A0_broad_cheap | d2_yes | 975 | +0.093 | +0.058 | -39.000 | +0.195 | +0.957 | -0.850 |
| A0_broad_cheap | tail_yes | 182 | +0.110 | +0.044 | -16.000 | +0.667 | +0.983 | -0.631 |
| A1_warming | d1_yes | 383 | +0.070 | +0.039 | -18.000 | +0.100 | +0.895 | -0.934 |
| A1_warming | d2_yes | 396 | +0.104 | +0.063 | -16.000 | +0.250 | +0.934 | -0.775 |
| A1_warming | tail_yes | 98 | +0.102 | +0.051 | -11.000 | +0.500 | +0.979 | -0.701 |
| A2_peak_ahead | d1_yes | 473 | +0.106 | +0.059 | -25.000 | +0.174 | +0.953 | -0.826 |
| A2_peak_ahead | d2_yes | 602 | +0.105 | +0.060 | -29.000 | +0.115 | +0.962 | -0.884 |
| A2_peak_ahead | tail_yes | 160 | +0.125 | +0.050 | -14.000 | +0.667 | +0.994 | -0.584 |
| A3_conflict | d1_yes | 420 | +0.110 | +0.057 | -21.000 | +0.143 | +0.899 | -0.927 |
| A3_conflict | d2_yes | 281 | +0.132 | +0.075 | -17.000 | +0.000 | +0.938 | -1.000 |
| A3_conflict | tail_yes | 14 | +0.214 | +0.000 | -3.000 |  | +1.000 | -1.000 |
| A4_hot_city | d1_yes | 377 | +0.093 | +0.074 | -17.000 | +0.182 | +0.958 | -0.781 |
| A4_hot_city | d2_yes | 323 | +0.087 | +0.050 | -14.000 | +0.071 | +0.945 | -0.882 |
| A4_hot_city | tail_yes | 65 | +0.092 | +0.031 | -8.000 | +1.000 | +1.000 | -0.568 |
| A5_phys_core(warm+peak+conflict) | d1_yes | 175 | +0.086 | +0.017 | -11.000 | +0.000 | +0.931 | -1.000 |
| A5_phys_core(warm+peak+conflict) | d2_yes | 122 | +0.164 | +0.082 | -8.000 | +0.000 | +0.916 | -1.000 |
| A5_phys_core(warm+peak+conflict) | tail_yes | 8 | +0.250 | +0.000 | -1.000 |  | +1.000 | -1.000 |
| A6_A5_hot_city | d1_yes | 57 | +0.070 | +0.000 | -7.000 | +0.000 | +0.982 | -1.000 |
| A6_A5_hot_city | d2_yes | 51 | +0.176 | +0.059 | -4.000 | +0.000 | +0.878 | -1.000 |
| B0_rich_current | current_bracket_no | 3053 | +0.161 | +0.108 | -89.558 | +0.255 | +0.930 | -0.865 |
| B0_rich_current | d1_yes | 2765 | +0.166 | +0.116 | -80.591 | +0.221 | +0.935 | -0.863 |
| B0_rich_current | d2_yes | 1794 | +0.024 | +0.017 | -82.000 | +0.208 | +0.933 | -0.970 |
| B1_warming | current_bracket_no | 681 | +0.254 | +0.175 | -22.000 | +0.235 | +0.933 | -0.829 |
| B1_warming | d1_yes | 646 | +0.255 | +0.184 | -24.000 | +0.202 | +0.935 | -0.818 |
| B1_warming | d2_yes | 468 | +0.028 | +0.021 | -19.000 | +0.167 | +0.944 | -0.958 |
| B2_conflict_no | current_bracket_no | 686 | +0.238 | +0.156 | -37.222 | +0.296 | +0.939 | -0.832 |
| B2_conflict_no | d1_yes | 658 | +0.223 | +0.153 | -34.833 | +0.267 | +0.931 | -0.834 |
| B2_conflict_no | d2_yes | 490 | +0.041 | +0.031 | -32.000 | +0.250 | +0.932 | -0.964 |
| B3_warm+conflict | current_bracket_no | 154 | +0.325 | +0.247 | -8.000 | +0.231 | +0.930 | -0.753 |
| B3_warm+conflict | d1_yes | 155 | +0.290 | +0.232 | -8.000 | +0.162 | +0.941 | -0.853 |
| B3_warm+conflict | d2_yes | 117 | +0.060 | +0.051 | -10.000 | +0.000 | +0.930 | -1.000 |
| B4_B3+peak_ahead | current_bracket_no | 70 | +0.486 | +0.343 | -5.000 | +0.304 | +0.957 | -0.531 |
| B4_B3+peak_ahead | d1_yes | 70 | +0.443 | +0.329 | -5.000 | +0.174 | +0.957 | -0.744 |
| B4_B3+peak_ahead | d2_yes | 57 | +0.088 | +0.070 | -7.000 |  | +0.947 | -1.000 |
