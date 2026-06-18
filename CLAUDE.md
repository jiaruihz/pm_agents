# PM Agent — Project Context

> Codex 相关约定见 `AGENTS.md`；两份文件应保持核心项目规范一致。

## 当前主线：天气温度策略（必读，不要被 README 误导）

**README 描述的是旧 PMM/ARB 框架，当前活跃主线是天气策略。**

关键目录（不是 `src/strategies/`，是下面这些）：

```text
weather_dashboard/          ← Python 后端：FastAPI + SQLite DB + ingest 管道
  db/schema.sql             ← 数据库 schema（信号/计划/订单/成交/结算/策略版本）
  api/                      ← FastAPI routers
  ingest/                   ← CSV/JSONL → DB 的 ingest 脚本

frontend/strategy_dashboard/← React 前端（Vite + TypeScript）
  src/pages/weather/        ← 天气策略页面（WeatherRunsPage / HistoryPage / LivePage / ComparePage）
  src/data/weather-http.ts  ← API 客户端

scripts/weather_dashboard/  ← 一键启动脚本
  run_stack.sh              ← 启动全栈（建库 + ingest + API + 前端）

runtime/weather_edge_v1/    ← 数据目录（N100 镜像 + DB + 日志，不进 git）
  market_data/              ← N100 rsync 镜像（snapshots / paper_trades / research CSV）
  weather.db                ← SQLite 主库（可随时删掉重建，不是源头）

docs/                             ← 完整索引见下方"文档索引"节
  WEATHER_SYSTEM_CONTRACT.md      ← 字段名/枚举/ID 契约（改字段必读）
  WEATHER_STRATEGY_QUANT_DESIGN.md← 核心量化系统架构设计
  WEATHER_STRATEGY_ENTRYPOINT.md  ← 实盘排查入口（N100 状态 / live 口径）
```

生产端（N100 `192.168.0.200`）：`/home/jiarui/projects/weather-predict` + `/home/jiarui/projects/pm_agent`<br>
分析端（当前这台 Mac）：`/Users/deepsleep/projects/pm_agents`（dashboard / 回测 / 研究 / 部署 staging）<br>
历史 WSL 分析端：`/home/rui/projects/pm_agent`（只有在实际 shell 是 WSL/Linux 时才使用）

当前这台 Mac 可直接承担本机分析、看板、回测、脚本开发和 N100 部署 staging：
- 项目根目录：`/Users/deepsleep/projects/pm_agents`
- 默认 shell：macOS `zsh` / Darwin；不要套 `wsl -d ...`
- 本机命令示例：`cd /Users/deepsleep/projects/pm_agents && scripts/ops/sync_weather_remote.sh`
- N100 访问：从 Mac 直接 `ssh jiarui@192.168.0.200 '<command>'`
- 本机只做镜像分析和 staging；不会因为能跑脚本就自动成为生产采集/下单来源。

## 全局工程姿态：个人项目，默认直接推进

这是个人研究/交易项目，不是承载外部线上流量的多租户生产系统。默认实现时不要为了“看起来稳妥”层层加保守兜底、静默 fallback、双路径兼容或过度抽象。

默认偏好:
- **直接实现主路径**：优先把当前要验证的策略、看板或分析链路跑通，少写与当前目标无关的防御性分支。
- **显式失败优于静默兜底**：数据缺失、字段不一致、盘口不可用时，优先报错/告警并暴露原因；不要偷偷换旧字段、旧数据、默认值继续跑，除非文档已约定这是兼容层。
- **兼容逻辑要有退出条件**：如果必须兼容历史字段或旧文件，在代码/文档里标明原因和删除时机，不要无限期保留。
- **研究和本机工具可以激进**：回测、对比脚本、dashboard、本机分析默认选择可观测、可调参、可快速迭代的实现，而不是最保守的企业级兜底。
- **真实下单仍保留硬边界**：涉及 N100 live、私钥、余额、真实 CLOB 下单、删除数据、远端部署时，保留显式确认、暂停开关、notional 上限和可追溯日志；不要把“少兜底”理解成绕过资金安全或不可逆操作。

