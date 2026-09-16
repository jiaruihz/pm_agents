# Low-Price YES Integrated Tail v2

Generated: 2026-07-02T15:17:49+00:00

## Verdict

`shadow_candidate` for telemetry only; no live selector/size change.

The core finding is boring but useful: source-aware v3 remains the best broad forecast-tail sleeve, while METAR/regime confirmation still does not improve the same denominator.  Station-bias and p_cal are valuable as forward tags, but city-diagnostic p_cal is too likely to be city/source memory to promote.

Recommended V2 shadow record:

```text
base candidate: V1 no-dust low-price YES, edge>=0.20, ask 0.05..0.20
shadow tags: source_aware_v3, station_bias_p90_high, station_hot_tail_high,
             p_cal_no_city/city_diag EV, live METAR observation summary,
             simplified METAR regime score and blocker/executable status
shadow decision: diagnostic only; no order routing, no size-up
```

```text
significance=PARTIAL (source-aware historical CI passes; integrated no-city CI does not)
baseline=PARTIAL (source-aware improves headline; no-city integrated not stable vs complement)
forward=PARTIAL/LOW_N (6/27..6/30 only 19 baseline rows)
conclusion=shadow_candidate; keep V1 tiny live, add V2 shadow telemetry only
```

## Data Snapshot

- Sync/rebuild: scripts/ops/sync_weather_remote.sh + scripts/weather_dashboard/run_stack.sh completed on 2026-07-02
- Fact signal candidates: 42652 rows, event_date 2026-05-05..2026-07-04, fact_built_at_utc 2026-07-02T15:12:46Z
- Fact trades: 4414 rows, target_date 2026-05-06..2026-07-01, fact_built_at_utc 2026-07-02T15:12:24Z
- Base denominator rows: 476; dates 2026-05-06..2026-06-30; cities 48.
- METAR/regime same-bracket join coverage: 3.8%. Missing METAR join means no same city-date-bracket intraday low-price tail row in v2 atlas.
- Station thresholds are train-only q66: bias_p90>=3.900, hot_tail_pct>=0.621.

## Main A/B

