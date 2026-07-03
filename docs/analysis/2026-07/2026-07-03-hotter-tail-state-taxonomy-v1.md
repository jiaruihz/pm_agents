# Hotter-Tail State Taxonomy v1 (exploratory)

Generated: 2026-07-03T13:59:25.762337+00:00

Status: hypothesis-generation on the fixed intraday denominator; nothing here is a
tradable rule until pre-registered and confirmed on fresh forward. No live changes.

## Verdict

```text
theme: intraday hotter-tail underpricing as a STANDING condition = does not exist
  every hotter event (up>=1 / ==1 / ==2 / 3+) is priced ABOVE realized frequency in
  every local-hour band (taker edge -1.2pp .. -4.3pp); physical state features
  (trend/gap/peak clock/minutes-since-max) separate outcomes strongly but the NO ask
  tracks realized P(hotter) almost exactly in every bucket -- the market prices the
  physics, plus a margin.
conditional alpha: only where the BOOK LAGS the physics inside the runway setup
  (price contradicts obs state), e.g. the runway one-step cell where the market
  still holds current_high >= 0.40 despite warming + runway -- that cell is not
  reproducible as any broad gap x trend region.
conclusion=shadow_research_only; no broad hotter-tail sleeve exists to promote
```

State -> expression map (from sections 1-4):

- stays_current / false-warming (gap<=0): no hotter expression is viable; all legs
  significantly negative. Fading the false warming via current-side YES was already
  tested (heat_death) and is not significant either -- spread eats the margin.
- up_1 (single overshoot): d1 YES, but ONLY under runway book-lag conditions
  (current still priced as live despite warming + runway, n=32 shadow_candidate).
  As a broad gap1_2|warming cell it is -20.1%.
- up_2 (double jump, gap>2 & warming): market prices the skip (d2 realized 27.4% vs
  ask 0.289). Least-bad expression is current_bracket NO (+5.9% point, CI crosses 0)
  -- i.e. no tradable edge, only reduced bleed.
- up_3plus (true high tail): intraday realized is roughly HALF of implied in every
  hour band; the retail hotter-lottery premium is largest here. If high-tail YES has
  any home it is the D-1 forecast-tail sleeve (station-bias prior, pre-obs), not intraday.
- basket variants (d1+d2, d1+d2+tail) never beat their best single leg; they average
  a good leg with overpriced ones.

Feature roles: trend/gap/peak/minutes are state CLASSIFIERS (fully priced, no
standalone edge); moisture/wind/city hot-tail prior add little intraday. Their value
is in building an implied-vs-physical divergence score (book staleness detector),
which is the pre-registered v2 direction: score = calibrated physical P(up>=1) minus
current_bracket NO ask; trade only extreme divergence; expression by predicted
landing (gap magnitude). Until that exists, the only live-adjacent artifact stays
the `runway_d1_yes_reversal` shadow branch.

Caveats: taker-at-ask calibration overstates overpricing by ~half-spread (typical
spread 2c); at mid the hotter side is roughly fair, so the premium is captured by
makers, not available to takers. 2026-07-01 orderbook exists but has no settled
labels yet; it is telemetry-only for this denominator.

- Denominator: 7842 settled city-date-hour snapshots, 2026-05-19..2026-06-30, 36 cities.
- Outcome join integrity: {'string_match': 7841, 'no_bracket_match': 210, 'numeric_fallback': 1}

## 1. Outcome taxonomy by local hour (row-normalized)

| decision_hour_local | stays_current | up_1 | up_2 | up_3plus | n |
|---|---|---|---|---|---|
| +10.000 | +0.111 | +0.198 | +0.235 | +0.456 | +586.000 |
| +11.000 | +0.183 | +0.268 | +0.232 | +0.316 | +693.000 |
| +12.000 | +0.291 | +0.268 | +0.239 | +0.202 | +757.000 |
| +13.000 | +0.444 | +0.283 | +0.175 | +0.098 | +756.000 |
| +14.000 | +0.602 | +0.251 | +0.117 | +0.031 | +814.000 |
| +15.000 | +0.752 | +0.192 | +0.048 | +0.007 | +807.000 |
| +16.000 | +0.873 | +0.115 | +0.012 | +0.000 | +828.000 |
| +17.000 | +0.965 | +0.032 | +0.002 | +0.000 | +800.000 |
| +18.000 | +0.985 | +0.015 | +0.000 | +0.000 | +684.000 |
| +19.000 | +0.992 | +0.008 | +0.000 | +0.000 | +508.000 |
| +20.000 | +0.991 | +0.009 | +0.000 | +0.000 | +351.000 |
| +21.000 | +1.000 | +0.000 | +0.000 | +0.000 | +258.000 |

