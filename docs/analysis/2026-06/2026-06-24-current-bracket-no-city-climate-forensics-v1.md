# Current-Bracket NO City/Climate Forensics V1

## 结论

这次检验的是“全错是不是集中在某些气候特征城市”。答案：有城市/气候族群信号，但它不是一个干净到可以直接删城解决的机制。

- `humid_low_latitude` 历史确实拖累最大：focus ROI 为 -1.8%，去掉后整体历史 ROI 从 +10.1% 到 +19.7%。
- 但这个 leave-out 到 forward 仍然亏：6/21..6/23 去掉 `humid_low_latitude` 后 settled ROI 仍是 -36.1%。
- 全错日不是单一城市问题：5 个 all-loss dates 横跨多个 family；`southern_or_maritime`、`europe_cloud_break` 的 all-loss enrichment 也不低。
- 因此正确动作不是删一批城市，而是把 city/climate family 当作机制特征或分层校准项；任何 city removal 都只能 shadow 观察，不能 live。

Verdict: `city_climate_signal_but_not_clean_filter`，live_ready=`False`。

## 数据层

- Generated at UTC: `2026-06-24T03:36:06+00:00`
- Source selected rows: `docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/selected_trade_rows.csv`
- Focus: `enhanced_all_rows::remaining_heat_p40_ev10`
- Historical selected trades: `280` / dates `32` / cities `35`
- All-loss dates: `['2026-05-21', '2026-05-25', '2026-05-31', '2026-06-01', '2026-06-10']`
- Forward dates: `['2026-06-21', '2026-06-22', '2026-06-23']`

## Family Summary

| city_family | trades | cities | win_rate | roi | roi_ci_low | roi_ci_high | holdout_roi | all_loss_trade_share | all_loss_enrichment | avg_curve_next_3h_delta_f | avg_relative_humidity_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| humid_low_latitude | 125 | 13 | +24.0% | -1.8% | -35.4% | +33.3% | +9.8% | +11.2% | 0.784 | -0.950 | 70.014 |
| southern_or_maritime | 86 | 10 | +23.3% | +12.8% | -30.2% | +65.9% | +26.4% | +17.4% | 1.221 | -1.517 | 63.422 |
| continental_dry_hot | 36 | 7 | +22.2% | +13.9% | -50.4% | +90.7% | +60.3% | +8.3% | 0.583 | -1.497 | 42.931 |
| europe_cloud_break | 26 | 4 | +26.9% | +14.6% | -72.5% | +109.2% | +185.7% | +23.1% | 1.615 | -0.431 | 55.268 |
| east_asia_continental | 7 | 1 | +57.1% | +154.1% | -20.6% | +316.0% | +455.6% | +28.6% | 2.000 | -0.171 | 57.119 |

## Leave-One Family

| removed_city_family | removed_trades | removed_roi | roi | roi_lift_vs_base | holdout_roi | all_loss_trade_share |
| --- | --- | --- | --- | --- | --- | --- |
| humid_low_latitude | 125 | -1.8% | +19.7% | +9.6% | +63.8% | +16.8% |
| europe_cloud_break | 26 | +14.6% | +9.7% | -0.5% | +27.9% | +13.4% |
| continental_dry_hot | 36 | +13.9% | +9.6% | -0.6% | +28.2% | +15.2% |
| southern_or_maritime | 86 | +12.8% | +9.0% | -1.2% | +35.6% | +12.9% |
| east_asia_continental | 7 | +154.1% | +6.4% | -3.7% | +28.5% | +13.9% |

## Forward By Family

| city_family | trades | settled_trades | win_rate | roi | profit_usd | avg_curve_next_3h_delta_f | avg_relative_humidity_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| east_asia_continental | 2 | 1 | +0.0% | -100.0% | $-5.00 | -2.800 | 47.305 |
| europe_cloud_break | 2 | 1 | +0.0% | -100.0% | $-5.00 | -7.300 | 45.170 |
| continental_dry_hot | 7 | 6 | +16.7% | -46.2% | $-13.87 | -2.671 | 33.170 |
| humid_low_latitude | 10 | 6 | +16.7% | -33.3% | $-10.00 | -1.250 | 88.273 |
| southern_or_maritime | 9 | 7 | +28.6% | -9.1% | $-3.18 | -2.378 | 56.599 |

