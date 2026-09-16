# Weather Edge 策略进度与执行手册

> 最近更新：2026-03-04
> 适用范围：仅 `weather_edge_v1`

## 1. 策略目标与核心逻辑

`weather_edge_v1` 不是双边做市，而是事件驱动的单边 carry：

- 交易对象：天气市场极端区间的 `NO` token
- 主要收益：时间流逝导致不确定性收敛，`NO` 价格向 1 逼近的中间价差
- 基本动作：开盘附近建仓 -> 持有一段时间 -> 结算前提前平仓
- 风险原则：严格止损，不拖到尾盘结算

简化流程：

1. 过滤 token：若配置了 `weather_no_token_ids`，仅交易白名单 token。  
2. 入场判断：`mid` 在安全区间、点差不超阈值、冷却已结束。  
3. 建仓：按可用 USDC 的固定比例下 `BUY`（默认 5%）。  
4. 持仓管理：动态检查止盈、止损、最长持有、结算前离场。  
5. 离场：触发任一条件即 `SELL` 平仓。  

---

## 2. 当前开发进度（仅本策略）

### 2.1 已完成

- 策略实现：
  - `src/strategies/pmm/variants/weather_edge_v1.py`
- 引擎接入（可运行）：
  - `src/strategies/pmm/engine/tick_engine.py`
- 回测接入（可回放）：
  - `src/strategies/pmm/backtest/replay_runner.py`
- 策略导出注册：
  - `src/strategies/pmm/variants/__init__.py`
- 文档接入（概要级）：
  - `docs/pmm/STRATEGY_PLAYBOOK.md`
  - `docs/pmm/ARCHITECTURE.md`
  - `docs/pmm/BACKTEST_SCENARIO_METHOD.md`
- 单元测试：
  - `tests/pmm_tests/test_weather_theta_no_strategy.py`
  - 本地验证：`PYTHONPATH=. .venv/bin/pytest -q tests/pmm_tests/test_weather_theta_no_strategy.py`
  - 最近结果：`5 passed`（2026-03-04）

### 2.2 当前能力边界

- 已具备：可执行、可回测、可参数化调参。
- 未闭环：天气数据源自动接入与“安全边界”动态计算（目前由参数手动驱动）。
- 未完成：14 城市 token 清单与结算时间自动维护（可先手工配置）。

---

## 3. 如何执行（最小可运行）

推荐使用策略包脚本：

```bash
bash src/strategies/weather_edge_v1/run.sh
```

### 3.1 Paper 模式启动

```bash
cd /home/rui/projects/pm_agent
set -a; source .env; set +a

export PMM_EXECUTION_MODE="paper"
export PMM_STRATEGY_KEY="weather_edge_v1"
export PMM_TOKEN_IDS="NO_TOKEN_ID"   # 可放多个 token，逗号分隔
export PMM_STRATEGY_PARAMS_JSON='{
  "weather_no_token_ids": ["NO_TOKEN_ID"],
  "weather_entry_min_price": 0.80,
  "weather_entry_max_price": 0.96,
  "weather_position_pct": 0.05,
  "weather_take_profit_abs": 0.01,
  "weather_stop_loss_abs": 0.03,
  "weather_max_hold_hours": 48,
  "weather_exit_before_hours": 6,
  "weather_reentry_cooldown_hours": 12
}'

.venv/bin/python -u -m src.strategies.pmm.main
```

### 3.2 快速验证点

- 日志里应看到 `strategy_selected` 且 `strategy_key=weather_edge_v1`
- 价格进入区间时应出现 `BUY` 挂单
- 触发止盈/止损/超时/临近结算时应出现 `SELL` 挂单

---

## 4. 参数配置说明（后续怎么配）

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `weather_no_token_ids` | `[]` | 允许交易的 token 白名单 |
| `weather_entry_min_price` | `0.78` | 入场下限 |
| `weather_entry_max_price` | `0.96` | 入场上限 |
| `weather_entry_max_spread` | `0.06` | 入场最大点差 |
| `weather_position_pct` | `0.05` | 单次建仓预算占可用 USDC 比例 |
| `weather_min_order_notional` | `1.0` | 最小下单金额 |
| `weather_take_profit_abs` | `0.01` | 止盈绝对价差（相对入场中价） |
| `weather_stop_loss_abs` | `0.03` | 止损绝对价差（相对入场中价） |
| `weather_min_hold_hours` | `0.0` | 最短持有时间（止盈前） |
| `weather_max_hold_hours` | `48.0` | 最长持有时间 |
| `weather_exit_before_hours` | `6.0` | 距结算前强制平仓窗口 |
| `weather_reentry_cooldown_hours` | `12.0` | 平仓后重入冷却 |
| `weather_allow_reentry` | `true` | 是否允许重入 |
| `weather_force_flat_on_range_break` | `true` | 跌出安全区是否强制平仓 |
| `weather_token_end_ts` | `{}` | 每个 token 的结算时间（epoch 秒） |

建议起步配置：

- `weather_position_pct=0.03~0.05`
- `weather_entry_min_price=0.80`
- `weather_entry_max_price=0.95~0.96`
- `weather_stop_loss_abs=0.02~0.03`
- `weather_take_profit_abs=0.008~0.015`

---

## 5. 扩展开发规范（后续怎么拓展）

### 5.1 参数扩展规范

- 新参数统一使用 `weather_*` 前缀。
- 新参数必须给默认值，确保旧配置不崩。
- 参数语义必须单一，不复用同一参数做多重含义。

### 5.2 逻辑扩展顺序

1. 先扩风险触发：止损、强平、异常天气波动保护。  
2. 再扩收益触发：止盈分层、分批减仓。  
3. 最后扩入场优化：更复杂的天气预报特征与动态阈值。  

### 5.3 测试扩展要求

- 每新增一条触发逻辑，至少补 3 类测试：
  - 可触发（应下单）
  - 不触发（不应下单）
  - 边界值（阈值附近）

---

## 6. 里程碑与下一步

### M1（已完成）

- 策略实现 + 引擎接入 + 回测接入 + 单元测试。

### M2（建议下一步）

- 建立 14 城市天气市场 token 配置模板（含 `weather_token_end_ts`）。
- 提供每日自动刷新脚本，降低手工维护成本。

### M3（建议下一步）

- 接入天气预报数据源，动态更新“安全区间”和入场阈值。
- 增加策略表现统计：入场后持有时长分布、触发类型占比、止损频率。

### M4（建议下一步）

- 多城市组合资金分配与流动性约束。
- 引入分层止盈和分批离场，降低单价位依赖。
