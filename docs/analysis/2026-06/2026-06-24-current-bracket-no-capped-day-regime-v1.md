# Current-Bracket NO Capped-Day Regime V1

## 结论

这版把问题拆成两层：remaining-heat 继续判断“有没有足够升温空间”，新增 capped-day regime 判断“这个空间是不是假的”。结果是：p_cap 可以解释一部分高分假阳性，但跨窗口还不够稳定，尤其 forward 仍亏。它证明根因方向对，但还不能作为 live gate。

Verdict: `capped_day_regime_direction_confirmed_but_shadow_only`，live_ready=`False`。

## Data

- Generated at UTC: `2026-06-24T08:07:03+00:00`
- Sync/rebuild: `sync_weather_remote.sh completed; run_stack rebuilt fact tables and CLOB gate, then exited non-clean because FE port 5174 stayed busy.`
- Historical dates: `2026-05-20`..`2026-06-20`
- Historical mechanism rows: `4306`
- Historical trade-base rows: `499`
- Forward rows: `580`

## Cap Model Swap Metrics

| train_spec | eval_window | rows | trade_base_rows | active_dates | cap_rate | cap_auc | cap_brier | avg_p_cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| weather_early | early | 3033 | 348 | 22 | +36.4% | 0.961 | 0.080 | 0.364 |
| weather_early | late | 1273 | 151 | 10 | +35.7% | 0.901 | 0.123 | 0.379 |
| weather_early | forward_settled | 434 | 50 | 2 | +77.9% | 0.879 | 0.124 | 0.684 |
| weather_late | early | 3033 | 348 | 22 | +36.4% | 0.892 | 0.131 | 0.326 |
| weather_late | late | 1273 | 151 | 10 | +35.7% | 0.986 | 0.056 | 0.358 |
| weather_late | forward_settled | 434 | 50 | 2 | +77.9% | 0.833 | 0.168 | 0.622 |
| weather_city_early | early | 3033 | 348 | 22 | +36.4% | 0.964 | 0.078 | 0.364 |
| weather_city_early | late | 1273 | 151 | 10 | +35.7% | 0.900 | 0.123 | 0.377 |
| weather_city_early | forward_settled | 434 | 50 | 2 | +77.9% | 0.884 | 0.122 | 0.686 |
| weather_city_late | early | 3033 | 348 | 22 | +36.4% | 0.893 | 0.130 | 0.325 |
| weather_city_late | late | 1273 | 151 | 10 | +35.7% | 0.987 | 0.054 | 0.357 |
| weather_city_late | forward_settled | 434 | 50 | 2 | +77.9% | 0.849 | 0.155 | 0.636 |

## Variant Performance

