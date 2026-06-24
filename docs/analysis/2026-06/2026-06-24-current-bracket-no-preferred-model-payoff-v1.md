# Current-Bracket NO Preferred-Model Payoff V1

## 结论

这轮把两件事真正接上了：payoff/up-margin label，以及按城市 calibration 路由的 forecast replay。结果是：方向仍然值得继续，但还没有变成 live-ready 规则。

最重要的变化不是 ROI 点估，而是口径更诚实：非 GFS 的部分现在明确标成 `source-corrected historical replay`，不是冒充 true previous-day PIT。严格 PIT 历史仍主要只有 GFS daily。

Verdict: `research_promising_shadow_only`，live_ready=`False`。

## 数据层

- Generated at UTC: `2026-06-23T18:13:03+00:00`
- Scored rows: `4306`
- Trade-base rows: `499`
- Date range: `2026-05-20`..`2026-06-20`
- Split date: `2026-06-10`
- Forecast route status: `{'calibration_best_available': 839, 'fallback_best_available_gfs_ecmwf': 205, 'fallback_gfs_true_prevday': 27, 'fallback_gfs_historical': 1}`

## 模型判别力

训练只用 trade-base rows，避免让大量不可交易/高价行主导 payoff label。

| label | period | rows | active_dates | label_rate | auc | brier |
| --- | --- | --- | --- | --- | --- | --- |
| label_no_wins | train | 348 | 22 | +17.5% | 0.800 | 0.192 |
| label_no_wins | holdout | 151 | 10 | +18.5% | 0.718 | 0.184 |
| label_up_margin | train | 348 | 22 | +17.5% | 0.800 | 0.192 |
| label_up_margin | holdout | 151 | 10 | +18.5% | 0.718 | 0.184 |

## 交易表达

| variant | selected_trades | active_dates | cities | no_win_rate | up_margin_win_rate | roi | roi_ci_low | roi_ci_high | holdout_roi | selected_all_loss_days | selected_all_loss_trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_trade_base | 375 | 32 | 35 | +20.5% | +20.5% | -8.4% | -0.271 | 0.105 | +7.0% | 4 | 40 |
| payoff_ev05_p35 | 245 | 32 | 31 | +26.9% | +26.9% | +20.1% | -0.065 | 0.465 | +27.7% | 5 | 32 |
| payoff_ev10_p40 | 225 | 32 | 31 | +28.0% | +28.0% | +25.6% | -0.007 | 0.524 | +29.9% | 5 | 26 |
| up_margin_ev05_p30 | 264 | 32 | 32 | +26.1% | +26.1% | +16.7% | -0.070 | 0.399 | +29.1% | 4 | 28 |
| up_margin_ev10_p35 | 241 | 32 | 31 | +26.6% | +26.6% | +19.5% | -0.068 | 0.456 | +26.6% | 5 | 32 |
| payoff_ev05_p35_max2_day | 62 | 32 | 24 | +38.7% | +38.7% | +102.1% | 0.406 | 1.697 | +136.7% | 12 | 23 |
| up_margin_ev05_p30_max2_day | 62 | 32 | 24 | +38.7% | +38.7% | +102.1% | 0.406 | 1.697 | +136.7% | 12 | 23 |

读法：payoff/up-margin label 的排序能力提升，但 all-loss day 仍没有被根治；所以这还是 shadow/research。

## Forecast Route Overlay

