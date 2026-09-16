# HeadA Clean EV Expression Selector v1

Generated: `2026-07-06T04:56:38Z`

Scope: HeadA `forecast_tail_low_price_yes`, expression layer only.  This is the cleaner mechanism formulation requested: estimate `P(expression wins)`, subtract ask and official fee, then choose the best expression or no trade.  It does **not** change live.

## Clean Mechanism

Instead of:

```text
low-price YES + more filters + maybe a hotter bracket patch
```

the cleaner expression is:

```text
For each city-date snapshot:
  for each sibling YES expression e:
      p_e = P(e wins | forecast distance, as-of station bias, forecast uncertainty, regime context)
      net_ev_e = p_e - ask_e - fee_per_share(ask_e)

  trade argmax(net_ev_e) only if net_ev_e > 0
```

The primary universe is still the HeadA hot-tail universe because that is the strategy family definition, not a patch: early low-price YES tickets above the forecast max.

## Verdict

The clean EV expression selector is the right architecture, but this first version is **not ready to replace the live selector**.

```text
significance=FAIL/PARTIAL
baseline=FAIL
forward=FAIL
conclusion=inconclusive_clean_architecture; keep HeadA live unchanged
```

Why: the EV models can rank expression candidates and sometimes choose hotter legs, but same-row A/B does not beat simply holding the original selected YES.  The market price is already carrying a lot of information; the current selected expression remains the best historical expression on this denominator.

Current selected baseline ROI in this run: +41.8%.

## Data Snapshot

- Denominator: HeadA hot-tail expression rows generated from `low_price_yes_heada_refinement_v1`.
- Base rows: 476 current HeadA candidates; expression candidates scored: 1176 single-leg sibling rows.
- Date range: 2026-05-06..2026-06-30, 53 dates, 48 cities.
- Cost model: official Weather taker fee `shares * 0.05 * price * (1-price)`.
- Expression price cap for primary selectors: `ask <= 0.20`.
- No city identity is used in the models.

## Model Quality

| model | period | rows | dates | wins | win_rate | avg_p | brier | logloss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| attention_blend | full | 1176 | 53 | 160 | 13.6% | 14.5% | 0.094 | 0.304 |
| attention_blend | recent_ge_2026_06_21 | 206 | 9 | 27 | 13.1% | 12.8% | 0.096 | 0.314 |
| attention_blend | train_le_2026_06_20 | 970 | 44 | 133 | 13.7% | 14.9% | 0.094 | 0.302 |
| market_blend | full | 1176 | 53 | 160 | 13.6% | 14.5% | 0.095 | 0.305 |
| market_blend | recent_ge_2026_06_21 | 206 | 9 | 27 | 13.1% | 12.9% | 0.095 | 0.310 |
| market_blend | train_le_2026_06_20 | 970 | 44 | 133 | 13.7% | 14.9% | 0.095 | 0.304 |
| physics | full | 1176 | 53 | 160 | 13.6% | 14.5% | 0.111 | 0.366 |
| physics | recent_ge_2026_06_21 | 206 | 9 | 27 | 13.1% | 13.6% | 0.112 | 0.370 |
| physics | train_le_2026_06_20 | 970 | 44 | 133 | 13.7% | 14.7% | 0.111 | 0.365 |

Read: `physics` is the purest model but underpowered.  `market_blend` improves probability calibration because ask is informative.  `attention_blend` can look better historically because book state captures attention/staleness, but that is exactly the part requiring fresh fillability evidence.

## Selector Performance

All selectors use price-tier shares and taker-fee hold-to-settlement.  Positive selectors use the natural rule `net_ev > 0`.  Same-count selectors set one train threshold so train trade count matches the current selected baseline; they are diagnostic, not live rules.

