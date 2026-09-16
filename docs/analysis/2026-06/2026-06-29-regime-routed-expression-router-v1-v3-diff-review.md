# Regime-Routed Expression Router V1 vs V3 Diff Review

## 结论

在当前可结算分母 `2026-05-20`..`2026-06-26` 上，v1 是 284 笔，v3 是 283 笔，净少 1 笔。用户说的“差 30 笔”按当前文件精确拆出来是：12 笔 v1 stale-current-NO 被影响，其中 1 笔被移除，11 笔被改成 current-high YES。

这 12 笔如果按旧 v1 的 NO 买法，历史 PnL 是 $-14.35，ROI -23.9%。所以 v3 不是简单“去掉亏损单”：它移除了/改写了一组语义不干净的 stale/pullback 单，其中有些历史是赚钱的。

最关键的机制修复是 11 笔 `pullback_uncertain`：旧 v1 买 NO 的 PnL $-16.71、ROI -30.4%；v3 改 current-high YES 的 PnL $-1.05、ROI -1.9%。这一块方向合理，但样本太小且 YES size 缺，仍只能 shadow。

## Summary

| slice | rows | dates | cities | wins | win_rate | avg_ask | cost_usd | pnl_usd | roi | weighted_cost_usd | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_mixed_v1 | 284 | 38.0 | 35.0 | 149.0 | +52.5% | 0.512 | $+1420.00 | $+170.67 | +12.0% | $+492.75 | $+131.43 | +26.7% |
| router_v3 | 283 | 38.0 | 35.0 | 153.0 | +54.1% | 0.526 | $+1415.00 | $+183.98 | +13.0% | $+491.32 | $+127.20 | +25.9% |
| v1_rows_changed_by_v3_old_v1_result | 12 | 11.0 | 9.0 | 4.0 | +33.3% | 0.386 | $+60.00 | $-14.35 | -23.9% | $+24.13 | $+1.17 | +4.9% |
| removed_from_v3_old_v1_result | 1 | 1.0 | 1.0 | 1.0 | +100.0% | 0.680 | $+5.00 | $+2.35 | +47.1% | $+1.43 | $+0.67 | +47.1% |
| flipped_rows_old_v1_no_result | 11 | 11.0 | 8.0 | 3.0 | +27.3% | 0.359 | $+55.00 | $-16.71 | -30.4% | $+22.69 | $+0.50 | +2.2% |
| flipped_rows_new_v3_yes_result | 11 | 11.0 | 8.0 | 8.0 | +72.7% | 0.727 | $+55.00 | $-1.05 | -1.9% | $+22.69 | $-3.05 | -13.5% |
| net_router_v3_minus_v1 | -1 | nan | nan | nan | NA | NA | $-5.00 | $+13.31 | NA | $-1.43 | $-4.23 | NA |

## Affected State Breakdown

| diff_group | running_max_state | intraday_state | rows | dates | cities | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flipped_to_current_high_yes | pullback_from_high | pullback_uncertain | 11 | 11 | 8 | 3 | +27.3% | 0.359 | $-16.71 | -30.4% | $+0.50 | +2.2% |
| removed_from_v3 | near_high_plateau | fresh_high | 1 | 1 | 1 | 1 | +100.0% | 0.680 | $+2.35 | +47.1% | $+0.67 | +47.1% |

## Affected Daily Breakdown

| diff_group | target_date | rows | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flipped_to_current_high_yes | 2026-05-21 | 1 | 0 | +0.0% | 0.430 | $-5.00 | -100.0% | $-1.77 | -100.0% |
| flipped_to_current_high_yes | 2026-05-31 | 1 | 0 | +0.0% | 0.250 | $-5.00 | -100.0% | $-1.90 | -100.0% |
| flipped_to_current_high_yes | 2026-06-05 | 1 | 0 | +0.0% | 0.429 | $-5.00 | -100.0% | $-2.40 | -100.0% |
| flipped_to_current_high_yes | 2026-06-06 | 1 | 1 | +100.0% | 0.280 | $+12.86 | +257.1% | $+11.03 | +257.1% |
| flipped_to_current_high_yes | 2026-06-07 | 1 | 0 | +0.0% | 0.350 | $-5.00 | -100.0% | $-2.12 | -100.0% |
| flipped_to_current_high_yes | 2026-06-11 | 1 | 0 | +0.0% | 0.110 | $-5.00 | -100.0% | $-2.02 | -100.0% |
| flipped_to_current_high_yes | 2026-06-13 | 1 | 0 | +0.0% | 0.373 | $-5.00 | -100.0% | $-1.82 | -100.0% |
| flipped_to_current_high_yes | 2026-06-19 | 1 | 0 | +0.0% | 0.260 | $-5.00 | -100.0% | $-0.75 | -100.0% |
| removed_from_v3 | 2026-06-19 | 1 | 1 | +100.0% | 0.680 | $+2.35 | +47.1% | $+0.67 | +47.1% |
| flipped_to_current_high_yes | 2026-06-21 | 1 | 0 | +0.0% | 0.440 | $-5.00 | -100.0% | $-1.75 | -100.0% |
| flipped_to_current_high_yes | 2026-06-24 | 1 | 1 | +100.0% | 0.400 | $+7.50 | +150.0% | $+2.82 | +150.0% |
| flipped_to_current_high_yes | 2026-06-25 | 1 | 1 | +100.0% | 0.630 | $+2.94 | +58.7% | $+1.17 | +58.7% |

