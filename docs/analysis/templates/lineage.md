<!--
  M2 单日血缘报告模板
  按问题 grain 组织标题；单笔可直接回答，多单明细放可追溯产物，不强制复制整张表。
  异常订单段不得留空——若无异常，写"无"。
  输出到：docs/analysis/YYYY-MM/YYYY-MM-DD-lineage-<target_date>.md
  口径：docs/WEATHER_ANALYSIS_CONTRACT.md
-->

# 血缘复盘：{order_id / target_date / scope}

> 目标日期：{target_date}  
> 策略：{strategy_id}  
> 城市池：{city_pool}  
> 数据源：{data_source}

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源路径 | {source_path} |
| 数据快照时间 | {snapshot_ts} |
| 信号数（signals） | {signal_count} |
| 计划数（plans） | {plan_count} |
| 订单数（orders） | {order_count} |
| 成交数（filled fills） | {fill_count} |
| 结算数（settled） | {settled_count} |
| 未结算数 | {unsettled_count} |
| missing_bracket | {missing_bracket_n} |

## 当日策略身份与配置

| 字段 | 值 |
|---|---|
| strategy_id | |
| code_version | |
| sizing_mode | |
| city_pool | |
| run_id | |

## 全链路表（按 city 分组）

<!-- 每个城市复制以下小节，按 city 名称排序 -->

### {city}（{city_pool}）

| 阶段 | 字段 | 值 |
|---|---|---|
| **Event / Decision** | event_id / checkpoint_id / decision_context_id | |
| **Model / Candidate** | model/artifact/config identity / candidate_id | |
| **Intent / Handoff** | intent_id / execution_id / blocker | |
| **Signal（legacy）** | signal_id / adapter / missing layers | |
| | snapshot_ts（北京时间） | |
| | forecast_source（model） | |
| | target_date | |
| **Plan** | plan_id | |
| | order_side | |
| | entry_price（plan_price） | |
| | notional | |
| | desired_shares | |
| **Order** | execution_id | |
| | venue | |
| | requested / posted price | |
| | submitted shares / notional | |
| | limit_price | |
| | created_at（北京时间 / 当地时间） | |
| | maker/taker / exchange status | |
| | scoped cap / gate state | |
| | rejection text | |
| **Fill** | fill_id | |
| | filled_price | |
| | filled_shares（fill_qty） | |
| | fees_usd / fee evidence | |
| | fill_status | |
| | effective fill time / timestamp evidence / discovery time | |
| **Settlement** | final_price（settlement_yes_price） | |
| | settlement_status | |
| | bracket | |
| **PnL** | pnl_usd（fill 口径） | |
| | pnl_usd（plan 口径） | |

同一 order 有 partial fills 时逐段列出，并给 authenticated `size_matched` 与累计 fill 对账。不能把 submitted size 当 fill size。

## 异常订单列表

<!-- 若无异常，写"无" -->

| city | 异常类型 | 说明 |
|---|---|---|
| | missing_bracket | |
| | plan/fill 偏差及适用的 execution policy 判据 | |
| | 未成交原因（resting/cancel/reject；不自动视为异常） | |
| | 其他 | |

## 当日 PnL 汇总

| 指标 | 已结算（fill 口径） | 已结算（plan 口径） |
|---|---|---|
| 总 PnL (USD) | | |
| 总 fills | | |
| Win rate（by count） | | |

## 数据完整性自检

- [ ] 每层计数注明 grain；未进入 plan 的 candidate/signal 列出 blocker 或 record-only 依据，不要求数量相等
- [ ] plan 未发单、reprice/replacement 与 partial fills 分别可追溯，不要求 plan/order/fill 一对一
- [ ] 无 orphan fills（fill 有 execution_id 但 order 不存在）
- [ ] 列出无法结算城市（missing_bracket / missing_event）—— 若无写"无"

## 观察与建议

<!-- agent 自由发挥，不超过 500 字 -->