| strategy | period | rows | dates | cities | win | avg ask | ROI | CI low | CI high | top5 removed | losing days | <= -50% days | max daily loss | METAR join |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_v1_all | forward_2026_06_27_30 | 19 | 3 | 17 | +15.8% | 0.100 | +65.9% | -48.1% | +809.1% | -100.0% | 1 | 0 | $-5.29 | 0.000 |
| baseline_v1_all | holdout_2026_06_21_26 | 74 | 6 | 32 | +16.2% | 0.109 | +43.9% | -37.7% | +133.2% | -34.5% | 3 | 3 | $-7.94 | 0.041 |
| baseline_v1_all | train_pre_2026_06_21 | 383 | 44 | 48 | +13.1% | 0.105 | +20.4% | -14.4% | +59.9% | +1.2% | 23 | 15 | $-13.00 | 0.039 |
| source_aware_v3 | forward_2026_06_27_30 | 11 | 3 | 10 | +27.3% | 0.112 | +186.6% | +14.3% | +809.1% | -100.0% | 0 | 0 | $+0.71 | 0.000 |
| source_aware_v3 | holdout_2026_06_21_26 | 43 | 6 | 24 | +18.6% | 0.116 | +55.8% | -60.7% | +199.1% | -54.5% | 4 | 2 | $-4.00 | 0.070 |
| source_aware_v3 | train_pre_2026_06_21 | 235 | 44 | 47 | +16.6% | 0.114 | +44.4% | -0.3% | +97.7% | +13.8% | 21 | 18 | $-9.00 | 0.043 |
| source_station_no_city_score_ge_2 | forward_2026_06_27_30 | 12 | 3 | 11 | +16.7% | 0.094 | +115.1% | -100.0% | +1718.2% | -100.0% | 1 | 1 | $-6.00 | 0.000 |
| source_station_no_city_score_ge_2 | holdout_2026_06_21_26 | 51 | 6 | 27 | +13.7% | 0.104 | +21.4% | -51.3% | +112.8% | -75.6% | 4 | 2 | $-6.07 | 0.039 |
| source_station_no_city_score_ge_2 | train_pre_2026_06_21 | 253 | 44 | 44 | +15.8% | 0.109 | +43.4% | +1.9% | +91.2% | +14.6% | 20 | 16 | $-9.00 | 0.047 |
| source_station_no_city_score_ge_3 | forward_2026_06_27_30 | 7 | 3 | 6 | +28.6% | 0.099 | +268.8% | -100.0% | +1718.2% | -100.0% | 1 | 1 | $-4.00 | 0.000 |
| source_station_no_city_score_ge_3 | holdout_2026_06_21_26 | 21 | 5 | 9 | +19.0% | 0.096 | +47.2% | -43.8% | +161.1% | -100.0% | 1 | 1 | $-5.00 | 0.048 |
| source_station_no_city_score_ge_3 | train_pre_2026_06_21 | 126 | 42 | 24 | +17.5% | 0.110 | +61.7% | +8.7% | +122.9% | +5.5% | 23 | 23 | $-7.00 | 0.032 |
| source_station_citydiag_score_ge_3_diag_only | forward_2026_06_27_30 | 5 | 3 | 4 | +40.0% | 0.111 | +416.3% | -100.0% | +1718.2% |  | 1 | 1 | $-3.00 | 0.000 |
| source_station_citydiag_score_ge_3_diag_only | holdout_2026_06_21_26 | 13 | 5 | 7 | +30.8% | 0.116 | +137.7% | +13.6% | +269.9% | -100.0% | 1 | 1 | $-2.00 | 0.077 |
| source_station_citydiag_score_ge_3_diag_only | train_pre_2026_06_21 | 99 | 39 | 22 | +22.2% | 0.120 | +105.8% | +40.2% | +180.7% | +35.9% | 20 | 20 | $-5.00 | 0.030 |
| source_station_citydiag_score_ge_4_diag_only | forward_2026_06_27_30 | 2 | 2 | 2 | +50.0% | 0.110 | +809.1% |  |  |  | 1 | 1 | $-1.00 | 0.000 |
| source_station_citydiag_score_ge_4_diag_only | holdout_2026_06_21_26 | 5 | 4 | 3 | +20.0% | 0.088 | +172.1% | -100.0% | +920.4% |  | 3 | 3 | $-2.00 | 0.200 |
| source_station_citydiag_score_ge_4_diag_only | train_pre_2026_06_21 | 30 | 23 | 7 | +33.3% | 0.117 | +216.0% | +45.9% | +412.6% | +25.5% | 14 | 14 | $-2.00 | 0.033 |
| source_aware_and_metar_score_ge_4 | forward_2026_06_27_30 | 0 | 0 | 0 |  |  |  |  |  |  |  |  |  |  |
| source_aware_and_metar_score_ge_4 | holdout_2026_06_21_26 | 3 | 2 | 3 | +0.0% | 0.097 | -100.0% |  |  |  | 2 | 2 | $-2.00 | 1.000 |
| source_aware_and_metar_score_ge_4 | train_pre_2026_06_21 | 5 | 4 | 5 | +20.0% | 0.133 | +60.0% | -100.0% | +500.0% |  | 3 | 3 | $-2.00 | 1.000 |
| integrated_with_metar_score_ge_4 | forward_2026_06_27_30 | 7 | 3 | 6 | +28.6% | 0.099 | +268.8% | -100.0% | +1718.2% | -100.0% | 1 | 1 | $-4.00 | 0.000 |
| integrated_with_metar_score_ge_4 | holdout_2026_06_21_26 | 22 | 5 | 10 | +18.2% | 0.099 | +40.5% | -46.5% | +144.8% | -100.0% | 1 | 1 | $-5.00 | 0.091 |
| integrated_with_metar_score_ge_4 | train_pre_2026_06_21 | 129 | 42 | 25 | +17.8% | 0.109 | +69.7% | +10.4% | +139.8% | +13.7% | 23 | 23 | $-7.00 | 0.054 |
| metar_open_late_lightwind | forward_2026_06_27_30 | 0 | 0 | 0 |  |  |  |  |  |  |  |  |  |  |
| metar_open_late_lightwind | holdout_2026_06_21_26 | 2 | 2 | 2 | +0.0% | 0.107 | -100.0% |  |  |  | 2 | 2 | $-1.00 | 1.000 |
| metar_open_late_lightwind | train_pre_2026_06_21 | 5 | 5 | 4 | +40.0% | 0.098 | +363.0% | -100.0% | +969.1% |  | 3 | 3 | $-1.00 | 1.000 |

