# Reversal Lottery Lab v1

Generated: 2026-07-01

## One-Line Verdict

The lottery direction has one real historical candidate, `edge_ge_20c`, but the current evidence is still `shadow_candidate / not live`: full-window significance is positive, while holdout/recent support is too thin and the post-2026-06-10 cheap-YES rows are mostly unsettled.

robust historical lottery candidate `edge_ge_20c`: 140 rows/24 dates, ROI +62.9%, CI [+11.1%, +115.3%], top3 removed +26.3%. highest point-ROI low-price slice `edge_top_quintile`: 86 rows/23 dates, ROI +83.5%, CI [-11.9%, +177.7%], top3 removed +2.4%. expression-matrix best `matrix_lottery_yes_05_25_open_tail`: 8 rows/7 dates, ROI +64.5%, CI [-100.0%, +463.9%], top3 removed -100.0%.

```text
significance=PASS only for historical fact_low_price_yes edge_ge_20c; FAIL for expression lottery and most other slices
baseline=FAIL/NA (low-price YES uses market ask as break-even; same-row expression reversals remain thin)
forward=FAIL (recent/holdout sample is thin or weak)
conclusion=inconclusive; keep as independent shadow research head, no live change
```

## Data Snapshot

- DB: `runtime/weather.db`, fact built `2026-07-01T08:04:15.729897+00:00`.
- `fact_trades`: 4412 rows, target_date 2026-05-06..2026-06-30.
- `fact_signal_candidates`: 41047 rows, event_date 2026-05-05..2026-07-01.
- Settled low-price BUY_YES denominator: 456 rows, event_date 2026-05-06..2026-06-10.
- Unsettled low-price BUY_YES forward pool: 4301 rows / 55 dates / 49 cities, event_date 2026-05-06..2026-06-30.
- CLOB fill coverage gate pass: `True`. This report does not publish live_real ROI.
- Expression matrix source: `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv`; it currently covers 2026-05-19..2026-06-26, so expression-matrix reversal results do not include 2026-06-27..2026-06-30.

## Low-Price YES Lottery: fact_signal_candidates

Unit: one first city-date candidate per selector, $5 notional at `decision_entry_price`, settled rows only. This answers whether cheap YES itself has a usable base-rate edge.

| label | rows | dates | cities | avg ask | win | hit-ask | ROI | CI low | CI high | loss days | max loss | top-day share | top3 removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| edge_top_quintile | 86 | 23 | 33 | 0.103 | +16.3% | +6.0% | +83.5% | -11.9% | +177.7% | 13 | $-30.00 | +25.9% | +2.4% |
| model_p_ge_25_ask_le_15 | 124 | 15 | 42 | 0.076 | +10.5% | +2.9% | +65.5% | -9.2% | +138.7% | 8 | $-45.00 | +24.3% | +8.3% |
| edge_ge_20c | 140 | 24 | 40 | 0.102 | +16.4% | +6.2% | +62.9% | +11.1% | +115.3% | 11 | $-35.00 | +22.8% | +26.3% |
| late_peak_or_unknown | 306 | 32 | 48 | 0.102 | +9.2% | -1.0% | -6.1% | -33.0% | +32.3% | 18 | $-116.46 | +40.0% | -27.1% |
| all_low_price_yes | 310 | 32 | 48 | 0.102 | +9.0% | -1.1% | -7.3% | -34.2% | +30.3% | 18 | $-116.46 | +40.0% | -28.2% |
| cheap_ask_05_15 | 168 | 20 | 44 | 0.096 | +8.3% | -1.2% | -16.5% | -55.7% | +25.1% | 13 | $-95.00 | +41.7% | -45.3% |
| forecast_in_bracket | 0 | 0 | 0 |  |  |  |  |  |  |  |  |  |  |
| forecast_above_or_in_bracket | 0 | 0 | 0 |  |  |  |  |  |  |  |  |  |  |

## Practical Shadow Prompt

For a separate zero-notional lottery shadow head, the only fixed historical rule worth forwarding now is:

```text
side = BUY_YES
decision_entry_price between 0.01 and 0.25
edge >= 0.20
first city-date candidate only
record token/market/ask/model_p_yes/edge; notional_usd = 0; no live order
```

Historical settled result for this fixed prompt: 140 rows / 24 dates / 40 cities, avg ask 0.102, win 16.4%, ROI +62.9%, date-block CI [+11.1%, +115.3%], top3-removed ROI +26.3%. Holdout after 2026-06-01 is only 9 rows with ROI +7.1% and CI crossing zero, so it is not live-approved.

## Low-Price Holdout/Recent

Holdout starts 2026-06-01. Recent starts 2026-06-08; this low-price fact denominator only has settled low-price rows through 2026-06-10, so recent is a stress slice, not a full forward month.

| label | rows | dates | cities | avg ask | win | hit-ask | ROI | CI low | CI high | loss days | max loss | top-day share | top3 removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| edge_ge_20c | 9 | 5 | 7 | 0.206 | +22.2% | +1.6% | +7.1% | -100.0% | +139.5% | 3 | $-10.00 | +81.1% | -100.0% |
| edge_top_quintile | 5 | 4 | 5 | 0.206 | +20.0% | -0.6% | -2.4% | -100.0% | +109.1% | 3 | $-5.00 | +100.0% | -100.0% |
| all_low_price_yes | 19 | 10 | 13 | 0.204 | +21.1% | +0.7% | -3.5% | -71.9% | +78.1% | 6 | $-25.00 | +36.9% | -65.2% |
| late_peak_or_unknown | 19 | 10 | 13 | 0.204 | +21.1% | +0.7% | -3.5% | -71.9% | +78.1% | 6 | $-25.00 | +36.9% | -65.2% |
| cheap_ask_05_15 | 1 | 1 | 1 | 0.115 | +0.0% | -11.5% | -100.0% |  |  | 1 | $-5.00 |  |  |
| model_p_ge_25_ask_le_15 | 0 | 0 | 0 |  |  |  |  |  |  |  |  |  |  |
| forecast_in_bracket | 0 | 0 | 0 |  |  |  |  |  |  |  |  |  |  |
| forecast_above_or_in_bracket | 0 | 0 | 0 |  |  |  |  |  |  |  |  |  |  |

## Same-Snapshot Reversal / Tail Expressions

Unit: generated expression matrix, first city-date per label. This is the cleaner same-denominator reversal layer but only through 2026-06-26.

| label | rows | dates | cities | avg ask | win | hit-ask | ROI | CI low | CI high | loss days | max loss | top-day share | top3 removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| matrix_lottery_yes_05_25_open_tail | 8 | 7 | 8 | 0.117 | +12.5% | +0.8% | +64.5% | -100.0% | +463.9% | 6 | $-10.00 | +100.0% | -100.0% |
| pullback_current_high_yes_midprice | 12 | 11 | 10 | 0.647 | +75.0% | +10.3% | +21.5% | -15.0% | +55.5% | 3 | $-5.00 | +19.1% | +2.2% |
| high_current_no_reverse_current_yes | 51 | 33 | 21 | 0.403 | +43.1% | +2.9% | +7.4% | -21.8% | +39.1% | 16 | $-15.00 | +16.9% | -7.7% |
| matrix_lottery_yes_05_25_all | 67 | 30 | 25 | 0.116 | +3.0% | -8.6% | -58.1% | -100.0% | +15.7% | 28 | $-25.00 | +53.4% | -100.0% |

## Expression Holdout

