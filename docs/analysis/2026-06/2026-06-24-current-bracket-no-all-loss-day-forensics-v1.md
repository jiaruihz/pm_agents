# Current-Bracket NO All-Loss Day Forensics V1

## 结论

全错日的核心问题不是“有没有午后高温”，而是 payoff 判断错：模型认为当天还有足够空间打穿当前 bracket upper，但最终最高温卡在 upper 下方或附近。也就是说，方向判断经常对了一半，真正错在 `final max > current upper + margin`。

source policy 是放大器，不是根因。历史 forced-GFS 能改善交易筛选，但 6/21..6/23 forward 仍失败，说明还缺 day-regime gate：识别“预报仍偏热、盘中仍像升温，但全市场/多城市同时封顶”的日期。

Verdict: `inconclusive_shadow_only`，不改 live。

## 数据层

- Generated at UTC: `2026-06-24T02:43:13+00:00`
- Sync/rebuild: `sync_weather_remote.sh completed; run_stack rebuilt facts and CLOB gate passed, then exited non-clean only because FE port 5174 stayed busy.`
- Historical rows: `4306`
- Common source rows: `499`
- Date range: `2026-05-20`..`2026-06-20`
- Split date: `2026-06-10`

## All-Loss Dates

| policy_variant | sample | target_date | trades | settled_trades | roi | avg_p_no_win | avg_no_ask | avg_actual_margin_f | avg_forecast_gap_f | avg_forecast_overestimate_f | peak_after_decision_2h_rate | ecmwf_route_share | cause_tags | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-05-21 | 1 | 1 | -100.0% | 0.90 | 0.16 | -1.00 | 0.20 | 1.20 | +0.0% | +0.0% | forecast_break_overestimated,forecast_high_too_hot,trend_positive_but_exhausted | LA |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-05-25 | 2 | 2 | -100.0% | 0.68 | 0.16 | -0.20 | 3.30 | 3.50 | +50.0% | +0.0% | forecast_break_overestimated,peak_timing_not_enough,forecast_high_too_hot,trend_positive_but_exhausted | Shanghai,Tokyo |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-05-31 | 2 | 2 | -100.0% | 0.83 | 0.19 | -0.20 | 0.80 | 1.00 | +0.0% | +0.0% | forecast_break_overestimated,forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | Amsterdam,LA |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-06-01 | 2 | 2 | -100.0% | 0.76 | 0.21 | 0.00 | 3.25 | 3.25 | +0.0% | +0.0% | forecast_break_overestimated,forecast_high_too_hot,trend_positive_but_exhausted | LA,NYC |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-06-04 | 1 | 1 | -100.0% | 0.92 | 0.18 | 0.00 | 3.40 | 3.40 | +100.0% | +0.0% | forecast_break_overestimated,peak_timing_not_enough,forecast_high_too_hot,trend_positive_but_exhausted | LA |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-06-08 | 2 | 2 | -100.0% | 0.75 | 0.16 | -0.20 | -0.85 | -0.65 | +50.0% | +0.0% | peak_timing_not_enough,trend_positive_but_exhausted | Seattle,Tokyo |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-06-10 | 1 | 1 | -100.0% | 0.59 | 0.17 | -0.40 | 1.30 | 1.70 | +100.0% | +0.0% | forecast_break_overestimated,peak_timing_not_enough,forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | Munich |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-06-11 | 2 | 2 | -100.0% | 0.75 | 0.16 | -0.40 | 2.75 | 3.15 | +0.0% | +0.0% | forecast_break_overestimated,forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | LA,Wuhan |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-06-15 | 1 | 1 | -100.0% | 0.57 | 0.19 | -1.00 | 6.30 | 7.30 | +0.0% | +0.0% | forecast_break_overestimated,forecast_high_too_hot | Houston |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-06-17 | 2 | 2 | -100.0% | 0.48 | 0.13 | -0.60 | -1.30 | -0.70 | +50.0% | +0.0% | peak_timing_not_enough,trend_positive_but_exhausted | LA,Shanghai |
| forced_gfs::payoff_ev05_p35_max2_day | historical | 2026-06-19 | 2 | 2 | -100.0% | 0.54 | 0.21 | 0.10 | 0.45 | 0.35 | +0.0% | +0.0% | non_gfs_city_cluster,trend_positive_but_exhausted | CapeTown,Manila |
| forced_gfs::payoff_ev05_p35_max2_day | forward | 2026-06-21 | 2 | 2 | -100.0% | 0.81 | 0.25 | 0.10 | 7.29 | 7.19 | NA | +0.0% | forecast_high_too_hot,trend_positive_but_exhausted | Denver,Tokyo |
| forced_gfs::payoff_ev05_p35_max2_day | forward | 2026-06-22 | 2 | 2 | -100.0% | 0.77 | 0.14 | 0.00 | 4.85 | 4.85 | NA | +0.0% | forecast_break_overestimated,forecast_high_too_hot,trend_positive_but_exhausted | Denver,NYC |
| forced_gfs::payoff_ev10_p40 | historical | 2026-05-21 | 5 | 5 | -100.0% | 0.56 | 0.16 | -0.20 | 2.80 | 3.00 | +40.0% | +0.0% | forecast_break_overestimated,forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | Beijing,Helsinki,LA,SanFrancisco,Shanghai |
| forced_gfs::payoff_ev10_p40 | historical | 2026-05-25 | 6 | 6 | -100.0% | 0.57 | 0.17 | -0.30 | -0.27 | 0.03 | +50.0% | +0.0% | peak_timing_not_enough,trend_positive_but_exhausted | Busan,LA,Shanghai,TelAviv,Tokyo,Wuhan |
| forced_gfs::payoff_ev10_p40 | historical | 2026-05-31 | 6 | 6 | -100.0% | 0.63 | 0.23 | -0.20 | 0.58 | 0.78 | +33.3% | +0.0% | forecast_break_overestimated,trend_positive_but_exhausted | Amsterdam,Atlanta,Busan,LA,TelAviv,Wellington |
| forced_gfs::payoff_ev10_p40 | historical | 2026-06-10 | 5 | 5 | -100.0% | 0.53 | 0.23 | -0.20 | 0.32 | 0.52 | +60.0% | +0.0% | forecast_break_overestimated,peak_timing_not_enough,trend_positive_but_exhausted | BuenosAires,Munich,Shanghai,TelAviv,Wellington |
| forced_gfs::payoff_ev10_p40 | historical | 2026-06-19 | 5 | 5 | -100.0% | 0.50 | 0.27 | -0.24 | -0.10 | 0.14 | +40.0% | +0.0% | trend_positive_but_exhausted | CapeTown,Houston,Manila,TelAviv,Wellington |
| preferred_route::payoff_ev05_p35 | historical | 2026-05-21 | 6 | 6 | -100.0% | 0.53 | 0.16 | -0.10 | 1.53 | 1.63 | +50.0% | +50.0% | forecast_break_overestimated,peak_timing_not_enough,forecast_high_too_hot,ecmwf_route_cluster,non_gfs_city_cluster,trend_positive_but_exhausted | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| preferred_route::payoff_ev05_p35 | historical | 2026-05-25 | 8 | 8 | -100.0% | 0.49 | 0.17 | -0.25 | 0.76 | 1.01 | +37.5% | +25.0% | forecast_break_overestimated,forecast_high_too_hot,trend_positive_but_exhausted | Busan,Helsinki,LA,Shanghai,Taipei,TelAviv,Tokyo,Wuhan |
| preferred_route::payoff_ev05_p35 | historical | 2026-05-31 | 5 | 5 | -100.0% | 0.59 | 0.23 | -0.16 | 0.24 | 0.40 | +20.0% | +20.0% | forecast_break_overestimated,trend_positive_but_exhausted | Amsterdam,Busan,LA,Taipei,TelAviv |
| preferred_route::payoff_ev05_p35 | historical | 2026-06-10 | 7 | 7 | -100.0% | 0.47 | 0.19 | -0.06 | -0.36 | -0.30 | +42.9% | +28.6% | trend_positive_but_exhausted | Amsterdam,BuenosAires,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| preferred_route::payoff_ev05_p35 | historical | 2026-06-19 | 6 | 6 | -100.0% | 0.46 | 0.25 | -0.27 | 0.28 | 0.55 | +33.3% | +33.3% | forecast_break_overestimated,trend_positive_but_exhausted | Busan,CapeTown,Houston,Manila,TelAviv,Wellington |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-05-21 | 2 | 2 | -100.0% | 0.73 | 0.13 | -0.70 | -0.10 | 0.60 | +50.0% | +0.0% | peak_timing_not_enough,non_gfs_city_cluster,trend_positive_but_exhausted | Helsinki,LA |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-05-24 | 2 | 2 | -100.0% | 0.67 | 0.16 | 0.00 | 2.15 | 2.15 | +50.0% | +0.0% | forecast_break_overestimated,peak_timing_not_enough,forecast_high_too_hot,non_gfs_city_cluster | Ankara,Tokyo |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-05-25 | 2 | 2 | -100.0% | 0.66 | 0.16 | -0.20 | 0.80 | 1.00 | +50.0% | +0.0% | forecast_break_overestimated,peak_timing_not_enough,forecast_high_too_hot,trend_positive_but_exhausted | Shanghai,Tokyo |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-05-31 | 2 | 2 | -100.0% | 0.79 | 0.19 | -0.20 | 0.25 | 0.45 | +0.0% | +0.0% | forecast_break_overestimated,non_gfs_city_cluster,trend_positive_but_exhausted | Amsterdam,LA |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-06-01 | 2 | 2 | -100.0% | 0.72 | 0.21 | 0.00 | 0.75 | 0.75 | +0.0% | +0.0% | forecast_break_overestimated,trend_positive_but_exhausted | LA,NYC |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-06-02 | 2 | 2 | -100.0% | 0.72 | 0.25 | -0.60 | 0.90 | 1.50 | +50.0% | +0.0% | forecast_break_overestimated,peak_timing_not_enough,forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | Amsterdam,Atlanta |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-06-04 | 1 | 1 | -100.0% | 0.91 | 0.18 | 0.00 | -0.50 | -0.50 | +100.0% | +0.0% | peak_timing_not_enough,trend_positive_but_exhausted | LA |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-06-08 | 2 | 2 | -100.0% | 0.69 | 0.16 | -0.20 | -0.65 | -0.45 | +50.0% | +0.0% | peak_timing_not_enough,trend_positive_but_exhausted | Seattle,Tokyo |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-06-10 | 2 | 2 | -100.0% | 0.52 | 0.14 | -0.20 | -2.60 | -2.40 | +0.0% | +0.0% | trend_positive_but_exhausted | Shanghai,Taipei |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-06-11 | 2 | 2 | -100.0% | 0.71 | 0.16 | -0.40 | 0.85 | 1.25 | +0.0% | +50.0% | forecast_break_overestimated,forecast_high_too_hot,ecmwf_route_cluster,non_gfs_city_cluster,trend_positive_but_exhausted | LA,Wuhan |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-06-17 | 2 | 2 | -100.0% | 0.51 | 0.10 | -0.50 | 4.70 | 5.20 | +0.0% | +50.0% | forecast_break_overestimated,forecast_high_too_hot,ecmwf_route_cluster,non_gfs_city_cluster,trend_positive_but_exhausted | Chengdu,LA |
| preferred_route::payoff_ev05_p35_max2_day | historical | 2026-06-19 | 2 | 2 | -100.0% | 0.52 | 0.21 | 0.10 | 0.20 | 0.10 | +0.0% | +50.0% | ecmwf_route_cluster,non_gfs_city_cluster,trend_positive_but_exhausted | CapeTown,Manila |
| preferred_route::payoff_ev05_p35_max2_day | forward | 2026-06-21 | 2 | 2 | -100.0% | 0.77 | 0.16 | 0.30 | 7.47 | 7.17 | NA | +0.0% | forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | CapeTown,Tokyo |
| preferred_route::payoff_ev05_p35_max2_day | forward | 2026-06-22 | 2 | 2 | -100.0% | 0.83 | 0.14 | 0.00 | 4.85 | 4.85 | NA | +0.0% | forecast_break_overestimated,forecast_high_too_hot,trend_positive_but_exhausted | Denver,NYC |
| preferred_route::payoff_ev10_p40 | historical | 2026-05-21 | 5 | 5 | -100.0% | 0.56 | 0.16 | -0.12 | 2.32 | 2.44 | +60.0% | +60.0% | forecast_break_overestimated,peak_timing_not_enough,forecast_high_too_hot,ecmwf_route_cluster,non_gfs_city_cluster,trend_positive_but_exhausted | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo |
| preferred_route::payoff_ev10_p40 | historical | 2026-05-25 | 7 | 7 | -100.0% | 0.51 | 0.17 | -0.29 | 0.11 | 0.40 | +28.6% | +14.3% | forecast_break_overestimated,trend_positive_but_exhausted | Busan,Helsinki,LA,Shanghai,Taipei,TelAviv,Tokyo |
| preferred_route::payoff_ev10_p40 | historical | 2026-05-31 | 5 | 5 | -100.0% | 0.59 | 0.23 | -0.16 | 0.24 | 0.40 | +20.0% | +20.0% | forecast_break_overestimated,trend_positive_but_exhausted | Amsterdam,Busan,LA,Taipei,TelAviv |
| preferred_route::payoff_ev10_p40 | historical | 2026-06-10 | 5 | 5 | -100.0% | 0.50 | 0.18 | -0.12 | -0.22 | -0.10 | +20.0% | +40.0% | trend_positive_but_exhausted | BuenosAires,Shanghai,Taipei,TelAviv,Warsaw |
| preferred_route::payoff_ev10_p40 | historical | 2026-06-19 | 4 | 4 | -100.0% | 0.50 | 0.27 | -0.05 | 0.07 | 0.12 | +50.0% | +25.0% | forecast_break_overestimated,peak_timing_not_enough,trend_positive_but_exhausted | CapeTown,Manila,TelAviv,Wellington |

