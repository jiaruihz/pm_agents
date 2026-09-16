# Reversal Archetype Selector v3

Generated: 2026-07-01

## Verdict

最佳 executable archetype `capped_or_low_runway_current_hold`：51 rows/33 dates，ROI +7.4% CI -21.8%..+39.1%，baseline ROI -26.4%，excess +33.8%。

`inconclusive`: broadened archetype search finds mechanisms worth logging, but no executable archetype clears significance+baseline+forward.

significance=FAIL baseline=FAIL forward=FAIL conclusion=inconclusive

## 数据快照

- DB: `runtime/weather.db`, fact_built_at_utc `2026-07-01T02:11:08.518593+00:00`, CLOB gate_pass=True.
- Expression matrix: 3363 rows, 39 dates, 2026-05-19..2026-06-26, 36 cities.
- Selection features are PIT/historical only: price conflict, forecast gap to running high, day/intraday/moisture/wind regime, city/source historical forecast bias.
- Excluded from selection: `forecast_error_native`, final max, final winning bracket, payoff labels.

## Archetype Summary

| period | archetype | expression | exec | rows | dates | cities | avg ask | win | ROI | CI low | CI high | baseline | excess | loss days | <=-50% days | max loss | pnl/day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | capped_or_low_runway_current_hold | buy_current_yes | True | 51 | 33 | 21 | 0.40 | +43.1% | +7.4% | -21.8% | +39.1% | -26.4% | +33.8% | 16 | 13 | $-15.00 | $+0.57 |
| full | complex_weather_current_hold | buy_current_yes | True | 54 | 27 | 27 | 0.40 | +40.7% | +2.9% | -26.4% | +31.9% | -22.4% | +25.3% | 15 | 10 | $-10.00 | $+0.29 |
| full | current_no_overconfidence_all | buy_current_yes | True | 96 | 37 | 31 | 0.40 | +40.6% | +2.5% | -19.3% | +25.4% | -23.3% | +25.8% | 18 | 9 | $-20.00 | $+0.33 |
| full | cold_or_tight_source_current_hold | buy_current_yes | True | 28 | 21 | 11 | 0.39 | +35.7% | -9.9% | -57.1% | +35.9% | -17.1% | +7.2% | 13 | 13 | $-10.00 | $-0.66 |
| full | current_yes_overconfidence_tail_escape | buy_current_bracket_no | True | 383 | 38 | 36 | 0.18 | +13.3% | -31.0% | -52.1% | -8.6% | -0.9% | -30.1% | 24 | 19 | $-70.00 | $-15.61 |
| full | hot_underforecast_higher_yes_tail | buy_higher_tail_yes | True | 3 | 3 | 3 | 0.23 | +0.0% | -100.0% | -100.0% | -100.0% |  |  | 3 | 3 | $-5.00 | $-5.00 |
| full | d1_no_overconfidence_fade_proxy | buy_d1_yes_proxy | False | 629 | 39 | 36 | 0.20 | +15.1% | -29.7% | -43.9% | -15.8% | -2.4% | -27.4% | 31 | 17 | $-90.00 | $-23.95 |
| full | d2_no_overconfidence_fade_proxy | buy_d2_yes_proxy | False | 441 | 39 | 36 | 0.13 | +7.3% | -44.5% | -65.3% | -20.3% | +0.7% | -45.2% | 32 | 23 | $-95.00 | $-25.16 |
| holdout | capped_or_low_runway_current_hold | buy_current_yes | True | 18 | 12 | 13 | 0.40 | +44.4% | +8.8% | -36.3% | +48.9% | -29.1% | +37.9% | 6 | 5 | $-5.00 | $+0.66 |
| holdout | current_no_overconfidence_all | buy_current_yes | True | 37 | 14 | 25 | 0.40 | +43.2% | +8.4% | -24.8% | +46.4% | -26.7% | +35.1% | 8 | 3 | $-12.47 | $+1.11 |
| holdout | complex_weather_current_hold | buy_current_yes | True | 21 | 9 | 16 | 0.41 | +42.9% | +5.0% | -35.1% | +48.3% | -24.6% | +29.6% | 4 | 2 | $-10.00 | $+0.58 |
| holdout | current_yes_overconfidence_tail_escape | buy_current_bracket_no | True | 136 | 14 | 34 | 0.18 | +14.7% | -19.6% | -57.6% | +20.0% | -3.2% | -16.5% | 7 | 5 | $-70.00 | $-9.54 |
| holdout | cold_or_tight_source_current_hold | buy_current_yes | True | 9 | 8 | 6 | 0.39 | +22.2% | -44.8% | -100.0% | +34.7% | +3.4% | -48.2% | 6 | 6 | $-10.00 | $-2.52 |
| holdout | hot_underforecast_higher_yes_tail | buy_higher_tail_yes | True | 1 | 1 | 1 | 0.07 | +0.0% | -100.0% |  |  |  |  | 1 | 1 | $-5.00 | $-5.00 |
| holdout | d2_no_overconfidence_fade_proxy | buy_d2_yes_proxy | False | 170 | 14 | 36 | 0.14 | +9.4% | -28.4% | -58.6% | +9.3% | -0.7% | -27.8% | 10 | 5 | $-70.00 | $-17.27 |
| holdout | d1_no_overconfidence_fade_proxy | buy_d1_yes_proxy | False | 221 | 14 | 35 | 0.20 | +14.0% | -28.7% | -53.9% | -3.8% | -1.3% | -27.4% | 10 | 6 | $-85.24 | $-22.64 |
| recent | complex_weather_current_hold | buy_current_yes | True | 8 | 3 | 8 | 0.42 | +50.0% | +22.8% | -37.3% | +137.6% | -37.2% | +60.0% | 1 | 0 | $-7.47 | $+3.04 |
| recent | current_no_overconfidence_all | buy_current_yes | True | 15 | 6 | 11 | 0.41 | +40.0% | +0.6% | -57.9% | +72.8% | -24.1% | +24.8% | 3 | 2 | $-12.47 | $+0.08 |
| recent | cold_or_tight_source_current_hold | buy_current_yes | True | 3 | 3 | 2 | 0.39 | +33.3% | -27.1% | -100.0% | +118.8% | -9.1% | -18.0% | 2 | 2 | $-5.00 | $-1.35 |
| recent | current_yes_overconfidence_tail_escape | buy_current_bracket_no | True | 51 | 6 | 23 | 0.18 | +15.7% | -41.5% | -74.6% | -2.3% | -5.6% | -35.9% | 4 | 2 | $-45.00 | $-17.62 |
| recent | capped_or_low_runway_current_hold | buy_current_yes | True | 4 | 4 | 3 | 0.41 | +0.0% | -100.0% | -100.0% | -100.0% | +23.4% | -123.4% | 4 | 4 | $-5.00 | $-5.00 |
| recent | hot_underforecast_higher_yes_tail | buy_higher_tail_yes | True | 0 | 0 | 0 |  |  |  |  |  |  |  |  |  |  |  |
| recent | d2_no_overconfidence_fade_proxy | buy_d2_yes_proxy | False | 67 | 6 | 27 | 0.15 | +10.4% | -3.1% | -67.8% | +80.8% | -1.4% | -1.7% | 3 | 2 | $-65.00 | $-1.71 |
| recent | d1_no_overconfidence_fade_proxy | buy_d1_yes_proxy | False | 78 | 6 | 29 | 0.21 | +11.5% | -51.4% | -71.5% | -33.8% | +2.2% | -53.5% | 6 | 3 | $-63.71 | $-33.39 |
| train | cold_or_tight_source_current_hold | buy_current_yes | True | 19 | 13 | 7 | 0.40 | +42.1% | +6.7% | -53.7% | +63.9% | -26.8% | +33.5% | 7 | 7 | $-10.00 | $+0.49 |
| train | capped_or_low_runway_current_hold | buy_current_yes | True | 33 | 21 | 19 | 0.40 | +42.4% | +6.6% | -31.5% | +53.4% | -25.0% | +31.6% | 10 | 8 | $-15.00 | $+0.52 |
| train | complex_weather_current_hold | buy_current_yes | True | 33 | 18 | 20 | 0.40 | +39.4% | +1.6% | -38.7% | +42.8% | -21.0% | +22.5% | 11 | 8 | $-10.00 | $+0.14 |
| train | current_no_overconfidence_all | buy_current_yes | True | 59 | 23 | 24 | 0.40 | +39.0% | -1.2% | -29.7% | +27.4% | -21.1% | +19.9% | 10 | 6 | $-20.00 | $-0.15 |
| train | current_yes_overconfidence_tail_escape | buy_current_bracket_no | True | 247 | 24 | 34 | 0.19 | +12.6% | -37.2% | -61.2% | -8.3% | +0.4% | -37.6% | 17 | 14 | $-60.00 | $-19.15 |
| train | hot_underforecast_higher_yes_tail | buy_higher_tail_yes | True | 2 | 2 | 2 | 0.31 | +0.0% | -100.0% |  |  |  |  | 2 | 2 | $-5.00 | $-5.00 |
| train | d1_no_overconfidence_fade_proxy | buy_d1_yes_proxy | False | 408 | 25 | 36 | 0.20 | +15.7% | -30.3% | -47.1% | -12.6% | -2.9% | -27.3% | 21 | 11 | $-90.00 | $-24.69 |
| train | d2_no_overconfidence_fade_proxy | buy_d2_yes_proxy | False | 271 | 25 | 35 | 0.13 | +5.9% | -54.6% | -80.2% | -20.7% | +1.5% | -56.1% | 22 | 18 | $-95.00 | $-29.58 |