## Weather 策略接手入口

天气策略相关开发、实盘排查、回测分析优先从这里开始:

- [docs/WEATHER_STRATEGY_ENTRYPOINT.md](docs/WEATHER_STRATEGY_ENTRYPOINT.md)

这份入口文档记录当前 live 口径、N100 检查命令、关键代码路径、近期实盘事故结论和后续设计项。不要只凭本文件下方的历史摘要判断当前实盘状态。

## Weather Dashboard（本机看板，独立于 N100 生产）

本机的策略大盘 / 数据 DB / FastAPI / React 全在 `weather_dashboard/` + `frontend/strategy_dashboard/`。它是分析用的二级镜像，**不参与 N100 生产，不发单**。

设计与现状文档：

- **系统接口契约**（N100↔pm_agent 字段名/枚举/ID算法，改字段前必读）:
  [docs/WEATHER_SYSTEM_CONTRACT.md](docs/WEATHER_SYSTEM_CONTRACT.md)
- **核心量化系统架构设计**（血缘链 / 策略身份 / Run Registry / DB schema / API / 前端）:
  [docs/WEATHER_STRATEGY_QUANT_DESIGN.md](docs/WEATHER_STRATEGY_QUANT_DESIGN.md)
- **数据模型分层与缺口审计**（P0已完成，P1/P2待办）:
  [docs/WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md](docs/WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md)
- **早期实盘历史与回填治理**（本机/N100 live、重复下单、城市池错误、sizing 改动）:
  [docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md](docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md)
- **数据管道与统一 PnL 口径**（N100 raw → 镜像 → DB → API 全链路、所有脚本职责、已知缺口、运维 runbook、统一 PnL 口径标准）:
  [docs/WEATHER_DATA_PIPELINE.md](docs/WEATHER_DATA_PIPELINE.md)

> 任何关于"曲线/PnL 口径/为什么数据到不了今天/某脚本干嘛的"问题，先去 `WEATHER_DATA_PIPELINE.md` 找。
>
> 2026-06-07 CLOB fill 勘误：Polymarket public activity 不是逐 order 权威 fill 来源，只能作受 order cap 约束的 fallback。真实 fill 优先 `exchange_response.place.status=matched` 和 authenticated CLOB order/trade 数据。任何 live_real PnL/ROI/曲线前必须跑 `scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py`，`gate_pass=false` 时停止分析并先修数据链路。

任何 agent（Claude / Codex / MiniMax / 人）启动看板都用这一个脚本，不要手动跑多条命令:

```bash
# 一键启动（建库 + ingest CSV + 启动 API + 启动前端）
scripts/weather_dashboard/run_stack.sh

# 已经有 DB，只启服务
scripts/weather_dashboard/run_stack.sh --no-rebuild

# 只看状态（DB 里几个 run / 端口是否占用）
scripts/weather_dashboard/run_stack.sh --status

# 仅 API / 仅前端
scripts/weather_dashboard/run_stack.sh --api-only
scripts/weather_dashboard/run_stack.sh --fe-only
```

启动后入口:
- 前端: <http://localhost:5173/weather/runs>
- Live 监控: <http://localhost:5173/weather/live>
- API docs: <http://localhost:8000/docs>

日志:
```text
runtime/_dashboard_logs/api.log
runtime/_dashboard_logs/fe.log
runtime/_dashboard_logs/ingest_{snapshot,paper}.log
```

停止:
```bash
pkill -F runtime/_dashboard_logs/api.pid 2>/dev/null
pkill -F runtime/_dashboard_logs/fe.pid 2>/dev/null
```

数据源固定:
- snapshot replay CSV → `runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv`
- paper ledger CSV → `runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv`

