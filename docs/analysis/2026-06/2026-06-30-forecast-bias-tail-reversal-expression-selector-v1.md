# Forecast-Bias Tail Reversal / Expression Selector v1

Generated: 2026-06-30

## Verdict

在 2026-05-20..2026-06-28，forecast-bias tail-reversal selector 相对 train-best static expression `current_high_yes` 的 forward excess ROI 为 -34.5%（95% CI -44.1%..-15.9%），前瞻 FAIL，结论等级 `inconclusive`。

结论：`inconclusive`。这个 head 作为独立 research head 保留，不改 live，不默认接入 `regime-routed NO` runner。原因是 selector 本身全样本 ROI 为负，且相对 train-best static expression 的 full/forward excess 都显著为负；目前不是独立 forecast-bias alpha，也不是现有 runner 的表达选择改良证据。

significance=FAIL baseline=FAIL forward=FAIL conclusion=inconclusive

## 数据快照

- 数据源：`runtime/weather.db` 自检 + generated expression matrix；主绩效不是 live_real fill PnL。
- 数据快照时间：DB mtime `2026-06-30T11:10:46.438186169+00:00`, fact_built_at_utc `2026-06-30T11:10:16.721495+00:00`。
- 记录行数：fact_trades=4411, fact_signal_candidates=41047, expression same-denominator decisions=2980。
- unsettled 占比：fact_trades NULL/unsettled-like=151 / 4411。
- missing_bracket 数：settlement_status_counts={'NULL': 151, 'settled': 4260}; CLOB gate_pass=True。
- `run_stack.sh` 本轮数据层完成，但因本机 FE 5174 端口仍忙非数据退出；本报告使用 SQLite 自检后的事实层和 generated replay artifacts。

## Head Boundary

- 这是独立 research head：`forecast_bias_tail_reversal_v1`。
- 主分母是同一 city-date-decision snapshot 下 5 个表达都有可用 ask/payoff 的 rows；不使用当前 runner selected trades 做主样本。
- `high_tail_yes` 在本轮用 expression matrix 里的 `lottery_yes_*` 作为 hotter-tail YES proxy；如果以后要 live/shadow，需要把 d1/d2 YES 与 full bracket tail YES 分开记录。
- 每个表达按同一 `$5` stake 计算：`pnl = payoff * (5 / ask) - 5`；ROI 是 total pnl / total cost。

## 8 环覆盖

| 环 | 覆盖 | 说明 |
| --- | --- | --- |
| 1 描述性绩效切片 | yes | expression/selector/city/source/regime/daily |
| 2 统计推断 | yes | target_date block bootstrap ROI/delta CI |
| 3 信号判别 | partial | 只验证 first-principles selector 的 payoff，不训练概率模型 |
| 4 概率分布评估 | no | 本轮没有校准概率分布 |
| 5 执行微结构 | partial | 使用 generated ask；未做 fresh CLOB/depth/live fill |
| 6 容量 | no | 未做 capacity/depth 放大检验 |
| 7 组合相关性 | partial | 使用 target_date block bootstrap；未估 n_eff |
| 8 基准/反事实 | yes | 同分母 static expressions + train-best static baseline |

## Historical City + Source Bias