| train_spec | eval_window | variant | selected_trades | settled_trades | active_dates | win_rate | roi | profit_usd | avg_p_cross | avg_p_cap | avg_actual_margin_f | avg_pred_error_f | loss_reason_counts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| weather_early | early | base_p40_ev10 | 196 | 196 | 22 | +23.0% | +0.1% | $+0.77 | 0.623 | 0.646 | 0.001 | 0.347 | capped_day_model_overestimate:108,capped_day_shortfall:43,win:45 |
| weather_early | early | cap_veto_train_top25_risk | 147 | 147 | 22 | +29.9% | +29.9% | $+219.46 | 0.665 | 0.564 | 0.103 | 0.364 | capped_day_model_overestimate:89,capped_day_shortfall:14,win:44 |
| weather_early | early | cap_adjusted_p40_ev10 | 38 | 38 | 17 | +52.6% | +109.6% | $+208.17 | 0.835 | 0.287 | 0.537 | 0.495 | capped_day_model_overestimate:18,win:20 |
| weather_early | late | base_p40_ev10 | 84 | 84 | 10 | +28.6% | +33.6% | $+140.98 | 0.666 | 0.601 | 0.074 | 0.455 | capped_day_model_overestimate:55,capped_day_shortfall:5,win:24 |
| weather_early | late | cap_veto_train_top25_risk | 75 | 75 | 10 | +29.3% | +37.6% | $+140.90 | 0.685 | 0.565 | 0.089 | 0.502 | capped_day_model_overestimate:52,capped_day_shortfall:1,win:22 |
| weather_early | late | cap_adjusted_p40_ev10 | 17 | 17 | 7 | +41.2% | +83.4% | $+70.85 | 0.879 | 0.288 | 0.344 | 0.992 | capped_day_model_overestimate:10,win:7 |
| weather_early | forward_settled | base_p40_ev10 | 30 | 21 | 3 | +19.0% | -35.3% | $-37.05 | 0.733 | 0.505 | -0.119 | 1.159 | capped_day_model_overestimate:15,capped_day_shortfall:2,win:4 |
| weather_early | forward_settled | cap_veto_train_top25_risk | 25 | 18 | 3 | +22.2% | -24.5% | $-22.05 | 0.779 | 0.428 | -0.075 | 1.288 | capped_day_model_overestimate:13,capped_day_shortfall:1,win:4 |
| weather_early | forward_settled | cap_adjusted_p40_ev10 | 14 | 9 | 3 | +22.2% | -30.5% | $-13.72 | 0.938 | 0.262 | -0.022 | 2.266 | capped_day_model_overestimate:7,win:2 |
| weather_late | early | base_p40_ev10 | 195 | 195 | 22 | +21.5% | -5.2% | $-50.56 | 0.732 | 0.549 | -0.053 | 0.721 | capped_day_model_overestimate:131,capped_day_shortfall:22,win:42 |
| weather_late | early | cap_veto_train_top25_risk | 157 | 157 | 22 | +24.8% | +10.2% | $+79.84 | 0.765 | 0.470 | 0.009 | 0.773 | capped_day_model_overestimate:105,capped_day_shortfall:13,win:39 |
| weather_late | early | cap_adjusted_p40_ev10 | 66 | 66 | 22 | +28.8% | +23.8% | $+78.49 | 0.926 | 0.258 | 0.042 | 1.376 | capped_day_model_overestimate:47,win:19 |
| weather_late | late | base_p40_ev10 | 66 | 66 | 10 | +39.4% | +80.0% | $+263.99 | 0.717 | 0.560 | 0.352 | 0.175 | capped_day_model_overestimate:32,capped_day_shortfall:8,win:26 |
| weather_late | late | cap_veto_train_top25_risk | 50 | 50 | 10 | +52.0% | +137.6% | $+343.99 | 0.763 | 0.463 | 0.544 | 0.107 | capped_day_model_overestimate:23,capped_day_shortfall:1,win:26 |
| weather_late | late | cap_adjusted_p40_ev10 | 26 | 26 | 9 | +80.8% | +280.2% | $+364.31 | 0.873 | 0.323 | 1.094 | -0.101 | capped_day_model_overestimate:5,win:21 |
| weather_late | forward_settled | base_p40_ev10 | 33 | 24 | 3 | +16.7% | -43.4% | $-52.05 | 0.799 | 0.470 | -0.171 | 1.279 | capped_day_model_overestimate:19,capped_day_shortfall:1,win:4 |
| weather_late | forward_settled | cap_veto_train_top25_risk | 26 | 19 | 3 | +21.1% | -28.5% | $-27.05 | 0.874 | 0.360 | -0.076 | 1.484 | capped_day_model_overestimate:15,win:4 |
| weather_late | forward_settled | cap_adjusted_p40_ev10 | 19 | 13 | 3 | +7.7% | -76.7% | $-49.85 | 0.949 | 0.267 | -0.265 | 2.145 | capped_day_model_overestimate:12,win:1 |
| weather_city_early | early | base_p40_ev10 | 196 | 196 | 22 | +23.0% | +0.1% | $+0.77 | 0.623 | 0.648 | 0.001 | 0.347 | capped_day_model_overestimate:108,capped_day_shortfall:43,win:45 |
| weather_city_early | early | cap_veto_train_top25_risk | 149 | 149 | 22 | +30.2% | +31.6% | $+235.77 | 0.662 | 0.571 | 0.108 | 0.353 | capped_day_model_overestimate:88,capped_day_shortfall:16,win:45 |
| weather_city_early | early | cap_adjusted_p40_ev10 | 37 | 37 | 17 | +54.1% | +115.1% | $+212.86 | 0.836 | 0.294 | 0.553 | 0.486 | capped_day_model_overestimate:17,win:20 |
| weather_city_early | late | base_p40_ev10 | 84 | 84 | 10 | +28.6% | +33.6% | $+140.98 | 0.666 | 0.599 | 0.074 | 0.455 | capped_day_model_overestimate:55,capped_day_shortfall:5,win:24 |
| weather_city_early | late | cap_veto_train_top25_risk | 73 | 73 | 10 | +28.8% | +34.2% | $+124.86 | 0.685 | 0.556 | 0.082 | 0.511 | capped_day_model_overestimate:51,capped_day_shortfall:1,win:21 |
| weather_city_early | late | cap_adjusted_p40_ev10 | 19 | 19 | 7 | +42.1% | +82.2% | $+78.09 | 0.879 | 0.297 | 0.387 | 0.970 | capped_day_model_overestimate:11,win:8 |
| weather_city_early | forward_settled | base_p40_ev10 | 30 | 21 | 3 | +19.0% | -35.3% | $-37.05 | 0.733 | 0.501 | -0.119 | 1.159 | capped_day_model_overestimate:15,capped_day_shortfall:2,win:4 |
| weather_city_early | forward_settled | cap_veto_train_top25_risk | 25 | 18 | 3 | +22.2% | -24.5% | $-22.05 | 0.779 | 0.425 | -0.075 | 1.288 | capped_day_model_overestimate:13,capped_day_shortfall:1,win:4 |
| weather_city_early | forward_settled | cap_adjusted_p40_ev10 | 13 | 8 | 3 | +25.0% | -21.8% | $-8.72 | 0.945 | 0.240 | -0.019 | 2.427 | capped_day_model_overestimate:6,win:2 |
| weather_city_late | early | base_p40_ev10 | 195 | 195 | 22 | +21.5% | -5.2% | $-50.56 | 0.732 | 0.548 | -0.053 | 0.721 | capped_day_model_overestimate:131,capped_day_shortfall:22,win:42 |
| weather_city_late | early | cap_veto_train_top25_risk | 152 | 152 | 22 | +25.0% | +11.4% | $+86.39 | 0.769 | 0.459 | 0.010 | 0.789 | capped_day_model_overestimate:103,capped_day_shortfall:11,win:38 |
| weather_city_late | early | cap_adjusted_p40_ev10 | 62 | 62 | 21 | +29.0% | +23.5% | $+72.80 | 0.932 | 0.238 | 0.065 | 1.349 | capped_day_model_overestimate:44,win:18 |
| weather_city_late | late | base_p40_ev10 | 66 | 66 | 10 | +39.4% | +80.0% | $+263.99 | 0.717 | 0.560 | 0.352 | 0.175 | capped_day_model_overestimate:32,capped_day_shortfall:8,win:26 |
| weather_city_late | late | cap_veto_train_top25_risk | 50 | 50 | 10 | +52.0% | +137.6% | $+343.99 | 0.771 | 0.465 | 0.552 | 0.116 | capped_day_model_overestimate:23,capped_day_shortfall:1,win:26 |
| weather_city_late | late | cap_adjusted_p40_ev10 | 24 | 24 | 9 | +83.3% | +297.0% | $+356.46 | 0.890 | 0.313 | 1.148 | -0.097 | capped_day_model_overestimate:4,win:20 |
| weather_city_late | forward_settled | base_p40_ev10 | 33 | 24 | 3 | +16.7% | -43.4% | $-52.05 | 0.799 | 0.487 | -0.171 | 1.279 | capped_day_model_overestimate:19,capped_day_shortfall:1,win:4 |
| weather_city_late | forward_settled | cap_veto_train_top25_risk | 27 | 20 | 3 | +20.0% | -32.1% | $-32.05 | 0.861 | 0.397 | -0.095 | 1.434 | capped_day_model_overestimate:16,win:4 |
| weather_city_late | forward_settled | cap_adjusted_p40_ev10 | 18 | 12 | 3 | +8.3% | -74.7% | $-44.85 | 0.947 | 0.273 | -0.250 | 2.118 | capped_day_model_overestimate:11,win:1 |