| group_col | group_value | trades | dates | cities | win_rate | roi | avg_margin |
| --- | --- | --- | --- | --- | --- | --- | --- |
| forecast_route_status | calibration_best_available | 302 | 32 | 28 | +19.5% | -12.8% | 0.164 |
| forecast_route_status | fallback_best_available_gfs_ecmwf | 52 | 29 | 6 | +25.0% | +18.1% | 0.224 |
| forecast_route_status | fallback_gfs_historical | 1 | 1 | 1 | +0.0% | -100.0% | 0.000 |
| forecast_route_status | fallback_gfs_true_prevday | 20 | 20 | 1 | +25.0% | -5.9% | 0.050 |
| forecast_route_model | ecmwf | 146 | 32 | 16 | +15.8% | -31.6% | 0.129 |
| forecast_route_model | gfs | 229 | 32 | 19 | +23.6% | +6.5% | 0.189 |
| calibration_best_model |  | 21 | 21 | 1 | +23.8% | -10.4% | 0.048 |
| calibration_best_model | ecmwf | 117 | 31 | 13 | +15.4% | -32.8% | 0.131 |
| calibration_best_model | gfs | 185 | 32 | 15 | +22.2% | -0.1% | 0.185 |
| calibration_best_model | icon_eu | 35 | 24 | 5 | +28.6% | +37.5% | 0.295 |
| calibration_best_model | jma | 17 | 17 | 1 | +17.6% | -21.8% | 0.078 |

## All-Loss Days

