# Current-Bracket NO Forward Regime Stress V1

## 结论

6/21-6/22 崩盘不是单纯因为没有把这两天放进训练；滚动 2 日 block CV 显示，类似的坏 block 在历史里也存在。区别是 6/21-6/22 的 regime 更集中：cap rate 明显更高，p40 选中票的 actual margin 偏负，且出现 `p_cross` 极高但实际不穿 upper 的 forecast-overconfidence。

把 6/21-6/22 已结算数据放进训练后，换其它历史日期当 pseudo-forward，仍会出现 ROI <= -50% 的坏 block。这说明这是典型的 regime / non-stationarity 问题，不是简单换一个训练集就能治好。

Verdict: `forward_failure_is_regime_nonstationarity_not_single_split_bug`，live_ready=`False`。

## Data

- Generated at UTC: `2026-06-24T09:33:13+00:00`
- Sync/rebuild: `sync_weather_remote.sh completed; run_stack rebuilt fact tables and CLOB gate, then exited non-clean because FE port 5174 stayed busy.`
- Settled forward dates: `2026-06-21,2026-06-22`
- Open/unsettled date excluded from label training: `2026-06-23` rows=146
- Combined settled dates: `2026-05-20`..`2026-06-22`

## Why 6/21-6/22 Broke

| target_date | period | trades | win_rate | roi | profit_usd | avg_p_cross | avg_p_cap | avg_pred_error_f | avg_actual_margin_f | overconf_trades | cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-21 | train | 5 | +0.0% | -100.0% | $-25.00 | 0.554 | 0.722 | 0.647 | -0.510 | 0 | Beijing,LA,SanFrancisco,SaoPaulo,Shanghai |
| 2026-05-25 | train | 9 | +0.0% | -100.0% | $-45.00 | 0.611 | 0.729 | 0.646 | -0.361 | 0 | Beijing,Jeddah,Karachi,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| 2026-05-26 | train | 10 | +30.0% | -2.2% | $-1.11 | 0.639 | 0.653 | 0.157 | 0.385 | 2 | Helsinki,Karachi,Lucknow,SanFrancisco,SaoPaulo,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| 2026-05-31 | train | 6 | +0.0% | -100.0% | $-30.00 | 0.649 | 0.546 | 1.021 | -0.633 | 0 | Amsterdam,Atlanta,Busan,LA,Munich,Wellington |
| 2026-06-01 | train | 12 | +0.0% | -100.0% | $-60.00 | 0.595 | 0.728 | 0.600 | -0.354 | 0 | BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Singapore,TelAviv,Warsaw,Wellington,Wuhan |
| 2026-06-10 | train | 8 | +0.0% | -100.0% | $-40.00 | 0.593 | 0.654 | 0.684 | -0.450 | 0 | Amsterdam,BuenosAires,Karachi,Munich,Taipei,TelAviv,Warsaw,Wellington |
| 2026-06-21 | settled_forward | 12 | +25.0% | -14.5% | $-8.72 | 0.744 | 0.502 | 1.080 | 0.025 | 2 | BuenosAires,CapeTown,Chongqing,Denver,Jeddah,Karachi,Lucknow,NYC,Shanghai,Tokyo,Warsaw,Wuhan |
| 2026-06-22 | settled_forward | 9 | +11.1% | -63.0% | $-28.33 | 0.663 | 0.540 | 1.264 | -0.311 | 2 | Ankara,Beijing,Chongqing,Denver,NYC,Seattle,Shanghai,TelAviv,Wellington |

## Distribution Compare

