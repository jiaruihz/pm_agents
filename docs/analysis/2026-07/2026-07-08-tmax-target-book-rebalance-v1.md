# Tmax Target-Book Rebalance v1

> generated_at_utc: `2026-07-08T12:30:10+00:00`
> Scope: research/shadow only. No live runner/config/order behavior changed.

## 结论 / 交易动作

- 这版把策略从“每小时 posterior 生成新单”改成“city-day 级目标持仓账本”。核心机制是先重估已有仓位，再决定是否用同 bracket 反向 token 锁平，最后才考虑重新开新目标。
- 中间成本不是拍参数：开仓成本=`ask + 0.05*ask*(1-ask)`；锁平成本=买同 bracket complement 的 `ask + fee`；换仓只在 `1 - close_cost + (p_new - new_cost) - p_old > buffer` 时发生。
- Primary 建议不是 follow-latest，而是 `first_lock_no_current_yes` 作为 live 前默认；`target_book_rebalance_ev0` 只做 shadow。原因是 verified 点估略好/接近，但 CI 跨 0，且 current data layer 仍没有真实 position ids / fresh bid path。
- Lucknow 7/05 在这个体系下：主动表达集不含 `current_yes`，所以 12:17 的 36 YES 不会作为新主动目标；已有 36 NO 只会被重估。即使允许 current_yes，成本账本也会先走“平/翻仓成本”计算，而不是直接叠加反向单。

## 数据快照

- Source candidates: `docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/expression_candidates.csv`
- Candidate rows: `32832`; best state rows after ask/fee edge: `754`.
- Date range: `2026-06-02`..`2026-07-03`; scopes: `{'dev_cv': 22458, 'verified_forward': 10374}`.
- This is P5/P6 materialized evidence, not fresh current-day live replay. It cannot replace executor-level fill sync.

## 成本机制

For one active old leg at a later hour:

```text
old_hold_value = p_old
close_value = 1 - close_ask - close_fee
new_value = p_new - new_ask - new_fee

close_only_incremental_EV = close_value - old_hold_value
rebalance_incremental_EV = close_value + new_value - old_hold_value
execute iff incremental_EV > buffer
```

`buffer=0` is the clean theoretical cost gate because ask/spread/fee are already paid. `buffer=0.02` is only a robustness sensitivity for stale-book/adverse-selection; it is not used as primary proof.

## Verified Forward Summary

| policy | rows | dates | cities | closes | reopens | cost_net | pnl_net | roi_net | roi_net_ci_low | roi_net_ci_high | daily_positive | daily_negative |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_lock_no_current_yes | 126 | 12 | 35 | 0 | 0 | 81.10 | 7.90 | +9.7% | -1.5% | +21.4% | 9 | 3 |
| target_book_close_only_ev0 | 126 | 12 | 35 | 11 | 0 | 88.10 | 6.90 | +7.8% | -2.6% | +19.8% | 8 | 4 |
| target_book_rebalance_ev0 | 126 | 12 | 35 | 22 | 22 | 105.98 | 5.02 | +4.7% | -4.0% | +14.9% | 8 | 4 |
| target_book_rebalance_ev02 | 126 | 12 | 35 | 17 | 17 | 100.39 | 5.61 | +5.6% | -3.4% | +16.4% | 8 | 4 |

## Lucknow 2026-07-05 Counterfactual

This date is not in the P5 materialized denominator, so it is a case-level replay from the live review facts, not part of the aggregate ROI table.

- Actual bug path: three repeated `36 NO` buys, then one opposite `36 YES` buy.
- New primary policy path: buy the first eligible `36 NO` once; do not open active `current_yes`; later signals only revalue the existing `36 NO`.
- Per-share clean cost for the first `36 NO @0.65`: `0.65 + 0.05*0.65*(1-0.65) = 0.6614`; final reached 37, so 36 NO wins, net `+0.3386/share` before share multiplier.
- Important stress check: if `current_yes` were allowed, the 12:17 posterior could still justify flipping under the cost formula because `p_yes≈0.586`, `yes_ask≈0.48`, and the model overvalued stop-at-36. That is why cost-aware ledger is necessary but not sufficient; `current_yes` stays shadow until E2/five-bucket target is fixed.

