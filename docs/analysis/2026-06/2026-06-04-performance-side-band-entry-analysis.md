# Side-band 策略入场差异分析（同模型口径）

## 数据快照

- 目标指标：`side_band_same_model_entry_delta` = 优先在 `trade_class='live_real'` 下比较 realized PnL；若 live_real 因 CLOB fill sync 缺失不可用，则降级到 synced live `plans/` 与 `live/` JSONL，在同一模型、core 9 城、同目标日期窗口下比较 submitted order 的入场价、edge、quote spread，并用 `fact_signal_candidates` 做 entry band gate 诊断。
- 数据源：`runtime/weather.db.fact_trades` + `runtime/weather.db.fact_signal_candidates`。
- DB last modified：2026-06-04T23:26:20+08:00。
- fact built：2026-06-04T15:25:56.499187+00:00。
- 同模型范围：`model_version IN (ecmwf)`。
- 公平窗口：core 9 城，`target_date=2026-06-01..2026-06-01`，因为这是 side-band 实际成交目标日期窗口。
- 记录行数：fact_trades 3657 rows；settled 2908。
- 降级口径：side-band scoped accepted plans 4 / submitted live orders 4；v1 25-75 scoped accepted plans 4 / submitted live orders 4。
- unsettled/null 占比：125 / 3657 = +3.4%。
- missing_bracket 数：624。
- fact_signal_candidates：20144 rows；eligible 6217；decision_window_missing 8862 (+44.0%)。

完整性自检：

| check | value |
| --- | --- |
| settled rows with null PnL | 0 |
| live_real rows / settled | 67 / 51 |
| live_simulated rows / settled | 669 / 486 |
| paper rows / settled | 2285 / 1751 |
| snapshot_replay rows / settled | 636 / 620 |

结算 join/status Top 12：

| join_method | status | rows |
| --- | --- | --- |
| fallback | settled | 2844 |
| fallback | missing_bracket | 567 |
| none | null | 125 |
| token | settled | 64 |
| token | missing_bracket | 57 |

## 结论先行

交易动作：**side-band 目前不应扩大 live size；应该继续 shadow/极小 size。** 当前 DB 已恢复出部分 `live_real`，但同模型/core 9/side-band 活跃窗口内的公平成交样本仍太少，不能用这批数据判断 realized EV。
入场行为上，side-band 已经把样本压得很窄：同 ecmwf/gfs、core 9、同 target_date 窗口里，它主要提交 BUY_NO 的 35-65c 中价带订单；YES 需要更高 edge，submitted 样本几乎被压没。这符合配置意图，但意味着继续原样跑很慢才会有统计功效。
## Fair Live Fill 对照

`fact_trades` 当前有 live_real=67，但同模型/core 9/side-band 活跃 target_date 窗口内没有可比 live_real fill。因此本节不能给公平 realized PnL。

_No rows._

## Submitted Order 入场对照（降级口径）

| strategy | layer | n | cities | days | models | side_mix | avg_entry | avg_market | avg_edge | avg_quote_edge | avg_quote_spread |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 25-75 | accepted plans | 4 | 2 | 1 | ecmwf | BUY_NO:3,BUY_YES:1 | 0.570 | 0.574 | 0.198 | 0.202 | 0.000 |
| v1 25-75 | submitted live orders | 4 | 2 | 1 | ecmwf | BUY_NO:3,BUY_YES:1 | 0.569 | 0.574 | 0.198 | 0.202 | 0.013 |
| side-band | accepted plans | 4 | 2 | 1 | ecmwf | BUY_NO:3,BUY_YES:1 | 0.500 | 0.501 | 0.237 | 0.238 | 0.000 |
| side-band | submitted live orders | 4 | 2 | 1 | ecmwf | BUY_NO:3,BUY_YES:1 | 0.500 | 0.501 | 0.237 | 0.238 | 0.018 |

| strategy | model | side | orders | cities | days | avg_entry | entry_range | avg_market | avg_edge | avg_quote_edge | avg_quote_spread |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 25-75 | ecmwf | BUY_NO | 3 | 2 | 1 | 0.635 | 0.590-0.726 | 0.640 | 0.220 | 0.223 | 0.011 |
| v1 25-75 | ecmwf | BUY_YES | 1 | 1 | 1 | 0.370 | 0.370-0.370 | 0.375 | 0.132 | 0.137 | 0.020 |
| side-band | ecmwf | BUY_NO | 3 | 2 | 1 | 0.597 | 0.580-0.620 | 0.598 | 0.241 | 0.243 | 0.017 |
| side-band | ecmwf | BUY_YES | 1 | 1 | 1 | 0.210 | 0.210-0.210 | 0.210 | 0.224 | 0.224 | 0.020 |

side-band submitted live orders：

