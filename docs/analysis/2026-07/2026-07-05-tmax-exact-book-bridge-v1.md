# Tmax Exact-Book Bridge v1

> generated_at_utc: `2026-07-05T14:16:42+00:00`
> source P5: `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/opportunities.csv`
> source atlas: `docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv`
> Scope: research/shadow only. No live runner or order policy changed.

## 结论

- 这版完成的是 `四桶概率 -> exact sibling 表达` 的最小实验，不是全 ladder hazard 模型。
- d1/d2 YES 可以从 `1 - NO bid` 构造出可成交 ask proxy；`ask>=0.40 + fee_edge02` verified 上 bridge_no_current_yes_5expr 为 `+9.7%`，legacy_4expr 为 `+8.4%`。
- 这个改善方向是合理的，但 dev/verified 都没有足够窄的 CI，且 d1_yes 单腿仍弱；新的 live 表达不应替换现有策略。合理动作是把 `bridge_no_current_yes_5expr` 作为 shadow target-book bridge 记录，继续补全真正 full-ladder/hazard 概率。
- `current_yes` 仍然不该回 live：它在 Lucknow 暴露的是重锚和 basis 风险；本实验把 `no_current_yes` 单独列出来，而不是靠单日事故把 d1_no 删掉。

## 口径

- 模型：固定 `loo_no_city_source_blend` 的 current/d1/d2/tail 概率，不重训、不新增天气 gate。
- 表达集：legacy_4expr=`current_yes/current_no/d1_no/d2_no`；bridge_6expr=legacy + `d1_yes/d2_yes`；bridge_no_current_yes_5expr=去掉 `current_yes` 后的 bridge。
- d1/d2 YES ask：`1 - d1_no_bid` / `1 - d2_no_bid`。这是 executable ask proxy；若要 live，需要实时 fresh CLOB 重新拉 sibling book。
- 执行成本：taker fee `0.05 * ask * (1-ask)`；`fee_edge02` 用 `p_win - ask - fee >= 0.02` 选表达。
- 回测单位：每 city-day 第一条 eligible state；固定 5 shares 只影响美元数，不影响 ROI。

## Policy Summary

| scope | ask_floor | selection_rule | expression_set | rows | dates | cities | win_rate | cost_net | pnl_net | roi_net | roi_net_ci_low | roi_net_ci_high | avg_ask | avg_fee_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | 0.20 | fee_edge02 | legacy_4expr | 398 | 19 | 36 | +66.8% | 251.51 | 14.49 | +5.8% | +0.4% | +11.8% | 0.622 | +4.3% |
| dev_cv | 0.20 | fee_edge02 | legacy_no_current_yes_3expr | 364 | 19 | 36 | +68.4% | 236.34 | 12.66 | +5.4% | +0.2% | +10.8% | 0.639 | +4.3% |
| dev_cv | 0.20 | gross_edge02 | legacy_4expr | 474 | 19 | 36 | +66.0% | 298.91 | 14.09 | +4.7% | -1.2% | +11.1% | 0.620 | +3.5% |
| dev_cv | 0.40 | fee_edge02 | bridge_6expr | 402 | 19 | 36 | +66.9% | 257.15 | 11.85 | +4.6% | -0.9% | +10.0% | 0.629 | +4.3% |
| dev_cv | 0.40 | fee_edge02 | bridge_no_current_yes_5expr | 373 | 19 | 36 | +67.3% | 240.47 | 10.53 | +4.4% | -0.4% | +8.6% | 0.634 | +4.2% |
| dev_cv | 0.40 | fee_edge02 | legacy_4expr | 369 | 19 | 36 | +70.5% | 249.29 | 10.71 | +4.3% | -0.8% | +9.7% | 0.665 | +4.2% |
| dev_cv | 0.40 | fee_edge02 | legacy_no_current_yes_3expr | 339 | 19 | 36 | +71.4% | 231.79 | 10.21 | +4.4% | -0.0% | +8.3% | 0.674 | +4.2% |
| verified_forward | 0.20 | fee_edge02 | legacy_4expr | 152 | 12 | 34 | +63.2% | 87.86 | 8.14 | +9.3% | +0.3% | +18.9% | 0.568 | +5.2% |
| verified_forward | 0.20 | fee_edge02 | legacy_no_current_yes_3expr | 137 | 12 | 34 | +64.2% | 81.43 | 6.57 | +8.1% | -1.6% | +16.4% | 0.584 | +5.3% |
| verified_forward | 0.20 | gross_edge02 | legacy_4expr | 192 | 12 | 36 | +64.1% | 112.56 | 10.44 | +9.3% | +0.2% | +17.8% | 0.576 | +4.0% |
| verified_forward | 0.40 | fee_edge02 | bridge_6expr | 139 | 12 | 36 | +70.5% | 88.95 | 9.05 | +10.2% | -0.9% | +20.9% | 0.629 | +4.7% |
| verified_forward | 0.40 | fee_edge02 | bridge_no_current_yes_5expr | 126 | 12 | 35 | +70.6% | 81.10 | 7.90 | +9.7% | -2.4% | +21.0% | 0.633 | +4.8% |
| verified_forward | 0.40 | fee_edge02 | legacy_4expr | 125 | 12 | 34 | +72.8% | 83.97 | 7.03 | +8.4% | -2.5% | +19.5% | 0.662 | +4.7% |
| verified_forward | 0.40 | fee_edge02 | legacy_no_current_yes_3expr | 112 | 12 | 33 | +73.2% | 76.12 | 5.88 | +7.7% | -3.7% | +19.8% | 0.670 | +4.8% |