这两个 CSV 来自 N100 `sync_weather_remote.sh` 拉取的镜像。如果看板数据陈旧，先同步 N100 再重跑 `run_stack.sh`。

## 桌面端 / Host-aware 命令执行约定

关键限制: Codex/Claude 桌面端可能在不同宿主环境里调用命令。先判断当前 shell/OS，再选择命令形态；不要因为文档里出现 WSL 路径就默认当前机器有 `wsl`。

当前这台机器是 Mac（`/Users/deepsleep/projects/pm_agents`）。在这台机器上：
- 所有本机构建、测试、同步、看板脚本都直接在 repo 根目录运行。
- 访问 N100 时直接用 macOS `ssh`，例如 `ssh jiarui@192.168.0.200 'hostname'`。
- 如果旧文档给出 `wsl -d Ubuntu-24.04 -- ...`，在本机应翻译成等价的直接命令。

Mac 本机例子:
```bash
cd /Users/deepsleep/projects/pm_agents
pytest
npm test
scripts/ops/sync_weather_remote.sh
scripts/weather_dashboard/run_stack.sh --status
```

历史 Windows/WSL 限制: 如果 Codex/Claude 桌面端在 Windows 环境里调用命令，即使代码目录来自 WSL，当前 shell 是 PowerShell/CMD 时也不是 WSL 里的 bash。

本项目在桌面端操作时，文件可以通过 Windows 侧 WSL 路径打开:
```text
\\wsl.localhost\Ubuntu-24.04\home\rui\projects\pm_agent
```

执行命令前先判断当前环境:
- 如果当前 shell 已经在 WSL/Linux 中，正常执行即可，例如 `pytest`、`npm test`。
- 如果当前 shell 是 Windows PowerShell/CMD，所有构建、测试、安装、脚本运行命令都应显式通过 WSL 执行:

```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && <command>"
```

Windows shell 下的例子:
```bash
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && pytest"
wsl -d Ubuntu-24.04 -- bash -lc "cd /home/rui/projects/pm_agent && npm test"
```

PowerShell 引号很容易把上一层查询拆坏，尤其是嵌套 `bash -lc`、`ssh '<command>'`、`python -c`、`awk`/`sed` 或包含 JSON/SQL 的命令时。遇到复杂查询时不要硬塞一长串混合引号；优先进入 WSL 后用 bash 原生命令执行，或把复杂逻辑写成临时脚本/项目脚本再调用。若必须从 PowerShell 发起，先用最小只读命令验证 quoting，再跑真实查询。

如果后续迁移到其他 WSL 发行版或用户名，按实际路径替换 `Ubuntu-24.04`、`rui` 和项目目录即可。若回到 Mac 侧，则以 `/Users/deepsleep/projects/pm_agents` 为准。

## Weather 数据分工与数据真相

> **重要**：N100 上同时跑**两个 repo**，不是只跑 weather-predict。完整四角色边界见 [docs/WEATHER_REPO_BOUNDARY.md](docs/WEATHER_REPO_BOUNDARY.md)。下面是简版口径。

四角色拓扑：

| 角色 | 路径 | 干什么 | 不干什么 |
|---|---|---|---|
| **N100 weather-predict** | `/home/jiarui/projects/weather-predict` | 信号原料 + 行情采集（systemd timer）：paper snapshot、orderbook snapshot、pm_history、GFS cache、observation cache | 实盘下单 |
| **N100 pm_agent** | `/home/jiarui/projects/pm_agent` | 实盘交易执行（foreground loop）：读信号 → 生成 plan → 下 CLOB 单 → 写 live JSONL + live_cycle 日志 | 行情采集、信号生成 |
| **本机 pm_agent** | `/Users/deepsleep/projects/pm_agents` | 分析、看板、回测、本机部署 staging | 任何生产数据采集 |
| **本机 weather-predict** | `/Users/deepsleep/projects/weather-predict` | weather-predict 代码改动的开发副本 | 数据真相 |