| variant | target_date | trades | wins | roi | avg_p_no_win | avg_p_up_margin | avg_margin | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_trade_base | 2026-05-21 | 6 | 0 | -100.0% | 0.532 | 0.532 | -0.130 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| baseline_trade_base | 2026-05-25 | 12 | 0 | -100.0% | 0.395 | 0.395 | -0.083 | Beijing,Busan,Helsinki,Jeddah,Karachi,LA,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| baseline_trade_base | 2026-05-31 | 9 | 0 | -100.0% | 0.435 | 0.435 | -0.111 | Amsterdam,Atlanta,Busan,LA,Munich,Shanghai,Taipei,TelAviv,Wellington |
| baseline_trade_base | 2026-06-10 | 13 | 0 | -100.0% | 0.392 | 0.392 | -0.043 | Amsterdam,BuenosAires,Helsinki,Houston,Karachi,Manila,Miami,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| payoff_ev05_p35 | 2026-05-21 | 6 | 0 | -100.0% | 0.532 | 0.532 | -0.130 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| payoff_ev05_p35 | 2026-05-25 | 8 | 0 | -100.0% | 0.490 | 0.490 | -0.194 | Busan,Helsinki,LA,Shanghai,Taipei,TelAviv,Tokyo,Wuhan |
| payoff_ev05_p35 | 2026-05-31 | 5 | 0 | -100.0% | 0.591 | 0.591 | -0.089 | Amsterdam,Busan,LA,Taipei,TelAviv |
| payoff_ev05_p35 | 2026-06-10 | 7 | 0 | -100.0% | 0.467 | 0.467 | -0.032 | Amsterdam,BuenosAires,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| payoff_ev05_p35 | 2026-06-19 | 6 | 0 | -100.0% | 0.456 | 0.456 | -0.222 | Busan,CapeTown,Houston,Manila,TelAviv,Wellington |
| payoff_ev05_p35_max2_day | 2026-05-21 | 2 | 0 | -100.0% | 0.730 | 0.730 | -0.611 | Helsinki,LA |
| payoff_ev05_p35_max2_day | 2026-05-24 | 2 | 0 | -100.0% | 0.666 | 0.666 | 0.000 | Ankara,Tokyo |
| payoff_ev05_p35_max2_day | 2026-05-25 | 2 | 0 | -100.0% | 0.660 | 0.660 | -0.111 | Shanghai,Tokyo |
| payoff_ev05_p35_max2_day | 2026-05-31 | 2 | 0 | -100.0% | 0.787 | 0.787 | -0.111 | Amsterdam,LA |
| payoff_ev05_p35_max2_day | 2026-06-01 | 2 | 0 | -100.0% | 0.725 | 0.725 | 0.000 | LA,NYC |
| payoff_ev05_p35_max2_day | 2026-06-02 | 2 | 0 | -100.0% | 0.720 | 0.720 | -0.556 | Amsterdam,Atlanta |
| payoff_ev05_p35_max2_day | 2026-06-04 | 1 | 0 | -100.0% | 0.906 | 0.906 | 0.000 | LA |
| payoff_ev05_p35_max2_day | 2026-06-08 | 2 | 0 | -100.0% | 0.692 | 0.692 | -0.111 | Seattle,Tokyo |
| payoff_ev05_p35_max2_day | 2026-06-10 | 2 | 0 | -100.0% | 0.521 | 0.521 | -0.111 | Shanghai,Taipei |
| payoff_ev05_p35_max2_day | 2026-06-11 | 2 | 0 | -100.0% | 0.710 | 0.710 | -0.444 | LA,Wuhan |
| payoff_ev05_p35_max2_day | 2026-06-17 | 2 | 0 | -100.0% | 0.511 | 0.511 | -0.500 | Chengdu,LA |
| payoff_ev05_p35_max2_day | 2026-06-19 | 2 | 0 | -100.0% | 0.524 | 0.524 | 0.056 | CapeTown,Manila |
| payoff_ev10_p40 | 2026-05-21 | 5 | 0 | -100.0% | 0.559 | 0.559 | -0.156 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo |
| payoff_ev10_p40 | 2026-05-25 | 7 | 0 | -100.0% | 0.505 | 0.505 | -0.222 | Busan,Helsinki,LA,Shanghai,Taipei,TelAviv,Tokyo |
| payoff_ev10_p40 | 2026-05-31 | 5 | 0 | -100.0% | 0.591 | 0.591 | -0.089 | Amsterdam,Busan,LA,Taipei,TelAviv |
| payoff_ev10_p40 | 2026-06-10 | 5 | 0 | -100.0% | 0.499 | 0.499 | -0.067 | BuenosAires,Shanghai,Taipei,TelAviv,Warsaw |
| payoff_ev10_p40 | 2026-06-19 | 4 | 0 | -100.0% | 0.502 | 0.502 | -0.028 | CapeTown,Manila,TelAviv,Wellington |
| up_margin_ev05_p30 | 2026-05-21 | 6 | 0 | -100.0% | 0.532 | 0.532 | -0.130 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| up_margin_ev05_p30 | 2026-05-25 | 8 | 0 | -100.0% | 0.490 | 0.490 | -0.194 | Busan,Helsinki,LA,Shanghai,Taipei,TelAviv,Tokyo,Wuhan |
| up_margin_ev05_p30 | 2026-05-31 | 6 | 0 | -100.0% | 0.547 | 0.547 | -0.111 | Amsterdam,Busan,LA,Shanghai,Taipei,TelAviv |
| up_margin_ev05_p30 | 2026-06-10 | 8 | 0 | -100.0% | 0.451 | 0.451 | -0.042 | Amsterdam,BuenosAires,Helsinki,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| up_margin_ev05_p30_max2_day | 2026-05-21 | 2 | 0 | -100.0% | 0.730 | 0.730 | -0.611 | Helsinki,LA |
| up_margin_ev05_p30_max2_day | 2026-05-24 | 2 | 0 | -100.0% | 0.666 | 0.666 | 0.000 | Ankara,Tokyo |
| up_margin_ev05_p30_max2_day | 2026-05-25 | 2 | 0 | -100.0% | 0.660 | 0.660 | -0.111 | Shanghai,Tokyo |
| up_margin_ev05_p30_max2_day | 2026-05-31 | 2 | 0 | -100.0% | 0.787 | 0.787 | -0.111 | Amsterdam,LA |
| up_margin_ev05_p30_max2_day | 2026-06-01 | 2 | 0 | -100.0% | 0.725 | 0.725 | 0.000 | LA,NYC |
| up_margin_ev05_p30_max2_day | 2026-06-02 | 2 | 0 | -100.0% | 0.720 | 0.720 | -0.556 | Amsterdam,Atlanta |
| up_margin_ev05_p30_max2_day | 2026-06-04 | 1 | 0 | -100.0% | 0.906 | 0.906 | 0.000 | LA |
| up_margin_ev05_p30_max2_day | 2026-06-08 | 2 | 0 | -100.0% | 0.692 | 0.692 | -0.111 | Seattle,Tokyo |
| up_margin_ev05_p30_max2_day | 2026-06-10 | 2 | 0 | -100.0% | 0.521 | 0.521 | -0.111 | Shanghai,Taipei |
| up_margin_ev05_p30_max2_day | 2026-06-11 | 2 | 0 | -100.0% | 0.710 | 0.710 | -0.444 | LA,Wuhan |
| up_margin_ev05_p30_max2_day | 2026-06-17 | 2 | 0 | -100.0% | 0.511 | 0.511 | -0.500 | Chengdu,LA |
| up_margin_ev05_p30_max2_day | 2026-06-19 | 2 | 0 | -100.0% | 0.524 | 0.524 | 0.056 | CapeTown,Manila |
| up_margin_ev10_p35 | 2026-05-21 | 6 | 0 | -100.0% | 0.532 | 0.532 | -0.130 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| up_margin_ev10_p35 | 2026-05-25 | 8 | 0 | -100.0% | 0.490 | 0.490 | -0.194 | Busan,Helsinki,LA,Shanghai,Taipei,TelAviv,Tokyo,Wuhan |
| up_margin_ev10_p35 | 2026-05-31 | 5 | 0 | -100.0% | 0.591 | 0.591 | -0.089 | Amsterdam,Busan,LA,Taipei,TelAviv |
| up_margin_ev10_p35 | 2026-06-10 | 7 | 0 | -100.0% | 0.461 | 0.461 | -0.032 | Amsterdam,BuenosAires,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| up_margin_ev10_p35 | 2026-06-19 | 6 | 0 | -100.0% | 0.456 | 0.456 | -0.222 | Busan,CapeTown,Houston,Manila,TelAviv,Wellington |