## Dev CV Summary

| policy | rows | dates | cities | closes | reopens | cost_net | pnl_net | roi_net | roi_net_ci_low | roi_net_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_lock_no_current_yes | 373 | 19 | 36 | 0 | 0 | 240.47 | 10.53 | +4.4% | +0.2% | +8.8% |
| target_book_close_only_ev0 | 373 | 19 | 36 | 33 | 0 | 263.05 | -0.05 | -0.0% | -4.3% | +4.6% |
| target_book_rebalance_ev0 | 373 | 19 | 36 | 66 | 66 | 310.55 | 5.45 | +1.8% | -2.1% | +5.9% |
| target_book_rebalance_ev02 | 373 | 19 | 36 | 57 | 57 | 300.94 | 6.06 | +2.0% | -2.3% | +6.5% |

## Verified Daily: First Lock vs Target Book

| policy | target_date | rows | cities | closes | reopens | cost_net | pnl_net | roi_net |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_lock_no_current_yes | 2026-06-21 | 14 | 14 | 0 | 0 | 9.01 | 0.99 | +11.0% |
| target_book_rebalance_ev0 | 2026-06-21 | 14 | 14 | 1 | 1 | 9.97 | 1.03 | +10.3% |
| first_lock_no_current_yes | 2026-06-22 | 12 | 12 | 0 | 0 | 7.59 | 3.41 | +45.0% |
| target_book_rebalance_ev0 | 2026-06-22 | 12 | 12 | 0 | 0 | 7.59 | 3.41 | +45.0% |
| first_lock_no_current_yes | 2026-06-23 | 10 | 10 | 0 | 0 | 6.80 | 0.20 | +2.9% |
| target_book_rebalance_ev0 | 2026-06-23 | 10 | 10 | 0 | 0 | 6.80 | 0.20 | +2.9% |
| first_lock_no_current_yes | 2026-06-25 | 17 | 17 | 0 | 0 | 10.68 | -1.68 | -15.7% |
| target_book_rebalance_ev0 | 2026-06-25 | 17 | 17 | 4 | 4 | 14.55 | -1.55 | -10.6% |
| first_lock_no_current_yes | 2026-06-26 | 12 | 12 | 0 | 0 | 7.79 | 1.21 | +15.5% |
| target_book_rebalance_ev0 | 2026-06-26 | 12 | 12 | 2 | 2 | 10.23 | 0.77 | +7.6% |
| first_lock_no_current_yes | 2026-06-27 | 19 | 19 | 0 | 0 | 11.61 | 2.39 | +20.6% |
| target_book_rebalance_ev0 | 2026-06-27 | 19 | 19 | 5 | 5 | 17.97 | 1.03 | +5.7% |
| first_lock_no_current_yes | 2026-06-28 | 3 | 3 | 0 | 0 | 1.56 | 0.44 | +28.4% |
| target_book_rebalance_ev0 | 2026-06-28 | 3 | 3 | 1 | 1 | 2.85 | 0.15 | +5.3% |
| first_lock_no_current_yes | 2026-06-29 | 6 | 6 | 0 | 0 | 3.57 | -1.57 | -43.9% |
| target_book_rebalance_ev0 | 2026-06-29 | 6 | 6 | 1 | 1 | 4.37 | -1.37 | -31.4% |
| first_lock_no_current_yes | 2026-06-30 | 12 | 12 | 0 | 0 | 8.01 | -0.01 | -0.2% |
| target_book_rebalance_ev0 | 2026-06-30 | 12 | 12 | 1 | 1 | 9.73 | -0.73 | -7.5% |
| first_lock_no_current_yes | 2026-07-01 | 10 | 10 | 0 | 0 | 6.97 | 2.03 | +29.1% |
| target_book_rebalance_ev0 | 2026-07-01 | 10 | 10 | 2 | 2 | 8.89 | 2.11 | +23.8% |
| first_lock_no_current_yes | 2026-07-02 | 3 | 3 | 0 | 0 | 1.97 | 0.03 | +1.5% |
| target_book_rebalance_ev0 | 2026-07-02 | 3 | 3 | 1 | 1 | 2.92 | 1.08 | +36.9% |
| first_lock_no_current_yes | 2026-07-03 | 8 | 8 | 0 | 0 | 5.55 | 0.45 | +8.2% |
| target_book_rebalance_ev0 | 2026-07-03 | 8 | 8 | 4 | 4 | 10.12 | -1.12 | -11.1% |

