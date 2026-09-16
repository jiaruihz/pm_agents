# 绩效分析：Tokyo JMA cross previous-bracket NO

> 窗口：2026-07-09 — 2026-07-21
> 策略身份：`fast_source_prev_no_trial_v1` / `live_weather_edge_v1_c16645cc1165` / `fast_source_prev_no_fok` + `fast_source_taker_10_maker_5_v1`
> evidence layer：Mac/JRS first-seen raw + `runtime/weather.db`

## 结论与动作

Tokyo cross-NO 的历史表面命中率高，但不足以支持继续 live。已结算 first-cross signal 为 `50/51 = 98.0%`；按每个 target date 只看最后一档 cross，降为 `10/11 = 90.9%`。唯一错误是 2026-07-15：JMA `33.5°C -> 34`，但 Polymarket/WU 最终仍为 33。2026-07-21 的 JMA `34.6°C -> 35`、当前 settlement-facing METAR max 34 与它是同型风险，尚未结算。

实际已结算 live 为 7 个 market / 8 fills，全部获胜，fee-adjusted PnL `+$7.2910`、cash cost `$50.1584`、ROI `+14.53%`。但 2026-07-21 新增 15 shares 34 NO，cost `$13.00`、entry fee `$0.08665`；若最终 34 胜出，累计 live PnL 会变为 `-$5.7957`、ROI `-9.18%`。一次 terminal false cross 会吞掉此前全部利润。

动作：Tokyo exact-bracket cross-NO 不应继续 live；保留 collector/shadow，先修 JMA arithmetic bracket 与 WU/METAR settlement lattice/basis，再做同价 market baseline。此报告不执行生产变更。

```text
significance=FAIL_LOW_SAMPLE; baseline=FAIL; forward=FAIL; conclusion=rejected_for_expression
```

## 数据快照

| 项目 | 值 |
|---|---|
| raw 覆盖 | JRS first-seen source/event 到 2026-07-21 11:16 北京 / 12:16 东京附近 |
| DB `fact_built_at_utc` | `2026-07-21T04:52:30.886705Z` |
| first-cross signal | 58 rows / 12 target dates；51 rows / 11 dates 已结算 |
| quote coverage | 30/58 first-cross rows 有 fresh NO ask |
| policy-eligible | 11 rows；其中 10 settled、1 unsettled |
| actual live | 8 settled fills + 2 unsettled fills；7 settled markets + 1 unsettled market |
| unsettled | 2026-07-21 Tokyo 34 NO，15 shares |
| settlement refresh | 已增量补 2026-07-19、2026-07-20；未全库 rebuild |
| CLOB coverage gate | `gate_pass=true`；DB/raw fill 差异 0；missing order 0；over-cap 0 |
| fee evidence | 全部 Tokyo cross fills：exact 8、estimate 2 |

分析 freshness monitor 对全局 `fact_signal_candidates` 报 snapshot stale warning；本报告的 cross opportunity 分母直接使用当前 JRS first-seen raw，不用该陈旧 snapshot 字段。actual fill/PnL 只读 `fact_trades`。

## Target metric 与固定分母

- unit/grain：每个 `(target_date, source_bracket, prior METAR running max)` 的首次 JMA cross；重复 observation 不重复计数。
- label：`settlement_outcomes` 的 Polymarket/WU exact winning bracket；`T-1 NO` 在 winner bracket 不等于 `T-1` 时获胜。
- price：first-seen fresh NO ask；policy-eligible 要求 raw `live_blockers=[]`。固定反事实只计 10-share immediate taker，fee=`shares*0.05*p*(1-p)`，不把未验证 maker fill 当成交。
- 主指标：settlement accuracy、fee-adjusted actual live PnL/ROI。
- baseline：同一时点 market-implied win probability / 同价无条件 NO 尚未物化，故 baseline FAIL。
- forward：没有独立 frozen split；7/21 是新的 adverse-selected live row，故 forward FAIL。

## Signal funnel

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| raw runner events | source observation | 97 unique Tokyo event rows | 12 | 含同一跨档后的重复 observations |
| first-cross mechanism | cross increment | 58 | 12 | 去重后研究分母 |
| settled first-cross | cross increment | 51 | 11 | 50 win / 1 loss |
| terminal cross | city-day | 11 | 11 | 10 win / 1 loss |

## Evidence funnel

| 层 | grain | rows | dates | coverage gap |
|---|---|---:|---:|---|
| first-seen JMA + prior METAR | cross increment | 58 | 12 | 无 |
| fresh NO ask | cross increment | 30 | 11 | 28 个 cross 无 decision-time fresh ask |
| settlement | cross increment | 51 | 11 | 7/21 未结算 |
| historical policy-eligible | cross increment | 11 | 9 | raw blocker 为空；不是完整全机会盘口分母 |
| actual live filled | market | 8 | 5 | 7 settled + 1 unsettled |

## 正确率

