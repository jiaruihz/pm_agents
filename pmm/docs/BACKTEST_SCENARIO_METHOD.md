# Backtest & Scenario Replay

场景造数、策略回放、真实数据录制与结果分析的方法说明。

> 文档分工：`pmm/docs/EXECUTION_PLAN_REALDATA_MULTI_LEVEL.md` 负责阶段计划，本文件负责可执行细则，避免冗余维护。

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

独立造数脚本：`scripts/python/generate_backtest_scenarios.py`

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
| `trade_flow` (逐 tick 生成) | buy/sell taker qty | 主动单流，用于队列成交模拟 |
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
- 每个 tick 额外生成 `trade_flow`（YES/NO 各自的 `buy_taker_qty` / `sell_taker_qty`）

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
4. `PaperBroker.on_market_data(orderbooks, trade_flow)` 撮合
5. 记录 metrics + actions

撮合模型（当前版本）：
- `cross_fill`：盘口穿价触发成交
- `queue_fill`：仅在有 `trade_flow` 时才允许 BBO 队列成交
- `conservative` 相比 `optimistic` 使用更保守的 BBO 队列份额

回放前置校验（已接入）：

- `run` / `run-all` / `run-all-fill-models` 在执行前会先校验 scenario 结构。
- 严重问题（如盘口非单调、负价格/负数量）会直接失败并阻止回测。

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

### 多 fill model 联跑输出

```
results_fill_models/
├── <scenario_id>__fill_conservative/
├── <scenario_id>__fill_optimistic/
├── summary_all_fill_models.json
├── summary_all_fill_models_table.txt
└── summary_all_fill_models_table.csv
```

`compare_summary.json` 关键字段：

- `runs`：每个 profile 一条 `summary`（含 `quote_runtime`）
- `best_by_pnl` / `worst_by_pnl`：按 `pnl_end` 排序后的最好/最差方案
- `best_pnl_value` / `worst_pnl_value`：对应收益值

`summary_all_fill_models_table.txt` 字段：

- `Scenario`：场景名（不含 fill model 后缀）
- `FillModel`：`conservative` / `optimistic`
- `PnL`：`pnl_end`
- `Fills`：`total_fills`（至少成交过一次的订单数）
- `Orders`：`total_placed`
- `Fill%`：`total_fills / total_placed`
- `MDD`：`max_drawdown`

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
| `total_placed` / `total_canceled` / `total_fills` | 操作计数（`total_fills`=被成交订单数） |
| `total_fill_events` | 成交事件条数（可大于 `total_fills`） |
| `total_filled_qty` | 成交总数量（按份额） |
| `fill_rate_per_order` | `fills / placed` |
| `avg_pnl_per_tick` | 各 tick PnL 均值 |
| `strategy_key` | 本次回测实际使用的策略实现 key |
| `quote_runtime` | 报价运行元信息（请求档位/生效档位） |
| `strategy_overrides` | 本次回测实际覆盖参数 |
| `scenario_validation` | 回放前数据校验摘要（ok/errors/warnings/stats） |

### quote_runtime 字段说明

- `quote_levels_requested`：配置请求档位（例如 3）
- `quote_levels_effective`：当前实际生效档位（`single_level_v1`=1；`multi_level_v1`=请求值）
- `multi_level_quote_enabled`：当前策略是否为多档实现（`strategy_key=multi_level_v1`）
- `multi_level_placeholder_active`：请求 > 1 但当前策略不是多档时为 `true`

策略路由相关：

- 环境变量：`PMM_STRATEGY_KEY`（默认 `single_level_v1`）
- 当前实现：`single_level_v1`、`multi_level_v1`
- 多档是否生效：由 `strategy_key=multi_level_v1` + `quote_levels>1` 决定

这保证了：

- 默认不改现有单档行为（仍可用 `single_level_v1` 做基准）
- 可以直接把多档配置放进回测对照组
- 通过比较 `quote_levels_effective` 从 1→N 评估多档收益差异

---

## 4. 命令