| grain | metric | hist_mean | hist_p10 | hist_p50 | hist_p90 | settled_forward_mean | forward_percentile_vs_hist |
| --- | --- | --- | --- | --- | --- | --- | --- |
| daily_base_p40 | roi | 0.048 | -1.000 | -0.035 | 0.965 | -0.387 | 0.219 |
| daily_base_p40 | win_rate | 0.233 | 0.000 | 0.250 | 0.440 | 0.181 | 0.281 |
| daily_base_p40 | avg_pred_error_f | 0.390 | 0.078 | 0.374 | 0.737 | 1.172 | 1.000 |
| daily_base_p40 | avg_actual_margin_f | -0.006 | -0.360 | -0.029 | 0.376 | -0.143 | 0.281 |
| daily_base_p40 | overconf_trades | 0.250 | 0.000 | 0.000 | 1.000 | 2.000 | 1.000 |
| rolling_2day_base_p40 | roi | -0.070 | -0.539 | -0.047 | 0.360 | -0.477 | 0.182 |
| rolling_2day_base_p40 | win_rate | 0.209 | 0.118 | 0.217 | 0.299 | 0.154 | 0.303 |
| rolling_2day_base_p40 | avg_pred_error_f | 0.564 | 0.274 | 0.563 | 0.801 | 1.352 | 1.000 |
| rolling_2day_base_p40 | avg_actual_margin_f | -0.068 | -0.277 | -0.072 | 0.204 | -0.233 | 0.182 |
| rolling_2day_base_p40 | overconf_trades | 0.758 | 0.000 | 1.000 | 2.000 | 3.000 | 1.000 |
| rolling_2day_base_p40 | holdout_cap_rate | 0.383 | 0.311 | 0.366 | 0.416 | 0.779 | 1.000 |

## Rolling 2-Day Block CV: Target Block

| variant | holdout_dates | trades | win_rate | roi | profit_usd | avg_p_cross | avg_p_cap | avg_pred_error_f | avg_actual_margin_f | overconf_trades | holdout_cap_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base_p40_ev10 | 2026-06-21,2026-06-22 | 26 | +15.4% | -47.7% | $-62.05 | 0.724 | 0.539 | 1.352 | -0.233 | 3 | +77.9% |
| cap_adjusted_p40_ev10 | 2026-06-21,2026-06-22 | 12 | +16.7% | -47.9% | $-28.72 | 0.910 | 0.339 | 2.321 | -0.133 | 3 | +77.9% |
| cap_veto_train_top25_risk | 2026-06-21,2026-06-22 | 20 | +20.0% | -32.1% | $-32.05 | 0.763 | 0.445 | 1.550 | -0.188 | 3 | +77.9% |

## Worst Rolling Blocks