## Cap Risk Buckets

| train_spec | eval_window | bucket | rows | active_dates | avg_p_cap | cap_rate | win_rate | roi | avg_actual_margin_f | avg_pred_error_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| weather_early | early | 1 | 49 | 18 | 0.322 | +53.1% | +46.9% | +89.6% | 0.362 | 0.483 |
| weather_early | early | 2 | 49 | 20 | 0.610 | +71.4% | +28.6% | +33.3% | 0.083 | 0.260 |
| weather_early | early | 3 | 49 | 20 | 0.761 | +85.7% | +14.3% | -33.4% | -0.137 | 0.349 |
| weather_early | early | 4 | 49 | 20 | 0.890 | +98.0% | +2.0% | -89.3% | -0.303 | 0.296 |
| weather_early | late | 1 | 21 | 9 | 0.310 | +61.9% | +38.1% | +76.4% | 0.152 | 0.683 |
| weather_early | late | 2 | 21 | 7 | 0.562 | +66.7% | +33.3% | +70.0% | 0.200 | 0.416 |
| weather_early | late | 3 | 21 | 10 | 0.691 | +76.2% | +23.8% | +1.1% | 0.036 | 0.379 |
| weather_early | late | 4 | 21 | 10 | 0.840 | +81.0% | +19.0% | -13.2% | -0.090 | 0.343 |
| weather_early | forward_settled | 1 | 6 | 2 | 0.204 | +100.0% | +0.0% | -100.0% | -0.333 | 3.227 |
| weather_early | forward_settled | 2 | 5 | 2 | 0.403 | +40.0% | +60.0% | +91.8% | 0.460 | 0.149 |
| weather_early | forward_settled | 3 | 5 | 2 | 0.667 | +100.0% | +0.0% | -100.0% | -0.430 | 0.743 |
| weather_early | forward_settled | 4 | 5 | 2 | 0.862 | +80.0% | +20.0% | -20.0% | -0.130 | 0.104 |
| weather_late | early | 1 | 49 | 21 | 0.187 | +73.5% | +26.5% | +7.7% | -0.032 | 1.487 |
| weather_late | early | 2 | 49 | 20 | 0.497 | +65.3% | +34.7% | +76.3% | 0.245 | 0.312 |
| weather_late | early | 3 | 48 | 19 | 0.672 | +85.4% | +14.6% | -41.4% | -0.153 | 0.610 |
| weather_late | early | 4 | 49 | 20 | 0.844 | +89.8% | +10.2% | -64.0% | -0.273 | 0.474 |
| weather_late | late | 1 | 17 | 8 | 0.243 | +11.8% | +88.2% | +337.3% | 1.138 | -0.190 |
| weather_late | late | 2 | 16 | 8 | 0.473 | +50.0% | +50.0% | +108.1% | 0.569 | 0.177 |
| weather_late | late | 3 | 16 | 9 | 0.671 | +81.2% | +18.8% | -30.3% | -0.075 | 0.310 |
| weather_late | late | 4 | 17 | 10 | 0.852 | +100.0% | +0.0% | -100.0% | -0.238 | 0.412 |
| weather_late | forward_settled | 1 | 6 | 2 | 0.127 | +83.3% | +16.7% | -49.5% | -0.167 | 3.027 |
| weather_late | forward_settled | 2 | 6 | 2 | 0.428 | +83.3% | +16.7% | -33.3% | -0.100 | 0.902 |
| weather_late | forward_settled | 3 | 6 | 2 | 0.576 | +66.7% | +33.3% | +9.3% | 0.067 | 0.647 |
| weather_late | forward_settled | 4 | 6 | 2 | 0.848 | +100.0% | +0.0% | -100.0% | -0.483 | 0.539 |

