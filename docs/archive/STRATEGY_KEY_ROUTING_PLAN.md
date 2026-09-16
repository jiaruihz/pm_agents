# PMM Strategy Key 路由重构方案

## 1. 目标

在不改变当前单档做市行为的前提下，引入“策略模式 + key 路由”。

- 当前：`tick_loop` 直接写死单一策略逻辑。
- 目标：`tick_loop` 只负责编排，策略逻辑由 `strategy_key` 决定。
- 多档报价：已实现 `multi_level_v1`（按档位生成 quote targets），可直接做 A/B 回测。

---

## 2. 设计原则

1. 行为兼容优先：默认策略结果与现状一致（`single_level_v1`）。
2. 可插拔：新增策略不改主循环。
3. 可回测：同一场景可按 `strategy_key` 横向比较。
4. 可追踪：metrics/summary 输出 `strategy_key` 与运行时元数据。

---

## 3. 核心设计

### 3.1 策略接口

```python
class MarketMakingStrategy(Protocol):
    key: str

    def generate_quotes(
        self,
        token_id: str,
        market_ctx: dict,
        account_ctx: dict,
        signal_ctx: dict,
        config: PMMConfig,
    ) -> list[QuoteTarget]:
        ...
```

说明：
- 返回值是 `list[QuoteTarget]`，即使当前是单档也返回 list（长度=2：BUY/SELL）。
- 后续多档策略只需返回更多 `QuoteTarget(level=0..N)`，调用侧无需改动。

### 3.2 策略注册表

```python
class StrategyRegistry:
    def register(self, strategy: MarketMakingStrategy) -> None: ...
    def get(self, key: str) -> MarketMakingStrategy: ...
```

内置策略：
- `single_level_v1`（默认基准）
- `multi_level_v1`（已注册可运行）

### 3.3 运行路由

新增配置：

- `PMM_STRATEGY_KEY=single_level_v1`
- `PMM_STRATEGY_PARAMS_JSON={...}`（可选）

运行时：

1. `tick_loop` 启动时读取 `strategy_key`
2. 从 `StrategyRegistry` 获取策略实例
3. 每个 token 调用 `strategy.generate_quotes(...)`
4. 统一交给现有 `OrderManager.diff` 执行下撤单

---

## 4. 数据结构与日志

### 4.1 QuoteTarget（保持兼容，支持扩展）

```python
@dataclass
class QuoteTarget:
    token_id: str
    side: str         # BUY/SELL
    price: float
    size: float
    level: int = 0    # 单档默认 0；多档时 0..N
```

### 4.2 Metrics / Summary 新字段

- `strategy_key`
- `strategy_version`（可选）
- `quote_runtime`（已存在：requested/effective/placeholder）

目的：
- 能区分“同参数不同策略实现”的效果。
- 为后续多档上线提供可比的前后证据。

---

## 5. 一次性落地方案（本次）

一次性完成“策略模式重构 + 单档策略实现”，不保留 legacy 运行分支：

1. 新建 `pmm/strategy_base.py`（接口 + `QuoteTarget`）
2. 新建 `pmm/strategies/single_level_v1.py`（搬运当前逻辑）
3. 新建 `pmm/strategy_registry.py`
4. `tick_loop` 改为通过 `strategy_key` 获取策略并生成 quote list
5. 默认 `strategy_key=single_level_v1`
6. 回测结果需与当前基线一致（误差阈值内）

后续扩展：

1. 优化 `multi_level_v1` 的层间风控（每侧 notional cap / per-level cap）
2. 继续优化 `OrderManager.diff_multi` 匹配启发式
3. 真实录制数据回放对比：`single_level_v1` vs `multi_level_v1`

---

## 6. 回测对比方案

`compare_profiles_all_strategies.json` 增加字段：

- `strategy_key`
- `strategy_overrides`

同一 scenario 下做横向回放：

- `single_level_v1`
- `multi_level_v1`

比较指标：

- `pnl_end`
- `max_drawdown`
- `fill_rate_per_order`
- `total_canceled / total_placed`

---

## 7. 验收标准

1. 默认配置下，`strategy_key=single_level_v1` 与当前行为一致。
2. `metrics.jsonl` / `summary.json` 能看到 `strategy_key`。
3. `compare` 报告中可按策略分组比较。
4. 启用多档时（`strategy_key=multi_level_v1` 且 `quote_levels>1`）可稳定输出 N 档报价并进入统一执行路径。

---

## 8. 风险与回滚策略（工程层）

风险：
- 接口抽象后可能引入上下文字段缺失，导致报价偏差。

控制：
- 用 golden 回测对齐（固定场景 + 固定参数 + 固定 seed）作为强约束门禁。
- PR 合并前必须通过回测阈值。

回滚：
- 不在运行时保留 `legacy_single_path` 开关。
- 若偏差超阈值，直接 `git revert` 当前重构提交，回到前一稳定版本。
