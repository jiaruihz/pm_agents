# HeadA Low-Price YES Sizing / Fee / Strict-Stop Replay v2

Generated: 2026-07-03T15:49:50.393345+00:00

## Verdict

This is still an execution-layer audit for HeadA (`forecast_tail_low_price_yes`), not a new entry alpha search.

```text
conclusion=shadow_research_only
entry_selector=unchanged
live_action=keep_current_hold; do_not_add_stop_yet; do_not_size_up
```

Plain English: after official weather taker fees, the sleeve still can be positive, but the margin is thinner. The current fixed-cash sizing overweights the cheapest longshots. Fixed-share / price-tier sizing is more logically aligned with a lottery sleeve because each trade has a more similar max payout. The strict dead-ticket stop is not a clear win yet; it can reduce some dead exposure, but it has not beaten hold cleanly enough on recent/frozen windows to deploy.

## Fee Rule

Official Polymarket docs define trading fees as:

```text
fee = C * feeRate * p * (1 - p)
```

For Weather, the official category table lists taker `feeRate=0.05`, maker fee `0`, and maker rebate `25%`. Fees are applied at match time and markets expose fee parameters through CLOB market info. The live Manila weather market checked during this run returned `fd={"r":0.05,"e":1,"to":true}`; `/fee-rate` returned raw `base_fee=1000`. This report therefore uses `shares * 0.05 * price * (1-price)` for taker fills and zero fee for maker fills. No invented flat fee is used.