数据流：

```text
N100 weather-predict ─(snapshot/cache)─┐
                                       │ rsync (sync_weather_remote.sh)
                                       ▼
                            本机 runtime/weather_edge_v1/market_data/
N100 pm_agent ─(live signals/plans/orders/cycle)─┐
                                                 │ rsync (sync_weather_remote.sh)
                                                 ▼
                            本机 runtime/weather_edge_v1/remote_pm_agent/
                                                 │
                                                 │ ingest (weather_dashboard/legacy_migration/*)
                                                 ▼
                            本机 runtime/weather.db (fact_trades / fact_signal_candidates / ...)
```

### N100 远端：双 repo 生产机

远端机器: `jiarui@192.168.0.200`
- 信号原料 base: `/home/jiarui/projects/weather-predict`
- 实盘执行 base: `/home/jiarui/projects/pm_agent`

### N100 SSH 约定

当前这台 Mac 直接从本机发起 SSH:
```bash
ssh jiarui@192.168.0.200 '<command>'
ssh jiarui@192.168.0.200 'cd /home/jiarui/projects/weather-predict && <command>'
ssh jiarui@192.168.0.200 'cd /home/jiarui/projects/pm_agent && <command>'
```

只有在 Windows/PowerShell 宿主里，才从 WSL 侧发起:
```bash
wsl -d Ubuntu-24.04 -- ssh jiarui@192.168.0.200 '<command>'
```

WSL 内已配置 `~/.ssh/config`，`192.168.0.200` 使用:
```sshconfig
Host 192.168.0.200
  HostName 192.168.0.200
  User jiarui
  IdentityFile ~/.ssh/id_ed25519_weather_deploy
  IdentitiesOnly yes
```

因此在 WSL 内也可以直接运行:
```bash
ssh jiarui@192.168.0.200 'cd /home/jiarui/projects/weather-predict && <command>'
```

N100 是生产数据真相，**两个 repo 各管一块**：

**weather-predict**（systemd timer 驱动）负责行情/天气原料：
- 实时半小时 Polymarket snapshot
- paper order ledger
- 每日 settlement / `pm_history` / GFS cache refresh
- T2 盘口先行采集
- 天气观测数据补全的生产运行

**pm_agent**（foreground loop 驱动，CWD=`/home/jiarui/projects/pm_agent`）负责实盘交易：
- `scripts/ops/weather_live_cycle_loop.sh`（多策略实例并行）
- 读 signals → 生成 plans → 调 py_clob_client 下单 → 写 live JSONL + live_cycle 日志
- 暂停开关 / Telegram 控制 / live doctor

生产数据目录（weather-predict 侧）:
- `/home/jiarui/projects/weather-predict/output/paper_snapshots/`
- `/home/jiarui/projects/weather-predict/output/paper_trades/`
- `/home/jiarui/projects/weather-predict/output/research/`
- `/home/jiarui/projects/weather-predict/cache/pm_history/`
- `/home/jiarui/projects/weather-predict/cache/wu_obs/`
- `/home/jiarui/projects/weather-predict/cache/iem_v2_*.csv`

生产数据目录（pm_agent 侧 — 实盘 lineage）:
- `/home/jiarui/projects/pm_agent/runtime/weather_edge_v1/signals/`
- `/home/jiarui/projects/pm_agent/runtime/weather_edge_v1/plans/`
- `/home/jiarui/projects/pm_agent/runtime/weather_edge_v1/live/` (CLOB 下单 JSONL)
- `/home/jiarui/projects/pm_agent/runtime/weather_edge_v1/live_cycle/` (cycle 日志)

健康检查入口:
```bash
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && scripts/ops/doctor_restart.sh'
```

规则: 判断生产是否断流时，先看 N100 doctor/check；不要用本机镜像的新旧直接判断生产状态。

