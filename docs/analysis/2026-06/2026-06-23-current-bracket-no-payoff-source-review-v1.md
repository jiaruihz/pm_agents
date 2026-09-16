# Current-Bracket NO Payoff + Source Review V1

## 结论

这个方向值得继续，但不是因为这次已经找到可上线规则。真正有价值的发现是：旧表达式把目标学偏了，而且历史 PIT 预报层对 source/model 的处理太粗。

旧模型学的是“最高温是不是出现在午后”；这笔交易真正需要的是“current bracket 的 NO 是否会赢”，更具体地说，是最终最高温能不能明确穿过 current bracket upper 并留出 source/noise margin。

状态：`research_promising_shadow_only`。不改 live。

## Data Snapshot

- Generated at UTC: `2026-06-23T15:55:32+00:00`
- PIT scored rows: `4174`
- PIT date range: `2026-05-20`..`2026-06-20`
- Historical selected universe date range: `2026-05-20`..`2026-06-20`
- Previous-day forecast layer: `{'source': 'docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1/prevday_pit_forecast_rows.csv', 'reused': True}`
- Calibration overlay: `/Users/deepsleep/projects/weather-predict/calibration_results_v5.json`

重要限制：历史 previous-day PIT 层主要是 `gfs_daily`。这里的 ECMWF/ICON/JMA 只是城市级 WU calibration overlay，还不是完整 point-in-time 的 ECMWF/ICON/JMA 预报 replay。

## 为什么旧 Label 不对

`avg_gap` 的定义是 `previous-day forecast peak max - current bracket upper`。它只能说明前一日预报是否看起来会穿档，不是交易结算 label。

payoff label 应该是 `label_no_wins`：current bracket 的 NO 是否结算为 1。对“继续升温穿档”这个 setup，更干净的机制 label 是 `final_max > current bracket upper + margin`，这里暂用 F 城 `0.5F`、C 城 `0.25C`。

## 这次多跑一把后的结果

| variant | selected_trades | active_dates | cities | no_win_rate | up_margin_win_rate | roi | holdout_roi | selected_all_loss_days | selected_all_loss_trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_trade_base | 361 | 32 | 34 | +20.5% | +20.5% | -8.5% | +6.7% | 4 | 39 |
| old_afternoon_p50 | 155 | 32 | 30 | +29.0% | +29.0% | +32.3% | +73.7% | 6 | 18 |
| payoff_p50 | 58 | 29 | 26 | +24.1% | +24.1% | -6.3% | -8.1% | 20 | 35 |
| payoff_ev05_p35 | 117 | 31 | 28 | +29.9% | +29.9% | +30.5% | +70.2% | 13 | 34 |
| payoff_ev05_p35_default_wu | 113 | 31 | 26 | +30.1% | +30.1% | +32.2% | +76.0% | 14 | 34 |
| payoff_ev05_p35_default_wu_max2_day | 55 | 31 | 25 | +23.6% | +23.6% | +13.3% | +39.6% | 20 | 33 |
| up_margin_ev05_p25_default_wu_max2_day | 55 | 31 | 23 | +25.5% | +25.5% | +24.2% | +49.0% | 19 | 32 |

这张表要这么读：payoff/up-margin 模型的 holdout AUC 比 old afternoon label 更好，说明它确实更贴近结算机制；但直接拿它做 EV/threshold 选单，并没有解决 all-loss day 问题。

- old afternoon 版本：`155` 笔，ROI `+32.3%`，holdout ROI `+73.7%`，全亏 active days `6`。
- payoff+default_wu 版本：`113` 笔，ROI `+32.2%`，holdout ROI `+76.0%`，全亏 active days `14`。

所以本轮不是“新规则已胜出”，而是“target 修正方向成立，但还缺 day-regime/source-model replay”。

## Source / Model Overlay

