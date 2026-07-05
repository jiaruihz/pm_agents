# Tmax Reverse Management v1

> generated_at_utc: `2026-07-05T08:44:43+00:00`
> source: `docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/opportunities.csv`

## 结论

- P6 主回测只选 city-day 第一笔；今天 live 的多笔 Lucknow 说明执行层没有按这个主口径做持仓血缘/去重。
- 在 P6/P5 可验证账本里，真正盘口平仓缺 bid path 和 market id，不能硬算；本报告只做同分母近似：第一笔、后续反向替换、反向腿叠加，以及 likely same-bracket 的平仓 proxy。
- 结论方向很清楚：后续反向信号不是免费增强。直接叠加双边通常恶化；same-bracket close 只是锁定点差损耗；是否反向开仓必须先解决持仓血缘和 same-market 判定，不能让 live runner 自己翻来翻去。

## Verified Forward Taker Summary

| ask_floor | policy | rows | dates | opposite_rows | same_bracket_proxy_rows | avg_first_ask | avg_reverse_ask | cost | pnl | roi | roi_ci_low | roi_ci_high | daily_win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.2 | add_first_opposite_hold_both | 192 | 12 | 19 | 7 | 0.576 | 0.643 | 124.96 | 11.04 | +8.8% | -0.3% | +17.7% | 75.0% |
| 0.2 | first_only_hold | 192 | 12 | 19 | 7 | 0.576 | 0.643 | 112.56 | 10.44 | +9.3% | +0.2% | +17.8% | 66.7% |
| 0.2 | replace_with_first_opposite_hold | 192 | 12 | 19 | 7 | 0.576 | 0.643 | 114.42 | 10.58 | +9.2% | -1.5% | +19.0% | 66.7% |
| 0.2 | same_bracket_close_only_proxy | 192 | 12 | 19 | 7 | 0.576 | 0.643 | 117.49 | 9.51 | +8.1% | -0.4% | +16.4% | 66.7% |
| 0.2 | same_bracket_close_plus_reverse_proxy | 192 | 12 | 19 | 7 | 0.576 | 0.643 | 122.43 | 10.57 | +8.6% | -0.1% | +16.8% | 66.7% |
| 0.4 | add_first_opposite_hold_both | 163 | 12 | 15 | 4 | 0.662 | 0.644 | 119.42 | 11.58 | +9.7% | +0.0% | +20.9% | 66.7% |
| 0.4 | first_only_hold | 163 | 12 | 15 | 4 | 0.662 | 0.644 | 109.60 | 10.40 | +9.5% | -0.5% | +21.8% | 58.3% |
| 0.4 | replace_with_first_opposite_hold | 163 | 12 | 15 | 4 | 0.662 | 0.644 | 109.50 | 12.50 | +11.4% | +0.1% | +24.5% | 66.7% |
| 0.4 | same_bracket_close_only_proxy | 163 | 12 | 15 | 4 | 0.662 | 0.644 | 112.25 | 10.75 | +9.6% | -0.8% | +21.8% | 66.7% |
| 0.4 | same_bracket_close_plus_reverse_proxy | 163 | 12 | 15 | 4 | 0.662 | 0.644 | 114.91 | 12.09 | +10.5% | +0.3% | +22.3% | 66.7% |

## Transition Summary

| ask_floor | transition | same_bracket_proxy | rows | dates | avg_first_ask | avg_reverse_ask | first_pnl | reverse_pnl | add_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.2 | d2_no->current_yes | False | 9 | 6 | 0.652 | 0.590 | 1.13 | 0.69 | 1.83 |
| 0.2 | d1_no->current_yes | True | 4 | 3 | 0.540 | 0.661 | -0.16 | 1.36 | 1.20 |
| 0.2 | current_no->current_yes | True | 2 | 2 | 0.440 | 0.755 | 0.12 | -0.51 | -0.39 |
| 0.2 | current_yes->d2_no | False | 2 | 2 | 0.463 | 0.865 | 0.07 | -0.73 | -0.66 |
| 0.2 | current_yes->current_no | True | 1 | 1 | 0.260 | 0.710 | -0.26 | 0.29 | 0.03 |
| 0.2 | current_yes->d1_no | False | 1 | 1 | 0.240 | 0.310 | -0.24 | -0.31 | -0.55 |
| 0.4 | d2_no->current_yes | False | 10 | 7 | 0.675 | 0.621 | 1.24 | 0.79 | 2.03 |
| 0.4 | d1_no->current_yes | True | 3 | 3 | 0.600 | 0.607 | -0.80 | 1.18 | 0.38 |
| 0.4 | current_no->current_yes | True | 1 | 1 | 0.630 | 0.790 | -0.63 | 0.21 | -0.42 |
| 0.4 | current_yes->d2_no | False | 1 | 1 | 0.576 | 0.840 | -0.58 | -0.84 | -1.42 |

## Definitions

- `first_only_hold`: 每个 city-day 第一条 clean_edge02 edge-pass，持有到结算。
- `replace_with_first_opposite_hold`: 如果后续同城同日 best expression 反向，则用后续反向腿替换第一腿，持有到结算；这是“相信新模型判断”的反事实，不是实盘平仓价格。
- `add_first_opposite_hold_both`: 第一腿不动，再加第一条反向腿，双边都持有到结算；这近似今天这种矛盾持仓的风险形态。
- `same_bracket_close_only_proxy`: 仅对 likely same-bracket 反向，按 `1 - old_ask - new_ask` 锁平；其他行沿用第一腿。
- `same_bracket_close_plus_reverse_proxy`: likely same-bracket 时买两份反向 token：一份锁平旧仓，一份作为新反向仓。
- `taker`: 买入腿按 Polymarket weather fee `0.05 * price * (1-price)` 扣费。

## Caveat

P5/P6 没有 `market_id/token_id/bid path/position id`。所以本报告不能替代真实 executor replay；它回答的是“后续反向信号在历史上是否值得相信”，不是精确账户平仓收益。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_reverse_management_v1/reverse_decisions.csv`
- `docs/analysis/2026-07/generated/tmax_reverse_management_v1/reverse_policy_summary.csv`
- `docs/analysis/2026-07/generated/tmax_reverse_management_v1/reverse_transition_summary.csv`
- `docs/analysis/2026-07/2026-07-05-tmax-reverse-management-v1.json`
