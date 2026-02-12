# PMM 架构说明

> **最近更新**：2026-02-12（重构后）

## 概览

PMM（Polymarket Market Maker）是一个分层做市系统，支持：

- 策略插件化（`single_level_v1` / `multi_level_v1`）
- `paper` / `live` 执行模式切换
- 真实数据录制、场景回放、批量回测与绘图

## 目录结构

```text
pmm/
├── config.py, main.py            # 程序入口与全局配置
├── engine/
│   ├── tick_engine.py            # 主编排引擎
│   └── context_builder.py        # token/历史上下文构建
│
├── core/                         # 纯计算层（无 I/O）
│   ├── pricing.py                # 报价公式
│   ├── signals.py                # 信号计算（weighted_mid、OFI、波动率、动量等）
│   ├── sizing.py                 # 仓位规模计算
│   ├── anchoring.py              # 报价锚定到盘口
│   ├── strategy_base.py          # 策略协议 + 数据结构
│   └── strategy_registry.py      # 策略注册表
│
├── data/                         # 数据 I/O 层
│   ├── http_client.py            # REST 客户端
│   ├── market_ws.py              # WebSocket 行情 + 本地 L2 盘口
│   ├── orderbook.py              # 盘口工具函数
│   └── parsers.py                # 接口响应解析
│
├── execution/                    # 执行层
│   ├── broker_interface.py       # Broker 抽象接口
│   ├── live_broker.py            # 实盘执行（支持 dry_run）
│   ├── paper_broker.py           # 模拟执行（本地撮合）
│   └── order_manager.py          # 挂撤单 diff + deadband
│
├── risk/                         # 风控层
│   ├── safety_guard.py           # 下单前检查（白名单、肥手指、日亏损）
│   └── circuit_breaker.py        # 行情突变熔断
│
├── utils/                        # 公共工具
│   ├── converters.py             # 类型转换
│   ├── quantize.py               # 价格 tick 对齐
│   └── metrics.py                # 指标日志写入
│
├── strategies/                   # 策略插件
│   ├── single_level_v1.py        # 单档报价
│   └── multi_level_v1.py         # 多档梯度报价
│
└── backtest/                     # 回测基础设施
    ├── replay_runner.py          # 场景回放引擎
    ├── recorder.py               # 真实数据录制器
    ├── scenario_generator.py     # 合成场景生成
    ├── scenario_validator.py     # 场景校验器
    └── plotter.py                # 结果可视化
```

## 分层依赖规则

```text
engine/tick_engine.py（编排）
  ↓
strategies/（策略插件）
  ↓
core/（纯计算）
  ↓
execution/ + risk/（状态组件）
  ↓
data/（I/O）
  ↓
utils/（通用基础）
```

约束：

- `core/` 不应依赖 `execution/`、`risk/`、`data/`、`strategies/`
- `data/` 不应依赖 `core/`、`execution/`、`strategies/`
- `utils/` 不应依赖其他 `pmm` 子包

## Tick 主流程

```text
1) 拉账户状态
   balance + open_orders + positions

2) 拉行情数据
   ws -> 本地 L2 缓存（过期时 fallback REST）

3) 熔断检查
   跳价超过阈值 -> cancel_all + 停止/冷却

4) 信号计算（逐 token）
   inventory_signal -> realized_vol -> required_spread
   -> OFI / momentum -> side block

5) 策略路由与报价
   strategy_key -> StrategyRegistry -> strategy 实现
   -> quote_targets

6) 执行挂撤
   diff_multi(open_orders + pending_orders, side_targets)
   -> cancel + place(1..N levels)

7) 周期性 Auto Merge
   min(yes_pos, no_pos) >= threshold -> merge -> USDC

8) 指标落盘
   strategy_key + quote_runtime + pnl/signals
```

## 策略系统

策略实现统一遵循 `MarketMakingStrategy` 协议。

当前策略：

- `single_level_v1`：每侧 1 档
- `multi_level_v1`：每侧 N 档（可配步长和 size 衰减）

