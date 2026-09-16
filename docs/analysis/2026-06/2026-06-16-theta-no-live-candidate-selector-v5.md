# Theta NO Live Candidate Selector v5

Status: snapshot
Generated: 2026-06-15T16:53:26.039714+00:00
Target metric: `live_candidate_theta_no_selector` = 在 expanded paired replay 中，只看 source-aligned d1 高 ask NO carry，检验是否存在可前瞻复用的 live 规则。

## 数据完整性自检

- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。
- input quote rows: 3209; d1 rows: 2204; active dates: 26.
- fact_built_at_utc: `2026-06-15T16:31:04.483297+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30140, 'eligible': 10366, 'paper_ordered': 3968, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

我把 d1 高 ask NO carry 继续往 live 方向压了一轮：网格一共形成 93 个有 train/holdout 支撑的候选 profile，但同时满足 train NO ROI>0、train NO-over-YES>0、holdout NO ROI>0、holdout NO-over-YES>0、且 holdout 至少 30 行/10 天的 profile 数量是 0。

这不是“物理逻辑错了”，而是市场价格已经把这类 no-reheat 信息吃得很干净。你能看到一些 holdout 看起来还行的切片，但它们在 train 里同号失败；prefix walk-forward 只能用过去日期选参数，结果也没有过 live 的三道门。

一句话结论：在 2026-05-20..2026-06-14 expanded replay 中，d1 high-ask theta-NO 相对 current YES 的可前瞻超额 ROI 没有显著大于 0，前瞻 FAIL，结论等级 `inconclusive`，不允许 live。

## 最好的 holdout 切片也没过前瞻

这一段只用来解释“为什么不能 live”：排在最上面的切片行数很少，而且 train 同号失败；它不是可部署规则。

| profile | rows | dates | NO ROI | YES ROI | NO-YES | NO CI95 | Delta CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `d1|ask>=0.85|decline>=1.5|h15-17|anyYES` | 11 | 7 | +6.5% | +6.7% | -0.3% | [+4.2%, +8.1%] | [-1.3%, +0.5%] |
| `d1|ask>=0.9|decline>=1.5|h15-17|anyYES` | 11 | 7 | +4.9% | +5.3% | -0.5% | [+3.6%, +5.8%] | [-1.3%, -0.0%] |
| `d1|ask>=0.93|decline>=1.5|h15-17|anyYES` | 10 | 7 | +4.4% | +4.8% | -0.4% | [+3.6%, +5.1%] | [-1.3%, +0.1%] |
| `d1|ask>=0.85|decline>=1|h15-17|anyYES` | 43 | 13 | +0.9% | -1.7% | +2.6% | [-7.1%, +6.1%] | [-0.1%, +8.9%] |
| `d1|ask>=0.85|decline>=1.5|h14-17|anyYES` | 16 | 10 | -0.1% | +0.5% | -0.6% | [-17.1%, +7.8%] | [-1.3%, -0.1%] |
| `d1|ask>=0.8|decline>=1|h15-17|anyYES` | 46 | 13 | -0.1% | -2.4% | +2.2% | [-8.8%, +6.0%] | [-0.3%, +8.3%] |
| `d1|ask>=0.9|decline>=1|h15-17|anyYES` | 41 | 13 | -0.4% | -0.6% | +0.1% | [-9.2%, +4.9%] | [-0.2%, +0.8%] |
| `d1|ask>=0.85|decline>=2|h14-17|anyYES` | 14 | 10 | -0.9% | -0.2% | -0.6% | [-20.9%, +8.4%] | [-1.3%, -0.0%] |

## Prefix walk-forward

| selector | rows | dates | NO ROI | YES ROI | NO-YES | NO CI95 | Delta CI95 | conclusion |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `best_hist_delta_positive` | 39 | 11 | +4.1% | +3.0% | +1.1% | [-7.3%, +11.0%] | [-4.0%, +7.2%] | inconclusive |
| `best_hist_no_roi_positive` | 30 | 14 | -6.1% | -5.5% | -0.6% | [-21.2%, +3.9%] | [-1.0%, -0.1%] | inconclusive |
| `strict_both_positive` | 10 | 3 | -15.7% | -15.6% | -0.1% | [-100.0%, +4.2%] | [-0.4%, +0.2%] | inconclusive |

## 失败来源

核心坏例不是随机散落：d1 NO 的输法是最终刚好落到上方一档。新增 6/10..6/14 的问题尤其明显：skip-over 几乎没有，NO 少了它相对 current YES 的结构优势。

| feature | value | rows | dates | NO ROI | YES ROI | NO lose | skip-over |
|---|---|---:|---:|---:|---:|---:|---:|
| decline_bucket_v5 | `1.5-2.0` | 11 | 7 | -1.5% | +0.3% | 9.1% | 0.0% |
| decline_bucket_v5 | `0.5-1.0` | 67 | 25 | -2.7% | -5.2% | 11.9% | 7.5% |
| decline_bucket_v5 | `1.0-1.5` | 132 | 25 | -4.7% | -3.5% | 14.4% | 3.0% |
| decline_bucket_v5 | `>=2.0` | 28 | 16 | -9.6% | -10.4% | 17.9% | 3.6% |
| hour_bucket | `13` | 88 | 24 | +3.0% | +5.0% | 9.1% | 4.5% |
| hour_bucket | `17` | 16 | 13 | -0.2% | +0.7% | 6.2% | 0.0% |
| hour_bucket | `15` | 49 | 23 | -6.6% | -6.3% | 14.3% | 4.1% |
| hour_bucket | `14` | 55 | 24 | -10.7% | -14.8% | 20.0% | 7.3% |
| hour_bucket | `16` | 30 | 15 | -13.8% | -12.8% | 20.0% | 0.0% |
| no_ask_bucket | `0.85-0.90` | 49 | 21 | +1.9% | +0.8% | 10.2% | 6.1% |
| no_ask_bucket | `0.75-0.85` | 51 | 23 | +1.6% | -0.3% | 17.6% | 9.8% |
| no_ask_bucket | `0.90-0.95` | 68 | 22 | -5.5% | -3.8% | 11.8% | 1.5% |
| no_ask_bucket | `0.95-0.97` | 66 | 24 | -9.0% | -8.7% | 12.1% | 1.5% |
| yes_ask_bucket | `>0.40` | 230 | 25 | -5.3% | -4.6% | 14.3% | 1.3% |

## 三道门

- significance=FAIL：NO ROI 和 NO-over-YES 的日期 bootstrap CI 没有稳定不跨 0。
- baseline=FAIL：相对 current-bucket YES 没有稳定超额；这正是要回答的 sibling expression baseline。
- forward=FAIL：train 看起来可选的 profile 在 holdout 不同号，prefix walk-forward 也不过。
- conclusion=inconclusive：禁止 live；最多继续做 shadow-only sibling selector。

## 产物

- CSV: `docs/analysis/2026-06/generated/theta_no_live_candidate_selector_v5/profile_grid.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_live_candidate_selector_v5/walkforward_rows.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_live_candidate_selector_v5/walkforward_summary.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_live_candidate_selector_v5/badcase_bins.csv`
- JSON: `docs/analysis/2026-06/2026-06-16-theta-no-live-candidate-selector-v5.json`
