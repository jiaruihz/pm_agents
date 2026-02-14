# 代码实现说明

各模块的代码走读，按数据流顺序。

## 模块依赖

```
main.py
  └─ engine/tick_engine.py (orchestration)
       ├─ config.py                   全局配置 (env)
       ├─ engine/context_builder.py   运行上下文构建
       ├─ core/
       │    ├─ signals.py             信号计算
       │    ├─ sizing.py              仓位计算
       │    ├─ anchoring.py           报价锚定
       │    ├─ pricing.py             报价公式
       │    ├─ strategy_base.py       策略协议
       │    └─ strategy_registry.py   策略注册
       ├─ data/
       │    ├─ market_ws.py           WS 行情 + L2 book
       │    ├─ http_client.py         REST API
       │    ├─ orderbook.py           orderbook 工具
       │    └─ parsers.py             数据解析
       ├─ execution/
       │    ├─ broker_interface.py    抽象接口
       │    ├─ live_broker.py         实盘执行
       │    ├─ paper_broker.py        纸盘撮合
       │    └─ order_manager.py       diff + deadband
       ├─ risk/
       │    ├─ safety_guard.py        风控检查
       │    └─ circuit_breaker.py     熔断器
       ├─ utils/
       │    ├─ converters.py          类型转换
       │    ├─ quantize.py            价格 tick 对齐
       │    └─ metrics.py             日志写入
       └─ strategies/
            ├─ single_level_v1.py     单档策略
            └─ multi_level_v1.py      多档策略
```

---

## 1. 入口 — `main.py`

```python
config = PMMConfig.from_env()
asyncio.run(TickEngine(config).run())
```

所有参数从环境变量加载。`PMMConfig` 是一个 dataclass，`from_env()` 做 `os.getenv → 类型转换`。

## 2. 行情 — `market_ws.py` / `http_client.py`

两条数据路径，由 `market_data_source` 决定：

| 路径 | 延迟 | 成本 |
|------|------|------|
| `ws` | 推送级（~ms） | 需维护长连接、处理重连 |
| `rest` | 轮询（= tick 间隔） | 简单但每 tick 3-4 个 HTTP 请求 |

**WS 路径** (`MarketWsFeed`)：
- 订阅 `type=market, assets_ids=[...], detail_level=agg`
- `LocalOrderBookStore` 在内存维护每个 token 的 L2 book（price→size dict）
- 收到 snapshot 事件 → 全量覆盖；收到 changes 事件 → 增量更新
- 应用层心跳：定时发 `"PING"`，收 `"PONG"`
- `is_stale()` 检查上次更新时间，过期则回退到 REST

**REST 路径** (`ToolServiceClient`)：
- 标准 CRUD：`get_orderbook`, `get_balance`, `get_orders`, `get_positions`
- `retry()` 带指数退避，默认 3 次

## 3. 主循环 — `engine/tick_engine.py`

每个 tick 的执行链：

```
1. 拉取 account snapshot (balance / orders / positions)
2. 拉取 / 读取 orderbook → 计算 mid / spread
3. Circuit breaker 检查 (risk/circuit_breaker.py)
4. 如果触发 → cancel_all → 停止或 cooldown
5. 对每个 token:
   a. 计算 inventory signal (core/signals.py)
   b. 计算 realized volatility (core/signals.py)
   c. 计算 required spread → adaptive spread (core/signals.py)
   d. 检查自然 spread < 盈利阈值 → 判定是否 block
   e. 检查 OFI / 参考市场动量 → 判定是否 block (core/signals.py)
   f. 策略报价生成 (strategies/)
   g. anchor_to_book → 执行 bid/ask (core/anchoring.py)
   h. quantize → tick 对齐 (utils/quantize.py)
   i. 计算 target sizes (core/sizing.py)
   j. 对 BUY/SELL: diff → cancel + place (execution/order_manager.py)
6. 自动 merge（按周期）
7. 写 metrics.jsonl (utils/metrics.py)
```

### 关键实现细节

**执行层切换**：`_exec_call()` 做 live/paper 路由。live 走 `client.retry()`，paper 直接调用 `PaperBroker` 方法。两者暴露相同接口（`place_limit_order`, `cancel_order`, `get_balance` 等）。

**Side blocking**：被 block 的方向会先撤掉已有挂单，然后跳过 create。阻断和策略计算解耦——本 tick 被 block 不影响下 tick 重新评估。

**Merge pending credit**：live merge 提交后，在链上确认回来之前，按 `amount / amount_scale × ratio` 给 sizing 一笔临时可用资金。用 TTL 控制过期。

## 4. 报价 — `pricing.py`

```python
bid = mid - spread/2 - skew
ask = mid + spread/2 - skew
```

单个函数，无状态。skew 方向：inventory 为正（多头） → bid 下移 + ask 下移 → 鼓励被动卖出。

## 5. 订单管理 — `order_manager.py`

`diff()` 的决策逻辑：

```
同 token+side 有挂单？
  ├─ 无 → create=True, reason="no_order"
  └─ 有 → 挑 price 最接近 target 的作为 anchor
         ├─ anchor 在 deadband 内 → create=False, reason="keep_in_deadband"
         └─ anchor 超出 deadband → cancel anchor + create=True, reason="replace"
         多余的 same-side 单一律 cancel
```

## 6. 纸盘 — `paper_broker.py`

本地内存撮合引擎。关键行为：

- **资金冻结**：BUY 下单冻结 `price × size` USDC；SELL 下单冻结等量 token 仓位
- **成交判定**：
  - `conservative`：盘口必须"穿透"你的限价（`best_ask ≤ order_price - ε`）
  - `optimistic`：盘口触碰即可（`best_ask ≤ order_price + ε`）
- **成交量限制**：`top_of_book_size × queue_share`，模拟排队
- **merge / split**：纯内存操作，merge 把 min(yes, no) 转回 USDC，split 则反向

## 7. 日志 — `metrics.py`

每 tick 追加一行 JSON 到 `pmm_logs/metrics.jsonl`。字段定义详见 [METRICS_FORMAT.md](METRICS_FORMAT.md)。

---

## 工程边界 (v1)

- PnL 是 mark-to-market，不区分 realized / unrealized
- 没有 fee 扣减（paper PnL 偏乐观）
- 只接入了 market 公共频道，没有 user 私有频道（订单 / 成交推送）
- 只有 merge，没有自动 split / redeem
- Paper 撮合只看 top-of-book，不考虑深度穿透