## Verified Daily, ask>=0.40 fee_edge02

| expression_set | target_date | rows | cities | wins | win_rate | cost_net | pnl_net | roi_net | avg_ask | avg_fee_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bridge_no_current_yes_5expr | 2026-06-21 | 14 | 14 | 10 | +71.4% | 9.01 | 0.99 | +11.0% | 0.633 | +3.7% |
| bridge_no_current_yes_5expr | 2026-06-22 | 12 | 12 | 11 | +91.7% | 7.59 | 3.41 | +45.0% | 0.622 | +7.0% |
| bridge_no_current_yes_5expr | 2026-06-23 | 10 | 10 | 7 | +70.0% | 6.80 | 0.20 | +2.9% | 0.671 | +5.4% |
| bridge_no_current_yes_5expr | 2026-06-25 | 17 | 17 | 9 | +52.9% | 10.68 | -1.68 | -15.7% | 0.617 | +4.1% |
| bridge_no_current_yes_5expr | 2026-06-26 | 12 | 12 | 9 | +75.0% | 7.79 | 1.21 | +15.5% | 0.639 | +5.7% |
| bridge_no_current_yes_5expr | 2026-06-27 | 19 | 19 | 14 | +73.7% | 11.61 | 2.39 | +20.6% | 0.599 | +4.8% |
| bridge_no_current_yes_5expr | 2026-06-28 | 3 | 3 | 2 | +66.7% | 1.56 | 0.44 | +28.4% | 0.507 | +4.4% |
| bridge_no_current_yes_5expr | 2026-06-29 | 6 | 6 | 2 | +33.3% | 3.57 | -1.57 | -43.9% | 0.583 | +5.5% |
| bridge_no_current_yes_5expr | 2026-06-30 | 12 | 12 | 8 | +66.7% | 8.01 | -0.01 | -0.2% | 0.658 | +4.3% |
| bridge_no_current_yes_5expr | 2026-07-01 | 10 | 10 | 9 | +90.0% | 6.97 | 2.03 | +29.1% | 0.687 | +4.4% |
| bridge_no_current_yes_5expr | 2026-07-02 | 3 | 3 | 2 | +66.7% | 1.97 | 0.03 | +1.5% | 0.647 | +2.8% |
| bridge_no_current_yes_5expr | 2026-07-03 | 8 | 8 | 6 | +75.0% | 5.55 | 0.45 | +8.2% | 0.684 | +4.3% |
| legacy_4expr | 2026-06-21 | 14 | 14 | 10 | +71.4% | 9.95 | 0.05 | +0.5% | 0.701 | +3.5% |
| legacy_4expr | 2026-06-22 | 12 | 12 | 11 | +91.7% | 8.02 | 2.98 | +37.1% | 0.658 | +6.2% |
| legacy_4expr | 2026-06-23 | 12 | 12 | 9 | +75.0% | 8.07 | 0.93 | +11.5% | 0.662 | +5.6% |
| legacy_4expr | 2026-06-25 | 16 | 16 | 9 | +56.2% | 10.46 | -1.46 | -14.0% | 0.643 | +4.1% |
| legacy_4expr | 2026-06-26 | 14 | 14 | 10 | +71.4% | 9.14 | 0.86 | +9.4% | 0.643 | +5.5% |
| legacy_4expr | 2026-06-27 | 18 | 18 | 14 | +77.8% | 11.05 | 2.95 | +26.7% | 0.603 | +4.8% |
| legacy_4expr | 2026-06-28 | 4 | 4 | 2 | +50.0% | 2.46 | -0.46 | -18.7% | 0.604 | +4.8% |
| legacy_4expr | 2026-06-29 | 5 | 5 | 2 | +40.0% | 3.01 | -1.01 | -33.6% | 0.592 | +5.5% |
| legacy_4expr | 2026-06-30 | 10 | 10 | 6 | +60.0% | 6.90 | -0.90 | -13.0% | 0.680 | +4.2% |
| legacy_4expr | 2026-07-01 | 9 | 9 | 8 | +88.9% | 6.79 | 1.21 | +17.9% | 0.745 | +3.9% |
| legacy_4expr | 2026-07-02 | 3 | 3 | 3 | +100.0% | 2.36 | 0.64 | +26.9% | 0.780 | +2.7% |
| legacy_4expr | 2026-07-03 | 8 | 8 | 7 | +87.5% | 5.75 | 1.25 | +21.7% | 0.710 | +4.6% |

