# Low-Price YES Source-Aware Tail v3

Generated: 2026-07-02T11:51:01+00:00

## Verdict

`source_aware_v3` is the best current research direction I found for the low-price tail work.  It is a `shadow_candidate`, not a live size-up approval.

```text
Base denominator:
  BUY_YES, edge >= 0.20, ask 0.05..0.20
  one earliest PIT candidate per city-date

Source-aware selector:
  GFS   -> ask 0.05..0.15
  ECMWF -> ask 0.10..0.20

Research sizing:
  $1/order fixed cost
```

Historical: 278 rows / 50 dates / 47 cities, avg ask 0.115, win +16.9%, ROI +46.1%, daily block CI [+3.0%, +95.7%], top-trade-removed +40.6%.
Forward closed 2026-06-27..2026-06-30: 11 rows / 3 dates, ROI +186.6%.

This is materially better shaped than pure v1 because it keeps row count broad enough while matching each source to the price zone where it historically pays.

```text
significance=PASS on historical point estimate and date-block CI
baseline=PASS versus unified ask 0.05..0.20 v1 denominator
forward=PARTIAL because forward has only 3 closed target dates
conclusion=shadow_candidate_keep_collecting; do not size up live yet
```

## Evidence Window

- Source rows: `docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/details.csv`.
- Denominator rows: 476 selected rows, target_date 2026-05-06..2026-06-30.
- Historical rows: 457, forward rows: 19.
- This is not `live_real` PnL; it is research replay using settled/closed low-price YES rows.

## Strategy Comparison

| strategy | hist rows | hist dates | hist win | hist ask | hist ROI | CI low | CI high | top removed | recent rows | recent ROI | holdout rows | holdout ROI | fwd rows | fwd ROI | full top removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| source_aware_v3 | 278 | 50 | +16.9% | 0.115 | +46.1% | +3.0% | +95.7% | +40.6% | 132 | +43.0% | 43 | +55.8% | 11 | +186.6% | +45.7% |
| baseline_v1_all_05_20 | 457 | 50 | +13.6% | 0.105 | +24.2% | -9.1% | +60.6% | +20.9% | 226 | +20.1% | 74 | +43.9% | 19 | +65.9% | +22.3% |
| gfs_05_15 | 150 | 49 | +11.3% | 0.091 | +34.9% | -20.8% | +93.3% | +24.6% | 77 | +46.0% | 24 | +27.6% | 6 | +330.3% | +35.5% |
| ecmwf_10_20 | 128 | 49 | +23.4% | 0.142 | +59.3% | +7.7% | +118.7% | +52.6% | 55 | +38.7% | 19 | +91.3% | 5 | +14.3% | +51.2% |
| source_aware_wide | 320 | 50 | +16.2% | 0.111 | +44.8% | +3.1% | +91.5% | +40.0% | 153 | +46.5% | 51 | +54.8% | 13 | +142.5% | +43.6% |
| source_aware_ecmwf_model35 | 277 | 49 | +16.2% | 0.112 | +42.0% | -3.2% | +93.0% | +36.5% | 131 | +45.8% | 44 | +30.6% | 12 | +162.7% | +41.2% |
| ask_08_20_all_sources | 291 | 50 | +16.5% | 0.128 | +24.0% | -9.2% | +60.4% | +20.3% | 143 | +27.9% | 50 | +52.5% | 11 | +21.3% | +20.3% |
| ask_05_15_all_sources | 376 | 50 | +10.9% | 0.090 | +18.4% | -19.7% | +59.9% | +14.3% | 188 | +19.9% | 58 | +35.0% | 16 | +61.3% | +15.8% |
| gfs_05_20 | 185 | 50 | +11.9% | 0.107 | +24.5% | -22.9% | +75.9% | +16.2% | 98 | +32.2% | 32 | +29.7% | 7 | +268.8% | +24.6% |
| ecmwf_08_20 | 170 | 49 | +20.6% | 0.128 | +53.5% | +10.4% | +104.9% | +47.3% | 76 | +47.1% | 27 | +79.0% | 7 | -18.4% | +44.7% |

## First-Principles Read

