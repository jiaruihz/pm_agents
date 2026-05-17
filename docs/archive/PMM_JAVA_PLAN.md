# Personal Market Maker (PMM) Java Architecture - Polymarket Example

目标市场示例：
- https://polymarket.com/event/will-the-us-acquire-any-part-of-greenland-in-2026

这是一套面向个人做市商（PMM）的 Java 实现方案草案，适用于 Polymarket（CTF Exchange）。

**运行环境**
- Java 21（建议使用 Virtual Threads 处理高并发 IO）

**通信协议**
- 市场数据：WebSocket（推荐）或 REST 轮询（CLOB API）
- 下单与订单管理：REST API（需要 EIP-712 签名）
- 链上操作：Web3j（用于 USDC 授权、CTF Merge / Redeem）

---

## A & B. 高频循环（Tick Loop）

**频率**
- 1–5 秒（或基于 WebSocket 推送事件驱动）

**线程模型**
- 单个 `ScheduledExecutorService` 或 `while` 循环
- 建议该循环内串行执行，避免状态竞态

### Step 1: 数据同步（Sync World State）

1. 拉取市场数据（Public Data）
- 调用 `GET /orderbook` 获取当前 marketId 的 Bids/Asks
- 建议将 orderbook 解析为 `TreeMap<Double, Double>`（Price -> Size）以便快速查找最优价

2. 拉取私有数据（Private Data）
- 调用 `GET /orders` 获取当前 Open Orders（需要接口或本地缓存）
- 调用 `GET /balance` 或使用本地 fill 事件更新 YES/NO/USDC 余额
- 计算 Net Position（净头寸）

### Step 2: 定价计算（Pricing Engine）

1. 基准价（Fair Price / Mid）
- 简单做市可用 `mid = (bestBid + bestAsk) / 2`
- 有外部信号时，可用预测模型输出作为修正

2. 库存倾斜（Inventory Skew）
- 目标：库存偏多时降低该侧买价、提高卖价
- 示例线性倾斜：`price = mid + k * inventory`
- 注意价格边界：`price` 必须在 `(0, 1)` 内

3. 挂单量（Size Sizing）
- `size = min(baseSize, USDC_Balance / price)`

### Step 3: 订单管理（Order Management / Diffing）

1. 遍历现有挂单
- 判断订单是否失效
- 失效条件示例：
  - 价格偏离 `TargetPrice` 超过阈值
  - 订单量与目标量差异过大
  - 订单不再位于前 N 档

2. 生成动作列表
- `To_Cancel`: 需要撤销的订单 ID 列表
- `To_Create`: 需要新挂的订单（价格、方向、数量）
- 自成交保护：确保新订单不会与自己挂单交叉

### Step 4: 执行与状态写入（Execution & Telemetry）

1. API 执行
- 并发发送 `DELETE /orders`（批量撤单）
- 并发发送 `POST /order`（批量/逐个下单）

2. 指标记录（Telemetry）
- PnL（标记市值）
- Inventory
- Spread
- FillRate
- 输出到日志或监控系统（CSV/JSON/InfluxDB）

---

## C. 库存与结算操作（Inventory Ops）

**频率**
- 中频（每 1–5 分钟）或事件驱动

1. 合并仓位（Merge）
- 若同时持有 YES/NO，执行 `mergePositions` 释放 USDC

2. 赎回（Redemption）
- 市场 RESOLVED 后调用 `redeemPositions`

3. 风险再平衡（Risk Rebalancing）
- 若单边暴露 > 80%，触发强制减仓，暂停该侧买入

---

## D. 参数调优（LLM Agent Loop）

**频率**
- 低频（每 10–30 分钟）

1. 构建 Prompt Context
- 近 30 分钟成交率、PnL、波动率、外部新闻摘要

2. 请求 LLM 建议
- 例：
  - “当前 Spread=0.02，成交率低，库存积压 YES。请给出新的 Spread/Skew/BaseSize，以 JSON 返回。”

3. 应用参数
- 解析 JSON 后原子更新内存配置（下一轮 Tick 生效）

---

## 备注

- 如果需要完整的做市接口（例如 `GET /orders`、`GET /positions`），请在 Python 工具服务或 Java 层补充。
- Polymarket 的合约与 CLOB 价格规则需要在回测和线上同时监控，避免 UI 价格与真实盘口偏差。