## Expression Contribution

| expression_set | expression | rows | dates | win_rate | cost_net | pnl_net | roi_net | avg_ask | avg_fee_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bridge_6expr | current_yes | 18 | 7 | +61.1% | 11.28 | -0.28 | -2.5% | 0.616 | +4.5% |
| bridge_6expr | d1_yes | 12 | 9 | +50.0% | 5.81 | 0.19 | +3.3% | 0.472 | +5.8% |
| bridge_6expr | current_no | 20 | 9 | +70.0% | 12.27 | 1.73 | +14.1% | 0.603 | +7.4% |
| bridge_6expr | d2_no | 44 | 12 | +77.3% | 31.92 | 2.08 | +6.5% | 0.716 | +4.1% |
| bridge_6expr | d2_yes | 13 | 8 | +69.2% | 6.68 | 2.32 | +34.8% | 0.502 | +3.6% |
| bridge_6expr | d1_no | 32 | 10 | +75.0% | 21.00 | 3.00 | +14.3% | 0.646 | +4.1% |
| bridge_no_current_yes_5expr | d1_yes | 12 | 9 | +50.0% | 5.81 | 0.19 | +3.3% | 0.472 | +5.8% |
| bridge_no_current_yes_5expr | d2_no | 45 | 12 | +75.6% | 32.76 | 1.24 | +3.8% | 0.719 | +4.1% |
| bridge_no_current_yes_5expr | current_no | 20 | 9 | +70.0% | 12.27 | 1.73 | +14.1% | 0.603 | +7.4% |
| bridge_no_current_yes_5expr | d2_yes | 13 | 8 | +69.2% | 6.68 | 2.32 | +34.8% | 0.502 | +3.6% |
| bridge_no_current_yes_5expr | d1_no | 36 | 10 | +72.2% | 23.58 | 2.42 | +10.3% | 0.645 | +4.3% |
| legacy_4expr | current_yes | 18 | 7 | +61.1% | 11.28 | -0.28 | -2.5% | 0.616 | +4.5% |
| legacy_4expr | d2_no | 49 | 12 | +75.5% | 35.41 | 1.59 | +4.5% | 0.713 | +4.2% |
| legacy_4expr | current_no | 23 | 10 | +73.9% | 14.35 | 2.65 | +18.5% | 0.613 | +6.9% |
| legacy_4expr | d1_no | 35 | 10 | +74.3% | 22.93 | 3.07 | +13.4% | 0.645 | +4.0% |

## Bridge vs Legacy Changed Rows

同一 city-day 上，bridge_no_current_yes_5expr 相对 legacy_4expr 的变化。负数表示 bridge 更差，正数表示 bridge 更好。

### Worst Deltas

