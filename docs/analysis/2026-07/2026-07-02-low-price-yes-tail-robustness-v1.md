# Low-Price YES Tail Robustness v1

Generated: 2026-07-02T14:47:41+00:00

## Verdict

`inconclusive` for a new selector.  This audit makes the shape clearer: the broad v1 sleeve is still the only thing worth running tiny, while the attractive p_cal/city-fixed lines are not yet a mechanism we can promote.

Main read:

- `v1_edge20_baseline_all` is not pretty, but it is the broadest and least pathologically selected denominator.
- `city_diag_ev>=0.5` has very high ROI and even survives simple leave-one-city stress in train/holdout, but it uses raw city identity and has only 8 forward rows; it stays diagnostic-only until fresh forward and leave-region/leave-city retraining tests exist.
- `station_bias_p90_high` is the most interesting mechanism tag: it has better train ROI than v1, but in holdout it underperforms its complement and forward is too winner-dependent.
- No result here justifies changing live.  The correct next move is telemetry plus fresh-forward validation.

Gate summary: `significance=FAIL`, `baseline=PARTIAL`, `forward=FAIL/NA`, `conclusion=inconclusive`.

## Evidence Window

- Input: `docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1/details.csv` from p_cal v1.
- Region map: `docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv`.
- Unit: one selected city-date-bracket BUY_YES decision row, fixed `$1` cost.
- Periods: train `<2026-06-21`, holdout `2026-06-21..2026-06-26`, forward `2026-06-27..2026-06-30`.

## Selector Stress Summary