| city | unit | model | rows | bias | MAE | p10 | p50 | p90 | hot tail | cold tail | tail skew | bias regime |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Dallas | F | gfs | 356 | 3.860 | 3.902 | 1.250 | 4.000 | 6.300 | +92.1% | +0.6% | +91.6% | hot_underforecast_clean |
| Miami | F | ecmwf | 356 | 2.373 | 2.415 | 0.700 | 2.100 | 4.500 | +85.7% | +0.6% | +85.1% | hot_underforecast_clean |
| Manila | C | ecmwf | 356 | 1.897 | 1.927 | 0.667 | 1.889 | 3.222 | +82.0% | +0.0% | +82.0% | hot_underforecast_clean |
| Atlanta | F | ecmwf | 356 | 2.396 | 2.581 | 0.350 | 2.500 | 4.400 | +81.5% | +3.6% | +77.8% | hot_underforecast_clean |
| Guangzhou | C | ecmwf | 356 | 1.916 | 2.064 | 0.111 | 1.944 | 3.667 | +76.4% | +3.1% | +73.3% | hot_underforecast_clean |
| HongKong | C | gfs | 356 | 1.912 | 2.021 | 0.222 | 2.000 | 3.444 | +75.3% | +2.0% | +73.3% | hot_underforecast_clean |
| Jeddah | C | gfs | 356 | 1.497 | 1.613 | 0.056 | 1.500 | 2.833 | +69.7% | +2.0% | +67.7% | hot_underforecast_clean |
| Lagos | C | ecmwf | 346 | 1.505 | 1.819 | 0.000 | 1.722 | 2.945 | +72.0% | +4.6% | +67.3% | hot_underforecast_clean |
| Austin | F | gfs | 737 | 1.620 | 1.715 | 0.100 | 1.500 | 3.400 | +68.1% | +1.4% | +66.8% | hot_underforecast_clean |
| Houston | F | ecmwf | 356 | 2.052 | 2.228 | -0.050 | 1.800 | 4.350 | +70.5% | +3.9% | +66.6% | hot_underforecast_clean |
| Wellington | C | ecmwf | 356 | 1.343 | 1.421 | 0.167 | 1.389 | 2.500 | +65.2% | +0.6% | +64.6% | hot_underforecast_clean |
| PanamaCity | C | ecmwf | 353 | 1.428 | 1.521 | 0.111 | 1.333 | 2.833 | +64.3% | +0.9% | +63.5% | hot_underforecast_clean |
| LA | F | ecmwf | 356 | -6.115 | 6.998 | -14.000 | -6.200 | 1.650 | +14.0% | +76.7% | -62.7% | cold_overforecast_clean |
| Lucknow | C | gfs | 356 | -1.840 | 1.973 | -4.222 | -1.500 | 0.056 | +2.0% | +64.3% | -62.4% | cold_overforecast_clean |
| Minneapolis | F | ecmwf | 356 | 1.626 | 1.919 | -0.500 | 1.550 | 3.950 | +66.8% | +5.1% | +61.8% | hot_underforecast_clean |
| Singapore | C | ecmwf | 356 | 1.332 | 1.377 | 0.111 | 1.222 | 2.667 | +61.5% | +0.3% | +61.2% | hot_underforecast_clean |
| Austin | F | ecmwf | 356 | 1.764 | 1.985 | -0.250 | 1.500 | 4.200 | +65.5% | +4.5% | +61.0% | hot_underforecast_clean |
| Manila | C | gfs | 356 | 1.289 | 1.475 | -0.139 | 1.389 | 2.611 | +64.0% | +3.9% | +60.1% | hot_underforecast_clean |
| Seoul | C | gfs | 737 | 2.855 | 3.369 | -1.022 | 2.889 | 6.778 | +70.2% | +10.3% | +59.8% | hot_underforecast_clean |
| Shenzhen | C | gfs | 356 | -1.357 | 1.568 | -2.833 | -1.444 | 0.167 | +4.2% | +63.8% | -59.5% | cold_overforecast_clean |
| Wellington | C | gfs | 356 | 1.181 | 1.323 | 0.167 | 1.222 | 2.222 | +61.0% | +3.4% | +57.6% | hot_underforecast_clean |
| HongKong | C | ecmwf | 356 | 1.375 | 1.627 | -0.416 | 1.444 | 3.000 | +62.9% | +5.3% | +57.6% | hot_underforecast_clean |
| Chicago | F | ecmwf | 356 | 1.442 | 1.716 | -0.500 | 1.300 | 3.550 | +59.6% | +4.8% | +54.8% | hot_underforecast_clean |
| Phoenix | F | gfs | 737 | 1.116 | 1.238 | -0.100 | 1.100 | 2.400 | +55.6% | +1.6% | +54.0% | hot_underforecast_clean |
| Beijing | C | ecmwf | 356 | 1.228 | 1.458 | -0.333 | 1.167 | 2.778 | +58.1% | +4.5% | +53.7% | hot_underforecast_clean |

Interpretation: large stable positive skew means station actual often beats forecast max; large stable negative skew means forecast often overstates station actual. This is a calibration prior, not a trade gate.

## Same-Denominator Expression A/B

| expression | rows | dates | cities | win | avg ask | ROI | CI low | CI high | losing days | <=-50% days | max daily loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | 2980 | 36 | 36 | +23.6% | 0.35 | -37.5% | -47.5% | -26.2% | 30 | 15 | $-361.53 |
| current_high_yes | 2980 | 36 | 36 | +76.4% | 0.74 | +5.2% | +0.9% | +9.6% | 11 | 0 | $-116.64 |
| d1_no | 2980 | 36 | 36 | +79.2% | 0.79 | +0.8% | -2.4% | +3.8% | 15 | 0 | $-83.14 |
| d2_no | 2980 | 36 | 36 | +97.4% | 0.97 | +0.8% | -0.3% | +1.8% | 12 | 0 | $-29.03 |
| high_tail_yes | 2980 | 36 | 36 | +0.0% | 0.01 | -100.0% | -100.0% | -100.0% | 36 | 36 | $-620.00 |

## Forecast-Bias Selector

| selector | rows | dates | cities | win | avg ask | ROI | CI low | CI high | daily p10 | daily median | daily p90 | losing days | <=-50% days | max daily loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forecast_bias_tail_reversal_v1 | 2980 | 36 | 36 | +54.3% | 0.58 | -17.2% | -25.7% | -6.7% | $-186.68 | $-94.76 | $+54.31 | 30 | 0 | $-223.56 |

Selector mix:

| picked expression | bias regime | rows | share | dates | cities | win | avg ask | ROI | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | hot_underforecast_clean | 903 | +30.3% | 36 | 18 | +28.3% | 0.38 | -19.0% | $-858.51 |
| d1_no | mild_or_mixed | 378 | +12.7% | 34 | 6 | +77.2% | 0.78 | +0.8% | $+15.11 |
| d1_no | balanced_tight | 295 | +9.9% | 33 | 6 | +81.4% | 0.82 | -3.3% | $-48.99 |
| d2_no | cold_overforecast_noisy | 271 | +9.1% | 30 | 6 | +97.0% | 0.98 | -1.1% | $-15.40 |
| d1_no | cold_overforecast_noisy | 260 | +8.7% | 34 | 7 | +71.2% | 0.74 | -1.0% | $-13.04 |
| current_bracket_no | hot_underforecast_noisy | 228 | +7.7% | 35 | 6 | +24.6% | 0.36 | -36.3% | $-413.97 |
| high_tail_yes | hot_underforecast_clean | 205 | +6.9% | 28 | 13 | +0.0% | 0.01 | -100.0% | $-1025.00 |
| d2_no | cold_overforecast_clean | 129 | +4.3% | 21 | 2 | +100.0% | 0.98 | +2.7% | $+17.39 |
| d1_no | cold_overforecast_clean | 75 | +2.5% | 20 | 4 | +77.3% | 0.74 | +2.4% | $+8.92 |
| d1_no | two_sided_noisy | 71 | +2.4% | 22 | 1 | +94.4% | 0.83 | +15.7% | $+55.80 |
| high_tail_yes | hot_underforecast_noisy | 53 | +1.8% | 15 | 5 | +0.0% | 0.02 | -100.0% | $-265.00 |
| current_bracket_no | mild_or_mixed | 31 | +1.0% | 13 | 5 | +61.3% | 0.50 | +12.1% | $+18.77 |
| d1_no | unclassified | 28 | +0.9% | 3 | 9 | +78.6% | 0.76 | +6.7% | $+9.41 |
| current_bracket_no | balanced_tight | 23 | +0.8% | 11 | 4 | +34.8% | 0.39 | -42.9% | $-49.34 |
| current_high_yes | cold_overforecast_noisy | 15 | +0.5% | 7 | 5 | +66.7% | 0.75 | -4.3% | $-3.22 |
| current_high_yes | mild_or_mixed | 10 | +0.3% | 3 | 2 | +100.0% | 0.84 | +22.2% | $+11.09 |
| current_high_yes | balanced_tight | 4 | +0.1% | 2 | 2 | +25.0% | 0.81 | -72.8% | $-14.57 |
| current_high_yes | cold_overforecast_clean | 1 | +0.0% | 1 | 1 | +100.0% | 0.93 | +7.5% | $+0.38 |

## Baseline / Market-Structure Check

| window | baseline | rows | dates | selector ROI | baseline ROI | delta ROI | CI low | CI high | delta pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | static_current_high_yes | 2980 | 36 | -17.2% | +5.2% | -22.5% | -32.2% | -10.4% | $-3351.91 |
| forward | static_current_high_yes | 214 | 3 | -26.2% | +8.2% | -34.5% | -44.1% | -15.9% | $-368.89 |
| full | static_d1_no | 2980 | 36 | -17.2% | +0.8% | -18.0% | -26.9% | -6.7% | $-2683.01 |
| forward | static_d1_no | 214 | 3 | -26.2% | +1.4% | -27.7% | -33.5% | -18.5% | $-296.12 |
| full | static_d2_no | 2980 | 36 | -17.2% | +0.8% | -18.0% | -26.7% | -7.4% | $-2685.29 |
| forward | static_d2_no | 214 | 3 | -26.2% | +0.2% | -26.4% | -43.7% | -4.0% | $-282.45 |
| full | static_high_tail_yes | 2980 | 36 | -17.2% | -100.0% | +82.8% | +74.3% | +93.3% | $+12329.86 |
| forward | static_high_tail_yes | 214 | 3 | -26.2% | -100.0% | +73.8% | +61.1% | +88.2% | $+789.36 |
| full | static_current_bracket_no | 2980 | 36 | -17.2% | -37.5% | +20.2% | +10.4% | +30.4% | $+3011.94 |
| forward | static_current_bracket_no | 214 | 3 | -26.2% | -45.1% | +18.9% | -3.2% | +30.9% | $+201.79 |
| full | train_best_static_current_high_yes | 2980 | 36 | -17.2% | +5.2% | -22.5% | -32.2% | -10.4% | $-3351.91 |
| forward | train_best_static_current_high_yes | 214 | 3 | -26.2% | +8.2% | -34.5% | -44.1% | -15.9% | $-368.89 |

If the selector cannot beat a static expression chosen only on train dates in the forward window, the result is not forecast-bias alpha; it is likely expression/base-rate or sample noise.

## Daily PnL Distribution

