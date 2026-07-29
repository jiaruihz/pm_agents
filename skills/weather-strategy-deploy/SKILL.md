---
name: weather-strategy-deploy
description: 部署、启停或变更 weather 生产行为，包括 Mac 当前生产 runner、data-feed、city/source policy、strategy instance、entry/sizing/execution policy、live/shadow switch，以及 N100 恢复后的远端部署。必须 git-first、动态核对实例、保留资金安全和显式确认，并分别验证本地代码、进程、raw runtime、API/页面。禁止把旧 N100/mid_price 示例当当前清单或用 scp/rsync 直推代码。
---

# Weather strategy deploy

生产行为变更是高风险动作。代码修改、测试和提交可直接完成；真正启停 live、重启生产、远端 checkout 或改变资金行为前必须有用户对该动作的明确授权。

## 当前边界

- 当前短期生产主机是 Mac；`/Users/deepsleep/projects/pm_agents` 是控制/开发仓库，不证明 live 进程从该 checkout 运行。实际 checkout、HEAD、loaded SHA 必须由 manifest 和进程核对。
- 当前 data-feed runtime：`/Volumes/jrs/weather_data_feed_service_runtime`。
- N100 在磁盘/备份/服务链恢复验证前只作历史/恢复对象，不是默认部署目标。
- 策略状态以进程 + raw runtime + authenticated exchange evidence 为准；registry 是路由，不是 present-state proof。

先读：`AGENTS.md`、`WEATHER_REPO_BOUNDARY.md`、`WEATHER_STRATEGY_ENTRYPOINT.md`、`WEATHER_STRATEGY_REGISTRY.md`、`OPS_RUNBOOK.md`，以及目标策略 living doc。

## 先定义部署对象

写清：

```text
host / repo SHA / strategy instance / process manager / execution mode
source policy / signal policy / sizing / entry band / fee policy / caps
expected raw output / stop-pause mechanism / rollback
```

不能只写 `execution_policy`。同名 policy 的不同 instance、source、band、size 是不同生产对象。

## 变更前动态盘点

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
git status --short
git rev-parse HEAD
ps aux | rg 'weather|low_price|tmax|hko|source_event|regime' | rg -v 'rg '
launchctl list | rg 'pm-agents|weather'
tmux list-sessions
tmux -L weather-jrs list-sessions
```

若策略使用 screen，再查 `screen -ls`。随后读目标实例的 latest/events/opportunities/orders 与 pause/state 文件。不要根据脚本名、文档 `live` 标签或一个 PID 推断真实下单能力。

manifest 是部署 preflight：必须核对 physical DB route、每个 live PID 的 checkout/head/loaded SHA、canonical JRS tmux session、LaunchAgent 退出状态和 DB open handles。`critical` 时不得重启或切 live；先修 identity 根因。manifest 不替代 exchange/order pre-state。

## Git-first

1. 在本机源码修改。
2. 运行 focused tests、静态检查和 dry-run/shadow smoke。
3. 复核 diff，只纳入本次范围。
4. 创建 scoped commit，记录 SHA。
5. Mac 当前生产从这个已提交 checkout 重载；不得让未提交代码直接成为生产版本。
6. N100 恢复后使用 `git fetch` + 固定 SHA checkout/fast-forward；不 `scp`/`rsync` 代码文件。

worktree 已脏时保留用户改动。若目标文件已有无关修改，先分离范围；不能把整棵脏树一并提交。

## 本地验证

按目标选择：

- `.venv/bin/python -m pytest <focused tests>`
- `.venv/bin/python -m py_compile <touched python files>`
- runner 的 dry-run / shadow mode
- `scripts/ops/weather_data_feed_prod_health_check.py`
- `scripts/weather_dashboard/run_stack.sh --status`

涉及 order lifecycle 时至少覆盖：partial fill、cancel/replace remaining、hard shares vs cash、fee evidence、duplicate city/date/token、stale thesis、exchange reject。

## 生产启停

只有明确授权后执行。优先使用仓库已有 start/stop/status/pause 脚本或已登记的 LaunchAgent/tmux/screen 管理方式，不手写临时 daemon。

顺序：

1. 记录 pre-state：PID、command line、SHA、pause、最近 raw event/order/fill。
2. pause/stop 目标实例；不影响邻近策略。
3. 重载已提交版本与明确参数；不依赖脚本默认 policy。
4. 检查新 PID/started-at/command line。
5. 检查第一轮 latest/events/opportunities/plans/orders。
6. 若允许 live，确认 caps、pause、`--live/--confirm-live` 等真实状态与 authenticated order 结果。
7. 失败立即回滚到记录的 SHA/参数/进程状态。

## N100 恢复分支

部署前先验证 `smartctl`、文件系统可写、备份完整性、目标 checkout 状态和服务链。任何一项未过都不恢复生产 timer。成功后分别报告：local verified、pushed、remote checkout SHA、runtime restarted、raw/API/browser reachable。

## 修复影响半径

生产 bug 修好后必须重放污染窗口，给出受影响 order/fill 数、逐条清单、错误 notional/shares/fee/PnL delta，并更新现有事故/治理记录。只说“guard 加好了”不算完成。

## 交付格式

```text
scope:
local tests:
commit SHA:
production host/process:
pre-state -> post-state:
runtime evidence:
orders/fills impact:
rollback:
not done / blocker:
```

不要把“端口在监听”“文件已复制”“进程存在”单独称为部署完成。