## City Diagnostics

这些是诊断，不是推荐删城清单；city-level 样本很薄，且同日高度相关。

| city | city_family | trades | win_rate | roi | holdout_roi | all_loss_trades | all_loss_enrichment | avg_relative_humidity_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BuenosAires | southern_or_maritime | 5 | +0.0% | -100.0% | NA | 2 | 2.800 | 76.114 |
| Chongqing | humid_low_latitude | 4 | +0.0% | -100.0% | -100.0% | 1 | 1.750 | 92.540 |
| Chengdu | humid_low_latitude | 2 | +0.0% | -100.0% | -100.0% | 0 | 0.000 | 83.120 |
| Houston | humid_low_latitude | 2 | +0.0% | -100.0% | -100.0% | 0 | 0.000 | 87.505 |
| Dallas | continental_dry_hot | 1 | +0.0% | -100.0% | -100.0% | 0 | 0.000 | 73.830 |
| Karachi | continental_dry_hot | 12 | +8.3% | -74.7% | -100.0% | 2 | 1.167 | 48.302 |
| Warsaw | europe_cloud_break | 7 | +14.3% | -59.2% | NA | 2 | 2.000 | 54.439 |
| Jeddah | continental_dry_hot | 8 | +12.5% | -58.3% | +11.1% | 1 | 0.875 | 39.443 |
| Manila | humid_low_latitude | 12 | +16.7% | -43.2% | -47.9% | 1 | 0.583 | 65.287 |
| Shanghai | humid_low_latitude | 19 | +15.8% | -36.9% | -7.4% | 2 | 0.737 | 70.411 |
| SaoPaulo | southern_or_maritime | 11 | +18.2% | -30.2% | +44.9% | 1 | 0.636 | 69.221 |
| Tokyo | humid_low_latitude | 13 | +15.4% | -24.5% | -100.0% | 1 | 0.538 | 72.337 |
| Taipei | humid_low_latitude | 15 | +20.0% | -24.1% | -38.3% | 2 | 0.933 | 74.104 |
| Wuhan | humid_low_latitude | 11 | +18.2% | -16.7% | +25.0% | 2 | 1.273 | 76.687 |
| Singapore | humid_low_latitude | 14 | +28.6% | -6.7% | +0.0% | 2 | 1.000 | 73.874 |
| TelAviv | southern_or_maritime | 15 | +20.0% | -1.5% | -100.0% | 3 | 1.400 | 50.043 |
| Munich | europe_cloud_break | 5 | +20.0% | +0.0% | +150.0% | 2 | 2.800 | 54.904 |
| Busan | humid_low_latitude | 13 | +23.1% | +2.3% | +62.1% | 2 | 1.077 | 49.043 |
| LA | southern_or_maritime | 15 | +26.7% | +6.4% | -100.0% | 3 | 1.400 | 64.136 |
| Lucknow | continental_dry_hot | 9 | +22.2% | +16.8% | -23.1% | 0 | 0.000 | 32.931 |
| Istanbul | southern_or_maritime | 7 | +28.6% | +17.7% | +64.8% | 1 | 1.000 | 56.783 |
| NYC | southern_or_maritime | 3 | +33.3% | +19.0% | NA | 0 | 0.000 | 52.023 |
| CapeTown | southern_or_maritime | 7 | +28.6% | +31.3% | -100.0% | 1 | 1.000 | 67.771 |
| SanFrancisco | southern_or_maritime | 4 | +25.0% | +38.9% | +177.8% | 1 | 1.750 | 56.713 |
| Wellington | southern_or_maritime | 15 | +20.0% | +50.3% | +47.1% | 3 | 1.400 | 73.374 |

## Leave-One City Top Diagnostics