## All-Loss vs Other Dates

| policy_variant | all_loss_dates | other_dates | all_loss_avg_actual_margin_f | other_avg_actual_margin_f | all_loss_avg_forecast_gap_f | other_avg_forecast_gap_f | all_loss_avg_forecast_overestimate_f | other_avg_forecast_overestimate_f | all_loss_peak_after_decision_2h_rate | other_peak_after_decision_2h_rate | all_loss_ecmwf_route_share | other_ecmwf_route_share | all_loss_date_list |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forced_gfs::payoff_ev05_p35_max2_day | 11 | 21 | -0.35 | 1.00 | 1.78 | 1.92 | 2.14 | 0.91 | +31.8% | +52.4% | 0.00 | 0.00 | 2026-05-21,2026-05-25,2026-05-31,2026-06-01,2026-06-04,2026-06-08,2026-06-10,2026-06-11,2026-06-15,2026-06-17,2026-06-19 |
| forced_gfs::payoff_ev10_p40 | 5 | 27 | -0.23 | 0.65 | 0.67 | 0.56 | 0.90 | -0.09 | +44.7% | +48.9% | 0.00 | 0.00 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10,2026-06-19 |
| preferred_route::payoff_ev05_p35 | 5 | 27 | -0.17 | 0.54 | 0.49 | 0.41 | 0.66 | -0.13 | +36.7% | +48.8% | 0.31 | 0.26 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10,2026-06-19 |
| preferred_route::payoff_ev05_p35_max2_day | 12 | 20 | -0.24 | 1.15 | 0.56 | 0.84 | 0.80 | -0.31 | +29.2% | +55.0% | 0.12 | 0.12 | 2026-05-21,2026-05-24,2026-05-25,2026-05-31,2026-06-01,2026-06-02,2026-06-04,2026-06-08,2026-06-10,2026-06-11,2026-06-17,2026-06-19 |
| preferred_route::payoff_ev10_p40 | 5 | 27 | -0.15 | 0.54 | 0.51 | 0.49 | 0.65 | -0.05 | +35.7% | +49.8% | 0.32 | 0.25 | 2026-05-21,2026-05-25,2026-05-31,2026-06-10,2026-06-19 |

