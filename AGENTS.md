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

## Weather 策略分析强制规约

**任何 weather 策略分析请求必须先 invoke 对应 skill，不准跳过：**

| 分析类型 | 触发词 | Skill |
|---|---|---|
| 历史绩效 / A/B 对比 | 绩效、PnL、ROI、win rate、胜率、切片、对比、A/B、回测结果、策略表现 | `weather-strategy-performance` |
| 单日血缘 / 逐笔复盘 | 单日、血缘、逐笔、当日复盘、为什么下了这单、信号到结算 | `weather-strategy-lineage` |
| 持仓敞口 / 未平仓 | 持仓、敞口、未结算、未平仓、风险、当前仓位、open position | `weather-strategy-exposure` |
| 策略/参数部署到 N100 | 部署策略、上线策略、新 policy、切换策略、修改参数部署、上 V2/V3、启动新分支、城市池、加城市、移除城市、T1/T2、city_pools、paper_policy、N100 代码改动 | `weather-strategy-deploy` |

**禁止**：在不 invoke skill 的情况下直接写一次性 pandas 脚本做策略分析。  
**禁止**：任何改变 N100 生产行为的代码/配置变更（city_pools、paper_policy、execution_policy 等）通过 `scp`/`rsync` 直接推送，必须走 `weather-strategy-deploy` skill 的 git-first 流程。  
**口径唯一来源**：`docs/WEATHER_ANALYSIS_CONTRACT.md`（§2 PnL 公式、§5 切片维度白名单、§6 默认城市池）

---

## 全局工程姿态：个人项目，默认直接推进

这是个人研究/交易项目，不是承载外部线上流量的多租户生产系统。默认实现时不要为了“看起来稳妥”层层加保守兜底、静默 fallback、双路径兼容或过度抽象。

默认偏好:
- **直接实现主路径**：优先把当前要验证的策略、看板或分析链路跑通，少写与当前目标无关的防御性分支。
- **显式失败优于静默兜底**：数据缺失、字段不一致、盘口不可用时，优先报错/告警并暴露原因；不要偷偷换旧字段、旧数据、默认值继续跑，除非文档已约定这是兼容层。
- **兼容逻辑要有退出条件**：如果必须兼容历史字段或旧文件，在代码/文档里标明原因和删除时机，不要无限期保留。
- **研究和本机工具可以激进**：回测、对比脚本、dashboard、本机分析默认选择可观测、可调参、可快速迭代的实现，而不是最保守的企业级兜底。
- **真实下单仍保留硬边界**：涉及 N100 live、私钥、余额、真实 CLOB 下单、删除数据、远端部署时，保留显式确认、暂停开关、notional 上限和可追溯日志；不要把“少兜底”理解成绕过资金安全或不可逆操作。

## 策略研究防跑偏约定（重要）

天气策略研究中，用户提出的往往是一个很具体的失败模式或交易形态。Agent 必须先把问题收敛成一句明确的 **target metric / target slice**，再跑数据、写脚本或写结论。

默认流程:
- **先复述目标指标**：例如 `bought_no_hit_and_net_loss` = “pure NO city-day 中买了多个 NO，最终温度命中其中一个被买 NO bracket，导致该腿亏满且整组 city-day 净亏”。
- **先锁分母**：明确是在看事前持仓形态（如 `pure_no_basket`）还是事后结算形态（如 `no_wins_only`），不要把两者混用。
- **所有表格必须回答目标指标**：如果表格只是在说明背景（如 pure NO 总体 PnL），必须标注为背景，不能拿它替代主结论。
- **不要擅自扩大问题**：用户问“某个坏场景怎么优化”，不要扩展成“整个分支是否赚钱/是否该砍”；除非明确说明这是额外 sanity check。
- **结论先给交易动作**：先回答应该保留、过滤、降 size、shadow 还是不改 live，再给证据和细分数据。
- **发现口径漂移要立刻纠正**：如果分析过程中发现 target metric、样本分母或字段含义不一致，先暂停修正口径，不继续堆更多结果。

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

PowerShell 引号很容易把上一层查询拆坏，尤其是嵌套 `bash -lc`、`ssh '<command>'`、`python -c`、`awk`/`sed` 或包含 JSON/SQL 的命令时。遇到复杂查询时不要硬塞一长串混合引号；优先进入 WSL 后用 bash 原生命令执行，或把复杂逻辑写成临时脚本/项目脚本再调用。若必须从 PowerShell 发起，先用最小只读命令验证 quoting，再跑真实查询。

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

---

## 文档索引

> **维护规则：每次在 `docs/` 下新增或删除文档，必须同步更新本节。**  
> 归档文档移到 `docs/archive/`，从本节删除。

### 核心参考文档（稳定，高频查阅）

| 文档 | 用途 |
|---|---|
| [WEATHER_STRATEGY_ENTRYPOINT.md](docs/WEATHER_STRATEGY_ENTRYPOINT.md) | 实盘排查入口：N100 检查命令、live 口径、近期事故结论 |
| [WEATHER_REPO_BOUNDARY.md](docs/WEATHER_REPO_BOUNDARY.md) | weather-predict 与 pm_agent 的生产/本机职责边界、数据同步边界、重复文件注意事项 |
| [WEATHER_CITY_POOL_DECISIONS.md](docs/WEATHER_CITY_POOL_DECISIONS.md) | 城市池决策日志：T1/T2 当前口径、升降级依据、N100 部署记录 |
| [WEATHER_SYSTEM_CONTRACT.md](docs/WEATHER_SYSTEM_CONTRACT.md) | N100↔pm_agent 字段名/枚举/ID算法契约，**改字段前必读** |
| [WEATHER_DATA_PIPELINE.md](docs/WEATHER_DATA_PIPELINE.md) | N100→镜像→DB→API 全链路、所有脚本职责、PnL口径、运维 runbook |
| [WEATHER_STRATEGY_QUANT_DESIGN.md](docs/WEATHER_STRATEGY_QUANT_DESIGN.md) | 核心架构设计（血缘链/策略身份/Run Registry/DB schema/API/前端） |
| [WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md](docs/WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md) | 数据模型分层与缺口审计（P0已完成，P1/P2待办） |
| [WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md](docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md) | 早期实盘历史与回填治理（重复下单/城市池错误/sizing改动） |
| [WEATHER_DASHBOARD_TROUBLESHOOTING.md](docs/WEATHER_DASHBOARD_TROUBLESHOOTING.md) | Dashboard 故障排查：portproxy/CORS/env/null crash 根因与修复 |
| [OPS_RUNBOOK.md](docs/OPS_RUNBOOK.md) | 通用运维手册：常驻进程、日志路径、启停命令 |