## Full-Window Rank

| strategy | rows | dates | cities | win | avg ask | ROI | CI low | CI high | top5 removed | $/active day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| source_station_citydiag_score_ge_4_diag_only | 37 | 29 | 7 | +32.4% | 0.113 | +242.1% | +74.1% | +434.2% | +52.4% | $+3.09 |
| metar_open_late_lightwind | 7 | 7 | 6 | +28.6% | 0.101 | +230.7% | -100.0% | +675.8% | -100.0% | $+2.31 |
| metar_score_ge_5 | 8 | 8 | 7 | +25.0% | 0.112 | +189.4% | -100.0% | +578.8% | -100.0% | $+1.89 |
| integrated_with_metar_score_ge_5 | 63 | 38 | 14 | +22.2% | 0.123 | +123.3% | +16.6% | +245.0% | +8.3% | $+2.04 |
| source_station_citydiag_score_ge_3_diag_only | 117 | 47 | 22 | +23.9% | 0.119 | +122.6% | +59.1% | +195.8% | +60.3% | $+3.05 |
| pcal_city_diag_ev_ge_0_5_diag_only | 185 | 50 | 29 | +21.6% | 0.106 | +103.3% | +43.0% | +170.4% | +63.7% | $+3.82 |
| integrated_with_metar_score_ge_4 | 158 | 50 | 26 | +18.4% | 0.107 | +74.4% | +17.7% | +138.1% | +27.3% | $+2.35 |
| station_bias_p90_high | 157 | 51 | 20 | +15.3% | 0.097 | +69.6% | +10.0% | +137.9% | +21.6% | $+2.14 |
| source_station_no_city_score_ge_3 | 154 | 50 | 24 | +18.2% | 0.108 | +69.1% | +17.4% | +126.2% | +20.5% | $+2.13 |
| pcal_city_diag_ev_ge_0_2_diag_only | 326 | 53 | 42 | +16.3% | 0.105 | +53.1% | +13.0% | +97.1% | +29.7% | $+3.27 |
| source_aware_v3 | 289 | 53 | 47 | +17.3% | 0.114 | +51.5% | +8.0% | +100.2% | +25.7% | $+2.81 |
| station_hot_tail_high | 161 | 53 | 17 | +16.1% | 0.106 | +48.8% | -3.0% | +107.5% | +3.7% | $+1.48 |
| source_station_no_city_score_ge_2 | 316 | 53 | 44 | +15.5% | 0.108 | +42.6% | +5.5% | +84.8% | +18.7% | $+2.54 |
| pcal_no_city_ev_ge_0_5 | 130 | 47 | 22 | +14.6% | 0.106 | +30.6% | -27.4% | +94.3% | -20.3% | $+0.85 |
| baseline_v1_all | 476 | 53 | 48 | +13.7% | 0.105 | +25.9% | -6.2% | +61.7% | +9.7% | $+2.33 |
| pcal_no_city_ev_ge_0_2 | 392 | 53 | 46 | +13.5% | 0.106 | +24.3% | -9.6% | +62.4% | +4.5% | $+1.80 |
| source_aware_and_metar_score_ge_4 | 8 | 6 | 8 | +12.5% | 0.119 | +0.0% | -100.0% | +242.9% | -100.0% | $+0.00 |
| metar_joined_source_aware | 13 | 10 | 10 | +7.7% | 0.136 | -38.5% | -100.0% | +118.2% | -100.0% | $-0.50 |

## Complement Check

A selector is interesting only if selected rows beat the rows it leaves behind on the same denominator.  This is where no-city p_cal and station-bias tags still look unstable.

### Holdout 2026-06-21..26

