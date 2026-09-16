# Reversal Lottery Lab v2

Generated: 2026-07-01

## One-Line Verdict

`edge_ge_20c` upgrades from thin shadow to a real `shadow_candidate`: after the `fact_signal_candidates` settlement fallback fix, it has full, holdout, and recent positive ROI, with full-window date bootstrap CI above zero. It is still not live-approved because this is a model/market low-price edge without a same-row alternative-expression baseline and it needs forward shadow execution/settlement.

`edge_ge_20c`: 674 rows / 50 dates / 48 cities, avg ask 0.087, win +11.9%, ROI +50.0%, CI [+15.3%, +87.6%], holdout 418 rows ROI +36.3%, recent 330 rows ROI +36.6%, top3-removed ROI +28.3%.

```text
significance=PASS for historical edge_ge_20c
baseline=PARTIAL (beats market ask break-even, but no same-row expression baseline)
forward=PARTIAL (holdout/recent positive after overlay, but still offline backfill not forward shadow)
conclusion=shadow_candidate; independent lottery shadow head; no live orders
```

## Data Snapshot

- DB: `runtime/weather.db`, fact built `2026-07-01T08:42:49.238944+00:00`.
- CLOB fill coverage gate pass: `True`. This report does not publish live_real ROI.
- Canonical settled low-price BUY_YES after ETL fallback: 4381 rows, 2026-05-06..2026-06-26.
- Source-grain overlay parity check: 4381 rows / 51 dates / 49 cities, 2026-05-06..2026-06-26; extra rows beyond canonical `final_yes` now `0`.
- Expression matrix still covers only 2026-05-19..2026-06-26 from `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv`.

## Practical Shadow Prompt

```text
strategy_family = low_price_yes_lottery_reversal
side = BUY_YES
decision_entry_price between 0.01 and 0.25
edge >= 0.20
one candidate per city-date, earliest PIT decision snapshot
settle via settlement_outcomes city/date/bracket fallback
zero notional; no live order
```

## Low-Price YES With Source-Grain Settlement

| label | rows | dates | cities | avg ask | win | ROI | CI low | CI high | H rows | H ROI | R rows | R ROI | top3 removed | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| model_p_ge_25_ask_le_15 | 665 | 50 | 49 | 0.076 | +10.4% | +57.0% | +15.2% | +100.8% | 408 | +47.9% | 312 | +51.1% | +33.7% | 24 | $-95.00 |
| edge_top_quintile | 742 | 51 | 49 | 0.087 | +11.7% | +54.8% | +13.4% | +102.5% | 466 | +52.2% | 366 | +55.5% | +24.8% | 21 | $-90.00 |
| edge_ge_20c | 674 | 50 | 48 | 0.087 | +11.9% | +50.0% | +15.3% | +87.6% | 418 | +36.3% | 330 | +36.6% | +28.3% | 22 | $-80.00 |
| cheap_ask_05_15 | 1278 | 51 | 49 | 0.089 | +8.6% | -5.2% | -21.8% | +11.7% | 790 | -1.5% | 598 | -0.6% | -14.4% | 29 | $-155.00 |
| late_peak_or_unknown | 1898 | 51 | 49 | 0.045 | +3.6% | -20.5% | -45.7% | +8.7% | 1161 | -29.2% | 863 | -27.6% | -39.1% | 34 | $-235.00 |
| all_low_price_yes | 1901 | 51 | 49 | 0.045 | +3.6% | -20.6% | -45.8% | +8.5% | 1161 | -29.2% | 863 | -27.6% | -39.2% | 34 | $-235.00 |
| forecast_above_or_in_bracket | 320 | 7 | 47 | 0.044 | +3.1% | -61.7% | -84.6% | -38.6% | 320 | -61.7% | 320 | -61.7% | -83.1% | 7 | $-235.00 |
| forecast_in_bracket | 33 | 7 | 15 | 0.125 | +6.1% | -69.3% | -100.0% | -29.3% | 33 | -69.3% | 33 | -69.3% | -100.0% | 6 | $-35.00 |

## Same-Snapshot Expression Sanity

This remains a separate denominator. The hotter-tail expression is still too thin; the stronger result is the fact-level low-price lottery edge, not a current-runner expression switch.

