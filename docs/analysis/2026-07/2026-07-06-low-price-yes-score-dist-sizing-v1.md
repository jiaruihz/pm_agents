# HeadA Score x Dist Sizing v1

Generated: `2026-07-06T05:20:57Z`

Scope: HeadA `forecast_tail_low_price_yes` only.  This tests whether the useful probability score should be combined with the existing `dist>0` hot-tail boundary as a selector or as a sizing input.  It does not change live.

## Verdict

```text
probability_sorting=PASS
score_as_selector=shadow_candidate_only
score_as_sizing=promising_shadow_ledger
live_action=no_change_no_size_up
```

人话结论：`dist>0` 是机制边界，score 是质量排序。最合理的下一步不是用 score 替换 `dist>0`，而是在 `dist>0` 里面按 score 调仓做 shadow。历史上高分票确实更强，但样本仍由少数尾部赢家驱动，不能直接 live 放大。

## Data Snapshot

- Base denominator: 476 rows, current HeadA ask 5-20c / edge>=0.20.
- Hot-tail `dist>0`: 333 rows / 53 dates / 47 cities.
- Target dates: 2026-05-06 .. 2026-06-30.
- Train score tertiles inside `dist>0`: low <= 0.1313, high > 0.1778.
- Cost model: price-tier 6/8/10 shares baseline, official Weather taker fee, hold-to-settlement.

## Score Quintiles Inside `dist>0`

| period | score_quintile | rows | dates | cities | win_rate | avg_entry | avg_score | avg_raw_dist | avg_adj_dist |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train_le_2026_06_20 | 0 | 55 | 33 | 22 | 7.3% | 6.8% | 9.3% | 0.487 | -0.300 |
| train_le_2026_06_20 | 1 | 55 | 35 | 31 | 5.5% | 7.9% | 12.5% | 0.794 | 0.073 |
| train_le_2026_06_20 | 2 | 55 | 30 | 33 | 10.9% | 9.3% | 15.3% | 0.909 | 0.285 |
| train_le_2026_06_20 | 3 | 55 | 35 | 30 | 16.4% | 12.1% | 18.9% | 0.903 | 0.168 |
| train_le_2026_06_20 | 4 | 55 | 30 | 28 | 32.7% | 16.5% | 27.7% | 0.899 | 0.235 |
| recent_ge_2026_06_21 | 0 | 19 | 9 | 11 | 10.5% | 6.7% | 9.3% | 0.635 | -0.267 |
| recent_ge_2026_06_21 | 1 | 7 | 4 | 6 | 0.0% | 7.3% | 12.8% | 0.762 | 0.258 |
| recent_ge_2026_06_21 | 2 | 13 | 6 | 10 | 7.7% | 9.1% | 15.6% | 0.940 | 0.388 |
| recent_ge_2026_06_21 | 3 | 9 | 7 | 9 | 44.4% | 12.5% | 19.0% | 0.785 | 0.188 |
| recent_ge_2026_06_21 | 4 | 10 | 6 | 9 | 30.0% | 16.6% | 27.1% | 0.792 | 0.149 |

Read: within the already-correct `dist>0` universe, score still carries rank information.  The useful interpretation is quality, not a replacement for the hot-tail boundary.

## Selector And Sizing Performance

| label | rows | dates | cities | win_rate | avg_entry | avg_score | avg_shares | cost | pnl | roi | roi_ci_low | roi_ci_high | losing_days | le_minus50pct_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dist_gt0_all | 333 | 53 | 47 | 15.0% | 10.4% | 16.6% | 7.610 | $+294.71 | $+123.29 | +41.8% | +10.7% | +76.3% | 19 | 16 | $-11.04 |
| dist_gt0_score_mid_high | 218 | 52 | 45 | 19.3% | 12.2% | 19.9% | 8.229 | $+238.70 | $+127.30 | +53.3% | +15.0% | +96.1% | 21 | 20 | $-10.25 |
| dist_gt0_score_high | 110 | 47 | 39 | 28.2% | 14.8% | 24.3% | 9.091 | $+158.02 | $+127.98 | +81.0% | +29.4% | +135.2% | 21 | 21 | $-8.35 |
| dist_gt0_score_low | 115 | 46 | 34 | 7.0% | 7.1% | 10.3% | 6.435 | $+56.00 | $-4.00 | -7.2% | -61.2% | +53.0% | 38 | 38 | $-3.72 |
| dist_gt0_all_score_tier_conservative | 333 | 53 | 47 | 15.0% | 10.4% | 16.6% | 7.805 | $+320.21 | $+156.29 | +48.8% | +15.0% | +86.2% | 19 | 17 | $-12.93 |
| dist_gt0_all_score_tier_balanced | 333 | 53 | 47 | 15.0% | 10.4% | 16.6% | 8.000 | $+345.71 | $+189.29 | +54.8% | +18.0% | +94.5% | 20 | 17 | $-14.82 |
| dist_gt0_all_score_tier_aggressive | 333 | 53 | 47 | 15.0% | 10.4% | 16.6% | 9.081 | $+413.75 | $+254.25 | +61.5% | +21.2% | +105.0% | 22 | 18 | $-18.86 |