| 口径 | settled rows | wins | accuracy | Wilson 95% CI |
|---|---:|---:|---:|---:|
| 所有 first-cross increments | 51 | 50 | 98.0% | [89.7%, 99.7%] |
| 每日最后一档 terminal cross | 11 | 10 | 90.9% | [62.3%, 98.4%] |
| raw historical policy-eligible | 10 | 10 | 100.0% | [72.2%, 100.0%] |
| actual settled live markets | 7 | 7 | 100.0% | [64.6%, 100.0%] |

第一行不能当策略胜率：同一天早段 previous bracket 会随着全天继续升温而机械获胜，多个 row 高度相关。terminal cross 的 10/11 才更接近每天真正的 source-basis 风险。

## Fee-adjusted trade performance

| slice | opportunities / markets | fills | dates | cash cost | fees | PnL | ROI | target-date bootstrap 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| policy-eligible fixed 10-share taker，settled | 10 | — | 8 | $86.20 | $0.55188 | +$13.2481 | +15.37% | [+9.74%, +24.54%] |
| actual live，settled | 7 | 8 | 4 | $50.1584 | $0.25484 | +$7.2910 | +14.53% | [+8.91%, +21.06%] |
| actual live，若 7/21 的 34 YES 最终胜出 | 8 | 10 | 5 | $63.1584 | $0.34149 entry fees | -$5.7957 | -9.18% | scenario，非 realized |

bootstrap 点估虽为正，但实际 settled 只有 4 个 target dates，低于 `active_days>=10 / settled_fills>=30` 门槛；且没有同分母 market baseline，不能判定 alpha。

## 逐日清单

| target date | first crosses | signal wins | final winner | terminal T-1 NO | terminal win | actual markets | actual settled PnL |
|---|---:|---:|---:|---:|---|---:|---:|
| 2026-07-09 | 6 | 6 | 30 | 29 NO | yes | 0 | $0.0000 |
| 2026-07-10 | 4 | 4 | 30 | 29 NO | yes | 0 | $0.0000 |
| 2026-07-11 | 1 | 1 | 30 | 29 NO | yes | 0 | $0.0000 |
| 2026-07-12 | 3 | 3 | 29 | 28 NO | yes | 0 | $0.0000 |
| 2026-07-13 | 2 | 2 | 28 | 27 NO | yes | 0 | $0.0000 |
| 2026-07-14 | 7 | 7 | 34 | 33 NO | yes | 2 | +$2.1530 |
| 2026-07-15 | 6 | 5 | 33 | 33 NO | **no** | 0 | $0.0000 |
| 2026-07-16 | 6 | 6 | 33 | 32 NO | yes | 0 | $0.0000 |
| 2026-07-17 | 4 | 4 | 32 | 31 NO | yes | 2 | +$2.4598 |
| 2026-07-19 | 5 | 5 | 32 | 31 NO | yes | 2 | +$2.2019 |
| 2026-07-20 | 7 | 7 | 35 | 34 NO | yes | 1 | +$0.4763 |
| 2026-07-21 | 7 | pending | pending | 34 NO | pending | 1 | `[UNSETTLED]` |

## Failure case 与执行选择

- 2026-07-15 terminal false cross：JMA `33.5°C -> round 34`，previous 33 NO 最终输；当时 fresh ask `0.24 x 5.1`，被 `insufficient_top_ask_size` 挡住，没有成交。
- 2026-07-21 同型：JMA `34.6°C -> round 35`，previous 34 NO 在 `0.87 x 10` 可成交并实际买入 15 shares；settlement-facing METAR running max 仍为 34。
- 这形成典型 adverse selection：错误 source-basis event 有深度并成交；之前那次错误事件因偶然缺深度才躲过。不能把 7/15 的 unfilled loss 当策略过滤成功，也不能用历史 7/7 live wins 忽略当前 tail loss。

## 三门与残余风险

| 门 | 结果 | 证据 |
|---|---|---|
| significance | FAIL_LOW_SAMPLE | actual settled 4 dates / 8 fills；低于 10 days / 30 fills |
| same-denominator baseline | FAIL | 未和同一时点 market probability、同价 NO 做 paired residual 比较 |
| forward | FAIL | source→settlement basis 未校准；7/21 暴露重复 terminal false-cross 模式 |

残余风险：JMA alternate sensor 与 WU/METAR settlement basis、arithmetic rounding lattice、first-seen archive 缺口、多个 cross 同日相关性、maker fill/adverse-selection、live sizing 从早期约 5 shares 漂到 15 shares。

最终结论：在 2026-07-09—07-20 的 51 个 settled first-cross rows 上，命中率 98.0%，但按 terminal city-day 只有 90.9%；actual settled live ROI +14.53%，若 7/21 当前 34 NO 最终失败则累计变为 -9.18%。相对同分母 market baseline 的超额 ROI 未建立，forward FAIL，结论 `rejected_for_expression`，动作是 Tokyo cross-NO 转回 shadow。