| label | rows | dates | cities | avg ask | win | ROI | CI low | CI high | H rows | H ROI | R rows | R ROI | top3 removed | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| matrix_lottery_yes_05_25_open_tail | 8 | 7 | 8 | 0.117 | +12.5% | +64.5% | -100.0% | +463.9% | 3 | +338.6% | 2 | +557.9% | -100.0% | 6 | $-10.00 |
| pullback_current_high_yes_midprice | 12 | 11 | 10 | 0.647 | +75.0% | +21.5% | -15.0% | +55.5% | 11 | +19.5% | 8 | +22.1% | +2.2% | 3 | $-5.00 |
| high_current_no_reverse_current_yes | 51 | 33 | 21 | 0.403 | +43.1% | +7.4% | -21.8% | +39.1% | 33 | +4.8% | 24 | +13.2% | -7.7% | 16 | $-15.00 |
| matrix_lottery_yes_05_25_all | 67 | 30 | 25 | 0.116 | +3.0% | -58.1% | -100.0% | +15.7% | 32 | -12.2% | 18 | +56.0% | -100.0% | 28 | $-25.00 |

## Top edge_ge_20c Cities

| city | rows | dates | ask | win | ROI | PnL |
| --- | --- | --- | --- | --- | --- | --- |
| Paris | 18 | 18 | 0.047 | +16.7% | +376.0% | $+338.43 |
| Taipei | 5 | 5 | 0.036 | +20.0% | +1150.0% | $+287.50 |
| Singapore | 11 | 11 | 0.100 | +27.3% | +478.7% | $+263.28 |
| CapeTown | 8 | 8 | 0.072 | +12.5% | +635.3% | $+254.12 |
| KualaLumpur | 7 | 7 | 0.067 | +42.9% | +576.8% | $+201.86 |
| Atlanta | 25 | 25 | 0.120 | +16.0% | +139.4% | $+174.30 |
| Madrid | 10 | 10 | 0.122 | +40.0% | +284.7% | $+142.33 |
| Chongqing | 10 | 10 | 0.061 | +20.0% | +278.5% | $+139.24 |
| Dallas | 11 | 11 | 0.067 | +18.2% | +234.2% | $+128.82 |
| Shanghai | 14 | 14 | 0.073 | +21.4% | +179.5% | $+125.63 |
| LA | 21 | 21 | 0.120 | +14.3% | +88.1% | $+92.52 |
| Manila | 18 | 18 | 0.067 | +16.7% | +102.2% | $+91.99 |
| SaoPaulo | 5 | 5 | 0.079 | +40.0% | +363.0% | $+90.76 |
| Amsterdam | 20 | 20 | 0.145 | +30.0% | +82.5% | $+82.52 |
| Lucknow | 13 | 13 | 0.110 | +15.4% | +101.5% | $+65.95 |
| Guangzhou | 4 | 4 | 0.038 | +25.0% | +316.7% | $+63.33 |
| Wellington | 4 | 4 | 0.075 | +25.0% | +284.6% | $+56.92 |
| London | 24 | 24 | 0.072 | +12.5% | +42.3% | $+50.74 |
| Warsaw | 15 | 15 | 0.102 | +20.0% | +48.5% | $+36.35 |
| Busan | 10 | 10 | 0.059 | +20.0% | +67.6% | $+33.78 |

## edge_ge_20c Regime Contributions

_No rows._

## Interpretation

- The v1 weak-forward read was mostly a settlement coverage artifact in `fact_signal_candidates`, not a failure of the lottery idea. The local ETL now falls back to `settlement_outcomes` by city/date/bracket.
- `edge>=0.20` is deliberately simple and was already fixed before the source-grain overlay; this reduces threshold-mining risk.
- The payoff is still very volatile: many days are -100%, and positive expectancy comes from occasional 5x-20x winners. This is appropriate only as a capped lottery shadow sleeve.
- Next research step is forward shadow extraction from current low-price candidates, plus a data-layer fix so `fact_signal_candidates` can use `settlement_outcomes` fallback natively.

## Artifacts

- Summary CSV: `docs/analysis/2026-07/generated/reversal_lottery_lab_v2/summary.csv`
- Detail CSV: `docs/analysis/2026-07/generated/reversal_lottery_lab_v2/details.csv`
- Daily CSV: `docs/analysis/2026-07/generated/reversal_lottery_lab_v2/daily.csv`
- Script: `scripts/analysis/forecast_quality/research_reversal_lottery_lab_v2.py`