| group_col | group_value | trades | dates | cities | win_rate | roi | up_margin_win_rate | avg_actual_margin_to_upper_native |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| source_bucket | default_wu | 332 | 32 | 32 | +20.8% | -7.4% | +20.8% | 0.163 |
| source_bucket | other_or_unknown | 29 | 22 | 2 | +17.2% | -20.7% | +17.2% | 0.157 |
| calibration_best_model | ecmwf | 113 | 31 | 13 | +15.0% | -34.2% | +15.0% | 0.126 |
| calibration_best_model | gfs | 177 | 31 | 14 | +22.6% | +1.4% | +22.6% | 0.185 |
| calibration_best_model | icon_eu | 34 | 23 | 5 | +26.5% | +31.0% | +26.5% | 0.275 |
| calibration_best_model | jma | 17 | 17 | 1 | +17.6% | -21.8% | +17.6% | 0.078 |
| calibration_best_model | nan | 20 | 20 | 1 | +25.0% | -5.9% | +25.0% | 0.050 |

source overlay 的意义不是现在就删城市，而是说明 routing 层必须接回来。当前 PIT 用 GFS-first，但历史 calibration 明确存在 GFS/ECMWF/ICON/JMA 的城市差异；下一版应该消费 city preferred model 的 PIT forecast，并把多模型分歧当成风险特征。

## All-Loss Days Focus

| variant | target_date | trades | wins | roi | avg_p_no_win | avg_p_up_margin | avg_margin | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_trade_base | 2026-05-21 | 6 | 0 | -100.0% | 0.429 | 0.458 | -0.130 | Beijing,Helsinki,LA,SanFrancisco,SaoPaulo,Shanghai |
| baseline_trade_base | 2026-05-25 | 12 | 0 | -100.0% | 0.250 | 0.212 | -0.083 | Beijing,Busan,Helsinki,Jeddah,Karachi,LA,Shanghai,Singapore,Taipei,TelAviv,Tokyo,Wuhan |
| baseline_trade_base | 2026-05-31 | 9 | 0 | -100.0% | 0.370 | 0.350 | -0.111 | Amsterdam,Atlanta,Busan,LA,Munich,Shanghai,Taipei,TelAviv,Wellington |
| baseline_trade_base | 2026-06-10 | 12 | 0 | -100.0% | 0.304 | 0.277 | -0.046 | Amsterdam,BuenosAires,Helsinki,Houston,Karachi,Manila,Munich,Shanghai,Taipei,TelAviv,Warsaw,Wellington |
| old_afternoon_p50 | 2026-05-21 | 4 | 0 | -100.0% | 0.448 | 0.510 | -0.250 | Beijing,Helsinki,LA,SanFrancisco |
| old_afternoon_p50 | 2026-05-25 | 5 | 0 | -100.0% | 0.307 | 0.273 | -0.044 | Busan,Shanghai,Singapore,Tokyo,Wuhan |
| old_afternoon_p50 | 2026-05-31 | 3 | 0 | -100.0% | 0.524 | 0.498 | -0.111 | Amsterdam,Busan,Munich |
| old_afternoon_p50 | 2026-06-03 | 2 | 0 | -100.0% | 0.329 | 0.269 | 0.056 | Helsinki,Wuhan |
| old_afternoon_p50 | 2026-06-10 | 3 | 0 | -100.0% | 0.602 | 0.549 | -0.111 | BuenosAires,Munich,Warsaw |
| old_afternoon_p50 | 2026-06-20 | 1 | 0 | -100.0% | 0.270 | 0.222 | 0.111 | Manila |
| payoff_ev05_p35_default_wu | 2026-05-21 | 3 | 0 | -100.0% | 0.607 | 0.574 | -0.333 | Beijing,Helsinki,LA |
| payoff_ev05_p35_default_wu | 2026-05-24 | 2 | 0 | -100.0% | 0.450 | 0.391 | 0.000 | Ankara,Lucknow |
| payoff_ev05_p35_default_wu | 2026-05-25 | 2 | 0 | -100.0% | 0.414 | 0.301 | -0.611 | LA,Shanghai |
| payoff_ev05_p35_default_wu | 2026-05-27 | 1 | 0 | -100.0% | 0.501 | 0.495 | 0.000 | BuenosAires |
| payoff_ev05_p35_default_wu | 2026-05-29 | 1 | 0 | -100.0% | 0.521 | 0.541 | 0.000 | Wellington |
| payoff_ev05_p35_default_wu | 2026-05-30 | 2 | 0 | -100.0% | 0.510 | 0.650 | 0.111 | Miami,Warsaw |
| payoff_ev05_p35_default_wu | 2026-05-31 | 5 | 0 | -100.0% | 0.543 | 0.527 | -0.111 | Amsterdam,Atlanta,LA,Munich,Wellington |
| payoff_ev05_p35_default_wu | 2026-06-01 | 5 | 0 | -100.0% | 0.445 | 0.460 | 0.044 | LA,NYC,Warsaw,Wellington,Wuhan |
| payoff_ev05_p35_default_wu | 2026-06-03 | 1 | 0 | -100.0% | 0.540 | 0.637 | 0.111 | Shanghai |
| payoff_ev05_p35_default_wu | 2026-06-05 | 3 | 0 | -100.0% | 0.527 | 0.505 | 0.000 | Ankara,SaoPaulo,Singapore |
| payoff_ev05_p35_default_wu | 2026-06-10 | 4 | 0 | -100.0% | 0.607 | 0.556 | -0.111 | BuenosAires,Munich,Warsaw,Wellington |
| payoff_ev05_p35_default_wu | 2026-06-12 | 1 | 0 | -100.0% | 0.647 | 0.720 | 0.000 | Lucknow |
| payoff_ev05_p35_default_wu | 2026-06-15 | 2 | 0 | -100.0% | 0.659 | 0.606 | -0.556 | Houston,Lucknow |
| payoff_ev05_p35_default_wu | 2026-06-20 | 2 | 0 | -100.0% | 0.498 | 0.351 | 0.056 | Tokyo,Wellington |

