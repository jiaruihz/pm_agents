# Signal Ledger 权威明细与 84/86 差异核对(D1)

生成:2026-08-21(数据 as-of:canonical DB 与 runtime journal 读取时刻;runner 仍在运行,pre_live_scores 已由任务描述的 4708 行增至 4735 行)。
范围:`current_yes_core_carry_tiny_live_v2`(strategy_id `current_yes_core_carry_v3`),2026-07-25 至 2026-08-20。
全部数据只读:canonical `/Volumes/jrs/pm_agents/runtime/weather.db`(mode=ro URI)+ runtime journal `/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/`。

## 1. 结论:84/86 差异答案

**外部审阅解析 84 行是对的;“86 行”是我们自己的标签/计数错误,不存在被丢掉的两行数据。**

证据链:

1. consult prompt(`docs/analysis/2026-08/2026-08-20-core-carry-research-status-and-consult-prompt-v1.md` §3)标题声称“全部 signal 明细(86 行)”,但逐行解析该代码块实际只有 **84 个数据行**,与外部审阅的 84 行一致。
2. 这 84 行的 (city, target_date) 集合与 canonical `fact_trades`(instance_id=`current_yes_core_carry_tiny_live_v2`)的 84 个 distinct signal **完全一一对应,零差集**。
3. “86”的最可能来源:84 个已成交 signal + 备注中提到的“两个高 p 评分触发未成交”(Miami 2026-08-13 p=0.994、Warsaw 2026-08-11 p=0.976)。这两条在正文备注里被提及,但**从未作为行写入表格**;标题计数把它们算进了“全部 signal”口径却没落到行。
4. 真实全量口径(见 §2):**92 个触发 signal**,其中 84 成交、8 触发未成交。86 既不是成交口径也不是触发口径。

## 2. 权威账本口径(signal_ledger.csv,92 行)

- grain:signal = (city, target_date) 首次 `positive_taker_ev` 触发(pre_live_scores 每个 city-day 恰好 1 行触发行,无重复触发)。
- 成交/结算字段以 canonical `fact_trades` 为准;触发行提供 p/ask/bid/decision_hour。
- 分腿:taker = `execution_policy LIKE '%taker%'`(即 `current_yes_residual_carry_taker_v1`);maker 腿 = maker_v1/maker_v2/staged_maker_v3/pullback_maker_v1。
- `pnl_usd_at_fill_total` = 该 signal 全部 fill 行 `pnl_usd_at_fill` 之和(settled 口径);未结算行为空,不自行估值。

### 92 行构成

| 类别 | 行数 | 说明 |
|---|---|---|
| settled W | 70 | pnl 合计 +$54.50 |
| settled L | 5 | Singapore/Chengdu/Lucknow/Manila/Busan,pnl 合计 −$47.98 |
| pending_unsettled | 9 | canonical `settlement_status=missing_bracket`(08-18 与 08-20 两天,含外部已确认输的 Warsaw 08-18、Amsterdam 08-20) |
| triggered_no_fill | 8 | 触发且下了 live order 但全部 error,canonical 无成交 |

### 粘贴表 76W/7L/1? 与 canonical 的映射

- 76W = 70 个 settled W + 6 个 pending 被外部判 W(Wellington/Taipei/Karachi/Amsterdam 08-18、Shanghai/Jeddah 08-20)。
- 7L = 5 个 settled L + 2 个 pending 被外部判 L(Warsaw 08-18、Amsterdam 08-20;链上确认输,canonical 未结算)。
- 1? = Manila 08-20。
- 即:粘贴表的 W/L 标签混用了 canonical 结算与外部链上核对两种来源,表内未标注。本 ledger 将两者分开(`outcome` 只用 canonical;外部确认记在 packet 的 outcome.note)。

## 3. 一对一核对结果(全部通过)

- 触发行 (city,target_date,bracket) vs canonical:84/84 bracket 一致、84/84 condition_id 一致;无重复 signal_id、无 canonical 多 signal 映射同一 city-day。
- **触发行有评分但 canonical 无成交(8 个)**,taker 单全部 status=error、risk=passed、`place={}`(未到达交易所):

