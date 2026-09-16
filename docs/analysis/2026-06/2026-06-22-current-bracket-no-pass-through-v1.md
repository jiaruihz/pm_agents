# Current-Bracket NO Pass-Through v1

> 2026-06-23 update: this first pass used an over-strict hard-AND `ex_ante_core`
> rule and should be treated as a diagnostic funnel, not the best version of the
> idea. The corrected framing is the ex-ante afternoon-peak classifier in
> `2026-06-23-current-bracket-no-afternoon-peak-classifier-v1.md`.

## 数据快照

- 数据源：专用 reheat feature factory rows + 同窗真实 current-bracket NO ask；`docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1_feature_factory/reheat_feature_rows.csv`。
- 生成时间：`2026-06-22T15:55:41+00:00`；feature factory `generated_at_utc=2026-06-22T15:47:18+00:00`。
- 窗口：`2026-05-19`..`2026-06-20`，settled current-NO states `9196`。
- CLOB gate：`gate_pass=True`；本报告是 opportunity replay，不发布 live_real PnL。
- Unsettled：feature layer 当前回测行 `0`；missing bracket：`0`（来自 `settlement_outcomes` final winner 完整覆盖）。
- Rebuild 备注：`run_stack.sh` 完成 DB/fact/gate 后因 FE 5174 端口仍 busy 退出 1；数据层和 gate 已单独核验。

## 结论

在 2026-05-19..2026-06-20，`ex_ante_core` 选出 2 笔 / 2.0 天，固定 $5 notional 的 current-bracket NO ROI 为 -100.0%（日期 bootstrap 95% CI [NA, NA]），相对同价位/同午间 NO baseline 的 excess ROI 为 -91.1%，holdout ROI 为 NA。

机制上界很强但不可交易：`oracle_actual_peak_afternoon_price_only` 选出 76 笔，ROI +130.0% （CI [+35.5%, +256.2%]）。这说明“午后继续创新高”确实会让 current-bracket NO 赚钱，但这个条件本身是事后信息。

最像可交易 proxy 的探索性切片 `exploratory_h13_14_warming_gap05` 有 27 笔，ROI +67.7% （CI [-14.2%, +151.4%]，holdout +127.7%）。点估不错，但 CI 跨 0，是研究线索，不是 live edge。

三门：significance=FAIL / baseline=FAIL / forward=FAIL；conclusion=inconclusive。

直白交易动作：不加 live。这个形态的物理机制是真的，但可交易的“午后 peak 判定器”还没过三门；最多继续做 research / zero-notional shadow 采样。

## 规则口径

`ex_ante_core` = local h10-14，current temp 在 running max 附近且 running max age <=45m，1h/3h 仍升温，forecast peak 至少晚 2h，forecast max 高于 current bracket upper >=0.5 native unit，云/风/湿风险低，真实 NO ask 0.01..0.35，top ask notional >=$5。每个 city-date 只取第一笔触发。

`oracle_*` 行只用于机制验证，包含事后 actual peak / final max 条件，不可直接交易。

## 主表

| variant | selected_trades | active_dates | avg_no_ask | no_win_rate | pass_through_win_rate | roi | roi_ci_low | roi_ci_high | holdout_roi | baseline_roi | excess_roi_vs_baseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_midday_no_ask_le_35_cap5 | 192 | 32.000 | 0.212 | 0.172 | 0.172 | -8.9% | -46.6% | +42.9% | +53.4% | NA | NA |
| oracle_actual_peak_afternoon_price_only | 76 | 29.000 | 0.221 | 0.434 | 0.434 | +130.0% | +35.5% | +256.2% | +283.4% | -8.9% | +139.0% |
| oracle_peak_after_decision_2h_price_only | 81 | 31.000 | 0.238 | 0.210 | 0.210 | -21.3% | -58.2% | +16.4% | +0.5% | -8.9% | -12.3% |
| oracle_actual_pass_through_price_only | 82 | 27.000 | 0.211 | 0.402 | 0.402 | +113.2% | +31.4% | +231.8% | +256.0% | -8.9% | +122.1% |
| forecast_only_midday_gap1 | 1 | 1.000 | 0.150 | 0.000 | 0.000 | -100.0% | NA | NA | NA | -8.9% | -91.1% |
| warming_only_midday | 28 | 21.000 | 0.221 | 0.179 | 0.179 | -26.3% | -86.3% | +31.3% | +38.5% | -8.9% | -17.3% |
| ex_ante_core | 2 | 2.000 | 0.190 | 0.000 | 0.000 | -100.0% | NA | NA | NA | -8.9% | -91.1% |
| ex_ante_strict | 0 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NA |
| oracle_afternoon_peak_ex_ante_core | 0 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NA |
| oracle_peak_after_decision_2h_ex_ante_core | 2 | 2.000 | 0.190 | 0.000 | 0.000 | -100.0% | NA | NA | NA | -8.9% | -91.1% |
| oracle_actual_pass_through_ex_ante_core | 0 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NA |
| exploratory_h13_14_warming_gap05 | 27 | 15.000 | 0.233 | 0.370 | 0.370 | +67.7% | -14.2% | +151.4% | +127.7% | -8.9% | +76.7% |
| exploratory_h14_risk_gap0 | 33 | 19.000 | 0.214 | 0.303 | 0.303 | +39.9% | -17.9% | +91.7% | +114.3% | -8.9% | +48.8% |
| exploratory_h14_ask20_risk | 45 | 25.000 | 0.101 | 0.222 | 0.222 | +109.3% | -30.3% | +345.0% | +277.4% | -8.9% | +118.2% |