## Affected City Breakdown

| diff_group | city | rows | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flipped_to_current_high_yes | Helsinki | 2 | 0 | +0.0% | 0.401 | $-10.00 | -100.0% | $-4.22 | -100.0% |
| flipped_to_current_high_yes | TelAviv | 2 | 0 | +0.0% | 0.180 | $-10.00 | -100.0% | $-3.92 | -100.0% |
| flipped_to_current_high_yes | Busan | 1 | 0 | +0.0% | 0.260 | $-5.00 | -100.0% | $-0.75 | -100.0% |
| flipped_to_current_high_yes | NYC | 1 | 0 | +0.0% | 0.350 | $-5.00 | -100.0% | $-2.12 | -100.0% |
| flipped_to_current_high_yes | SaoPaulo | 1 | 0 | +0.0% | 0.430 | $-5.00 | -100.0% | $-1.77 | -100.0% |
| flipped_to_current_high_yes | Jeddah | 2 | 1 | +50.0% | 0.420 | $+2.50 | +25.0% | $+1.07 | +29.6% |
| flipped_to_current_high_yes | Shanghai | 1 | 1 | +100.0% | 0.630 | $+2.94 | +58.7% | $+1.17 | +58.7% |
| flipped_to_current_high_yes | Denver | 1 | 1 | +100.0% | 0.280 | $+12.86 | +257.1% | $+11.03 | +257.1% |
| removed_from_v3 | Lucknow | 1 | 1 | +100.0% | 0.680 | $+2.35 | +47.1% | $+0.67 | +47.1% |

## Recent Daily V1 vs V3

| strategy | target_date | rows | wins | win_rate | avg_ask | pnl_usd | roi | weighted_pnl_usd | weighted_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_mixed_v1 | 2026-06-21 | 9 | 3 | +33.3% | 0.540 | $-14.39 | -32.0% | $-2.35 | -16.6% |
| router_v3 | 2026-06-21 | 9 | 4 | +44.4% | 0.555 | $-5.62 | -12.5% | $+0.72 | +5.1% |
| original_mixed_v1 | 2026-06-22 | 3 | 0 | +0.0% | 0.509 | $-15.00 | -100.0% | $-1.68 | -100.0% |
| router_v3 | 2026-06-22 | 3 | 0 | +0.0% | 0.509 | $-15.00 | -100.0% | $-1.68 | -100.0% |
| original_mixed_v1 | 2026-06-23 | 6 | 3 | +50.0% | 0.552 | $+9.94 | +33.1% | $+6.55 | +66.8% |
| router_v3 | 2026-06-23 | 6 | 3 | +50.0% | 0.552 | $+9.94 | +33.1% | $+6.55 | +66.8% |
| original_mixed_v1 | 2026-06-24 | 3 | 2 | +66.7% | 0.554 | $+6.24 | +41.6% | $+3.37 | +72.4% |
| router_v3 | 2026-06-24 | 3 | 1 | +33.3% | 0.661 | $-6.26 | -41.7% | $-1.33 | -28.7% |
| original_mixed_v1 | 2026-06-25 | 5 | 4 | +80.0% | 0.574 | $+9.37 | +37.5% | $+2.54 | +38.2% |
| router_v3 | 2026-06-25 | 5 | 3 | +60.0% | 0.528 | $+1.43 | +5.7% | $-0.64 | -9.6% |
| original_mixed_v1 | 2026-06-26 | 5 | 3 | +60.0% | 0.532 | $-0.67 | -2.7% | $-0.49 | -4.9% |
| router_v3 | 2026-06-26 | 5 | 3 | +60.0% | 0.532 | $-0.67 | -2.7% | $-0.49 | -4.9% |

## Changed Rows