- Baseline `dist_gt0_all` ROI: +41.8%.
- High-score selector ROI: +81.0%.
- Balanced score-tier sizing ROI: +54.8%.

## Train vs Recent

| period | label | rows | dates | cities | win_rate | avg_entry | avg_score | avg_shares | cost | pnl | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| train_le_2026_06_20 | dist_gt0_all | 275 | 44 | 46 | 14.5% | 10.5% | 16.7% | 7.658 | $+247.17 | $+86.83 | +35.1% | -0.1% | +76.4% |
| recent_ge_2026_06_21 | dist_gt0_all | 58 | 9 | 28 | 17.2% | 9.9% | 15.7% | 7.379 | $+47.53 | $+36.47 | +76.7% | +31.7% | +133.9% |
| train_le_2026_06_20 | dist_gt0_score_high | 92 | 39 | 36 | 26.1% | 14.8% | 24.5% | 9.109 | $+132.56 | $+89.44 | +67.5% | +7.1% | +131.7% |
| recent_ge_2026_06_21 | dist_gt0_score_high | 18 | 8 | 16 | 38.9% | 14.8% | 23.6% | 9.000 | $+25.46 | $+38.54 | +151.4% | +79.8% | +254.4% |
| train_le_2026_06_20 | dist_gt0_all_score_tier_balanced | 275 | 44 | 46 | 14.5% | 10.5% | 16.7% | 8.095 | $+290.35 | $+134.65 | +46.4% | +4.5% | +94.3% |
| recent_ge_2026_06_21 | dist_gt0_all_score_tier_balanced | 58 | 9 | 28 | 17.2% | 9.9% | 15.7% | 7.552 | $+55.36 | $+54.64 | +98.7% | +49.7% | +158.6% |

## Delta vs Current `dist>0` Baseline

| label | period | rows | dates | baseline_rows | cost_ratio_vs_baseline | delta_roi | delta_ci_low | delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dist_gt0_score_high | full | 110 | 47 | 333 | 53.6% | +39.2% | +11.3% | +70.2% |
| dist_gt0_score_high | train_le_2026_06_20 | 92 | 39 | 275 | 53.6% | +32.3% | +1.4% | +65.9% |
| dist_gt0_score_high | recent_ge_2026_06_21 | 18 | 8 | 58 | 53.6% | +74.7% | +2.7% | +159.0% |
| dist_gt0_all_score_tier_balanced | full | 333 | 53 | 333 | 117.3% | +12.9% | +3.0% | +23.5% |
| dist_gt0_all_score_tier_balanced | train_le_2026_06_20 | 275 | 44 | 275 | 117.5% | +11.2% | +0.8% | +22.8% |
| dist_gt0_all_score_tier_balanced | recent_ge_2026_06_21 | 58 | 9 | 58 | 116.5% | +22.0% | -7.4% | +51.5% |
| dist_gt0_all_score_tier_aggressive | full | 333 | 53 | 333 | 140.4% | +19.6% | +5.1% | +35.6% |
| dist_gt0_all_score_tier_aggressive | train_le_2026_06_20 | 275 | 44 | 275 | 140.6% | +16.9% | +1.3% | +34.1% |
| dist_gt0_all_score_tier_aggressive | recent_ge_2026_06_21 | 58 | 9 | 58 | 139.6% | +34.3% | -6.4% | +78.9% |

Selector interpretation:

- `score_high` is much cleaner than low-score rows, but it cuts volume and still needs forward proof before replacing the current selector.
- `score_mid_high` is a softer selector, useful for priority but not clearly better enough to become a gate.

Sizing interpretation:

- Score-tier sizing is the more natural use: keep the same mechanism boundary, shift notional from low-score to high-score tickets.
- The balanced rule is the cleanest shadow candidate: low score 0.5x, mid 1.0x, high 1.5x of current price-tier shares, capped at 15 shares.
- Aggressive sizing is research-only; it increases tail concentration and daily drawdown risk.

## Decision

Do not change live today.  Add a shadow ledger for:

```text
score_tier_hot_train = low/mid/high
shadow_sizing_score_tier_balanced
shadow_sizing_score_tier_conservative
shadow_selector_score_high
```

Forward gate before live sizing change:

- at least 15 fresh active target dates after ECMWF source repair;
- score tiers retain monotonic hit-rate direction;
- balanced score-tier sizing improves ROI or PnL per dollar after actual maker/fill costs;
- no material increase in max daily loss relative to current tiny sizing.