| strategy | selected | selected ROI | complement | complement ROI | delta |
| --- | --- | --- | --- | --- | --- |
| integrated_with_metar_score_ge_5 | 5 | +172.1% | 69 | +34.6% | +137.5% |
| source_station_citydiag_score_ge_4_diag_only | 5 | +172.1% | 69 | +34.6% | +137.5% |
| source_station_citydiag_score_ge_3_diag_only | 13 | +137.7% | 61 | +23.9% | +113.8% |
| pcal_city_diag_ev_ge_0_5_diag_only | 28 | +87.2% | 46 | +17.6% | +69.6% |
| pcal_city_diag_ev_ge_0_2_diag_only | 55 | +52.0% | 19 | +20.6% | +31.4% |
| source_aware_v3 | 43 | +55.8% | 31 | +27.5% | +28.3% |
| source_station_no_city_score_ge_3 | 21 | +47.2% | 53 | +42.6% | +4.5% |
| integrated_with_metar_score_ge_4 | 22 | +40.5% | 52 | +45.4% | -4.9% |
| station_hot_tail_high | 23 | +34.4% | 51 | +48.2% | -13.9% |
| station_bias_p90_high | 20 | +27.9% | 54 | +49.9% | -22.0% |
| pcal_no_city_ev_ge_0_5 | 18 | +19.4% | 56 | +51.8% | -32.4% |
| source_station_no_city_score_ge_2 | 51 | +21.4% | 23 | +93.8% | -72.4% |
| pcal_no_city_ev_ge_0_2 | 63 | +32.7% | 11 | +108.3% | -75.6% |
| metar_score_ge_5 | 2 | -100.0% | 72 | +47.9% | -147.9% |
| metar_open_late_lightwind | 2 | -100.0% | 72 | +47.9% | -147.9% |
| metar_joined_source_aware | 3 | -100.0% | 71 | +50.0% | -150.0% |
| source_aware_and_metar_score_ge_4 | 3 | -100.0% | 71 | +50.0% | -150.0% |

### Forward 2026-06-27..30

| strategy | selected | selected ROI | complement | complement ROI | delta |
| --- | --- | --- | --- | --- | --- |
| source_station_citydiag_score_ge_4_diag_only | 2 | +809.1% | 17 | -21.5% | +830.6% |
| integrated_with_metar_score_ge_5 | 3 | +506.1% | 16 | -16.6% | +522.6% |
| source_station_citydiag_score_ge_3_diag_only | 5 | +416.3% | 14 | -59.2% | +475.5% |
| station_bias_p90_high | 7 | +268.8% | 12 | -52.4% | +321.2% |
| source_station_no_city_score_ge_3 | 7 | +268.8% | 12 | -52.4% | +321.2% |
| integrated_with_metar_score_ge_4 | 7 | +268.8% | 12 | -52.4% | +321.2% |
| source_aware_v3 | 11 | +186.6% | 8 | -100.0% | +286.6% |
| station_hot_tail_high | 8 | +222.7% | 11 | -48.1% | +270.7% |
| source_station_no_city_score_ge_2 | 12 | +115.1% | 7 | -18.4% | +133.5% |
| pcal_city_diag_ev_ge_0_5_diag_only | 8 | +127.3% | 11 | +21.3% | +105.9% |
| pcal_city_diag_ev_ge_0_2_diag_only | 14 | +84.4% | 5 | +14.3% | +70.1% |
| pcal_no_city_ev_ge_0_2 | 15 | +21.2% | 4 | +233.7% | -212.5% |
| pcal_no_city_ev_ge_0_5 | 5 | -100.0% | 14 | +125.2% | -225.2% |

## Daily Distribution