## Forecast Overconfidence Slices

| train_spec | eval_window | slice | rows | active_dates | win_rate | roi | profit_usd | avg_p_cross | avg_p_cap | avg_actual_margin_f | avg_pred_error_f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| weather_early | early | all_p40 | 196 | 22 | +23.0% | +0.1% | $+0.77 | 0.623 | 0.646 | 0.001 | 0.347 |
| weather_early | early | p_cross_ge_095_pcap_lt_030 | 4 | 3 | +75.0% | +144.5% | $+28.89 | 0.972 | 0.132 | 1.050 | 0.888 |
| weather_early | early | p_cap_top25 | 49 | 20 | +2.0% | -89.3% | $-218.68 | 0.497 | 0.890 | -0.303 | 0.296 |
| weather_early | early | p_cap_bottom25 | 49 | 18 | +46.9% | +89.6% | $+219.63 | 0.779 | 0.322 | 0.362 | 0.483 |
| weather_early | late | all_p40 | 84 | 10 | +28.6% | +33.6% | $+140.98 | 0.666 | 0.601 | 0.074 | 0.455 |
| weather_early | late | p_cross_ge_095_pcap_lt_030 | 4 | 4 | +50.0% | +122.2% | $+24.44 | 0.974 | 0.174 | 0.900 | 1.023 |
| weather_early | late | p_cap_top25 | 21 | 10 | +19.0% | -13.2% | $-13.88 | 0.578 | 0.840 | -0.090 | 0.343 |
| weather_early | late | p_cap_bottom25 | 21 | 9 | +38.1% | +76.4% | $+80.26 | 0.754 | 0.310 | 0.152 | 0.683 |
| weather_early | forward_settled | all_p40 | 21 | 2 | +19.0% | -35.3% | $-37.05 | 0.709 | 0.518 | -0.119 | 1.159 |
| weather_early | forward_settled | p_cross_ge_095_pcap_lt_030 | 4 | 2 | +0.0% | -100.0% | $-20.00 | 0.998 | 0.175 | -0.363 | 4.020 |
| weather_early | forward_settled | p_cap_top25 | 6 | 2 | +16.7% | -33.3% | $-10.00 | 0.485 | 0.851 | -0.183 | 0.148 |
| weather_early | forward_settled | p_cap_bottom25 | 5 | 2 | +0.0% | -100.0% | $-25.00 | 0.960 | 0.181 | -0.390 | 3.483 |
| weather_late | early | all_p40 | 195 | 22 | +21.5% | -5.2% | $-50.56 | 0.732 | 0.549 | -0.053 | 0.721 |
| weather_late | early | p_cross_ge_095_pcap_lt_030 | 25 | 17 | +32.0% | +35.0% | $+43.81 | 0.991 | 0.150 | 0.208 | 1.864 |
| weather_late | early | p_cap_top25 | 49 | 20 | +10.2% | -64.0% | $-156.83 | 0.598 | 0.844 | -0.273 | 0.474 |
| weather_late | early | p_cap_bottom25 | 49 | 21 | +26.5% | +7.7% | $+18.78 | 0.919 | 0.187 | -0.032 | 1.487 |
| weather_late | late | all_p40 | 66 | 10 | +39.4% | +80.0% | $+263.99 | 0.717 | 0.560 | 0.352 | 0.175 |
| weather_late | late | p_cross_ge_095_pcap_lt_030 | 4 | 3 | +100.0% | +456.1% | $+91.23 | 0.988 | 0.175 | 1.950 | -0.028 |
| weather_late | late | p_cap_top25 | 17 | 10 | +0.0% | -100.0% | $-85.00 | 0.591 | 0.852 | -0.238 | 0.412 |
| weather_late | late | p_cap_bottom25 | 17 | 8 | +88.2% | +337.3% | $+286.73 | 0.838 | 0.243 | 1.138 | -0.190 |
| weather_late | forward_settled | all_p40 | 24 | 2 | +16.7% | -43.4% | $-52.05 | 0.785 | 0.494 | -0.171 | 1.279 |
| weather_late | forward_settled | p_cross_ge_095_pcap_lt_030 | 6 | 2 | +16.7% | -49.5% | $-14.85 | 0.993 | 0.127 | -0.167 | 3.027 |
| weather_late | forward_settled | p_cap_top25 | 7 | 2 | +0.0% | -100.0% | $-35.00 | 0.548 | 0.824 | -0.486 | 0.577 |
| weather_late | forward_settled | p_cap_bottom25 | 5 | 2 | +0.0% | -100.0% | $-25.00 | 1.000 | 0.092 | -0.300 | 3.490 |

