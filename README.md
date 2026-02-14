# PM Trader Agent

一个面向 Polymarket 的量化交易研究仓库，当前聚焦两条主线：

- `pmm/`: Personal Market Maker（做市策略、回测、实盘/仿真引擎）
- `pm_arb_bot/`: YES/NO 价差套利机器人骨架

旧版 `agents/` 框架已移除，仓库现在以策略研究和回测工程为核心。

## 目录结构

```text
.
├── pmm/                       # 做市策略引擎 + 回测系统
│   ├── core/                  # 纯计算层
│   ├── engine/                # tick 主循环
│   ├── execution/             # paper/live broker + order manager
│   ├── data/                  # ws/rest 数据接入
│   ├── strategies/            # 策略插件
│   ├── backtest/              # 造数、回放、可视化
│   └── docs/                  # PMM 详细文档
├── pm_arb_bot/                # 原子套利策略骨架
├── scripts/python/
│   ├── pmm_backtest.py        # 回测总入口
│   ├── generate_backtest_scenarios.py
│   └── pmm_orderbook_capture.py
├── tests/pmm_tests/           # PMM 单元测试
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
  --catalog pmm/backtest/case_catalog.json \
  --out-dir pmm/backtest/scenarios

# 跑全部场景
python scripts/python/pmm_backtest.py run-all \
  --scenarios-dir pmm/backtest/scenarios \
  --out-dir pmm/backtest/.artifacts/results_all

# 结果绘图
python scripts/python/pmm_backtest.py plot-all \
  --results-dir pmm/backtest/.artifacts/results_all
```

### 4) PMM 实时采集（生成可复用回测数据）

```bash
python scripts/python/pmm_orderbook_capture.py capture \
  --tokens "YES_TOKEN_ID,NO_TOKEN_ID" \
  --duration 120 \
  --out-scenario pmm/backtest/.artifacts/recorded/live_sample.json
```

### 5) PM_ARB 本地仿真

```bash
export PM_ARB_MARKET_DATA_SOURCE=mock
export PM_ARB_MOCK_SCENARIO=toggle
export PM_ARB_DRY_RUN=1
python -m pm_arb_bot.main
```

## 测试

```bash
pytest tests/pmm_tests -q
```

## 文档

- PMM 文档导航：`pmm/docs/README.md`
- 架构说明：`pmm/docs/ARCHITECTURE.md`
- 回测方法：`pmm/docs/BACKTEST_SCENARIO_METHOD.md`

## License

MIT (`LICENSE.md`)