## 2. Market calibration: implied vs realized per hotter event

| hour_band | event | rows | implied(avg ask) | realized | edge(realized-implied) |
|---|---|---|---|---|---|
| h10_12 | up_ge_1 (current NO) | 1215 | +0.726 | +0.689 | -0.037 |
| h10_12 | up_eq_1 (d1 YES) | 1894 | +0.299 | +0.261 | -0.038 |
| h10_12 | up_eq_2 (d2 YES) | 1921 | +0.273 | +0.249 | -0.025 |
| h10_12 | tail_3p (cheapest 3+) | 652 | +0.071 | +0.057 | -0.015 |
| h13_15 | up_ge_1 (current NO) | 2021 | +0.382 | +0.340 | -0.042 |
| h13_15 | up_eq_1 (d1 YES) | 2220 | +0.296 | +0.256 | -0.040 |
| h13_15 | up_eq_2 (d2 YES) | 1820 | +0.166 | +0.146 | -0.020 |
| h13_15 | tail_3p (cheapest 3+) | 375 | +0.036 | +0.024 | -0.012 |
| h16_18 | up_ge_1 (current NO) | 1360 | +0.113 | +0.088 | -0.025 |
| h16_18 | up_eq_1 (d1 YES) | 1200 | +0.130 | +0.108 | -0.022 |
| h16_18 | up_eq_2 (d2 YES) | 695 | +0.040 | +0.017 | -0.023 |
| h16_18 | tail_3p (cheapest 3+) | 108 | +0.022 | +0.000 | -0.022 |
| h19_21 | up_ge_1 (current NO) | 165 | +0.034 | +0.030 | -0.004 |
| h19_21 | up_eq_1 (d1 YES) | 118 | +0.054 | +0.051 | -0.003 |
| h19_21 | up_eq_2 (d2 YES) | 49 | +0.014 | +0.000 | -0.014 |

## 3. Feature discrimination (realized hotter rates by bucket)

| feature | bucket | rows | p_up_ge_1 | p_up_eq_1 | p_up_ge_2 | avg_d1_ask | avg_no_ask |
|---|---|---|---|---|---|---|---|
| temp_trend_1h_f | (-99.0, -0.5] | 2247 | +0.066 | +0.039 | +0.027 | +0.062 | +0.079 |
| temp_trend_1h_f | (-0.5, 0.4] | 3107 | +0.307 | +0.166 | +0.141 | +0.205 | +0.335 |
| temp_trend_1h_f | (0.4, 99.0] | 2478 | +0.651 | +0.247 | +0.404 | +0.296 | +0.623 |
| forecast_gap_native | (-99.0, 0.0] | 3224 | +0.147 | +0.110 | +0.036 | +0.155 | +0.166 |
| forecast_gap_native | (0.0, 1.0] | 1387 | +0.346 | +0.209 | +0.137 | +0.244 | +0.339 |
| forecast_gap_native | (1.0, 2.0] | 969 | +0.540 | +0.261 | +0.279 | +0.285 | +0.523 |
| forecast_gap_native | (2.0, 99.0] | 1392 | +0.739 | +0.157 | +0.583 | +0.199 | +0.714 |
| forecast_peak_delta_hours_local | (-99, -2] | 1920 | +0.818 | +0.261 | +0.557 | +0.293 | +0.786 |
| forecast_peak_delta_hours_local | (-2, 0] | 1217 | +0.394 | +0.295 | +0.099 | +0.333 | +0.426 |
| forecast_peak_delta_hours_local | (0, 2] | 1241 | +0.085 | +0.070 | +0.015 | +0.113 | +0.116 |
| forecast_peak_delta_hours_local | (2, 99] | 2594 | +0.135 | +0.066 | +0.069 | +0.096 | +0.141 |
| minutes_since_running_max | (-1, 45] | 4179 | +0.573 | +0.252 | +0.321 | +0.294 | +0.568 |
| minutes_since_running_max | (45, 120] | 1143 | +0.150 | +0.078 | +0.073 | +0.108 | +0.170 |
| minutes_since_running_max | (120, 9999] | 2520 | +0.060 | +0.029 | +0.031 | +0.046 | +0.071 |
| dewpoint_depression_f | (-99, 10] | 2536 | +0.315 | +0.122 | +0.193 | +0.162 | +0.315 |
| dewpoint_depression_f | (10, 25] | 3917 | +0.359 | +0.168 | +0.191 | +0.216 | +0.363 |
| dewpoint_depression_f | (25, 199] | 1380 | +0.370 | +0.180 | +0.189 | +0.207 | +0.330 |
| wind_speed_kt | (-1, 8] | 4089 | +0.402 | +0.155 | +0.247 | +0.198 | +0.384 |
| wind_speed_kt | (8, 15] | 2997 | +0.303 | +0.161 | +0.141 | +0.202 | +0.311 |
| wind_speed_kt | (15, 99] | 747 | +0.218 | +0.130 | +0.088 | +0.178 | +0.237 |
| city_hot_tail_pct | (-1.0, 0.45] | 3089 | +0.320 | +0.163 | +0.157 | +0.200 | +0.336 |
| city_hot_tail_pct | (0.45, 0.6] | 2068 | +0.357 | +0.134 | +0.223 | +0.190 | +0.342 |
| city_hot_tail_pct | (0.6, 1.01] | 2685 | +0.369 | +0.162 | +0.207 | +0.200 | +0.348 |

