# PM Trader Agent

一个面向 Polymarket 的量化交易研究仓库，当前聚焦三条业务线：

- `src/domains/pmm/`: Personal Market Maker（做市策略、回测、实盘/仿真引擎）
- `src/domains/arb/`: YES/NO 价差套利机器人骨架
- `src/domains/research/`: 研究筛选流水线（Gamma/CLOB 同步、LLM 规则解析、候选评分）

旧版 `agents/` 框架已移除，仓库现在以策略研究和回测工程为核心。

## 目录结构

```text
.
├── src/
│   ├── domains/
│   │   ├── pmm/               # 做市策略引擎 + 回测系统
│   │   ├── arb/               # 套利策略域
│   │   └── research/          # 研究流水线域
│   ├── platform/              # 公共基础设施（clients/storage）
│   ├── interfaces/
│   │   ├── cli/               # 对外命令入口（预留）
│   │   └── web/               # 对外 Web 入口（预留）
│   └── workflows/
│       ├── backtest/          # 回测跨域编排（预留）
│       └── pap/               # PAP 跨域编排（预留）
├── scripts/python/
│   ├── pmm_backtest.py        # 回测总入口
│   ├── generate_backtest_scenarios.py
│   └── pmm_orderbook_capture.py
├── scripts/research/          # research 相关脚本
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

### 3) PMM 回测

```bash
# 生成场景
python scripts/python/pmm_backtest.py generate \
  --catalog src/domains/pmm/backtest/case_catalog.json \
  --out-dir src/domains/pmm/backtest/scenarios

# 跑全部场景
python scripts/python/pmm_backtest.py run-all \
  --scenarios-dir src/domains/pmm/backtest/scenarios \
  --out-dir src/domains/pmm/backtest/.artifacts/results_all

# 结果绘图
python scripts/python/pmm_backtest.py plot-all \
  --results-dir src/domains/pmm/backtest/.artifacts/results_all
```

### 4) PMM 实时采集（生成可复用回测数据）

```bash
python scripts/python/pmm_orderbook_capture.py capture \
  --tokens "YES_TOKEN_ID,NO_TOKEN_ID" \
  --duration 120 \
  --out-scenario src/domains/pmm/backtest/.artifacts/recorded/live_sample.json
```

### 5) PM_ARB 本地仿真

```bash
export PM_ARB_MARKET_DATA_SOURCE=mock
export PM_ARB_MOCK_SCENARIO=toggle
export PM_ARB_DRY_RUN=1
python -m src.domains.arb.main
```

### 6) PM Research 运行

```bash
# 初始化研究库
python -m src.domains.research.cli init-db

# 拉取 Gamma 市场/事件
python -m src.domains.research.cli sync --active true --pages 3 --page-size 100

# 拉取价格与订单簿
python -m src.domains.research.cli enrich --limit 300 --top-n 20

# 规则解析（需配置 LLM_*）
python -m src.domains.research.cli parse --llm --batch 100

# Telegram 通知测试（需配置 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID）
python -m src.domains.research.cli notify-telegram "Hello from pm_agent"
```

## 测试

```bash
pytest tests/pmm_tests -q
pytest tests/research_tests -q
```

## 文档

- PMM 文档导航：`src/domains/pmm/docs/README.md`
- 架构说明：`src/domains/pmm/docs/ARCHITECTURE.md`
- 回测方法：`src/domains/pmm/docs/BACKTEST_SCENARIO_METHOD.md`
- RESEARCH 整合说明：`docs/RESEARCH_INTEGRATION.md`
- RESEARCH 原文档：`docs/research/RESEARCH_README.md`

## License

MIT (`LICENSE.md`)