全亏日仍然是核心 blocker。特别是 payoff 模型虽然更贴结算，但 naive EV 选单会把很多“概率看起来高、实际只差一点没穿档”的行挑出来，这说明还需要 day-level regime 和 source/model PIT replay，而不是只换 label。

## 下一步实验

1. 补 preferred-model PIT forecast：每城按 calibration 选择 GFS/ECMWF/ICON/JMA，不再 GFS-first。
2. 主 label 改成 `label_no_wins` / `upper + margin`；`afternoon_peak` 降级成辅助特征。
3. 做 day-regime gate：同一天最多 1-2 笔只是临时风控，不是根因修复；根因是识别“看似升温但不穿档”的日型。
4. source-sensitive / non-default source 单独桶，不混入 default-WU 泛化结论。
5. 继续 forward shadow；至少等 post-6/23 多个 settled day 通过后，才重新讨论 tiny live。

## Files

- JSON: `docs/analysis/2026-06/generated/current_bracket_no_payoff_source_review_v1/summary.json`
- Variants: `docs/analysis/2026-06/generated/current_bracket_no_payoff_source_review_v1/variant_summary.csv`
- Daily summary: `docs/analysis/2026-06/generated/current_bracket_no_payoff_source_review_v1/daily_variant_summary.csv`
- All-loss details: `docs/analysis/2026-06/generated/current_bracket_no_payoff_source_review_v1/all_loss_day_trade_details.csv`
- Source/model overlay: `docs/analysis/2026-06/generated/current_bracket_no_payoff_source_review_v1/source_model_overlay_summary.csv`
- Selected rows: `docs/analysis/2026-06/generated/current_bracket_no_payoff_source_review_v1/selected_trade_rows.csv`
