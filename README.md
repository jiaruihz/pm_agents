# PM Trader Agent

> **Agent 快速定向（必读）**: 当前主线是**天气温度策略**。  
> 天气策略代码: `weather_dashboard/`（FastAPI + SQLite 后端）+ `frontend/strategy_dashboard/`（React 前端）。  
> 启动看板: `scripts/weather_dashboard/run_stack.sh`。详见 `CLAUDE.md`。

---

一个面向 Polymarket 的量化交易研究仓库。当前活跃主线是**天气温度策略**。

## 当前主线：天气温度策略

### 目录结构

```text
weather_dashboard/          ← Python 后端（FastAPI + SQLite + ingest 管道）
  db/schema.sql             ← 数据库 schema（信号/计划/订单/成交/结算/策略版本）
  api/                      ← FastAPI routers
  ingest/                   ← CSV/JSONL → DB 的 ingest 脚本

frontend/strategy_dashboard/← React 前端（Vite + TypeScript）
  src/pages/weather/        ← 天气策略页面

scripts/weather_dashboard/  ← 一键启动脚本
  run_stack.sh              ← 启动全栈（建库 + ingest + API + 前端）

scripts/ops/                ← 生产运维脚本（sync、doctor、live cycle 等）

runtime/weather_edge_v1/    ← 数据目录（N100 镜像 + DB + 日志，不进 git）
  market_data/              ← N100 rsync 镜像（snapshots / CSV）
  weather.db                ← SQLite 分析库（可随时删掉重建）

docs/                       ← 设计文档（见下方）
src/strategies/weather_edge_v1/ ← 策略核心逻辑（信号/计划/执行）
```

### 一键启动

```bash
# 全量启动（建库 + ingest + API :8000 + 前端 :5173）
scripts/weather_dashboard/run_stack.sh

# 已有 DB，只启服务
scripts/weather_dashboard/run_stack.sh --no-rebuild

# 查看状态
scripts/weather_dashboard/run_stack.sh --status
```

打开:
- 看板: http://localhost:5173/weather/runs
- Live 监控: http://localhost:5173/weather/live
- API: http://localhost:8000/docs

### 关键文档

| 文档 | 用途 |
|---|---|
| [docs/WEATHER_SYSTEM_CONTRACT.md](docs/WEATHER_SYSTEM_CONTRACT.md) | N100↔pm_agent 字段名/枚举/ID契约（改字段必读） |
| [docs/WEATHER_STRATEGY_ENTRYPOINT.md](docs/WEATHER_STRATEGY_ENTRYPOINT.md) | 实盘排查入口（N100状态/live口径/关键命令） |
| [docs/WEATHER_STRATEGY_QUANT_DESIGN.md](docs/WEATHER_STRATEGY_QUANT_DESIGN.md) | 核心量化系统架构设计（血缘链/DB schema/API/前端） |
| [docs/WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md](docs/WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md) | 数据模型缺口审计（P0已完成，P1待做） |
| [docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md](docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md) | 早期实盘历史与数据治理 |
| [docs/WEATHER_EXECUTION_ARCHITECTURE.md](docs/WEATHER_EXECUTION_ARCHITECTURE.md) | 实盘执行链路架构 |
| [docs/OPS_RUNBOOK.md](docs/OPS_RUNBOOK.md) | 运维手册 |

### 生产环境

- 生产端（N100）: `jiarui@192.168.0.200:/home/jiarui/projects/weather-predict`（信号生成/实盘下单/结算）
- 分析端（本机）: `/home/rui/projects/pm_agent`（dashboard/回测/研究）

N100 数据同步:
```bash
scripts/ops/sync_weather_remote.sh
```

---

## 其他策略（非活跃）

`src/strategies/` 下还有 `pmm/`、`arb/`、`rule_lawyer/` 等，目前非活跃主线，文档已移至 `docs/archive/`。

## 测试

```bash
pytest tests/ -q
```

## License

MIT (`LICENSE.md`)