| label | rows | dates | cities | win_rate | avg_entry | avg_net_ev | roi | roi_ci_low | roi_ci_high | top5_removed_roi | losing_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_selected_yes_baseline | 333 | 53.000 | 47.000 | 15.0% | 10.4% |  | +41.8% | +10.7% | +76.3% | +28.3% | 19.000 | $-11.04 |
| physics_ev_positive_cap20 | 315 | 53.000 | 45.000 | 7.0% | 4.8% | +4.6% | +45.2% | -11.1% | +111.0% | +11.8% | 35.000 | $-7.61 |
| physics_same_count_cap20 | 333 | 53.000 | 47.000 | 11.4% | 7.3% | +2.4% | +55.1% | +13.3% | +101.0% | +36.8% | 24.000 | $-11.96 |
| market_blend_ev_positive_cap20 | 318 | 53.000 | 45.000 | 4.7% | 3.4% | +2.4% | +32.1% | -29.7% | +99.7% | -13.4% | 41.000 | $-3.94 |
| market_blend_same_count_cap20 | 333 | 53.000 | 47.000 | 6.9% | 5.3% | +0.8% | +22.1% | -27.1% | +76.1% | -5.3% | 37.000 | $-8.36 |
| attention_blend_ev_positive_cap20 | 318 | 53.000 | 45.000 | 3.8% | 3.6% | +2.3% | -2.1% | -58.8% | +64.7% | -45.8% | 44.000 | $-6.07 |
| attention_blend_same_count_cap20 | 333 | 53.000 | 47.000 | 6.9% | 5.6% | +0.8% | +16.7% | -28.2% | +65.5% | -9.1% | 36.000 | $-8.36 |

## Train vs Recent

| period | label | rows | dates | win_rate | avg_entry | avg_net_ev | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recent_ge_2026_06_21 | current_selected_yes_baseline | 58 | 9.000 | 17.2% | 9.9% |  | +76.7% | +31.7% | +133.9% | -8.4% |
| train_le_2026_06_20 | current_selected_yes_baseline | 275 | 44.000 | 14.5% | 10.5% |  | +35.1% | -0.1% | +76.4% | +18.7% |
| recent_ge_2026_06_21 | physics_ev_positive_cap20 | 55 | 9.000 | 1.8% | 3.0% | +4.1% | -36.0% | -100.0% | +140.7% | -100.0% |
| train_le_2026_06_20 | physics_ev_positive_cap20 | 260 | 44.000 | 8.1% | 5.1% | +4.7% | +54.4% | -4.7% | +121.4% | +17.7% |
| recent_ge_2026_06_21 | physics_same_count_cap20 | 58 | 9.000 | 12.1% | 5.9% | +1.8% | +118.4% | +54.9% | +206.5% | -24.1% |
| train_le_2026_06_20 | physics_same_count_cap20 | 275 | 44.000 | 11.3% | 7.6% | +2.6% | +45.1% | -0.2% | +96.6% | +23.2% |
| recent_ge_2026_06_21 | market_blend_ev_positive_cap20 | 55 | 9.000 | 3.6% | 2.8% | +1.7% | +24.5% | -100.0% | +141.1% | -100.0% |
| train_le_2026_06_20 | market_blend_ev_positive_cap20 | 263 | 44.000 | 4.9% | 3.5% | +2.5% | +33.2% | -35.5% | +109.6% | -19.4% |
| recent_ge_2026_06_21 | market_blend_same_count_cap20 | 58 | 9.000 | 12.1% | 6.3% | +0.1% | +99.7% | +8.5% | +189.1% | -38.9% |
| train_le_2026_06_20 | market_blend_same_count_cap20 | 275 | 44.000 | 5.8% | 5.1% | +1.0% | +3.0% | -52.3% | +66.3% | -31.6% |
| recent_ge_2026_06_21 | attention_blend_ev_positive_cap20 | 54 | 9.000 | 0.0% | 1.9% | +1.7% | -100.0% | -100.0% | -100.0% | -100.0% |
| train_le_2026_06_20 | attention_blend_ev_positive_cap20 | 264 | 44.000 | 4.5% | 4.0% | +2.5% | +5.8% | -53.8% | +74.3% | -41.1% |
| recent_ge_2026_06_21 | attention_blend_same_count_cap20 | 58 | 9.000 | 10.3% | 6.5% | -0.1% | +76.1% | +10.8% | +142.5% | -65.6% |
| train_le_2026_06_20 | attention_blend_same_count_cap20 | 275 | 44.000 | 6.2% | 5.5% | +1.0% | +2.3% | -47.9% | +61.4% | -30.0% |

Forward/recent does not rescue the selector.  This is the point where the little EV-engine says, quite politely, “nice architecture, not enough edge yet.”

## Expression Choice Mix

