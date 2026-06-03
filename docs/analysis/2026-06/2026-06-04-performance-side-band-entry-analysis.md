# Side-band 策略入场差异分析（同模型口径）

## 数据快照

- 目标指标：`side_band_same_model_entry_delta` = 优先在 `trade_class='live_real'` 下比较 realized PnL；若 live_real 因 CLOB fill sync 缺失不可用，则降级到 synced live `plans/` 与 `live/` JSONL，在同一模型、core 9 城、同目标日期窗口下比较 submitted order 的入场价、edge、quote spread，并用 `fact_signal_candidates` 做 entry band gate 诊断。
- 数据源：`runtime/weather.db.fact_trades` + `runtime/weather.db.fact_signal_candidates`。
- DB last modified：2026-06-04T00:11:40+08:00。
- fact built：2026-06-03T16:11:22.750728+00:00。
- 同模型范围：`model_version IN (ecmwf, gfs)`（来自 synced side-band plan/order；fact_trades live_real 为空）。
- 公平窗口：core 9 城，`target_date=2026-06-01..2026-06-04`，因为这是 side-band 实际成交目标日期窗口。
- 记录行数：fact_trades 3590 rows；settled 2857。
- 降级口径：side-band scoped accepted plans 22 / submitted live orders 22；v1 25-75 scoped accepted plans 36 / submitted live orders 36。
- unsettled/null 占比：125 / 3590 = +3.5%。
- missing_bracket 数：608。
- fact_signal_candidates：20144 rows；eligible 6217；decision_window_missing 8862 (+44.0%)。

完整性自检：

| check | value |
| --- | --- |
| settled rows with null PnL | 0 |
| live_simulated rows / settled | 669 / 486 |
| paper rows / settled | 2285 / 1751 |
| snapshot_replay rows / settled | 636 / 620 |

结算 join/status Top 12：

| join_method | status | rows |
| --- | --- | --- |
| fallback | settled | 2802 |
| fallback | missing_bracket | 559 |
| none | null | 125 |
| token | settled | 55 |
| token | missing_bracket | 49 |

## 结论先行

交易动作：**side-band 目前不应扩大 live size；应该继续 shadow/极小 size。** 这次 DB rebuild 后没有 `live_real` 行，真实成交 PnL 不可用；只能评价入场行为，不能评价 realized EV。
入场行为上，side-band 已经把样本压得很窄：同 ecmwf/gfs、core 9、同 target_date 窗口里，它主要提交 BUY_NO 的 35-65c 中价带订单；YES 需要更高 edge，submitted 样本几乎被压没。这符合配置意图，但意味着继续原样跑很慢才会有统计功效。
## Fair Live Fill 对照

`fact_trades` 当前没有 `live_real` 行；CLOB activity/trades API 在 rebuild 时 connection reset，593 个 submitted orders 全被标成 still_open。因此本节不能给 realized PnL。

_No rows._

## Submitted Order 入场对照（降级口径）

| strategy | layer | n | cities | days | models | side_mix | avg_entry | avg_market | avg_edge | avg_quote_edge | avg_quote_spread |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 25-75 | accepted plans | 36 | 7 | 4 | ecmwf,gfs | BUY_NO:22,BUY_YES:14 | 0.521 | 0.524 | 0.198 | 0.201 | 0.000 |
| v1 25-75 | submitted live orders | 36 | 7 | 4 | ecmwf,gfs | BUY_NO:22,BUY_YES:14 | 0.516 | 0.524 | 0.198 | 0.201 | 0.025 |
| side-band | accepted plans | 22 | 7 | 4 | ecmwf,gfs | BUY_NO:15,BUY_YES:7 | 0.489 | 0.492 | 0.263 | 0.266 | 0.000 |
| side-band | submitted live orders | 22 | 7 | 4 | ecmwf,gfs | BUY_NO:15,BUY_YES:7 | 0.486 | 0.492 | 0.263 | 0.266 | 0.023 |

| strategy | model | side | orders | cities | days | avg_entry | entry_range | avg_market | avg_edge | avg_quote_edge | avg_quote_spread |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 25-75 | ecmwf | BUY_NO | 7 | 2 | 3 | 0.651 | 0.590-0.726 | 0.659 | 0.208 | 0.211 | 0.017 |
| v1 25-75 | ecmwf | BUY_YES | 4 | 2 | 3 | 0.328 | 0.300-0.370 | 0.344 | 0.127 | 0.131 | 0.028 |
| v1 25-75 | gfs | BUY_NO | 15 | 5 | 4 | 0.629 | 0.490-0.730 | 0.636 | 0.236 | 0.239 | 0.024 |
| v1 25-75 | gfs | BUY_YES | 10 | 4 | 4 | 0.328 | 0.220-0.430 | 0.334 | 0.163 | 0.165 | 0.031 |
| side-band | ecmwf | BUY_NO | 5 | 2 | 3 | 0.610 | 0.580-0.640 | 0.613 | 0.230 | 0.233 | 0.016 |
| side-band | ecmwf | BUY_YES | 2 | 2 | 2 | 0.195 | 0.180-0.210 | 0.205 | 0.232 | 0.232 | 0.015 |
| side-band | gfs | BUY_NO | 10 | 5 | 4 | 0.584 | 0.490-0.640 | 0.591 | 0.261 | 0.263 | 0.026 |
| side-band | gfs | BUY_YES | 5 | 3 | 2 | 0.284 | 0.210-0.400 | 0.289 | 0.313 | 0.318 | 0.028 |

side-band submitted live orders：

