# Higher NO Carry Expression Selector v1

Status: snapshot
Generated: 2026-06-16T16:56:57.725991+00:00

Target metric: `higher_no_carry_expression_delta` = same city-hour/orderbook state 下，比较 current YES、d1 NO、d2 NO、两腿 NO ladder 的 ROI 和相对 sibling current YES 的 excess ROI。
Row grain: one paired state = `orderbook_file + city + target_date + decision_hour_local + current_bracket`; d1/d2 NO quote 必须和 current YES 来自同一 orderbook file/window。

## 数据快照

- 数据源: `runtime/weather.db` mandatory self-check + `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv` shared feature factory.
- 本报告没有重新 materialize observed max/orderbook/settlement；同窗 current YES、d1/d2 NO、settlement labels 均来自 shared factory v1。
- sync/run_stack: 本次未重跑；使用当前本地 DB/factory snapshot。
- fact_built_at_utc: `2026-06-16T15:50:05.971834+00:00`.
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`.
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 90}, {'settlement_status': 'settled', 'rows': 4310}]`.
- fact_signal_candidates coverage: `{'rows': 30919, 'eligible': 10685, 'paper_ordered': 4123, 'live_filled': 348}`.
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`.
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.
- Paired state rows: current+d1 `5093`, current+d1+d2 `4210`, active dates `27` (2026-05-19..2026-06-14).

## 选择逻辑

不要把 higher NO carry 当独立 alpha。它只是在同一个 no-reheat thesis 下换 payoff：current YES 只在当前档最终命中时赢；d1 NO 输在 d1-hit，但在 current-hit、d2-hit、skip-over 时赢；d2 NO 输在 d2-hit，但在 current-hit、d1-hit、skip-over 时赢；ladder 用更高成本买更宽的 current-hit/skip-over convexity。

当前数据给出的交易规则很朴素：默认先看 current YES；只有当同窗 d1/d2 NO 的 holdout excess ROI 相对 current YES 也过门，才允许说 NO carry/ladder 是更好表达。这里还没有过门，所以表达选择只能保留为 shadow/research，不改 live。

## Decision Table

| decision | when | holdout evidence | action |
|---|---|---|---|
| current YES | Visible fade and current YES quote is still not too rich. | rows 956 / dates 13; ROI +0.2%; current YES +0.2%; excess +0.0% CI [+0.0%, +0.0%] | `choose_current_yes_over_NO_carry_when_current_yes_gate_passes` |
| d1 NO | Adjacent higher NO is expensive enough to pay for exact d1-hit risk. | rows 965 / dates 13; ROI -0.5%; current YES -0.4%; excess -0.1% CI [-0.5%, +0.2%] | `do_not_choose_over_current_yes_yet` |
| d2 NO | Tail NO is visible and expensive while d2 exact-hit risk stays modest. | rows 712 / dates 13; ROI -0.5%; current YES +0.2%; excess -0.8% CI [-2.9%, +1.3%] | `do_not_choose_over_current_yes_yet` |
| ladder | Both d1 and d2 NO are visible; ladder buys broader no-reheat/overshoot payoff. | rows 669 / dates 13; ROI -0.2%; current YES -0.1%; excess -0.1% CI [-1.0%, +0.8%] | `do_not_choose_over_current_yes_yet` |
| skip | Default outside a supported expression segment. | rows 2619 / dates 14; ROI -2.6%; current YES -2.6%; excess +0.0% CI [+0.0%, +0.0%] | `skip` |

## Expression ROI vs Current YES

| profile | period | expression | rows | dates | avg cost | ROI | excess vs current YES | CI95 | current-hit | d1-hit | d2-hit | skip-over |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `all_current_d1_h13_17` | holdout | `current_yes` | 2619 | 14 | 0.730 | -2.6% | +0.0% | [+0.0%, +0.0%] | 71.1% | 18.8% | 7.4% | 2.6% |
| `all_current_d1_h13_17` | holdout | `d1_no` | 2701 | 14 | 0.820 | -2.0% | +0.5% | [-1.5%, +2.6%] | 69.8% | 19.7% | 7.6% | 3.0% |
| `all_current_d1_h13_17` | holdout | `d2_no` | 2257 | 14 | 0.926 | -1.7% | +1.3% | [-2.1%, +4.4%] | 65.1% | 22.4% | 9.0% | 3.5% |
| `all_current_d1_h13_17` | holdout | `no_ladder` | 2257 | 14 | 1.721 | -2.0% | +1.0% | [-1.5%, +3.5%] | 65.1% | 22.4% | 9.0% | 3.5% |
| `all_current_d1_h13_17` | all | `current_yes` | 4955 | 27 | 0.720 | -2.5% | +0.0% | [+0.0%, +0.0%] | 70.3% | 19.1% | 8.0% | 2.7% |
| `all_current_d1_h13_17` | all | `d1_no` | 5093 | 27 | 0.820 | -2.1% | +0.4% | [-0.8%, +1.7%] | 69.1% | 19.7% | 8.1% | 3.2% |
| `all_current_d1_h13_17` | all | `d2_no` | 4210 | 27 | 0.924 | -2.3% | +0.8% | [-1.3%, +2.9%] | 64.3% | 22.3% | 9.7% | 3.7% |
| `all_current_d1_h13_17` | all | `no_ladder` | 4210 | 27 | 1.721 | -2.4% | +0.7% | [-0.9%, +2.3%] | 64.3% | 22.3% | 9.7% | 3.7% |
| `current_d1_d2_visible` | holdout | `current_yes` | 2200 | 14 | 0.687 | -3.0% | +0.0% | [+0.0%, +0.0%] | 66.7% | 21.4% | 8.8% | 3.1% |
| `current_d1_d2_visible` | holdout | `d1_no` | 2257 | 14 | 0.795 | -2.3% | +0.6% | [-1.8%, +3.1%] | 65.1% | 22.4% | 9.0% | 3.5% |
| `current_d1_d2_visible` | holdout | `d2_no` | 2257 | 14 | 0.926 | -1.7% | +1.3% | [-2.1%, +4.4%] | 65.1% | 22.4% | 9.0% | 3.5% |
| `current_d1_d2_visible` | holdout | `no_ladder` | 2257 | 14 | 1.721 | -2.0% | +1.0% | [-1.5%, +3.5%] | 65.1% | 22.4% | 9.0% | 3.5% |
| `current_d1_d2_visible` | all | `current_yes` | 4114 | 27 | 0.677 | -3.0% | +0.0% | [+0.0%, +0.0%] | 65.6% | 21.5% | 9.6% | 3.3% |
| `current_d1_d2_visible` | all | `d1_no` | 4210 | 27 | 0.797 | -2.4% | +0.6% | [-1.0%, +2.2%] | 64.3% | 22.3% | 9.7% | 3.7% |
| `current_d1_d2_visible` | all | `d2_no` | 4210 | 27 | 0.924 | -2.3% | +0.8% | [-1.3%, +2.9%] | 64.3% | 22.3% | 9.7% | 3.7% |
| `current_d1_d2_visible` | all | `no_ladder` | 4210 | 27 | 1.721 | -2.4% | +0.7% | [-0.9%, +2.3%] | 64.3% | 22.3% | 9.7% | 3.7% |
| `fade_high_current_yes` | holdout | `current_yes` | 956 | 13 | 0.964 | +0.2% | +0.0% | [+0.0%, +0.0%] | 96.5% | 3.2% | 0.2% | 0.0% |
| `fade_high_current_yes` | holdout | `d1_no` | 956 | 13 | 0.968 | -0.1% | -0.3% | [-0.6%, +0.1%] | 96.5% | 3.2% | 0.2% | 0.0% |
| `fade_high_current_yes` | holdout | `d2_no` | 672 | 13 | 0.996 | +0.1% | -0.2% | [-2.4%, +1.7%] | 95.7% | 4.0% | 0.3% | 0.0% |
| `fade_high_current_yes` | holdout | `no_ladder` | 672 | 13 | 1.955 | +0.1% | -0.3% | [-1.4%, +0.7%] | 95.7% | 4.0% | 0.3% | 0.0% |
| `fade_high_current_yes` | all | `current_yes` | 1745 | 26 | 0.963 | -0.1% | +0.0% | [+0.0%, +0.0%] | 96.3% | 3.5% | 0.2% | 0.0% |
| `fade_high_current_yes` | all | `d1_no` | 1745 | 26 | 0.969 | -0.4% | -0.3% | [-0.6%, +0.0%] | 96.3% | 3.5% | 0.2% | 0.0% |
| `fade_high_current_yes` | all | `d2_no` | 1204 | 26 | 0.995 | +0.2% | +0.1% | [-1.3%, +1.5%] | 95.3% | 4.4% | 0.3% | 0.0% |
| `fade_high_current_yes` | all | `no_ladder` | 1204 | 26 | 1.954 | -0.1% | -0.2% | [-0.9%, +0.5%] | 95.3% | 4.4% | 0.3% | 0.0% |
| `d1_high_carry` | holdout | `current_yes` | 943 | 13 | 0.963 | -0.4% | +0.0% | [+0.0%, +0.0%] | 96.0% | 3.1% | 0.5% | 0.4% |
| `d1_high_carry` | holdout | `d1_no` | 965 | 13 | 0.975 | -0.5% | -0.1% | [-0.5%, +0.2%] | 95.6% | 3.0% | 0.5% | 0.8% |
| `d1_high_carry` | holdout | `d2_no` | 664 | 13 | 0.992 | +0.0% | +0.2% | [-1.4%, +1.9%] | 94.6% | 3.8% | 0.8% | 0.9% |
| `d1_high_carry` | holdout | `no_ladder` | 664 | 13 | 1.959 | -0.2% | +0.0% | [-0.8%, +0.9%] | 94.6% | 3.8% | 0.8% | 0.9% |
| `d1_high_carry` | all | `current_yes` | 1723 | 26 | 0.962 | -0.1% | +0.0% | [+0.0%, +0.0%] | 96.2% | 3.0% | 0.5% | 0.3% |
| `d1_high_carry` | all | `d1_no` | 1761 | 26 | 0.975 | -0.5% | -0.4% | [-1.0%, +0.1%] | 95.9% | 3.0% | 0.5% | 0.7% |
| `d1_high_carry` | all | `d2_no` | 1191 | 26 | 0.992 | +0.1% | +0.1% | [-1.0%, +1.3%] | 94.8% | 3.9% | 0.7% | 0.7% |
| `d1_high_carry` | all | `no_ladder` | 1191 | 26 | 1.959 | -0.2% | -0.3% | [-1.0%, +0.5%] | 94.8% | 3.9% | 0.7% | 0.7% |
| `d2_tail_carry` | holdout | `current_yes` | 696 | 13 | 0.932 | +0.2% | +0.0% | [+0.0%, +0.0%] | 93.4% | 5.0% | 1.3% | 0.3% |
| `d2_tail_carry` | holdout | `d1_no` | 712 | 13 | 0.928 | +0.3% | +0.1% | [-0.8%, +1.0%] | 91.3% | 6.9% | 1.3% | 0.6% |
| `d2_tail_carry` | holdout | `d2_no` | 712 | 13 | 0.992 | -0.5% | -0.8% | [-2.9%, +1.3%] | 91.3% | 6.9% | 1.3% | 0.6% |
| `d2_tail_carry` | holdout | `no_ladder` | 712 | 13 | 1.920 | -0.1% | -0.3% | [-1.4%, +0.6%] | 91.3% | 6.9% | 1.3% | 0.6% |
| `d2_tail_carry` | all | `current_yes` | 1255 | 26 | 0.924 | +0.2% | +0.0% | [+0.0%, +0.0%] | 92.6% | 6.1% | 1.1% | 0.2% |
| `d2_tail_carry` | all | `d1_no` | 1276 | 26 | 0.931 | -0.5% | -0.7% | [-1.7%, +0.2%] | 91.2% | 7.4% | 1.1% | 0.3% |
| `d2_tail_carry` | all | `d2_no` | 1276 | 26 | 0.991 | -0.2% | -0.4% | [-1.9%, +1.2%] | 91.2% | 7.4% | 1.1% | 0.3% |
| `d2_tail_carry` | all | `no_ladder` | 1276 | 26 | 1.922 | -0.4% | -0.5% | [-1.4%, +0.3%] | 91.2% | 7.4% | 1.1% | 0.3% |
| `two_leg_ladder_high_carry` | holdout | `current_yes` | 667 | 13 | 0.953 | -0.1% | +0.0% | [+0.0%, +0.0%] | 95.2% | 4.0% | 0.4% | 0.3% |
| `two_leg_ladder_high_carry` | holdout | `d1_no` | 669 | 13 | 0.964 | -0.4% | -0.3% | [-0.8%, +0.2%] | 94.9% | 4.0% | 0.4% | 0.6% |
| `two_leg_ladder_high_carry` | holdout | `d2_no` | 669 | 13 | 0.995 | +0.1% | +0.2% | [-1.6%, +2.0%] | 94.9% | 4.0% | 0.4% | 0.6% |
| `two_leg_ladder_high_carry` | holdout | `no_ladder` | 669 | 13 | 1.959 | -0.2% | -0.1% | [-1.0%, +0.8%] | 94.9% | 4.0% | 0.4% | 0.6% |
| `two_leg_ladder_high_carry` | all | `current_yes` | 1200 | 26 | 0.949 | +0.3% | +0.0% | [+0.0%, +0.0%] | 95.2% | 4.1% | 0.6% | 0.2% |
| `two_leg_ladder_high_carry` | all | `d1_no` | 1204 | 26 | 0.964 | -0.4% | -0.7% | [-1.6%, +0.0%] | 95.0% | 4.1% | 0.6% | 0.3% |
| `two_leg_ladder_high_carry` | all | `d2_no` | 1204 | 26 | 0.994 | +0.0% | -0.3% | [-1.5%, +1.0%] | 95.0% | 4.1% | 0.6% | 0.3% |
| `two_leg_ladder_high_carry` | all | `no_ladder` | 1204 | 26 | 1.957 | -0.2% | -0.5% | [-1.3%, +0.3%] | 95.0% | 4.1% | 0.6% | 0.3% |

## Skip-Over Convexity

Skip-over 是 NO carry 相对 current YES 的核心 convexity，但本轮必须拆成 d2-hit 和 true skip-over：d1 NO 在 d2-hit 也赢，d2 NO 在 d1-hit 也赢，ladder 在 d1/d2 exact hit 只剩一条腿赢。

| profile | period | settlement | rows | d1 NO - YES pnl | d2 NO - YES pnl | ladder - YES pnl |
|---|---|---|---:|---:|---:|---:|
| `current_d1_d2_visible` | holdout | `current_hit` | 1469 | -32.98 | -158.37 | -12.96 |
| `current_d1_d2_visible` | holdout | `d1_hit` | 505 | -73.86 | +244.93 | -21.83 |
| `current_d1_d2_visible` | holdout | `d2_hit` | 204 | +95.25 | -100.44 | -32.58 |
| `current_d1_d2_visible` | holdout | `skip_over` | 79 | +14.24 | +25.27 | +36.71 |
| `two_leg_ladder_high_carry` | holdout | `current_hit` | 635 | -3.72 | -21.65 | -1.32 |
| `two_leg_ladder_high_carry` | holdout | `d1_hit` | 27 | -0.42 | +23.39 | -0.13 |
| `two_leg_ladder_high_carry` | holdout | `d2_hit` | 3 | +2.05 | -1.08 | -0.72 |
| `two_leg_ladder_high_carry` | holdout | `skip_over` | 4 | +0.05 | +0.34 | +0.37 |

## 三道门 Verdict

在 2026-05-19..2026-06-14 的 factory-backed opportunity replay 中，NO carry/ladder 相对 sibling current YES 的 holdout excess ROI 没有稳定显著大于 0，前瞻不足，结论等级 `inconclusive`。

- significance=FAIL: d1/d2/ladder 相对 current YES 的 cluster-by-date CI 未稳定全在 0 以上。
- baseline=FAIL: baseline 是同窗 sibling current YES；NO carry 没有证明能打赢它。
- forward=FAIL: train/holdout 不足以支持固定 selector。
- conclusion=inconclusive: 不改 N100/live；若继续，做 shadow-only sibling selector telemetry。

## 产物

- Script: `scripts/analysis/reheat_risk/research_higher_no_carry_expression_selector_v1.py`
- Paired rows CSV: `docs/analysis/2026-06/generated/higher_no_carry_expression_selector_v1/paired_expression_state_rows.csv`
- Summary CSV: `docs/analysis/2026-06/generated/higher_no_carry_expression_selector_v1/expression_summary.csv`
- Settlement CSV: `docs/analysis/2026-06/generated/higher_no_carry_expression_selector_v1/settlement_contribution.csv`
- JSON: `docs/analysis/2026-06/2026-06-16-higher-no-carry-expression-selector-v1.json`
