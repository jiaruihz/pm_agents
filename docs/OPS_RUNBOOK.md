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

### B. Strategy Dashboard Server

用途：

- 查看实例、运行状态、日志、ops 接口

启动命令：

```bash
cd /home/rui/projects/pm_agent
PYTHONPATH=. .venv/bin/python -m src.interfaces.web.strategy_dashboard_server \
  --host 127.0.0.1 \
  --port 8011 \
  --artifacts-dir src/strategies/pmm/backtest/.artifacts \
  --runtime-dir runtime
```

是否建议重启后拉起：

- 如果你还在用看板，就要
- 如果暂时不用看板，可以不拉

健康检查：

```bash
curl -s http://127.0.0.1:8011/api/v1/health
```

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

用途：

- N100 上运行天气策略实盘 loop
- 当前代码目录：`/home/jiarui/projects/pm_agent`
- 生产天气数据目录：`/home/jiarui/projects/weather-predict`

当前口径：

- live loop 可以常驻，但真实下单必须先看 pause 状态
- 已成交的小额试错仓先不处理，优先观察
- 不再增加第二套 T1 allowlist；`city_pool=t1_trading` 是交易池真相
- 如果再出现 T2 城市进入 live signal，应视为上游 city_pool / live 参数问题，直接暂停并排查，而不是靠下游静默过滤

检查命令：

```bash
cd /home/jiarui/projects/pm_agent
.venv/bin/python scripts/ops/weather_live_status.py status --json
.venv/bin/python scripts/ops/weather_live_doctor.py --http-timeout 6 --sync-dry-run --require-live-loop --require-telegram-control
tail -n 80 runtime/weather_edge_v1/live_cycle/loop.log
```

N100 代理配置：

```bash
HTTP_PROXY=http://127.0.0.1:10809
HTTPS_PROXY=http://127.0.0.1:10809
ALL_PROXY=http://127.0.0.1:10809
```

说明：

- 这是 N100 本机已有的 `xray` HTTP proxy
- Gamma / CLOB / Telegram 都依赖这条出网路径
- doctor 每 15 分钟通过 crontab 巡检一次，失败时尝试 Telegram 告警，并写 `runtime/weather_edge_v1/live_cycle/doctor_cron.log`

2026-05-16 观察记录：

- 旧本机 live 曾在未限制 T1 时提交过 Wuhan 31C No，小额已成交，先不处理
- 根因是旧 live signal builder 从全量 snapshot 入池；现行 N100 live 已传 `--city-pool t1_trading`
- 后续观察重点：是否还有非 T1 城市出现在 N100 `runtime/weather_edge_v1/live/*.jsonl`
- 若出现，先暂停实盘，再查上游 snapshot 的 `city_pool` 和 live cycle 参数

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

如果机器重启，按这个顺序恢复：

1. 进入项目目录

```bash
cd /home/rui/projects/pm_agent
```

2. 检查环境变量和 `.env`

```bash
test -f .env && echo ".env ok"
```

3. 看当前状态

```bash
PYTHONPATH=. .venv/bin/python scripts/ops/process_status.py
```

4. 先拉你现在真正依赖的服务

- Telegram Research Bot
- Strategy Dashboard Server
- Weather Position Monitor

推荐先拉 research bot：

```bash
scripts/ops/telegram_research_bot_ctl.sh start
```

5. 再次检查状态

```bash
PYTHONPATH=. .venv/bin/python scripts/ops/process_status.py
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