## 执行模式

由环境变量 `PMM_EXEC_MODE` 控制：

| 模式 | Broker | 用途 |
|------|--------|------|
| `paper` | `PaperBroker` | 回测/仿真（真实行情 + 本地撮合） |
| `live` | `LiveBroker` | 实盘执行（支持 dry_run） |

## 行情来源

由环境变量 `PMM_MARKET_DATA_SOURCE` 控制：

| 来源 | 延迟 | 说明 |
|------|------|------|
| `ws` | ~ms（推送） | 本地维护 L2、自动重连、过期检测 |
| `rest` | ~tick 间隔 | 轮询简单，但延迟更高 |

## 风控

- `SafetyGuard`：白名单、价格范围、肥手指、日亏损
- `CircuitBreaker`：跳价检测，触发后自动 `cancel_all`
- `In-flight Guard`：把 `pending_orders` 视为已存在订单参与 diff，减少确认延迟导致的重复下单

## 回测流程

```text
1. record-live   录制真实数据
2. convert-live  转换为场景
3. run           单场景回测
4. run-all       全场景回测
5. plot          结果可视化
```

撮合模型：

- `conservative`：要求穿价（`best_ask <= bid - ε`）
- `optimistic`：触价成交（`best_ask <= bid + ε`）

详见：`BACKTEST_SCENARIO_METHOD.md`。

独立录制能力（可复用）：

```bash
# 录制真实 orderbook + trade_flow 估计（输出 scenario + jsonl）
python scripts/python/pmm_orderbook_capture.py capture \
  --tokens "YES_TOKEN,NO_TOKEN" \
  --duration 180 \
  --interval 1.0 \
  --out-scenario pmm/backtest/.artifacts/recorded/sample.json

# 把 jsonl 转成可直接 replay 的 scenario
python scripts/python/pmm_orderbook_capture.py convert \
  --jsonl pmm/backtest/.artifacts/recorded/sample.jsonl.gz \
  --out-scenario pmm/backtest/.artifacts/recorded/sample_converted.json
```

## 录制落盘规范（Bronze）

- 写入模型：主线程仅入队，后台异步线程落盘（`AsyncJsonlWriter`），避免 tick 主循环被磁盘 I/O 阻塞。
- 压缩格式：默认 `jsonl.gz`；如安装 `zstandard` 可使用 `jsonl.zst`。
- 双时间戳：每个 tick 必须同时记录
  - `ts_event`：交易所事件时间（用于回放撮合）
  - `ts_ingest`：本地接收时间（用于延迟与数据新鲜度分析）
- 产物分层：
  - `pmm/backtest/.artifacts/`：运行产物，默认不入库
  - `pmm/backtest/metadata/`：汇总元数据，可入库

## 配置

全部通过环境变量配置，示例：

```bash
# 基础
PMM_TOKEN_IDS="token1,token2"
PMM_EXEC_MODE="paper"  # or "live"
PMM_MARKET_DATA_SOURCE="ws"  # or "rest"
PMM_STRATEGY_KEY="multi_level_v1"

# 策略
PMM_BASE_SIZE=10.0
PMM_MAX_POSITION=500.0
PMM_TARGET_PROFIT_SPREAD=0.002

# 风控
PMM_CIRCUIT_BREAKER_THRESHOLD=0.05
PMM_MAX_DAILY_LOSS=50.0
```

完整参数见 `config.py`。

## 指标日志

每个 tick 会向 `pmm_logs/metrics.jsonl` 追加一行 JSON。
字段规范见：`METRICS_FORMAT.md`。

## 当前状态

已完成：

- 分层结构基础搭建
- 多档报价策略
- Paper 撮合（含 BBO join 行为）
- 真实数据录制 + 场景回放
- WebSocket 行情接入（自动重连）
- OFI + 动量信号
- SafetyGuard + CircuitBreaker

进行中：

- `LiveBroker` API 细化接入

后续路线：见 `TODO_IMPROVEMENTS.md`。
