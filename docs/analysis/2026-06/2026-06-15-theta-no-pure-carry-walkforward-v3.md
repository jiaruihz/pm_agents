# Theta NO Pure Carry Walk-Forward v3

Status: snapshot
Generated: 2026-06-15T16:00:34.292867+00:00
Target metric: `pure_theta_carry_walkforward` = 高 NO ask 的 d1 NO carry，只允许用测试日前的历史日期选择 ask/decline/hour/no-reheat risk 参数。

## 数据快照

- 数据源: v2 calibrated quote replay；DB 只用于强制自检和 live fill gate。
- deduped d1 h13-17 quote rows: 1082; target dates: 21.
- fact_built_at_utc: `2026-06-15T14:43:25.893487+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`。
- fact_signal_candidates coverage: `{'rows': 30132, 'eligible': 10364, 'paper_ordered': 3961, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

这次只看纯 theta carry，没有混入 0.40-0.55 的方向单。结果是：信号还活着，但还小。disciplined walk-forward 选出的单有 40 行、11 个交易日，ROI +3.8%，相对同 ask/hour/risk 但不要求 decline 的 baseline 超额 +10.0%。

更严格的 `ask>=0.75` selector 只剩 20 行、9 个交易日，ROI +2.1%。这更像我们想要的高价 NO 低保，但样本太小，容量也小。

我的判断：这个方向应该继续做，但当前证据等级是 research-positive / no-live。下一步优先扩样和 forward 记录，而不是马上变成 shadow/paper；如果要 shadow，也应该只记录 zero-notional telemetry。

## Walk-Forward 结果

| selector | rows | dates | avg ask | ROI | PnL | positive dates | excess vs matched no-decline | CI95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `loose_ask65` | 49 | 14 | 0.87 | +3.3% | +1.42 | 71.4% | +9.1% | [-2.2%, +17.4%] |
| `disciplined_ask65` | 40 | 11 | 0.87 | +3.8% | +1.32 | 72.7% | +10.0% | [-3.5%, +19.5%] |
| `strict_ask75` | 20 | 9 | 0.93 | +2.1% | +0.39 | 88.9% | +2.8% | [-9.4%, +11.0%] |
| `strict_ask80` | 20 | 9 | 0.93 | +2.1% | +0.39 | 88.9% | +2.8% | [-9.4%, +11.0%] |

Row grain: 一行是一个去重后的 `city,target_date,bracket` NO 买入机会，不是真实成交。

## Holdout 里最强的固定口径

这些是事后 holdout 排名，用来理解形状，不作为可上线选参。

| profile | rows | dates | ROI | PnL | excess | CI95 |
|---|---:|---:|---:|---:|---:|---:|
| `ask>=0.65|decline>=0.5|h13-15|none` | 41 | 9 | +10.8% | +3.81 | +14.8% | [+6.8%, +23.5%] |
| `ask>=0.65|decline>=0.5|h13-17|none` | 44 | 9 | +10.7% | +4.07 | +13.9% | [+6.2%, +21.8%] |
| `ask>=0.7|decline>=0.5|h13-15|none` | 40 | 9 | +10.1% | +3.48 | +13.7% | [+4.3%, +23.2%] |
| `ask>=0.65|decline>=1|h13-15|none` | 34 | 9 | +9.6% | +2.81 | +13.7% | [+3.2%, +22.6%] |
| `ask>=0.7|decline>=0.5|h13-17|none` | 43 | 9 | +10.0% | +3.74 | +13.1% | [+4.8%, +21.4%] |
| `ask>=0.65|decline>=0.5|h13-14|none` | 39 | 9 | +10.4% | +3.49 | +13.1% | [+5.3%, +22.2%] |
| `ask>=0.65|decline>=1|h13-17|none` | 36 | 9 | +9.3% | +2.89 | +12.4% | [+2.9%, +20.7%] |
| `ask>=0.7|decline>=1|h13-15|none` | 33 | 9 | +8.7% | +2.48 | +12.4% | [+0.5%, +22.0%] |
| `ask>=0.7|decline>=0.5|h13-14|none` | 38 | 9 | +9.6% | +3.16 | +12.4% | [+3.8%, +21.8%] |
| `ask>=0.65|decline>=1|h13-14|none` | 32 | 9 | +9.1% | +2.49 | +11.7% | [+1.9%, +21.0%] |

## 最近的历史选参

| selector | target_date | chosen profile | train ROI | train excess | train rows | day rows |
|---|---:|---|---:|---:|---:|---:|
| `disciplined_ask65` | 2026-06-02 | `ask>=0.7|decline>=0.5|h13-17|pLose<=0.2` | +4.1% | +2.9% | 38 | 1 |
| `disciplined_ask65` | 2026-06-03 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +0.9% | +3.1% | 26 | 5 |
| `disciplined_ask65` | 2026-06-04 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +1.7% | +3.1% | 31 | 1 |
| `disciplined_ask65` | 2026-06-05 | `ask>=0.7|decline>=0.5|h13-17|none` | +0.3% | +3.2% | 70 | 8 |
| `disciplined_ask65` | 2026-06-06 | `ask>=0.7|decline>=0.5|h13-17|none` | +2.4% | +5.3% | 78 | 5 |
| `disciplined_ask65` | 2026-06-07 | `ask>=0.7|decline>=0.5|h13-17|none` | +3.1% | +6.4% | 83 | 4 |
| `disciplined_ask65` | 2026-06-08 | `ask>=0.7|decline>=0.5|h13-17|none` | +2.4% | +6.0% | 87 | 3 |
| `disciplined_ask65` | 2026-06-09 | `ask>=0.7|decline>=0.5|h13-17|none` | +3.0% | +7.0% | 90 | 5 |
| `loose_ask65` | 2026-06-02 | `ask>=0.7|decline>=0.5|h13-17|pLose<=0.2` | +4.1% | +2.9% | 38 | 1 |
| `loose_ask65` | 2026-06-03 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +0.9% | +3.1% | 26 | 5 |
| `loose_ask65` | 2026-06-04 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +1.7% | +3.1% | 31 | 1 |
| `loose_ask65` | 2026-06-05 | `ask>=0.7|decline>=0.5|h13-17|none` | +0.3% | +3.2% | 70 | 8 |
| `loose_ask65` | 2026-06-06 | `ask>=0.7|decline>=0.5|h13-17|none` | +2.4% | +5.3% | 78 | 5 |
| `loose_ask65` | 2026-06-07 | `ask>=0.7|decline>=0.5|h13-17|none` | +3.1% | +6.4% | 83 | 4 |
| `loose_ask65` | 2026-06-08 | `ask>=0.7|decline>=0.5|h13-17|none` | +2.4% | +6.0% | 87 | 3 |
| `loose_ask65` | 2026-06-09 | `ask>=0.7|decline>=0.5|h13-17|none` | +3.0% | +7.0% | 90 | 5 |
| `strict_ask75` | 2026-06-02 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +0.9% | +2.1% | 26 | 0 |
| `strict_ask75` | 2026-06-03 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +0.9% | +3.1% | 26 | 5 |
| `strict_ask75` | 2026-06-04 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +1.7% | +3.1% | 31 | 1 |
| `strict_ask75` | 2026-06-05 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +1.8% | +2.8% | 32 | 1 |
| `strict_ask75` | 2026-06-06 | `ask>=0.85|decline>=0.5|h13-17|pLose<=0.2` | +2.7% | +3.0% | 37 | 3 |
| `strict_ask75` | 2026-06-07 | `ask>=0.85|decline>=0.5|h13-17|pLose<=0.2` | +3.2% | +3.4% | 40 | 2 |
| `strict_ask75` | 2026-06-08 | `ask>=0.85|decline>=0.5|h13-17|pLose<=0.2` | +0.8% | +1.2% | 42 | 1 |
| `strict_ask75` | 2026-06-09 | `ask>=0.85|decline>=0.5|h13-17|pLose<=0.2` | +1.1% | +1.2% | 43 | 4 |
| `strict_ask80` | 2026-06-02 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +0.9% | +2.1% | 26 | 0 |
| `strict_ask80` | 2026-06-03 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +0.9% | +3.1% | 26 | 5 |
| `strict_ask80` | 2026-06-04 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +1.7% | +3.1% | 31 | 1 |
| `strict_ask80` | 2026-06-05 | `ask>=0.85|decline>=0.5|h13-14|pLose<=0.2` | +1.8% | +2.8% | 32 | 1 |
| `strict_ask80` | 2026-06-06 | `ask>=0.85|decline>=0.5|h13-17|pLose<=0.2` | +2.7% | +3.0% | 37 | 3 |
| `strict_ask80` | 2026-06-07 | `ask>=0.85|decline>=0.5|h13-17|pLose<=0.2` | +3.2% | +3.4% | 40 | 2 |
| `strict_ask80` | 2026-06-08 | `ask>=0.85|decline>=0.5|h13-17|pLose<=0.2` | +0.8% | +1.2% | 42 | 1 |
| `strict_ask80` | 2026-06-09 | `ask>=0.85|decline>=0.5|h13-17|pLose<=0.2` | +1.1% | +1.2% | 43 | 4 |

## 三道门 verdict

在 prefix walk-forward 中，disciplined selector ROI 为 +3.8%，matched baseline excess 为 +10.0%（95% CI [-3.5%, +19.5%]），但样本只有 40 行/11 天，且 strict high-ask 版本只有 20 行，结论等级 `inconclusive`。

- significance=WEAK: walk-forward 点估计为正，daily bootstrap 可能受少数日期和小样本影响。
- baseline=PASS_ON_POINT_ESTIMATE: 相对 matched no-decline baseline 明显为正。
- forward=WEAK: prefix walk-forward 为正，但时间窗仅 21 天，strict high-ask 样本太小。
- conclusion=inconclusive: 不进 live；可继续做 zero-notional forward telemetry / 扩样研究。

## 输出文件

- JSON: `docs/analysis/2026-06/2026-06-15-theta-no-pure-carry-walkforward-v3.json`
- generated CSV dir: `docs/analysis/2026-06/generated/theta_no_pure_carry_walkforward_v3`
- Script: `scripts/analysis/reheat_risk/research_theta_no_pure_carry_walkforward_v3.py`
