# Current-YES 反手买当前档 NO 盘口 EV v1

日期：2026-06-22  
状态：research only / 不动 live  
脚本：`scripts/analysis/reheat_risk/research_current_yes_no_side_overround_ev_v1.py`  
输出：`docs/analysis/2026-06/generated/current_yes_no_side_overround_ev_v1/summary.json`、`enriched_rows.csv`

## 一句话结论

在 `2026-05-19..2026-06-14`，current-YES v8 同一机会集上，买当前档 **真实 NO ask** 相对“市场价当概率 / EV=0”基准的超额 ROI 为 **-10.6%**（target_date block bootstrap 95% CI **[-16.1%, -5.3%]**），前瞻 **FAIL**，结论等级 **inconclusive / not-promotable**。

交易动作：**不把 current-YES 主线翻到当前档 NO**。YES ask 端确实偏贵，但真实 NO ask 端也带抽水，`YES ask + NO ask - 1` 平均 **+5.9pp**；反手买 NO 被点差/overround 吃光并显著为负。主线应回到“晚盘 fade-confirmed tiny sleeve + 更严格状态/组合去重”，不要把 peak-forming 的亏损直接翻译成 broad BUY_NO。

## 数据快照

- 已执行 `scripts/ops/sync_weather_remote.sh`：同步到 `2026-06-22 00:30` 附近 orderbook snapshot；split current-YES runtime 目录已同步。
- 已执行 `scripts/weather_dashboard/run_stack.sh`：DB 重建完成，最后 frontend 端口 `5174` 未释放导致命令非零退出；随后单独重跑 CLOB gate。
- `runtime/weather.db` mtime：`2026-06-22T00:42:48` 本地时间；size `69,058,560` bytes。
- `MAX(fact_trades.fact_built_at_utc)`：`2026-06-21T16:42:28.257284+00:00`。
- CLOB gate：`gate_pass=true`；`db_not_in_cache=0`、`cache_not_in_db=0`、`db_fill_cost_minus_fact_cost=0.0`、`missing_order_rows=0`、`over_order_keys=0`。
- 5 行 SQL 自检：`fact_trades` rows = live_real 855 / live_simulated 624 / paper 2285 / snapshot_replay 636；settled 4250、unsettled/blank 150；`fact_signal_candidates` rows 34830、eligible 12371、paper_ordered 4819、live_filled 348；CLOB orders submitted 961 with_fill 855，error 33 with_fill 0。
- 本研究主分母不是 fill-grain PnL：v8 current-YES replay `feature_rows.csv` 3239 rows，窗口 `2026-05-19..2026-06-14`，全部 settled；3167 rows 能从对应 `orderbook_file` 取到真实当前档 NO ask。缺 NO quote 72 rows 全部是 `YES ask <0.50`，不影响 [0.50, 1.00] 校准桶。
- `fact_signal_candidates` schema 已确认有 `market_yes_price / yes_spread / no_spread / best_entry_price / final_yes`，但没有 `current_no_ask`；当前档 NO 价格来自 raw orderbook snapshot 中同一 `condition_id` 的 `outcome='no'`，不是 `1 - yes_ask`。

## 目标指标与分母

`current_bracket_no_net_ev` = 在 current-YES v8 replay 的同一 opportunity-grain 分母上，买“当前 running max 所在 bracket”的 NO：

- 分母：`contains_running=True`、settled label 非空、`yes_current_ask` 非空、同一 snapshot 可取 current NO ask。
- 价格：YES 用 `yes_current_ask`；NO 用 `orderbook_file + condition_id + outcome='no'` 的真实 `best_ask`。
- PnL：一股 NO 的净结果 `label_no_wins - no_ask`；ROI = `SUM(pnl) / SUM(no_ask)`。买在 ask 已扣点差；未另加 fees。
- 推断：按 `target_date` block bootstrap，5000 reps。
- 基准：市场价当概率，即 NO EV=0；超额 ROI 就是上面的 realized NO ROI。
- 前瞻：按 target_date 前 70% train (`2026-05-19..06-05`) / 后 30% holdout (`2026-06-06..06-14`)。

## YES 高估复核

复核结果与 6/21 诊断一致：YES ask 在 [0.50,0.90) 桶里真实胜率低于 ask；`>=0.70` 合计差为 -1.4pp。