Sources: [Polymarket Fees](https://docs.polymarket.com/trading/fees), [CLOB fee-rate endpoint](https://docs.polymarket.com/api-reference/market-data/get-fee-rate), [Maker Rebates](https://docs.polymarket.com/market-makers/maker-rebates).

## Data Snapshot

- Input denominator: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv`.
- Rows: 476 candidates; dates 2026-05-06..2026-06-30; cities 48.
- Snapshot files: `runtime/weather_edge_v1/market_data/paper_snapshots`; path rows with future quote 475/476, with future bid 396/476.
- Path events found: TP20 rows 161, strict-dead stop rows 186, late-dust stop rows 0.
- This replay uses only snapshots after `decision_snapshot_ts_utc` for exits. It does not change the entry selector.

## Current Sizing + Fee Hit

| profile | ROI | CI | note |
| --- | ---: | ---: | --- |
| fixed cash $0.80, maker/no-fee hold | +25.9% | [-6.1%, +61.3%] | old optimistic executable baseline |
| fixed cash $0.80, taker fee hold | +20.5% | [-10.1%, +54.4%] | official weather taker fee only |
| fixed cash $0.80, +1c entry + taker fee hold | +8.6% | [-19.1%, +38.9%] | conservative taker/slippage profile |
| fixed cash $0.80, taker fee + strict stop | +20.9% | [-5.0%, +49.0%] | tests dead-ticket stop, no TP |

## Main Same-Denominator Results

Full window:

| sizing | fee/profile | exit | rows | dates | avg cost | avg fees | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | maker_entry_no_fee | hold | 476 | 53 | $0.800 | $0.0000 | 0.0% | +25.9% | [-6.1%, +61.3%] | 27 | 18 | $-10.40 / -100.0% |
| fixed_cash_0p80 | taker_entry_weather_fee | hold | 476 | 53 | $0.836 | $0.0358 | 0.0% | +20.5% | [-10.1%, +54.4%] | 29 | 19 | $-10.86 / -100.0% |
| fixed_cash_0p80 | taker_entry_plus1c_weather_fee | hold | 476 | 53 | $0.928 | $0.0394 | 0.0% | +8.6% | [-19.1%, +38.9%] | 29 | 20 | $-12.07 / -100.0% |
| fixed_cash_0p80 | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 476 | 53 | $0.928 | $0.0472 | 39.1% | +9.0% | [-14.4%, +34.3%] | 27 | 16 | $-10.72 / -100.0% |
| fixed_8_shares | maker_entry_no_fee | hold | 476 | 53 | $0.841 | $0.0000 | 0.0% | +29.9% | [-1.2%, +64.1%] | 25 | 14 | $-11.39 / -100.0% |
| fixed_8_shares | taker_entry_weather_fee | hold | 476 | 53 | $0.878 | $0.0369 | 0.0% | +24.5% | [-5.4%, +57.2%] | 26 | 14 | $-11.89 / -100.0% |
| fixed_8_shares | taker_entry_plus1c_weather_fee | hold | 476 | 53 | $0.961 | $0.0401 | 0.0% | +13.7% | [-13.4%, +43.6%] | 27 | 15 | $-12.97 / -100.0% |
| fixed_8_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 476 | 53 | $0.961 | $0.0469 | 39.1% | +14.0% | [-10.0%, +39.7%] | 25 | 17 | $-12.02 / -100.0% |
| price_tier_6_8_10_shares | taker_entry_weather_fee | hold | 476 | 53 | $0.900 | $0.0376 | 0.0% | +26.5% | [-3.4%, +60.1%] | 25 | 16 | $-12.83 / -100.0% |
| price_tier_6_8_10_shares | taker_entry_plus1c_weather_fee | hold | 476 | 53 | $0.980 | $0.0405 | 0.0% | +16.2% | [-11.4%, +47.1%] | 27 | 17 | $-13.88 / -100.0% |
| price_tier_6_8_10_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 476 | 53 | $0.980 | $0.0469 | 39.1% | +16.5% | [-8.5%, +44.4%] | 24 | 17 | $-13.08 / -100.0% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee | hold | 476 | 53 | $0.874 | $0.0364 | 0.0% | +28.5% | [-2.2%, +62.5%] | 24 | 15 | $-11.85 / -100.0% |
| quality_price_tier_5_8_12_shares | taker_entry_plus1c_weather_fee | hold | 476 | 53 | $0.950 | $0.0392 | 0.0% | +18.3% | [-10.0%, +49.6%] | 26 | 15 | $-12.82 / -100.0% |
| quality_price_tier_5_8_12_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 476 | 53 | $0.950 | $0.0453 | 39.1% | +17.1% | [-8.0%, +45.0%] | 23 | 16 | $-12.33 / -100.0% |

Recent window (`target_date >= 2026-06-21`):

| sizing | fee/profile | exit | rows | dates | avg cost | avg fees | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | maker_entry_no_fee | hold | 93 | 9 | $0.800 | $0.0000 | 0.0% | +48.4% | [-24.0%, +149.0%] | 4 | 3 | $-6.35 / -56.7% |
| fixed_cash_0p80 | taker_entry_weather_fee | hold | 93 | 9 | $0.836 | $0.0357 | 0.0% | +42.1% | [-27.3%, +138.3%] | 4 | 4 | $-6.86 / -58.6% |
| fixed_cash_0p80 | taker_entry_plus1c_weather_fee | hold | 93 | 9 | $0.927 | $0.0393 | 0.0% | +28.1% | [-34.1%, +114.8%] | 4 | 4 | $-8.31 / -63.1% |
| fixed_cash_0p80 | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 93 | 9 | $0.927 | $0.0465 | 36.6% | +34.9% | [-21.5%, +113.6%] | 4 | 0 | $-5.99 / -48.2% |
| fixed_8_shares | maker_entry_no_fee | hold | 93 | 9 | $0.854 | $0.0000 | 0.0% | +51.2% | [+1.0%, +115.8%] | 4 | 0 | $-2.96 / -27.0% |
| fixed_8_shares | taker_entry_weather_fee | hold | 93 | 9 | $0.891 | $0.0374 | 0.0% | +44.8% | [-3.2%, +106.7%] | 4 | 0 | $-3.43 / -30.0% |
| fixed_8_shares | taker_entry_plus1c_weather_fee | hold | 93 | 9 | $0.974 | $0.0405 | 0.0% | +32.5% | [-11.6%, +88.9%] | 4 | 0 | $-4.47 / -35.8% |
| fixed_8_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 93 | 9 | $0.974 | $0.0470 | 36.6% | +37.1% | [+0.2%, +80.5%] | 4 | 0 | $-2.62 / -21.0% |
| price_tier_6_8_10_shares | taker_entry_weather_fee | hold | 93 | 9 | $0.918 | $0.0382 | 0.0% | +47.5% | [+7.3%, +97.2%] | 3 | 0 | $-2.48 / -19.9% |
| price_tier_6_8_10_shares | taker_entry_plus1c_weather_fee | hold | 93 | 9 | $0.998 | $0.0412 | 0.0% | +35.8% | [-1.3%, +81.1%] | 4 | 0 | $-3.43 / -25.6% |
| price_tier_6_8_10_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 93 | 9 | $0.998 | $0.0474 | 36.6% | +39.7% | [+10.1%, +72.8%] | 3 | 0 | $-1.52 / -11.3% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee | hold | 93 | 9 | $0.888 | $0.0369 | 0.0% | +42.1% | [+4.2%, +92.6%] | 3 | 0 | $-2.23 / -21.8% |
| quality_price_tier_5_8_12_shares | taker_entry_plus1c_weather_fee | hold | 93 | 9 | $0.964 | $0.0397 | 0.0% | +30.9% | [-4.0%, +77.1%] | 4 | 0 | $-3.09 / -27.9% |
| quality_price_tier_5_8_12_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 93 | 9 | $0.964 | $0.0456 | 36.6% | +32.6% | [+6.3%, +61.6%] | 3 | 0 | $-2.20 / -19.9% |

Closed forward (`2026-06-27..2026-06-30`):

| sizing | fee/profile | exit | rows | dates | avg cost | avg fees | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | maker_entry_no_fee | hold | 19 | 3 | $0.800 | $0.0000 | 0.0% | +65.9% | [-48.1%, +809.1%] | 1 | 0 | $-4.23 / -48.1% |
| fixed_cash_0p80 | taker_entry_weather_fee | hold | 19 | 3 | $0.836 | $0.0360 | 0.0% | +58.8% | [-50.3%, +769.9%] | 1 | 1 | $-4.62 / -50.3% |
| fixed_cash_0p80 | taker_entry_plus1c_weather_fee | hold | 19 | 3 | $0.934 | $0.0399 | 0.0% | +42.2% | [-55.2%, +673.3%] | 1 | 1 | $-5.62 / -55.2% |
| fixed_cash_0p80 | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 19 | 3 | $0.934 | $0.0425 | 15.8% | +48.0% | [-48.2%, +690.4%] | 1 | 0 | $-4.92 / -48.2% |
| fixed_8_shares | maker_entry_no_fee | hold | 19 | 3 | $0.797 | $0.0000 | 0.0% | +58.5% | [-15.1%, +400.0%] | 1 | 0 | $-1.42 / -15.1% |
| fixed_8_shares | taker_entry_weather_fee | hold | 19 | 3 | $0.832 | $0.0352 | 0.0% | +51.8% | [-18.6%, +378.9%] | 1 | 0 | $-1.83 / -18.6% |
| fixed_8_shares | taker_entry_plus1c_weather_fee | hold | 19 | 3 | $0.915 | $0.0384 | 0.0% | +38.0% | [-25.6%, +335.6%] | 1 | 0 | $-2.75 / -25.6% |
| fixed_8_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 19 | 3 | $0.915 | $0.0418 | 15.8% | +45.4% | [-17.8%, +360.9%] | 1 | 0 | $-1.92 / -17.8% |
| price_tier_6_8_10_shares | taker_entry_weather_fee | hold | 19 | 3 | $0.839 | $0.0352 | 0.0% | +50.5% | [-1.6%, +223.0%] | 1 | 0 | $-0.17 / -1.6% |
| price_tier_6_8_10_shares | taker_entry_plus1c_weather_fee | hold | 19 | 3 | $0.917 | $0.0381 | 0.0% | +37.7% | [-9.4%, +196.5%] | 1 | 0 | $-1.04 / -9.4% |
| price_tier_6_8_10_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 19 | 3 | $0.917 | $0.0419 | 15.8% | +46.2% | [-1.4%, +225.2%] | 1 | 0 | $-0.15 / -1.4% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee | hold | 19 | 3 | $0.798 | $0.0335 | 0.0% | +28.0% | [-21.8%, +233.9%] | 1 | 0 | $-2.23 / -21.8% |
| quality_price_tier_5_8_12_shares | taker_entry_plus1c_weather_fee | hold | 19 | 3 | $0.871 | $0.0362 | 0.0% | +17.3% | [-27.9%, +206.3%] | 1 | 0 | $-3.09 / -27.9% |
| quality_price_tier_5_8_12_shares | taker_entry_plus1c_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 19 | 3 | $0.871 | $0.0397 | 15.8% | +25.5% | [-19.9%, +234.8%] | 1 | 0 | $-2.20 / -19.9% |

## Stop Study

Strict stop definition: after the forecast peak window, if the ticket never had a meaningful pump (`max_bid_so_far < max(0.12, entry+0.03)`) and the current bid is only residual value (`2c..7c`), sell at the bid and charge taker fee if the profile says the stop is taker-executed. `late_dust_stop` is the same idea inside the last three hours before settlement with bid `2c..6c`.

Full:

| sizing | fee/profile | exit | rows | dates | avg cost | avg fees | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold | 476 | 53 | $0.836 | $0.0358 | 0.0% | +20.5% | [-10.1%, +54.4%] | 29 | 19 | $-10.86 / -100.0% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_time_stop_or_late_salvage | 476 | 53 | $0.836 | $0.0466 | 45.0% | +4.8% | [-18.1%, +29.3%] | 29 | 18 | $-8.85 / -100.0% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_stop | 476 | 53 | $0.836 | $0.0437 | 39.1% | +20.9% | [-5.0%, +49.0%] | 25 | 16 | $-9.52 / -100.0% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_late_dust_stop | 476 | 53 | $0.836 | $0.0358 | 0.0% | +20.5% | [-10.1%, +54.4%] | 29 | 19 | $-10.86 / -100.0% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 476 | 53 | $0.836 | $0.0437 | 39.1% | +20.9% | [-5.0%, +49.0%] | 25 | 16 | $-9.52 / -100.0% |
| fixed_8_shares | taker_entry_weather_fee_stop_taker_fee | hold | 476 | 53 | $0.878 | $0.0369 | 0.0% | +24.5% | [-5.4%, +57.2%] | 26 | 14 | $-11.89 / -100.0% |
| fixed_8_shares | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 476 | 53 | $0.878 | $0.0438 | 39.1% | +24.8% | [-1.5%, +52.8%] | 24 | 16 | $-10.94 / -100.0% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee_stop_taker_fee | hold | 476 | 53 | $0.874 | $0.0364 | 0.0% | +28.5% | [-2.2%, +62.5%] | 24 | 15 | $-11.85 / -100.0% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 476 | 53 | $0.874 | $0.0425 | 39.1% | +27.3% | [+0.0%, +57.7%] | 22 | 16 | $-11.36 / -100.0% |

Recent:

| sizing | fee/profile | exit | rows | dates | avg cost | avg fees | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold | 93 | 9 | $0.836 | $0.0357 | 0.0% | +42.1% | [-27.3%, +138.3%] | 4 | 4 | $-6.86 / -58.6% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_time_stop_or_late_salvage | 93 | 9 | $0.836 | $0.0466 | 46.2% | -3.3% | [-30.7%, +22.1%] | 6 | 2 | $-4.69 / -70.2% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_stop | 93 | 9 | $0.836 | $0.0430 | 36.6% | +49.6% | [-12.7%, +136.7%] | 4 | 0 | $-4.54 / -42.6% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_late_dust_stop | 93 | 9 | $0.836 | $0.0357 | 0.0% | +42.1% | [-27.3%, +138.3%] | 4 | 4 | $-6.86 / -58.6% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 93 | 9 | $0.836 | $0.0430 | 36.6% | +49.6% | [-12.7%, +136.7%] | 4 | 0 | $-4.54 / -42.6% |
| fixed_8_shares | taker_entry_weather_fee_stop_taker_fee | hold | 93 | 9 | $0.891 | $0.0374 | 0.0% | +44.8% | [-3.2%, +106.7%] | 4 | 0 | $-3.43 / -30.0% |
| fixed_8_shares | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 93 | 9 | $0.891 | $0.0439 | 36.6% | +49.9% | [+9.6%, +97.4%] | 4 | 0 | $-1.66 / -14.5% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee_stop_taker_fee | hold | 93 | 9 | $0.888 | $0.0369 | 0.0% | +42.1% | [+4.2%, +92.6%] | 3 | 0 | $-2.23 / -21.8% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 93 | 9 | $0.888 | $0.0429 | 36.6% | +43.9% | [+15.4%, +75.6%] | 2 | 0 | $-1.34 / -13.1% |

Closed forward:

| sizing | fee/profile | exit | rows | dates | avg cost | avg fees | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold | 19 | 3 | $0.836 | $0.0360 | 0.0% | +58.8% | [-50.3%, +769.9%] | 1 | 1 | $-4.62 / -50.3% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_time_stop_or_late_salvage | 19 | 3 | $0.836 | $0.0402 | 15.8% | -22.5% | [-70.2%, +21.7%] | 2 | 1 | $-3.49 / -70.2% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_stop | 19 | 3 | $0.836 | $0.0387 | 15.8% | +65.3% | [-42.6%, +789.1%] | 1 | 0 | $-3.91 / -42.6% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_late_dust_stop | 19 | 3 | $0.836 | $0.0360 | 0.0% | +58.8% | [-50.3%, +769.9%] | 1 | 1 | $-4.62 / -50.3% |
| fixed_cash_0p80 | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 19 | 3 | $0.836 | $0.0387 | 15.8% | +65.3% | [-42.6%, +789.1%] | 1 | 0 | $-3.91 / -42.6% |
| fixed_8_shares | taker_entry_weather_fee_stop_taker_fee | hold | 19 | 3 | $0.832 | $0.0352 | 0.0% | +51.8% | [-18.6%, +378.9%] | 1 | 0 | $-1.83 / -18.6% |
| fixed_8_shares | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 19 | 3 | $0.832 | $0.0386 | 15.8% | +60.0% | [-10.2%, +406.8%] | 1 | 0 | $-1.00 / -10.2% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee_stop_taker_fee | hold | 19 | 3 | $0.798 | $0.0335 | 0.0% | +28.0% | [-21.8%, +233.9%] | 1 | 0 | $-2.23 / -21.8% |
| quality_price_tier_5_8_12_shares | taker_entry_weather_fee_stop_taker_fee | hold_plus_strict_dead_or_late_dust | 19 | 3 | $0.798 | $0.0370 | 15.8% | +36.9% | [-13.1%, +264.9%] | 1 | 0 | $-1.34 / -13.1% |

## Best Rows Under Fee-Aware Profiles

| full rank | sizing | fee/profile | exit | ROI | CI | rows | dates | total cost | max daily loss |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | quality_price_tier_5_8_12_shares | taker_entry_weather_fee | hold | +28.5% | [-2.2%, +62.5%] | 476 | 53 | $416.17 | $-11.85 |
| 2 | quality_price_tier_5_8_12_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +28.0% | [+0.8%, +58.3%] | 476 | 53 | $416.17 | $-11.33 |
| 3 | price_tier_6_8_10_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +27.5% | [+0.4%, +58.0%] | 476 | 53 | $428.48 | $-11.99 |
| 4 | pcal_scaled_8_shares | taker_entry_weather_fee | hold | +27.2% | [-3.5%, +60.9%] | 476 | 53 | $416.51 | $-12.45 |
| 5 | price_tier_6_8_10_shares | taker_entry_weather_fee | hold | +26.5% | [-3.4%, +60.1%] | 476 | 53 | $428.48 | $-12.83 |
| 6 | payout25_cap5 | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +25.5% | [-0.7%, +53.7%] | 476 | 53 | $1305.64 | $-34.04 |
| 7 | fixed_8_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +25.5% | [-0.7%, +53.7%] | 476 | 53 | $417.80 | $-10.89 |
| 8 | fixed_12_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +25.5% | [-0.7%, +53.7%] | 476 | 53 | $626.71 | $-16.34 |

| recent rank | sizing | fee/profile | exit | ROI | CI | rows | dates | total cost | max daily loss |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | price_tier_6_8_10_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +52.5% | [+20.3%, +88.8%] | 93 | 9 | $85.40 | $-0.48 |
| 2 | fixed_8_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +50.6% | [+10.3%, +98.2%] | 93 | 9 | $82.86 | $-1.57 |
| 3 | payout25_cap5 | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +50.6% | [+10.3%, +98.2%] | 93 | 9 | $258.95 | $-4.91 |
| 4 | fixed_12_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +50.6% | [+10.3%, +98.2%] | 93 | 9 | $124.29 | $-2.36 |
| 5 | fixed_cash_0p80 | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +50.5% | [-11.8%, +137.6%] | 93 | 9 | $77.72 | $-4.43 |
| 6 | modelp_scaled_8_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +49.2% | [+17.3%, +85.1%] | 93 | 9 | $84.97 | $-0.51 |
| 7 | edge_scaled_8_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +47.7% | [+11.8%, +88.6%] | 93 | 9 | $78.78 | $-1.46 |
| 8 | price_tier_6_8_10_shares | taker_entry_weather_fee | hold | +47.5% | [+7.3%, +97.2%] | 93 | 9 | $85.40 | $-2.48 |

| closed_forward rank | sizing | fee/profile | exit | ROI | CI | rows | dates | total cost | max daily loss |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | edge_scaled_8_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +69.4% | [+23.0%, +416.3%] | 19 | 3 | $15.29 | $+2.26 |
| 2 | modelp_scaled_8_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +67.7% | [+23.2%, +327.9%] | 19 | 3 | $16.16 | $+2.42 |
| 3 | fixed_cash_0p80 | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +65.6% | [-42.2%, +790.1%] | 19 | 3 | $15.88 | $-3.88 |
| 4 | edge_scaled_8_shares | taker_entry_weather_fee | hold | +62.1% | [+15.4%, +387.3%] | 19 | 3 | $15.29 | $+1.52 |
| 5 | fixed_8_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +60.4% | [-9.8%, +408.1%] | 19 | 3 | $15.81 | $-0.96 |
| 6 | payout25_cap5 | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +60.4% | [-9.8%, +408.1%] | 19 | 3 | $49.42 | $-3.00 |
| 7 | fixed_12_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +60.4% | [-9.8%, +408.1%] | 19 | 3 | $23.72 | $-1.44 |
| 8 | price_tier_6_8_10_shares | taker_entry_weather_fee | hold_plus_strict_dead_or_late_dust | +60.2% | [+7.5%, +255.8%] | 19 | 3 | $15.95 | $+0.77 |

## Interpretation

1. `fixed_cash_0p80` is not neutral. It buys many more shares at 5c than at 15c, so it implicitly says the cheapest tickets deserve larger max payout. That is not obviously true for this sleeve.
2. Fixed-share and price-tier policies are more defensible first-principles sizing because they keep max payout closer across rows and let cash risk rise with price/quality.
3. Official weather taker fees are large enough to matter for $0.80 probes: around 4-5% of entry cash in the 5c-20c band. Maker fills avoid this fee, but maker fill probability and adverse selection must be measured live.
4. The strict stop is a useful shadow telemetry field, not a live change. It should be logged forward as `strict_dead_stop_would_trigger`, `late_dust_stop_would_trigger`, bid, time-to-settle, and eventual settlement.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_sizing_fee_stop_v2.py`
- Replay rows: `docs/analysis/2026-07/generated/low_price_yes_sizing_fee_stop_v2/replay_rows.csv`
- Summary: `docs/analysis/2026-07/generated/low_price_yes_sizing_fee_stop_v2/summary.csv`
- Machine-readable result: `docs/analysis/2026-07/generated/low_price_yes_sizing_fee_stop_v2/summary.csv`
- The former top-level JSON duplicated all 2,520 CSV summary rows and was removed
  on 2026-08-12. Git retains its historical blob; future replays write only the
  CSV machine result plus this decision summary.
