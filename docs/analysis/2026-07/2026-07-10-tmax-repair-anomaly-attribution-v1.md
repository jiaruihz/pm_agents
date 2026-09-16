# Tmax Repair Anomaly Attribution v1

> generated_at_utc: `2026-07-10T15:47:00.746662+00:00`
> Frozen expanding model and execution policy. Feature groups are restored one at a time; no new gate, city exclusion, or threshold search.

## 结论

- 修复后的 selected ROI 从 12.0% 降到 8.1%，但 paired date-block delta 为 -3.9%，95% CI [-10.9%, +3.3%]，不能证明真实 edge 下降。
- 这次历史对比实际只检验了 humidity/sky：6/21+ 的 GFS/ECMWF gap 覆盖率为 0%，所以 forecast-gap 修复尚无 forward ROI 证据。
- humidity/sky 让分布 logloss 从 0.5926 改善到 0.5887，但 first-lock max-edge 选单把小概率变化离散成取消/新增/换表达；概率模型改善不等于当前 selector 的 ROI 必然改善。
- 收益下降主要来自取消旧机会和新增弱机会，不来自换表达：old_only -5.22 PnL、full_only -1.29、switched_expression +1.45。
- 负向归因集中在 RH 60-80%、cloud suppression / humid convective、late morning / solar peak、forecast capped；dry heat / open runway 改善。城市差异样本太薄，不支持城市 blacklist。
- `mechanism_interactions_full` 只增加 cloud×solar/path 与 humidity×runway 交互，并冻结 C/alpha/执行阈值；它是本轮事后机制诊断，不是独立 forward 或 live 候选。
- 该交互诊断版没有改善：forward logloss 0.5891（full 0.5887），selected ROI 6.0%（full 8.1%）。因此不采纳该改法。

## Feature Coverage Audit

| scope | feature | rows | non_null_rows | coverage | first_date | last_date |
| --- | --- | --- | --- | --- | --- | --- |
| all | gfs_gap_to_running_native | 8376 | 6299 | 0.7520 | 2026-05-19 | 2026-06-20 |
| all | ecmwf_gap_to_running_native | 8376 | 6299 | 0.7520 | 2026-05-19 | 2026-06-20 |
| all | relative_humidity_pct | 8376 | 8371 | 0.9994 | 2026-05-19 | 2026-07-08 |
| all | sky_cover_code | 8376 | 8376 | 1.0000 | 2026-05-19 | 2026-07-08 |
| verified_forward | gfs_gap_to_running_native | 2077 | 0 | 0.0000 |  |  |
| verified_forward | ecmwf_gap_to_running_native | 2077 | 0 | 0.0000 |  |  |
| verified_forward | relative_humidity_pct | 2077 | 2074 | 0.9986 | 2026-06-21 | 2026-07-08 |
| verified_forward | sky_cover_code | 2077 | 2077 | 1.0000 | 2026-06-21 | 2026-07-08 |

## Feature-group Probability Scores

| variant | rows | dates | logloss | brier |
| --- | --- | --- | --- | --- |
| both_gaps_only | 2077 | 16 | 0.5926 | 0.3308 |
| ecmwf_gap_only | 2077 | 16 | 0.5926 | 0.3308 |
| full_repaired | 2077 | 16 | 0.5887 | 0.3283 |
| gfs_gap_only | 2077 | 16 | 0.5926 | 0.3308 |
| humidity_only | 2077 | 16 | 0.5912 | 0.3299 |
| humidity_sky_only | 2077 | 16 | 0.5887 | 0.3283 |
| mechanism_interactions_full | 2077 | 16 | 0.5891 | 0.3286 |
| old_all_missing | 2077 | 16 | 0.5926 | 0.3308 |
| sky_only | 2077 | 16 | 0.5904 | 0.3294 |

## Feature-group Selected Policy