| city | date | p | ask | taker 单 quote_reason |
|---|---|---|---|---|
| Karachi | 2026-07-29 | 0.933 | 0.91 | order_request_builder_rejected |
| Wellington | 2026-08-02 | 0.860 | 0.85 | fresh_book_replan_rejected |
| Warsaw | 2026-08-11 | 0.976 | 0.97 | order_request_builder_rejected |
| Shanghai | 2026-08-13 | 0.921 | 0.91 | post_order_dispatch_unknown |
| BuenosAires | 2026-08-13 | 0.870 | 0.86 | post_order_dispatch_unknown |
| Miami | 2026-08-13 | 0.994 | 0.99 | post_order_dispatch_unknown |
| SanFrancisco | 2026-08-13 | 0.963 | 0.96 | post_order_dispatch_unknown |
| Karachi | 2026-08-14 | 0.938 | 0.91 | order_request_builder_rejected |

  Miami 2026-08-13 p=0.994 即已知案例。注意:08-13 一天占 4 个,均为 `post_order_dispatch_unknown`(quote_status=rejected,place 响应为空),建议单独归因(疑似当日订单通道/共享 runtime 问题)。
- **canonical 有成交但触发行缺失:0 个。**

## 4. 7 个亏损 signal 核对(p/ask 均取自触发行,canonical 复核通过)

| city | date | bracket | model_p | entry ask | taker 腿 | maker 腿 | settlement | pnl |
|---|---|---|---|---|---|---|---|---|
| Singapore | 07-27 | 31 | 0.8755 | 0.85 | 5 @ 0.850 | 10 @ 0.800 | settled, final_yes=0 | −12.28 |
| Chengdu | 07-27 | 29 | **0.9885** | 0.92 | 10 @ 0.930 | 5 @ 0.910 | settled, final_yes=0 | −13.88 |
| Lucknow | 07-31 | 32 | 0.8708 | 0.85 | 10 @ 0.850 | — | settled, final_yes=0 | −8.56 |
| Manila | 08-07 | 28 | 0.9192 | 0.90 | — | 5 @ 0.830 | settled, final_yes=0 | −4.15 |
| Busan | 08-17 | 25 | 0.9167 | 0.897 | 10 @ 0.906 | — | settled, final_yes=0 | −9.10 |
| Warsaw | 08-18 | 20 | 0.8791 | 0.85 | 10 @ 0.850 | 5@0.83+5@0.82 | missing_bracket(链上已确认输) | 未结算 |
| Amsterdam | 08-20 | 21 | 0.8915 | 0.86 | 10 @ 0.860 | 5 @ 0.840 | missing_bracket(链上已确认输) | 未结算 |

- **Chengdu p 区间修正**:7 个亏损的 p 真实区间是 **[0.8708, 0.9885]**(Chengdu 0.9885 在区间内)。此前“7/7 亏损 p 都在 0.871–0.917”的表述把 Chengdu 0.989 错误排除在区间外——外部审阅指出的冲突属实,应以本表为准。
- 入场 ask ≤ 0.92 的说法复核成立:7/7 亏损 ask ∈ [0.85, 0.92]。

## 5. 字段口径声明(signal_ledger.csv 列)

| 列 | 口径 |
|---|---|
| signal_id | canonical `fact_trades.signal_id`;未成交行为空 |
| bracket_trigger / bracket_canonical / bracket_match | 触发行 `current_bracket` vs canonical bracket,一致性核对本报告 §3 |
| trigger_ts_utc | 触发行 `created_at_utc`(journal 写入时刻);另有 decision_snapshot_ts_utc 列(数据时点) |
| decision_hour_local | 触发行 `decision_hour_local`(当地时区小数小时) |
| model_p | 触发行 `model_probability_hold`(冻结 logistic 的 hold 概率) |
| entry_ask / entry_bid | 触发行 `current_yes_ask` / `current_yes_bid`(评分时盘口快照) |
| taker_fill_shares / taker_fill_vwap | canonical 中 taker policy fill 的股数与金额加权均价 |
| maker_fill_shares / maker_fill_vwap | canonical 中 maker 家族 policy fill 的股数与均价 |
| settlement_status / final_yes / pnl_usd_at_fill_total | canonical `fact_trades`;未结算为 missing_bracket/空 |
| outcome | canonical 口径:W/L(settled)、pending_unsettled、triggered_no_fill;不用外部链上判断 |
| unfilled_quote_status / unfilled_quote_reason | live_orders.jsonl taker 单 status 与 exchange_response.quote_reason |
| settlement_source | 固定 `canonical:fact_trades` |

已知局限:(a) `model_p`/ask 是评分快照,非下单瞬间重估的盘口(下单链路有 fresh book recheck,见 live_orders `quote_mode`);(b) pending 行 pnl 留空,不自行 MTM。