| yes ask 桶 | n | avg YES ask | true YES | true-ask | YES ROI 95% CI |
|---|---:|---:|---:|---:|---:|
| [0.50,0.60) | 122 | 0.547 | 0.459 | -8.8% | -16.1% [-30.2%, -1.4%] |
| [0.60,0.70) | 137 | 0.651 | 0.613 | -3.8% | -5.8% [-19.4%, +6.4%] |
| [0.70,0.80) | 229 | 0.747 | 0.712 | -3.5% | -4.7% [-15.1%, +5.6%] |
| [0.80,0.90) | 421 | 0.851 | 0.800 | -5.0% | -5.9% [-11.2%, -0.7%] |
| [0.90,1.00] | 1469 | 0.969 | 0.969 | +0.0% | +0.0% [-1.2%, +1.1%] |
| >=0.70 | 2119 | 0.922 | 0.908 | -1.4% | -1.5% [-3.3%, +0.3%] |

关键解释：YES 被买贵不等于 NO 可买。二元合约两边同时有盘口抽水；必须看真实 NO ask。

## NO 净 EV

按同一 YES ask 桶看，当前档 NO 没有一个 live-like 高 ask 桶通过。`>=0.70` 这组最接近当前 live 的高 YES ask 形态，NO ROI 是 **-30.4%**，CI 完全低于 0。

| YES ask 桶 | n | avg NO ask | true NO | true-NOask | NO ROI 95% CI | avg NO spread | ask overround |
|---|---:|---:|---:|---:|---:|---:|---:|
| TOTAL | 3167 | 0.346 | 0.309 | -3.7% | -10.6% [-16.1%, -5.3%] | 0.059 | +5.9% |
| [0.50,0.60) | 122 | 0.554 | 0.541 | -1.3% | -2.4% [-15.2%, +9.4%] | 0.100 | +10.1% |
| [0.60,0.70) | 137 | 0.474 | 0.387 | -8.7% | -18.4% [-36.1%, +3.7%] | 0.125 | +12.5% |
| [0.70,0.80) | 229 | 0.372 | 0.288 | -8.4% | -22.6% [-44.3%, +0.2%] | 0.119 | +11.9% |
| [0.80,0.90) | 421 | 0.245 | 0.200 | -4.6% | -18.7% [-39.7%, +5.5%] | 0.096 | +9.6% |
| [0.90,1.00] | 1469 | 0.062 | 0.031 | -3.2% | -50.9% [-67.8%, -31.2%] | 0.031 | +3.1% |
| >=0.70 | 2119 | 0.132 | 0.092 | -4.0% | -30.4% [-44.7%, -15.8%] | 0.054 | +5.4% |

按 NO ask 桶看也一样：高 NO ask 桶和低 NO ask 桶都未形成稳定正 EV。低 NO ask 看起来“便宜”，但真实 NO 命中率更低；高 NO ask true-NO 仍低于 ask。

| NO ask 桶 | n | avg NO ask | true NO | true-NOask | NO ROI 95% CI |
|---|---:|---:|---:|---:|---:|
| <0.05 | 743 | 0.026 | 0.008 | -1.8% | -68.5% [-90.1%, -42.0%] |
| [0.05,0.10) | 459 | 0.068 | 0.044 | -2.4% | -35.6% [-62.7%, -4.2%] |
| [0.10,0.20) | 422 | 0.143 | 0.118 | -2.4% | -17.1% [-41.7%, +8.1%] |
| [0.20,0.30) | 232 | 0.245 | 0.211 | -3.4% | -13.8% [-36.0%, +7.9%] |
| [0.30,0.50) | 339 | 0.388 | 0.342 | -4.6% | -11.8% [-28.8%, +6.1%] |
| >=0.50 | 972 | 0.820 | 0.760 | -6.0% | -7.3% [-11.6%, -3.4%] |

## 结构性 vs 条件性归因

如果“exact-value 合约天然彩票溢价”能直接反手到 NO，应该看到 NO 边在 reheat 状态外也均匀为正。结果相反：NO edge 全样本为负，且 no-reheat / fade / forecast 已封顶的状态更负。

| slice | n | avg NO ask | true NO | NO ROI 95% CI |
|---|---:|---:|---:|---:|
| peak_forming_like | 2137 | 0.452 | 0.416 | -8.0% [-13.5%, -2.9%] |
| fade_like | 1030 | 0.127 | 0.089 | -29.7% [-41.9%, -17.6%] |
| temp cooling | 430 | 0.076 | 0.047 | -39.1% [-63.5%, -13.2%] |
| temp flat | 577 | 0.176 | 0.127 | -28.2% [-44.1%, -12.3%] |
| temp warming | 2149 | 0.446 | 0.413 | -7.4% [-12.2%, -2.9%] |
| forecast remaining <=0.5F | 1506 | 0.266 | 0.208 | -21.8% [-31.4%, -12.4%] |
| forecast remaining 0.5-2F | 614 | 0.407 | 0.396 | -2.8% [-14.0%, +8.4%] |
| forecast remaining >2F | 671 | 0.559 | 0.528 | -5.6% [-11.5%, +0.4%] |
| minutes since max <30m | 478 | 0.468 | 0.446 | -4.8% [-12.2%, +3.3%] |
| minutes since max 30-90m | 1087 | 0.438 | 0.404 | -7.7% [-14.9%, -0.6%] |
| minutes since max >=90m | 1100 | 0.267 | 0.213 | -20.3% [-29.1%, -11.7%] |

