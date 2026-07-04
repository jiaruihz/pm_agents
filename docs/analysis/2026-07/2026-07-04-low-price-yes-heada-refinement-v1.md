# HeadA Low-Price YES Refinement v1

Generated: 2026-07-04T00:55:11+00:00

Scope: only HeadA `forecast_tail_low_price_yes`.  This is a refinement study for the existing low-price forecast-tail YES sleeve, not METAR reversal and not tmax distribution.

## One-Line Read

After removing `dist<=0`, HeadA is cleaner but still not ready to size up.  The strongest practical next step is **keep live tiny, collect fresh book-state/fill data, and shadow fixed-share / price-tier sizing**.  The two tempting quick fixes do not pass: hotter sibling replay does **not** show that we should simply buy the next higher bracket, and strict stop only looks useful in some windows after costs, so it stays telemetry.

```text
significance=NA/partial
baseline=PARTIAL
forward=FAIL/NA
conclusion=shadow_candidate for current tiny probe; no live size-up
```

## Data Snapshot

- Synced N100 mirror and rebuilt `runtime/weather.db` before running this script.
- CLOB fill coverage gate: `gate_pass=true` after rebuild.
- Frozen HeadA denominator: 476 rows, 2026-05-06..2026-06-30, 48 cities.
- Current implemented selector after 2026-07-04: `dist > 0`; historical hot-only denominator here: 333 rows / 53 dates / 47 cities.
- `dist<=0` would have blocked 143 / 476 rows (30.0%): `dist<0` 131, `dist=0` 12; remaining `dist>0` 333.
- Fee model: official Weather taker `shares * 0.05 * price * (1-price)`; maker fee baseline zero.

## Execution / Sizing On Hot-Only

| sizing | entry_profile | exit_policy | rows | dates | win_rate | avg_entry | avg_cost | stop_hit_rate | roi | roi_ci_low | roi_ci_high | losing_days | le_minus50pct_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fixed_cash_0p80 | maker_no_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.80 | 0.0% | +38.5% | +3.9% | +76.9% | 22 | 16 | $-8.80 |
| fixed_cash_0p80 | taker_weather_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.84 | 0.0% | +32.6% | -0.5% | +69.3% | 24 | 16 | $-9.19 |
| fixed_cash_0p80 | taker_plus1c_weather_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.93 | 0.0% | +19.4% | -10.4% | +52.5% | 27 | 16 | $-10.13 |
| fixed_cash_0p80 | taker_weather_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.84 | 39.3% | +33.0% | +5.1% | +63.0% | 22 | 17 | $-8.58 |
| fixed_8_shares | taker_weather_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.87 | 0.0% | +38.2% | +7.4% | +72.1% | 19 | 16 | $-10.39 |
| fixed_8_shares | taker_weather_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.87 | 39.3% | +40.3% | +12.6% | +70.2% | 18 | 17 | $-10.09 |
| price_tier_6_8_10_shares | taker_weather_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.89 | 0.0% | +41.8% | +10.7% | +76.3% | 19 | 16 | $-11.04 |
| quality_price_tier_5_8_12_shares | taker_weather_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.88 | 0.0% | +43.3% | +10.4% | +80.5% | 20 | 16 | $-11.45 |
| fixed_8_shares | maker_no_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.83 | 0.0% | +44.3% | +12.1% | +79.7% | 19 | 16 | $-9.96 |
| fixed_8_shares | maker_no_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.83 | 39.3% | +46.5% | +17.5% | +77.8% | 18 | 17 | $-9.66 |
| fixed_8_shares | taker_plus1c_weather_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.95 | 0.0% | +26.1% | -2.0% | +57.1% | 21 | 16 | $-11.31 |
| fixed_8_shares | taker_plus1c_weather_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.95 | 39.3% | +28.1% | +2.7% | +55.3% | 18 | 17 | $-11.00 |
| fixed_cash_0p80 | maker_no_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.80 | 39.3% | +39.0% | +9.9% | +70.3% | 20 | 17 | $-8.19 |
| fixed_cash_0p80 | taker_plus1c_weather_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.93 | 39.3% | +19.7% | -5.4% | +46.7% | 22 | 17 | $-9.62 |
| price_tier_6_8_10_shares | maker_no_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.85 | 0.0% | +48.0% | +15.5% | +84.0% | 19 | 16 | $-10.58 |
| price_tier_6_8_10_shares | maker_no_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.85 | 39.3% | +51.0% | +20.5% | +84.4% | 19 | 17 | $-10.35 |
| price_tier_6_8_10_shares | taker_plus1c_weather_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.96 | 0.0% | +30.2% | +1.6% | +62.1% | 20 | 16 | $-11.95 |
| price_tier_6_8_10_shares | taker_plus1c_weather_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.96 | 39.3% | +32.8% | +6.0% | +62.4% | 19 | 17 | $-11.72 |
| price_tier_6_8_10_shares | taker_weather_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.89 | 39.3% | +44.6% | +15.4% | +76.7% | 19 | 17 | $-10.81 |
| quality_price_tier_5_8_12_shares | maker_no_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.85 | 0.0% | +49.6% | +15.2% | +88.4% | 20 | 16 | $-10.97 |
| quality_price_tier_5_8_12_shares | maker_no_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.85 | 39.3% | +52.5% | +19.9% | +88.3% | 19 | 17 | $-10.75 |
| quality_price_tier_5_8_12_shares | taker_plus1c_weather_fee | hold | 333 | 53 | 15.0% | 10.4% | $+0.96 | 0.0% | +31.8% | +1.5% | +66.0% | 21 | 17 | $-12.37 |
| quality_price_tier_5_8_12_shares | taker_plus1c_weather_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.96 | 39.3% | +34.4% | +5.7% | +66.1% | 20 | 17 | $-12.14 |
| quality_price_tier_5_8_12_shares | taker_weather_fee | strict_dead_or_late_dust | 333 | 53 | 15.0% | 10.4% | $+0.88 | 39.3% | +46.1% | +14.9% | +80.4% | 19 | 17 | $-11.22 |