### 本机 pm_agent：分析、看板、策略开发机

本机只拉 N100 数据镜像，用于分析、回测、报表、前端看板和策略开发。本机不作为生产采集来源，也不直接影响远端 paper order，除非明确执行部署。

本机镜像有**两个根**，分别对应 N100 两个 repo:
```text
/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/    ← N100 weather-predict 镜像
/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/remote_pm_agent/ ← N100 pm_agent 镜像
```

镜像目录映射 — weather-predict 侧:
- N100 `output/paper_snapshots/` → 本机 `runtime/weather_edge_v1/market_data/paper_snapshots/`
- N100 `output/paper_trades/` → 本机 `runtime/weather_edge_v1/market_data/paper_trades/`
- N100 `output/research/` → 本机 `runtime/weather_edge_v1/market_data/research/`
- N100 `cache/pm_history/` → 本机 `runtime/weather_edge_v1/market_data/cache/pm_history/`
- N100 `cache/wu_obs/` → 本机 `runtime/weather_edge_v1/market_data/cache/wu_obs/`
- N100 `cache/iem_v2_*.csv` → 本机 `runtime/weather_edge_v1/market_data/cache/iem/`

镜像目录映射 — N100 pm_agent 侧（实盘 lineage，分析必读）:
- N100 `runtime/weather_edge_v1/signals/`    → 本机 `runtime/weather_edge_v1/remote_pm_agent/signals/`
- N100 `runtime/weather_edge_v1/plans/`      → 本机 `runtime/weather_edge_v1/remote_pm_agent/plans/`
- N100 `runtime/weather_edge_v1/live/`       → 本机 `runtime/weather_edge_v1/remote_pm_agent/live/`（CLOB 实盘订单 JSONL）
- N100 `runtime/weather_edge_v1/live_cycle/` → 本机 `runtime/weather_edge_v1/remote_pm_agent/live_cycle/`
- N100 `runtime/weather_edge_v1/paper/`      → 本机 `runtime/weather_edge_v1/remote_pm_agent/paper/`

本机 `runtime/weather.db` 由 `weather_dashboard/legacy_migration/*` 从上面两个镜像 ingest 生成；它**不是** N100 任何 DB 的拷贝，是本机独立重建的分析 DB。本机 `runtime/weather_edge_v1/live/` 是早期本机自跑 live loop 留下的历史目录，**当前已无新数据，可忽略**。

同步入口:
```bash
cd /Users/deepsleep/projects/pm_agents
scripts/ops/sync_weather_remote.sh
```

同步 dry-run:
```bash
cd /Users/deepsleep/projects/pm_agents
scripts/ops/sync_weather_remote.sh --dry-run
```

分析前口径:
- 先确认是否需要最新数据；需要时先运行 `scripts/ops/sync_weather_remote.sh`。
- 本机分析只读 `runtime/weather_edge_v1/market_data/` 镜像。
- 如果镜像落后，先同步；不要直接判断生产断了。

Paper ledger 口径:
- N100 半小时 snapshot 覆盖 T1 + T2 全部 configured cities。
- `output/paper_trades/paper_orders.jsonl` 是全池 paper ledger，T1/T2 都可以入池。
- 每条 ledger row 用 `city_pool` 区分 `t1_trading` / `t2_research`。
- `eligible_for_paper_order=false` 表示该城市不是生产交易池，不表示不能进入 paper ledger。
- 绩效分析优先看 `output/research/t24_paper_ledger_summary.json` 里的 `overall` 和 `by_pool`；需要组合策略时再按 `by_city` / `by_side` / `by_model` / `by_pool_side` / `by_pool_model` 切片。
- 每日 `daily_pipeline.py` 的 settlement / `pm_history` 覆盖 `FULL_CITY_CONFIGS`，因此 T2 ledger row 后续也应可结算。

### 本机 weather-predict：开发副本