| strategy | period | date | rows | wins | cost | PnL | ROI |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_v1_all | forward_2026_06_27_30 | 2026-06-27 | 11 | 1 | $+11.00 | $-5.29 | -48.1% |
| baseline_v1_all | forward_2026_06_27_30 | 2026-06-28 | 6 | 1 | $+6.00 | $+1.63 | +27.2% |
| baseline_v1_all | forward_2026_06_27_30 | 2026-06-30 | 2 | 1 | $+2.00 | $+16.18 | +809.1% |
| baseline_v1_all | holdout_2026_06_21_26 | 2026-06-21 | 14 | 1 | $+14.00 | $-7.94 | -56.7% |
| baseline_v1_all | holdout_2026_06_21_26 | 2026-06-22 | 11 | 1 | $+11.00 | $-5.69 | -51.8% |
| baseline_v1_all | holdout_2026_06_21_26 | 2026-06-23 | 16 | 3 | $+16.00 | $+4.51 | +28.2% |
| baseline_v1_all | holdout_2026_06_21_26 | 2026-06-24 | 13 | 4 | $+13.00 | $+27.99 | +215.3% |
| baseline_v1_all | holdout_2026_06_21_26 | 2026-06-25 | 9 | 2 | $+9.00 | $+19.64 | +218.3% |
| baseline_v1_all | holdout_2026_06_21_26 | 2026-06-26 | 11 | 1 | $+11.00 | $-6.00 | -54.5% |
| source_aware_v3 | forward_2026_06_27_30 | 2026-06-27 | 5 | 1 | $+5.00 | $+0.71 | +14.3% |
| source_aware_v3 | forward_2026_06_27_30 | 2026-06-28 | 4 | 1 | $+4.00 | $+3.63 | +90.8% |
| source_aware_v3 | forward_2026_06_27_30 | 2026-06-30 | 2 | 1 | $+2.00 | $+16.18 | +809.1% |
| source_aware_v3 | holdout_2026_06_21_26 | 2026-06-21 | 9 | 1 | $+9.00 | $-2.94 | -32.7% |
| source_aware_v3 | holdout_2026_06_21_26 | 2026-06-22 | 6 | 1 | $+6.00 | $-0.69 | -11.6% |
| source_aware_v3 | holdout_2026_06_21_26 | 2026-06-23 | 12 | 2 | $+12.00 | $+2.63 | +21.9% |
| source_aware_v3 | holdout_2026_06_21_26 | 2026-06-24 | 9 | 4 | $+9.00 | $+31.99 | +355.4% |
| source_aware_v3 | holdout_2026_06_21_26 | 2026-06-25 | 3 | 0 | $+3.00 | $-3.00 | -100.0% |
| source_aware_v3 | holdout_2026_06_21_26 | 2026-06-26 | 4 | 0 | $+4.00 | $-4.00 | -100.0% |
| source_station_no_city_score_ge_2 | forward_2026_06_27_30 | 2026-06-27 | 6 | 0 | $+6.00 | $-6.00 | -100.0% |
| source_station_no_city_score_ge_2 | forward_2026_06_27_30 | 2026-06-28 | 5 | 1 | $+5.00 | $+2.63 | +52.7% |
| source_station_no_city_score_ge_2 | forward_2026_06_27_30 | 2026-06-30 | 1 | 1 | $+1.00 | $+17.18 | +1718.2% |
| source_station_no_city_score_ge_2 | holdout_2026_06_21_26 | 2026-06-21 | 11 | 1 | $+11.00 | $-4.94 | -44.9% |
| source_station_no_city_score_ge_2 | holdout_2026_06_21_26 | 2026-06-22 | 6 | 1 | $+6.00 | $-0.69 | -11.6% |
| source_station_no_city_score_ge_2 | holdout_2026_06_21_26 | 2026-06-23 | 12 | 1 | $+12.00 | $-6.07 | -50.5% |
| source_station_no_city_score_ge_2 | holdout_2026_06_21_26 | 2026-06-24 | 11 | 3 | $+11.00 | $+21.65 | +196.8% |
| source_station_no_city_score_ge_2 | holdout_2026_06_21_26 | 2026-06-25 | 7 | 1 | $+7.00 | $+4.98 | +71.1% |
| source_station_no_city_score_ge_2 | holdout_2026_06_21_26 | 2026-06-26 | 4 | 0 | $+4.00 | $-4.00 | -100.0% |

## P2 Expression Selector Context

The Tmax distribution P2 branch is not the same denominator as low-price YES.  It is the broader expression selector line for current YES/current NO/d1 NO/d2 NO.  It should be shadowed as expression telemetry, not merged into this low-price live sleeve yet.

| method | rows | dates | cities | ROI | CI low | CI high | YES | current NO | d1 NO | d2 NO |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fusion_city_blend | 567 | 6 | 36 | +12.3% | +6.8% | +19.9% | 221 | 120 | 167 | 59 |
| fusion_context_blend | 541 | 6 | 36 | +12.3% | +4.7% | +19.7% | 217 | 114 | 156 | 54 |
| fusion_numeric_blend | 467 | 6 | 36 | +10.8% | +1.7% | +20.7% | 216 | 81 | 135 | 35 |

## Contribution Slices