| variant | rows | dates | cities | win_rate | avg_ask | pnl | roi | roi_ci_low | roi_ci_high | yes_rows | no_rows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| both_gaps_only | 172 | 16 | 36 | 0.7093 | 0.6226 | 13.0719 | 0.1200 | 0.0046 | 0.2329 | 33 | 139 |
| ecmwf_gap_only | 172 | 16 | 36 | 0.7093 | 0.6226 | 13.0719 | 0.1200 | 0.0046 | 0.2329 | 33 | 139 |
| full_repaired | 159 | 16 | 36 | 0.6918 | 0.6296 | 8.2244 | 0.0808 | -0.0161 | 0.1652 | 34 | 125 |
| gfs_gap_only | 172 | 16 | 36 | 0.7093 | 0.6226 | 13.0719 | 0.1200 | 0.0046 | 0.2329 | 33 | 139 |
| humidity_only | 175 | 16 | 36 | 0.7086 | 0.6223 | 13.2167 | 0.1193 | 0.0154 | 0.2204 | 33 | 142 |
| humidity_sky_only | 159 | 16 | 36 | 0.6918 | 0.6296 | 8.2244 | 0.0808 | -0.0161 | 0.1652 | 34 | 125 |
| mechanism_interactions_full | 170 | 16 | 35 | 0.6765 | 0.6275 | 6.5316 | 0.0602 | -0.0301 | 0.1407 | 42 | 128 |
| old_all_missing | 172 | 16 | 36 | 0.7093 | 0.6226 | 13.0719 | 0.1200 | 0.0046 | 0.2329 | 33 | 139 |
| sky_only | 167 | 16 | 36 | 0.7006 | 0.6217 | 11.3920 | 0.1079 | -0.0005 | 0.2146 | 36 | 131 |

## Selection Transition Attribution

| transition | rows | dates | old_pnl | full_pnl | delta_pnl |
| --- | --- | --- | --- | --- | --- |
| old_only | 40 | 13 | 5.2153 | 0.0000 | -5.2153 |
| full_only | 27 | 14 | 0.0000 | -1.2858 | -1.2858 |
| same_expression | 118 | 16 | 10.5216 | 10.7269 | 0.2053 |
| switched_expression | 14 | 10 | -2.6651 | -1.2167 | 1.4484 |

`old_only/full_only` 表示同一 city-day 只在一个版本跨过 edge 门；`switched_expression` 才是真正换方向/换档。

## Same-state Same-expression Counterfactual

| source_variant | selected_rows | still_eligible_rows | still_eligible_share | mean_probability_delta | lost_rows | lost_realized_pnl | lost_realized_roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full_repaired | 159 | 123 | 0.7736 | -0.0106 | 36 | -1.9985 | -0.0869 |
| old_all_missing | 172 | 122 | 0.7093 | -0.0090 | 50 | 2.8174 | 0.0904 |

下面是旧版机会在补入 humidity/sky 后，于同一时点、同一表达跌破 edge 门的明细汇总：

| expression | rows | dates | win_rate | avg_ask | old_p_win | full_p_win | mean_probability_delta | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1_no | 36 | 12 | 0.6944 | 0.6159 | 0.6636 | 0.6222 | -0.0413 | 2.4308 |
| d2_no | 5 | 5 | 0.8000 | 0.7200 | 0.7535 | 0.7469 | -0.0067 | 0.3536 |
| current_no | 1 | 1 | 1.0000 | 0.7100 | 0.7586 | 0.7374 | -0.0211 | 0.2797 |
| d2_yes | 8 | 7 | 0.5000 | 0.5188 | 0.5579 | 0.5430 | -0.0149 | -0.2467 |

## Expression-level Probability Skill