## Threshold Sweep

| variant | selected_trades | active_dates | avg_no_ask | no_win_rate | roi | roi_ci_low | roi_ci_high | holdout_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| warming_gap|noask<=0.50|gap>=0.0 | 100 | 30.000 | 0.356 | 0.340 | +5.5% | -29.4% | +45.1% | +64.2% |
| warming_gap|noask<=0.50|gap>=0.5 | 71 | 28.000 | 0.357 | 0.380 | +15.7% | -24.6% | +63.9% | +78.4% |
| warming_gap|noask<=0.40|gap>=0.0 | 56 | 25.000 | 0.274 | 0.339 | +31.2% | -22.7% | +85.9% | +109.5% |
| warming_gap|noask<=0.35|gap>=0.0 | 43 | 23.000 | 0.240 | 0.302 | +34.5% | -29.7% | +101.5% | +110.9% |
| warming_gap|noask<=0.50|gap>=1.0 | 43 | 26.000 | 0.356 | 0.349 | +9.5% | -33.1% | +58.7% | +7.4% |
| warming_gap|noask<=0.40|gap>=0.5 | 40 | 21.000 | 0.274 | 0.375 | +41.6% | -18.4% | +111.7% | +116.3% |
| new_high_gap|noask<=0.50|gap>=0.0 | 39 | 22.000 | 0.351 | 0.410 | +24.7% | -32.4% | +88.5% | +131.9% |
| warming_gap|noask<=0.30|gap>=0.0 | 31 | 19.000 | 0.201 | 0.323 | +61.8% | -23.9% | +147.1% | +186.1% |
| warming_gap|noask<=0.35|gap>=0.5 | 30 | 17.000 | 0.237 | 0.333 | +45.6% | -34.3% | +127.7% | +104.9% |
| new_high_gap|noask<=0.50|gap>=0.5 | 30 | 18.000 | 0.345 | 0.400 | +22.1% | -48.8% | +99.9% | +124.3% |

## 8 环覆盖

- 描述性绩效：PASS，opportunity replay fixed-notional PnL。
- 统计推断：PASS，按 `target_date` block bootstrap。
- 信号判别：PARTIAL，只验证手工条件切片，不是独立模型 IC。
- 概率分布评估：NA，未校准概率。
- 执行微结构：PARTIAL，真实 NO ask + top ask capacity；未模拟 queue / stale chase。
- 容量：PARTIAL，仅 `$5` top ask notional gate。
- 组合相关性：PARTIAL，按日期 bootstrap，未做跨策略组合叠加。
- 基准/反事实：PASS，比较同午间同 price/cap baseline；未通过 baseline gate。

## 输出

- JSON：`docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1/summary.json`
- Variant CSV：`docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1/variant_summary.csv`
- Sweep CSV：`docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1/threshold_sweep.csv`
- Selected signals：`docs/analysis/2026-06/generated/current_bracket_no_pass_through_v1/selected_signals.csv`

## 限制

- `cloud/rain/sea breeze risk` 这里只能用 METAR sky/wind/RH/dewpoint proxy，没有真正的海风边界层特征。
- 6/21 settlement 已有，但 observed-detail/forecast-peak research layer 没完整补到 6/21，所以本报告严守到 6/20。
- `oracle_*` 不是可交易规则，只说明“如果事后知道午后继续创新高”，current-NO 会怎样。