| strategy | dimension | level | rows | dates | win | avg ask | PnL | ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| source_aware_and_metar_score_ge_4 | day_regime | day_open_runway | 6 | 6 | +16.7% | 0.115 | $+2.00 | +33.3% |
| source_aware_and_metar_score_ge_4 | forecast_model | ecmwf | 4 | 4 | +25.0% | 0.150 | $+4.00 | +100.0% |
| source_aware_and_metar_score_ge_4 | forecast_model | gfs | 4 | 3 | +0.0% | 0.088 | $-4.00 | -100.0% |
| source_aware_and_metar_score_ge_4 | forecast_source | open_meteo_live_ecmwf | 4 | 4 | +25.0% | 0.150 | $+4.00 | +100.0% |
| source_aware_and_metar_score_ge_4 | forecast_source | open_meteo_live_gfs | 4 | 3 | +0.0% | 0.088 | $-4.00 | -100.0% |
| source_aware_and_metar_score_ge_4 | intraday_state | active_warming | 6 | 5 | +16.7% | 0.104 | $+2.00 | +33.3% |
| source_aware_and_metar_score_ge_4 | moisture_cloud_regime | humid_convective_risk | 3 | 3 | +33.3% | 0.128 | $+5.00 | +166.7% |
| source_aware_and_metar_score_ge_4 | moisture_cloud_regime | mixed_moisture | 5 | 5 | +0.0% | 0.114 | $-5.00 | -100.0% |
| source_aware_and_metar_score_ge_4 | wind_regime | light_wind | 8 | 6 | +12.5% | 0.119 | $+0.00 | +0.0% |
| source_aware_v3 | city | Shanghai | 12 | 12 | +41.7% | 0.094 | $+52.94 | +441.2% |
| source_aware_v3 | city | Amsterdam | 12 | 12 | +50.0% | 0.155 | $+25.56 | +213.0% |
| source_aware_v3 | city | Manila | 12 | 12 | +25.0% | 0.082 | $+24.40 | +203.3% |
| source_aware_v3 | city | Madrid | 5 | 5 | +60.0% | 0.155 | $+13.25 | +265.0% |
| source_aware_v3 | city | KualaLumpur | 3 | 3 | +66.7% | 0.150 | $+11.04 | +368.0% |
| source_aware_v3 | city | BuenosAires | 7 | 7 | +42.9% | 0.166 | $+10.32 | +147.5% |
| source_aware_v3 | city | Moscow | 10 | 10 | +30.0% | 0.154 | $+8.48 | +84.8% |
| source_aware_v3 | city | MexicoCity | 9 | 9 | +22.2% | 0.129 | $+7.19 | +79.9% |
| source_aware_v3 | city | Chicago | 15 | 15 | +13.3% | 0.097 | $+6.96 | +46.4% |
| source_aware_v3 | city | Jeddah | 8 | 8 | +25.0% | 0.146 | $+5.71 | +71.4% |
| source_aware_v3 | city | Helsinki | 3 | 3 | +33.3% | 0.131 | $+5.03 | +167.7% |
| source_aware_v3 | city | Houston | 9 | 9 | +11.1% | 0.086 | $+3.82 | +42.5% |
| source_aware_v3 | city | Lucknow | 6 | 6 | +16.7% | 0.137 | $+3.52 | +58.7% |
| source_aware_v3 | city | Istanbul | 3 | 3 | +33.3% | 0.135 | $+3.25 | +108.3% |
| source_aware_v3 | city | Miami | 10 | 10 | +10.0% | 0.101 | $+3.25 | +32.5% |
| source_aware_v3 | city | Singapore | 6 | 6 | +16.7% | 0.121 | $+2.70 | +44.9% |
| source_aware_v3 | city | London | 5 | 5 | +20.0% | 0.141 | $+2.41 | +48.1% |
| source_aware_v3 | city | Warsaw | 4 | 4 | +25.0% | 0.135 | $+2.25 | +56.2% |
| source_aware_v3 | city | Busan | 4 | 4 | +25.0% | 0.115 | $+2.23 | +55.8% |
| source_aware_v3 | city | Seoul | 8 | 8 | +12.5% | 0.129 | $+2.00 | +25.0% |
| source_aware_v3 | city | Paris | 7 | 7 | +14.3% | 0.079 | $+1.33 | +19.0% |
| source_aware_v3 | city | Karachi | 5 | 5 | +20.0% | 0.170 | $+1.25 | +25.0% |
| source_aware_v3 | city | LA | 10 | 10 | +10.0% | 0.092 | $+0.53 | +5.3% |
| source_aware_v3 | city | Milan | 6 | 6 | +16.7% | 0.125 | $+0.06 | +1.0% |
| source_aware_v3 | city | Atlanta | 13 | 13 | +7.7% | 0.094 | $-2.47 | -19.0% |
| source_aware_v3 | city | Beijing | 4 | 4 | +0.0% | 0.119 | $-4.00 | -100.0% |
| source_aware_v3 | city | Dallas | 4 | 4 | +0.0% | 0.121 | $-4.00 | -100.0% |
| source_aware_v3 | city | Munich | 5 | 5 | +0.0% | 0.129 | $-5.00 | -100.0% |
| source_aware_v3 | city | Ankara | 7 | 7 | +0.0% | 0.124 | $-7.00 | -100.0% |
| source_aware_v3 | city | Shenzhen | 7 | 7 | +0.0% | 0.152 | $-7.00 | -100.0% |
| source_aware_v3 | city | Tokyo | 16 | 16 | +6.2% | 0.084 | $-7.30 | -45.7% |
| source_aware_v3 | city | Austin | 8 | 8 | +0.0% | 0.101 | $-8.00 | -100.0% |
| source_aware_v3 | city | Denver | 8 | 8 | +0.0% | 0.100 | $-8.00 | -100.0% |
| source_aware_v3 | city | NYC | 9 | 9 | +0.0% | 0.094 | $-9.00 | -100.0% |
| source_aware_v3 | city | TelAviv | 13 | 13 | +0.0% | 0.083 | $-13.00 | -100.0% |
| source_aware_v3 | day_regime | nan | 276 | 53 | +17.8% | 0.113 | $+153.75 | +55.7% |
| source_aware_v3 | day_regime | day_open_runway | 6 | 6 | +16.7% | 0.115 | $+2.00 | +33.3% |
| source_aware_v3 | day_regime | day_forecast_busted | 3 | 3 | +0.0% | 0.175 | $-3.00 | -100.0% |
| source_aware_v3 | forecast_model | ecmwf | 133 | 51 | +23.3% | 0.141 | $+76.56 | +57.6% |
| source_aware_v3 | forecast_model | gfs | 156 | 52 | +12.2% | 0.091 | $+72.19 | +46.3% |
| source_aware_v3 | forecast_source | open_meteo_live_ecmwf | 133 | 51 | +23.3% | 0.141 | $+76.56 | +57.6% |
| source_aware_v3 | forecast_source | open_meteo_live_gfs | 156 | 52 | +12.2% | 0.091 | $+72.19 | +46.3% |
| source_aware_v3 | intraday_state | nan | 276 | 53 | +17.8% | 0.113 | $+153.75 | +55.7% |
| source_aware_v3 | intraday_state | active_warming | 9 | 8 | +11.1% | 0.124 | $-1.00 | -11.1% |
| source_aware_v3 | intraday_state | fresh_high | 3 | 3 | +0.0% | 0.157 | $-3.00 | -100.0% |
| source_aware_v3 | moisture_cloud_regime | nan | 276 | 53 | +17.8% | 0.113 | $+153.75 | +55.7% |
| source_aware_v3 | moisture_cloud_regime | humid_convective_risk | 3 | 3 | +33.3% | 0.128 | $+5.00 | +166.7% |
| source_aware_v3 | moisture_cloud_regime | mixed_moisture | 9 | 8 | +0.0% | 0.135 | $-9.00 | -100.0% |
| source_aware_v3 | wind_regime | nan | 276 | 53 | +17.8% | 0.113 | $+153.75 | +55.7% |
| source_aware_v3 | wind_regime | light_wind | 9 | 7 | +11.1% | 0.128 | $-1.00 | -11.1% |
| source_aware_v3 | wind_regime | moderate_wind | 3 | 3 | +0.0% | 0.148 | $-3.00 | -100.0% |
| source_station_citydiag_score_ge_3_diag_only | city | Shanghai | 14 | 14 | +35.7% | 0.106 | $+50.94 | +363.9% |
| source_station_citydiag_score_ge_3_diag_only | city | Amsterdam | 11 | 11 | +54.5% | 0.158 | $+26.56 | +241.5% |
| source_station_citydiag_score_ge_3_diag_only | city | Manila | 13 | 13 | +23.1% | 0.088 | $+23.40 | +180.0% |
| source_station_citydiag_score_ge_3_diag_only | city | Wellington | 3 | 3 | +33.3% | 0.097 | $+12.38 | +412.8% |
| source_station_citydiag_score_ge_3_diag_only | city | KualaLumpur | 4 | 4 | +50.0% | 0.127 | $+10.04 | +251.0% |
| source_station_citydiag_score_ge_3_diag_only | city | BuenosAires | 8 | 8 | +37.5% | 0.157 | $+9.32 | +116.6% |
| source_station_citydiag_score_ge_3_diag_only | city | Seoul | 14 | 14 | +14.3% | 0.108 | $+7.11 | +50.8% |
| source_station_citydiag_score_ge_3_diag_only | city | Helsinki | 3 | 3 | +33.3% | 0.131 | $+5.03 | +167.7% |
| source_station_citydiag_score_ge_3_diag_only | city | Singapore | 6 | 6 | +16.7% | 0.121 | $+2.70 | +44.9% |
| source_station_citydiag_score_ge_3_diag_only | city | Warsaw | 4 | 4 | +25.0% | 0.135 | $+2.25 | +56.2% |
| source_station_citydiag_score_ge_3_diag_only | city | Milan | 8 | 8 | +12.5% | 0.107 | $-1.94 | -24.2% |
| source_station_citydiag_score_ge_3_diag_only | city | Beijing | 4 | 4 | +0.0% | 0.119 | $-4.00 | -100.0% |
| source_station_citydiag_score_ge_3_diag_only | city | HongKong | 4 | 4 | +0.0% | 0.077 | $-4.00 | -100.0% |
| source_station_citydiag_score_ge_3_diag_only | city | Munich | 5 | 5 | +0.0% | 0.129 | $-5.00 | -100.0% |
| source_station_citydiag_score_ge_3_diag_only | city | Shenzhen | 7 | 7 | +0.0% | 0.152 | $-7.00 | -100.0% |
| source_station_citydiag_score_ge_3_diag_only | day_regime | nan | 113 | 47 | +23.9% | 0.118 | $+139.47 | +123.4% |
| source_station_citydiag_score_ge_3_diag_only | forecast_model | gfs | 38 | 28 | +28.9% | 0.100 | $+104.09 | +273.9% |
| source_station_citydiag_score_ge_3_diag_only | forecast_model | ecmwf | 79 | 40 | +21.5% | 0.128 | $+39.38 | +49.8% |
| source_station_citydiag_score_ge_3_diag_only | forecast_source | open_meteo_live_gfs | 38 | 28 | +28.9% | 0.100 | $+104.09 | +273.9% |
| source_station_citydiag_score_ge_3_diag_only | forecast_source | open_meteo_live_ecmwf | 79 | 40 | +21.5% | 0.128 | $+39.38 | +49.8% |