- Broad cheap YES is not the alpha.  The v1 base works only after requiring model-market edge; v2 intraday regime alone did not rescue broad tail YES.
- The strongest stable improvement is source-aware price expression: GFS is useful in cheaper 5c-15c tail tickets, while ECMWF works better after the market prices the tail at 10c-20c.
- That is consistent with source behavior: GFS tail signals are noisier but can catch cheap right tails; ECMWF is more conservative, so a higher ask is acceptable when it still shows edge.
- This is still a convex sleeve.  Losing days are normal; the question is whether the selected bucket keeps enough 5x-20x winners without relying on one trade.  The top-trade-removed result stays positive.

## Daily PnL

| period | date | rows | wins | cost | PnL | ROI |
| --- | --- | --- | --- | --- | --- | --- |
| forward_2026_06_27_30 | 2026-06-27 | 5 | 1 | $+5.00 | $+0.71 | +14.3% |
| forward_2026_06_27_30 | 2026-06-28 | 4 | 1 | $+4.00 | $+3.63 | +90.8% |
| forward_2026_06_27_30 | 2026-06-30 | 2 | 1 | $+2.00 | $+16.18 | +809.1% |
| historical | 2026-05-06 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| historical | 2026-05-07 | 3 | 1 | $+3.00 | $+10.33 | +344.4% |
| historical | 2026-05-08 | 6 | 1 | $+6.00 | $+9.27 | +154.5% |
| historical | 2026-05-09 | 3 | 1 | $+3.00 | $+3.25 | +108.3% |
| historical | 2026-05-10 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| historical | 2026-05-11 | 3 | 0 | $+3.00 | $-3.00 | -100.0% |
| historical | 2026-05-12 | 2 | 1 | $+2.00 | $+3.13 | +156.4% |
| historical | 2026-05-13 | 7 | 3 | $+7.00 | $+20.01 | +285.8% |
| historical | 2026-05-14 | 7 | 5 | $+7.00 | $+32.25 | +460.7% |
| historical | 2026-05-15 | 3 | 0 | $+3.00 | $-3.00 | -100.0% |
| historical | 2026-05-16 | 7 | 0 | $+7.00 | $-7.00 | -100.0% |
| historical | 2026-05-17 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| historical | 2026-05-20 | 7 | 1 | $+7.00 | $+0.41 | +5.8% |
| historical | 2026-05-21 | 4 | 0 | $+4.00 | $-4.00 | -100.0% |
| historical | 2026-05-22 | 10 | 2 | $+10.00 | $+6.98 | +69.8% |
| historical | 2026-05-23 | 6 | 1 | $+6.00 | $+2.00 | +33.3% |
| historical | 2026-05-24 | 5 | 2 | $+5.00 | $+12.14 | +242.9% |
| historical | 2026-05-25 | 5 | 0 | $+5.00 | $-5.00 | -100.0% |
| historical | 2026-05-26 | 3 | 1 | $+3.00 | $+3.45 | +115.1% |
| historical | 2026-05-27 | 4 | 1 | $+4.00 | $+2.25 | +56.2% |
| historical | 2026-05-28 | 12 | 1 | $+12.00 | $+3.38 | +28.2% |
| historical | 2026-05-29 | 6 | 0 | $+6.00 | $-6.00 | -100.0% |
| historical | 2026-05-30 | 5 | 1 | $+5.00 | $+1.90 | +37.9% |
| historical | 2026-05-31 | 3 | 2 | $+3.00 | $+10.38 | +346.0% |
| historical | 2026-06-01 | 3 | 0 | $+3.00 | $-3.00 | -100.0% |
| historical | 2026-06-02 | 3 | 1 | $+3.00 | $+3.25 | +108.3% |
| historical | 2026-06-03 | 1 | 1 | $+1.00 | $+6.14 | +614.3% |
| historical | 2026-06-04 | 4 | 0 | $+4.00 | $-4.00 | -100.0% |
| historical | 2026-06-05 | 4 | 0 | $+4.00 | $-4.00 | -100.0% |
| historical | 2026-06-06 | 9 | 0 | $+9.00 | $-9.00 | -100.0% |
| historical | 2026-06-07 | 5 | 0 | $+5.00 | $-5.00 | -100.0% |
| historical | 2026-06-08 | 7 | 1 | $+7.00 | $-1.87 | -26.7% |
| historical | 2026-06-09 | 4 | 0 | $+4.00 | $-4.00 | -100.0% |
| historical | 2026-06-10 | 6 | 0 | $+6.00 | $-6.00 | -100.0% |
| historical | 2026-06-11 | 7 | 1 | $+7.00 | $+3.53 | +50.4% |
| historical | 2026-06-12 | 6 | 1 | $+6.00 | $+2.70 | +44.9% |
| historical | 2026-06-13 | 9 | 1 | $+9.00 | $-2.10 | -23.4% |
| historical | 2026-06-14 | 12 | 1 | $+12.00 | $-4.59 | -38.3% |
| historical | 2026-06-15 | 8 | 1 | $+8.00 | $+8.67 | +108.3% |
| historical | 2026-06-16 | 9 | 4 | $+9.00 | $+30.72 | +341.3% |
| historical | 2026-06-17 | 6 | 0 | $+6.00 | $-6.00 | -100.0% |
| historical | 2026-06-18 | 5 | 1 | $+5.00 | $+2.41 | +48.1% |
| historical | 2026-06-19 | 6 | 0 | $+6.00 | $-6.00 | -100.0% |
| historical | 2026-06-20 | 4 | 2 | $+4.00 | $+15.27 | +381.8% |
| historical | 2026-06-21 | 9 | 1 | $+9.00 | $-2.94 | -32.7% |
| historical | 2026-06-22 | 6 | 1 | $+6.00 | $-0.69 | -11.6% |
| historical | 2026-06-23 | 12 | 2 | $+12.00 | $+2.63 | +21.9% |
| historical | 2026-06-24 | 9 | 4 | $+9.00 | $+31.99 | +355.4% |
| historical | 2026-06-25 | 3 | 0 | $+3.00 | $-3.00 | -100.0% |
| historical | 2026-06-26 | 4 | 0 | $+4.00 | $-4.00 | -100.0% |