`/Users/deepsleep/projects/weather-predict` 是当前 Mac 侧约定的开发副本路径（如果存在），用来改代码、测试脚本，不作为数据真相，也不靠它判断生产是否断了。历史 WSL 路径是 `/home/rui/projects/weather-predict`。

生产脚本改动流程:
1. 本机 `weather-predict` 改代码
2. 本机 `py_compile` / test
3. `rsync` 到 N100
4. N100 跑 `scripts/ops/doctor_restart.sh`
5. N100 手动 smoke 一次

天气数据补全策略:
- 补全逻辑可以在本机开发副本中实现和验证。
- 生产补全最终应部署到 N100 执行，结果落到 N100 生产 cache。
- pm_agent 只通过同步后的镜像读取补全结果。

T2 天气数据补全入口:
```bash
# N100 生产运行，默认补 RESEARCH_T2_CITIES
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && python3 scripts/ops/fill_t2_weather_cache.py'

# 本机同步后查看补全结果
cd /Users/deepsleep/projects/pm_agents
scripts/ops/sync_weather_remote.sh
cat runtime/weather_edge_v1/market_data/research/t2_weather_cache_fill_summary.json
```

补全产物口径:
- N100 `cache/iem_v2_<ICAO>_<start>_<end>.csv` → 本机 `runtime/weather_edge_v1/market_data/cache/iem/`
- N100 `cache/wu_obs/wu_obs_<ICAO>.csv` → 本机 `runtime/weather_edge_v1/market_data/cache/wu_obs/`
- N100 `output/research/t2_weather_cache_fill_summary.json` → 本机 `runtime/weather_edge_v1/market_data/research/`
- HongKong 当前使用 `VHHH`，不是无 IEM 覆盖的 `VHKO`。

### 备份策略

N100 备份入口:
```bash
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && scripts/ops/backup_data.sh'
```

备份输出:
```text
/home/jiarui/weather-predict-backups/weather-predict-data-YYYYMMDDTHHMMSSZ.tar.zst
/home/jiarui/weather-predict-backups/weather-predict-data-YYYYMMDDTHHMMSSZ.tar.zst.sha256
```

备份范围:
- `output/`
- `cache/pm_history/`
- `cache/wu_obs/`
- `cache/iem_v2_*.csv`

最低要求:
- N100 本地保留最近 14 天 tar 包。
- `pm_agent/runtime/weather_edge_v1/market_data/` 是第二份镜像。
- 每天同步一次，重启或故障后手动同步一次。
- 大文件原始 cache 不进 git，只走 `rsync` / `tar`。

## Weather 策略盈利查询方法

### 方法1: 用现成脚本（推荐）

远端运行:
```bash
cd ~/projects/weather-predict
python3 scripts/analysis/settle_t24_paper.py --source ledger
```

输出 `output/research/t24_paper_ledger_summary.json`，包含按日期/城市/模型/SIDE的 PnL 汇总。

关键字段说明:
- `by_date["2026-05-09"]` 即当天结算结果
- `overall.pnl_usd`: 总盈利
- `overall.roi`: 投资回报率
- `overall.win_rate`: 胜率
- `unsettled`: 未结算的交易（因 missing_bracket 或 missing_event）

### 方法2: 用 snapshot replay

远端运行:
```bash
cd ~/projects/weather-predict
python3 scripts/analysis/settle_t24_paper.py --source snapshots
```

输出 `output/research/t24_paper_snapshot_replay_summary.json`。

### 结算数据（pm_history）

每城市每日一个文件: `cache/pm_history/{City}_{date}.json`

读取方式（Python）:
```python
import json
d = json.load(open(f"cache/pm_history/Tokyo_2026-05-09.json"))
winners = [b["label"] for b in d["brackets"] if b.get("final_price") == 1.0]
# winners = ["23"]  表示当天东京最高温23°C
```

