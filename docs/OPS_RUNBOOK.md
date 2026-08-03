# 统一运维手册（第一版）

这份文档只解决当前最实际的问题：

- 现在有哪些常驻进程
- 哪些进程在机器重启后需要手动拉起
- 现在机器上哪些服务正在跑
- 出问题时先看哪里

目标不是做完整运维体系，而是先让当前仓库可维护。

## 1. 统一查看入口

### Mac JRS 常驻进程

Mac 上所有 weather/tmax/range 常驻进程统一由
`scripts/ops/weather_jrs_tmux_env.sh` 管理，唯一 socket 是
`weather-data-feed-jrs`。各业务 `start_*.sh` 是 controller 的底层合同，不是人工入口，
不再支持默认 tmux、独立 socket、screen、nohup 或 start-mode fallback；启动前的
JRS write probe 由 helper 在 tmux server 内执行。helper 固定使用 tmux `-N`
attach-only：server 不存在时 fail closed；只有 controller `recover-jrs-context`
可以创建或重建 permission host，persistent session mutation 也必须带 controller authority。

该 tmux server 的权限宿主固定为已在 macOS「完全磁盘访问权限」中授权的
`/opt/homebrew/Cellar/tmux/3.6b/bin/tmux`，helper 同时固定其 SHA-256。
禁止让 Homebrew symlink 静默切换生产 binary。升级 tmux 时必须先把新 binary
加入完全磁盘访问权限，再更新 helper 的 path/hash、运行入口契约测试，并在维护
窗口重建 canonical server；任一步未完成都继续使用旧的已授权 binary。

底层只读诊断先 source helper，再调用
`weather_jrs_tmux weather-data-feed-jrs list-sessions`；日常盘点仍优先使用下面的
controller health/plan，禁止直接执行 raw tmux 命令。

JRS/TCC、canonical tmux crash、历史入口与验收记录统一维护在
[WEATHER_JRS_RUNTIME_INCIDENTS.md](WEATHER_JRS_RUNTIME_INCIDENTS.md)。

当前生产 desired state 在 `src/strategies/runtime/production.yaml` 的
`managed_runtimes`。它与研究/历史 `instances.yaml` 分开：只有
`managed_runtimes` 表示“现在应该持续运行”。统一控制入口：

```bash
# 人类可读的全链路健康检查；critical 时退出码为 2
.venv/bin/python scripts/ops/weather_production_ctl.py health

# 机器可读输出
.venv/bin/python scripts/ops/weather_production_ctl.py health --json

# 只显示 desired vs observed 和建议动作，不改生产
.venv/bin/python scripts/ops/weather_production_ctl.py plan

# 仅补启 production.yaml 中缺失且有恢复合同的实例；不停止任何额外进程
.venv/bin/python scripts/ops/weather_production_ctl.py reconcile --apply \
  --reason "named incident recovery"

# 如果恢复集合包含 live，必须再显式确认
.venv/bin/python scripts/ops/weather_production_ctl.py reconcile --apply \
  --confirm-live --reason "named live incident recovery"

# 单实例重启也必须走 controller；先不带 --apply 查看目标合同
.venv/bin/python scripts/ops/weather_production_ctl.py restart \
  --instance INSTANCE --json

# safe 非 live 实例使用 exact-session stop + registered start；live 缺显式合同时阻断
.venv/bin/python scripts/ops/weather_production_ctl.py restart \
  --instance INSTANCE --apply --reason "named runtime restart"
```

`health` 同时检查 canonical DB/进程 manifest、全部 required tmux sessions、
关键 runtime artifact freshness、checkout、live flags、`live_enabled`、上游依赖，
以及 observation/forecast/orderbook/snapshot/source-model 的 data-feed 语义健康。
城市 same-day weather state、少量 fresh forecast curve / orderbook target 缺口单列为
warning；forecast fallback、stale/invalid forecast capture、核心 cache/parity 或整层
orderbook 缺失为 critical。当前已关闭的旧 `fast_observation_state` 不再覆盖活跃的
`weather_live_cross_observations` 健康判断。
除 permission-host keeper 外，当前 required shadow/collector/monitor 都有显式
start/health/dependency contract；controller 不从正在运行的 pane 猜恢复命令。
canonical refresh 是唯一允许的 unmanaged bounded one-shot，但只能 attach，不能创建
server。生产操作不得直接用 `tmux kill-server`、`tmux kill-session`、底层 start/stop
脚本或手拼 live 命令绕过 controller。

如果发现进程仍在其他 socket，只记录并按生产变更流程迁移；涉及 live 的 session
不得在巡检中自动重启或跨 socket 搬迁。

凡是会重建 canonical tmux server、批量重启 session 或调整生产启动集合的操作，
必须先保存 observed manifest，完成后比较 session 全集：