| ask_floor | city | target_date | baseline_expression | bridge_expression | baseline_pnl_net | bridge_pnl_net | pnl_net_delta |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.40 | NYC | 2026-06-21 | d1_no | d2_yes | 0.28 | -0.60 | -0.88 |
| 0.40 | Denver | 2026-06-27 |  | d2_yes | 0.00 | -0.65 | -0.65 |
| 0.40 | Ankara | 2026-07-02 | current_no | d1_yes | 0.14 | -0.46 | -0.61 |
| 0.40 | Chongqing | 2026-06-26 | current_yes |  | 0.56 | 0.00 | -0.56 |
| 0.40 | Madrid | 2026-06-29 |  | d1_yes | 0.00 | -0.55 | -0.55 |
| 0.40 | Dallas | 2026-07-03 |  | d2_yes | 0.00 | -0.54 | -0.54 |
| 0.40 | Chengdu | 2026-06-25 |  | d1_yes | 0.00 | -0.48 | -0.48 |
| 0.40 | Wellington | 2026-06-23 | current_yes |  | 0.48 | 0.00 | -0.48 |
| 0.40 | Lucknow | 2026-06-25 |  | d1_yes | 0.00 | -0.42 | -0.42 |
| 0.40 | BuenosAires | 2026-06-21 | current_yes |  | 0.40 | 0.00 | -0.40 |
| 0.40 | Istanbul | 2026-06-25 | current_yes |  | 0.31 | 0.00 | -0.31 |
| 0.40 | Tokyo | 2026-06-28 | current_yes |  | 0.29 | 0.00 | -0.29 |

### Best Deltas

| ask_floor | city | target_date | baseline_expression | bridge_expression | baseline_pnl_net | bridge_pnl_net | pnl_net_delta |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.40 | Busan | 2026-06-21 | current_yes |  | -0.64 | 0.00 | 0.64 |
| 0.40 | Guangzhou | 2026-06-25 |  | d2_yes | 0.00 | 0.59 | 0.59 |
| 0.40 | Singapore | 2026-06-21 |  | d2_yes | 0.00 | 0.59 | 0.59 |
| 0.40 | Ankara | 2026-06-30 |  | d1_yes | 0.00 | 0.55 | 0.55 |
| 0.40 | Wuhan | 2026-06-28 |  | d2_yes | 0.00 | 0.55 | 0.55 |
| 0.40 | Dallas | 2026-06-25 | current_yes |  | -0.52 | 0.00 | 0.52 |
| 0.40 | Munich | 2026-06-21 |  | d1_yes | 0.00 | 0.51 | 0.51 |
| 0.40 | Tokyo | 2026-06-26 | current_yes |  | -0.48 | 0.00 | 0.48 |
| 0.40 | Beijing | 2026-07-01 |  | d2_yes | 0.00 | 0.46 | 0.46 |
| 0.40 | Guangzhou | 2026-06-28 | current_yes |  | -0.46 | 0.00 | 0.46 |
| 0.40 | Austin | 2026-06-26 |  | d2_yes | 0.00 | 0.41 | 0.41 |
| 0.40 | Dallas | 2026-06-22 |  | d1_yes | 0.00 | 0.41 | 0.41 |

## Later Signal Audit

这个表只回答 first-lock 后当天还有多少后续候选，不等于允许翻仓；真实 target-book reconciliation 还缺 close bid、market id 和持仓血缘。

| expression_set | scope | city_days | later_eligible_days | later_different_days | later_opposite_days |
| --- | --- | --- | --- | --- | --- |
| bridge_no_current_yes_5expr | verified_forward | 126 | 37 | 30 | 14 |
| legacy_4expr | verified_forward | 125 | 33 | 25 | 13 |

## Data Boundary

- `p5_opportunities`: `{'rows': 196992, 'min_date': '2026-06-02', 'max_date': '2026-07-03', 'scopes': {'dev_cv': 134748, 'verified_forward': 62244}}`
- `atlas`: `{'rows': 13860, 'min_date': '2026-05-19', 'max_date': '2026-07-04'}`
- `settlement_outcomes`: `{'rows': 28164, 'min_date': '2026-05-04', 'max_date': '2026-07-04'}`
- P5/P6 可评分分母当前到 2026-07-03；atlas 虽到 2026-07-04，但 tmax 四桶 label 对 7/04 仍有缺口，不能把 7/04 硬算进 verified。

## Verdict

conclusion=`shadow_bridge_complete_not_live`; target-book bridge v1 完成离线实验，但没有足够证据替换 legacy 四表达。下一步是 full ladder/hazard 概率层和实时 sibling book 落盘。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/expression_candidates.csv`
- `docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/selected_trades.csv`
- `docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/policy_summary.csv`
- `docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/daily.csv`
- `docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/expression_summary.csv`
- `docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/bridge_vs_legacy_diff.csv`
- `docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/later_signal_audit.csv`
- `docs/analysis/2026-07/2026-07-05-tmax-exact-book-bridge-v1.json`
