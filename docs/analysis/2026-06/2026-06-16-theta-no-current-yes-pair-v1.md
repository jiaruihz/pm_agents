# Theta NO vs Current YES Pair v1

Status: snapshot
Generated: 2026-06-15T16:14:41.381610+00:00
Target metric: `theta_no_current_yes_expression_delta` = 同一 orderbook snapshot/city/date 下，买 d1 NO carry 与买当前 observed-max bucket YES 是否只是同一表达。

## 数据快照

- 数据源: v2 calibrated NO quotes + 原始 orderbook snapshot 中同文件 current-bucket YES ask；DB 只用于强制自检和 gate。
- run_stack: DB rebuild completed; FE start failed because port 5174 remained busy.
- fact_built_at_utc: `2026-06-15T16:08:57.366089+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30136, 'eligible': 10365, 'paper_ordered': 3968, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

它们是同一个天气 thesis 的两个表达，但不是同一个 payoff。`YES current` 只在最终停在当前档时赢；`NO d1` 在当前档赢，也在跳过下一档时赢，只输给“刚好命中下一档”。

在全部可配对 quote 里，最终当前档占 57.5%，d1 命中占 28.2%，skip-over 占 14.3%。这 14.3% 就是 NO d1 相对 current YES 的 payoff convexity。

但在真正 high-ask carry 子集里，skip-over 很小：`ask>=0.75/decline>=0.5` holdout skip-over 7.7%；`ask>=0.85/decline>=0.5/risk<=0.2` holdout skip-over 4.8%。所以越接近低保 theta，它越像 current YES 的 sibling expression，而不是完全独立方向。

交易上它们仍有区别：NO d1 通常更贵、更稳，YES current 更集中、更依赖不再升温。当前数据里没有证明 NO carry 明显优于 YES current；更像是同一 no-reheat thesis 下的 payoff/价格选择问题。

## Strategy-Grain Paired Performance

| profile | period | rows | dates | NO ROI | YES-current ROI | NO-YES ROI delta | delta CI95 | current | d1 hit | skip-over |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `all_paired_d1_h13_17` | train | 453 | 12 | -7.2% | -7.4% | +0.3% | [-5.2%, +5.9%] | 47.9% | 33.8% | 18.3% |
| `all_paired_d1_h13_17` | holdout | 338 | 9 | -1.8% | -5.3% | +3.5% | [-2.4%, +8.8%] | 48.2% | 30.5% | 21.3% |
| `all_paired_d1_h13_17` | all | 791 | 21 | -4.9% | -6.5% | +1.7% | [-2.4%, +5.6%] | 48.0% | 32.4% | 19.6% |
| `carry_ask75_decline05` | train | 94 | 12 | -6.8% | -6.1% | -0.7% | [-3.3%, +2.3%] | 83.0% | 14.9% | 2.1% |
| `carry_ask75_decline05` | holdout | 52 | 9 | +3.9% | +2.2% | +1.8% | [-2.2%, +6.2%] | 86.5% | 5.8% | 7.7% |
| `carry_ask75_decline05` | all | 146 | 21 | -3.0% | -3.2% | +0.2% | [-2.1%, +2.8%] | 84.2% | 11.6% | 4.1% |
| `carry_ask85_decline05_risk20` | train | 77 | 12 | -0.8% | +0.9% | -1.6% | [-2.4%, -1.1%] | 92.2% | 7.8% | 0.0% |
| `carry_ask85_decline05_risk20` | holdout | 42 | 9 | +1.7% | -0.3% | +2.0% | [-1.1%, +6.9%] | 90.5% | 4.8% | 4.8% |
| `carry_ask85_decline05_risk20` | all | 119 | 21 | +0.1% | +0.5% | -0.4% | [-1.7%, +1.9%] | 91.6% | 6.7% | 1.7% |
| `walkforward_shape_ask70_decline05` | train | 99 | 12 | -5.8% | -5.3% | -0.5% | [-3.9%, +3.1%] | 81.8% | 15.2% | 3.0% |
| `walkforward_shape_ask70_decline05` | holdout | 54 | 9 | +4.6% | +3.0% | +1.5% | [-2.7%, +6.1%] | 85.2% | 7.4% | 7.4% |
| `walkforward_shape_ask70_decline05` | all | 153 | 21 | -2.2% | -2.4% | +0.3% | [-2.5%, +3.2%] | 83.0% | 12.4% | 4.6% |

Row grain: strategy 表中一行是每个 profile 下首次触发的 `city,target_date,d1_bracket` paired opportunity；quote-grain payoff 只用于解释状态关系。

## 三道门 verdict

在 paired strategy-grain 下，NO carry 相对 current YES 的 ROI delta 没有稳定过门；结论等级 `inconclusive`。表达层结论是：不是完全同一 payoff，但应归入同一个 no-reheat thesis family，一起比较价格和 skip-over convexity。

- significance=FAIL: NO-vs-YES delta CI 不稳定。
- baseline=FAIL/NA: current YES 是 sibling expression baseline，不是被显著打败的弱 baseline。
- forward=FAIL: train/holdout 没有一致证明 NO 优于 YES。
- conclusion=inconclusive: 不进 live；下一步把 current YES、NO d1、NO d2/ladder 放进同一个 expression selector。

## 输出文件

- JSON: `docs/analysis/2026-06/2026-06-16-theta-no-current-yes-pair-v1.json`
- generated CSV dir: `docs/analysis/2026-06/generated/theta_no_current_yes_pair_v1`
- Script: `scripts/analysis/reheat_risk/research_theta_no_current_yes_pair_v1.py`
