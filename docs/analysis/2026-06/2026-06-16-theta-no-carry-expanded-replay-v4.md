# Theta NO Carry Expanded Replay v4

Status: snapshot
Generated: 2026-06-16T15:52:36.137680+00:00
Target metric: `source_aligned_theta_no_carry_vs_current_yes` = 在历史 source-aligned whitelist 城市里，用同一 orderbook 文件比较 d1/d2 高 ask NO carry 和当前 running-max 档 YES。

## 数据快照

- 数据更新: 已使用 2026-05-19..2026-06-14 已结算 pm_history + raw orderbook；2026-06-15/16 orderbook 虽已同步，但 pm_history 未结算，不进入 PnL。
- 天气补全: 默认 WU/IEM cache 停在 2026-06-09 UTC；本次用 `theta_no_wu_obs_patch_v1` 研究补丁抓取 36 个 source-aligned 城市的 2026-05-19..2026-06-14 IEM/METAR，并重建 observed running-max detail。
- whitelist cities: 36; paired tail NO quote rows: 3209; active dates: 26.
- orderbook files seen: 1289; quote rows after hourly dedupe: 33481.
- fact_built_at_utc: `2026-06-16T15:50:05.971834+00:00`。
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`。
- fact_trades settlement_status: `[{'settlement_status': '', 'rows': 90}, {'settlement_status': 'settled', 'rows': 4310}]`。
- fact_signal_candidates coverage: `{'rows': 30919, 'eligible': 10685, 'paper_ordered': 4123, 'live_filled': 348}`。
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`。
- CLOB coverage gate: gate_pass=True, missing_order_rows=0, over_order_keys=0, db_fill_cost_minus_fact_cost=0.0.

## 人话结论

补数据后，NO carry 和当前最高温 YES 仍然不是“完全同一张票”：NO d1 输在最终刚好落到上方一档，赢在当前档守住或直接跳过 d1；当前 YES 只赢在最终等于当前档。也就是说，二者共享同一个 no-reheat 物理判断，但 payout 不同。

交易上更重要的是：扩展到 6/14 后，核心 `d1 ask>=0.75 + decline>=0.5` holdout NO ROI 是 -1.9%，当前 YES ROI 是 -2.5%，NO-YES ROI 差 +0.6%，CI95 [-2.0%, +4.1%]。新增 6/10-6/14 单独看，NO ROI -3.7%，当前 YES ROI -0.5%。

这说明“补更多历史”是对的，而且已经把样本从原来 21 个 replay 日期推进到 26 个 active replay 日期；但这次补出来的新增日期没有把证据推到 live。真正卡点仍是：高 ask carry 的可交易样本有限，且 NO 相对当前 YES 的优势区间还压着 0。

## 关键切片

| profile | period | rows | dates | NO ask | NO ROI | YES ROI | NO-YES ROI | CI95 | NO lose | skip-over |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `d1_ask75_decline05` | holdout | 118 | 13 | 0.90 | -1.9% | -2.5% | +0.6% | [-2.0%, +4.1%] | 11.9% | 5.1% |
| `d1_ask75_decline05` | new_2026_06_10_14 | 45 | 4 | 0.90 | -3.7% | -0.5% | -3.3% | [-3.9%, -2.6%] | 13.3% | 0.0% |
| `d1_ask75_decline05` | all | 238 | 25 | 0.90 | -4.5% | -4.6% | +0.0% | [-1.8%, +2.2%] | 13.9% | 4.2% |
| `d1_ask85_decline05` | holdout | 101 | 13 | 0.93 | -4.1% | -3.9% | -0.2% | [-2.1%, +2.5%] | 10.9% | 3.0% |
| `d1_ask85_decline05` | new_2026_06_10_14 | 39 | 4 | 0.93 | -3.2% | -0.2% | -2.9% | [-4.4%, -1.7%] | 10.3% | 0.0% |
| `d1_ask85_decline05` | all | 209 | 25 | 0.93 | -4.0% | -3.5% | -0.5% | [-1.8%, +1.4%] | 11.0% | 2.4% |
| `d1_ask75_decline10` | holdout | 89 | 13 | 0.90 | -1.4% | -2.3% | +0.9% | [-2.6%, +5.5%] | 11.2% | 5.6% |
| `d1_ask75_decline10` | new_2026_06_10_14 | 36 | 4 | 0.90 | -0.8% | +3.2% | -4.0% | [-5.3%, -3.1%] | 11.1% | 0.0% |
| `d1_ask75_decline10` | all | 177 | 25 | 0.90 | -5.5% | -4.7% | -0.8% | [-2.7%, +1.6%] | 14.7% | 2.8% |
| `d2_ask75_decline05` | holdout | 33 | 13 | 0.92 | -8.3% | +17.2% | -25.5% | [-46.0%, -1.9%] | 15.2% | 18.2% |
| `d2_ask75_decline05` | new_2026_06_10_14 | 13 | 4 | 0.93 | -0.6% | +44.8% | -45.4% | [-72.1%, -22.9%] | 7.7% | 15.4% |
| `d2_ask75_decline05` | all | 76 | 25 | 0.92 | -3.1% | -3.5% | +0.4% | [-22.6%, +21.6%] | 10.5% | 35.5% |

## 交易动作

- 不上 live：这份 expanded replay 是 opportunity/orderbook replay，不是 shadow/paper/live fill；而且核心 NO-over-YES 优势 CI 仍跨 0。
- 下一步如果继续，只应该做 shadow-only sibling selector：同一 city-day 同时记录 `current YES`、`d1 NO`、`d2 NO` 的可买价和事后结算，让 live 之前先验证表达选择，而不是直接发真钱。

## 产物

- CSV: `docs/analysis/2026-06/generated/theta_no_carry_expanded_replay_v4/expanded_quote_rows.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_carry_expanded_replay_v4/paired_strategy_rows.csv`
- CSV: `docs/analysis/2026-06/generated/theta_no_carry_expanded_replay_v4/paired_summary.csv`
- JSON: `docs/analysis/2026-06/2026-06-16-theta-no-carry-expanded-replay-v4.json`