## 已知的盈利模式

- **BUY_NO 远强于 BUY_YES**: win_rate ~76% vs ~12%
- ** Warsaw 表现最好**: ROI +52.9%（5/8-5/10累计）
- **ECMwf 模型优于 GFS**: ROI +12% vs +1.4%（同周期累计）
- **LA 数据经常 missing_bracket**，结算失败率高

---

## Weather 策略分析强制规约

**任何 weather 策略分析请求必须先 invoke 对应 skill，不准跳过：**

| 分析类型 | 触发词 | Skill |
|---|---|---|
| 补全/重建底表 + 同步数据 | 补全底表、重建底表、刷新底表、同步数据、sync N100、数据陈旧/落后、重建 weather.db、重新结算、rebuild、resync | `weather-fact-rebuild` |
| 历史绩效 / A/B 对比 | 绩效、PnL、ROI、win rate、胜率、切片、对比、A/B、回测结果、策略表现 | `weather-strategy-performance` |
| 单日血缘 / 逐笔复盘 | 单日、血缘、逐笔、当日复盘、为什么下了这单、信号到结算 | `weather-strategy-lineage` |
| 持仓敞口 / 未平仓 | 持仓、敞口、未结算、未平仓、风险、当前仓位、open position | `weather-strategy-exposure` |
| 账户余额 / CLOB 对账 | 余额、钱包、账户、USDC、cash、cashflow、资金少了、买入成本、submitted notional、actual fill cost、CLOB 对账、fill_id 对不上 | `weather-live-account-reconcile` |
| 策略/参数部署到 N100 | 部署策略、上线策略、新 policy、切换策略、修改参数部署、上 V2/V3、启动新分支、城市池、加城市、移除城市、T1/T2、city_pools、paper_policy、N100 代码改动 | `weather-strategy-deploy` |

> 数据可能陈旧时（分析「最新/今天/最近几天」战绩），先走 `weather-fact-rebuild` 同步+重建，再 invoke 上面的分析 skill。

**禁止**：在不 invoke skill 的情况下直接写一次性 pandas 脚本做策略分析。  
**禁止**：手搓单跑 `build_weather_*.py` 或跳过 `sync_weather_remote.sh` 直接重建底表（破坏链条顺序，见 `weather-fact-rebuild`）。  
**禁止**：任何改变 N100 生产行为的代码/配置变更（city_pools、paper_policy、execution_policy、live_cycle 等）通过 `scp`/`rsync` 直接推送，必须走 `weather-strategy-deploy` skill 的 git-first 流程。  
**口径唯一来源**：[docs/WEATHER_ANALYSIS_CONTRACT.md](docs/WEATHER_ANALYSIS_CONTRACT.md)

### Live 账户余额 / CLOB 对账强制口径

用户问“余额少了 / 最近几天账户亏了 / 钱包对不上 / CLOB fill 对不上”时，必须走 `weather-live-account-reconcile`，使用：

```bash
python3 scripts/analysis/account_reconcile/weather_live_account_reconcile.py --start YYYY-MM-DD --end YYYY-MM-DD --date-field fill_date_bj --group-by instance,selected_date
```

硬规定：
- **禁止**用 `fact_trades.order_date_bj` 解释钱包现金流或余额变化；它可能受回填/重建链路污染，只能作为策略下单归属诊断字段。
- `target_date` 是天气合约目标日，不是现金流日期；`fill_date_bj` 才是已成交买入真正花钱日期。
- `submitted_notional_usd` / `posted_notional_usd` / `actual_fill_cost_usd` / `open_cost_usd` / `realized_pnl_usd` 必须分开报；`cost_usd` 或 open cost 不是“亏损”。
- 已结算 PnL 只看 `settlement_status='settled'` 的 `pnl_usd_at_fill`；未结算只能报 MTM，并必须附 `val_snapshot_ts_utc`，估值旧就明确说旧。
- 如果 raw live order 文件比 DB 新，必须用脚本里的 Raw Live Order Files 段补充 submitted/posted notional，并说明 DB 滞后。
- 最终结论前必须报告 fill_id reconciliation：`db_live_real_distinct_fills`、`raw_clob_distinct_fills`、`db_not_in_raw`、`raw_not_in_db`。
- 最终结论前必须跑 `python3 scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py`；`gate_pass=false` 时禁止发布 live_real PnL/ROI/曲线。

