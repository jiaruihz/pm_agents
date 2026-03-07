# 核心监控与大盘视图设计说明 (Monitoring & Dashboard Architecture)

随着 Polymarket Agent 系统的重构，特别是异步引擎 (Unified Engine) + 面向多策略并发的隔离运行机制，我们在底层将状态全部迁入并固化在了 SQLite 中 (`strategy_runtime.db`)。

这份文档系统回答了：**系统目前有哪些表？它们分别记录了什么？如何支撑实盘监控和自动化回测？以及前端/后端还缺什么？**

---

## 1. 核心表结构与“一专多能”的数据隔离

系统中的所有监控表完全围绕着 **实例化隔离** (Instance Isolation) 的思路进行设计。由于我们可以同时跑 3 个不同的套利实例和 1 个做市实例，数据必须互不干扰。

| 数据表名 | 核心职责 | 记录的关键维度与机制 |
| :--- | :--- | :--- |
| `strategies` | 策略元数据 | `strategy_key` (如 `smart_money_copy`), 作者, 描述字典。用于大盘展示可用策略矩阵。 |
| `strategy_instances` | 实例运行时配置 | `instance_id`, `strategy_key`, `token_ids`, `execution_mode` (如 `DRY_RUN` 或 `LIVE`), `钱包地址`, `参数配置 (JSON)`。 |
| `strategy_instance_state`| **实时心脏 (Heartbeat)** | 每个 `instance_id` 只有 1 行记录。包含最新 `pnl`, `equity`, `usdc`, 最新 `open_orders` 数量，极其微小的 JSON State。Dashboard 首页用这批数据亮绿灯/红灯，性能秒开。 |
| `strategy_instance_snapshots`| **时序切片 (K线基建)** | 引擎每隔 60 秒（可配）对 `state` 表进行一次快照（Append-only）。这就是 **Streamlit 绘制资金曲线、PNL 回撤图** 的唯一数据源。 |
| `trade_orders` | **全生命周期订单表** | 包含 `order_id`, `instance_id`, **`strategy_key`** (区分策略族归属), `side`, `price`, `size`, 以及实时流转的 `status` (PENDING->OPEN->FILLED), **`fee_paid`**。它是我们审计每笔下单动作的绝对真理。 |
| `trade_fills` | **微观成交明细表** | 以 `fill_id` 作为主键，关联 `order_id`。因为 Polymarket CLOB 可能对一个挂单产生多次部分成交。它精确记录了每次拆弹的 `fill_price`, `fill_size` 以及扣除的 `fee_paid`，用于精确防抖和滑点分析。 |

---

## 2. 监控大盘如何使用这些数据？

在传统的架构中，我们往往靠暴力正则匹配 `.txt` 日志来读取“过去 1 小时成交了多少”。在大盘完全转移到这套表结构后：

### A. 整体风控大屏 (Dashboard 首页)
通过 `SELECT * FROM strategy_instance_state` 可以瞬间拉出一个矩阵阵列，显示：当前活跃进程 5 个，总 PNL `$145.2`，系统异常 (Error) 0 次。这对于云端集群管控至关重要。

### B. 按策略深入审查 (Instance Detail)
点击某一个策略实例后：
1. 取 `strategy_instance_snapshots` 拉出时间序列，渲染 ECharts / Plotly 的收益折线图。
2. 取 `trade_orders WHERE instance_id = ? AND status = 'OPEN'`，展示该策略目前有多少资金挂在盘口（防爆仓锁算力）。
3. 取 `trade_fills WHERE instance_id = ?`，展示成交明细流水。

---

## 3. 回测系统的无缝衔接 (Mock Runtime)

这套设计的终极威力在于：**实盘与回测前端 100% 共享！**

由于我们的策略代码是纯净的 `IStrategy` 标准，它只抛出 `OrderCommand`。
在回测时：
1. **Mock Data Feeder** 读取历史 K 线/Tick，投递 `MarketTickEvent`。
2. 策略计算抛出 `OrderCommand`。
3. **Mock Execution Service** 接管 `OrderCommand`，基于撮合引擎生成虚拟成交，然后**依然向 `trade_orders` 和 `trade_fills` 表中写入数据**（只不过是写入 `backtest.db` 而不是 `runtime.db`）。

这意味着，前端 Streamlit 在切换到“回测面板”时，没有任何特殊的代码。它只是读取换成了 `backtest.db` 的统一表结构，就能看到和实盘一样细致入微的订单轨迹图（甚至能检查在历史哪一秒被部分成交）。

---

## 4. 现状评估：前端和后端是否够用？

**答案是：需要改造补充（Task 4.5）**

目前，您代码库的 `src/interfaces/web/strategy_dashboard_server.py` (BFF 层) 以及对应的前端，很大一部分是为老版、纯文字版或者旧 PMM 设计的。

### 下一步需要的重构改造点：
1. **BFF 后端 (Dashboard Server)**：
   * 必须补充或修改 Endpoint 原本用于拉取旧 logs 的逻辑，改为直接查询 `StrategyRuntimeStore`.
   * 需要暴露 `GET /api/v1/orders/{instance_id}` 以及 `GET /api/v1/fills/{order_id}` 的 JSON 接口。
2. **Streamlit 前端**：
   * 在订单列表里，必须新增列项显示我们今天加入的 `strategy_key` (展示到底是什么子模型下的单) 和 `fee_paid` (累积手续费)。
   * 目前的大盘看板（BFF）更多承载“做市”单一视图，需要升级为能下拉框切换 `weather`, `smart_copy`，`arb` 多模态视图的总控台。

### 结论
底层引擎与数据库模型**已经彻底打通并足够支撑万级并发与金融级查账**。但作为整个系统的表面，我们的“控制塔 (Dashboard BFF)” 仍在旧代码上，这是接下来 Phase 4 需要收尾的战役之一。
