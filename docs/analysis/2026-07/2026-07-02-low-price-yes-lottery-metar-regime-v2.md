# Low-Price YES Lottery METAR/Regime v2

Generated: 2026-07-02T09:48:23+00:00

## Verdict

`inconclusive` for live or size-up.  Waiting for intraday METAR/regime evidence did not improve the low-price YES lottery idea in a robust way.

The narrow tag that looked best by point estimate is:

```text
BUY hotter-tail YES
ask 0.05..0.20
first trigger per city-date
day_regime == day_open_runway
solar_window == late_morning
wind_regime == light_wind
paper sizing for research: $1/order
```

It is only a case label, not a selector.  Full-window point ROI is positive, but holdout is negative and the CI crosses zero.  The wider bucket check below is the more important result.

```text
significance=FAIL
baseline=FAIL versus v1 early-entry selector
forward/holdout=FAIL
conclusion=inconclusive; keep shadow telemetry only
```

## Evidence Window

- Atlas file: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`.
- Atlas rows: 13,173, target_date 2026-05-19..2026-06-29.
- Low-price evaluable rows: 368, target_date 2026-05-20..2026-06-26, ask `0.05..0.20`.
- Strategy grain: first trigger per `rule + city + target_date`; PnL assumes fixed `$1` cost per selected row.
- Holdout split: `2026-06-21` onward. Recent split: `2026-06-08` onward.
- This is historical/shadow research from expression/regime rows, not `live_real` PnL.

## Backtest Summary

| rule | rows | dates | cities | win | avg ask | ROI | CI low | CI high | top-trade removed | holdout rows | holdout ROI | recent rows | recent ROI | $1/order PnL/day | max daily loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| late_morning | 97 | 36 | 23 | +8.2% | 0.106 | -20.5% | -73.6% | +44.3% | -37.0% | 11 | -1.6% | 27 | +23.6% | $-0.55 | $-8.00 |
| active_warming | 134 | 37 | 31 | +8.2% | 0.104 | -33.8% | -71.5% | +10.7% | -44.5% | 12 | -9.8% | 35 | -9.6% | $-1.22 | $-9.00 |
| open_late_lightwind | 40 | 27 | 18 | +12.5% | 0.092 | +26.3% | -70.6% | +161.5% | -13.2% | 7 | -20.6% | 12 | +134.2% | $+0.39 | $-3.00 |
| open_gap_ge1_late | 51 | 31 | 22 | +9.8% | 0.092 | -1.0% | -79.6% | +105.9% | -32.3% | 8 | -30.6% | 16 | +75.7% | $-0.02 | $-4.00 |
| non_capped_non_busted | 114 | 34 | 28 | +8.8% | 0.098 | -15.9% | -68.3% | +47.1% | -29.9% | 17 | -36.4% | 34 | +36.8% | $-0.53 | $-10.00 |
| all_low_price_05_20 | 171 | 37 | 31 | +5.8% | 0.102 | -43.9% | -78.2% | -3.9% | -53.4% | 17 | -36.4% | 50 | -6.9% | $-2.03 | $-10.00 |
| day_open_runway | 72 | 33 | 26 | +8.3% | 0.089 | -22.1% | -79.0% | +53.7% | -44.5% | 9 | -38.3% | 20 | +40.5% | $-0.48 | $-6.00 |
| open_runway_active_warming | 61 | 30 | 25 | +8.2% | 0.095 | -47.8% | -87.6% | -0.8% | -59.8% | 9 | -38.3% | 16 | -28.5% | $-0.97 | $-5.00 |
| forecast_gap_ge_1 | 99 | 33 | 28 | +9.1% | 0.096 | -8.5% | -66.4% | +63.4% | -24.6% | 10 | -44.4% | 23 | +79.4% | $-0.25 | $-10.00 |
| open_or_marginal_runway | 108 | 34 | 28 | +8.3% | 0.096 | -16.1% | -69.7% | +49.9% | -30.9% | 11 | -49.5% | 28 | +47.4% | $-0.51 | $-10.00 |
| open_gap_ge1_humid | 12 | 10 | 11 | +16.7% | 0.079 | +86.7% | -100.0% | +407.3% | -30.1% | 2 | -100.0% | 4 | -100.0% | $+1.04 | $-2.00 |
| humid_convective | 28 | 21 | 15 | +10.7% | 0.094 | +27.0% | -100.0% | +184.5% | -22.8% | 2 | -100.0% | 11 | +19.6% | $+0.36 | $-3.00 |
| southern_maritime | 35 | 21 | 7 | +11.4% | 0.094 | +20.6% | -79.8% | +154.0% | -19.1% | 4 | -100.0% | 10 | +31.6% | $+0.34 | $-3.00 |
| false_fade_risk | 28 | 19 | 14 | +7.1% | 0.100 | +12.0% | -100.0% | +169.6% | -45.5% | 1 | -100.0% | 11 | +51.5% | $+0.18 | $-3.00 |
| open_runway_false_fade | 7 | 7 | 5 | +28.6% | 0.076 | +348.2% | -100.0% | +852.4% | +145.1% | 0 |  | 3 | +455.6% | $+3.48 | $-1.00 |

## Wider Bucket Check

To avoid over-reading a tiny hard gate, v2 also uses a loose additive regime score: open/marginal day, forecast runway >= 1, late morning, false-fade/fresh-high, humid-convective, light wind, and not capped/busted.

| score bucket | rows | dates | cities | win | avg ask | ROI | CI low | CI high | top-trade removed | holdout rows | holdout ROI | recent rows | recent ROI | max daily loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| regime_score_ge_0 | 171 | 37 | 31 | +5.8% | 0.102 | -43.9% | -78.2% | -3.9% | -53.4% | 17 | -36.4% | 50 | -6.9% | $-10.00 |
| regime_score_ge_1 | 167 | 37 | 31 | +6.0% | 0.103 | -42.6% | -77.8% | -1.3% | -52.3% | 17 | -36.4% | 48 | -3.1% | $-10.00 |
| regime_score_ge_2 | 135 | 37 | 28 | +8.1% | 0.102 | -24.1% | -71.9% | +34.2% | -35.9% | 17 | -36.4% | 41 | +13.5% | $-10.00 |
| regime_score_ge_3 | 117 | 36 | 28 | +8.5% | 0.099 | -18.1% | -69.6% | +45.7% | -31.7% | 14 | -22.7% | 34 | +36.8% | $-10.00 |
| regime_score_ge_4 | 108 | 34 | 27 | +8.3% | 0.094 | -15.8% | -69.8% | +49.2% | -30.6% | 12 | -53.7% | 30 | +37.5% | $-10.00 |
| regime_score_ge_5 | 67 | 30 | 23 | +11.9% | 0.095 | +25.7% | -58.3% | +128.1% | +2.4% | 8 | -30.6% | 18 | +129.2% | $-5.00 |
| regime_score_ge_6 | 22 | 15 | 14 | +13.6% | 0.092 | +77.6% | -100.0% | +327.8% | +6.7% | 3 | -100.0% | 5 | +233.3% | $-3.00 |
| regime_score_ge_7 | 3 | 3 | 3 | +33.3% | 0.091 | +390.2% | -100.0% | +1370.6% | -100.0% | 0 |  | 0 |  | $-1.00 |

This is why the narrow tag is not actionable: `score>=4` still has 108 rows but ROI is -15.8%; `score>=5` turns positive at 67 rows but holdout is -30.6%; `score>=6` is already only 22 rows and fails holdout completely.

## Best v2 Candidate Details

| period | rows | dates | cities | wins | win | avg ask | cost | PnL | ROI | CI low | CI high | losing days | <= -50% days | max daily loss | top-trade removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | 40 | 27 | 18 | 5 | +12.5% | 0.092 | $+40.00 | $+10.50 | +26.3% | -70.6% | +161.5% | 22 | 22 | $-3.00 | -13.2% |
| holdout_2026_06_21_plus | 7 | 5 | 3 | 1 | +14.3% | 0.122 | $+7.00 | $-1.44 | -20.6% | -100.0% | +108.3% | 4 | 4 | $-2.00 | -100.0% |
| recent_2026_06_08_plus | 12 | 10 | 5 | 3 | +25.0% | 0.105 | $+12.00 | $+16.10 | +134.2% | -100.0% | +458.8% | 7 | 7 | $-2.00 | +4.0% |
| train_pre_2026_06_21 | 33 | 22 | 18 | 4 | +12.1% | 0.085 | $+33.00 | $+11.95 | +36.2% | -80.3% | +200.4% | 18 | 18 | $-3.00 | -11.6% |

At `$1/order`, `open_late_lightwind` averages 1.48 trades per active day and $+0.39 per active day in-sample.  Holdout is 7 rows with -20.6% ROI, so it is not live-confirmed.

Daily PnL for the best v2 tag:

| date | rows | wins | cost | PnL | ROI |
| --- | --- | --- | --- | --- | --- |
| 2026-05-20 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| 2026-05-21 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-05-22 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| 2026-05-23 | 2 | 1 | $+2.00 | $+5.69 | +284.6% |
| 2026-05-24 | 1 | 1 | $+1.00 | $+13.71 | +1370.6% |
| 2026-05-26 | 3 | 0 | $+3.00 | $-3.00 | -100.0% |
| 2026-05-27 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| 2026-05-28 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-05-29 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-05-30 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-05-31 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| 2026-06-01 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-06-02 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| 2026-06-03 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| 2026-06-04 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-06-05 | 3 | 0 | $+3.00 | $-3.00 | -100.0% |
| 2026-06-06 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-06-09 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-06-10 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-06-12 | 1 | 1 | $+1.00 | $+4.88 | +488.2% |
| 2026-06-15 | 1 | 1 | $+1.00 | $+15.67 | +1566.7% |
| 2026-06-18 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-06-21 | 2 | 1 | $+2.00 | $+3.56 | +177.8% |
| 2026-06-22 | 2 | 0 | $+2.00 | $-2.00 | -100.0% |
| 2026-06-23 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-06-24 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |
| 2026-06-26 | 1 | 0 | $+1.00 | $-1.00 | -100.0% |

## v1 Reference

This v2 is not an upgrade over the current early-entry v1 selector.  The closest v1 reference is `edge>=0.20 && ask 0.05..0.20`, one city-date, `sizing=min($5, ask*25 shares)`.

| window | rows | dates | cities | win | avg ask | ROI | CI low | CI high | top-trade removed | max daily loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical | 457 | 50 | 48 | +13.6% | 0.105 | +28.8% | -2.9% | +64.5% | +26.9% | $-35.60 |
| historical_recent | 226 | 19 | 43 | +12.8% | 0.105 | +22.8% | -18.3% | +66.1% | +18.9% | $-35.60 |
| forward | 19 | 3 | 17 | +15.8% | 0.100 | +58.5% | -15.1% | +400.0% | +8.8% | $-4.44 |

The v1 row source is earlier forecast/model edge.  The v2 row source is later intraday atlas state.  They are related strategy families, but not the same denominator.

## Interpretation

- The broad intraday version is worse: `all_low_price_05_20` has 171 rows, win +5.8%, avg ask 0.102, ROI -43.9%.
- The broad score test is the main answer to the over-filtering concern: there is no wide positive bucket.  Positive point estimates appear only after the denominator gets thin.
- The plausible mechanism pocket `open_late_lightwind` improves full-window ROI to +26.3%, but holdout stays negative and daily losses are frequent.
- The positive v2 pockets look like weather complexity / late light-wind runway tags, not a clean forecast-bias alpha.  Current evidence cannot separate true alpha from a few city-date lottery hits.
- This argues against moving the live v1 selector later just to wait for METAR/regime.  The v1 edge appears to be mostly forecast/model/market mispricing at early snapshots; the intraday regime layer is better used as shadow attribution for now.

## Contributions

| rule | dimension | level | rows | dates | cities | win | avg ask | PnL | ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_low_price_05_20 | city | SaoPaulo | 3 | 3 | 1 | +100.0% | 0.091 | $+32.56 | +1085.2% |
| all_low_price_05_20 | city | Taipei | 12 | 12 | 1 | +16.7% | 0.112 | $+7.97 | +66.4% |
| all_low_price_05_20 | city | Jeddah | 23 | 23 | 1 | +13.0% | 0.118 | $+5.10 | +22.2% |
| all_low_price_05_20 | city | Austin | 6 | 6 | 1 | +16.7% | 0.090 | $-0.44 | -7.4% |
| all_low_price_05_20 | city | SanFrancisco | 9 | 9 | 1 | +11.1% | 0.111 | $-2.33 | -25.9% |
| all_low_price_05_20 | city | Atlanta | 3 | 3 | 1 | +0.0% | 0.112 | $-3.00 | -100.0% |
| all_low_price_05_20 | city | Busan | 6 | 6 | 1 | +0.0% | 0.123 | $-6.00 | -100.0% |
| all_low_price_05_20 | city | Chengdu | 4 | 4 | 1 | +0.0% | 0.101 | $-4.00 | -100.0% |
| all_low_price_05_20 | city | Chongqing | 6 | 6 | 1 | +0.0% | 0.111 | $-6.00 | -100.0% |
| all_low_price_05_20 | city | Dallas | 4 | 4 | 1 | +0.0% | 0.071 | $-4.00 | -100.0% |
| all_low_price_05_20 | city | Denver | 3 | 3 | 1 | +0.0% | 0.103 | $-3.00 | -100.0% |
| all_low_price_05_20 | city | Guangzhou | 10 | 10 | 1 | +0.0% | 0.095 | $-10.00 | -100.0% |
| all_low_price_05_20 | city | Helsinki | 7 | 7 | 1 | +0.0% | 0.106 | $-7.00 | -100.0% |
| all_low_price_05_20 | city | Houston | 7 | 7 | 1 | +0.0% | 0.090 | $-7.00 | -100.0% |
| all_low_price_05_20 | city | Istanbul | 8 | 8 | 1 | +0.0% | 0.092 | $-8.00 | -100.0% |
| all_low_price_05_20 | city | Karachi | 16 | 16 | 1 | +0.0% | 0.131 | $-16.00 | -100.0% |
| all_low_price_05_20 | city | LA | 9 | 9 | 1 | +0.0% | 0.101 | $-9.00 | -100.0% |
| all_low_price_05_20 | city | Manila | 6 | 6 | 1 | +0.0% | 0.088 | $-6.00 | -100.0% |
| all_low_price_05_20 | city | Munich | 5 | 5 | 1 | +0.0% | 0.081 | $-5.00 | -100.0% |
| all_low_price_05_20 | city | Seattle | 4 | 4 | 1 | +0.0% | 0.050 | $-4.00 | -100.0% |
| all_low_price_05_20 | city | Shanghai | 3 | 3 | 1 | +0.0% | 0.107 | $-3.00 | -100.0% |
| all_low_price_05_20 | city | Tokyo | 5 | 5 | 1 | +0.0% | 0.096 | $-5.00 | -100.0% |
| all_low_price_05_20 | city | Wuhan | 3 | 3 | 1 | +0.0% | 0.073 | $-3.00 | -100.0% |
| all_low_price_05_20 | city_family | southern_or_maritime | 35 | 21 | 7 | +11.4% | 0.094 | $+7.22 | +20.6% |
| all_low_price_05_20 | city_family | continental_dry_hot | 55 | 32 | 7 | +7.3% | 0.112 | $-21.34 | -38.8% |
| all_low_price_05_20 | city_family | humid_low_latitude | 66 | 29 | 12 | +3.0% | 0.102 | $-46.03 | -69.7% |
| all_low_price_05_20 | city_family | europe_cloud_break | 15 | 14 | 5 | +0.0% | 0.088 | $-15.00 | -100.0% |
| all_low_price_05_20 | day_regime | day_marginal_runway | 36 | 22 | 19 | +8.3% | 0.110 | $-1.47 | -4.1% |
| all_low_price_05_20 | day_regime | day_space_unknown | 6 | 4 | 6 | +16.7% | 0.126 | $-0.74 | -12.3% |
| all_low_price_05_20 | day_regime | day_open_runway | 72 | 33 | 26 | +8.3% | 0.089 | $-15.94 | -22.1% |
| all_low_price_05_20 | day_regime | day_forecast_busted | 28 | 17 | 14 | +0.0% | 0.117 | $-28.00 | -100.0% |
| all_low_price_05_20 | day_regime | day_forecast_capped | 29 | 17 | 14 | +0.0% | 0.106 | $-29.00 | -100.0% |
| all_low_price_05_20 | forecast_source | open_meteo_live_gfs | 7 | 4 | 6 | +14.3% | 0.098 | $-1.74 | -24.8% |
| all_low_price_05_20 | forecast_source | gfs_seamless | 153 | 31 | 30 | +5.2% | 0.100 | $-67.97 | -44.4% |
| all_low_price_05_20 | forecast_source | open_meteo_live_ecmwf | 11 | 7 | 5 | +9.1% | 0.133 | $-5.44 | -49.5% |
| all_low_price_05_20 | intraday_state | false_fade_risk | 14 | 10 | 11 | +14.3% | 0.094 | $+17.37 | +124.1% |
| all_low_price_05_20 | intraday_state | fresh_high | 28 | 18 | 16 | +3.6% | 0.112 | $-14.84 | -53.0% |
| all_low_price_05_20 | intraday_state | active_warming | 119 | 37 | 29 | +5.9% | 0.102 | $-67.68 | -56.9% |
| all_low_price_05_20 | intraday_state | mature_fade | 7 | 7 | 6 | +0.0% | 0.099 | $-7.00 | -100.0% |
| all_low_price_05_20 | moisture_cloud_regime | humid_convective_risk | 24 | 19 | 15 | +12.5% | 0.096 | $+11.56 | +48.2% |
| all_low_price_05_20 | moisture_cloud_regime | mixed_moisture | 107 | 33 | 24 | +5.6% | 0.106 | $-52.59 | -49.1% |
| all_low_price_05_20 | moisture_cloud_regime | dry_heat_inertia | 30 | 23 | 13 | +3.3% | 0.094 | $-24.12 | -80.4% |
| all_low_price_05_20 | moisture_cloud_regime | cloud_suppression | 8 | 7 | 4 | +0.0% | 0.104 | $-8.00 | -100.0% |
| all_low_price_05_20 | solar_window | afternoon_decay_window | 15 | 11 | 12 | +6.7% | 0.087 | $-1.84 | -12.3% |
| all_low_price_05_20 | solar_window | late_morning | 97 | 36 | 23 | +8.2% | 0.106 | $-19.86 | -20.5% |
| all_low_price_05_20 | solar_window | solar_peak_window | 55 | 26 | 24 | +1.8% | 0.101 | $-49.44 | -89.9% |
| all_low_price_05_20 | solar_window | evening_tail | 4 | 4 | 4 | +0.0% | 0.091 | $-4.00 | -100.0% |
| all_low_price_05_20 | wind_regime | light_wind | 112 | 35 | 29 | +6.2% | 0.098 | $-41.53 | -37.1% |
| all_low_price_05_20 | wind_regime | moderate_wind | 53 | 25 | 18 | +5.7% | 0.111 | $-27.62 | -52.1% |
| all_low_price_05_20 | wind_regime | windy_mixing_noise | 6 | 4 | 4 | +0.0% | 0.113 | $-6.00 | -100.0% |
| open_late_lightwind | city | Jeddah | 10 | 10 | 1 | +30.0% | 0.121 | $+18.10 | +181.0% |
| open_late_lightwind | city | Busan | 3 | 3 | 1 | +0.0% | 0.063 | $-3.00 | -100.0% |
| open_late_lightwind | city | SanFrancisco | 4 | 4 | 1 | +0.0% | 0.105 | $-4.00 | -100.0% |
| open_late_lightwind | city | Taipei | 3 | 3 | 1 | +0.0% | 0.090 | $-3.00 | -100.0% |
| open_late_lightwind | city_family | southern_or_maritime | 8 | 8 | 4 | +25.0% | 0.099 | $+14.40 | +180.0% |
| open_late_lightwind | city_family | continental_dry_hot | 13 | 12 | 4 | +23.1% | 0.107 | $+15.10 | +116.2% |
| open_late_lightwind | city_family | humid_low_latitude | 19 | 17 | 10 | +0.0% | 0.078 | $-19.00 | -100.0% |
| open_late_lightwind | day_regime | day_open_runway | 40 | 27 | 18 | +12.5% | 0.092 | $+10.50 | +26.3% |
| open_late_lightwind | forecast_source | gfs_seamless | 33 | 22 | 18 | +12.1% | 0.085 | $+11.95 | +36.2% |
| open_late_lightwind | forecast_source | open_meteo_live_ecmwf | 5 | 5 | 1 | +20.0% | 0.138 | $+0.56 | +11.1% |
| open_late_lightwind | intraday_state | false_fade_risk | 3 | 3 | 3 | +66.7% | 0.073 | $+28.37 | +945.8% |
| open_late_lightwind | intraday_state | active_warming | 33 | 22 | 16 | +9.1% | 0.093 | $-13.87 | -42.0% |
| open_late_lightwind | intraday_state | fresh_high | 3 | 3 | 3 | +0.0% | 0.103 | $-3.00 | -100.0% |
| open_late_lightwind | moisture_cloud_regime | humid_convective_risk | 5 | 5 | 4 | +40.0% | 0.086 | $+17.40 | +348.0% |
| open_late_lightwind | moisture_cloud_regime | mixed_moisture | 26 | 20 | 13 | +7.7% | 0.093 | $-3.78 | -14.5% |
| open_late_lightwind | moisture_cloud_regime | dry_heat_inertia | 7 | 7 | 4 | +14.3% | 0.090 | $-1.12 | -16.0% |
| open_late_lightwind | solar_window | late_morning | 40 | 27 | 18 | +12.5% | 0.092 | $+10.50 | +26.3% |
| open_late_lightwind | wind_regime | light_wind | 40 | 27 | 18 | +12.5% | 0.092 | $+10.50 | +26.3% |

## Next Practical Step

Keep the current low-price YES live test at small notional.  Add v2 shadow telemetry fields to the journal when convenient: `day_regime`, `intraday_state`, `solar_window`, `wind_regime`, `moisture_cloud_regime`, `forecast_gap_to_running_native`, and whether the row matches `open_late_lightwind`.  Do not use this as a live gate unless forward rows show positive holdout after settlement.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_lottery_metar_regime_v2.py`
- JSON summary: `docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-metar-regime-v2.json`
- Details: `docs/analysis/2026-07/generated/low_price_yes_lottery_metar_regime_v2/details.csv`
- Summary: `docs/analysis/2026-07/generated/low_price_yes_lottery_metar_regime_v2/summary.csv`
- Daily: `docs/analysis/2026-07/generated/low_price_yes_lottery_metar_regime_v2/daily.csv`
- Contributions: `docs/analysis/2026-07/generated/low_price_yes_lottery_metar_regime_v2/contributions.csv`
- Score buckets: `docs/analysis/2026-07/generated/low_price_yes_lottery_metar_regime_v2/score_buckets.csv`