```bash
PRECHANGE_DIR="$(mktemp -d /tmp/weather-production-prechange.XXXXXX)"
PRECHANGE_MANIFEST="$PRECHANGE_DIR/manifest.json"
.venv/bin/python scripts/ops/weather_production_manifest.py --strict --json-out "$PRECHANGE_MANIFEST"
# 执行已授权的改动
.venv/bin/python scripts/ops/weather_production_manifest.py --strict --compare-prechange "$PRECHANGE_MANIFEST"
```

改动前存在、改动后消失的任何 canonical JRS session 都是 `critical`，部署不算完成。
只有本次明确要停的实例才可逐项传
`--allow-missing-session SESSION`；不得因为主要服务已恢复就忽略其他 strategy/shadow/collector。

先用这个脚本看当前状态：

```bash
cd /home/rui/projects/pm_agent
PYTHONPATH=. .venv/bin/python scripts/ops/process_status.py
```

它会输出：

- 当前识别到的常驻服务
- 是否正在运行
- PID
- 日志路径
- 是否建议在重启后重新拉起

## 2. 当前默认关注的常驻进程

### A. Telegram Research Bot

用途：

- Telegram 里收 Polymarket 链接
- 支持 `/full` 和 `/prompt`

启动命令：

```bash
cd /home/rui/projects/pm_agent
export TG_RESEARCH_BOT_TOKEN="..."
export TG_RESEARCH_ALLOWED_CHAT_IDS="5589339017"
scripts/ops/telegram_research_bot_ctl.sh start
```

是否建议重启后拉起：

- 是

说明：

- 当前推荐通过固定脚本管理：

```bash
scripts/ops/telegram_research_bot_ctl.sh start
scripts/ops/telegram_research_bot_ctl.sh status
scripts/ops/telegram_research_bot_ctl.sh logs 80
scripts/ops/telegram_research_bot_ctl.sh stop
```

- 默认日志：`runtime/logs/telegram_research_bot.log`
- 默认 pid：`runtime/telegram_research_bot.pid`
- 机器重启后不会自动恢复，但恢复方式已经固定

### B. Weather Dashboard（FastAPI + React 前端）

> ⚠️ 旧的 `strategy_dashboard_server`（端口 8011，服务 PMM/ARB 框架）已废弃，不需要启动。当前使用下方的天气 dashboard。

用途：天气策略大盘看板（本机分析用，不参与 N100 生产）。

启动命令（一键启动/复用 API + 前端，不改写现有 DB）：

```bash
cd /home/rui/projects/pm_agent
scripts/weather_dashboard/run_stack.sh
```

全量重建只能显式执行 `scripts/weather_dashboard/run_stack.sh --rebuild`。

服务端口：API `:8000`，前端 `:5174`。详见 [`docs/WEATHER_DASHBOARD_TROUBLESHOOTING.md`](WEATHER_DASHBOARD_TROUBLESHOOTING.md)。

### C. Weather Position Monitor

用途：

- 监控天气策略已有持仓
- 写 heartbeat 到 `runtime/strategy_runtime.db`

启动命令：

- 不写死，按你当前实际参数启动
- 当前在线参数可以直接从 `scripts/ops/process_status.py` 输出里抄

是否建议重启后拉起：

- 如果你还在跑 weather live/paper 监控，就要

### C2. Weather Live on N100

> **详细命令和口径见** [`docs/WEATHER_STRATEGY_ENTRYPOINT.md`](WEATHER_STRATEGY_ENTRYPOINT.md)，此处只列最小恢复信息。

用途：N100 上运行天气策略实盘 loop（`/home/jiarui/projects/pm_agent`）。

**机器重启后恢复**：

```bash
# 从当前 Mac 执行
ssh jiarui@192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_doctor.py --http-timeout 6'
```

关键注意：`city_pool=t1_trading` 是交易池真相，N100 代理 `xray` 监听 `127.0.0.1:10809`（Gamma/CLOB/Telegram 均依赖）。doctor 通过 crontab 每 15 分钟巡检，日志在 `runtime/weather_edge_v1/live_cycle/doctor_cron.log`。

**N100 代理 failover**：

当 N100 原生 `127.0.0.1:10809` 访问 Gamma/CLOB 失败，但本机代理可以访问时，先用下面的只读检查确认：

```bash
scripts/ops/weather_n100_proxy_failover.sh --check-only
```

如果输出显示 N100 原代理失败、本机隧道代理可用，则执行切换：

```bash
scripts/ops/weather_n100_proxy_failover.sh
```