## Verified Expression Contribution

| policy | final_expression | rows | dates | win_rate | cost_net | pnl_net | roi_net |
| --- | --- | --- | --- | --- | --- | --- | --- |
| first_lock_no_current_yes | d1_yes | 12 | 9 | +50.0% | 5.81 | 0.19 | +3.3% |
| first_lock_no_current_yes | d2_no | 45 | 12 | +75.6% | 32.76 | 1.24 | +3.8% |
| first_lock_no_current_yes | current_no | 20 | 9 | +70.0% | 12.27 | 1.73 | +14.1% |
| first_lock_no_current_yes | d2_yes | 13 | 8 | +69.2% | 6.68 | 2.32 | +34.8% |
| first_lock_no_current_yes | d1_no | 36 | 10 | +72.2% | 23.58 | 2.42 | +10.3% |
| target_book_rebalance_ev0 | d1_yes | 12 | 8 | +50.0% | 10.22 | -1.22 | -12.0% |
| target_book_rebalance_ev0 | d2_yes | 13 | 7 | +53.8% | 8.98 | 0.02 | +0.2% |
| target_book_rebalance_ev0 | d1_no | 42 | 11 | +73.8% | 39.29 | 1.71 | +4.4% |
| target_book_rebalance_ev0 | d2_no | 33 | 10 | +78.8% | 24.19 | 1.81 | +7.5% |
| target_book_rebalance_ev0 | current_no | 26 | 11 | +73.1% | 23.29 | 2.71 | +11.6% |

## Target-Book Delta vs First Lock

Worst and best deltas are city-day level. Positive means target-book improved over first-lock.

### Worst Deltas

| city | target_date | first_expression | target_book_final_expression | first_pnl_net | target_book_pnl_net | pnl_net_delta | closes | reopens | last_action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Denver | 2026-07-03 | d1_no | d1_yes | 0.41 | -0.76 | -1.16 | 1 | 1 | close_and_reopen |
| Seattle | 2026-06-27 | d2_no | d2_yes | 0.35 | -0.68 | -1.02 | 1 | 1 | close_and_reopen |
| Madrid | 2026-06-30 | d2_yes | d1_yes | 0.34 | -0.37 | -0.71 | 1 | 1 | close_and_reopen |
| BuenosAires | 2026-06-26 | d2_no | d1_no | 0.15 | -0.31 | -0.46 | 1 | 1 | close_and_reopen |
| Atlanta | 2026-07-03 | d2_no | d1_no | 0.25 | -0.19 | -0.44 | 1 | 1 | close_and_reopen |
| Beijing | 2026-06-25 | d1_no | current_no | 0.38 | 0.03 | -0.35 | 1 | 1 | close_and_reopen |
| CapeTown | 2026-06-27 | d2_yes | current_no | 0.57 | 0.24 | -0.33 | 1 | 1 | close_and_reopen |
| Singapore | 2026-06-28 | d2_no | d1_no | -0.56 | -0.86 | -0.29 | 1 | 1 | close_and_reopen |
| Singapore | 2026-06-27 | d2_no | d1_no | 0.35 | 0.14 | -0.20 | 1 | 1 | close_and_reopen |
| CapeTown | 2026-07-03 | d2_no | d1_no | 0.26 | 0.13 | -0.13 | 1 | 1 | close_and_reopen |
| Wellington | 2026-06-27 | d1_yes | d1_no | 0.56 | 0.47 | -0.08 | 1 | 1 | hold_same_expression |
| Madrid | 2026-07-01 | d1_no | current_no | 0.10 | 0.03 | -0.08 | 1 | 1 | close_and_reopen |