Interpretation:

1. Fixed cash `$0.80` is still a hidden bet that 5c tickets deserve much larger max payout than 15c tickets.
2. Fixed 8 shares and price-tier sizing are more coherent for this sleeve because they make the lottery payout more comparable across prices.
3. +1c taker stress matters; this is why maker-first telemetry is still central, even though the backtest reports taker profiles.
4. Strict stop is not a live rule yet. It is useful as telemetry because it can reduce daily drawdown, but it does not dominate hold robustly enough.

## Sizing First-Principles Experiment

For a binary YES ticket, one share has expected PnL `P(win) - entry - fee_per_share`.  Because this sleeve does not yet have a trusted per-row calibrated `P(win)`, the first sizing question is not Kelly sizing; it is exposure geometry.

- Fixed cash `$0.80` means `shares = 0.80 / entry`: a 5c ticket gets 16 shares, a 20c ticket gets 4 shares.  This implicitly says cheaper tickets deserve much larger max payout.
- Fixed shares means every signal gets the same max payout; cost naturally rises with entry price.
- Price-tier sizing is the mild middle ground used here: 6 shares for 5-8c, 8 shares for 8-14c, 10 shares for 14-20c.  It only uses entry price, not city/date fitting.

Full-window attribution by price bucket, same 333 hot-only rows, taker fee, hold-to-settlement:

| sizing | price_band | rows | win_rate | avg_entry | avg_shares | cost_pct | shares_pct | wins_pct | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fixed_cash_0p80 | 5-8c | 130 | 9.2% | 6.5% | 12.5 | 39.1% | 54.6% | 24.0% | +32.9% | -32.6% | +108.1% |
| fixed_cash_0p80 | 8-14c | 138 | 12.3% | 10.9% | 7.5 | 41.4% | 34.9% | 34.0% | +7.9% | -31.6% | +52.3% |
| fixed_cash_0p80 | 14-20c | 65 | 32.3% | 17.0% | 4.8 | 19.5% | 10.4% | 42.0% | +84.6% | +18.2% | +155.3% |
| fixed_8_shares | 5-8c | 130 | 9.2% | 6.5% | 8.0 | 24.6% | 39.0% | 24.0% | +34.8% | -31.2% | +110.7% |
| fixed_8_shares | 8-14c | 138 | 12.3% | 10.9% | 8.0 | 43.6% | 41.4% | 34.0% | +7.8% | -31.8% | +53.5% |
| fixed_8_shares | 14-20c | 65 | 32.3% | 17.0% | 8.0 | 31.8% | 19.5% | 42.0% | +82.3% | +17.2% | +152.4% |
| price_tier_6_8_10_shares | 5-8c | 130 | 9.2% | 6.5% | 6.0 | 18.1% | 30.8% | 24.0% | +34.8% | -31.2% | +110.7% |
| price_tier_6_8_10_shares | 8-14c | 138 | 12.3% | 10.9% | 8.0 | 42.8% | 43.6% | 34.0% | +7.8% | -31.8% | +53.5% |
| price_tier_6_8_10_shares | 14-20c | 65 | 32.3% | 17.0% | 10.0 | 39.1% | 25.7% | 42.0% | +82.3% | +17.2% | +152.4% |