| selector | expression | rows | dates | win_rate | avg_entry | roi |
| --- | --- | --- | --- | --- | --- | --- |
| current_selected_yes_baseline | selected_yes | 333 | 53 | 15.0% | 10.4% | +41.8% |
| physics_ev_positive_cap20 | higher_plus_yes | 169 | 48 | 0.6% | 0.7% | -14.8% |
| physics_ev_positive_cap20 | next_hotter_yes | 16 | 15 | 6.2% | 7.9% | -14.9% |
| physics_ev_positive_cap20 | selected_yes | 129 | 42 | 14.7% | 9.6% | +48.9% |
| physics_ev_positive_cap20 | two_hotter_yes | 1 | 1 | 100.0% | 16.0% | +499.8% |
| physics_same_count_cap20 | higher_plus_yes | 142 | 48 | 0.7% | 1.4% | -60.4% |
| physics_same_count_cap20 | next_hotter_yes | 3 | 3 | 33.3% | 19.5% | +64.3% |
| physics_same_count_cap20 | selected_yes | 187 | 51 | 18.7% | 11.5% | +60.1% |
| physics_same_count_cap20 | two_hotter_yes | 1 | 1 | 100.0% | 16.0% | +499.8% |
| market_blend_ev_positive_cap20 | higher_plus_yes | 192 | 51 | 0.0% | 0.4% | -100.0% |
| market_blend_ev_positive_cap20 | next_hotter_yes | 19 | 16 | 15.8% | 6.0% | +182.9% |
| market_blend_ev_positive_cap20 | selected_yes | 84 | 40 | 14.3% | 10.3% | +26.7% |
| market_blend_ev_positive_cap20 | two_hotter_yes | 23 | 18 | 0.0% | 0.9% | -100.0% |
| market_blend_same_count_cap20 | higher_plus_yes | 182 | 51 | 0.5% | 1.3% | -66.4% |
| market_blend_same_count_cap20 | next_hotter_yes | 20 | 18 | 20.0% | 9.7% | +88.1% |
| market_blend_same_count_cap20 | selected_yes | 115 | 44 | 14.8% | 11.3% | +20.8% |
| market_blend_same_count_cap20 | two_hotter_yes | 16 | 15 | 6.2% | 2.3% | +207.8% |
| attention_blend_ev_positive_cap20 | higher_plus_yes | 189 | 51 | 0.0% | 0.4% | -100.0% |
| attention_blend_ev_positive_cap20 | next_hotter_yes | 18 | 16 | 11.1% | 6.3% | +69.8% |
| attention_blend_ev_positive_cap20 | selected_yes | 87 | 38 | 11.5% | 10.8% | -2.3% |
| attention_blend_ev_positive_cap20 | two_hotter_yes | 24 | 18 | 0.0% | 1.0% | -100.0% |
| attention_blend_same_count_cap20 | higher_plus_yes | 177 | 51 | 0.6% | 1.4% | -66.9% |
| attention_blend_same_count_cap20 | next_hotter_yes | 13 | 12 | 30.8% | 11.7% | +125.2% |
| attention_blend_same_count_cap20 | selected_yes | 125 | 43 | 13.6% | 11.5% | +11.9% |
| attention_blend_same_count_cap20 | two_hotter_yes | 18 | 16 | 5.6% | 2.4% | +177.9% |

The selector does choose hotter legs sometimes, but those switches are not reliably profitable.  This confirms the prior mechanism finding: overshoot exists, but “buy hotter” is not the answer unless the probability lift beats the extra ask.

## Same-Row A/B Against Selected YES

This is the important table.  For every row where the EV selector trades, compare its chosen expression against buying the original selected YES on exactly the same row.