| expression | rows | base_rate | old_avg_p | full_avg_p | old_logloss | full_logloss | old_brier | full_brier | delta_logloss | delta_brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_no | 2077 | 0.5200 | 0.4944 | 0.5118 | 0.2580 | 0.2542 | 0.0794 | 0.0781 | -0.0038 | -0.0013 |
| d1_no | 2077 | 0.7747 | 0.7898 | 0.7747 | 0.3575 | 0.3552 | 0.1163 | 0.1152 | -0.0023 | -0.0011 |
| d1_yes | 2077 | 0.2253 | 0.2102 | 0.2253 | 0.3575 | 0.3552 | 0.1163 | 0.1152 | -0.0023 | -0.0011 |
| d2_no | 2077 | 0.8488 | 0.8514 | 0.8520 | 0.2810 | 0.2814 | 0.0896 | 0.0897 | 0.0004 | 0.0001 |
| d2_yes | 2077 | 0.1512 | 0.1486 | 0.1480 | 0.2810 | 0.2814 | 0.0896 | 0.0897 | 0.0004 | 0.0001 |

全量 expression score 与 edge 门附近 selected slice 是两层问题；下一版应做 date-block cross-fitted expression calibration，而不是把全局 logloss 改善直接当作可交易 EV。

## Probability Skill by Weather State

`delta_logloss < 0` 表示补入 humidity/sky 后概率更准；这里使用全部 forward state，不受交易阈值和选单数量影响。

### Worsened slices

| feature | value | rows | dates | cities | old_logloss | full_logloss | delta_logloss | delta_brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| intraday_state | plateau_near_high | 52 | 10 | 15 | 0.4550 | 0.4636 | 0.0086 | 0.0035 |
| moisture_cloud_regime | cloud_suppression | 89 | 13 | 16 | 0.7240 | 0.7318 | 0.0078 | 0.0049 |
| forecast_boundary_bin | far_above_upper | 150 | 14 | 22 | 0.3582 | 0.3659 | 0.0077 | 0.0057 |
| solar_window | evening_tail | 99 | 13 | 31 | 0.0243 | 0.0299 | 0.0056 | 0.0019 |
| day_regime | day_forecast_busted | 218 | 14 | 25 | 0.3202 | 0.3243 | 0.0042 | 0.0021 |
| sky_bin | broken_overcast | 193 | 15 | 19 | 0.6229 | 0.6254 | 0.0025 | 0.0006 |
| intraday_state | pullback_uncertain | 172 | 15 | 35 | 0.2192 | 0.2213 | 0.0021 | 0.0024 |
| humidity_bin | rh_ge80 | 319 | 14 | 22 | 0.5579 | 0.5598 | 0.0019 | 0.0007 |
| moisture_cloud_regime | humid_overcast_suppression | 92 | 11 | 10 | 0.5215 | 0.5225 | 0.0010 | -0.0011 |
| trend3h_bin | warming | 377 | 16 | 36 | 0.5942 | 0.5948 | 0.0007 | -0.0000 |
| running_max_state | pullback_from_high | 267 | 16 | 36 | 0.3789 | 0.3794 | 0.0005 | 0.0014 |
| trend3h_bin | flat | 294 | 16 | 36 | 0.3611 | 0.3612 | 0.0001 | -0.0011 |

### Improved slices

