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
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_production_ctl.py plan
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
git status --short
git rev-parse HEAD
ps aux | rg 'weather|low_price|tmax|hko|source_event|regime' | rg -v 'rg '
launchctl list | rg 'pm-agents|weather'
```

不直接查默认 tmux、旧 `weather-jrs` socket 或 screen；这些不是 JRS 生产真相。
canonical JRS session 由 manifest 和 `weather_production_ctl.py health` 盘点；需要底层诊断时只能
`source scripts/ops/weather_jrs_tmux_env.sh` 后通过 `weather_jrs_tmux weather-data-feed-jrs ...`
查看。随后读目标实例的 latest/events/opportunities/orders 与 pause/state 文件。不要根据脚本名、文档
`live` 标签或一个 PID 推断真实下单能力。

manifest 是部署 preflight：必须核对 physical DB route、每个 live PID 的 checkout/head/loaded SHA、canonical JRS tmux session、LaunchAgent 退出状态和 DB open handles。`critical` 时不得重启或切 live；先修 identity 根因。manifest 不替代 exchange/order pre-state。

`src/strategies/runtime/production.yaml.managed_runtimes` 是当前生产 desired state；
`instances.yaml` 仍是研究/历史 registry，不能代替 active production list。生产启停和恢复优先走
`scripts/ops/weather_production_ctl.py`。底层 start script 是 controller 的执行合同，不是 AI/操作员的默认直接入口；禁止手拼 tmux/live 命令绕过 desired-state、依赖和后置检查。

当前 controller 的真实边界是：`health/plan` 只读，`reconcile --apply` 只启动 desired-state 中缺失的
runtime，不停止、替换或重启已存在进程。因此不得把 `reconcile` 说成完整部署事务；对已存在实例的
pause/stop/restart 只能使用该实例在仓库中已登记的精确合同，并必须执行 pre/post manifest 对比。
若实例只有 `start_script` 而没有可审计的 pause/stop/restart 合同，当次生产重启必须阻断：先补齐控制面合同和测试，
不允许 AI 手拼 kill/tmux 命令填空。

任何可能重启、重建或迁移 canonical JRS tmux server/session 的改动，必须保存 observed pre-state，并在改动后做同集合比较：

```bash
PRECHANGE_DIR="$(mktemp -d /tmp/weather-production-prechange.XXXXXX)"
PRECHANGE_MANIFEST="$PRECHANGE_DIR/manifest.json"
.venv/bin/python scripts/ops/weather_production_manifest.py --strict --json-out "$PRECHANGE_MANIFEST"
# 执行已授权的生产改动
.venv/bin/python scripts/ops/weather_production_manifest.py --strict --compare-prechange "$PRECHANGE_MANIFEST"
```

后置比较会把任何改动前存在、改动后消失的 canonical JRS session 判为 `critical`。若本次明确授权就是停某实例，只能逐个传 `--allow-missing-session SESSION`；不得用宽泛通配或跳过后置检查。后置检查失败时部署不算完成，先恢复被误伤实例，再重新验证 process、raw runtime 和 exchange/order state。

## Git-first

1. 在本机源码修改。
2. 运行 focused tests、静态检查和 dry-run/shadow smoke。

共享分析/运维入口（coverage gate、manifest、canonical helper）发生接口或
过滤语义变更时，除 focused unit test 外，还要运行所有固定下游入口的真实
CLI smoke（至少 account reconcile 与 canonical refresh dry-run/status）。
公共参数新增应提供兼容默认值；不能只验证被改模块自身能启动。
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

只有明确授权后执行。使用 `production.yaml` 登记的 controller/start/stop/pause 合同；凡读写 JRS 的子进程均由
canonical helper 进入 `weather-data-feed-jrs` tmux server。不使用默认 tmux、旧 socket、screen、nohup 或手写临时 daemon。

顺序：

1. 记录 pre-state：PID、command line、SHA、pause、最近 raw event/order/fill。
2. 用 `weather_production_ctl.py plan` 锁定目标与依赖；确认存在已登记的精确实例合同后才 pause/stop 目标，不影响邻近策略。
3. 重载已提交版本与明确参数；不依赖脚本默认 policy。
4. 检查新 PID/started-at/command line。
5. 用 pre-change manifest 做 canonical JRS session 同集合后置比较，确认没有误伤其他已有实例。
6. 检查第一轮 latest/events/opportunities/plans/orders。
7. 若允许 live，确认 caps、pause、`--live/--confirm-live` 等真实状态与 authenticated order 结果。
8. 失败立即回滚到记录的 SHA/参数/进程状态。

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