| diff_group | target_date | city | v1_expression | v1_ask | v1_payoff | v1_pnl_usd | v3_expression | v3_ask | v3_payoff | v3_pnl_usd | pnl_delta_v3_minus_v1 | running_max_state | intraday_state | minutes_since_running_max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flipped_to_current_high_yes | 2026-06-06 | Denver | current_bracket_no | 0.280 | 1.0 | $+12.86 | current_high_yes | 0.800 | 0.0 | $-5.00 | -17.857142857142854 | pullback_from_high | pullback_uncertain | 92.88333333333334 |
| flipped_to_current_high_yes | 2026-06-24 | Jeddah | current_bracket_no | 0.400 | 1.0 | $+7.50 | current_high_yes | 0.720 | 0.0 | $-5.00 | -12.5 | pullback_from_high | pullback_uncertain | 90.73333333333332 |
| flipped_to_current_high_yes | 2026-06-25 | Shanghai | current_bracket_no | 0.630 | 1.0 | $+2.94 | current_high_yes | 0.400 | 0.0 | $-5.00 | -7.936507936507937 | pullback_from_high | pullback_uncertain | 91.01666666666668 |
| removed_from_v3 | 2026-06-19 | Lucknow | current_bracket_no | 0.680 | 1.0 | $+2.35 | nan | NA | nan | NA | -2.352941176470588 | near_high_plateau | fresh_high | 58.61666666666667 |
| flipped_to_current_high_yes | 2026-05-31 | TelAviv | current_bracket_no | 0.250 | 0.0 | $-5.00 | current_high_yes | 0.900 | 1.0 | $+0.56 | 5.555555555555555 | pullback_from_high | pullback_uncertain | 100.88333333333334 |
| flipped_to_current_high_yes | 2026-06-11 | TelAviv | current_bracket_no | 0.110 | 0.0 | $-5.00 | current_high_yes | 0.900 | 1.0 | $+0.56 | 5.555555555555555 | pullback_from_high | pullback_uncertain | 40.88333333333333 |
| flipped_to_current_high_yes | 2026-06-13 | Helsinki | current_bracket_no | 0.373 | 0.0 | $-5.00 | current_high_yes | 0.893 | 1.0 | $+0.60 | 5.599104143337066 | pullback_from_high | pullback_uncertain | 70.88333333333334 |
| flipped_to_current_high_yes | 2026-06-19 | Busan | current_bracket_no | 0.260 | 0.0 | $-5.00 | current_high_yes | 0.760 | 1.0 | $+1.58 | 6.578947368421052 | pullback_from_high | pullback_uncertain | 91.0 |
| flipped_to_current_high_yes | 2026-05-21 | SaoPaulo | current_bracket_no | 0.430 | 0.0 | $-5.00 | current_high_yes | 0.700 | 1.0 | $+2.14 | 7.142857142857143 | pullback_from_high | pullback_uncertain | 90.88333333333334 |
| flipped_to_current_high_yes | 2026-06-07 | NYC | current_bracket_no | 0.350 | 0.0 | $-5.00 | current_high_yes | 0.679 | 1.0 | $+2.36 | 7.363770250368188 | pullback_from_high | pullback_uncertain | 99.88333333333334 |
| flipped_to_current_high_yes | 2026-06-05 | Helsinki | current_bracket_no | 0.429 | 0.0 | $-5.00 | current_high_yes | 0.677 | 1.0 | $+2.39 | 7.385524372230428 | pullback_from_high | pullback_uncertain | 100.88333333333334 |
| flipped_to_current_high_yes | 2026-06-21 | Jeddah | current_bracket_no | 0.440 | 0.0 | $-5.00 | current_high_yes | 0.570 | 1.0 | $+3.77 | 8.771929824561404 | pullback_from_high | pullback_uncertain | 90.88333333333334 |

## 机制判断

- `pullback_uncertain` 不该继续归到 current-bracket NO；更自然的表达是 current-high YES，但必须 shadow 补 capacity。
- `mature_fade/mature_fade` 已经不再被移除；新 v3 把它保留为 `cheap_stale_tail_current_no`，和 runway NO 分账。
- 被 v3 移除的 1 笔现在只剩 `plateau_near_high` 与 `running_max_clock_unknown`。前者历史表现偏弱，后者更像 clock/data freshness 诊断，不应塞回 runway 策略。
- v3 的收益变化来自 route 语义重排：pullback 改 YES、mature fade 拆成 tail NO、plateau/clock unknown 留作诊断，而不是简单收益筛选。

Verdict: `inconclusive_shadow_candidate`。v3 是更干净的表达路由，但不是 live-ready；后续应单独评估 `cheap_stale_tail_current_no`，并继续审计 removed 1 笔里的 clock/freshness 问题。