该脚本会检查 N100 原 `127.0.0.1:10809`，失败时建立 Mac `127.0.0.1:7897` 到 N100 `127.0.0.1:18089` 的 SSH reverse tunnel，并备份更新 N100 `~/projects/weather_data_feed_service/.env` 与 `~/projects/pm_agent/.env`。脚本不会自动重启数据/策略服务；更新后只重启受影响的 data-feed producer 或 live runner，并先确认是否会触发真实下单。这是临时兜底；长期仍应修 N100 自身 xray 节点/订阅/流量。

如果只是显式切换远端代理，不走 failover 判断，直接指定目标 proxy：

```bash
# 临时走 Mac fallback
scripts/ops/weather_n100_proxy_failover.sh --apply-proxy http://127.0.0.1:18089

# N100 原生代理恢复后切回
scripts/ops/weather_n100_proxy_failover.sh --apply-proxy http://127.0.0.1:10809
```

默认会写这些 key：`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`WEATHER_DATA_FEED_MARKET_PROXY`、`WEATHER_PREDICT_PROXY`。需要调整时用 `REMOTE_PROXY_ENV_KEYS=...`，不要改脚本逻辑。

注意：本机 fallback 不能让远端数据走高倍率节点。脚本会尝试通过 Clash Verge/Mihomo 的 Unix socket 把相关 selector 分组切到 `🇯🇵 日本 01丨1x JP`；如本机代理 UI 改过规则，先确认 `Gamma/CLOB/Telegram` 不在 5x 节点上。高频 `source_orderbook_timing` 监控不要长期走本机 fallback，除非另行限频和确认节点倍率。2026-06-30 当前策略：先用 JP 1x fallback 顶到 7 月，再切回修好的 N100 原生代理。

### D. PMM Main

用途：

- 真正的 PMM 主循环

说明：

- 当前不在运行也正常
- 只有在你明确跑某个 PMM 实例时才需要拉起

参考手册：

- [docs/pmm/PAPER_RUNBOOK.md](/home/rui/projects/pm_agent/docs/pmm/PAPER_RUNBOOK.md)

## 3. 当前哪些信息算“真”

优先级按下面来：

1. `scripts/ops/process_status.py` 的实时输出
2. 实际 `ps -ef`
3. `runtime/strategy_runtime.db`
4. `runtime/*.pid`
5. 文档

注意：

- `runtime/*.pid` 不是全仓库统一真相，只能作为辅助
- `strategy_runtime.db` 目前主要覆盖 PMM / weather monitor 这类接入 heartbeat 的实例
- Telegram Research Bot 现在还没有接进 `strategy_runtime.db`

## 4. 重启后最小恢复清单

**一键恢复（推荐）：**

```bash
cd /home/rui/projects/pm_agent
scripts/ops/after_reboot.sh
```

自动按顺序：打印状态 → 启动 Dashboard → 启动 Telegram Bot → 检查 N100 实盘。已运行的服务会自动跳过。

分项运行：

```bash
scripts/ops/after_reboot.sh --dashboard   # 仅 Dashboard
scripts/ops/after_reboot.sh --n100        # 仅 N100 检查
scripts/ops/after_reboot.sh --status      # 仅看状态
```

## 5. 常用文件

- 运行库：`runtime/strategy_runtime.db`
- 天气决策日志：`runtime/weather_decision_journal.db`
- 运行日志目录：`runtime/logs/`
- 研究 bot 入口：`scripts/ops/telegram_research_bot.py`
- 状态脚本：`scripts/ops/process_status.py`

## 6. 当前已知缺口

这一版先不解决，但要心里有数：

- 没有统一 supervisor
- 没有 systemd user service
- Telegram Research Bot 没接 heartbeat
- dashboard 的 `/api/v1/ops/status` 目前偏 PMM，不是统一运维总览

## 7. 建议的后续升级顺序

如果后面继续补，优先顺序建议是：

1. 给 Telegram Research Bot 接入 heartbeat
2. 给常驻进程做统一 pid/log/service 约定
3. 再决定要不要上 systemd user services

## 8. 备份策略（2026-06-19 从 AGENTS.md/CLAUDE.md 迁入）

N100 备份入口：
```bash
ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && scripts/ops/backup_data.sh'
```

备份输出：
```text
/home/jiarui/weather-predict-backups/weather-predict-data-YYYYMMDDTHHMMSSZ.tar.zst
/home/jiarui/weather-predict-backups/weather-predict-data-YYYYMMDDTHHMMSSZ.tar.zst.sha256
```

备份范围：`output/`、`cache/pm_history/`、`cache/wu_obs/`、`cache/iem_v2_*.csv`

最低要求：
- N100 本地保留最近 14 天 tar 包。
- 本机 `runtime/weather_edge_v1/market_data/` 是第二份镜像。
- 每天同步一次，重启或故障后手动同步一次。
- 大文件原始 cache 不进 git，只走 `rsync` / `tar`。