Window stability for the three non-quality sizing rules:

| period | sizing | rows | dates | win_rate | avg_entry | avg_cost | roi | roi_ci_low | roi_ci_high | losing_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | fixed_cash_0p80 | 333 | 53 | 15.0% | 10.4% | $+0.84 | +32.6% | -0.5% | +69.3% | 24 | $-9.19 |
| full | fixed_8_shares | 333 | 53 | 15.0% | 10.4% | $+0.87 | +38.2% | +7.4% | +72.1% | 19 | $-10.39 |
| full | price_tier_6_8_10_shares | 333 | 53 | 15.0% | 10.4% | $+0.89 | +41.8% | +10.7% | +76.3% | 19 | $-11.04 |
| recent_ge_2026_06_21 | fixed_cash_0p80 | 58 | 9 | 17.2% | 9.9% | $+0.84 | +49.7% | -16.3% | +149.4% | 4 | $-4.18 |
| recent_ge_2026_06_21 | fixed_8_shares | 58 | 9 | 17.2% | 9.9% | $+0.83 | +66.8% | +18.0% | +134.4% | 1 | $-4.20 |
| recent_ge_2026_06_21 | price_tier_6_8_10_shares | 58 | 9 | 17.2% | 9.9% | $+0.82 | +76.7% | +31.7% | +133.9% | 1 | $-4.26 |
| train_le_2026_06_20 | fixed_cash_0p80 | 275 | 44 | 14.5% | 10.5% | $+0.84 | +29.0% | -7.1% | +70.3% | 20 | $-9.19 |
| train_le_2026_06_20 | fixed_8_shares | 275 | 44 | 14.5% | 10.5% | $+0.88 | +32.5% | -1.5% | +72.7% | 18 | $-10.39 |
| train_le_2026_06_20 | price_tier_6_8_10_shares | 275 | 44 | 14.5% | 10.5% | $+0.90 | +35.1% | -0.1% | +76.4% | 18 | $-11.04 |

Read: the strongest price bucket in this historical sleeve is not the cheapest bucket; 14-20c has the highest realized hit rate.  That is why fixed cash is not the natural default.  But price-tier still remains shadow-only because the recent window is small and the rule has not passed a fresh forward clock.

## Mechanism Slices

Main execution profile for slices: `fixed_8_shares + taker_weather_fee + hold`.

| slice_type | label | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | losing_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adj_dist_band | -0.5..0 | 116 | 48 | 12.9% | 10.9% | +13.5% | -36.6% | +66.4% | 34 | $-5.37 |
| adj_dist_band | 0..0.5 | 128 | 49 | 17.2% | 11.3% | +45.5% | -13.3% | +113.9% | 31 | $-6.39 |
| adj_dist_band | 0.5..1 | 53 | 33 | 13.2% | 8.4% | +50.1% | -42.5% | +166.0% | 26 | $-3.01 |
| adj_dist_band | <-0.5 | 30 | 23 | 13.3% | 8.7% | +46.3% | -67.9% | +216.8% | 19 | $-3.08 |
| adj_dist_band | >1 | 6 | 6 | 33.3% | 7.2% | +343.0% | -100.0% | +900.3% | 4 | $-0.88 |
| book_state_v1 | feasible | 212 | 41 | 11.8% | 9.9% | +13.8% | -18.4% | +50.2% | 20 | $-8.43 |
| book_state_v1 | missing | 62 | 17 | 22.6% | 10.7% | +101.6% | +25.8% | +177.8% | 8 | $-2.92 |
| book_state_v1 | thin_wide | 59 | 28 | 18.6% | 11.8% | +51.3% | -19.0% | +127.0% | 18 | $-4.48 |
| price_band | 14-20c | 65 | 38 | 32.3% | 17.0% | +82.3% | +17.2% | +152.4% | 20 | $-5.80 |
| price_band | 5-8c | 130 | 49 | 9.2% | 6.5% | +34.8% | -31.2% | +110.7% | 37 | $-4.49 |
| price_band | 8-14c | 138 | 48 | 12.3% | 10.9% | +7.8% | -31.8% | +53.5% | 32 | $-4.55 |
| raw_dist_band | 0..0.25 | 49 | 33 | 12.2% | 10.7% | +9.3% | -65.6% | +104.2% | 27 | $-4.09 |
| raw_dist_band | 0.25..0.5 | 64 | 31 | 15.6% | 10.6% | +41.2% | -22.9% | +107.3% | 21 | $-3.42 |
| raw_dist_band | 0.5..1 | 111 | 47 | 15.3% | 10.7% | +37.0% | -14.6% | +93.0% | 31 | $-5.41 |
| raw_dist_band | >1 | 109 | 46 | 15.6% | 9.8% | +51.7% | -13.4% | +119.6% | 32 | $-6.34 |

