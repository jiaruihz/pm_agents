# PM Trader Agent

一个面向 Polymarket 的量化交易研究仓库，当前聚焦三条业务线：

- `src/strategies/pmm/`: Personal Market Maker（做市策略、回测、实盘/仿真引擎）
- `src/strategies/arb/`: YES/NO 价差套利机器人骨架
- `src/strategies/rule_lawyer/`: 研究筛选流水线（Gamma/CLOB 同步、LLM 规则解析、候选评分）

旧版 `agents/` 框架已移除，仓库现在以策略研究和回测工程为核心。

## 目录结构

```text
.
├── src/
│   ├── platform/              # 公共基础设施（clients/storage）
│   ├── interfaces/
│   │   ├── cli/               # 对外命令入口（预留）
│   │   └── web/               # 对外 Web 入口（统一策略看板 BFF）
│   ├── strategies/            # 策略实现 + 全局策略目录（manifest + runbook + params）
│   └── workflows/
│       ├── backtest/          # 回测跨域编排（预留）
│       └── pap/               # PAP 跨域编排（预留）
├── scripts/python/
│   ├── pmm_backtest.py        # 回测总入口
│   ├── strategy_catalog.py    # 策略目录管理入口
│   ├── generate_backtest_scenarios.py
│   └── pmm_orderbook_capture.py
├── tests/pmm_tests/           # PMM 单元测试
├── tests/research_tests/      # research 单元测试
└── .env.example
```

## 快速开始

### 1) 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) 环境变量

```bash
cp .env.example .env
```

运行实例主库使用 `STRATEGY_RUNTIME_DB_PATH`（默认 `runtime/strategy_runtime.db`）。
`PMM_INSTANCE_DB_PATH` 仅保留兼容读取，已弃用（deprecated）。
Research 主库默认 `RESEARCH_DB_PATH=runtime/db/research.db`（兼容旧 `research.db` 自动迁移/回退）。

### 3) PMM 回测

```bash
# 生成场景
python scripts/python/pmm_backtest.py generate \
  --catalog src/strategies/pmm/backtest/case_catalog.json \
  --out-dir src/strategies/pmm/backtest/scenarios

# 跑全部场景
python scripts/python/pmm_backtest.py run-all \
  --scenarios-dir src/strategies/pmm/backtest/scenarios \
  --out-dir src/strategies/pmm/backtest/.artifacts/results_all

# 结果绘图
python scripts/python/pmm_backtest.py plot-all \
  --results-dir src/strategies/pmm/backtest/.artifacts/results_all
```

### 4) PMM 实时采集（生成可复用回测数据）

```bash
python scripts/python/pmm_orderbook_capture.py capture \
  --tokens "YES_TOKEN_ID,NO_TOKEN_ID" \
  --duration 120 \
  --out-scenario src/strategies/pmm/backtest/.artifacts/recorded/live_sample.json
```

### 5) PM_ARB 本地仿真

```bash
export PM_ARB_MARKET_DATA_SOURCE=mock
export PM_ARB_MOCK_SCENARIO=toggle
export PM_ARB_DRY_RUN=1
python -m src.strategies.arb.main
```

### 6) PM Research 运行

```bash
# 初始化研究库
python -m src.strategies.rule_lawyer.cli init-db

# 拉取 Gamma 市场/事件
python -m src.strategies.rule_lawyer.cli sync --active true --pages 3 --page-size 100

# 拉取价格与订单簿
python -m src.strategies.rule_lawyer.cli enrich --limit 300 --top-n 20

# 规则解析（需配置 LLM_*）
python -m src.strategies.rule_lawyer.cli parse --llm --batch 100

# Telegram 通知测试（需配置 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID）
python -m src.strategies.rule_lawyer.cli notify-telegram "Hello from pm_agent"
```

### 7) 统一策略看板（BFF）

```bash
PYTHONPATH=. .venv/bin/python -m src.interfaces.web.strategy_dashboard_server \
  --host 127.0.0.1 \
  --port 8011 \
  --artifacts-dir src/strategies/pmm/backtest/.artifacts \
  --runtime-dir runtime
```

健康检查：`http://127.0.0.1:8011/api/v1/health`

## 测试

```bash
pytest tests/pmm_tests -q
pytest tests/research_tests -q
```

## 文档

- PMM 文档导航：`docs/pmm/README.md`
- 策略目录（全局）：`src/strategies/README.md`
- 天气策略进度手册：`docs/pmm/WEATHER_THETA_NO_PROGRESS.md`
- PMM paper 运维手册：`docs/pmm/PAPER_RUNBOOK.md`
- 架构说明：`docs/pmm/ARCHITECTURE.md`
- 回测方法：`docs/pmm/BACKTEST_SCENARIO_METHOD.md`
- RESEARCH 整合说明：`docs/RESEARCH_INTEGRATION.md`
- RESEARCH 原文档：`docs/research/RESEARCH_README.md`
- 统一策略看板计划：`docs/STRATEGY_DASHBOARD_PLAN.md`

## License

MIT (`LICENSE.md`)