| holdout_dates | trades | win_rate | roi | profit_usd | avg_p_cross | avg_p_cap | avg_pred_error_f | avg_actual_margin_f | overconf_trades | holdout_cap_rate | cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31,2026-06-01 | 19 | +0.0% | -100.0% | $-95.00 | 0.680 | 0.539 | 1.043 | -0.447 | 1 | +38.2% | Amsterdam,Atlanta,BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Munich,Singapore,Taipei,TelAviv,Warsaw,Wellington,Wuhan |
| 2026-05-30,2026-05-31 | 17 | +5.9% | -69.0% | $-58.68 | 0.658 | 0.556 | 0.985 | -0.403 | 2 | +31.3% | Amsterdam,Atlanta,Busan,CapeTown,Guangzhou,LA,Miami,Munich,Shanghai,Taipei,Tokyo,Warsaw,Wellington |
| 2026-06-01,2026-06-02 | 24 | +12.5% | -58.6% | $-70.32 | 0.661 | 0.593 | 0.736 | -0.260 | 0 | +38.6% | Amsterdam,Atlanta,BuenosAires,Busan,CapeTown,Chongqing,Helsinki,Houston,Istanbul,Karachi,LA,Manila,Miami,SaoPaulo,Shanghai,Singapore,Taipei,TelAviv,Warsaw,Wellington,Wuhan |
| 2026-05-24,2026-05-25 | 21 | +9.5% | -55.2% | $-57.99 | 0.583 | 0.702 | 0.513 | -0.281 | 0 | +36.6% | Ankara,Beijing,Busan,Guangzhou,Jeddah,Karachi,LA,Lucknow,Manila,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| 2026-05-25,2026-05-26 | 19 | +15.8% | -48.5% | $-46.11 | 0.633 | 0.602 | 0.439 | 0.013 | 2 | +38.0% | Beijing,Busan,Helsinki,Jeddah,Karachi,Lucknow,SaoPaulo,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| 2026-06-21,2026-06-22 | 26 | +15.4% | -47.7% | $-62.05 | 0.724 | 0.539 | 1.352 | -0.233 | 3 | +77.9% | Ankara,Austin,Beijing,BuenosAires,Busan,CapeTown,Chongqing,Denver,Jeddah,Karachi,Lucknow,Madrid,Miami,NYC,Seattle,Shanghai,Taipei,TelAviv,Tokyo,Warsaw,Wellington,Wuhan |
| 2026-05-21,2026-05-22 | 15 | +13.3% | -42.5% | $-31.88 | 0.660 | 0.586 | 0.609 | -0.087 | 1 | +36.6% | Ankara,Beijing,BuenosAires,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai,Singapore,Tokyo,Wuhan |
| 2026-06-04,2026-06-05 | 17 | +11.8% | -41.2% | $-35.00 | 0.640 | 0.605 | 0.611 | -0.185 | 0 | +37.1% | Ankara,Beijing,Busan,Helsinki,Jeddah,LA,Manila,SaoPaulo,Shanghai,Singapore,Tokyo,Warsaw,Wellington |
| 2026-06-19,2026-06-20 | 13 | +15.4% | -39.6% | $-25.77 | 0.599 | 0.713 | 0.402 | -0.127 | 0 | +30.9% | Beijing,CapeTown,Istanbul,Jeddah,Karachi,Lucknow,Manila,Singapore,TelAviv,Tokyo,Wellington |
| 2026-05-29,2026-05-30 | 18 | +16.7% | -35.9% | $-32.31 | 0.609 | 0.662 | 0.447 | -0.072 | 1 | +33.8% | Busan,CapeTown,Guangzhou,LA,Miami,Seattle,Shanghai,Taipei,TelAviv,Tokyo,Warsaw,Wellington |
| 2026-06-03,2026-06-04 | 15 | +13.3% | -33.3% | $-25.00 | 0.678 | 0.551 | 0.687 | -0.040 | 2 | +37.5% | Beijing,Helsinki,Jeddah,Karachi,LA,Manila,SaoPaulo,Shanghai,TelAviv,Tokyo,Wellington,Wuhan |
| 2026-06-02,2026-06-03 | 19 | +21.1% | -30.2% | $-28.66 | 0.692 | 0.549 | 0.756 | -0.113 | 2 | +39.4% | Amsterdam,Atlanta,Busan,Helsinki,Houston,Karachi,LA,Miami,SaoPaulo,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wellington,Wuhan |

## Other Bad Blocks After Training Includes 6/21-6/22

| holdout_dates | trades | win_rate | roi | profit_usd | avg_p_cross | avg_p_cap | avg_pred_error_f | avg_actual_margin_f | overconf_trades | cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31,2026-06-01 | 19 | +0.0% | -100.0% | $-95.00 | 0.680 | 0.539 | 1.043 | -0.447 | 1 | Amsterdam,Atlanta,BuenosAires,Busan,CapeTown,Chongqing,Istanbul,LA,Manila,Munich,Singapore,Taipei,TelAviv,Warsaw,Wellington,Wuhan |
| 2026-05-30,2026-05-31 | 17 | +5.9% | -69.0% | $-58.68 | 0.658 | 0.556 | 0.985 | -0.403 | 2 | Amsterdam,Atlanta,Busan,CapeTown,Guangzhou,LA,Miami,Munich,Shanghai,Taipei,Tokyo,Warsaw,Wellington |
| 2026-06-01,2026-06-02 | 24 | +12.5% | -58.6% | $-70.32 | 0.661 | 0.593 | 0.736 | -0.260 | 0 | Amsterdam,Atlanta,BuenosAires,Busan,CapeTown,Chongqing,Helsinki,Houston,Istanbul,Karachi,LA,Manila,Miami,SaoPaulo,Shanghai,Singapore,Taipei,TelAviv,Warsaw,Wellington,Wuhan |
| 2026-05-24,2026-05-25 | 21 | +9.5% | -55.2% | $-57.99 | 0.583 | 0.702 | 0.513 | -0.281 | 0 | Ankara,Beijing,Busan,Guangzhou,Jeddah,Karachi,LA,Lucknow,Manila,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |

## 6/23 Open Shadow

| target_date | city | no_ask | p_cross_upper | p_cap | p_cross_cap_adjusted | mechanism_edge | cap_adjusted_edge |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-23 | Chengdu | 0.950 | 1.000 | 0.016 | 0.984 | 0.050 | 0.034 |
| 2026-06-23 | Chengdu | 0.990 | 1.000 | 0.033 | 0.967 | 0.010 | -0.023 |
| 2026-06-23 | Chengdu | 0.950 | 1.000 | 0.037 | 0.963 | 0.050 | 0.013 |
| 2026-06-23 | Warsaw | 0.997 | 1.000 | 0.018 | 0.982 | 0.003 | -0.015 |
| 2026-06-23 | Jeddah | 0.977 | 1.000 | 0.022 | 0.978 | 0.023 | 0.001 |
| 2026-06-23 | Chongqing | 0.530 | 1.000 | 0.039 | 0.961 | 0.470 | 0.431 |
| 2026-06-23 | Helsinki | 0.999 | 1.000 | 0.013 | 0.987 | 0.001 | -0.012 |
| 2026-06-23 | Wuhan | 0.620 | 1.000 | 0.031 | 0.968 | 0.380 | 0.348 |
| 2026-06-23 | Jeddah | 0.661 | 1.000 | 0.141 | 0.859 | 0.339 | 0.198 |
| 2026-06-23 | Chongqing | 0.260 | 1.000 | 0.113 | 0.887 | 0.740 | 0.627 |
| 2026-06-23 | Singapore | 0.999 | 1.000 | 0.017 | 0.983 | 0.001 | -0.016 |
| 2026-06-23 | Chengdu | 0.800 | 1.000 | 0.049 | 0.950 | 0.200 | 0.150 |
| 2026-06-23 | NYC | 0.210 | 0.999 | 0.021 | 0.978 | 0.789 | 0.768 |
| 2026-06-23 | Helsinki | 0.999 | 0.999 | 0.025 | 0.975 | 0.000 | -0.024 |
| 2026-06-23 | Wuhan | 0.740 | 0.998 | 0.043 | 0.955 | 0.258 | 0.215 |
| 2026-06-23 | Jeddah | 0.310 | 0.998 | 0.172 | 0.826 | 0.688 | 0.516 |
| 2026-06-23 | Chongqing | 0.029 | 0.997 | 0.446 | 0.552 | 0.968 | 0.523 |
| 2026-06-23 | Warsaw | 0.840 | 0.996 | 0.052 | 0.945 | 0.156 | 0.105 |
| 2026-06-23 | Beijing | 0.766 | 0.996 | 0.105 | 0.891 | 0.230 | 0.125 |
| 2026-06-23 | Wuhan | 0.400 | 0.995 | 0.072 | 0.924 | 0.595 | 0.524 |

## Quant Read

1. 这是 blocked walk-forward / regime validation 问题：随机拆样本会高估稳定性，因为同一天多城市高度相关。
2. 6/21-6/22 不是唯一坏 block；历史也有 5/25、5/31、6/01、6/10 这类同步亏损日。策略收益来自好 regime 覆盖坏 regime，而不是每个 regime 都稳。
3. 把坏 block 放进训练可以让模型见过这种形态，但如果特征没有表达 forecast-overconfidence 的根因，它仍会在其它坏 block 上复发。
4. 下一步不是继续换 split，而是建 regime-aware policy：当 day-regime 不可判别时降低交易频率或只 shadow。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_forward_regime_stress_v1/summary.json`
- Daily regime: `docs/analysis/2026-06/generated/current_bracket_no_forward_regime_stress_v1/live_like_daily_regime.csv`
- Rolling blocks: `docs/analysis/2026-06/generated/current_bracket_no_forward_regime_stress_v1/rolling_2day_block_cv.csv`
- Distribution compare: `docs/analysis/2026-06/generated/current_bracket_no_forward_regime_stress_v1/regime_distribution_compare.csv`
- 6/23 open shadow: `docs/analysis/2026-06/generated/current_bracket_no_forward_regime_stress_v1/open_2026_06_23_shadow_rows.csv`