## Holdout Only

| archetype | expression | exec | rows | dates | win | ROI | CI low | CI high | baseline | excess | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| capped_or_low_runway_current_hold | buy_current_yes | True | 18 | 12 | +44.4% | +8.8% | -36.3% | +48.9% | -29.1% | +37.9% | 6 | $-5.00 |
| current_no_overconfidence_all | buy_current_yes | True | 37 | 14 | +43.2% | +8.4% | -24.8% | +46.4% | -26.7% | +35.1% | 8 | $-12.47 |
| complex_weather_current_hold | buy_current_yes | True | 21 | 9 | +42.9% | +5.0% | -35.1% | +48.3% | -24.6% | +29.6% | 4 | $-10.00 |
| current_yes_overconfidence_tail_escape | buy_current_bracket_no | True | 136 | 14 | +14.7% | -19.6% | -57.6% | +20.0% | -3.2% | -16.5% | 7 | $-70.00 |
| cold_or_tight_source_current_hold | buy_current_yes | True | 9 | 8 | +22.2% | -44.8% | -100.0% | +34.7% | +3.4% | -48.2% | 6 | $-10.00 |
| hot_underforecast_higher_yes_tail | buy_higher_tail_yes | True | 1 | 1 | +0.0% | -100.0% |  |  |  |  | 1 | $-5.00 |
| d2_no_overconfidence_fade_proxy | buy_d2_yes_proxy | False | 170 | 14 | +9.4% | -28.4% | -58.6% | +9.3% | -0.7% | -27.8% | 10 | $-70.00 |
| d1_no_overconfidence_fade_proxy | buy_d1_yes_proxy | False | 221 | 14 | +14.0% | -28.7% | -53.9% | -3.8% | -1.3% | -27.4% | 10 | $-85.24 |

