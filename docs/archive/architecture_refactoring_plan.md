# 统一量化交易架构重构与执行计划

根据您提出的架构思路，结合当前项目 `pm_agent` 的现状（包含 `pmm`, `arb`, `rule_lawyer` 等业务线），我设计了以下重构与执行计划。

这个架构非常经典且分工明确（Engine抓数据 -> Strategy算逻辑 -> Execution下订单），非常适合多策略运行的量化系统。

## 一、架构对比与差异分析

### 当前架构现状
目前仓库代码比较分散，分为几个不同的业务线独立运行：
1. `src/strategies/pmm/`: 自有成套的 `engine`, `core`, `execution`, `risk`（主要针对做市场景）。
2. `src/strategies/arb/`: 拥有独立的逻辑（近期重构过 Node-based pipeline）。
3. 这导致了组件重复：各自管理状态、各自负责下单、数据结构未完全统一。

### 目标架构 (基于您的提议)
将系统重构为五大分层（统一底座，支持所有策略类型）：
1. **核心引擎层 (Engine & Dispatcher) **
2. **策略执行层 (Strategy Worker) **
3. **交易与执行层 (Execution Service) **
4. **持久化与大盘监控层 (Storage & Observability) **
5. **通知与干预层 (Notification & Control) **

> [!NOTE]
> 您的设计非常契合目前的需求，将原本分散在各个策略里的“轮询、下单、状态保存”抽离出来，让策略只关注“计算”。

---

## 二、架构细化设计探讨

对于您提出的分层，我们可以这样落地并在项目结构上映射（供您参考讨论）：

### 1. 核心引擎层 (Engine & Dispatcher)
- **代码位置**: 建议放在 `src/platform/engine/` 或 `src/engine/`
- **组件结构**:
  - `MarketDataFeeder`: 支持 REST 轮询和 WebSocket 两套实现，统一对外抛出标准化 `Orderbook` 和 `Price` 数据。
  - `StrategyRegistry`: 管理策略实例生命周期。
  - `EventDispatcher`: 负责构建 `MarketTickEvent` 并提交到线程池/协程池（Python 中推荐使用 `asyncio` 的 Queue + Worker，或者 `ThreadPoolExecutor`）。

### 2. 策略执行层 (Strategy Worker)
- **代码位置**: 保持在 `src/strategies/` 下，但所有策略继承自统一的基类。
- **组件结构**:
  - `IStrategy` 基类 (在 `src/platform/interfaces/`):
    ```python
    class IStrategy:
        def init(self, context: StrategyContext): pass
        def on_market_tick(self, event: MarketTickEvent) -> Optional[OrderCommand]: pass
        def on_order_update(self, event: OrderUpdateEvent): pass
    ```
  - `StrategyContext`: 维护实例运行时的内存状态（持仓、参数）。

### 3. 交易与执行层 (Execution Service)
- **代码位置**: `src/platform/execution/`
- **核心逻辑**:
  - 维护 Polymarket/Clob 客户端连接。
  - 监听 `OrderCommand` 队列。
  - 执行下单/撤单（带有重试和速率限制），并在完成后将结果写库并回调/投递 `OrderUpdateEvent` 回 Dispatcher。

### 4. 持久化与大盘监控层 (Storage)
- **代码位置**: `src/platform/storage/` 结合 `src/interfaces/web/`
- **数据库设计**: 直接复用当前的 `strategy_runtime.db`。现有的 `strategy_instances` 表已经包含了 `instance_id`, `strategy_key`, `status`, `run_params` 等所有必需字段，非常完善。我们只需要新建一张 `trade_order` 表用于按实例记录订单流水。
- **实时大盘**: 现有的 `strategy_dashboard_server.py` (BFF) 可以直接拉取这部分数据并在前端展现实例状态和订单历史。

### 5. 通知与干预层 (Notification & Control)
- **代码位置**: `src/platform/notification/`
- **实现**: 集成当前的 Telegram 逻辑，通过简单的 `asyncio.Queue` 监听核心事件（如 Error, 熔断断路, 大额成交事件等）并发送通知。

---

## 三、架构重构整体蓝图 (The Blueprint)

