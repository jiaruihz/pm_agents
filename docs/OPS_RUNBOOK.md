# 统一运维手册（第一版）

这份文档只解决当前最实际的问题：

- 现在有哪些常驻进程
- 哪些进程在机器重启后需要手动拉起
- 现在机器上哪些服务正在跑
- 出问题时先看哪里

目标不是做完整运维体系，而是先让当前仓库可维护。

## 1. 统一查看入口

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

启动命令（一键，含 DB 重建 + API + 前端）：

```bash
cd /home/rui/projects/pm_agent
scripts/weather_dashboard/run_stack.sh --no-rebuild   # 已有 DB 时跳过重建
```

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
# 从 WSL 执行
wsl -d Ubuntu-24.04 -- ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_doctor.py --http-timeout 6'
```

关键注意：`city_pool=t1_trading` 是交易池真相，N100 代理 `xray` 监听 `127.0.0.1:10809`（Gamma/CLOB/Telegram 均依赖）。doctor 通过 crontab 每 15 分钟巡检，日志在 `runtime/weather_edge_v1/live_cycle/doctor_cron.log`。

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