## Forward 6/21..6/23 Date Profiles

| policy_variant | target_date | trades | settled_trades | open_trades | wins | roi | avg_p_no_win | avg_actual_margin_f | avg_forecast_gap_f | avg_forecast_overestimate_f | cause_tags | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forced_gfs::payoff_ev05_p35_max2_day | 2026-06-21 | 2 | 2 | 0 | 0.00 | -100.0% | 0.81 | 0.10 | 7.29 | 7.19 | forecast_high_too_hot,trend_positive_but_exhausted | Denver,Tokyo |
| forced_gfs::payoff_ev05_p35_max2_day | 2026-06-22 | 2 | 2 | 0 | 0.00 | -100.0% | 0.77 | 0.00 | 4.85 | 4.85 | forecast_break_overestimated,forecast_high_too_hot,trend_positive_but_exhausted | Denver,NYC |
| forced_gfs::payoff_ev05_p35_max2_day | 2026-06-23 | 2 | 0 | 2 | 0.00 | +0.0% | 0.77 | -0.10 | 1.97 | 2.07 | forecast_break_overestimated,forecast_high_too_hot |  |
| forced_gfs::payoff_ev10_p40 | 2026-06-21 | 8 | 8 | 0 | 1.00 | -62.1% | 0.60 | 0.10 | 2.53 | 2.44 | forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | CapeTown,Chongqing,Denver,LA,Madrid,Tokyo,Warsaw |
| forced_gfs::payoff_ev10_p40 | 2026-06-22 | 14 | 14 | 0 | 1.00 | -76.2% | 0.60 | -0.07 | 0.03 | 0.10 | forecast_break_overestimated,trend_positive_but_exhausted | Austin,Beijing,Chongqing,Denver,Houston,LA,Manila,Miami,NYC,Seattle,Taipei,TelAviv,Tokyo |
| forced_gfs::payoff_ev10_p40 | 2026-06-23 | 7 | 0 | 7 | 0.00 | +0.0% | 0.62 | 0.31 | 3.13 | 2.82 | forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted |  |
| preferred_route::payoff_ev05_p35 | 2026-06-21 | 14 | 14 | 0 | 4.00 | -5.1% | 0.59 | 0.49 | 1.13 | 0.64 | non_gfs_city_cluster,trend_positive_but_exhausted | BuenosAires,CapeTown,Chongqing,Denver,Jeddah,LA,Madrid,Shanghai,Tokyo,Warsaw |
| preferred_route::payoff_ev05_p35 | 2026-06-22 | 17 | 17 | 0 | 1.00 | -80.4% | 0.59 | -0.08 | -0.14 | -0.06 | trend_positive_but_exhausted | Ankara,Austin,Beijing,Busan,Chongqing,Denver,Houston,LA,Manila,Miami,NYC,Seattle,Shanghai,Taipei,TelAviv,Tokyo |
| preferred_route::payoff_ev05_p35 | 2026-06-23 | 7 | 0 | 7 | 0.00 | +0.0% | 0.71 | 0.31 | 3.13 | 2.82 | forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted |  |
| preferred_route::payoff_ev05_p35_max2_day | 2026-06-21 | 2 | 2 | 0 | 0.00 | -100.0% | 0.77 | 0.30 | 7.47 | 7.17 | forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | CapeTown,Tokyo |
| preferred_route::payoff_ev05_p35_max2_day | 2026-06-22 | 2 | 2 | 0 | 0.00 | -100.0% | 0.83 | 0.00 | 4.85 | 4.85 | forecast_break_overestimated,forecast_high_too_hot,trend_positive_but_exhausted | Denver,NYC |
| preferred_route::payoff_ev05_p35_max2_day | 2026-06-23 | 2 | 0 | 2 | 0.00 | +0.0% | 0.82 | 0.00 | 1.97 | 1.97 | forecast_break_overestimated,forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted |  |
| preferred_route::payoff_ev10_p40 | 2026-06-21 | 13 | 13 | 0 | 3.00 | -21.1% | 0.60 | 0.35 | 1.56 | 1.21 | forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted | BuenosAires,CapeTown,Chongqing,Denver,Jeddah,LA,Madrid,Shanghai,Tokyo,Warsaw |
| preferred_route::payoff_ev10_p40 | 2026-06-22 | 16 | 16 | 0 | 1.00 | -79.2% | 0.60 | -0.10 | -0.00 | 0.10 | trend_positive_but_exhausted | Austin,Beijing,Busan,Chongqing,Denver,Houston,LA,Manila,Miami,NYC,Seattle,Shanghai,Taipei,TelAviv,Tokyo |
| preferred_route::payoff_ev10_p40 | 2026-06-23 | 7 | 0 | 7 | 0.00 | +0.0% | 0.71 | 0.31 | 3.13 | 2.82 | forecast_high_too_hot,non_gfs_city_cluster,trend_positive_but_exhausted |  |