## 6/22 Details

| train_spec | variant | city | label_no_wins | no_ask | stake_profit_usd | p_cross_upper | p_cap | p_cross_cap_adjusted | actual_margin_f | pred_error_f | loss_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| weather_city_early | base_p40_ev10 | Ankara | 0.000 | 0.280 | $-5.00 | 0.430 | 0.861 | 0.060 | -0.250 | 0.082 | capped_day_shortfall |
| weather_city_early | base_p40_ev10 | Shanghai | 0.000 | 0.300 | $-5.00 | 0.500 | 0.820 | 0.090 | -0.650 | 0.650 | capped_day_model_overestimate |
| weather_city_early | base_p40_ev10 | Denver | 0.000 | 0.130 | $-5.00 | 0.759 | 0.625 | 0.285 | -0.500 | 1.174 | capped_day_model_overestimate |
| weather_city_early | base_p40_ev10 | Seattle | 0.000 | 0.310 | $-5.00 | 0.419 | 0.596 | 0.169 | -0.500 | 0.305 | capped_day_shortfall |
| weather_city_early | base_p40_ev10 | Beijing | 0.000 | 0.190 | $-5.00 | 0.780 | 0.581 | 0.327 | -0.250 | 0.991 | capped_day_model_overestimate |
| weather_city_early | base_p40_ev10 | TelAviv | 0.000 | 0.320 | $-5.00 | 0.608 | 0.418 | 0.354 | -0.650 | 0.914 | capped_day_model_overestimate |
| weather_city_early | base_p40_ev10 | NYC | 0.000 | 0.262 | $-5.00 | 1.000 | 0.147 | 0.853 | -0.500 | 5.253 | capped_day_model_overestimate |
| weather_city_early | base_p40_ev10 | Chongqing | 0.000 | 0.240 | $-5.00 | 0.996 | 0.147 | 0.850 | -0.650 | 3.212 | capped_day_model_overestimate |
| weather_city_early | base_p40_ev10 | Wellington | 1.000 | 0.300 | $+11.67 | 0.476 | 0.538 | 0.220 | 1.150 | -1.207 | win |
| weather_city_early | cap_adjusted_p40_ev10 | NYC | 0.000 | 0.262 | $-5.00 | 1.000 | 0.147 | 0.853 | -0.500 | 5.253 | capped_day_model_overestimate |
| weather_city_early | cap_adjusted_p40_ev10 | Chongqing | 0.000 | 0.240 | $-5.00 | 0.996 | 0.147 | 0.850 | -0.650 | 3.212 | capped_day_model_overestimate |
| weather_city_early | cap_veto_train_top25_risk | Shanghai | 0.000 | 0.300 | $-5.00 | 0.500 | 0.820 | 0.090 | -0.650 | 0.650 | capped_day_model_overestimate |
| weather_city_early | cap_veto_train_top25_risk | Denver | 0.000 | 0.130 | $-5.00 | 0.759 | 0.625 | 0.285 | -0.500 | 1.174 | capped_day_model_overestimate |
| weather_city_early | cap_veto_train_top25_risk | Seattle | 0.000 | 0.310 | $-5.00 | 0.419 | 0.596 | 0.169 | -0.500 | 0.305 | capped_day_shortfall |
| weather_city_early | cap_veto_train_top25_risk | Beijing | 0.000 | 0.190 | $-5.00 | 0.780 | 0.581 | 0.327 | -0.250 | 0.991 | capped_day_model_overestimate |
| weather_city_early | cap_veto_train_top25_risk | TelAviv | 0.000 | 0.320 | $-5.00 | 0.608 | 0.418 | 0.354 | -0.650 | 0.914 | capped_day_model_overestimate |
| weather_city_early | cap_veto_train_top25_risk | NYC | 0.000 | 0.262 | $-5.00 | 1.000 | 0.147 | 0.853 | -0.500 | 5.253 | capped_day_model_overestimate |
| weather_city_early | cap_veto_train_top25_risk | Chongqing | 0.000 | 0.240 | $-5.00 | 0.996 | 0.147 | 0.850 | -0.650 | 3.212 | capped_day_model_overestimate |
| weather_city_early | cap_veto_train_top25_risk | Wellington | 1.000 | 0.300 | $+11.67 | 0.476 | 0.538 | 0.220 | 1.150 | -1.207 | win |
| weather_city_late | base_p40_ev10 | Tokyo | 0.000 | 0.302 | $-5.00 | 0.513 | 0.847 | 0.079 | -0.850 | 0.872 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | Shanghai | 0.000 | 0.300 | $-5.00 | 0.406 | 0.842 | 0.064 | -0.650 | 0.482 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | Ankara | 0.000 | 0.280 | $-5.00 | 0.760 | 0.733 | 0.203 | -0.250 | 0.747 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | Seattle | 0.000 | 0.160 | $-5.00 | 0.665 | 0.706 | 0.196 | -0.500 | 0.800 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | TelAviv | 0.000 | 0.320 | $-5.00 | 0.617 | 0.629 | 0.229 | -0.650 | 0.859 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | Denver | 0.000 | 0.130 | $-5.00 | 0.872 | 0.525 | 0.415 | -0.500 | 1.300 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | Miami | 0.000 | 0.270 | $-5.00 | 0.724 | 0.331 | 0.484 | -0.500 | 0.919 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | Beijing | 0.000 | 0.190 | $-5.00 | 0.965 | 0.328 | 0.648 | -0.250 | 1.528 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | Chongqing | 0.000 | 0.240 | $-5.00 | 1.000 | 0.307 | 0.693 | -0.650 | 3.636 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | NYC | 0.000 | 0.262 | $-5.00 | 0.999 | 0.072 | 0.928 | -0.500 | 2.737 | capped_day_model_overestimate |
| weather_city_late | base_p40_ev10 | Wellington | 1.000 | 0.300 | $+11.67 | 0.757 | 0.475 | 0.398 | 1.150 | -0.658 | win |
| weather_city_late | cap_adjusted_p40_ev10 | Denver | 0.000 | 0.130 | $-5.00 | 0.872 | 0.525 | 0.415 | -0.500 | 1.300 | capped_day_model_overestimate |
| weather_city_late | cap_adjusted_p40_ev10 | Ankara | 0.000 | 0.280 | $-5.00 | 0.871 | 0.498 | 0.438 | -0.250 | 1.046 | capped_day_model_overestimate |
| weather_city_late | cap_adjusted_p40_ev10 | Miami | 0.000 | 0.270 | $-5.00 | 0.724 | 0.331 | 0.484 | -0.500 | 0.919 | capped_day_model_overestimate |
| weather_city_late | cap_adjusted_p40_ev10 | Beijing | 0.000 | 0.190 | $-5.00 | 0.965 | 0.328 | 0.648 | -0.250 | 1.528 | capped_day_model_overestimate |
| weather_city_late | cap_adjusted_p40_ev10 | Chongqing | 0.000 | 0.240 | $-5.00 | 1.000 | 0.307 | 0.693 | -0.650 | 3.636 | capped_day_model_overestimate |
| weather_city_late | cap_adjusted_p40_ev10 | NYC | 0.000 | 0.262 | $-5.00 | 0.999 | 0.072 | 0.928 | -0.500 | 2.737 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | Ankara | 0.000 | 0.280 | $-5.00 | 0.760 | 0.733 | 0.203 | -0.250 | 0.747 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | Seattle | 0.000 | 0.160 | $-5.00 | 0.665 | 0.706 | 0.196 | -0.500 | 0.800 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | TelAviv | 0.000 | 0.320 | $-5.00 | 0.617 | 0.629 | 0.229 | -0.650 | 0.859 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | Denver | 0.000 | 0.130 | $-5.00 | 0.872 | 0.525 | 0.415 | -0.500 | 1.300 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | Miami | 0.000 | 0.270 | $-5.00 | 0.724 | 0.331 | 0.484 | -0.500 | 0.919 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | Beijing | 0.000 | 0.190 | $-5.00 | 0.965 | 0.328 | 0.648 | -0.250 | 1.528 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | Chongqing | 0.000 | 0.240 | $-5.00 | 1.000 | 0.307 | 0.693 | -0.650 | 3.636 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | NYC | 0.000 | 0.262 | $-5.00 | 0.999 | 0.072 | 0.928 | -0.500 | 2.737 | capped_day_model_overestimate |
| weather_city_late | cap_veto_train_top25_risk | Wellington | 1.000 | 0.300 | $+11.67 | 0.757 | 0.475 | 0.398 | 1.150 | -0.658 | win |
| weather_early | base_p40_ev10 | Ankara | 0.000 | 0.280 | $-5.00 | 0.430 | 0.909 | 0.039 | -0.250 | 0.082 | capped_day_shortfall |
| weather_early | base_p40_ev10 | Shanghai | 0.000 | 0.300 | $-5.00 | 0.500 | 0.805 | 0.097 | -0.650 | 0.650 | capped_day_model_overestimate |
| weather_early | base_p40_ev10 | Seattle | 0.000 | 0.310 | $-5.00 | 0.419 | 0.772 | 0.095 | -0.500 | 0.305 | capped_day_shortfall |
| weather_early | base_p40_ev10 | Beijing | 0.000 | 0.190 | $-5.00 | 0.780 | 0.644 | 0.278 | -0.250 | 0.991 | capped_day_model_overestimate |
| weather_early | base_p40_ev10 | Denver | 0.000 | 0.130 | $-5.00 | 0.759 | 0.590 | 0.311 | -0.500 | 1.174 | capped_day_model_overestimate |
| weather_early | base_p40_ev10 | TelAviv | 0.000 | 0.320 | $-5.00 | 0.608 | 0.318 | 0.415 | -0.650 | 0.914 | capped_day_model_overestimate |
| weather_early | base_p40_ev10 | NYC | 0.000 | 0.262 | $-5.00 | 1.000 | 0.190 | 0.810 | -0.500 | 5.253 | capped_day_model_overestimate |
| weather_early | base_p40_ev10 | Chongqing | 0.000 | 0.240 | $-5.00 | 0.996 | 0.157 | 0.840 | -0.650 | 3.212 | capped_day_model_overestimate |
| weather_early | base_p40_ev10 | Wellington | 1.000 | 0.300 | $+11.67 | 0.476 | 0.474 | 0.250 | 1.150 | -1.207 | win |
| weather_early | cap_adjusted_p40_ev10 | NYC | 0.000 | 0.262 | $-5.00 | 1.000 | 0.190 | 0.810 | -0.500 | 5.253 | capped_day_model_overestimate |
| weather_early | cap_adjusted_p40_ev10 | Chongqing | 0.000 | 0.240 | $-5.00 | 0.996 | 0.157 | 0.840 | -0.650 | 3.212 | capped_day_model_overestimate |
| weather_early | cap_veto_train_top25_risk | Shanghai | 0.000 | 0.300 | $-5.00 | 0.500 | 0.805 | 0.097 | -0.650 | 0.650 | capped_day_model_overestimate |
| weather_early | cap_veto_train_top25_risk | Seattle | 0.000 | 0.310 | $-5.00 | 0.419 | 0.772 | 0.095 | -0.500 | 0.305 | capped_day_shortfall |
| weather_early | cap_veto_train_top25_risk | Beijing | 0.000 | 0.190 | $-5.00 | 0.780 | 0.644 | 0.278 | -0.250 | 0.991 | capped_day_model_overestimate |
| weather_early | cap_veto_train_top25_risk | Denver | 0.000 | 0.130 | $-5.00 | 0.759 | 0.590 | 0.311 | -0.500 | 1.174 | capped_day_model_overestimate |
| weather_early | cap_veto_train_top25_risk | TelAviv | 0.000 | 0.320 | $-5.00 | 0.608 | 0.318 | 0.415 | -0.650 | 0.914 | capped_day_model_overestimate |
| weather_early | cap_veto_train_top25_risk | NYC | 0.000 | 0.262 | $-5.00 | 1.000 | 0.190 | 0.810 | -0.500 | 5.253 | capped_day_model_overestimate |
| weather_early | cap_veto_train_top25_risk | Chongqing | 0.000 | 0.240 | $-5.00 | 0.996 | 0.157 | 0.840 | -0.650 | 3.212 | capped_day_model_overestimate |
| weather_early | cap_veto_train_top25_risk | Wellington | 1.000 | 0.300 | $+11.67 | 0.476 | 0.474 | 0.250 | 1.150 | -1.207 | win |
| weather_late | base_p40_ev10 | Shanghai | 0.000 | 0.300 | $-5.00 | 0.406 | 0.872 | 0.052 | -0.650 | 0.482 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | Tokyo | 0.000 | 0.302 | $-5.00 | 0.513 | 0.827 | 0.089 | -0.850 | 0.872 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | Ankara | 0.000 | 0.280 | $-5.00 | 0.760 | 0.765 | 0.179 | -0.250 | 0.747 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | Seattle | 0.000 | 0.160 | $-5.00 | 0.665 | 0.680 | 0.213 | -0.500 | 0.800 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | TelAviv | 0.000 | 0.320 | $-5.00 | 0.617 | 0.573 | 0.263 | -0.650 | 0.859 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | Denver | 0.000 | 0.130 | $-5.00 | 0.872 | 0.447 | 0.482 | -0.500 | 1.300 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | Beijing | 0.000 | 0.190 | $-5.00 | 0.965 | 0.418 | 0.562 | -0.250 | 1.528 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | Miami | 0.000 | 0.270 | $-5.00 | 0.724 | 0.349 | 0.471 | -0.500 | 0.919 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | Chongqing | 0.000 | 0.240 | $-5.00 | 1.000 | 0.187 | 0.813 | -0.650 | 3.636 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | NYC | 0.000 | 0.262 | $-5.00 | 0.999 | 0.059 | 0.940 | -0.500 | 2.737 | capped_day_model_overestimate |
| weather_late | base_p40_ev10 | Wellington | 1.000 | 0.300 | $+11.67 | 0.757 | 0.500 | 0.378 | 1.150 | -0.658 | win |
| weather_late | cap_adjusted_p40_ev10 | Ankara | 0.000 | 0.280 | $-5.00 | 0.871 | 0.509 | 0.427 | -0.250 | 1.046 | capped_day_model_overestimate |
| weather_late | cap_adjusted_p40_ev10 | Denver | 0.000 | 0.130 | $-5.00 | 0.872 | 0.447 | 0.482 | -0.500 | 1.300 | capped_day_model_overestimate |
| weather_late | cap_adjusted_p40_ev10 | Beijing | 0.000 | 0.190 | $-5.00 | 0.965 | 0.418 | 0.562 | -0.250 | 1.528 | capped_day_model_overestimate |
| weather_late | cap_adjusted_p40_ev10 | Miami | 0.000 | 0.270 | $-5.00 | 0.724 | 0.349 | 0.471 | -0.500 | 0.919 | capped_day_model_overestimate |
| weather_late | cap_adjusted_p40_ev10 | Chongqing | 0.000 | 0.240 | $-5.00 | 1.000 | 0.187 | 0.813 | -0.650 | 3.636 | capped_day_model_overestimate |

