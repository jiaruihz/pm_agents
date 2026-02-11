# Backtest & Scenario Replay

场景造数、策略回放、结果分析的方法说明。

## 文件结构

```
pmm/backtest/
├── case_catalog.json        场景参数目录
├── scenario_generator.py    catalog → 逐 tick 场景文件
├── replay_runner.py          策略回放引擎
├── scenarios/                生成的场景文件（gitignore）
└── results/                  回测结果（gitignore）
```

CLI 入口：`scripts/python/pmm_backtest.py`

---

## 1. 造数

### 参数维度

| 维度 | 值域 | 控制什么 |
|------|------|----------|
| `base_mid` | 0.30 / 0.50 / 0.70 | 概率中枢 |
| `pattern` | 见下表 | 价格路径形态 |
| `volatility` | float | 逐 tick 噪声幅度 |
| `drift_per_tick` | float | 趋势偏移速度 |
| `spread_base` / `spread_jitter` | float | 盘口价差基础值 + 随机扰动 |
| `depth_base` | float | 盘口深度基准 |
| `fillability` | low / medium / high | 成交难度（影响 `queue_share`） |
| `shock_tick` / `shock_jump` / `shock_len` | int/float | 事件冲击时点、幅度、持续长度 |
| `dryup_start` | int | 流动性衰竭起始 tick |

### 路径模式

| 模式 | 特征 | 考验什么 |
|------|------|----------|
| `stable` | 平稳窄幅 | 正常做市收益 |
| `trend_up` / `trend_down` | 单边走势 | 库存 skew 是否有效 |
| `oscillating` | 周期震荡 | 是否过度追价 |
| `shock_up` / `shock_down` | 突发跳变 | 熔断响应 |
| `regime_switch` | 平稳 → 趋势 → 震荡 → 反转 | 策略适应性 |
| `whipsaw` | 来回拉扯 | 库存管理在快速反转中的表现 |
| `liquidity_dryup` | 深度逐渐衰竭 | spread 过窄时的退出逻辑 |
| `spike_revert` / `drop_revert` | 冲击后均值回归 | 冲击中的防护和恢复 |

### YES / NO 联动

- YES 路径按模式生成
- NO = `1 - YES + 微扰`（避免完全对称）
- 盘口按 mid ± spread/2 生成 top level

---

## 2. 回放

回放引擎复用策略核心组件，不走网络：

```
replay_runner
  ├── StrategyCore 函数（定价、信号、锚定）
  ├── OrderManager（diff/deadband）
  └── PaperBroker（本地撮合、余额、仓位）
```

每 tick 执行：
1. 读取场景 orderbook 快照
2. 计算 mid / spread / 库存信号 / 动态 spread
3. 生成报价 → diff → 挂撤单
4. `PaperBroker.on_market_data()` 撮合
5. 记录 metrics + actions

---

## 3. 输出格式

### 单场景输出

```
results/<scenario_id>/
├── metrics.jsonl     每 tick 状态快照
├── actions.jsonl     操作流水（place / cancel / fill / error）
└── summary.json      汇总统计
```

### 批量输出

```
results/
├── <scenario_id>/    各场景目录
└── summary_all.json  全量聚合
```

### 对比输出（compare）

```
results_compare/
├── <scenario_id>__<profile_name>/
│   ├── metrics.jsonl
│   ├── actions.jsonl
│   └── summary.json
└── compare_summary.json
```

`compare_summary.json` 关键字段：

- `runs`：每个 profile 一条 `summary`（含 `quote_runtime`）
- `best_by_pnl` / `worst_by_pnl`：按 `pnl_end` 排序后的最好/最差方案
- `best_pnl_value` / `worst_pnl_value`：对应收益值

### actions.jsonl 字段

| type | 额外字段 |
|------|----------|
| `place` | `reason`, `token_id`, `side`, `price`, `size`, `order_id` |
| `cancel` | `reason`, `token_id`, `side`, `order_ids` |
| `cancel_all` | `order_ids` |
| `fill` | `order_id`, `token_id`, `side`, `price`, `size`, `remaining_size`, `ts` |
| `error` | `action`, `token_id`, `side`, `message` |

