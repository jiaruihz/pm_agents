# PMM 多档报价 + 架构重构 — 执行计划

## 背景

当前 PMM 存在两个核心瓶颈：
1. **单档报价**：每侧只挂 1 单，小盘市场极易被 taker 一口吃穿
2. **God Function**：`tick_loop.py` 948 行单函数承载全部逻辑，无法独立测试、难以拓展

本计划将两者合并推进。先重构出清晰的分层架构，再在新架构上实现多档报价。

---

## Part A：架构重构

### 目标

将 `tick_loop()` 拆为 4 个可独立测试的组件：

```
TickEngine          → 编排循环，不做计算
StrategyCore        → 信号计算 + 报价生成（tick_loop 现有逻辑提取）
RiskManager         → 熔断 / 仓位限制 / side block 判定
ExecutionRouter     → live / paper 路由（已有 _exec_call 的正式化）
```

### 文件变更

---

#### [NEW] `pmm/strategy.py`

从 `tick_loop.py` 提取以下逻辑，形成 `StrategyCore` 类：

```python
class StrategyCore:
    """无状态策略计算核心，接收快照，输出报价决策。"""

    def __init__(self, config: PMMConfig): ...

    def compute_fair_value(self, orderbook: dict, token_id: str) -> float:
        """weighted mid / midpoint / fallback"""

    def compute_inventory_signal(self, token_id: str, positions: dict) -> float:
        """sigmoid 库存信号"""

    def compute_required_spread(self, rv: float, inv_signal: float) -> float:
        """fee + profit + vol + inv 成本"""

    def generate_quotes(self, snapshot: MarketSnapshot) -> list[QuoteTarget]:
        """对每个 token_id 生成目标 bid/ask + size"""

    def should_block_side(self, snapshot: MarketSnapshot) -> dict[str, list[str]]:
        """OFI / momentum / profitability 阻断判定"""
```

将以下函数从 `tick_loop.py` 移入（改为方法或模块级函数）：
- `_weighted_mid`, `_fair_mid`
- `_inventory_signal`, `_required_spread`
- `_target_sizes`
- `_anchor_quotes_to_book`, `_quantize_quote_pair`
- `_order_flow_imbalance`, `_momentum`, `_realized_vol`
- `_depth_near_mid`

---

#### [NEW] `pmm/risk.py`

```python
class RiskManager:
    """风控判定，与策略计算分离。"""

    def __init__(self, config: PMMConfig): ...

    def check_circuit_breaker(self, mid_history: dict, mids: dict) -> CBResult:
        """移动均价偏离检测"""

    def check_profitability(self, natural_spread: float, required_spread: float) -> bool:
        """入场盈利性检查"""

    def check_side_blocks(self, ofi: float, momentum: float, config: PMMConfig) -> dict:
        """OFI / 动量阻断"""
```

---

#### [NEW] `pmm/execution.py`

```python
class ExecutionRouter:
    """统一执行层接口，live 和 paper 走同一套方法签名。"""

    def __init__(self, client, paper_broker, mode: str): ...

    async def place_order(self, token_id, price, size, side) -> dict: ...
    async def cancel_order(self, order_id: str) -> dict: ...
    async def cancel_orders(self, order_ids: list[str]) -> dict: ...
    async def cancel_all(self) -> dict: ...
    async def get_snapshot(self, token_ids) -> AccountSnapshot: ...
```

---

#### [NEW] `pmm/models.py`

统一数据结构，消除到处传 dict 的问题：

```python
@dataclass
class MarketSnapshot:
    token_id: str
    orderbook: dict
    mid: float
    spread: float
    best_bid: float
    best_ask: float

@dataclass
class AccountSnapshot:
    usdc_balance: float
    positions: dict[str, float]
    open_orders: list[dict]

@dataclass
class QuoteTarget:
    token_id: str
    side: str          # BUY / SELL
    price: float
    size: float
    level: int         # 报价层级（为多档预留）

@dataclass
class TickResult:
    placed: int
    canceled: int
    errors: int
    merge_actions: list
    quotes: dict
    signals: dict
```

---

#### [MODIFY] `pmm/tick_loop.py`

重构为 `TickEngine` 类，只负责循环编排：

```python
class TickEngine:
    def __init__(self, config, strategy, risk_mgr, executor, data_feed, metrics):
        ...

    async def run(self):
        while True:
            snapshot = await self._fetch_snapshot()
            cb_result = self.risk_mgr.check_circuit_breaker(...)
            if cb_result.triggered:
                await self._handle_circuit_breaker(cb_result)
                continue
            quotes = self.strategy.generate_quotes(snapshot)
            blocks = self.strategy.should_block_side(snapshot)
            result = await self._execute_quotes(quotes, blocks)
            self._maybe_merge(...)
            self.metrics.log(result)
            await asyncio.sleep(self.config.tick_interval_sec)
```