| strategy | period | rows | dates | cities | regions | win | ask | ROI | CI low | CI high | top5 rm | top10 rm | min leave city | city | min leave region | region |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1_edge20_baseline_all | train_pre_2026_06_21 | 383 | 44 | 48 | 7 | +13.1% | 0.105 | +20.4% | -14.4% | +59.9% | +1.2% | -16.7% | +13.5% | Shanghai | +10.6% | AS |
| v1_edge20_baseline_all | holdout_2026_06_21_26 | 74 | 6 | 32 | 6 | +16.2% | 0.109 | +43.9% | -37.7% | +133.2% | -34.5% | -83.9% | +16.4% | Lucknow | -6.0% | AS |
| v1_edge20_baseline_all | forward_2026_06_27_30 | 19 | 3 | 17 | 5 | +15.8% | 0.100 | +65.9% | -48.1% | +809.1% | -100.0% | -100.0% | -66.4% | Shanghai | -56.0% | AS |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | train_pre_2026_06_21 | 314 | 44 | 46 | 7 | +13.7% | 0.106 | +22.8% | -14.0% | +62.7% | -0.7% | -21.6% | +16.9% | SaoPaulo | +13.1% | AS |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | holdout_2026_06_21_26 | 63 | 6 | 27 | 6 | +14.3% | 0.109 | +32.7% | -48.5% | +125.2% | -61.6% | -100.0% | -1.0% | Lucknow | -24.2% | AS |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | forward_2026_06_27_30 | 15 | 3 | 14 | 4 | +6.7% | 0.092 | +21.2% | -100.0% | +1718.2% | -100.0% | -100.0% | -100.0% | Shanghai | -100.0% | AS |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | train_pre_2026_06_21 | 107 | 39 | 22 | 5 | +15.9% | 0.106 | +38.5% | -26.9% | +115.2% | -22.6% | -59.4% | +21.4% | SaoPaulo | +13.6% | SA |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | holdout_2026_06_21_26 | 18 | 6 | 7 | 4 | +11.1% | 0.104 | +19.4% | -100.0% | +152.5% | -100.0% | -100.0% | -36.5% | Wuhan | -14.5% | SA |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | forward_2026_06_27_30 | 5 | 2 | 5 | 3 | +0.0% | 0.104 | -100.0% |  |  |  |  | -100.0% | Beijing | -100.0% | AS |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | train_pre_2026_06_21 | 257 | 44 | 41 | 7 | +16.3% | 0.105 | +51.7% | +8.5% | +99.6% | +23.4% | -2.4% | +42.5% | Shanghai | +39.8% | AS |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | holdout_2026_06_21_26 | 55 | 6 | 26 | 6 | +16.4% | 0.110 | +52.0% | -41.9% | +164.3% | -55.4% | -100.0% | +12.6% | Lucknow | -6.4% | AS |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | forward_2026_06_27_30 | 14 | 3 | 12 | 4 | +14.3% | 0.096 | +84.4% | -100.0% | +1718.2% | -100.0% | -100.0% | -100.0% | Shanghai | -100.0% | AS |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | 149 | 41 | 29 | 5 | +22.1% | 0.106 | +105.0% | +40.3% | +180.1% | +57.5% | +15.2% | +94.7% | SaoPaulo | +85.7% | AS |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | holdout_2026_06_21_26 | 28 | 6 | 15 | 4 | +21.4% | 0.109 | +87.2% | -16.6% | +219.3% | -76.9% | -100.0% | +46.3% | Amsterdam | +78.7% | SA |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | forward_2026_06_27_30 | 8 | 3 | 7 | 3 | +12.5% | 0.095 | +127.3% | -100.0% | +1718.2% | -100.0% |  | -100.0% | Shanghai | -100.0% | AS |
| station_hot_tail_high | train_pre_2026_06_21 | 130 | 44 | 17 | 6 | +15.4% | 0.109 | +40.6% | -14.3% | +104.1% | -10.7% | -46.7% | +20.7% | Shanghai | +26.3% | AS |
| station_hot_tail_high | holdout_2026_06_21_26 | 23 | 6 | 10 | 2 | +17.4% | 0.095 | +34.4% | -49.5% | +129.5% | -100.0% | -100.0% | -28.4% | Amsterdam | +4.7% | EU |
| station_hot_tail_high | forward_2026_06_27_30 | 8 | 3 | 7 | 3 | +25.0% | 0.094 | +222.7% | -100.0% | +1718.2% | -100.0% |  | -100.0% | Shanghai | -100.0% | AS |
| station_bias_p90_high | train_pre_2026_06_21 | 130 | 42 | 20 | 6 | +15.4% | 0.100 | +65.3% | -2.8% | +140.6% | +9.0% | -40.9% | +47.7% | Shanghai | +46.6% | SA |
| station_bias_p90_high | holdout_2026_06_21_26 | 20 | 6 | 9 | 2 | +10.0% | 0.076 | +27.9% | -100.0% | +122.4% | -100.0% | -100.0% | -29.6% | Manila | -100.0% | AS |
| station_bias_p90_high | forward_2026_06_27_30 | 7 | 3 | 6 | 3 | +28.6% | 0.099 | +268.8% | -100.0% | +1718.2% | -100.0% |  | -100.0% | Shanghai | -100.0% | AS |
| station_bias_p90_high_and_no_city_ev_ge_0 | train_pre_2026_06_21 | 130 | 42 | 20 | 6 | +15.4% | 0.100 | +65.3% | -2.8% | +140.6% | +9.0% | -40.9% | +47.7% | Shanghai | +46.6% | SA |
| station_bias_p90_high_and_no_city_ev_ge_0 | holdout_2026_06_21_26 | 20 | 6 | 9 | 2 | +10.0% | 0.076 | +27.9% | -100.0% | +122.4% | -100.0% | -100.0% | -29.6% | Manila | -100.0% | AS |
| station_bias_p90_high_and_no_city_ev_ge_0 | forward_2026_06_27_30 | 7 | 3 | 6 | 3 | +28.6% | 0.099 | +268.8% | -100.0% | +1718.2% | -100.0% |  | -100.0% | Shanghai | -100.0% | AS |

## Selected vs Complement

This asks whether a selector improves the current v1 denominator, instead of only showing standalone ROI.