> 同一个 tick 可能有多条 action（YES/NO × BUY/SELL）。没有操作的 tick 不会写入——tick 序号"跳号"是正常行为。

### summary.json 字段

| 字段 | 说明 |
|------|------|
| `pnl_end` | 期末 PnL |
| `max_drawdown` | 最大回撤（基于 equity 曲线） |
| `total_placed` / `total_canceled` / `total_fills` | 操作计数 |
| `fill_rate_per_order` | `fills / placed` |
| `avg_pnl_per_tick` | 各 tick PnL 均值 |
| `quote_runtime` | 报价运行元信息（请求档位/生效档位） |
| `strategy_overrides` | 本次回测实际覆盖参数 |

### quote_runtime 字段说明（多档挂单预留口子）

- `quote_levels_requested`：配置请求档位（例如 3）
- `quote_levels_effective`：当前实际生效档位（当前版本固定为 1）
- `multi_level_quote_enabled`：是否启用多档实现
- `multi_level_placeholder_active`：请求 > 1 但仍按单档执行时为 `true`

这保证了：

- 现在不改现有单档行为
- 现在就能把“未来多档配置”放进回测对照组
- 未来真正实现多档后，直接比较 `quote_levels_effective` 从 1→N 的收益差异

---

## 4. 命令

```bash
# 生成全部场景
./venv/bin/python scripts/python/pmm_backtest.py generate \
  --catalog pmm/backtest/case_catalog.json \
  --out-dir pmm/backtest/scenarios \
  --seed 42

# 跑单个场景
./venv/bin/python scripts/python/pmm_backtest.py run \
  --scenario pmm/backtest/scenarios/b50_oscillating_fill.json \
  --out-dir pmm/backtest/results

# 批量回测
./venv/bin/python scripts/python/pmm_backtest.py run-all \
  --scenarios-dir pmm/backtest/scenarios \
  --out-dir pmm/backtest/results

# 同场景多参数对比（先用于单档，后续可直接纳入多档）
./venv/bin/python scripts/python/pmm_backtest.py compare \
  --scenario pmm/backtest/scenarios/b50_oscillating_fill.json \
  --profiles pmm/backtest/compare_profiles_single_level.json \
  --out-dir pmm/backtest/results_compare

# 单场景绘图
./venv/bin/python scripts/python/pmm_backtest.py plot \
  --result-dir pmm/backtest/results/b50_oscillating_fill

# 批量汇总绘图
./venv/bin/python scripts/python/pmm_backtest.py plot-all \
  --results-dir pmm/backtest/results
```

---

## 5. 图表与数据映射

单场景绘图后会生成：

```
results/<scenario_id>/plots/
├── equity_pnl.png
├── prices_quotes.png
├── positions_usdc.png
├── actions_timeline.png
└── plot_manifest.json
```

批量绘图后会生成：

```
results/plots/
├── summary_pnl_ranking.png
├── summary_drawdown_ranking.png
├── summary_fills_vs_orders.png
└── plot_manifest.json
```

`plot_manifest.json` 是“图-数关系”清单，记录每张图来自哪个源文件、哪个字段路径。  
例如：

- `equity_pnl.png`  
  `metrics.jsonl[*].equity`、`metrics.jsonl[*].pnl`
- `prices_quotes.png`  
  `metrics.jsonl[*].mids.<token_id>`、`metrics.jsonl[*].final_quotes.<token_id>.bid/ask`
- `actions_timeline.png`  
  `actions.jsonl[*].type`（按 tick 聚合计数）
- `summary_pnl_ranking.png`  
  `summary_all.json.scenarios[*].pnl_end`

这保证你在复盘时可以从任意一张图追溯回原始字段。

---

## 6. 局限性

当前是 v1 回放框架，用来对比策略改动前后的**相对**效果，不是绝对收益预测。后续需要补充：

- 手续费 / 滑点 / 网络延迟建模
- 更真实的队列优先级
- 增量事件驱动（替代快照驱动）