| feature | value | rows | dates | cities | old_logloss | full_logloss | delta_logloss | delta_brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forecast_boundary_bin | near_upper | 108 | 14 | 24 | 0.6745 | 0.6519 | -0.0226 | -0.0161 |
| day_regime | day_marginal_runway | 259 | 15 | 32 | 0.7033 | 0.6874 | -0.0159 | -0.0095 |
| humidity_bin | rh_lt40 | 326 | 15 | 18 | 0.6024 | 0.5892 | -0.0132 | -0.0075 |
| wind_bin | wind_gt15 | 192 | 15 | 21 | 0.4762 | 0.4651 | -0.0111 | -0.0071 |
| moisture_cloud_regime | dry_heat_inertia | 417 | 15 | 23 | 0.5752 | 0.5647 | -0.0105 | -0.0066 |
| sky_bin | scattered | 545 | 16 | 36 | 0.6193 | 0.6089 | -0.0104 | -0.0065 |
| wind_regime | windy_mixing_noise | 109 | 15 | 14 | 0.4185 | 0.4095 | -0.0090 | -0.0028 |
| forecast_source | open_meteo_live_ecmwf | 815 | 14 | 20 | 0.5911 | 0.5828 | -0.0083 | -0.0056 |
| intraday_state | active_warming | 901 | 16 | 36 | 0.7517 | 0.7440 | -0.0077 | -0.0042 |
| wind_bin | wind_le5 | 482 | 16 | 34 | 0.7268 | 0.7197 | -0.0071 | -0.0029 |
| trend3h_bin | strong_warming | 1173 | 16 | 36 | 0.7238 | 0.7171 | -0.0067 | -0.0039 |
| running_max_state | fresh_running_high | 1111 | 16 | 36 | 0.6469 | 0.6402 | -0.0067 | -0.0048 |

## Argmax Stability

| variant | rows | median_top_second_margin | p25_top_second_margin | share_margin_le_1c | share_margin_le_2c | share_edge_above_threshold_le_1c | share_edge_above_threshold_le_2c |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full_repaired | 159 | 0.0490 | 0.0296 | 0.0816 | 0.1905 | 0.3333 | 0.4906 |
| old_all_missing | 172 | 0.0467 | 0.0310 | 0.0323 | 0.1161 | 0.3140 | 0.5640 |

`top_second_margin` is the fee-adjusted edge difference between the selected expression and runner-up at the first eligible city-day state.

## City Delta: Most Negative

| city | active_dates | old_all_missing_rows | old_all_missing_roi | full_repaired_rows | full_repaired_roi | delta_pnl |
| --- | --- | --- | --- | --- | --- | --- |
| Seattle | 4 | 3 | 0.3120 | 2 | -0.3004 | -1.1429 |
| Tokyo | 3 | 3 | 0.6753 | 1 | 0.1547 | -1.0753 |
| Chengdu | 5 | 3 | 0.3581 | 5 | -0.0488 | -0.9448 |
| Wellington | 5 | 5 | 0.5556 | 3 | 0.5688 | -0.6981 |
| Ankara | 10 | 6 | 0.3987 | 9 | 0.1219 | -0.6647 |
| Warsaw | 4 | 3 | 0.7018 | 4 | 0.2685 | -0.6021 |
| Atlanta | 7 | 6 | 0.1439 | 7 | -0.0170 | -0.5723 |
| Wuhan | 4 | 4 | -0.1182 | 3 | -0.4368 | -0.5075 |
| Austin | 4 | 3 | 0.4843 | 2 | 0.3537 | -0.4563 |
| BuenosAires | 9 | 9 | 0.2468 | 8 | 0.2016 | -0.4091 |
| LA | 2 | 2 | 0.3618 | 1 | 0.1810 | -0.3781 |
| CapeTown | 9 | 8 | 0.2477 | 8 | 0.1597 | -0.3646 |

## City Delta: Most Positive

| city | active_dates | old_all_missing_rows | old_all_missing_roi | full_repaired_rows | full_repaired_roi | delta_pnl |
| --- | --- | --- | --- | --- | --- | --- |
| Munich | 4 | 2 | -0.1873 | 4 | 0.5713 | 1.3212 |
| Karachi | 9 | 8 | -0.5441 | 5 | -0.6254 | 0.7170 |
| Lucknow | 5 | 3 | -0.0674 | 5 | 0.1567 | 0.6864 |
| Dallas | 5 | 3 | -1.0000 | 4 | -0.5120 | 0.5678 |
| Houston | 7 | 6 | 0.1547 | 5 | 0.3138 | 0.5244 |
| Chongqing | 4 | 4 | -0.2597 | 3 | -0.1029 | 0.4724 |
| Miami | 5 | 5 | -1.0000 | 4 | -1.0000 | 0.3346 |
| TelAviv | 6 | 6 | -0.2317 | 3 | -0.3999 | 0.2387 |
| Jeddah | 2 | 2 | 0.2242 | 2 | 0.3631 | 0.1666 |
| NYC | 7 | 6 | -0.4818 | 7 | -0.3637 | 0.1446 |
| SaoPaulo | 2 | 2 | 0.3994 | 2 | 0.3994 | 0.0000 |
| Guangzhou | 6 | 6 | 0.7059 | 6 | 0.7059 | 0.0000 |