## Top Forward / Recent Rows

| period | date | city | source | bracket | ask | edge | model p | YES final | PnL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forward | 2026-06-30 | Shanghai | open_meteo_live_gfs | 28 | 0.055 | 0.240 | 0.295 | 1.000 | $+17.18 |
| historical | 2026-06-15 | Guangzhou | open_meteo_live_gfs | 32+ | 0.060 | 0.382 | 0.442 | 1.000 | $+15.67 |
| historical | 2026-06-16 | Manila | open_meteo_live_gfs | 36 | 0.065 | 0.228 | 0.293 | 1.000 | $+14.38 |
| historical | 2026-05-28 | Wellington | open_meteo_live_gfs | 17 | 0.065 | 0.227 | 0.292 | 1.000 | $+14.38 |
| historical | 2026-05-08 | Shanghai | open_meteo_live_gfs | 19 | 0.066 | 0.230 | 0.296 | 1.000 | $+14.27 |
| historical | 2026-05-13 | Chicago | open_meteo_live_gfs | 58-59 | 0.068 | 0.471 | 0.539 | 1.000 | $+13.81 |
| historical | 2026-06-24 | Manila | open_meteo_live_gfs | 30 | 0.073 | 0.284 | 0.358 | 1.000 | $+12.61 |
| historical | 2026-05-07 | Shanghai | open_meteo_live_gfs | 30 | 0.075 | 0.272 | 0.346 | 1.000 | $+12.33 |
| historical | 2026-05-14 | Miami | open_meteo_live_gfs | 94-95 | 0.075 | 0.357 | 0.433 | 1.000 | $+12.25 |
| historical | 2026-06-20 | Houston | open_meteo_live_gfs | 84-85 | 0.078 | 0.412 | 0.490 | 1.000 | $+11.82 |
| historical | 2026-06-11 | Shanghai | open_meteo_live_gfs | 32 | 0.095 | 0.283 | 0.378 | 1.000 | $+9.53 |
| historical | 2026-05-22 | Atlanta | open_meteo_live_gfs | 86-87 | 0.095 | 0.230 | 0.325 | 1.000 | $+9.53 |
| historical | 2026-06-16 | LA | open_meteo_live_gfs | 68-69 | 0.095 | 0.427 | 0.522 | 1.000 | $+9.53 |
| historical | 2026-05-24 | Seoul | open_meteo_live_ecmwf | 24 | 0.100 | 0.278 | 0.378 | 1.000 | $+9.00 |
| historical | 2026-06-24 | MexicoCity | open_meteo_live_ecmwf | 21 | 0.105 | 0.264 | 0.369 | 1.000 | $+8.52 |
| historical | 2026-06-24 | Lucknow | open_meteo_live_ecmwf | 39 | 0.105 | 0.225 | 0.330 | 1.000 | $+8.52 |
| historical | 2026-06-23 | Tokyo | open_meteo_live_gfs | 23 | 0.115 | 0.233 | 0.348 | 1.000 | $+7.70 |
| historical | 2026-06-12 | Singapore | open_meteo_live_gfs | 30 | 0.115 | 0.324 | 0.439 | 1.000 | $+7.70 |
| historical | 2026-06-24 | Paris | open_meteo_live_gfs | 41 | 0.120 | 0.206 | 0.326 | 1.000 | $+7.33 |
| historical | 2026-05-31 | Helsinki | open_meteo_live_ecmwf | 14 | 0.124 | 0.244 | 0.368 | 1.000 | $+7.03 |
| historical | 2026-05-14 | Jeddah | open_meteo_live_ecmwf | 39 | 0.125 | 0.226 | 0.351 | 1.000 | $+7.00 |
| historical | 2026-05-23 | SaoPaulo | open_meteo_live_ecmwf | 18 | 0.125 | 0.211 | 0.336 | 1.000 | $+7.00 |
| forward | 2026-06-28 | Shanghai | open_meteo_live_gfs | 30 | 0.131 | 0.264 | 0.395 | 1.000 | $+6.63 |
| historical | 2026-05-20 | Manila | open_meteo_live_gfs | 36 | 0.135 | 0.236 | 0.371 | 1.000 | $+6.41 |
| historical | 2026-06-14 | Madrid | open_meteo_live_ecmwf | 35 | 0.135 | 0.274 | 0.408 | 1.000 | $+6.41 |
| historical | 2026-06-18 | London | open_meteo_live_ecmwf | 26 | 0.135 | 0.383 | 0.518 | 1.000 | $+6.41 |
| historical | 2026-06-03 | Amsterdam | open_meteo_live_ecmwf | 20 | 0.140 | 0.208 | 0.348 | 1.000 | $+6.14 |
| historical | 2026-05-24 | KualaLumpur | open_meteo_live_ecmwf | 34+ | 0.140 | 0.429 | 0.569 | 1.000 | $+6.14 |
| historical | 2026-06-16 | Chicago | open_meteo_live_gfs | 76-77 | 0.140 | 0.394 | 0.534 | 1.000 | $+6.14 |
| historical | 2026-05-30 | Moscow | open_meteo_live_ecmwf | 14 | 0.145 | 0.271 | 0.416 | 1.000 | $+5.90 |

