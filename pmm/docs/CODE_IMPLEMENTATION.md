# PMM Code Implementation Guide

本文档对应当前代码实现，强调“配置 -> 模块 -> 执行路径”的映射。

## 1. 入口与配置

- 入口：`pmm/main.py`
- 配置类：`pmm/config.py`
- 关键新增配置：
  - `execution_mode`
  - `paper_*`
  - `market_data_source`
  - `ws_*`
  - `mid_price_mode`
  - `join_epsilon`
  - `min_edge`
  - `inventory_sigmoid_k`
  - `alpha_*`
  - `circuit_breaker_*`
  - `min_profitability_spread`
  - `fee_spread_floor`
  - `target_profit_spread`
  - `volatility_spread_coeff`
  - `inventory_risk_spread_coeff`
  - `auto_merge_*`
  - `merge_pending_credit_*`
  - `merge_amount_scale`
  - `PMM_MERGE_PLANS_JSON`

## 2. 主循环代码路径

文件：`pmm/tick_loop.py`

1. 初始化

- `OrderManager(deadband=...)`
- `MetricsLogger(metrics_path)`
- `mid_history`（用于 circuit breaker）

2. 每个 tick 并发拉取

- 执行层状态：
  - `execution_mode=live`：`ToolServiceClient` 拉真实余额/挂单/仓位
  - `execution_mode=paper`：`PaperBroker` 拉本地虚拟余额/挂单/仓位
- 盘口获取：
  - `ws` 模式：优先读 `MarketWsFeed` 本地缓存
  - WS 订阅参数：`type=market`, `assets_ids=[token_id]`, `detail_level`
  - 应用层心跳：定时发送 `"PING"`，处理 `"PONG"`
  - 缓存缺失或过期：回退 `client.get_orderbook(token_id)`
  - `rest` 模式：直接每 tick 拉取
  - `paper` 模式会在同一批真实盘口上执行 `on_market_data()`，本地撮合后刷新账户状态

3. 价格计算

- `PMM_MID_PRICE_MODE=weighted` 时使用 weighted mid（微观价格近似）：
  - `W_Mid = (BidPrice * AskSize + AskPrice * BidSize) / (BidSize + AskSize)`
- `PMM_MID_PRICE_MODE=midpoint` 时使用中点
- 异常时 fallback `get_market(...).outcome_prices`
- 计算 `equity/pnl`

4. Alpha 信号

- OFI：由盘口近端深度计算 `ofi_imbalance`
- 参考市场动量：对 `PMM_ALPHA_REFERENCE_TOKEN_IDS` 计算短窗动量均值
- 若 OFI/动量超阈值，会触发单侧拦截（仅撤单，不再挂该侧）

5. 风控（Circuit Breaker）

- 对每个 token，比较 `mid` 与移动均价偏离：
  - `deviation = abs(mid - moving_average_mid) / moving_average_mid`
- 超阈值后：
  - `cancel_all_orders()`
  - 写 breaker 指标
  - `PMM_CB_HALT=1` 则停止循环

6. 报价与库存

- `_inventory_signal(...)`：
  - 先算净仓/上限得到 `raw_signal`
  - 再经 sigmoid 得到非线性信号
- 先计算 `required_spread`（费用、利润目标、波动、库存风险）
- 若自然盘口 spread 低于盈利阈值，直接不入场
- `compute_quotes(mid, adaptive_spread, inventory, skew_factor)` 得理论报价
- `_anchor_quotes_to_book(...)` 做执行报价：
  - 先贴盘口（`join_epsilon`）
  - 再守公允边界（`min_edge`）
  - 避免无限追单

7. 订单执行

- `OrderManager.diff(...)` 给出 `cancel_ids + create`
- 按需调用：
  - `live`：`cancel_order / cancel_orders / place_limit_order`
  - `paper`：同名本地方法，不发网络请求
- 若被 OFI/动量/盈利阈值拦截，会优先撤该侧订单并跳过下单

8. 指标落盘

- `MetricsLogger.log(...)` 写 `jsonl`

9. 自动 Merge（可选）

- 根据 `PMM_MERGE_PLANS_JSON` 配置对 YES/NO 仓位做周期性 `ctf/merge`
- 满足阈值时调用 `client.merge_positions(...)`
- 结果写入 `merge_actions`
- `paper` 模式下走 `merge_pair(yes_token_id, no_token_id, amount)` 本地释放 USDC
- `live` 模式可启用“在途资金”：
  - merge 提交后，按 `merge_amount / PMM_MERGE_AMOUNT_SCALE` 记临时 `pending_usdc_credit`
  - `effective_usdc_for_sizing = usdc_balance + pending_usdc_credit * ratio`
  - credit 到 `PMM_MERGE_PENDING_CREDIT_TTL_SEC` 后自动失效

## 2.1 Paper Trading 撮合逻辑

文件：`pmm/paper_broker.py`

- 真实行情来源：依然是真实 WS/REST 盘口。
- 本地执行：挂单、撤单、成交、余额、仓位都在内存维护。
- 资金冻结：
  - BUY 下单时冻结 `price * size` USDC
  - SELL 下单时冻结对应 token 仓位
- 撮合判定（可配）：
  - `conservative`：BUY 需 `best_ask <= order_price - epsilon`；SELL 需 `best_bid >= order_price + epsilon`
  - `optimistic`：BUY 需 `best_ask <= order_price + epsilon`；SELL 需 `best_bid >= order_price - epsilon`
- 单 tick 成交量：`top_of_book_size * queue_share` 上限，模拟排队劣后

示例：

```json
[
  {
    "yes_token_id": "123...",
    "no_token_id": "456...",
    "condition_id": "0xabc...",
    "partition": [1, 2],
    "min_amount": 1000000,
    "collateral_token": "0x2791...",
    "parent_collection_id": "0x0000..."
  }
]
```

## 3. OrderManager 行为

文件：`pmm/order_manager.py`

- 解析远端 open orders，兼容常见字段名（`id/orderID/order_id` 等）。
- 同 token+side 只保留一个“锚订单”比较。
- 超过 deadband 才替换，避免频繁改价丢队列。

## 4. 指标字段定义

输出文件：`pmm_logs/metrics.jsonl`

- 基础：
  - `ts`, `tick`, `usdc_balance`, `effective_usdc_for_sizing`, `pending_usdc_credit`, `positions`, `net_inventory`
  - `mids`, `spreads`, `equity`, `pnl`
  - `open_orders_count`, `placed`, `canceled`, `errors`
- 执行/策略可观测：
  - `inventory_signals`
  - `target_quotes`（理论价）
  - `final_quotes`（执行价）
  - `ofi_imbalances`
  - `realized_volatility`
  - `required_spreads`
  - `adaptive_spreads`
  - `alpha_reference_momentum`
  - `side_blocks`
  - `merge_actions`
  - `circuit_breaker_triggered`
  - `circuit_breaker_reasons`

## 5. 工程边界（当前版本）

- `pnl` 仍是 mark-to-market，非 realized PnL。
- WebSocket 事件字段在不同环境可能有差异，当前实现做了兼容解析与 REST 回退。
- 当前只实现了 market 公共频道，未实现 user 私有频道（order/fill 私有流）。
- 当前只实现自动 `merge`，未实现自动 `split/redeem`。
- `paper` 撮合仍是简化模型，不含严格队列位置回放与成交时间分布。
