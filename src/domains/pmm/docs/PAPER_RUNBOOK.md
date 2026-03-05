# PMM Paper 运维手册

面向日常运行 `PMM_EXECUTION_MODE=paper` 的操作说明，覆盖启动、监控、日志、Telegram 汇报和可视化看板。

## 1. 启动前检查

```bash
cd /home/rui/projects/pm_agent
test -f .env || cp .env.example .env
```

关键变量（`.env`）：

- `PMM_EXECUTION_MODE="paper"`
- `PMM_TOKEN_IDS="YES_TOKEN_ID,NO_TOKEN_ID"`
- `PMM_MARKET_DATA_SOURCE="ws"`（建议）
- `PMM_MAX_POSITION="30"`（你的当前上限）
- `PMM_TELEGRAM_ENABLED="1"`
- `PMM_TELEGRAM_REPORT_INTERVAL_SEC="3600"`
- `PMM_INSTANCE_ID="paper_rm_01"`（建议显式设置）
- `PMM_INSTANCE_LABEL="RealMadrid paper"`
- `STRATEGY_RUNTIME_DB_PATH="runtime/strategy_runtime.db"`

兼容项说明：`PMM_INSTANCE_DB_PATH` 仅兼容读取，已弃用（deprecated），不要作为主配置继续使用。

快速做配置体检：

```bash
rg -n "^(PMM_EXECUTION_MODE|PMM_TOKEN_IDS|PMM_MARKET_DATA_SOURCE|PMM_MAX_POSITION|PMM_TELEGRAM_ENABLED|PMM_TELEGRAM_REPORT_INTERVAL_SEC|PMM_INSTANCE_ID|PMM_INSTANCE_LABEL|STRATEGY_RUNTIME_DB_PATH)=" .env
```

## 2. 运行与停止

前台运行（调试时用）：

```bash
set -a; source .env; set +a
.venv/bin/python -u -m src.domains.pmm.main
```

后台常驻运行：

```bash
mkdir -p runtime/logs
set -a; source .env; set +a
nohup .venv/bin/python -u -m src.domains.pmm.main > runtime/logs/pmm_paper_live.log 2>&1 & echo $! > runtime/pmm_run.pid
```

停止：

```bash
kill "$(cat runtime/pmm_run.pid)"
```

强制停止（仅当普通 stop 无效）：

```bash
pkill -f "src.domains.pmm.main"
```

## 3. 监控命令

查看进程：

```bash
ps -p "$(cat runtime/pmm_run.pid)" -o pid=,etime=,cmd=
```

实时看日志：

```bash
tail -f runtime/logs/pmm_paper_live.log
```

查看最近 20 条 tick 汇总：

```bash
rg "tick_summary" runtime/logs/pmm_paper_live.log | tail -n 20
```

查看错误/异常：

```bash
rg -n "error|exception|failed|traceback" runtime/logs/pmm_paper_live.log
```

## 4. Telegram 检查

发送一条测试消息：

```bash
set -a; source .env; set +a
.venv/bin/python scripts/python/telegram_ping.py --text "PMM paper telegram test"
```

预期结果：终端打印 `TELEGRAM_OK ...`，并在 Telegram 收到消息。

## 5. 可视化看板

启动统一策略看板服务：

```bash
set -a; source .env; set +a
.venv/bin/python -m src.interfaces.web.strategy_dashboard_server --host 127.0.0.1 --port 8011 --artifacts-dir src/domains/pmm/backtest/.artifacts --runtime-dir runtime
```

浏览器打开：`http://127.0.0.1:8011/api/v1/health`

页面中新增的 **Paper 运维看板** 可直接查看：

- 运行状态（running/stopped、PID、启动时长）
- 当前关键配置快照（mode/strategy/token/cap/telegram）
- 运行日志 tail（可选择日志文件与行数）
- 常用命令速查（启动/停止/日志/错误筛查）

页面中的 **策略运行实例** 会从 SQLite（`runtime/strategy_runtime.db`）读取并展示多实例信息。

## 6. 实例数据库

查看实例表：

```bash
sqlite3 runtime/strategy_runtime.db "select i.instance_id,i.status,i.strategy_key,i.execution_mode,s.last_tick,s.last_pnl,i.updated_at_utc from strategy_instances i left join strategy_instance_state s on s.instance_id=i.instance_id order by i.updated_at_utc desc limit 20;"
```

查看策略目录表：

```bash
sqlite3 runtime/strategy_runtime.db "select strategy_key,strategy_name,strategy_group,is_active from strategies order by strategy_group,strategy_key;"
```

查看实时状态表：

```bash
sqlite3 runtime/strategy_runtime.db "select instance_id,last_tick,last_pnl,last_equity,heartbeat_at_utc from strategy_instance_state order by heartbeat_at_utc desc limit 20;"
```

## 7. HTTP 接口（给前端）

- `GET /api/v1/strategies`：策略目录 + 每个策略的实例统计
- `GET /api/v1/instances?limit=200&stale_after_sec=90`：实例列表（含 strategy 关联、模式、参数、路径、实时状态）
- `GET /api/v1/instances/<instance_id>/history?limit=300`：实例历史快照（用于曲线/回放）
- `GET /api/v1/accounts`：账户聚合视图
- `GET /api/v1/ops/status`：进程与日志总览
- `GET /api/v1/ops/logs?name=<log>&lines=120`：日志 tail

## 8. 常见问题

- 看板显示 `stopped` 但你刚启动过：  
  先检查 `runtime/pmm_run.pid` 是否是旧 PID，再 `ps -p <pid>` 确认。
- 有启动日志但长期无 `tick_summary`：  
  通常是行情更新慢或连接问题，优先确认 `PMM_MARKET_DATA_SOURCE=ws`，并观察 `ws_feed_started` 后是否还有新日志。
- Telegram 无小时汇报：  
  检查 `PMM_TELEGRAM_ENABLED=1`、`PMM_TELEGRAM_BOT_TOKEN`、`PMM_TELEGRAM_CHAT_ID`，并确认进程持续运行超过一小时。