### 设计文档（功能待实施或部分实施）

| 文档 | 用途 |
|---|---|
| [WEATHER_EXECUTION_ARCHITECTURE.md](docs/WEATHER_EXECUTION_ARCHITECTURE.md) | paper→live 执行边界设计（资金安全/暂停开关/notional 上限） |
| [WEATHER_SHADOW_PORTFOLIO_TRACKING.md](docs/WEATHER_SHADOW_PORTFOLIO_TRACKING.md) | 影子组合追踪架构（live策略的虚拟持仓快照设计） |
| [WEATHER_CLOB_ORDERBOOK_CAPTURE.md](docs/WEATHER_CLOB_ORDERBOOK_CAPTURE.md) | CLOB 快照捕获设计（供回测用更准确的 ask/bid 入场价） |
| [WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md](docs/WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md) | 城市/日组合优化器设计（多城市仓位分配） |
| [WEATHER_LEDGER_POSITION_ANALYSIS.md](docs/WEATHER_LEDGER_POSITION_ANALYSIS.md) | 持仓分析设计（Dashboard 持仓拆解页面） |
| [WEATHER_MID_PRICE_CORE_V2_DESIGN.md](docs/WEATHER_MID_PRICE_CORE_V2_DESIGN.md) | mid_price_core_v2 执行策略设计（低价正 alpha 漏单拆单、maker_queue 删除方案） |
| [WEATHER_ENTRY_BAND_AND_SIZING_DESIGN.md](docs/WEATHER_ENTRY_BAND_AND_SIZING_DESIGN.md) | 入场区间×仓位 sizing 设计（side×价位桶档位+edge缩放+硬上限,替代等额$5/统一0.25-0.75带） |

### 研究与分析报告（时间点快照，不更新）

| 文档 | 用途 |
|---|---|
| [WEATHER_LOW_PRICE_LOTTERY_RESEARCH_2026-05-19.md](docs/WEATHER_LOW_PRICE_LOTTERY_RESEARCH_2026-05-19.md) | 低价YES彩票仓研究（5c-20c YES 持有到结算回报分析） |
| [WEATHER_LIVE_STRATEGY_ANALYSIS_2026-05-23.md](docs/WEATHER_LIVE_STRATEGY_ANALYSIS_2026-05-23.md) | 2026-05-23 实盘血缘分析（t1_trading mid_price_core_v1 策略） |
| [2026-05-27-performance-live-full-research.md](docs/analysis/2026-05/2026-05-27-performance-live-full-research.md) | 2026-05-27 全量 live 实盘绩效归因（城市/方向/paper 对比/edge 赔率诊断） |
| [2026-05-28-performance-makerqueue-v3-city-pool.md](docs/analysis/2026-05/2026-05-28-performance-makerqueue-v3-city-pool.md) | 2026-05-28 maker_queue 与 v3 城市池当日未结算亏损拆解 |
| [2026-05-29-entry-timing-edge.md](docs/analysis/2026-05/2026-05-29-entry-timing-edge.md) | 2026-05-29 入场 timing / edge 诊断 |
| [2026-05-29-performance-candidates-vs-fills-link.md](docs/analysis/2026-05/2026-05-29-performance-candidates-vs-fills-link.md) | 2026-05-29 fact_signal_candidates × fact_trades 双底表首次串联（全机会 alpha vs 成交样本/漏单/滑点） |
| [2026-05-29-performance-city-pool-side-strategy.md](docs/analysis/2026-05/2026-05-29-performance-city-pool-side-strategy.md) | 2026-05-29 城市池选择策略复盘（合并城市 alpha+成交质量）：city×side 侧别白名单、fill级稳健性、三层一致性、Paris应降级 |
| [2026-05-29-strategy-entry-band-and-execution-quality.md](docs/analysis/2026-05/2026-05-29-strategy-entry-band-and-execution-quality.md) | 2026-05-29 入场价 25-75 区间调参 + maker_queue vs mid_price 成交质量/paper 对比 + 策略建议 |
| [2026-05-30-performance-entry-band-research.md](docs/analysis/2026-05/2026-05-30-performance-entry-band-research.md) | 2026-05-30 入场价带（0.25-0.75）调参研究：side×价位桶 EV、候选反事实、live/paper 对照 |
| [2026-05-30-performance-sizing-and-band-distribution.md](docs/analysis/2026-05/2026-05-30-performance-sizing-and-band-distribution.md) | 2026-05-30 仓位 sizing×入场区间收益分布研究（反过拟合、bootstrap、paper→live 样本外验证） |
| [COPY_TRADE_WALLET_RESEARCH_EXECUTION_PLAN.md](docs/COPY_TRADE_WALLET_RESEARCH_EXECUTION_PLAN.md) | Copy Trade 钱包研究执行计划 |