## City Contribution

| archetype | city | rows | dates | win | ROI | pnl | CI low | CI high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| capped_or_low_runway_current_hold | Jeddah | 5 | 5 | +80.0% | +89.2% | $+22.30 | -8.3% | +151.6% |
| capped_or_low_runway_current_hold | Warsaw | 2 | 2 | +100.0% | +177.8% | $+17.78 |  |  |
| capped_or_low_runway_current_hold | TelAviv | 2 | 2 | +100.0% | +157.1% | $+15.71 |  |  |
| capped_or_low_runway_current_hold | Manila | 2 | 2 | +100.0% | +144.0% | $+14.40 |  |  |
| capped_or_low_runway_current_hold | Austin | 3 | 3 | +66.7% | +50.7% | $+7.61 | -100.0% | +143.9% |
| capped_or_low_runway_current_hold | Chengdu | 4 | 4 | +50.0% | +33.5% | $+6.71 | -100.0% | +167.1% |
| capped_or_low_runway_current_hold | Taipei | 2 | 2 | +50.0% | +42.9% | $+4.29 |  |  |
| capped_or_low_runway_current_hold | Munich | 2 | 2 | +50.0% | +28.2% | $+2.82 |  |  |
| capped_or_low_runway_current_hold | BuenosAires | 2 | 2 | +50.0% | +25.0% | $+2.50 |  |  |
| capped_or_low_runway_current_hold | Atlanta | 2 | 2 | +50.0% | +9.9% | $+0.99 |  |  |
| capped_or_low_runway_current_hold | Busan | 3 | 3 | +33.3% | -4.8% | $-0.71 | -100.0% | +185.7% |
| capped_or_low_runway_current_hold | Dallas | 3 | 3 | +33.3% | -20.6% | $-3.10 | -100.0% | +138.1% |
| capped_or_low_runway_current_hold | Madrid | 3 | 3 | +33.3% | -20.6% | $-3.10 | -100.0% | +138.1% |
| capped_or_low_runway_current_hold | SaoPaulo | 3 | 3 | +33.3% | -29.1% | $-4.36 | -100.0% | +112.8% |
| capped_or_low_runway_current_hold | Helsinki | 2 | 2 | +0.0% | -100.0% | $-10.00 |  |  |
| capped_or_low_runway_current_hold | Istanbul | 2 | 2 | +0.0% | -100.0% | $-10.00 |  |  |
| capped_or_low_runway_current_hold | Karachi | 2 | 2 | +0.0% | -100.0% | $-10.00 |  |  |
| capped_or_low_runway_current_hold | Singapore | 2 | 2 | +0.0% | -100.0% | $-10.00 |  |  |
| capped_or_low_runway_current_hold | Ankara | 3 | 3 | +0.0% | -100.0% | $-15.00 | -100.0% | -100.0% |
| cold_or_tight_source_current_hold | CapeTown | 4 | 4 | +75.0% | +102.2% | $+20.45 | -31.3% | +176.3% |
| cold_or_tight_source_current_hold | Istanbul | 6 | 6 | +50.0% | +21.3% | $+6.40 | -64.5% | +110.9% |
| cold_or_tight_source_current_hold | NYC | 2 | 2 | +50.0% | +9.4% | $+0.94 |  |  |
| cold_or_tight_source_current_hold | SanFrancisco | 3 | 3 | +33.3% | -32.0% | $-4.80 | -100.0% | +104.1% |
| cold_or_tight_source_current_hold | Busan | 4 | 4 | +25.0% | -28.6% | $-5.71 | -100.0% | +114.3% |
| cold_or_tight_source_current_hold | Madrid | 2 | 2 | +0.0% | -100.0% | $-10.00 |  |  |
| cold_or_tight_source_current_hold | Amsterdam | 3 | 3 | +0.0% | -100.0% | $-15.00 | -100.0% | -100.0% |
| complex_weather_current_hold | CapeTown | 2 | 2 | +100.0% | +165.6% | $+16.56 |  |  |
| complex_weather_current_hold | Jeddah | 5 | 5 | +60.0% | +47.5% | $+11.88 | -56.5% | +149.1% |
| complex_weather_current_hold | TelAviv | 3 | 3 | +66.7% | +71.4% | $+10.71 | -100.0% | +170.3% |
| complex_weather_current_hold | BuenosAires | 2 | 2 | +50.0% | +42.9% | $+4.29 |  |  |
| complex_weather_current_hold | Taipei | 2 | 2 | +50.0% | +42.9% | $+4.29 |  |  |
| complex_weather_current_hold | Istanbul | 4 | 4 | +50.0% | +19.5% | $+3.90 | -100.0% | +139.0% |
| complex_weather_current_hold | Dallas | 2 | 2 | +50.0% | +19.0% | $+1.90 |  |  |
| complex_weather_current_hold | NYC | 2 | 2 | +50.0% | +9.4% | $+0.94 |  |  |
| complex_weather_current_hold | SanFrancisco | 2 | 2 | +50.0% | +2.0% | $+0.20 |  |  |
| complex_weather_current_hold | Busan | 3 | 3 | +33.3% | -4.8% | $-0.71 | -100.0% | +185.7% |
| complex_weather_current_hold | Amsterdam | 3 | 3 | +33.3% | -16.5% | $-2.47 | -100.0% | +150.6% |
| complex_weather_current_hold | Austin | 3 | 3 | +33.3% | -18.7% | $-2.80 | -100.0% | +143.9% |
| complex_weather_current_hold | SaoPaulo | 3 | 3 | +33.3% | -29.1% | $-4.36 | -100.0% | +112.8% |
| complex_weather_current_hold | Madrid | 2 | 2 | +0.0% | -100.0% | $-10.00 |  |  |
| complex_weather_current_hold | Singapore | 2 | 2 | +0.0% | -100.0% | $-10.00 |  |  |
| complex_weather_current_hold | Karachi | 3 | 3 | +0.0% | -100.0% | $-15.00 | -100.0% | -100.0% |
| current_no_overconfidence_all | CapeTown | 5 | 5 | +80.0% | +113.1% | $+28.27 | +6.2% | +172.9% |
| current_no_overconfidence_all | Warsaw | 2 | 2 | +100.0% | +177.8% | $+17.78 |  |  |
| current_no_overconfidence_all | Jeddah | 6 | 6 | +66.7% | +57.7% | $+17.30 | -30.6% | +142.3% |
| current_no_overconfidence_all | Chongqing | 3 | 3 | +66.7% | +88.1% | $+13.21 | -100.0% | +185.7% |
| current_no_overconfidence_all | TelAviv | 3 | 3 | +66.7% | +71.4% | $+10.71 | -100.0% | +170.3% |
| current_no_overconfidence_all | Manila | 3 | 3 | +66.7% | +62.7% | $+9.40 | -100.0% | +150.0% |
| current_no_overconfidence_all | BuenosAires | 4 | 4 | +50.0% | +33.9% | $+6.79 | -100.0% | +167.9% |
| current_no_overconfidence_all | Istanbul | 6 | 6 | +50.0% | +21.3% | $+6.40 | -64.5% | +110.9% |
| current_no_overconfidence_all | Taipei | 2 | 2 | +50.0% | +42.9% | $+4.29 |  |  |
| current_no_overconfidence_all | Munich | 2 | 2 | +50.0% | +28.2% | $+2.82 |  |  |
| current_no_overconfidence_all | Wellington | 2 | 2 | +50.0% | +28.2% | $+2.82 |  |  |
| current_no_overconfidence_all | Austin | 4 | 4 | +50.0% | +13.1% | $+2.61 | -100.0% | +126.1% |
| current_no_overconfidence_all | Atlanta | 2 | 2 | +50.0% | +9.9% | $+0.99 |  |  |
| current_no_overconfidence_all | NYC | 2 | 2 | +50.0% | +9.4% | $+0.94 |  |  |
| current_no_overconfidence_all | Amsterdam | 5 | 5 | +40.0% | +1.4% | $+0.35 | -100.0% | +104.0% |
| current_no_overconfidence_all | Dallas | 3 | 3 | +33.3% | -20.6% | $-3.10 | -100.0% | +138.1% |
| current_no_overconfidence_all | Chengdu | 6 | 6 | +33.3% | -11.0% | $-3.29 | -100.0% | +85.2% |
| current_no_overconfidence_all | SaoPaulo | 3 | 3 | +33.3% | -29.1% | $-4.36 | -100.0% | +112.8% |
| current_no_overconfidence_all | SanFrancisco | 3 | 3 | +33.3% | -32.0% | $-4.80 | -100.0% | +104.1% |
| current_no_overconfidence_all | Busan | 4 | 4 | +25.0% | -28.6% | $-5.71 | -100.0% | +114.3% |
| current_no_overconfidence_all | Madrid | 4 | 4 | +25.0% | -40.5% | $-8.10 | -100.0% | +78.6% |
| current_no_overconfidence_all | Helsinki | 2 | 2 | +0.0% | -100.0% | $-10.00 |  |  |
| current_no_overconfidence_all | Seattle | 5 | 5 | +20.0% | -47.4% | $-11.84 | -100.0% | +57.9% |
| current_no_overconfidence_all | Karachi | 3 | 3 | +0.0% | -100.0% | $-15.00 | -100.0% | -100.0% |
| current_no_overconfidence_all | Singapore | 3 | 3 | +0.0% | -100.0% | $-15.00 | -100.0% | -100.0% |
| current_no_overconfidence_all | Ankara | 4 | 4 | +0.0% | -100.0% | $-20.00 | -100.0% | -100.0% |
| current_yes_overconfidence_tail_escape | Wellington | 21 | 21 | +19.0% | +62.0% | $+65.07 | -65.7% | +245.1% |
| current_yes_overconfidence_tail_escape | Chengdu | 17 | 17 | +23.5% | +27.9% | $+23.69 | -82.2% | +153.3% |
| current_yes_overconfidence_tail_escape | LA | 4 | 4 | +50.0% | +112.4% | $+22.48 | -100.0% | +324.8% |
| current_yes_overconfidence_tail_escape | Seattle | 8 | 8 | +37.5% | +53.1% | $+21.23 | -65.3% | +206.9% |
| current_yes_overconfidence_tail_escape | Shanghai | 21 | 21 | +9.5% | +12.4% | $+13.06 | -100.0% | +184.4% |
| current_yes_overconfidence_tail_escape | Guangzhou | 13 | 13 | +30.8% | +15.5% | $+10.06 | -78.0% | +128.9% |
| current_yes_overconfidence_tail_escape | Helsinki | 6 | 6 | +16.7% | +19.0% | $+5.71 | -100.0% | +257.1% |
| current_yes_overconfidence_tail_escape | Jeddah | 23 | 23 | +13.0% | +3.9% | $+4.52 | -100.0% | +150.8% |
| current_yes_overconfidence_tail_escape | Warsaw | 7 | 7 | +28.6% | +4.7% | $+1.63 | -100.0% | +147.5% |
| current_yes_overconfidence_tail_escape | Miami | 3 | 3 | +33.3% | -2.0% | $-0.29 | -100.0% | +194.1% |
| current_yes_overconfidence_tail_escape | Houston | 4 | 4 | +25.0% | -24.0% | $-4.80 | -100.0% | +128.0% |
| current_yes_overconfidence_tail_escape | NYC | 14 | 14 | +28.6% | -8.1% | $-5.66 | -78.4% | +76.0% |

## Interpretation

- Losing days are expected for all binary-token reversal sleeves; they are reported as risk, not used as a hard rejection.
- The broad current-NO-overconfidence shape remains the main executable expression, but PIT-only variants are not yet stable enough.
- Source/city bias and complex weather are useful tags for forward logging. They do not yet identify a confirmed live selector.
- d1/d2 YES rows are proxy diagnostics because YES ask is inferred from NO bid; they must not be promoted without real YES ask/depth.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_reversal_archetype_selector_v3.py`
- JSON: `docs/analysis/2026-07/2026-07-01-reversal-archetype-selector-v3.json`
- Summary CSV: `docs/analysis/2026-07/generated/reversal_archetype_selector_v3/archetype_summary.csv`
- Trades CSV: `docs/analysis/2026-07/generated/reversal_archetype_selector_v3/archetype_trades.csv`