| date | rows | cities | cost | pnl | ROI |
| --- | --- | --- | --- | --- | --- |
| 2026-05-19 | 11 | 3 | $+55.00 | $-24.70 | -44.9% |
| 2026-05-20 | 78 | 22 | $+390.00 | $-135.25 | -34.7% |
| 2026-05-21 | 106 | 25 | $+530.00 | $-205.68 | -38.8% |
| 2026-05-22 | 69 | 19 | $+345.00 | $-68.59 | -19.9% |
| 2026-05-23 | 80 | 22 | $+400.00 | $+229.57 | +57.4% |
| 2026-05-24 | 78 | 18 | $+390.00 | $-172.62 | -44.3% |
| 2026-05-25 | 83 | 23 | $+415.00 | $+29.24 | +7.0% |
| 2026-05-26 | 75 | 18 | $+375.00 | $-47.56 | -12.7% |
| 2026-05-27 | 99 | 24 | $+495.00 | $-132.44 | -26.8% |
| 2026-05-28 | 56 | 16 | $+280.00 | $-94.38 | -33.7% |
| 2026-05-29 | 86 | 21 | $+430.00 | $+19.99 | +4.6% |
| 2026-05-30 | 73 | 18 | $+365.00 | $-67.24 | -18.4% |
| 2026-05-31 | 76 | 19 | $+380.00 | $-178.51 | -47.0% |
| 2026-06-01 | 71 | 21 | $+355.00 | $-80.35 | -22.6% |
| 2026-06-02 | 59 | 15 | $+295.00 | $-7.76 | -2.6% |
| 2026-06-03 | 74 | 22 | $+370.00 | $-95.14 | -25.7% |
| 2026-06-04 | 67 | 19 | $+335.00 | $+79.38 | +23.7% |
| 2026-06-05 | 72 | 20 | $+360.00 | $-102.20 | -28.4% |
| 2026-06-06 | 96 | 22 | $+480.00 | $+335.49 | +69.9% |
| 2026-06-07 | 104 | 26 | $+520.00 | $-154.22 | -29.7% |
| 2026-06-08 | 124 | 28 | $+620.00 | $-181.02 | -29.2% |
| 2026-06-09 | 116 | 28 | $+580.00 | $-188.69 | -32.5% |
| 2026-06-10 | 95 | 22 | $+475.00 | $-49.22 | -10.4% |
| 2026-06-11 | 71 | 20 | $+355.00 | $+220.26 | +62.0% |
| 2026-06-12 | 100 | 24 | $+500.00 | $-155.60 | -31.1% |
| 2026-06-13 | 101 | 27 | $+505.00 | $-70.02 | -13.9% |
| 2026-06-14 | 100 | 23 | $+500.00 | $-60.94 | -12.2% |
| 2026-06-15 | 91 | 23 | $+455.00 | $-156.01 | -34.3% |
| 2026-06-16 | 102 | 24 | $+510.00 | $-223.56 | -43.8% |
| 2026-06-17 | 92 | 24 | $+460.00 | $-184.67 | -40.1% |
| 2026-06-18 | 70 | 20 | $+350.00 | $-98.65 | -28.2% |
| 2026-06-19 | 109 | 31 | $+545.00 | $-78.25 | -14.4% |
| 2026-06-20 | 82 | 20 | $+410.00 | $-190.17 | -46.4% |
| 2026-06-21 | 74 | 22 | $+370.00 | $-102.98 | -27.8% |
| 2026-06-22 | 70 | 19 | $+350.00 | $-136.31 | -38.9% |
| 2026-06-23 | 70 | 18 | $+350.00 | $-41.34 | -11.8% |

## City / Source Contribution