### Best Deltas

| city | target_date | first_expression | target_book_final_expression | first_pnl_net | target_book_pnl_net | pnl_net_delta | closes | reopens | last_action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ankara | 2026-07-02 | d1_yes | current_no | -0.46 | 0.59 | 1.05 | 1 | 1 | close_and_reopen |
| Tokyo | 2026-06-27 | current_no | d1_no | -0.64 | -0.36 | 0.28 | 1 | 1 | close_and_reopen |
| BuenosAires | 2026-06-25 | d2_no | d1_no | -0.54 | -0.28 | 0.26 | 1 | 1 | close_and_reopen |
| Manila | 2026-06-25 | d2_no | d1_no | 0.32 | 0.55 | 0.23 | 1 | 1 | close_and_reopen |
| NYC | 2026-06-29 | d2_no | d1_yes | 0.13 | 0.33 | 0.19 | 1 | 1 | close_and_reopen |
| Ankara | 2026-07-03 | d2_no | current_no | -0.81 | -0.64 | 0.17 | 1 | 1 | close_and_reopen |
| Istanbul | 2026-07-01 | d2_no | d1_no | 0.29 | 0.45 | 0.17 | 1 | 1 | close_and_reopen |
| Denver | 2026-06-21 | d2_no | d2_yes | -0.76 | -0.72 | 0.04 | 1 | 1 | hold_rebalance_not_worth_cost |
| NYC | 2026-06-26 | d1_no | current_no | -0.43 | -0.41 | 0.03 | 1 | 1 | close_and_reopen |
| Warsaw | 2026-06-27 | d2_no | d2_no | 0.19 | 0.19 | 0.00 | 0 | 0 | open |
| SanFrancisco | 2026-06-27 | d2_no | d2_no | 0.23 | 0.23 | 0.00 | 0 | 0 | open |
| SaoPaulo | 2026-06-27 | d1_no | d1_no | 0.41 | 0.41 | 0.00 | 0 | 0 | open |

## Rebalance Action Samples

