# Personal Market Maker (PMM) Python Architecture - Polymarket Example

目标市场示例：
- https://polymarket.com/event/will-the-us-acquire-any-part-of-greenland-in-2026

这是一个基于 Python 的个人做市商（PMM）方案，强调轻量化与可迭代：
- **核心循环**：`asyncio + aiohttp`
- **数据处理**：`pandas DataFrame`
- **签名**：`eth_account.messages.encode_structured_data`
- **LLM**：`langchain` 或 `openai`

---

## 技术栈与依赖建议

- Python 3.10+
- `aiohttp`：异步请求（CLOB REST / Gamma REST）
- `websockets`：订阅行情（可选）
- `pandas`：盘口快照与统计指标
- `eth-account`：EIP-712 签名
- `py_clob_client`：直接走 Polymarket CLOB（可选）
- `langchain` 或 `openai`：参数调优

---

## A & B. 高频循环（Tick Loop）

**频率**：1–5 秒（或 WebSocket 推送驱动）

**实现方式**：
- `asyncio` 事件循环
- 用 `asyncio.gather` 并发拉取市场数据 + 私有数据

### Step 1: 数据同步（Sync World State）

1. 市场数据（Public）
- `GET /orderbook` 拉取当前 bids/asks
- 用 `pandas.DataFrame` 构建 orderbook

2. 私有数据（Private）
- `GET /orders` 拉当前挂单
- `GET /balance` 或本地 fill 缓存更新 YES/NO/USDC
- 计算 Net Position

> 建议：所有 API 调用封装成异步函数，统一超时与重试逻辑。

### Step 2: 定价计算（Pricing Engine）

1. 基准价（Fair Price）
- `mid = (bestBid + bestAsk) / 2`
- 可叠加外部信号（LLM/新闻）作为偏移

2. 库存倾斜（Inventory Skew）
- `price = mid + k * inventory`
- 确保价格落在 (0,1)

3. 挂单量（Size Sizing）
- `size = min(baseSize, USDC_Balance / price)`

### Step 3: 订单管理（Diffing）

- 对比现有订单 vs 目标订单
- 只撤销“偏离过大”的订单
- 只新增“需要挂单”的订单

### Step 4: 执行与记录

- 异步调用 `POST /order` / `DELETE /orders`
- 日志记录：PnL / Inventory / Spread / FillRate

---

## C. 库存与结算（Inventory Ops）

**频率**：每 1–5 分钟

1. Merge
- YES + NO 合并成 USDC
- 调用 CTF 合约 `mergePositions`

2. Redeem
- 市场 RESOLVED 后赎回

3. 风控
- 单边暴露超过阈值 → 强制减仓

---

## D. 参数调优（LLM Agent Loop）

**频率**：10–30 分钟

1. 采集指标
- 成交率、PnL、波动率、库存偏斜

2. 调用 LLM
- 让模型输出新的 `spread / skew / baseSize`

3. 原子更新配置

---

## 代码结构建议（Python）

```
/pmm
  /core
    tick_loop.py
    pricing.py
    order_manager.py
  /data
    orderbook.py
    metrics.py
  /ops
    inventory.py
  /llm
    tuner.py
  config.py
  main.py
```

---

## 与现有项目的衔接建议

- 你可以直接复用当前 Python 工具服务（HTTP API）
- Python PMM 进程只需要调用：
  - `/orderbook/{token_id}`
  - `/order`
  - `/market-order`
  - `/balance`
  - `/ctf/merge`

---

## 备注

- Python 版更适合快速迭代与策略试错
- 高并发与延迟控制可通过 `asyncio` + 合理并发上限解决