| selector_name | label | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | delta_roi | delta_ci_low | delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| physics_ev_positive_cap20 | same_rows_selected_yes | 315 | 53 | 15.2% | 10.2% | +47.4% | +14.1% | +84.0% |  |  |  |
| physics_ev_positive_cap20 | selector | 315 | 53 | 7.0% | 4.8% | +45.2% | -11.1% | +111.0% |  |  |  |
| physics_ev_positive_cap20 | selector_minus_same_rows_selected_yes | 315 | 53 |  |  |  |  |  | -2.2% | -50.7% | +50.3% |
| physics_same_count_cap20 | same_rows_selected_yes | 333 | 53 | 15.0% | 10.4% | +41.8% | +10.7% | +76.3% |  |  |  |
| physics_same_count_cap20 | selector | 333 | 53 | 11.4% | 7.3% | +55.1% | +13.3% | +101.0% |  |  |  |
| physics_same_count_cap20 | selector_minus_same_rows_selected_yes | 333 | 53 |  |  |  |  |  | +13.3% | -8.1% | +37.2% |
| market_blend_ev_positive_cap20 | same_rows_selected_yes | 318 | 53 | 15.4% | 10.3% | +46.9% | +13.6% | +84.3% |  |  |  |
| market_blend_ev_positive_cap20 | selector | 318 | 53 | 4.7% | 3.4% | +32.1% | -29.7% | +99.7% |  |  |  |
| market_blend_ev_positive_cap20 | selector_minus_same_rows_selected_yes | 318 | 53 |  |  |  |  |  | -14.8% | -78.3% | +47.5% |
| market_blend_same_count_cap20 | same_rows_selected_yes | 333 | 53 | 15.0% | 10.4% | +41.8% | +10.7% | +76.3% |  |  |  |
| market_blend_same_count_cap20 | selector | 333 | 53 | 6.9% | 5.3% | +22.1% | -27.1% | +76.1% |  |  |  |
| market_blend_same_count_cap20 | selector_minus_same_rows_selected_yes | 333 | 53 |  |  |  |  |  | -19.8% | -71.0% | +33.5% |
| attention_blend_ev_positive_cap20 | same_rows_selected_yes | 318 | 53 | 15.4% | 10.3% | +46.4% | +13.3% | +83.7% |  |  |  |
| attention_blend_ev_positive_cap20 | selector | 318 | 53 | 3.8% | 3.6% | -2.1% | -58.8% | +64.7% |  |  |  |
| attention_blend_ev_positive_cap20 | selector_minus_same_rows_selected_yes | 318 | 53 |  |  |  |  |  | -48.5% | -102.5% | +7.3% |
| attention_blend_same_count_cap20 | same_rows_selected_yes | 333 | 53 | 15.0% | 10.4% | +41.8% | +10.7% | +76.3% |  |  |  |
| attention_blend_same_count_cap20 | selector | 333 | 53 | 6.9% | 5.6% | +16.7% | -28.2% | +65.5% |  |  |  |
| attention_blend_same_count_cap20 | selector_minus_same_rows_selected_yes | 333 | 53 |  |  |  |  |  | -25.2% | -71.4% | +21.2% |

Interpretation:

- If a cleaner expression selector were genuinely better, this table should show positive `selector_minus_same_rows_selected_yes`.
- It does not.  The original selected YES remains a stubbornly good expression on the same rows.
- So the next improvement should focus on **whether to trade** and **whether the quote is real/fillable**, not on blanket expression switching.

## What This Means Mechanistically

The clean HeadA strategy should be:

```text
1. Define a hot-tail opportunity above forecast max.
2. Estimate probability for each available YES expression.
3. Select expression by net EV.
4. Require executable quote quality and realistic maker fill model.
5. Size from EV confidence and liquidity, not fixed cash.
```

But after this replay, the best current expression remains:

```text
selected low-price YES, not next-hotter / plus / basket
```

The cleaner optimization is therefore not a new bracket expression today.  It is a cleaner **EV architecture** to shadow forward:

```text
shadow tag:
  p_physics_selected
  p_market_blend_selected
  p_attention_blend_selected
  best_sibling_expression
  best_sibling_net_ev
  same_row_selected_yes_net_ev
  ev_selector_would_switch_expression
```

## Decision

- Do not change HeadA live.
- Keep live expression as selected YES.
- Keep dynamic maker lifecycle and price-tier shares.
- Add this EV expression selector as shadow telemetry, not as a selector.
- Next real blocker remains book-state/fillability: if the selected YES edge lives only in thin/missing books, the EV model must include executable fill probability before it can govern live.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_clean_ev_expression_v1.py`
- Scored expressions: `docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1/expression_scored.csv`
- Selected trades: `docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1/selected_trades.csv`
- Selector summary: `docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1/selector_summary.csv`
- Same-row A/B: `docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1/same_row_ab.csv`
- JSON: `docs/analysis/2026-07/2026-07-05-low-price-yes-clean-ev-expression-v1.json`