| city | model | bias regime | rows | dates | win | avg ask | ROI | pnl | CI low | CI high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ankara | gfs | hot_underforecast_clean | 63 | 20 | +34.9% | 0.34 | +51.8% | $+163.11 | -63.5% | +248.8% |
| Wellington | gfs | hot_underforecast_clean | 87 | 22 | +18.4% | 0.13 | +29.6% | $+128.94 | -96.3% | +197.3% |
| TelAviv | gfs | mild_or_mixed | 91 | 24 | +81.3% | 0.70 | +17.6% | $+80.23 | -7.1% | +39.1% |
| Seattle | gfs | hot_underforecast_clean | 40 | 13 | +55.0% | 0.38 | +36.2% | $+72.49 | -47.7% | +141.6% |
| Taipei | gfs | two_sided_noisy | 71 | 22 | +94.4% | 0.83 | +15.7% | $+55.80 | +0.9% | +27.3% |
| Istanbul | gfs | cold_overforecast_noisy | 141 | 27 | +85.8% | 0.84 | +5.1% | $+36.27 | -10.4% | +21.7% |
| SanFrancisco | gfs | cold_overforecast_clean | 83 | 20 | +94.0% | 0.88 | +8.3% | $+34.25 | -6.9% | +22.2% |
| Wuhan | gfs | mild_or_mixed | 72 | 18 | +75.0% | 0.76 | +7.4% | $+26.56 | -22.5% | +44.0% |
| Jeddah | ecmwf | cold_overforecast_clean | 14 | 4 | +100.0% | 0.79 | +28.8% | $+20.17 | +13.3% | +45.0% |
| Wellington | unknown | unclassified | 2 | 1 | +100.0% | 0.35 | +185.7% | $+18.57 |  |  |
| Helsinki | ecmwf | hot_underforecast_clean | 7 | 2 | +100.0% | 0.72 | +45.0% | $+15.77 |  |  |
| Houston | unknown | unclassified | 3 | 1 | +100.0% | 0.59 | +69.5% | $+10.42 |  |  |
| Ankara | ecmwf | hot_underforecast_clean | 12 | 3 | +75.0% | 0.61 | +13.3% | $+7.96 | -100.0% | +53.8% |
| Madrid | ecmwf | balanced_tight | 6 | 1 | +100.0% | 0.80 | +26.1% | $+7.84 |  |  |
| Manila | unknown | unclassified | 3 | 1 | +100.0% | 0.71 | +40.2% | $+6.03 |  |  |
| Chengdu | gfs | hot_underforecast_clean | 65 | 20 | +40.0% | 0.38 | +1.5% | $+4.77 | -63.3% | +74.5% |
| CapeTown | gfs | cold_overforecast_noisy | 97 | 24 | +87.6% | 0.89 | +0.6% | $+2.67 | -13.3% | +15.2% |
| Beijing | gfs | cold_overforecast_noisy | 19 | 9 | +78.9% | 0.80 | +2.7% | $+2.55 | -39.0% | +50.5% |
| LA | unknown | unclassified | 3 | 1 | +100.0% | 0.96 | +4.2% | $+0.63 |  |  |
| Busan | unknown | unclassified | 1 | 1 | +100.0% | 0.96 | +4.2% | $+0.21 |  |  |
| Miami | unknown | unclassified | 2 | 1 | +100.0% | 0.99 | +1.3% | $+0.13 |  |  |
| Wuhan | ecmwf | hot_underforecast_noisy | 15 | 3 | +53.3% | 0.33 | +0.1% | $+0.06 | -100.0% | +23.0% |
| SaoPaulo | gfs | mild_or_mixed | 92 | 23 | +76.1% | 0.75 | -0.6% | $-2.63 | -23.8% | +22.6% |
| Chengdu | ecmwf | hot_underforecast_clean | 3 | 2 | +33.3% | 0.37 | -24.9% | $-3.74 |  |  |
| Austin | unknown | unclassified | 2 | 1 | +50.0% | 0.79 | -47.7% | $-4.77 |  |  |
| Busan | ecmwf | balanced_tight | 14 | 2 | +57.1% | 0.72 | -10.0% | $-7.02 |  |  |
| NYC | gfs | cold_overforecast_noisy | 104 | 25 | +78.8% | 0.82 | -1.5% | $-7.76 | -23.0% | +18.2% |
| Lucknow | gfs | cold_overforecast_clean | 7 | 3 | +57.1% | 0.65 | -25.4% | $-8.90 | -100.0% | +38.9% |
| LA | gfs | cold_overforecast_noisy | 65 | 17 | +87.7% | 0.91 | -2.8% | $-9.16 | -24.0% | +14.9% |
| Karachi | ecmwf | cold_overforecast_noisy | 12 | 3 | +50.0% | 0.65 | -15.3% | $-9.18 | -100.0% | +69.4% |
| Istanbul | unknown | unclassified | 4 | 3 | +50.0% | 0.76 | -46.8% | $-9.36 | -100.0% | +6.4% |
| Warsaw | ecmwf | hot_underforecast_noisy | 9 | 4 | +22.2% | 0.39 | -21.8% | $-9.80 | -100.0% | +184.0% |
| Dallas | ecmwf | hot_underforecast_clean | 5 | 1 | +40.0% | 0.31 | -40.3% | $-10.07 |  |  |
| SanFrancisco | unknown | unclassified | 8 | 2 | +62.5% | 0.79 | -31.1% | $-12.45 |  |  |
| Tokyo | gfs | balanced_tight | 49 | 13 | +83.7% | 0.83 | -5.4% | $-13.11 | -27.2% | +15.7% |

## Regime Contribution

Day regime:

| day regime | bias regime | rows | dates | cities | win | avg ask | ROI | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| day_marginal_runway | mild_or_mixed | 126 | 25 | 6 | +82.5% | 0.76 | +14.8% | $+93.09 |
| day_open_runway | cold_overforecast_noisy | 124 | 24 | 6 | +78.2% | 0.73 | +14.1% | $+87.69 |
| day_marginal_runway | hot_underforecast_noisy | 13 | 6 | 3 | +76.9% | 0.43 | +68.9% | $+44.75 |
| day_forecast_busted | two_sided_noisy | 36 | 13 | 1 | +100.0% | 0.85 | +21.0% | $+37.79 |
| day_open_runway | cold_overforecast_clean | 38 | 12 | 4 | +92.1% | 0.79 | +17.5% | $+33.19 |
| day_open_runway | mild_or_mixed | 40 | 15 | 5 | +70.0% | 0.57 | +14.8% | $+29.60 |
| day_forecast_capped | two_sided_noisy | 26 | 7 | 1 | +96.2% | 0.83 | +20.2% | $+26.28 |
| day_forecast_capped | balanced_tight | 177 | 26 | 5 | +87.0% | 0.85 | +2.2% | $+19.73 |
| day_forecast_busted | balanced_tight | 54 | 12 | 5 | +90.7% | 0.85 | +6.2% | $+16.81 |
| day_forecast_capped | cold_overforecast_clean | 100 | 16 | 2 | +100.0% | 0.97 | +3.2% | $+16.24 |
| day_space_unknown | mild_or_mixed | 4 | 2 | 2 | +100.0% | 0.59 | +76.5% | $+15.30 |
| day_forecast_busted | cold_overforecast_noisy | 77 | 18 | 4 | +100.0% | 0.97 | +2.8% | $+10.75 |
| day_space_unknown | unclassified | 28 | 3 | 9 | +78.6% | 0.76 | +6.7% | $+9.41 |
| day_forecast_busted | cold_overforecast_clean | 29 | 6 | 1 | +100.0% | 0.99 | +0.8% | $+1.15 |
| day_space_unknown | hot_underforecast_noisy | 1 | 1 | 1 | +0.0% | 0.01 | -100.0% | $-5.00 |
| day_marginal_runway | two_sided_noisy | 9 | 3 | 1 | +66.7% | 0.77 | -18.4% | $-8.27 |
| day_forecast_capped | mild_or_mixed | 135 | 26 | 5 | +75.6% | 0.78 | -2.9% | $-19.55 |
| day_marginal_runway | cold_overforecast_clean | 38 | 12 | 3 | +63.2% | 0.69 | -12.6% | $-23.89 |
| day_forecast_capped | cold_overforecast_noisy | 194 | 26 | 6 | +95.9% | 0.98 | -2.7% | $-26.15 |
| day_open_runway | hot_underforecast_noisy | 12 | 4 | 3 | +33.3% | 0.53 | -49.5% | $-29.71 |
| day_space_unknown | hot_underforecast_clean | 9 | 2 | 5 | +0.0% | 0.16 | -100.0% | $-45.00 |
| day_open_runway | balanced_tight | 23 | 11 | 4 | +34.8% | 0.39 | -42.9% | $-49.34 |
| day_forecast_busted | mild_or_mixed | 114 | 19 | 6 | +72.8% | 0.82 | -12.9% | $-73.47 |
| day_open_runway | hot_underforecast_clean | 42 | 16 | 8 | +35.7% | 0.28 | -41.6% | $-87.29 |
| day_marginal_runway | balanced_tight | 68 | 17 | 5 | +55.9% | 0.73 | -29.4% | $-100.09 |
| day_marginal_runway | cold_overforecast_noisy | 151 | 27 | 7 | +64.9% | 0.75 | -13.8% | $-103.96 |
| day_forecast_capped | hot_underforecast_noisy | 94 | 24 | 6 | +20.2% | 0.26 | -55.3% | $-259.80 |
| day_marginal_runway | hot_underforecast_clean | 123 | 21 | 10 | +25.2% | 0.42 | -46.7% | $-287.01 |
| day_forecast_busted | hot_underforecast_noisy | 161 | 27 | 4 | +14.3% | 0.29 | -53.3% | $-429.22 |
| day_forecast_capped | hot_underforecast_clean | 398 | 35 | 16 | +25.9% | 0.37 | -33.3% | $-662.25 |
| day_forecast_busted | hot_underforecast_clean | 536 | 34 | 16 | +20.0% | 0.26 | -29.9% | $-801.96 |

Intraday state:

| intraday | bias regime | rows | dates | cities | win | avg ask | ROI | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| plateau_near_high | hot_underforecast_clean | 99 | 21 | 10 | +34.3% | 0.28 | +23.1% | $+114.36 |
| plateau_near_high | hot_underforecast_noisy | 51 | 12 | 3 | +25.5% | 0.28 | +22.4% | $+57.13 |
| plateau_near_high | cold_overforecast_noisy | 53 | 16 | 4 | +90.6% | 0.83 | +18.3% | $+48.59 |
| false_fade_risk | mild_or_mixed | 35 | 11 | 5 | +94.3% | 0.77 | +24.6% | $+43.00 |
| false_fade_risk | cold_overforecast_noisy | 43 | 13 | 6 | +97.7% | 0.85 | +17.2% | $+37.02 |
| plateau_near_high | mild_or_mixed | 54 | 16 | 5 | +81.5% | 0.76 | +11.9% | $+32.21 |
| fresh_high | cold_overforecast_noisy | 147 | 30 | 7 | +83.0% | 0.83 | +3.3% | $+24.36 |
| fresh_high | two_sided_noisy | 17 | 6 | 1 | +100.0% | 0.82 | +27.2% | $+23.08 |
| pullback_uncertain | cold_overforecast_noisy | 44 | 13 | 4 | +100.0% | 0.92 | +9.8% | $+21.46 |
| plateau_near_high | balanced_tight | 54 | 16 | 4 | +83.3% | 0.81 | +6.4% | $+17.29 |
| pullback_uncertain | cold_overforecast_clean | 33 | 10 | 3 | +100.0% | 0.93 | +9.6% | $+15.86 |
| active_warming | unclassified | 19 | 3 | 6 | +78.9% | 0.72 | +16.6% | $+15.80 |
| slow_warming | cold_overforecast_noisy | 6 | 3 | 2 | +100.0% | 0.74 | +49.2% | $+14.77 |
| false_fade_risk | two_sided_noisy | 15 | 7 | 1 | +100.0% | 0.86 | +19.7% | $+14.76 |
| fresh_high | mild_or_mixed | 99 | 24 | 6 | +73.7% | 0.71 | +2.7% | $+13.40 |
| false_fade_risk | cold_overforecast_clean | 23 | 7 | 3 | +100.0% | 0.91 | +10.2% | $+11.75 |
| slow_warming | mild_or_mixed | 7 | 3 | 3 | +100.0% | 0.84 | +21.8% | $+7.63 |
| pullback_uncertain | two_sided_noisy | 3 | 1 | 1 | +100.0% | 0.70 | +42.9% | $+6.43 |
| plateau_near_high | cold_overforecast_clean | 3 | 1 | 1 | +100.0% | 0.72 | +38.9% | $+5.83 |
| plateau_near_high | two_sided_noisy | 17 | 5 | 1 | +82.4% | 0.77 | +6.8% | $+5.76 |
| active_warming | two_sided_noisy | 4 | 3 | 1 | +100.0% | 0.85 | +23.5% | $+4.69 |
| reheating_after_dip | balanced_tight | 9 | 6 | 3 | +77.8% | 0.76 | +9.2% | $+4.12 |
| state_unknown | cold_overforecast_noisy | 8 | 2 | 2 | +100.0% | 0.92 | +8.7% | $+3.48 |
| plateau_near_high | unclassified | 1 | 1 | 1 | +100.0% | 0.70 | +42.9% | $+2.14 |
| fresh_high | cold_overforecast_clean | 30 | 11 | 3 | +86.7% | 0.86 | +1.2% | $+1.83 |
| mature_fade | cold_overforecast_clean | 5 | 4 | 1 | +100.0% | 0.95 | +5.1% | $+1.28 |
| mature_fade | two_sided_noisy | 15 | 7 | 1 | +93.3% | 0.92 | +1.4% | $+1.08 |
| slow_warming | balanced_tight | 3 | 1 | 1 | +100.0% | 0.95 | +5.3% | $+0.79 |
| flat_or_cooling | mild_or_mixed | 2 | 1 | 1 | +100.0% | 0.93 | +7.5% | $+0.75 |
| pullback_uncertain | balanced_tight | 13 | 6 | 4 | +92.3% | 0.88 | +1.1% | $+0.74 |
| pullback_uncertain | unclassified | 1 | 1 | 1 | +100.0% | 0.94 | +6.4% | $+0.32 |
| flat_or_cooling | unclassified | 1 | 1 | 1 | +100.0% | 0.94 | +6.4% | $+0.32 |
| mature_fade | cold_overforecast_noisy | 28 | 12 | 6 | +82.1% | 0.85 | -1.2% | $-1.68 |
| fresh_high | unclassified | 4 | 2 | 2 | +75.0% | 0.86 | -21.9% | $-4.37 |
| false_fade_risk | unclassified | 2 | 2 | 2 | +50.0% | 0.78 | -47.9% | $-4.79 |

Moisture/cloud:

| moisture/cloud | bias regime | rows | dates | cities | win | avg ask | ROI | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mixed_moisture | mild_or_mixed | 189 | 30 | 6 | +85.2% | 0.73 | +17.0% | $+160.20 |
| humid_overcast_suppression | hot_underforecast_clean | 96 | 18 | 4 | +16.7% | 0.19 | +14.7% | $+70.68 |
| mixed_moisture | cold_overforecast_clean | 150 | 25 | 3 | +94.7% | 0.90 | +6.4% | $+48.20 |
| mixed_moisture | two_sided_noisy | 45 | 16 | 1 | +97.8% | 0.85 | +18.8% | $+42.26 |
| humid_overcast_suppression | cold_overforecast_noisy | 17 | 6 | 5 | +100.0% | 0.78 | +36.6% | $+31.08 |
| cloud_suppression | mild_or_mixed | 55 | 14 | 4 | +87.3% | 0.82 | +11.1% | $+30.54 |
| humid_convective_risk | two_sided_noisy | 24 | 6 | 1 | +87.5% | 0.80 | +9.9% | $+11.92 |
| humid_convective_risk | cold_overforecast_noisy | 48 | 13 | 4 | +85.4% | 0.86 | +3.9% | $+9.42 |
| cloud_suppression | unclassified | 5 | 1 | 2 | +80.0% | 0.67 | +22.6% | $+5.65 |
| humid_overcast_suppression | cold_overforecast_clean | 14 | 3 | 1 | +100.0% | 0.95 | +5.6% | $+3.94 |
| mixed_moisture | unclassified | 23 | 3 | 7 | +78.3% | 0.78 | +3.3% | $+3.76 |
| moisture_cloud_unknown | cold_overforecast_noisy | 8 | 2 | 2 | +100.0% | 0.92 | +8.7% | $+3.48 |
| dry_heat_inertia | two_sided_noisy | 2 | 1 | 1 | +100.0% | 0.86 | +16.3% | $+1.63 |
| humid_convective_risk | cold_overforecast_clean | 14 | 3 | 1 | +92.9% | 0.94 | -0.7% | $-0.47 |
| dry_heat_inertia | cold_overforecast_clean | 18 | 6 | 3 | +83.3% | 0.80 | -0.7% | $-0.66 |
| humid_convective_risk | balanced_tight | 33 | 7 | 4 | +78.8% | 0.79 | -3.9% | $-6.44 |
| cloud_suppression | balanced_tight | 47 | 12 | 2 | +74.5% | 0.76 | -4.5% | $-10.58 |
| mixed_moisture | cold_overforecast_noisy | 355 | 31 | 7 | +85.1% | 0.87 | -0.9% | $-16.71 |
| cloud_suppression | cold_overforecast_clean | 9 | 3 | 2 | +44.4% | 0.69 | -54.1% | $-24.33 |
| cloud_suppression | cold_overforecast_noisy | 49 | 11 | 7 | +69.4% | 0.80 | -11.5% | $-28.16 |
| dry_heat_inertia | cold_overforecast_noisy | 69 | 17 | 5 | +81.2% | 0.87 | -8.9% | $-30.78 |
| dry_heat_inertia | balanced_tight | 28 | 8 | 3 | +60.7% | 0.75 | -27.3% | $-38.23 |
| humid_convective_risk | mild_or_mixed | 64 | 13 | 4 | +68.8% | 0.76 | -12.9% | $-41.42 |
| cloud_suppression | hot_underforecast_noisy | 35 | 8 | 2 | +28.6% | 0.30 | -23.9% | $-41.79 |
| humid_overcast_suppression | mild_or_mixed | 23 | 6 | 4 | +30.4% | 0.63 | -39.9% | $-45.91 |
| humid_overcast_suppression | hot_underforecast_noisy | 19 | 5 | 3 | +26.3% | 0.26 | -50.3% | $-47.82 |
| mixed_moisture | balanced_tight | 214 | 31 | 5 | +79.9% | 0.80 | -5.4% | $-57.65 |
| dry_heat_inertia | mild_or_mixed | 88 | 21 | 6 | +69.3% | 0.82 | -13.3% | $-58.43 |
| cloud_suppression | hot_underforecast_clean | 64 | 18 | 8 | +26.6% | 0.28 | -22.7% | $-72.52 |
| dry_heat_inertia | hot_underforecast_noisy | 46 | 13 | 2 | +19.6% | 0.33 | -62.4% | $-143.54 |
| humid_convective_risk | hot_underforecast_noisy | 50 | 16 | 5 | +12.0% | 0.08 | -77.9% | $-194.71 |
| dry_heat_inertia | hot_underforecast_clean | 189 | 34 | 10 | +36.0% | 0.39 | -21.3% | $-201.23 |
| mixed_moisture | hot_underforecast_noisy | 131 | 27 | 6 | +19.8% | 0.37 | -38.3% | $-251.12 |
| humid_convective_risk | hot_underforecast_clean | 110 | 22 | 9 | +3.6% | 0.02 | -91.1% | $-501.29 |
| mixed_moisture | hot_underforecast_clean | 649 | 36 | 16 | +23.3% | 0.37 | -36.3% | $-1179.14 |

## Interpretation

1. Historical city/source forecast bias is real and stable enough to be a feature layer: hot-underforecast cities and cold-overforecast cities are not symmetric.
2. Same-denominator expression payoff matters more than raw forecast-bias labels. `d1_no` / `d2_no` can look strong because of base-rate and ask level, not necessarily because bias selected them.
3. The first-principles selector has negative full-sample point estimate and negative excess over train-best static expression in the forward slice. That fails significance, baseline, and forward gates.
4. Therefore this is not a confirmed independent tail-reversal alpha, and it is not a runner-specific expression-selection improvement.

## Next Evidence To Collect

- Keep it as independent `forecast_bias_tail_reversal_v1` shadow research head.
- Add explicit d1/d2 hotter YES quotes, not only `lottery_yes` proxy.
- Forward-log selector decision, selected expression, all sibling asks, source-bias regime, day/intraday/moisture/wind regime, and realized payoff at decision time.
- Only after settled forward rows show positive excess vs train-best static expression should we decide whether this is a new strategy family or just an expression selector for an existing runner.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_forecast_bias_tail_reversal_expression_selector_v1.py`
- JSON summary: `docs/analysis/2026-06/2026-06-30-forecast-bias-tail-reversal-expression-selector-v1.json`
- Same-denominator long rows: `docs/analysis/2026-06/generated/forecast_bias_tail_reversal_expression_selector_v1/same_denominator_expression_rows.csv`
- Expression summary: `docs/analysis/2026-06/generated/forecast_bias_tail_reversal_expression_selector_v1/expression_summary.csv`
- Selector rows: `docs/analysis/2026-06/generated/forecast_bias_tail_reversal_expression_selector_v1/selector_rows.csv`
- Selector daily: `docs/analysis/2026-06/generated/forecast_bias_tail_reversal_expression_selector_v1/selector_daily.csv`
- Baseline comparison: `docs/analysis/2026-06/generated/forecast_bias_tail_reversal_expression_selector_v1/selector_vs_baselines.csv`