| strategy | period | selected | selected ROI | complement | complement ROI | delta |
| --- | --- | --- | --- | --- | --- | --- |
| station_bias_p90_high | forward_2026_06_27_30 | 7 | +268.8% | 12 | -52.4% | +321.2% |
| station_bias_p90_high | holdout_2026_06_21_26 | 20 | +27.9% | 54 | +49.9% | -22.0% |
| station_bias_p90_high | train_pre_2026_06_21 | 130 | +65.3% | 253 | -2.6% | +68.0% |
| station_bias_p90_high_and_no_city_ev_ge_0 | forward_2026_06_27_30 | 7 | +268.8% | 12 | -52.4% | +321.2% |
| station_bias_p90_high_and_no_city_ev_ge_0 | holdout_2026_06_21_26 | 20 | +27.9% | 54 | +49.9% | -22.0% |
| station_bias_p90_high_and_no_city_ev_ge_0 | train_pre_2026_06_21 | 130 | +65.3% | 253 | -2.6% | +68.0% |
| station_hot_tail_high | forward_2026_06_27_30 | 8 | +222.7% | 11 | -48.1% | +270.7% |
| station_hot_tail_high | holdout_2026_06_21_26 | 23 | +34.4% | 51 | +48.2% | -13.9% |
| station_hot_tail_high | train_pre_2026_06_21 | 130 | +40.6% | 253 | +10.1% | +30.6% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | forward_2026_06_27_30 | 14 | +84.4% | 5 | +14.3% | +70.1% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | holdout_2026_06_21_26 | 55 | +52.0% | 19 | +20.6% | +31.4% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.2 | train_pre_2026_06_21 | 257 | +51.7% | 126 | -43.2% | +94.9% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | forward_2026_06_27_30 | 8 | +127.3% | 11 | +21.3% | +105.9% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | holdout_2026_06_21_26 | 28 | +87.2% | 46 | +17.6% | +69.6% |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | 149 | +105.0% | 234 | -33.4% | +138.4% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | forward_2026_06_27_30 | 15 | +21.2% | 4 | +233.7% | -212.5% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | holdout_2026_06_21_26 | 63 | +32.7% | 11 | +108.3% | -75.6% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.2 | train_pre_2026_06_21 | 314 | +22.8% | 69 | +9.9% | +12.8% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | forward_2026_06_27_30 | 5 | -100.0% | 14 | +125.2% | -225.2% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | holdout_2026_06_21_26 | 18 | +19.4% | 56 | +51.8% | -32.4% |
| v1_edge20_v1_edge20_no_city_ev_ge_0.5 | train_pre_2026_06_21 | 107 | +38.5% | 276 | +13.4% | +25.1% |

## City Concentration