| city | target_date | hour | action | active_before | desired_expression | close_expr | cost_delta | old_p | new_p | close_incremental_ev | rebalance_incremental_ev |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ankara | 2026-07-02 | 14.000 | close | d1_yes | current_no | d1_no | 0.280 | 0.640 | 0.835 | 0.080 | 0.243 |
| Ankara | 2026-07-02 | 14.000 | reopen |  | current_no |  | 0.671 | n/a | 0.835 | n/a | 0.243 |
| Ankara | 2026-07-03 | 15.000 | close | d2_no | current_no | d2_yes | 0.073 | 0.954 | 0.795 | -0.027 | 0.009 |
| Ankara | 2026-07-03 | 15.000 | reopen |  | current_no |  | 0.759 | n/a | 0.795 | n/a | 0.009 |
| Atlanta | 2026-07-03 | 12.000 | close | d2_no | d1_no | d2_yes | 0.652 | 0.357 | 0.822 | -0.009 | 0.025 |
| Atlanta | 2026-07-03 | 12.000 | reopen |  | d1_no |  | 0.789 | n/a | 0.822 | n/a | 0.025 |
| Beijing | 2026-06-25 | 14.000 | close | d1_no | current_no | d1_yes | 0.671 | 0.326 | 0.722 | 0.003 | 0.043 |
| Beijing | 2026-06-25 | 14.000 | reopen |  | current_no |  | 0.681 | n/a | 0.722 | n/a | 0.043 |
| BuenosAires | 2026-06-25 | 12.000 | close | d2_no | d1_no | d2_yes | 0.290 | 0.709 | 0.493 | 0.000 | 0.041 |
| BuenosAires | 2026-06-25 | 12.000 | reopen |  | d1_no |  | 0.452 | n/a | 0.493 | n/a | 0.041 |
| BuenosAires | 2026-06-26 | 12.000 | close | d2_no | d1_no | d2_yes | 0.622 | 0.381 | 0.870 | -0.003 | 0.030 |
| BuenosAires | 2026-06-26 | 12.000 | reopen |  | d1_no |  | 0.837 | n/a | 0.870 | n/a | 0.030 |
| CapeTown | 2026-06-27 | 13.000 | close | d2_yes | current_no | d2_no | 0.904 | 0.108 | 0.467 | -0.012 | 0.033 |
| CapeTown | 2026-06-27 | 13.000 | reopen |  | current_no |  | 0.422 | n/a | 0.467 | n/a | 0.033 |
| CapeTown | 2026-07-03 | 12.000 | close | d2_no | d1_no | d2_yes | 0.542 | 0.488 | 0.640 | -0.030 | 0.017 |
| CapeTown | 2026-07-03 | 12.000 | reopen |  | d1_no |  | 0.592 | n/a | 0.640 | n/a | 0.017 |
| Denver | 2026-06-21 | 12.000 | close | d2_no | d2_yes | d2_yes | 0.482 | 0.486 | 0.514 | 0.032 | 0.063 |
| Denver | 2026-06-21 | 12.000 | reopen |  | d2_yes |  | 0.482 | n/a | 0.514 | n/a | 0.063 |
| Denver | 2026-07-03 | 15.000 | close | d1_no | d1_yes | d1_yes | 0.582 | 0.359 | 0.641 | 0.059 | 0.118 |
| Denver | 2026-07-03 | 15.000 | reopen |  | d1_yes |  | 0.582 | n/a | 0.641 | n/a | 0.118 |
| Istanbul | 2026-07-01 | 12.000 | close | d2_no | d1_no | d2_yes | 0.037 | 0.979 | 0.824 | -0.016 | 0.010 |
| Istanbul | 2026-07-01 | 12.000 | reopen |  | d1_no |  | 0.798 | n/a | 0.824 | n/a | 0.010 |
| Madrid | 2026-06-25 | 17.000 | close | d1_yes | current_no | d1_no | 0.270 | 0.691 | 0.761 | 0.039 | 0.061 |
| Madrid | 2026-06-25 | 17.000 | reopen |  | current_no |  | 0.740 | n/a | 0.761 | n/a | 0.061 |
| Madrid | 2026-06-30 | 16.000 | close | d2_yes | d1_yes | d2_no | 0.933 | 0.076 | 0.805 | -0.010 | 0.017 |
| Madrid | 2026-06-30 | 16.000 | reopen |  | d1_yes |  | 0.779 | n/a | 0.805 | n/a | 0.017 |
| Madrid | 2026-07-01 | 15.000 | close | d1_no | current_no | d1_yes | 0.208 | 0.809 | 0.905 | -0.017 | 0.018 |
| Madrid | 2026-07-01 | 15.000 | reopen |  | current_no |  | 0.870 | n/a | 0.905 | n/a | 0.018 |
| Manila | 2026-06-25 | 12.000 | close | d2_no | d1_no | d2_yes | 0.039 | 0.985 | 0.778 | -0.024 | 0.024 |
| Manila | 2026-06-25 | 12.000 | reopen |  | d1_no |  | 0.730 | n/a | 0.778 | n/a | 0.024 |

## 三道门

significance=FAIL/PARTIAL because verified point estimates are positive but policy deltas are not yet proven with executor-grade bid/fill data; baseline=PARTIAL because first-lock remains competitive and simpler; forward=FAIL because this is materialized P5/P6, not fresh live-shadow target-book ledger.

conclusion=`shadow_candidate_target_book_not_live`.

## Artifacts

- `docs/analysis/2026-07/generated/tmax_target_book_rebalance_v1/target_book_decisions.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_rebalance_v1/target_book_actions.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_rebalance_v1/policy_summary.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_rebalance_v1/daily_summary.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_rebalance_v1/expression_summary.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_rebalance_v1/delta_vs_first_lock.csv`
- `docs/analysis/2026-07/2026-07-08-tmax-target-book-rebalance-v1.json`