## Forward Sanity Check

| variant | selected_trades | active_dates | settled_trades | open_shadow_trades | settled_win_rate | settled_roi | settled_profit_usd | dates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_trade_base | 34 | 3 | 24 | 10 | +16.7% | -40.0% | $-47.97 | 2026-06-21,2026-06-22,2026-06-23 |
| payoff_ev05_p35 | 31 | 3 | 23 | 8 | +17.4% | -37.4% | $-42.97 | 2026-06-21,2026-06-22,2026-06-23 |
| payoff_ev10_p40 | 31 | 3 | 23 | 8 | +17.4% | -37.4% | $-42.97 | 2026-06-21,2026-06-22,2026-06-23 |
| up_margin_ev05_p30 | 32 | 3 | 23 | 9 | +17.4% | -37.4% | $-42.97 | 2026-06-21,2026-06-22,2026-06-23 |
| payoff_ev05_p35_max2_day | 6 | 3 | 4 | 2 | +0.0% | -100.0% | $-20.00 | 2026-06-21,2026-06-22,2026-06-23 |

这一步专门防止历史 replay 自嗨：已结算 forward 若继续亏，规则只能继续 shadow。

## 还差什么

1. 真正补齐非 GFS 的 previous-day PIT snapshot，而不只是 historical replay。
2. 单独建 day-regime classifier，识别“看似会继续升温但最终卡在同一 bracket”的日型。
3. 把 6/21 之后的 forward shadow 日子滚动结算进来，特别是 6/24 之后的样本。
4. 在 day-regime 没过之前，不允许 live；最多继续 zero-notional shadow。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_preferred_model_payoff_v1/summary.json`
- Forecast rows: `docs/analysis/2026-06/generated/current_bracket_no_preferred_model_payoff_v1/preferred_forecast_rows.csv`
- Variants: `docs/analysis/2026-06/generated/current_bracket_no_preferred_model_payoff_v1/variant_summary.csv`
- Daily: `docs/analysis/2026-06/generated/current_bracket_no_preferred_model_payoff_v1/daily_variant_summary.csv`
- All-loss details: `docs/analysis/2026-06/generated/current_bracket_no_preferred_model_payoff_v1/all_loss_day_trade_details.csv`
- Selected rows: `docs/analysis/2026-06/generated/current_bracket_no_preferred_model_payoff_v1/selected_trade_rows.csv`
- Forward validation/shadow: `docs/analysis/2026-06/generated/current_bracket_no_preferred_model_payoff_v1/forward_validation_and_shadow.csv`