```bash
# 生成全部场景
./venv/bin/python scripts/python/pmm_backtest.py generate \
  --catalog pmm/backtest/case_catalog.json \
  --out-dir pmm/backtest/scenarios \
  --seed 42

# 独立造数脚本（便于外部 AI/自动化调用）
./venv/bin/python scripts/python/generate_backtest_scenarios.py \
  --catalog pmm/backtest/case_catalog.json \
  --out-dir pmm/backtest/scenarios \
  --seed 42 \
  --show-initial \
  --show-sample b30_event_spike_then_revert

# 跑单个场景
./venv/bin/python scripts/python/pmm_backtest.py run \
  --scenario pmm/backtest/scenarios/b50_oscillating_fill.json \
  --out-dir pmm/backtest/results

# 批量回测
./venv/bin/python scripts/python/pmm_backtest.py run-all \
  --scenarios-dir pmm/backtest/scenarios \
  --out-dir pmm/backtest/results

# 同时跑 conservative + optimistic，并输出同一张结果榜单
./venv/bin/python scripts/python/pmm_backtest.py run-all-fill-models \
  --scenarios-dir pmm/backtest/scenarios \
  --out-dir pmm/backtest/results_fill_models \
  --fill-models conservative,optimistic

# 同场景多参数对比（先用于单档，后续可直接纳入多档）
./venv/bin/python scripts/python/pmm_backtest.py compare \
  --scenario pmm/backtest/scenarios/b50_oscillating_fill.json \
  --profiles pmm/backtest/compare_profiles_single_level.json \
  --out-dir pmm/backtest/results_compare

# 多档策略对比（示例：single vs multi_level_v1）
# profiles 文件中可设置:
# - strategy_key: multi_level_v1
# - quote_levels: 3
# - level_spread_step: 0.01
# - level_size_decay: 0.6

# 单场景绘图
./venv/bin/python scripts/python/pmm_backtest.py plot \
  --result-dir pmm/backtest/results/b50_oscillating_fill

# 批量汇总绘图
./venv/bin/python scripts/python/pmm_backtest.py plot-all \
  --results-dir pmm/backtest/results

# 录制真实 WS 盘口 + 估算订单流，直接输出 scenario + jsonl
./venv/bin/python scripts/python/pmm_backtest.py record-live \
  --tokens <YES_TOKEN_ID>,<NO_TOKEN_ID> \
  --duration 300 \
  --interval 1.0 \
  --out-scenario pmm/backtest/scenarios/recorded_live.json \
  --out-jsonl pmm/backtest/scenarios/recorded_live.jsonl \
  --initial-usdc 100 \
  --initial-positions-json '{"<YES_TOKEN_ID>":50,"<NO_TOKEN_ID>":50}'

# 将已有 jsonl 转换为标准 scenario 格式
./venv/bin/python scripts/python/pmm_backtest.py convert-live \
  --jsonl pmm/backtest/scenarios/recorded_live.jsonl \
  --out-scenario pmm/backtest/scenarios/recorded_live_converted.json \
  --tokens <YES_TOKEN_ID>,<NO_TOKEN_ID> \
  --initial-usdc 100 \
  --initial-positions-json '{"<YES_TOKEN_ID>":50,"<NO_TOKEN_ID>":50}'

# 校验单个 scenario
./venv/bin/python scripts/python/pmm_backtest.py validate \
  --scenario pmm/backtest/scenarios/recorded_live_converted.json

# 校验整个目录
./venv/bin/python scripts/python/pmm_backtest.py validate-dir \
  --scenarios-dir pmm/backtest/scenarios

# 严格模式：出现错误时返回非 0 退出码
./venv/bin/python scripts/python/pmm_backtest.py validate-dir \
  --scenarios-dir pmm/backtest/scenarios \
  --strict
```

真实数据录制说明：

- `record-live` 输出两份文件：
  - `scenario JSON`：可直接回测。
  - `jsonl`：逐 tick 原始快照序列，便于审计和重转换。
- `trade_flow` 当前来自盘口深度变化估计，属于近似值：
  - ask 深度减少 -> `buy_taker_qty`
  - bid 深度减少 -> `sell_taker_qty`
- 该估算会混入撤单噪音，适合做“保守回测”的输入，不等同逐笔真实成交流。

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

---

## 7. 数据质量门禁（Phase C）

校验器：`pmm/backtest/scenario_validator.py`

硬规则（error）：

- `scenario_id` 非空字符串
- `token_ids` 至少两个
- `ticks` 非空
- `bids` 价格递减、`asks` 价格递增
- 价格必须 `> 0`，数量不可为负

软规则（warning）：

- 缺失 `trade_flow`
- 某 token 在部分 tick 缺失 orderbook
- 出现 crossed book（`best_bid >= best_ask`）

输出字段：

- `ok`
- `errors_count` / `warnings_count`
- `stats`（覆盖率与异常 tick 计数）
- `errors[]` / `warnings[]`
