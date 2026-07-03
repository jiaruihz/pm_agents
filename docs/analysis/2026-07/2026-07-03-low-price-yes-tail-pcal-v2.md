# Low-Price YES Tail pcal v2 (pre-registered)

Generated: 2026-07-03T01:35:57.007423+00:00

## Verdict

```text
significance=PASS (train selected date-block CI)
baseline=FAIL (paired excess vs frozen v1 edge>=0.20 on train)
forward=NA (fresh forward starts 2026-07-01; settlements pending at build time)
conclusion=shadow_candidate
```

Trading action (before evidence): keep the $1 v1 live probe unchanged; run this frozen
selector as a zero-notional shadow tag (`pcal_v2_*` fields in
`scripts/ops/low_price_yes_integrated_tail_shadow_v2.py`); promotion decisions only from
fresh forward rows (>= 2026-07-01) against the gates in the tail review W3 section.
Acceptance did NOT fully pass: excess vs v1 is positive in point estimate but its CI
crosses 0, so this must not replace the live selector. The pcal-vs-complement excess on
the same denominator is significantly positive, and train deciles are calibrated and
monotone, which is why the selector is worth forward telemetry at all.

- Universe: no-edge cheap YES, one ticket/city-date; settled through 2026-06-28.
- theta=+0.0188 (max 5.0 tickets/day on train); K=2 variants declared.
- Acceptance (train): decile spearman +0.945 (pass=True), selected CI [+0.3%, +88.9%] (pass=True), excess vs v1 +31.8% CI [-30.8%, +96.8%] (pass=False).
- **acceptance all_pass = False**

## Strategy summary

| strategy | period | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | top5_removed_roi | top10_removed_roi | tickets_per_day |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pcal_v2_primary | train_le_2026_06_20 | 226 | +43.000 | +17.000 | +0.168 | +0.114 | +0.431 | +0.003 | +0.889 | +0.115 | -0.104 | +5.256 |
| fallback_edge20_hot_tail50 | train_le_2026_06_20 | 187 | +44.000 | +26.000 | +0.118 | +0.100 | +0.239 | -0.250 | +0.790 | -0.158 | -0.484 | +4.250 |
| baseline_v1_edge20 | train_le_2026_06_20 | 339 | +44.000 | +47.000 | +0.112 | +0.101 | +0.113 | -0.246 | +0.530 | -0.104 | -0.303 | +7.705 |
| baseline_no_edge_all | train_le_2026_06_20 | 1312 | +45.000 | +49.000 | +0.100 | +0.104 | -0.059 | -0.227 | +0.101 | -0.129 | -0.189 | +29.156 |
| pcal_vs_complement_excess | train_le_2026_06_20 | 226 |  |  |  |  | +0.592 | +0.111 | +1.101 |  |  |  |
| pcal_vs_v1_excess | train_le_2026_06_20 | 226 |  |  |  |  | +0.318 | -0.308 | +0.968 |  |  |  |
| pcal_v2_primary | diag_2026_06_21_28_burned | 41 | +6.000 | +11.000 | +0.098 | +0.097 | +0.218 | -0.743 | +1.447 | -1.000 | -1.000 | +6.833 |
| fallback_edge20_hot_tail50 | diag_2026_06_21_28_burned | 32 | +6.000 | +14.000 | +0.156 | +0.102 | +0.340 | -0.197 | +0.783 | -1.000 | -1.000 | +5.333 |
| baseline_v1_edge20 | diag_2026_06_21_28_burned | 68 | +6.000 | +30.000 | +0.162 | +0.107 | +0.480 | -0.416 | +1.616 | -0.376 | -0.914 | +11.333 |
| baseline_no_edge_all | diag_2026_06_21_28_burned | 220 | +7.000 | +47.000 | +0.091 | +0.105 | -0.045 | -0.293 | +0.256 | -0.374 | -0.641 | +31.429 |
| pcal_vs_complement_excess | diag_2026_06_21_28_burned | 41 |  |  |  |  | +0.323 | -0.788 | +1.562 |  |  |  |
| pcal_vs_v1_excess | diag_2026_06_21_28_burned | 41 |  |  |  |  | -0.262 | -1.281 | +0.648 |  |  |  |

## Execution sensitivity (train)

| strategy | period | rows | dates | cities | win_rate | avg_ask | roi | roi_ci_low | roi_ci_high | top5_removed_roi | top10_removed_roi | tickets_per_day |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pcal_primary_taker_ask_plus_1c | train | 226 | 43 | 17 | +0.168 | +0.124 | +0.305 | -0.083 | +0.716 | +0.034 | -0.163 | +5.256 |
| pcal_primary_fill_feasible | train | 132 | 31 | 13 | +0.197 | +0.114 | +0.741 | +0.153 | +1.341 | +0.243 | -0.090 | +4.258 |
| fallback_taker_ask_plus_1c | train | 187 | 44 | 26 | +0.118 | +0.110 | +0.109 | -0.323 | +0.598 | -0.232 | -0.521 | +4.250 |
| fallback_fill_feasible | train | 124 | 32 | 24 | +0.089 | +0.095 | -0.028 | -0.520 | +0.527 | -0.571 | -0.943 | +3.875 |

## p_cal train deciles

| decile | rows | win | p_cal | ask |
|---|---|---|---|---|
| +0.000 | +132.000 | +0.023 | +0.045 | +0.059 |
| +1.000 | +131.000 | +0.061 | +0.058 | +0.066 |
| +2.000 | +131.000 | +0.069 | +0.067 | +0.072 |
| +3.000 | +131.000 | +0.069 | +0.076 | +0.077 |
| +4.000 | +131.000 | +0.099 | +0.087 | +0.089 |
| +5.000 | +131.000 | +0.084 | +0.101 | +0.102 |
| +6.000 | +131.000 | +0.084 | +0.116 | +0.117 |
| +7.000 | +131.000 | +0.107 | +0.136 | +0.134 |
| +8.000 | +131.000 | +0.206 | +0.160 | +0.155 |
| +9.000 | +132.000 | +0.197 | +0.203 | +0.173 |

Frozen selector JSON: see companion .json; forward eval starts 2026-07-01 (settlement pending at build).