## City Probability Skill Delta

### Most worsened

| city | rows | dates | old_logloss | full_logloss | delta_logloss | delta_brier |
| --- | --- | --- | --- | --- | --- | --- |
| TelAviv | 41 | 8 | 0.5001 | 0.5320 | 0.0319 | 0.0184 |
| Seattle | 39 | 6 | 0.6737 | 0.6968 | 0.0231 | 0.0132 |
| Amsterdam | 77 | 14 | 0.6461 | 0.6621 | 0.0159 | 0.0128 |
| Tokyo | 57 | 9 | 0.4156 | 0.4287 | 0.0131 | 0.0098 |
| Manila | 67 | 11 | 0.6087 | 0.6181 | 0.0094 | 0.0067 |
| CapeTown | 85 | 14 | 0.4312 | 0.4398 | 0.0086 | 0.0044 |
| Atlanta | 67 | 14 | 0.6286 | 0.6368 | 0.0082 | 0.0038 |
| Lucknow | 55 | 10 | 0.7571 | 0.7618 | 0.0047 | 0.0062 |
| Taipei | 36 | 8 | 0.7827 | 0.7871 | 0.0044 | 0.0017 |
| Chongqing | 66 | 13 | 0.6254 | 0.6289 | 0.0034 | 0.0039 |

### Most improved

| city | rows | dates | old_logloss | full_logloss | delta_logloss | delta_brier |
| --- | --- | --- | --- | --- | --- | --- |
| Madrid | 39 | 11 | 0.5030 | 0.4683 | -0.0347 | -0.0298 |
| LA | 38 | 8 | 0.7223 | 0.6971 | -0.0252 | -0.0159 |
| NYC | 65 | 11 | 0.7612 | 0.7392 | -0.0220 | -0.0151 |
| Wuhan | 50 | 8 | 0.8853 | 0.8636 | -0.0217 | -0.0086 |
| SaoPaulo | 41 | 9 | 0.5334 | 0.5128 | -0.0206 | -0.0087 |
| Houston | 48 | 10 | 0.4560 | 0.4356 | -0.0204 | -0.0140 |
| Karachi | 64 | 10 | 0.5623 | 0.5447 | -0.0175 | -0.0162 |
| Miami | 59 | 11 | 0.6337 | 0.6170 | -0.0167 | -0.0077 |
| Jeddah | 49 | 11 | 0.9253 | 0.9116 | -0.0137 | -0.0032 |
| Istanbul | 84 | 11 | 0.3535 | 0.3410 | -0.0125 | -0.0126 |

## Mechanism Slices: Negative Delta