| strategy | period | city | rows | dates | win | ask | ROI | PnL | PnL share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Shanghai | 11 | 11 | +27.3% | 0.114 | +255.7% | $+28.13 | 0.359 |
| station_bias_p90_high | train_pre_2026_06_21 | Shanghai | 11 | 11 | +27.3% | 0.114 | +255.7% | $+28.13 | 0.331 |
| station_bias_p90_high | train_pre_2026_06_21 | SaoPaulo | 4 | 4 | +50.0% | 0.087 | +478.8% | $+19.15 | 0.225 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | SaoPaulo | 4 | 4 | +50.0% | 0.087 | +478.8% | $+19.15 | 0.122 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | SaoPaulo | 4 | 4 | +50.0% | 0.087 | +478.8% | $+19.15 | 0.245 |
| station_bias_p90_high | train_pre_2026_06_21 | Guangzhou | 1 | 1 | +100.0% | 0.060 | +1566.7% | $+15.67 | 0.184 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Guangzhou | 1 | 1 | +100.0% | 0.060 | +1566.7% | $+15.67 | 0.200 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Guangzhou | 1 | 1 | +100.0% | 0.060 | +1566.7% | $+15.67 | 0.100 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Moscow | 17 | 17 | +23.5% | 0.114 | +87.1% | $+14.81 | 0.095 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Moscow | 17 | 17 | +23.5% | 0.114 | +87.1% | $+14.81 | 0.189 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Manila | 10 | 10 | +20.0% | 0.085 | +127.9% | $+12.79 | 0.163 |
| station_bias_p90_high | train_pre_2026_06_21 | Manila | 10 | 10 | +20.0% | 0.085 | +127.9% | $+12.79 | 0.151 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Manila | 10 | 10 | +20.0% | 0.085 | +127.9% | $+12.79 | 0.082 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Wellington | 3 | 3 | +33.3% | 0.097 | +412.8% | $+12.38 | 0.079 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Wellington | 3 | 3 | +33.3% | 0.097 | +412.8% | $+12.38 | 0.158 |
| station_bias_p90_high | train_pre_2026_06_21 | Wellington | 3 | 3 | +33.3% | 0.097 | +412.8% | $+12.38 | 0.146 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Madrid | 6 | 6 | +50.0% | 0.133 | +204.2% | $+12.25 | 0.157 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Madrid | 6 | 6 | +50.0% | 0.133 | +204.2% | $+12.25 | 0.078 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | KualaLumpur | 2 | 2 | +100.0% | 0.143 | +602.0% | $+12.04 | 0.154 |
| station_bias_p90_high | train_pre_2026_06_21 | KualaLumpur | 2 | 2 | +100.0% | 0.143 | +602.0% | $+12.04 | 0.142 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | KualaLumpur | 2 | 2 | +100.0% | 0.143 | +602.0% | $+12.04 | 0.077 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Shanghai | 4 | 4 | +25.0% | 0.132 | +281.7% | $+11.27 | 0.072 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Busan | 6 | 6 | +33.3% | 0.094 | +179.3% | $+10.76 | 0.069 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Busan | 6 | 6 | +33.3% | 0.094 | +179.3% | $+10.76 | 0.137 |
| station_bias_p90_high | train_pre_2026_06_21 | Chongqing | 5 | 5 | +20.0% | 0.091 | +212.5% | $+10.62 | 0.125 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Chongqing | 5 | 5 | +20.0% | 0.091 | +212.5% | $+10.62 | 0.136 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Chongqing | 5 | 5 | +20.0% | 0.091 | +212.5% | $+10.62 | 0.068 |
| station_bias_p90_high | train_pre_2026_06_21 | BuenosAires | 7 | 7 | +42.9% | 0.166 | +147.5% | $+10.32 | 0.122 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | BuenosAires | 7 | 7 | +42.9% | 0.166 | +147.5% | $+10.32 | 0.066 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | BuenosAires | 7 | 7 | +42.9% | 0.166 | +147.5% | $+10.32 | 0.132 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Amsterdam | 10 | 10 | +30.0% | 0.135 | +102.6% | $+10.26 | 0.066 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Warsaw | 8 | 8 | +25.0% | 0.110 | +125.2% | $+10.01 | 0.128 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Helsinki | 14 | 14 | +14.3% | 0.081 | +69.0% | $+9.66 | 0.123 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Helsinki | 14 | 14 | +14.3% | 0.081 | +69.0% | $+9.66 | 0.062 |
| station_bias_p90_high | train_pre_2026_06_21 | Helsinki | 14 | 14 | +14.3% | 0.081 | +69.0% | $+9.66 | 0.114 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Amsterdam | 11 | 11 | +27.3% | 0.133 | +84.2% | $+9.26 | 0.118 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | Seoul | 12 | 12 | +16.7% | 0.111 | +75.9% | $+9.11 | 0.058 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Seoul | 13 | 13 | +15.4% | 0.109 | +62.4% | $+8.11 | 0.104 |
| station_bias_p90_high | train_pre_2026_06_21 | Seoul | 13 | 13 | +15.4% | 0.109 | +62.4% | $+8.11 | 0.095 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | Chicago | 14 | 14 | +14.3% | 0.107 | +56.8% | $+7.96 | 0.102 |

## Region Contribution