| label | rows | dates | cities | avg ask | win | hit-ask | ROI | CI low | CI high | loss days | max loss | top-day share | top3 removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| matrix_lottery_yes_05_25_open_tail | 3 | 3 | 3 | 0.075 | +33.3% | +25.9% | +338.6% | -100.0% | +1215.8% | 2 | $-5.00 | +100.0% |  |
| pullback_current_high_yes_midprice | 11 | 10 | 9 | 0.642 | +72.7% | +8.5% | +19.5% | -19.8% | +58.1% | 3 | $-5.00 | +21.0% | -2.9% |
| high_current_no_reverse_current_yes | 33 | 22 | 17 | 0.402 | +42.4% | +2.3% | +4.8% | -27.3% | +37.2% | 11 | $-7.50 | +14.8% | -13.2% |
| matrix_lottery_yes_05_25_all | 32 | 18 | 17 | 0.107 | +6.2% | -4.5% | -12.2% | -100.0% | +152.3% | 16 | $-15.00 | +53.4% | -100.0% |

## Top Contributors

| family | label | city | rows | dates | ask | win | ROI | PnL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fact_low_price_yes | model_p_ge_25_ask_le_15 | Guangzhou | 1 | 1 | 0.035 | +100.0% | +2798.6% | $+139.93 |
| fact_low_price_yes | edge_ge_20c | Madrid | 4 | 4 | 0.123 | +75.0% | +676.5% | $+135.29 |
| fact_low_price_yes | edge_top_quintile | Singapore | 3 | 3 | 0.106 | +33.3% | +866.2% | $+129.93 |
| fact_low_price_yes | model_p_ge_25_ask_le_15 | Singapore | 3 | 3 | 0.106 | +33.3% | +866.2% | $+129.93 |
| fact_low_price_yes | edge_ge_20c | Singapore | 3 | 3 | 0.106 | +33.3% | +866.2% | $+129.93 |
| fact_low_price_yes | all_low_price_yes | Singapore | 4 | 4 | 0.105 | +25.0% | +624.6% | $+124.93 |
| fact_low_price_yes | late_peak_or_unknown | Singapore | 4 | 4 | 0.105 | +25.0% | +624.6% | $+124.93 |
| fact_low_price_yes | edge_ge_20c | Shanghai | 5 | 5 | 0.081 | +40.0% | +472.0% | $+118.00 |
| fact_low_price_yes | all_low_price_yes | Madrid | 8 | 8 | 0.147 | +37.5% | +288.2% | $+115.29 |
| fact_low_price_yes | late_peak_or_unknown | Madrid | 8 | 8 | 0.147 | +37.5% | +288.2% | $+115.29 |
| fact_low_price_yes | edge_top_quintile | Madrid | 3 | 3 | 0.099 | +66.7% | +764.4% | $+114.65 |
| fact_low_price_yes | model_p_ge_25_ask_le_15 | Shanghai | 6 | 6 | 0.081 | +33.3% | +376.7% | $+113.00 |
| fact_low_price_yes | cheap_ask_05_15 | Shanghai | 7 | 7 | 0.099 | +28.6% | +308.6% | $+108.00 |
| fact_low_price_yes | all_low_price_yes | Tokyo | 12 | 12 | 0.041 | +8.3% | +173.2% | $+103.93 |
| fact_low_price_yes | late_peak_or_unknown | Tokyo | 12 | 12 | 0.041 | +8.3% | +173.2% | $+103.93 |

## Regime Contributions