| removed_city | city_family | removed_trades | removed_roi | roi | roi_lift_vs_base | holdout_roi |
| --- | --- | --- | --- | --- | --- | --- |
| Karachi | continental_dry_hot | 12 | -74.7% | +13.9% | +3.8% | +38.5% |
| Shanghai | humid_low_latitude | 19 | -36.9% | +13.6% | +3.4% | +36.7% |
| Manila | humid_low_latitude | 12 | -43.2% | +12.5% | +2.4% | +39.8% |
| Jeddah | continental_dry_hot | 8 | -58.3% | +12.1% | +2.0% | +34.4% |
| BuenosAires | southern_or_maritime | 5 | -100.0% | +12.1% | +2.0% | +33.6% |
| Taipei | humid_low_latitude | 15 | -24.1% | +12.1% | +1.9% | +39.1% |
| Warsaw | europe_cloud_break | 7 | -59.2% | +11.9% | +1.8% | +33.6% |
| Tokyo | humid_low_latitude | 13 | -24.5% | +11.8% | +1.7% | +38.5% |
| SaoPaulo | southern_or_maritime | 11 | -30.2% | +11.8% | +1.6% | +33.1% |
| Chongqing | humid_low_latitude | 4 | -100.0% | +11.7% | +1.6% | +36.8% |
| Wuhan | humid_low_latitude | 11 | -16.7% | +11.2% | +1.1% | +34.0% |
| Singapore | humid_low_latitude | 14 | -6.7% | +11.0% | +0.9% | +35.2% |
| Chengdu | humid_low_latitude | 2 | -100.0% | +10.9% | +0.8% | +35.2% |
| Houston | humid_low_latitude | 2 | -100.0% | +10.9% | +0.8% | +35.2% |
| TelAviv | southern_or_maritime | 15 | -1.5% | +10.8% | +0.7% | +38.5% |
| Dallas | continental_dry_hot | 1 | -100.0% | +10.5% | +0.4% | +35.2% |
| Busan | humid_low_latitude | 13 | +2.3% | +10.5% | +0.4% | +31.4% |
| LA | southern_or_maritime | 15 | +6.4% | +10.3% | +0.2% | +35.2% |
| Munich | europe_cloud_break | 5 | +0.0% | +10.3% | +0.2% | +30.7% |
| NYC | southern_or_maritime | 3 | +19.0% | +10.0% | -0.1% | +33.6% |
| Istanbul | southern_or_maritime | 7 | +17.7% | +9.9% | -0.2% | +31.6% |
| Lucknow | continental_dry_hot | 9 | +16.8% | +9.9% | -0.2% | +37.2% |
| SanFrancisco | southern_or_maritime | 4 | +38.9% | +9.7% | -0.4% | +30.0% |
| CapeTown | southern_or_maritime | 7 | +31.3% | +9.6% | -0.5% | +35.2% |
| Denver | continental_dry_hot | 1 | +257.1% | +9.2% | -0.9% | +33.6% |

## 机制判断

1. `humid_low_latitude` 更像“预报高估/湿热低纬封顶风险”的候选机制特征，不是独立可交易规则。
2. 全错日跨 family 发生，说明 day-regime/macro weather pattern 比单城黑名单更重要。
3. 下一步应该把 family 与 forecast overestimate、curve plateau、humidity/cloud/wind regime 做交互特征，并做 forward/shadow；不要用 leave-one-city 直接决定 live 城市池。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_city_climate_forensics_v1/summary.json`
- City summary: `docs/analysis/2026-06/generated/current_bracket_no_city_climate_forensics_v1/city_summary.csv`
- Family summary: `docs/analysis/2026-06/generated/current_bracket_no_city_climate_forensics_v1/family_summary.csv`
- Leave-one city: `docs/analysis/2026-06/generated/current_bracket_no_city_climate_forensics_v1/leave_one_city_summary.csv`
- Leave-one family: `docs/analysis/2026-06/generated/current_bracket_no_city_climate_forensics_v1/leave_one_family_summary.csv`
- Forward family: `docs/analysis/2026-06/generated/current_bracket_no_city_climate_forensics_v1/forward_family_summary.csv`