## Interpretation

- V1 is still mostly a forecast/model/market mispricing sleeve, not an observation-led METAR strategy.
- METAR/regime is useful forward telemetry because it tells us whether a ticket had physical runway, but historical same-denominator evidence does not justify waiting for METAR before entry.
- `source_aware_v3` is the broadest useful tag; it is still not clean alpha because forecast source is partially city/source-policy coupled.
- Station-bias tags are mechanism-plausible.  The high p90/hot-tail rows catch convex winners, but complement and forward checks are noisy.
- City-diagnostic p_cal looks strongest numerically.  That is exactly why it stays diagnostic-only until fresh forward rows prove it is not city memory.
- P2 distribution EV is the right architecture for a future expression selector, but it needs executable replay and more forward dates before it can replace or route V1.

## Shadow Spec

Implement `low_price_yes_integrated_tail_shadow_v2` as a zero-notional journal over current V1 candidates.  Each row should record:

- V1 candidate fields: city/date/bracket/source/ask/model_p/edge/snapshot.
- source tags: `source_aware_v3`, `source_aware_wide`.
- station tags: `bias_p90_asof`, `hot_tail_pct_asof`, `p_cal_no_city`, `p_cal_city_diag`, EV fields.
- live observation tags: observation cache status, current/running temp, forecast-to-running gap, humidity/wind/trend/minutes-since-high.
- simplified METAR/regime score tags for forward analysis.
- execution status: token id available, fresh book ask/depth, blocked reason.

This shadow should not submit orders and should not alter the existing `$1` V1 live sleeve.

## Artifacts

- Enriched rows: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv`
- Strategy summary: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/strategy_summary.csv`
- Daily summary: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/daily_summary.csv`
- Complement check: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/complement_check.csv`
- Contribution slices: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/contribution_slices.csv`
- JSON: `docs/analysis/2026-07/2026-07-02-low-price-yes-integrated-tail-v2.json`