| family | label | slice | value | rows | dates | ask | win | ROI | PnL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| expression_matrix | matrix_lottery_yes_05_25_open_tail | bias_regime | mixed | 5 | 4 | 0.103 | +20.0% | +163.2% | $+40.79 |
| expression_matrix | matrix_lottery_yes_05_25_open_tail | day_regime | day_marginal_runway | 7 | 6 | 0.123 | +14.3% | +88.0% | $+30.79 |
| expression_matrix | high_current_no_reverse_current_yes | day_regime | day_marginal_runway | 11 | 10 | 0.390 | +54.5% | +39.5% | $+21.72 |
| expression_matrix | high_current_no_reverse_current_yes | day_regime | day_forecast_capped | 20 | 18 | 0.419 | +50.0% | +19.8% | $+19.81 |
| expression_matrix | high_current_no_reverse_current_yes | intraday_state | fresh_high | 22 | 19 | 0.401 | +45.5% | +16.1% | $+17.67 |
| expression_matrix | high_current_no_reverse_current_yes | bias_regime | hot_underforecast | 36 | 26 | 0.409 | +44.4% | +8.4% | $+15.03 |
| expression_matrix | pullback_current_high_yes_midprice | intraday_state | pullback_uncertain | 12 | 11 | 0.647 | +75.0% | +21.5% | $+12.88 |
| expression_matrix | pullback_current_high_yes_midprice | day_regime | day_forecast_busted | 3 | 3 | 0.603 | +100.0% | +67.5% | $+10.12 |
| expression_matrix | pullback_current_high_yes_midprice | day_regime | day_forecast_capped | 5 | 5 | 0.648 | +80.0% | +27.4% | $+6.85 |
| expression_matrix | pullback_current_high_yes_midprice | bias_regime | hot_underforecast | 7 | 7 | 0.636 | +71.4% | +18.2% | $+6.38 |
| expression_matrix | high_current_no_reverse_current_yes | intraday_state | active_warming | 28 | 21 | 0.405 | +42.9% | +4.4% | $+6.17 |
| expression_matrix | high_current_no_reverse_current_yes | bias_regime | mixed | 9 | 8 | 0.394 | +44.4% | +12.5% | $+5.63 |
| expression_matrix | pullback_current_high_yes_midprice | bias_regime | cold_overforecast | 3 | 3 | 0.613 | +66.7% | +19.1% | $+2.86 |
| expression_matrix | pullback_current_high_yes_midprice | day_regime | day_open_runway | 3 | 3 | 0.663 | +66.7% | +6.1% | $+0.91 |
| expression_matrix | matrix_lottery_yes_05_25_all | bias_regime | mixed | 14 | 9 | 0.113 | +7.1% | -6.0% | $-4.21 |
| expression_matrix | high_current_no_reverse_current_yes | bias_regime | balanced_tight | 4 | 4 | 0.383 | +25.0% | -30.6% | $-6.11 |
| expression_matrix | matrix_lottery_yes_05_25_all | day_regime | day_marginal_runway | 15 | 13 | 0.121 | +6.7% | -12.3% | $-9.21 |
| expression_matrix | matrix_lottery_yes_05_25_open_tail | bias_regime | hot_underforecast | 3 | 3 | 0.140 | +0.0% | -100.0% | $-15.00 |
| expression_matrix | matrix_lottery_yes_05_25_all | intraday_state | pullback_uncertain | 4 | 4 | 0.117 | +0.0% | -100.0% | $-20.00 |
| expression_matrix | matrix_lottery_yes_05_25_open_tail | intraday_state | active_warming | 4 | 3 | 0.130 | +0.0% | -100.0% | $-20.00 |

## Interpretation

- `low_price YES` is not obviously dead: high-edge/model-supported cheap YES can print strong point ROI. But it is exactly the kind of distribution where one or two days dominate, so top-day/top3 stress tests matter more than headline ROI.
- `matrix_lottery_yes_05_25_open_tail` is the cleanest tail thesis to keep watching: cheap hotter-bracket YES plus open-runway/hot-noisy state. If it survives more forward dates, it could become a separate lottery family.
- `pullback_current_high_yes_midprice` remains the cleaner reversal expression, but it is not really lottery; it pays mid-price and depends on current bracket holding, with overshoot risk.
- Current decision: no live, no integration into regime-routed NO selector. Keep collecting shadow rows and settle them by expression family.

## Artifacts

- Summary CSV: `docs/analysis/2026-07/generated/reversal_lottery_lab_v1/summary.csv`
- Detail CSV: `docs/analysis/2026-07/generated/reversal_lottery_lab_v1/details.csv`
- Daily CSV: `docs/analysis/2026-07/generated/reversal_lottery_lab_v1/daily.csv`
- City contribution CSV: `docs/analysis/2026-07/generated/reversal_lottery_lab_v1/city_contribution.csv`
- Regime contribution CSV: `docs/analysis/2026-07/generated/reversal_lottery_lab_v1/regime_contribution.csv`
- Script: `scripts/analysis/forecast_quality/research_reversal_lottery_lab_v1.py`