归因结论：

- **结构性 NO 边：不成立。** 全样本、YES ask 高桶、NO ask 桶都没有正超额。
- **no-reheat 条件 NO 边：反向。** 越像 no-reheat / fade / forecast 已封顶，当前档 YES 越可能守住，买 NO 越亏。
- **reheat-risk 条件 NO 边：也未过门。** 比较接近的 slice 是 `forecast_remaining >2F` 或 `<30m since max`，但 CI 跨 0，不能变成交易规则；这更像“少亏的风险态”，不是已确认 alpha。

## 容量与流动性

当前档 NO 不是没流动性，而是放大后更难看：

- NO top ask shares：p10 5.5、median 16.29、p90 105.0。
- NO depth within 5c shares：p10 20.0、median 109.68、p90 700.29。
- top ask 可用成本 median $3.23；within 5c 成本 median $16.55。
- 3138/3167 rows top ask size >= 1 share；3085/3167 rows top ask size >= 5 shares；3044/3167 rows within-5c depth cost >= $1.5。

容量加权结果：

| execution cap | top ask ROI 95% CI | within 5c ROI 95% CI |
|---|---:|---:|
| $1.5 per opportunity | -25.8% [-35.0%, -16.8%] | -30.3% [-40.8%, -19.6%] |
| $5 per opportunity | -22.2% [-29.7%, -14.6%] | -33.1% [-41.6%, -24.8%] |
| $10 per opportunity | -19.5% [-26.3%, -12.8%] | -32.4% [-40.4%, -24.7%] |

所以不是“有 edge 但 size 不够”，而是“盘口给得出来，但买 NO 的真实净 edge 为负；吃更多深度后仍为负”。

## Train / Holdout

| split | dates | n | NO ROI 95% CI | NO edge |
|---|---:|---:|---:|---:|
| train `2026-05-19..2026-06-05` | 18 | 2082 | -11.7% [-17.9%, -5.2%] | -4.1% |
| holdout `2026-06-06..2026-06-14` | 9 | 1085 | -8.3% [-18.8%, +0.1%] | -2.7% |

Holdout 点估仍为负，前瞻 FAIL。

## 三门判定

| 门 | 结果 | 证据 |
|---|---|---|
| significance | FAIL | 当前档 NO 总 ROI -10.6%，95% CI [-16.1%, -5.3%]，不是正显著，是负显著 |
| baseline | FAIL | 相对“市场价当概率 / EV=0”没有超额；true NO rate 30.9% < avg NO ask 34.6% |
| forward | FAIL | holdout ROI -8.3%，点估为负 |
| level | inconclusive / not-promotable | 不允许 live 翻边；broad current NO 也不是 shadow_candidate |

## 交易动作

1. 不把 `theta_current_yes` 主线翻到“买当前档 NO”。
2. broad current-NO 不开 shadow/paper 主线；若继续研究，只能是更窄的 reheat-risk 条件头，并且必须重新过三门。
3. 回到组合层止血：同一 `city/date/bracket` proposition 跨 `peak_forming_micro` 与 `fade_confirmed` 去重；不要 split profile 重复押同一命题。
4. live 侧只保留/回到晚盘 `fade_confirmed` tiny sleeve 的研究节奏；`peak_forming` 继续按 fragile/micro-probe 或 shadow 对待，直到状态层和 forward evidence 过门。

## 残余风险

- 主窗口止于 v8 feature layer `2026-06-14`；不声称覆盖 6/20 reheat 之后的新 regime。
- reheat factory join 覆盖 2665/3167 rows；forecast/minutes 切片是辅助归因，不是单独交易规则。
- 本轮没有发布 live_real PnL；CLOB gate 只是确认真实成交链路健康。
- 多切片未做多重检验校正；但主结论是 broad NO 负显著，不依赖挑最优 slice。
- 手续费按现有 Polymarket weather replay 口径未另加；买在 ask 已包含点差成本。