Read this as hypothesis quality, not a selector menu.  `book_state_v1` remains the key uncertainty: if future live rows prove thin/wide books are actually fillable near the decision ask, the sleeve has a plausible attention/stale-attention alpha; if not, historical edge collapses toward the feasible-book baseline.

## Sibling Expression Replay

Single-leg expressions use 8 shares and official Weather taker fee.  Basket uses 4 shares selected + 4 shares next hotter.

| label | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | losing_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| higher_plus_yes | 246 | 52 | 2.8% | 4.1% | -32.4% | -64.2% | +0.1% | 47 | $-6.63 |
| next_hotter_yes | 307 | 53 | 17.3% | 17.0% | -1.8% | -24.3% | +20.1% | 31 | $-15.84 |
| selected_plus_next_basket | 307 | 53 | 31.9% | 14.2% | +12.4% | -1.2% | +25.7% | 15 | $-7.27 |
| selected_yes | 333 | 53 | 15.0% | 10.4% | +38.2% | +7.4% | +72.1% | 19 | $-10.39 |
| two_hotter_yes | 290 | 53 | 17.2% | 18.0% | -7.3% | -29.0% | +13.9% | 31 | $-16.63 |

Same-denominator checks:

| paired_alt | label | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| next_hotter_yes | selected_yes | 307 | 53 | 14.7% | 10.4% | +35.5% | +4.9% | +68.9% |
| next_hotter_yes | next_hotter_yes | 307 | 53 | 17.3% | 17.0% | -1.8% | -24.3% | +20.1% |
| selected_plus_next_basket | selected_yes | 307 | 53 | 14.7% | 10.4% | +35.5% | +4.9% | +68.9% |
| selected_plus_next_basket | selected_plus_next_basket | 307 | 53 | 31.9% | 14.2% | +12.4% | -1.2% | +25.7% |

Conclusion: overshoot is real, but the obvious cure is not.  The next hotter bracket is usually cheaper for a reason, and the basket waters down the convexity.  This argues for **better probability/EV ranking before entry**, not a blanket hotter-bracket switch.

## Fresh Forward Telemetry

Fresh live/shadow rows accumulated in the local HeadA journal since 2026-07-04:

| decision_status | blocker | rows |
| --- | --- | --- |
| blocked | decision_snapshot_too_stale | 94 |
| blocked | dist_lt0_cold_or_inside_forecast_tail_v1 | 30 |
| blocked | duplicate_submitted_signal | 624 |
| blocked | fresh_ask_exceeds_cushion_or_band | 172 |
| blocked | missing_yes_token_id | 114 |
| planned |  | 5 |

This is too fresh to score; keep it as the W3 forward clock.  The key forward questions are fillability by `book_state_v1`, maker fill adverse selection, and whether `adj_dist_p50_br` keeps the same shape after 2026-07-04.

## Decision

No new live change from this research.

- Keep current tiny HeadA probe with `dist<=0` blocked.
- Keep notional small; no size-up.
- Do not add strict stop yet; log it forward.
- Shadow sizing should compare fixed cash `$0.80`, fixed 8 shares, and price-tier 6/8/10 shares.
- Do not switch to next-hotter or basket expression without a future EV model; current sibling replay does not support it.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_heada_refinement_v1.py`
- Base rows: `docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/base_rows.csv`
- Execution replay: `docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/execution_replay.csv`
- Execution summary: `docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/execution_summary.csv`
- Sizing attribution: `docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/sizing_attribution.csv`
- Slice summary: `docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/slice_summary.csv`
- Expression replay: `docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/expression_replay.csv`
- JSON: `docs/analysis/2026-07/2026-07-04-low-price-yes-heada-refinement-v1.json`
