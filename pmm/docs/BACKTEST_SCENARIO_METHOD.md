# PMM 场景造数与回测方法说明

本文档描述当前仓库中“做市策略回测”的实现方法，重点是：

- 如何造数据（覆盖不同 market 状态）
- 如何基于这些数据回放策略
- 如何读取输出做策略评估

## 1. 文件结构

- `pmm/backtest/case_catalog.json`
  - 场景参数目录（case 定义）
- `pmm/backtest/scenario_generator.py`
  - 根据 catalog 生成逐 tick 场景数据
- `pmm/backtest/replay_runner.py`
  - 用策略核心组件回放并输出 `metrics/actions/summary`
- `scripts/python/pmm_backtest.py`
  - CLI：`generate` / `run` / `run-all`

## 2. 造数思路（How）

### 2.1 基础维度

每个 case 都由以下维度组合：

- `base_mid`：基准概率中枢（例如 0.30、0.50、0.70）
- `pattern`：价格路径形态
- `volatility`：噪声波动强度
- `drift_per_tick`：单边趋势速度
- `spread_base + spread_jitter`：盘口价差基础值和扰动
- `depth_base`：盘口深度基准
- `fillability`：成交难度（`low/medium/high`）
- 事件参数：`shock_tick/shock_jump/shock_len`、`dryup_start`

### 2.2 路径模式

当前支持：

- `stable`：平稳
- `trend_up` / `trend_down`：单边上涨/下跌
- `oscillating`：周期震荡
- `shock_up` / `shock_down`：突发跳变（应急场景）
- `regime_switch`：状态切换（平稳 -> 趋势 -> 震荡 -> 趋势反转）
- `whipsaw`：来回拉扯（库存管理困难）
- `liquidity_dryup`：流动性衰竭（深度下降、价差变宽）
- `spike_revert` / `drop_revert`：事件冲击后均值回归

### 2.3 YES/NO 联动

场景中默认双 outcome：

- `YES` mid 路径按模式生成
- `NO` mid 由 `1 - YES` 推导（并加少量微扰，避免完全对称）

盘口按 mid + spread 生成 top levels，确保可以驱动 `PaperBroker` 撮合逻辑。

## 3. 回测思路（How）

回放层复用现有策略核心：

- 复用 `compute_quotes`（定价）
- 复用 `OrderManager.diff`（撤改单）
- 复用 `PaperBroker`（本地撮合、余额与仓位）
- 复用 `tick_loop` 的关键函数（库存信号、动态 spread、锚定逻辑）

每个 tick 执行顺序：

1. 读取场景 orderbook 快照
2. 计算 mid/spread、库存信号、动态 spread
3. 生成 bid/ask，做 deadband diff（撤单/下单）
4. 用 `PaperBroker.on_market_data()` 执行撮合
5. 记录 metrics/actions

## 4. 输出文件说明

每个 scenario 运行后输出到：

- `pmm/backtest/results/<scenario_id>/metrics.jsonl`
  - 每 tick 指标（pnl/equity/mids/spreads/quotes/side_blocks 等）
- `pmm/backtest/results/<scenario_id>/actions.jsonl`
  - 操作流水（place/cancel/fill/error）
- `pmm/backtest/results/<scenario_id>/summary.json`
  - 汇总统计（`pnl_end/max_drawdown/total_fills/fill_rate_per_order`）

批量运行额外输出：

- `pmm/backtest/results/summary_all.json`
  - 所有场景聚合结果

## 5. 命令

### 5.1 生成全部场景

```bash
./venv/bin/python scripts/python/pmm_backtest.py generate \
  --catalog pmm/backtest/case_catalog.json \
  --out-dir pmm/backtest/scenarios \
  --seed 42
```

说明：

- 生成结果在 `pmm/backtest/scenarios/`
- 该目录已加入 `.gitignore`，默认作为本地回测数据，不纳入版本库

### 5.2 跑单场景

```bash
./venv/bin/python scripts/python/pmm_backtest.py run \
  --scenario pmm/backtest/scenarios/b50_oscillating_fill.json \
  --out-dir pmm/backtest/results
```

### 5.3 批量回测

```bash
./venv/bin/python scripts/python/pmm_backtest.py run-all \
  --scenarios-dir pmm/backtest/scenarios \
  --out-dir pmm/backtest/results
```

说明：

- 回测结果在 `pmm/backtest/results/`
- 该目录已加入 `.gitignore`，避免把大体量回测产物提交到仓库

## 6. 覆盖面（What）

`case_catalog.json` 当前已覆盖你要求的核心类别：

- 基准中枢：`30%`、`50%`、`70%`
- 走势：单边涨、单边跌、平稳、震荡
- 交易可达性：能吃单 / 不容易吃单（由 fillability + 路径共同决定）
- 风险场景：突发事件、流动性干涸、冲击后回归、whipsaw

## 7. 注意事项

- 当前是“策略回放框架 v1”，用于比较策略改动前后的相对效果，不是生产级收益承诺。
- 如果你要和实盘更一致，下一步建议补：
  - 手续费/滑点/延迟模型
  - 更真实的 queue 优先级
  - WS 增量事件驱动而非快照驱动