| strategy | period | region | rows | cities | win | ask | ROI | PnL | PnL share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| station_bias_p90_high | holdout_2026_06_21_26 | AS | 14 | 6 | +14.3% | 0.081 | +82.7% | $+11.58 | 2.075 |
| station_bias_p90_high | holdout_2026_06_21_26 | EU | 6 | 3 | +0.0% | 0.065 | -100.0% | $-6.00 | -1.075 |
| station_bias_p90_high | train_pre_2026_06_21 | AS | 76 | 12 | +14.5% | 0.099 | +70.2% | $+53.36 | 0.628 |
| station_bias_p90_high | train_pre_2026_06_21 | SA | 11 | 2 | +45.5% | 0.138 | +268.0% | $+29.48 | 0.347 |
| station_bias_p90_high | train_pre_2026_06_21 | OC | 3 | 1 | +33.3% | 0.097 | +412.8% | $+12.38 | 0.146 |
| station_bias_p90_high | train_pre_2026_06_21 | ME | 1 | 1 | +0.0% | 0.175 | -100.0% | $-1.00 | -0.012 |
| station_bias_p90_high | train_pre_2026_06_21 | US | 4 | 1 | +0.0% | 0.087 | -100.0% | $-4.00 | -0.047 |
| station_bias_p90_high | train_pre_2026_06_21 | EU | 35 | 3 | +8.6% | 0.090 | -15.1% | $-5.28 | -0.062 |
| v1_edge20_baseline_all | holdout_2026_06_21_26 | AS | 25 | 10 | +20.0% | 0.091 | +141.9% | $+35.47 | 1.091 |
| v1_edge20_baseline_all | holdout_2026_06_21_26 | EU | 18 | 9 | +22.2% | 0.104 | +42.4% | $+7.63 | 0.235 |
| v1_edge20_baseline_all | holdout_2026_06_21_26 | SA | 4 | 1 | +25.0% | 0.110 | +138.1% | $+5.52 | 0.170 |
| v1_edge20_baseline_all | holdout_2026_06_21_26 | AF | 1 | 1 | +0.0% | 0.185 | -100.0% | $-1.00 | -0.031 |
| v1_edge20_baseline_all | holdout_2026_06_21_26 | US | 18 | 8 | +11.1% | 0.140 | -39.5% | $-7.12 | -0.219 |
| v1_edge20_baseline_all | holdout_2026_06_21_26 | ME | 8 | 3 | +0.0% | 0.090 | -100.0% | $-8.00 | -0.246 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | AS | 106 | 17 | +13.2% | 0.097 | +46.1% | $+48.81 | 0.624 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | SA | 18 | 4 | +33.3% | 0.129 | +161.9% | $+29.14 | 0.372 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | EU | 118 | 11 | +14.4% | 0.101 | +11.6% | $+13.71 | 0.175 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | OC | 3 | 1 | +33.3% | 0.097 | +412.8% | $+12.38 | 0.158 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | AF | 5 | 1 | +0.0% | 0.097 | -100.0% | $-5.00 | -0.064 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | US | 106 | 11 | +9.4% | 0.111 | -7.6% | $-8.03 | -0.103 |
| v1_edge20_baseline_all | train_pre_2026_06_21 | ME | 27 | 3 | +7.4% | 0.112 | -47.2% | $-12.75 | -0.163 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | holdout_2026_06_21_26 | AS | 13 | 7 | +15.4% | 0.089 | +96.8% | $+12.58 | 0.516 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | holdout_2026_06_21_26 | EU | 9 | 5 | +33.3% | 0.127 | +92.2% | $+8.30 | 0.340 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | holdout_2026_06_21_26 | SA | 4 | 1 | +25.0% | 0.110 | +138.1% | $+5.52 | 0.226 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | holdout_2026_06_21_26 | US | 2 | 2 | +0.0% | 0.157 | -100.0% | $-2.00 | -0.082 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | AS | 55 | 13 | +21.8% | 0.098 | +138.1% | $+75.95 | 0.485 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | EU | 57 | 7 | +22.8% | 0.108 | +75.8% | $+43.23 | 0.276 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | SA | 17 | 4 | +35.3% | 0.131 | +177.3% | $+30.14 | 0.193 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | OC | 3 | 1 | +33.3% | 0.097 | +412.8% | $+12.38 | 0.079 |
| v1_edge20_v1_edge20_city_diag_ev_ge_0.5 | train_pre_2026_06_21 | US | 17 | 4 | +5.9% | 0.098 | -30.8% | $-5.24 | -0.033 |

## Interpretation

- The station-basis hypothesis is still better than the old source story, but not yet a tradable selector: train lift is real-looking, holdout lift is not.
- p_cal with city fixed effects is useful as an alarm bell and ranking diagnostic.  Because it uses raw city identity, promotion needs leave-one-city/region retraining plus fresh-forward proof, not just leave-one-city PnL stress on already selected rows.
- The best next research is not another historical threshold sweep.  It is to log as-of station-basis, bracket distance, p_cal, spread/depth, and then evaluate only rows generated after the telemetry patch.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_tail_robustness_v1.py`
- JSON summary: `docs/analysis/2026-07/2026-07-02-low-price-yes-tail-robustness-v1.json`
- Summary: `docs/analysis/2026-07/generated/low_price_yes_tail_robustness_v1/summary.csv`
- Complement: `docs/analysis/2026-07/generated/low_price_yes_tail_robustness_v1/complement.csv`
- City contributions: `docs/analysis/2026-07/generated/low_price_yes_tail_robustness_v1/city_contributions.csv`
- Region contributions: `docs/analysis/2026-07/generated/low_price_yes_tail_robustness_v1/region_contributions.csv`