## Interpretation

1. `p_cap` 是对的方向：它直接学习“高温是否被封顶在 required_gap 以下”，比城市 blacklist 更贴近 6/22 的错误。
2. 但当前可观测特征还没有让 p_cap 在 forward 稳定兑现；top-risk veto 只是减亏，不能翻正。
3. 更关键的新发现是 forecast-overconfidence：`p_cross>=0.95` 且 `p_cap<0.30` 的历史表现不稳定，forward 直接全亏。这说明当前 cap model 仍然信任了 forecast 曲线，没有识别 forecast 自身高估 final max 的 regime。
4. 下一步应补更直接的 day-regime 特征：同日 forecast bias、近期 forecast revision、云雨/海风真实变化、小时观测是否连续刷新高，而不是继续调 p40/p45。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_capped_day_regime_v1/summary.json`
- Cap model metrics: `docs/analysis/2026-06/generated/current_bracket_no_capped_day_regime_v1/cap_model_swap_metrics.csv`
- Variant performance: `docs/analysis/2026-06/generated/current_bracket_no_capped_day_regime_v1/cap_variant_performance.csv`
- Cap bucket calibration: `docs/analysis/2026-06/generated/current_bracket_no_capped_day_regime_v1/cap_risk_bucket_calibration.csv`
- Forecast overconfidence slices: `docs/analysis/2026-06/generated/current_bracket_no_capped_day_regime_v1/forecast_overconfidence_slices.csv`
- 6/22 details: `docs/analysis/2026-06/generated/current_bracket_no_capped_day_regime_v1/cap_2026_06_22_details.csv`