| target_date | created_utc | city | model | side | bracket | posted | market | edge | quote_edge | spread | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-01 | 2026-05-31T17:11:32+00:00 | Warsaw | ecmwf | BUY_NO | 22 | 0.590 | 0.590 | 0.292 | 0.292 | 0.010 | submitted |
| 2026-06-01 | 2026-05-31T18:42:05+00:00 | London | ecmwf | BUY_NO | 22 | 0.580 | 0.580 | 0.130 | 0.130 | 0.030 | submitted |
| 2026-06-01 | 2026-05-31T20:12:47+00:00 | London | ecmwf | BUY_NO | 21 | 0.620 | 0.625 | 0.302 | 0.307 | 0.010 | submitted |
| 2026-06-01 | 2026-05-31T23:13:38+00:00 | London | ecmwf | BUY_YES | 23 | 0.210 | 0.210 | 0.224 | 0.224 | 0.020 | submitted |

## 按模型和方向

_No rows._

## 入场分布

| strategy_instance | field | n | min | p25 | avg | p75 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mid_price_core_v1_25_75 | fill_price | 0 | - | - | - | - | - |
| mid_price_core_v1_25_75 | edge | 0 | - | - | - | - | - |
| mid_price_core_v1_25_75 | abs_edge | 0 | - | - | - | - | - |
| mid_price_core_v1_25_75 | hours_to_settle | 0 | - | - | - | - | - |
| mid_price_core_v1_side_band | fill_price | 0 | - | - | - | - | - |
| mid_price_core_v1_side_band | edge | 0 | - | - | - | - | - |
| mid_price_core_v1_side_band | abs_edge | 0 | - | - | - | - | - |
| mid_price_core_v1_side_band | hours_to_settle | 0 | - | - | - | - | - |

## side-band 实际成交明细

| target_date | order_date_bj | city | model | side | bracket | fill | plan | market | edge | abs_edge | hts | cost | pnl | status | final_yes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-01 | 2026-06-04 | Karachi | ecmwf | BUY_NO | 35 | 0.530 | 0.530 | 0.535 | 0.258 | 0.258 | 34.5 | 2.13 | +1.89 | settled | 0.0 |
| 2026-06-01 | 2026-06-04 | Lucknow | ecmwf | BUY_NO | 34 | 0.640 | 0.640 | 0.640 | 0.196 | 0.196 | 35.5 | 4.44 | +2.50 | settled | 0.0 |

## 候选机会层：entry band gate 差异

这段是机会粒度诊断，不是成交 PnL。分母只取 `final_yes IS NOT NULL AND decision_window_missing=0`，并在同模型、core 9、同日期窗口内用配置规则重放 entry band gate：v1 = `0.25-0.75 + abs_edge>=0.10`；side-band YES = `0.20-0.45 + abs_edge>=0.20`，NO = `0.35-0.65 + abs_edge>=0.10`。
_No rows._

## live_cycle 链路统计

| layer | count |
| --- | --- |
| side-band runs | 149 |
| records scanned | 335724 |
| candidate_signals | 266 |
| signals | 266 |
| accepted before live dedup | 259 |
| accepted after live dedup | 32 |
| live_orders | 31 |
| paper_written | 31 |

主要 filter / skip：

| reason | count |
| --- | --- |
| city_pool_not_t1_trading | 174829 |
| city_not_allowed | 80273 |
| hours_to_settle_above_max | 34534 |
| hours_to_settle_below_min | 32211 |
| edge_below_min | 11192 |
| entry_price_at_or_above_max | 1240 |
| entry_price_below_min | 695 |
| older_duplicate_candidate | 484 |

最新配置摘要：

| key | value |
| --- | --- |
| allowed_cities | Boston,LA,London,Miami,NYC,Phoenix,Shanghai,Tokyo,Warsaw |
| execution_policy | mid_price_core_v1 |
| min/max hours | 22.0-28.0 |
| YES band / edge | 0.2-0.45 / 0.2 |
| NO band / edge | 0.35-0.65 / 0.1 |

## 建议

1. **不扩大 side-band live。** 当前没有可用 live_real PnL，不能因为 submitted order 的入场更漂亮就加 size。
2. **把实验从 live PnL 判断改成 entry gate 判断。** 先看同模型 candidate universe 中 `v1_only / side_only / both` 的 settled 反事实表现，等 pass_side 的可估值机会至少达到几十个 city-day 后再回到 live fill PnL。
3. **优先放宽一个维度而不是同时改多处。** 如果想提高样本，最干净的是保留 core 9 和 22-28h，先把 YES edge 从 0.20 降到 0.15；NO 35-65c 可以先不动，因为它是这版 side-band 最核心的风险控制。
4. **v1 主路径暂不因 side-band 改动。** side-band 当前更像一个入场过滤实验，不足以替代 v1 25-75。