预计从 948 → ~200 行。

---

#### [MODIFY] `pmm/backtest/replay_runner.py`

当前 ~300 行与 `tick_loop.py` 重复。重构后直接复用 `StrategyCore` + `RiskManager`：

```python
async def _run_single_async(scenario, out_dir):
    strategy = StrategyCore(cfg)
    risk_mgr = RiskManager(cfg)
    # ... 用同一套 strategy/risk 对象，只是执行层换成 PaperBroker
```

预计从 496 → ~150 行。

---

#### [NEW] `pmm/utils.py`

合并散落各处的重复工具函数：
- `_safe_float` / `_safe_int` / `_to_float`
- `_normalize_levels`（统一 `tick_loop.py` / `orderbook.py` / `paper_broker.py` 三份）

---

#### [MODIFY] `pmm/orderbook.py`

去掉 pandas 依赖，用纯 list 操作替换 `orderbook_to_df`。当前 `best_bid_ask` 每次调用都创建 2 个 DataFrame，在高频 tick 下不必要。

---

## Part B：多档报价

### 目标

从当前每侧 1 档 → 可配置 N 档梯度报价，降低被一口吃穿风险。

### 配置新增

```python
# config.py 新增
quote_levels: int = 1              # 报价层数（1 = 兼容现有行为）
level_spread_step: float = 0.005   # 每档额外价差
level_size_decay: float = 0.6      # 每档 size 衰减系数
```

### 核心逻辑（在 `strategy.py` 中实现）

```python
def generate_quotes(self, snapshot: MarketSnapshot) -> list[QuoteTarget]:
    quotes = []
    for token_id in self.token_ids:
        base_bid, base_ask = self._compute_base_quote(...)
        base_buy_size, base_sell_size = self._compute_sizes(...)

        for level in range(self.config.quote_levels):
            step = level * self.config.level_spread_step
            decay = self.config.level_size_decay ** level
            bid_price = base_bid - step
            ask_price = base_ask + step
            buy_size  = base_buy_size * decay
            sell_size = base_sell_size * decay

            if buy_size >= self.config.min_size:
                quotes.append(QuoteTarget(token_id, "BUY", bid_price, buy_size, level))
            if sell_size >= self.config.min_size:
                quotes.append(QuoteTarget(token_id, "SELL", ask_price, sell_size, level))
    return quotes
```

### OrderManager 适配

当前 `OrderManager.diff` 假设每侧只有 1 单。改为：
- 按 level 分组比较（用 price 距离排序匹配）
- 每个 level 独立做 deadband 判定

```python
def diff_multi(self, open_orders, token_id, side, targets: list[QuoteTarget]) -> list[DiffDecision]:
    """多档 diff：targets 按 level 排序，open_orders 按 price 排序，贪心匹配。"""
```

---

## 执行顺序

分 3 个 PR 推进，每个 PR 保证自身可运行、可测试：

### PR 1：基础设施（不改行为）
1. 创建 `pmm/utils.py`，合并重复工具函数
2. 创建 `pmm/models.py`，定义数据结构
3. 重构 `pmm/orderbook.py` 去 pandas
4. 补充基础单元测试

### PR 2：架构重构（行为不变）
1. 创建 `pmm/strategy.py`，提取 `StrategyCore`
2. 创建 `pmm/risk.py`，提取 `RiskManager`
3. 创建 `pmm/execution.py`，提取 `ExecutionRouter`
4. 重构 `pmm/tick_loop.py` → `TickEngine`
5. 重构 `pmm/backtest/replay_runner.py` 复用 `StrategyCore`
6. 回归测试：确保 paper 回测结果与重构前完全一致

### PR 3：多档报价
1. `config.py` 新增 `quote_levels` / `level_spread_step` / `level_size_decay`
2. `strategy.py` 实现 `generate_quotes` 多档逻辑
3. `order_manager.py` 实现 `diff_multi`
4. `tick_loop.py` (`TickEngine`) 使用多档 flow
5. 回测验证：单档 vs 多档对比

---

## 验证计划

### 自动化测试
- `StrategyCore` 各信号计算函数的单元测试
- `RiskManager` 熔断 / 阻断的边界条件测试
- `OrderManager.diff_multi` 多档匹配逻辑测试
- 完整 replay 回归（重构前后 metrics 对比）

### 手动验证
- paper 模式运行 3 档报价，观察 metrics.jsonl 的 `final_quotes` 包含多层
- dry_run 模式确认输出格式正确