| feature | value | city_days | dates | cities | delta_pnl | avg_delta_pnl |
| --- | --- | --- | --- | --- | --- | --- |
| wind_regime | light_wind | 126 | 16 | 32 | -5.0057 | -0.0397 |
| humidity_bin | rh_60_80 | 69 | 16 | 26 | -4.6553 | -0.0675 |
| solar_window | solar_peak_window | 96 | 15 | 31 | -3.9574 | -0.0412 |
| forecast_source | open_meteo_live_gfs | 82 | 16 | 31 | -3.6102 | -0.0440 |
| trend3h_bin | strong_warming | 158 | 16 | 35 | -3.3228 | -0.0210 |
| solar_window | late_morning | 78 | 16 | 30 | -3.2588 | -0.0418 |
| day_regime | day_space_unknown | 84 | 12 | 31 | -2.8559 | -0.0340 |
| forecast_boundary_bin | unknown | 84 | 12 | 31 | -2.8559 | -0.0340 |
| peak_clock_bin | unknown | 84 | 12 | 31 | -2.8559 | -0.0340 |
| moisture_cloud_regime | mixed_moisture | 111 | 16 | 31 | -2.7087 | -0.0244 |
| wind_bin | wind_5_10 | 92 | 16 | 32 | -2.3246 | -0.0253 |
| sky_bin | few | 91 | 16 | 30 | -2.3101 | -0.0254 |
| wind_bin | wind_le5 | 49 | 15 | 23 | -2.1221 | -0.0433 |
| day_regime | day_forecast_capped | 15 | 9 | 10 | -2.0438 | -0.1363 |
| running_max_state | near_high_plateau | 51 | 8 | 24 | -1.9454 | -0.0381 |
| moisture_cloud_regime | cloud_suppression | 11 | 7 | 6 | -1.6899 | -0.1536 |
| sky_bin | broken_overcast | 15 | 9 | 9 | -1.6480 | -0.1099 |
| peak_clock_bin | gt2h_after_peak | 99 | 15 | 31 | -1.5539 | -0.0157 |

## Mechanism Slices: Positive Delta

| feature | value | city_days | dates | cities | delta_pnl | avg_delta_pnl |
| --- | --- | --- | --- | --- | --- | --- |
| solar_window | afternoon_decay_window | 25 | 12 | 14 | 2.3688 | 0.0948 |
| day_regime | day_open_runway | 53 | 14 | 29 | 1.4409 | 0.0272 |
| humidity_bin | rh_lt40 | 41 | 15 | 12 | 1.1348 | 0.0277 |
| moisture_cloud_regime | dry_heat_inertia | 50 | 15 | 15 | 1.0543 | 0.0211 |
| forecast_boundary_bin | below_upper | 79 | 15 | 34 | 1.0084 | 0.0128 |
| forecast_boundary_bin | near_upper | 14 | 10 | 12 | 0.7607 | 0.0543 |
| wind_regime | moderate_wind | 66 | 16 | 28 | 0.5318 | 0.0081 |
| wind_bin | wind_10_15 | 42 | 14 | 25 | 0.1211 | 0.0029 |
| forecast_source | unknown | 43 | 12 | 20 | 0.0620 | 0.0014 |
| sky_bin | scattered | 48 | 15 | 21 | -0.0658 | -0.0014 |
| day_regime | day_marginal_runway | 35 | 15 | 19 | -0.1601 | -0.0046 |
| intraday_state | false_fade_risk | 16 | 11 | 14 | -0.1832 | -0.0114 |

## Boundaries

- GFS/ECMWF gap 在 6/21+ canonical atlas 中完全缺失；7/4 后只保存了每城配置模型的 PIT hourly curve，不能诚实重建双模型 gap 的完整 6/21+ 分母。
- Historical full-ladder/fresh direct YES data remain unavailable before the repair; this study attributes feature parity only.
- Slices share city-days and are not independent multiple tests. They identify mechanisms for a preregistered selector experiment, not whitelist/gate approval.
- `live_real` fill coverage gate currently fails on historical over-order/cache mismatch; no live PnL is used here.

## Three Gates

- significance: FAIL for ROI delta; paired 95% CI [-10.9%, +3.3%] crosses zero.
- baseline: same frozen old-missing policy is the A/B baseline.
- forward: 2026-06-21+ expanding forward; complete-ladder post-repair forward remains pending.
- conclusion: feature parity remains mandatory; current max-edge selector is `shadow_candidate` for recalibration; every city/regime exclusion remains `inconclusive`.