### 分析前数据源自检（强制 5 行 SQL）

任何 weather 分析、模型评估、报告前，先跑这 5 个查询确认数据真在「权威源」上、缺口在哪。完整说明见 [docs/WEATHER_DATA_CANONICAL_SOURCES.md](docs/WEATHER_DATA_CANONICAL_SOURCES.md)。

```sql
SELECT MAX(fact_built_at_utc) FROM fact_trades;                       -- 数据新鲜度
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;   -- live_real / paper / snapshot_replay 分布
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status;  -- settled/unsettled
SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates;  -- 机会粒度覆盖
SELECT o.status, COUNT(*) orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill
  FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status;
```

**禁止**：读 `runtime/_legacy/*.db`（已退役）、读 `runtime/weather_v2.db` / `runtime/weather_edge_v1/weather.db`（已搬到 `_legacy/`）、绕过 `fact_trades` 自算 fill PnL、绕过 `fact_signal_candidates` 自算成交质量/漏单/滑点。  
**当前已知口径/缺口**（不要踩坑）：2026-06-07 修正后 live fill recovery 的硬标准是 `weather_clob_fill_coverage_gate.py gate_pass=true`，且 `missing_order_rows=0`、`over_order_keys=0`、DB/cache/fact 成本差为 0；`live_real` 行数会随新增真实成交变化，不要写死。之前大量 `missing_bracket` 是 settlement near-binary bug，`pm_history` raw `0.9995/0.0005` 必须归一化为 `1/0`，旧报告需重算。`gfs_365d_*` cache 文件名误标，实际 ~735 天。`decision_window_missing` ~44%，反事实结论只覆盖另一半机会。

---

## 文档索引

> 完整文档目录、状态分级和历史快照索引统一维护在
> [docs/WEATHER_DOCS_INDEX.md](docs/WEATHER_DOCS_INDEX.md)。每次新增、删除、
> 归档或改变 weather 文档职责时，只维护这份总索引；本节只保留最高频入口。

### 最高频权威入口

| 文档 | 用途 |
|---|---|
| [WEATHER_DOCS_INDEX.md](docs/WEATHER_DOCS_INDEX.md) | weather 文档总索引：状态分级、当前入口、历史快照、维护规则 |
| [WEATHER_STRATEGY_ENTRYPOINT.md](docs/WEATHER_STRATEGY_ENTRYPOINT.md) | 实盘接手入口：当前 live 实例、N100 检查、近期关键决策 |
| [WEATHER_CITY_POOL_DECISIONS.md](docs/WEATHER_CITY_POOL_DECISIONS.md) | 城市池决策日志：当前 T1/T2、升降级依据、回滚条件 |
| [WEATHER_ANALYSIS_CONTRACT.md](docs/WEATHER_ANALYSIS_CONTRACT.md) | 分析口径唯一来源：PnL、切片、账户现金流、数据自检 |
| [WEATHER_DATA_CANONICAL_SOURCES.md](docs/WEATHER_DATA_CANONICAL_SOURCES.md) | 数据源真相：source/mirror/derived/legacy 和已知缺口 |
| [WEATHER_REPO_BOUNDARY.md](docs/WEATHER_REPO_BOUNDARY.md) | weather-predict 与 pm_agent 的生产/本机职责边界 |
| [WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md](docs/WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md) | edge engine 当前接手入口：blender shadow/paper 与 city-day basket 下一步 |