## 判断错在哪里

1. 旧 afternoon-peak/near-noon 逻辑把“高温还会出现在后面”当作主目标；但 NO 的真钱 payoff 是“最终高温必须打穿当前 upper”。
2. 全错日里，模型分数和 forecast gap 仍然偏正，但实际 margin 经常接近 0 或为负；这是 forecast-high overestimate / capped-day 问题。
3. 多城市同日全错说明不是单城市 station/source 噪音，而是 day-regime：同一日的大尺度/日内形态让很多城市同时“不再够热”。
4. ECMWF/preferred route 会扩大历史坏样本暴露，但 forced-GFS 的 forward 同样亏，说明 source gate 只能降噪，不能替代 day-regime gate。

## 下一步改进

1. 新 label：训练 `capped_day_false_positive`，目标是 selected candidate 当天是否 `forecast_gap>0` 但 `final_margin<=0`。
2. 新特征：用 city-day 层聚合，而不是逐笔层；包括同日候选数、同日平均 forecast overestimate、GFS/ECMWF disagreement、候选城市族群、温度趋势是否开始衰竭。
3. 执行规则：candidate 模型过关后再过 day-regime gate；坏日型直接整日 skip 或 max1/day，不再让多城市同时下注。
4. source policy：短期研究默认 forced-GFS / GFS-route-only 做主对照；非 GFS 只有拿到 true previous-day PIT 后再恢复参赛。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_all_loss_day_forensics_v1/summary.json`
- All-loss profiles: `docs/analysis/2026-06/generated/current_bracket_no_all_loss_day_forensics_v1/all_loss_date_profiles.csv`
- Comparison: `docs/analysis/2026-06/generated/current_bracket_no_all_loss_day_forensics_v1/all_loss_vs_other_comparison.csv`
- Selected rows: `docs/analysis/2026-06/generated/current_bracket_no_all_loss_day_forensics_v1/selected_rows_forensic.csv`
- Forward profiles: `docs/analysis/2026-06/generated/current_bracket_no_all_loss_day_forensics_v1/forward_date_profiles.csv`