结合上述讨（采用 `asyncio` 驱动，复用现有 DB），重构后的最终全景和代码结构分布如下：

```text
src/
├── platform/
│   ├── engine/                  # [NEW] 核心引擎层 (The Heart)
│   │   ├── dispatcher.py        # [NEW] 基于 asyncio.Queue 的事件循环分发器
│   │   ├── registry.py          # [NEW] 管理 IStrategy 实例的挂载与卸载
│   │   └── base_strategy.py     # [NEW] IStrategy, StrategyContext (包含 instance_id 等)
│   ├── execution/               # [NEW] 统一执行层 (The Hands)
│   │   └── executor.py          # [NEW] 消费 OrderCommand，调用 client 下单，抛出 OrderUpdate
│   ├── market_data/             # [MODIFY] 数据喂给层 (The Eyes)
│   │   └── feeder.py            # [NEW] 轮询/WS 拉取行情，产生 MarketTickEvent (可复用现有的 PolymarketClient)
│   ├── storage/                 # [MODIFY] 统一存储层 (The Memory)
│   │   └── runtime_store.py     # [MODIFY] 增加 create_trade_order_table() 和相关写库逻辑
│   └── notification/            # [MODIFY] 统一通知层 (The Mouth)
│       └── telegram_bot.py      # [MODIFY] 作为独立 asyncio task 监听各种 Event 并发送
├── strategies/
│   └── arb/                     # (举例) 具体策略实现仅仅依赖 IStrategy (The Brain)
│       └── unified_arb.py       # [NEW] 继承 IStrategy, 实现 on_market_tick -> 抛出 OrderCommand
...
```

**数据流转总结 (Data Flow with asyncio)**:
1. `Feeder` (Aio Task) 不断 fetch 数据 -> 放入 `Dispatcher.event_queue`
2. `Dispatcher` (Aio Task) 取出 Event -> 查表 `Registry` 找到对应实例 -> 执行 `strategy.on_market_tick(event)`
3. `Strategy` 计算后返回 `OrderCommand` -> 放入 `Execution.command_queue`
4. `Executor` (Aio Task) 取出 Command -> 发起网络下单 -> 写 `trade_order` 库 -> 产生 `OrderUpdateEvent` -> 塞回 `Dispatcher.event_queue`
5. （可选）任何环节产生的 `AlertEvent` -> 放入 `Notifier.alert_queue` -> Telegram 推送。

---

## 四、分阶段执行计划与 Task 安排

基于这个明确的蓝图，我们的执行分为以下四个阶段：

### Phase 1: 基础设施搭建与领域模型 (Models & DB)
- [ ] 创建核心数据结构定义 (`MarketTickEvent`, `OrderUpdateEvent`, `OrderCommand`, `StrategyContext`)。
- [ ] 定义 `IStrategy` 基类。
- [ ] 在 `strategy_runtime.db` 中新建 `trade_order` 表，并扩展相关 storage 写库 API。

### Phase 2: 核心引擎层与异步流实现 (Async Engine)
- [ ] 实现 **Execution Service**：封装底层下单接口，监听 `command_queue`，处理真实的 Polymarket 下单逻辑并抛出回调事件。
- [ ] 实现 **MarketDataFeeder**：独立的轮询任务或 WebSocket，获取行情丢入调度队列。
- [ ] 实现 **EventDispatcher & Registry**：将上面两者连通，跑通整个 `asyncio` 的 Event Loop 和路由。

### Phase 3: 策略迁移与空跑测试 (Strategy Pilot)
- [ ] 选择一个简单的套利策略 (Arb/Toggle) 作为试点，迁移为继承自 `IStrategy` 的新策略。
- [ ] 编写一个入口脚本 (`main_engine.py`)，把 Pilot 策略注册进去，开启 `DRY_RUN`（不发真实订单），验证数据流：`Feeder -> Dispatcher -> Strategy -> Execution -> DB` 是否畅通。

### Phase 4: 全面替换与清理 (Rollout)
- [ ] 逐步将旧的 PMM 或其他遗留套利策略接入这套新引擎。
- [ ] 清理原来的各种独立运行脚本和废弃代码。
