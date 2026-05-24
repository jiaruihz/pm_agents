# PM Agent — Project Context

> Claude Code 相关约定见 `CLAUDE.md`；两份文件应保持核心项目规范一致。

## 当前主线：天气温度策略（必读，不要被 README 误导）

**README 描述的是旧 PMM/ARB 框架，当前活跃主线是天气策略。**

关键目录：

```text
weather_dashboard/          ← Python 后端：FastAPI + SQLite DB + ingest 管道
frontend/strategy_dashboard/← React 前端（src/pages/weather/ 是天气策略页面）
scripts/weather_dashboard/  ← run_stack.sh 一键启动脚本
runtime/weather_edge_v1/    ← 数据目录（N100 镜像 + DB，不进 git）
docs/WEATHER_SYSTEM_CONTRACT.md      ← 字段名/枚举契约（改字段必读）
docs/WEATHER_STRATEGY_QUANT_DESIGN.md← 架构设计
docs/WEATHER_STRATEGY_ENTRYPOINT.md  ← 实盘入口
```

生产端（N100）：`jiarui@192.168.0.200:/home/jiarui/projects/weather-predict`  
分析端（本机）：`/home/rui/projects/pm_agent`

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

## Weather Dashboard 一键启动

本机看板（独立于 N100 生产，不发单）。任何 agent 启动看板都用同一个脚本:

```bash
scripts/weather_dashboard/run_stack.sh             # 全量：建库+ingest+API+FE
scripts/weather_dashboard/run_stack.sh --no-rebuild
scripts/weather_dashboard/run_stack.sh --status
```

设计文档:
- **系统接口契约**（字段名/枚举/ID算法，改字段前必读）: [docs/WEATHER_SYSTEM_CONTRACT.md](docs/WEATHER_SYSTEM_CONTRACT.md)
- 核心量化架构设计 spec: [docs/WEATHER_STRATEGY_QUANT_DESIGN.md](docs/WEATHER_STRATEGY_QUANT_DESIGN.md)
- 数据模型缺口审计（P0已完成，P1待办）: [docs/WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md](docs/WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md)
- 早期实盘历史与回填治理: [docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md](docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md)
- 数据管道与统一 PnL 口径（数据流全链路 / 脚本职责 / 已知缺口 / 运维 runbook）: [docs/WEATHER_DATA_PIPELINE.md](docs/WEATHER_DATA_PIPELINE.md)

启动后:
- 看板 <http://localhost:5173/weather/runs>
- Live  <http://localhost:5173/weather/live>
- API   <http://localhost:8000/docs>

## 桌面端 / WSL 命令执行约定

关键限制: Codex 桌面端当前可能在 Windows 环境里调用命令。即使代码目录来自 WSL，如果当前 shell 是 PowerShell/CMD，也不是 WSL 里的 bash。

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

如果后续迁移到其他 WSL 发行版或用户名，按实际路径替换 `Ubuntu-24.04`、`rui` 和项目目录即可。

## Weather 数据分工与数据真相

### N100 远端：生产采集机

远端机器: `jiarui@192.168.0.200`
远端 base: `/home/jiarui/projects/weather-predict`

### N100 SSH 约定

Codex 桌面端里不要从 Windows 侧直接调用 `ssh`；当前 Windows `ssh` 可能被沙箱包装脚本拦截。访问 N100 时从 WSL 侧发起:
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

N100 是生产数据真相，负责:
- systemd timers
- 实时半小时 Polymarket snapshot
- paper order ledger
- 每日 settlement / `pm_history` / GFS cache refresh
- T2 盘口先行采集
- 天气观测数据补全的生产运行

生产数据目录:
- `/home/jiarui/projects/weather-predict/output/paper_snapshots/`
- `/home/jiarui/projects/weather-predict/output/paper_trades/`
- `/home/jiarui/projects/weather-predict/output/research/`
- `/home/jiarui/projects/weather-predict/cache/pm_history/`
- `/home/jiarui/projects/weather-predict/cache/wu_obs/`
- `/home/jiarui/projects/weather-predict/cache/iem_v2_*.csv`

健康检查入口:
```bash
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && scripts/ops/doctor_restart.sh'
```

规则: 判断生产是否断流时，先看 N100 doctor/check；不要用本机镜像的新旧直接判断生产状态。

### 本机 pm_agent：分析、看板、策略开发机

本机只拉 N100 数据镜像，用于分析、回测、报表、前端看板和策略开发。本机不作为生产采集来源，也不直接影响远端 paper order，除非明确执行部署。

本机镜像根目录:
```text
/home/rui/projects/pm_agent/runtime/weather_edge_v1/market_data/
```

镜像目录映射:
- N100 `output/paper_snapshots/` → 本机 `runtime/weather_edge_v1/market_data/paper_snapshots/`
- N100 `output/paper_trades/` → 本机 `runtime/weather_edge_v1/market_data/paper_trades/`
- N100 `output/research/` → 本机 `runtime/weather_edge_v1/market_data/research/`
- N100 `cache/pm_history/` → 本机 `runtime/weather_edge_v1/market_data/cache/pm_history/`
- N100 `cache/wu_obs/` → 本机 `runtime/weather_edge_v1/market_data/cache/wu_obs/`
- N100 `cache/iem_v2_*.csv` → 本机 `runtime/weather_edge_v1/market_data/cache/iem/`

同步入口:
```bash
cd /home/rui/projects/pm_agent
scripts/ops/sync_weather_remote.sh
```

同步 dry-run:
```bash
cd /home/rui/projects/pm_agent
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

`/home/rui/projects/weather-predict` 只是开发副本，用来改代码、测试脚本，不作为数据真相，也不靠它判断生产是否断了。

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
cd /home/rui/projects/pm_agent
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