| target_date | created_utc | city | model | side | bracket | posted | market | edge | quote_edge | spread | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-01 | 2026-05-31T17:11:32+00:00 | Warsaw | ecmwf | BUY_NO | 22 | 0.590 | 0.590 | 0.292 | 0.292 | 0.010 | submitted |
| 2026-06-01 | 2026-05-31T18:42:05+00:00 | London | ecmwf | BUY_NO | 22 | 0.580 | 0.580 | 0.130 | 0.130 | 0.030 | submitted |
| 2026-06-01 | 2026-05-31T20:12:47+00:00 | London | ecmwf | BUY_NO | 21 | 0.620 | 0.625 | 0.302 | 0.307 | 0.010 | submitted |
| 2026-06-01 | 2026-05-31T23:13:33+00:00 | Miami | gfs | BUY_YES | 88-89 | 0.210 | 0.215 | 0.367 | 0.372 | 0.030 | submitted |
| 2026-06-01 | 2026-05-31T23:13:35+00:00 | Miami | gfs | BUY_NO | 90-91 | 0.510 | 0.520 | 0.205 | 0.205 | 0.010 | submitted |
| 2026-06-01 | 2026-05-31T23:13:36+00:00 | NYC | gfs | BUY_YES | 70-71 | 0.320 | 0.325 | 0.202 | 0.206 | 0.040 | submitted |
| 2026-06-01 | 2026-05-31T23:13:37+00:00 | NYC | gfs | BUY_NO | 72-73 | 0.630 | 0.630 | 0.137 | 0.137 | 0.040 | submitted |
| 2026-06-01 | 2026-05-31T23:13:38+00:00 | London | ecmwf | BUY_YES | 23 | 0.210 | 0.210 | 0.224 | 0.224 | 0.020 | submitted |
| 2026-06-01 | 2026-06-01T01:44:14+00:00 | Miami | gfs | BUY_YES | 92-93 | 0.230 | 0.235 | 0.379 | 0.384 | 0.010 | submitted |
| 2026-06-01 | 2026-06-01T02:14:26+00:00 | LA | gfs | BUY_YES | 70-71 | 0.400 | 0.405 | 0.215 | 0.220 | 0.040 | submitted |
| 2026-06-01 | 2026-06-01T02:14:27+00:00 | LA | gfs | BUY_NO | 72-73 | 0.640 | 0.645 | 0.281 | 0.286 | 0.040 | submitted |
| 2026-06-02 | 2026-06-01T09:01:33+00:00 | Tokyo | gfs | BUY_NO | 25 | 0.570 | 0.570 | 0.112 | 0.112 | 0.020 | submitted |
| 2026-06-02 | 2026-06-01T19:33:56+00:00 | London | ecmwf | BUY_NO | 20 | 0.640 | 0.645 | 0.293 | 0.298 | 0.010 | submitted |
| 2026-06-02 | 2026-06-01T20:34:15+00:00 | Warsaw | ecmwf | BUY_YES | 22 | 0.180 | 0.200 | 0.239 | 0.239 | 0.010 | submitted |
| 2026-06-02 | 2026-06-02T02:05:25+00:00 | LA | gfs | BUY_NO | 70-71 | 0.600 | 0.605 | 0.360 | 0.365 | 0.060 | submitted |
| 2026-06-03 | 2026-06-02T09:06:59+00:00 | Tokyo | gfs | BUY_NO | 23 | 0.610 | 0.610 | 0.292 | 0.292 | 0.030 | submitted |
| 2026-06-03 | 2026-06-02T21:10:20+00:00 | London | ecmwf | BUY_NO | 18 | 0.620 | 0.625 | 0.133 | 0.138 | 0.020 | submitted |
| 2026-06-03 | 2026-06-02T23:10:47+00:00 | NYC | gfs | BUY_NO | 80-81 | 0.620 | 0.625 | 0.224 | 0.229 | 0.010 | submitted |
| 2026-06-03 | 2026-06-03T02:11:24+00:00 | LA | gfs | BUY_NO | 70-71 | 0.490 | 0.520 | 0.456 | 0.456 | 0.020 | submitted |
| 2026-06-03 | 2026-06-03T03:11:41+00:00 | LA | gfs | BUY_YES | 68-69 | 0.260 | 0.265 | 0.403 | 0.408 | 0.020 | submitted |
| 2026-06-04 | 2026-06-03T09:13:05+00:00 | Tokyo | gfs | BUY_NO | 23 | 0.540 | 0.545 | 0.391 | 0.396 | 0.020 | submitted |
| 2026-06-04 | 2026-06-03T10:43:27+00:00 | Shanghai | gfs | BUY_NO | 29 | 0.630 | 0.635 | 0.149 | 0.154 | 0.010 | submitted |

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

_No rows._

## 候选机会层：entry band gate 差异

这段是机会粒度诊断，不是成交 PnL。分母只取 `final_yes IS NOT NULL AND decision_window_missing=0`，并在同模型、core 9、同日期窗口内用配置规则重放 entry band gate：v1 = `0.25-0.75 + abs_edge>=0.10`；side-band YES = `0.20-0.45 + abs_edge>=0.20`，NO = `0.35-0.65 + abs_edge>=0.10`。
| model | side | evaluable | pass_v1 | pass_side | both | v1_only | side_only | v1_cf_pnl | side_cf_pnl | v1_avg_entry | side_avg_entry | v1_abs_edge | side_abs_edge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gfs | BUY_YES | 1 | 0 | 0 | 0 | 0 | 0 | +0.00 | +0.00 | - | - | - | - |

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