## 4. Physical core cells (gap x trend): same-snapshot expression A/B

| slice | expr | rows | dates | cities | win_rate | avg_cost | roi | roi_ci_low | roi_ci_high | cell_rows |
|---|---|---|---|---|---|---|---|---|---|---|
| gap<=0|cooling | d1_yes | 474 | 39 | 35 | +0.061 | +0.086 | -0.395 | -0.675 | -0.079 | 1231 |
| gap<=0|cooling | d2_yes | 256 | 36 | 34 | +0.027 | +0.052 | -0.853 | -0.955 | -0.718 | 1231 |
| gap<=0|cooling | tail3p_yes | 36 | 25 | 22 | +0.000 | +0.017 | -1.000 | -1.000 | -1.000 | 1231 |
| gap<=0|cooling | current_bracket_no | 562 | 39 | 35 | +0.066 | +0.094 | -0.564 | -0.762 | -0.335 | 1231 |
| gap<=0|cooling | basket_d1_d2 | 236 | 35 | 34 | +0.131 | +0.199 | -0.498 | -0.742 | -0.227 | 1231 |
| gap<=0|cooling | basket_d1_d2_tail | 31 | 22 | 17 | +0.129 | +0.314 | -0.801 | -0.968 | -0.575 | 1231 |
| gap<=0|flat | d1_yes | 827 | 39 | 36 | +0.168 | +0.221 | -0.203 | -0.527 | +0.229 | 1280 |
| gap<=0|flat | d2_yes | 585 | 39 | 35 | +0.067 | +0.087 | -0.646 | -0.801 | -0.460 | 1280 |
| gap<=0|flat | tail3p_yes | 132 | 34 | 30 | +0.000 | +0.028 | -1.000 | -1.000 | -1.000 | 1280 |
| gap<=0|flat | current_bracket_no | 885 | 39 | 36 | +0.194 | +0.253 | -0.331 | -0.600 | +0.039 | 1280 |
| gap<=0|flat | basket_d1_d2 | 580 | 39 | 35 | +0.286 | +0.371 | -0.309 | -0.461 | -0.134 | 1280 |
| gap<=0|flat | basket_d1_d2_tail | 124 | 34 | 30 | +0.468 | +0.559 | -0.046 | -0.346 | +0.273 | 1280 |
| gap<=0|warming | d1_yes | 609 | 39 | 36 | +0.300 | +0.340 | -0.234 | -0.365 | -0.088 | 712 |
| gap<=0|warming | d2_yes | 498 | 39 | 36 | +0.108 | +0.152 | -0.491 | -0.666 | -0.301 | 712 |
| gap<=0|warming | tail3p_yes | 136 | 36 | 33 | +0.015 | +0.029 | -0.937 | -1.000 | -0.826 | 712 |
| gap<=0|warming | current_bracket_no | 527 | 39 | 35 | +0.287 | +0.362 | -0.416 | -0.519 | -0.305 | 712 |
| gap<=0|warming | basket_d1_d2 | 494 | 39 | 36 | +0.451 | +0.546 | -0.274 | -0.380 | -0.161 | 712 |
| gap<=0|warming | basket_d1_d2_tail | 134 | 36 | 33 | +0.560 | +0.705 | -0.316 | -0.438 | -0.192 | 712 |
| gap0_1|cooling | d1_yes | 173 | 36 | 30 | +0.168 | +0.169 | -0.331 | -0.585 | -0.052 | 395 |
| gap0_1|cooling | d2_yes | 124 | 35 | 29 | +0.048 | +0.081 | -0.679 | -0.909 | -0.372 | 395 |
| gap0_1|cooling | tail3p_yes | 27 | 17 | 16 | +0.000 | +0.013 | -1.000 | -1.000 | -1.000 | 395 |
| gap0_1|cooling | current_bracket_no | 189 | 36 | 30 | +0.180 | +0.196 | -0.484 | -0.653 | -0.286 | 395 |
| gap0_1|cooling | basket_d1_d2 | 117 | 35 | 28 | +0.299 | +0.317 | -0.195 | -0.436 | +0.074 | 395 |
| gap0_1|cooling | basket_d1_d2_tail | 26 | 17 | 16 | +0.346 | +0.436 | -0.411 | -0.677 | -0.073 | 395 |
| gap0_1|flat | d1_yes | 461 | 38 | 35 | +0.286 | +0.293 | -0.063 | -0.376 | +0.340 | 586 |
| gap0_1|flat | d2_yes | 365 | 38 | 34 | +0.123 | +0.157 | -0.345 | -0.612 | -0.054 | 586 |
| gap0_1|flat | tail3p_yes | 91 | 28 | 27 | +0.011 | +0.031 | -0.985 | -1.000 | -0.947 | 586 |
| gap0_1|flat | current_bracket_no | 455 | 38 | 35 | +0.380 | +0.405 | -0.174 | -0.427 | +0.124 | 586 |
| gap0_1|flat | basket_d1_d2 | 364 | 38 | 34 | +0.478 | +0.513 | -0.154 | -0.309 | +0.045 | 586 |
| gap0_1|flat | basket_d1_d2_tail | 86 | 28 | 25 | +0.581 | +0.650 | -0.234 | -0.414 | -0.040 | 586 |
| gap0_1|warming | d1_yes | 377 | 39 | 36 | +0.340 | +0.384 | -0.122 | -0.330 | +0.088 | 406 |
| gap0_1|warming | d2_yes | 337 | 39 | 36 | +0.258 | +0.247 | -0.043 | -0.333 | +0.331 | 406 |
| gap0_1|warming | tail3p_yes | 104 | 34 | 30 | +0.038 | +0.071 | -0.854 | -1.000 | -0.558 | 406 |
| gap0_1|warming | current_bracket_no | 278 | 39 | 36 | +0.522 | +0.558 | -0.101 | -0.287 | +0.090 | 406 |
| gap0_1|warming | basket_d1_d2 | 336 | 39 | 36 | +0.628 | +0.664 | +0.032 | -0.165 | +0.283 | 406 |
| gap0_1|warming | basket_d1_d2_tail | 102 | 34 | 30 | +0.618 | +0.764 | -0.242 | -0.388 | -0.084 | 406 |
| gap1_2|cooling | d1_yes | 87 | 29 | 26 | +0.161 | +0.191 | -0.331 | -0.790 | +0.472 | 178 |
| gap1_2|cooling | d2_yes | 61 | 25 | 23 | +0.115 | +0.156 | -0.734 | -0.941 | -0.473 | 178 |
| gap1_2|cooling | tail3p_yes | 18 | 14 | 14 | +0.056 | +0.041 | -0.691 | -1.000 | +0.111 | 178 |
| gap1_2|cooling | current_bracket_no | 99 | 30 | 27 | +0.222 | +0.251 | -0.386 | -0.766 | +0.199 | 178 |
| gap1_2|cooling | basket_d1_d2 | 59 | 24 | 22 | +0.356 | +0.426 | -0.120 | -0.651 | +0.805 | 178 |
| gap1_2|cooling | basket_d1_d2_tail | 18 | 14 | 14 | +0.556 | +0.655 | -0.244 | -0.601 | +0.199 | 178 |
| gap1_2|flat | d1_yes | 367 | 36 | 35 | +0.316 | +0.318 | -0.097 | -0.291 | +0.126 | 426 |
| gap1_2|flat | d2_yes | 330 | 36 | 35 | +0.176 | +0.232 | -0.471 | -0.631 | -0.290 | 426 |
| gap1_2|flat | tail3p_yes | 84 | 30 | 25 | +0.060 | +0.031 | -0.329 | -0.959 | +0.549 | 426 |
| gap1_2|flat | current_bracket_no | 329 | 36 | 33 | +0.529 | +0.546 | -0.148 | -0.306 | +0.020 | 426 |
| gap1_2|flat | basket_d1_d2 | 327 | 36 | 35 | +0.523 | +0.580 | -0.138 | -0.268 | -0.005 | 426 |
| gap1_2|flat | basket_d1_d2_tail | 83 | 30 | 25 | +0.542 | +0.682 | -0.308 | -0.450 | -0.167 | 426 |
| gap1_2|warming | d1_yes | 343 | 38 | 36 | +0.350 | +0.366 | -0.210 | -0.388 | -0.012 | 365 |
| gap1_2|warming | d2_yes | 327 | 38 | 36 | +0.306 | +0.313 | -0.062 | -0.331 | +0.340 | 365 |
| gap1_2|warming | tail3p_yes | 94 | 32 | 33 | +0.053 | +0.053 | -0.580 | -0.938 | -0.156 | 365 |
| gap1_2|warming | current_bracket_no | 232 | 36 | 34 | +0.685 | +0.680 | -0.091 | -0.215 | +0.035 | 365 |
| gap1_2|warming | basket_d1_d2 | 323 | 38 | 36 | +0.663 | +0.686 | -0.059 | -0.154 | +0.048 | 365 |
| gap1_2|warming | basket_d1_d2_tail | 91 | 32 | 33 | +0.670 | +0.745 | -0.111 | -0.279 | +0.065 | 365 |
| gap>2|cooling | d1_yes | 95 | 33 | 26 | +0.116 | +0.173 | -0.705 | -0.871 | -0.488 | 180 |
| gap>2|cooling | d2_yes | 79 | 33 | 26 | +0.190 | +0.184 | -0.174 | -0.723 | +0.676 | 180 |
| gap>2|cooling | tail3p_yes | 20 | 13 | 12 | +0.100 | +0.035 | +0.569 | -1.000 | +3.069 | 180 |
| gap>2|cooling | current_bracket_no | 90 | 33 | 24 | +0.333 | +0.356 | -0.555 | -0.735 | -0.322 | 180 |
| gap>2|cooling | basket_d1_d2 | 76 | 33 | 26 | +0.342 | +0.400 | -0.424 | -0.637 | -0.171 | 180 |
| gap>2|cooling | basket_d1_d2_tail | 17 | 11 | 11 | +0.588 | +0.553 | -0.200 | -0.578 | +0.227 | 180 |
| gap>2|flat | d1_yes | 374 | 38 | 34 | +0.227 | +0.249 | -0.189 | -0.414 | +0.091 | 436 |
| gap>2|flat | d2_yes | 368 | 38 | 35 | +0.220 | +0.259 | -0.340 | -0.560 | -0.068 | 436 |
| gap>2|flat | tail3p_yes | 98 | 30 | 30 | +0.041 | +0.081 | -0.856 | -1.000 | -0.646 | 436 |
| gap>2|flat | current_bracket_no | 283 | 38 | 32 | +0.615 | +0.646 | -0.253 | -0.367 | -0.139 | 436 |
| gap>2|flat | basket_d1_d2 | 349 | 38 | 34 | +0.476 | +0.533 | -0.242 | -0.385 | -0.067 | 436 |
| gap>2|flat | basket_d1_d2_tail | 88 | 29 | 29 | +0.602 | +0.623 | -0.172 | -0.365 | +0.016 | 436 |
| gap>2|warming | d1_yes | 671 | 39 | 35 | +0.170 | +0.221 | -0.332 | -0.560 | +0.002 | 772 |
| gap>2|warming | d2_yes | 707 | 39 | 35 | +0.272 | +0.288 | -0.230 | -0.368 | -0.074 | 772 |
| gap>2|warming | tail3p_yes | 224 | 38 | 35 | +0.085 | +0.102 | -0.632 | -0.880 | -0.252 | 772 |
| gap>2|warming | current_bracket_no | 284 | 39 | 32 | +0.831 | +0.805 | +0.051 | -0.064 | +0.173 | 772 |
| gap>2|warming | basket_d1_d2 | 655 | 39 | 35 | +0.458 | +0.526 | -0.207 | -0.344 | -0.052 | 772 |
| gap>2|warming | basket_d1_d2_tail | 203 | 38 | 34 | +0.493 | +0.575 | -0.249 | -0.384 | -0.086 | 772 |