## Contribution Slices

| dimension | level | rows | dates | cities | win | avg ask | PnL | ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bracket | 30 | 11 | 10 | 8 | +36.4% | 0.112 | $+32.27 | +293.3% |
| bracket | 36 | 5 | 5 | 2 | +40.0% | 0.090 | $+17.79 | +355.8% |
| bracket | 18 | 7 | 7 | 7 | +57.1% | 0.161 | $+17.54 | +250.5% |
| bracket | 14 | 4 | 4 | 4 | +75.0% | 0.141 | $+17.38 | +434.5% |
| bracket | 26 | 9 | 8 | 8 | +44.4% | 0.149 | $+15.83 | +175.9% |
| bracket | 39 | 4 | 4 | 2 | +50.0% | 0.117 | $+13.52 | +338.1% |
| bracket | 15 | 5 | 5 | 5 | +60.0% | 0.157 | $+13.23 | +264.5% |
| bracket | 17 | 3 | 3 | 2 | +33.3% | 0.112 | $+12.38 | +412.8% |
| bracket | 21 | 4 | 4 | 4 | +50.0% | 0.123 | $+11.24 | +281.0% |
| bracket | 24 | 12 | 11 | 8 | +25.0% | 0.124 | $+10.92 | +91.0% |
| bracket | 19 | 6 | 5 | 6 | +16.7% | 0.113 | $+9.27 | +154.5% |
| bracket | 28 | 15 | 14 | 11 | +13.3% | 0.105 | $+9.12 | +60.8% |
| bracket | 84-85 | 5 | 5 | 3 | +20.0% | 0.085 | $+7.82 | +156.4% |
| bracket | 25 | 5 | 4 | 4 | +40.0% | 0.135 | $+6.76 | +135.1% |
| bracket | 94-95 | 7 | 6 | 6 | +14.3% | 0.106 | $+6.25 | +89.2% |
| bracket | 34 | 3 | 3 | 3 | +33.3% | 0.118 | $+3.90 | +129.9% |
| bracket | 68-69 | 7 | 6 | 2 | +14.3% | 0.087 | $+3.53 | +50.4% |
| bracket | 32 | 8 | 6 | 6 | +12.5% | 0.118 | $+2.53 | +31.6% |
| bracket | 35 | 6 | 6 | 5 | +16.7% | 0.112 | $+1.41 | +23.5% |
| bracket | 20 | 6 | 6 | 4 | +16.7% | 0.096 | $+1.14 | +19.0% |
| bracket | 86-87 | 10 | 9 | 6 | +10.0% | 0.102 | $+0.53 | +5.3% |
| bracket | 37+ | 6 | 6 | 2 | +16.7% | 0.154 | $+0.25 | +4.2% |
| bracket | 38 | 3 | 3 | 3 | +0.0% | 0.125 | $-3.00 | -100.0% |
| bracket | 40 | 3 | 3 | 2 | +0.0% | 0.102 | $-3.00 | -100.0% |
| bracket | 64-65 | 3 | 3 | 3 | +0.0% | 0.078 | $-3.00 | -100.0% |
| bracket | 72-73 | 3 | 3 | 1 | +0.0% | 0.096 | $-3.00 | -100.0% |
| bracket | 66-67 | 4 | 4 | 2 | +0.0% | 0.104 | $-4.00 | -100.0% |
| bracket | 82-83 | 4 | 4 | 3 | +0.0% | 0.092 | $-4.00 | -100.0% |
| bracket | 27 | 5 | 5 | 4 | +0.0% | 0.114 | $-5.00 | -100.0% |
| bracket | 92-93 | 5 | 5 | 3 | +0.0% | 0.096 | $-5.00 | -100.0% |
| bracket | 23 | 14 | 13 | 9 | +7.1% | 0.126 | $-5.30 | -37.9% |
| bracket | 16 | 7 | 7 | 4 | +0.0% | 0.141 | $-7.00 | -100.0% |
| bracket | 90-91 | 7 | 5 | 4 | +0.0% | 0.109 | $-7.00 | -100.0% |
| bracket | 29 | 8 | 8 | 6 | +0.0% | 0.105 | $-8.00 | -100.0% |
| bracket | 80-81 | 8 | 8 | 6 | +0.0% | 0.099 | $-8.00 | -100.0% |
| bracket | 31 | 9 | 8 | 5 | +0.0% | 0.111 | $-9.00 | -100.0% |
| bracket | 88-89 | 10 | 10 | 5 | +0.0% | 0.099 | $-10.00 | -100.0% |
| bracket | 22 | 11 | 11 | 6 | +0.0% | 0.114 | $-11.00 | -100.0% |
| city | Shanghai | 12 | 12 | 1 | +41.7% | 0.094 | $+52.94 | +441.2% |
| city | Amsterdam | 12 | 12 | 1 | +50.0% | 0.155 | $+25.56 | +213.0% |
| city | Manila | 12 | 12 | 1 | +25.0% | 0.082 | $+24.40 | +203.3% |
| city | Madrid | 5 | 5 | 1 | +60.0% | 0.155 | $+13.25 | +265.0% |
| city | KualaLumpur | 3 | 3 | 1 | +66.7% | 0.150 | $+11.04 | +368.0% |
| city | BuenosAires | 7 | 7 | 1 | +42.9% | 0.166 | $+10.32 | +147.5% |
| city | Moscow | 10 | 10 | 1 | +30.0% | 0.154 | $+8.48 | +84.8% |
| city | MexicoCity | 9 | 9 | 1 | +22.2% | 0.129 | $+7.19 | +79.9% |
| city | Chicago | 15 | 15 | 1 | +13.3% | 0.097 | $+6.96 | +46.4% |
| city | Jeddah | 8 | 8 | 1 | +25.0% | 0.146 | $+5.71 | +71.4% |
| city | Helsinki | 3 | 3 | 1 | +33.3% | 0.131 | $+5.03 | +167.7% |
| city | Houston | 9 | 9 | 1 | +11.1% | 0.086 | $+3.82 | +42.5% |
| city | Lucknow | 6 | 6 | 1 | +16.7% | 0.137 | $+3.52 | +58.7% |
| city | Istanbul | 3 | 3 | 1 | +33.3% | 0.135 | $+3.25 | +108.3% |
| city | Miami | 10 | 10 | 1 | +10.0% | 0.101 | $+3.25 | +32.5% |
| city | Singapore | 6 | 6 | 1 | +16.7% | 0.121 | $+2.70 | +44.9% |
| city | London | 5 | 5 | 1 | +20.0% | 0.141 | $+2.41 | +48.1% |
| city | Warsaw | 4 | 4 | 1 | +25.0% | 0.135 | $+2.25 | +56.2% |
| city | Busan | 4 | 4 | 1 | +25.0% | 0.115 | $+2.23 | +55.8% |
| city | Seoul | 8 | 8 | 1 | +12.5% | 0.129 | $+2.00 | +25.0% |
| city | Paris | 7 | 7 | 1 | +14.3% | 0.079 | $+1.33 | +19.0% |
| city | Karachi | 5 | 5 | 1 | +20.0% | 0.170 | $+1.25 | +25.0% |
| city | LA | 10 | 10 | 1 | +10.0% | 0.092 | $+0.53 | +5.3% |
| city | Milan | 6 | 6 | 1 | +16.7% | 0.125 | $+0.06 | +1.0% |
| city | Atlanta | 13 | 13 | 1 | +7.7% | 0.094 | $-2.47 | -19.0% |
| city | Beijing | 4 | 4 | 1 | +0.0% | 0.119 | $-4.00 | -100.0% |
| city | Dallas | 4 | 4 | 1 | +0.0% | 0.121 | $-4.00 | -100.0% |
| city | Munich | 5 | 5 | 1 | +0.0% | 0.129 | $-5.00 | -100.0% |
| city | Ankara | 7 | 7 | 1 | +0.0% | 0.124 | $-7.00 | -100.0% |
| city | Shenzhen | 7 | 7 | 1 | +0.0% | 0.152 | $-7.00 | -100.0% |
| city | Tokyo | 16 | 16 | 1 | +6.2% | 0.084 | $-7.30 | -45.7% |
| city | Austin | 8 | 8 | 1 | +0.0% | 0.101 | $-8.00 | -100.0% |
| city | Denver | 8 | 8 | 1 | +0.0% | 0.100 | $-8.00 | -100.0% |
| city | NYC | 9 | 9 | 1 | +0.0% | 0.094 | $-9.00 | -100.0% |
| city | TelAviv | 13 | 13 | 1 | +0.0% | 0.083 | $-13.00 | -100.0% |
| forecast_source | open_meteo_live_ecmwf | 133 | 51 | 28 | +23.3% | 0.141 | $+76.56 | +57.6% |
| forecast_source | open_meteo_live_gfs | 156 | 52 | 20 | +12.2% | 0.091 | $+72.19 | +46.3% |

## Action

Keep the current $1 live v1 running.  For the next iteration, shadow-tag whether each v1 live candidate is `source_aware_v3`; only after fresh forward settlement should we switch the live selector from unified v1 to this source-aware expression.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_source_aware_tail_v3.py`
- JSON summary: `docs/analysis/2026-07/2026-07-02-low-price-yes-source-aware-tail-v3.json`
- Details: `docs/analysis/2026-07/generated/low_price_yes_source_aware_tail_v3/details.csv`
- Summary: `docs/analysis/2026-07/generated/low_price_yes_source_aware_tail_v3/summary.csv`
- Daily: `docs/analysis/2026-07/generated/low_price_yes_source_aware_tail_v3/daily.csv`
- Contributions: `docs/analysis/2026-07/generated/low_price_yes_source_aware_tail_v3/contributions.csv`
