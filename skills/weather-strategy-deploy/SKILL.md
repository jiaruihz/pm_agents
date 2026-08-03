---
name: weather-strategy-deploy
description: 部署、启停或变更 Mac mini 上的 weather 当前生产行为，包括 runner、data-feed、city/source policy、strategy instance、entry/sizing/execution policy 与 live/shadow switch。必须以 Mac production controller、manifest 和 production.yaml 为当前真相，git-first，保留资金安全和显式确认，并验证代码、进程、raw runtime、exchange、API/页面。本 skill 不部署 N100；不得读取或执行历史 N100/mid_price 运维链路，显式 N100 灾备请求需要独立恢复合同。
---

# Weather strategy deploy

生产行为变更是高风险动作。代码修改、测试和提交可直接完成；真正启停 live、重启生产、远端 checkout 或改变资金行为前必须有用户对该动作的明确授权。

## 当前边界

- 当前生产主机是 Mac mini。所有普通部署、启停、健康检查和 runtime 恢复默认且仅以 Mac mini 为目标；不得为“保险起见”同时检查、同步、修改或重启 N100。
- `/Users/deepsleep/projects/pm_agents` 是控制/开发仓库，不证明 live 进程从该 checkout 运行。实际 checkout、HEAD、loaded SHA 必须由 manifest 和进程核对。
- 当前 data-feed runtime：`/Volumes/jrs/weather_data_feed_service_runtime`。
- N100 不属于当前生产拓扑。除非用户明确提出“N100 灾备恢复/迁回”，否则不得 SSH N100、读取其旧 live 配置、运行其 timer/service、把其 raw 当当前真相，或将任何代码部署到 N100。
- 策略状态以进程 + raw runtime + authenticated exchange evidence 为准；registry 是路由，不是 present-state proof。

默认只读：`AGENTS.md`、`src/strategies/runtime/production.yaml`、`WEATHER_REPO_BOUNDARY.md` 的当前 Mac 边界、目标策略 living doc，以及与本次参数直接相关的配置/测试。

需要生产接手背景时，只读 `WEATHER_STRATEGY_ENTRYPOINT.md` 的“当前接手口径”且在 `Historical Production Posture` 前停止。需要控制器/JRS 细节时，只读 `OPS_RUNBOOK.md` 的“Mac JRS 常驻进程”当前章节。普通 Mac 部署不得继续读取两个文档中的 N100 历史章节，也不得从旧命令复制部署步骤。策略研究状态仅在本次变更涉及 eligibility/live 授权判断时读取 `WEATHER_STRATEGY_REGISTRY.md`。

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
6. 不向 N100 复制或部署代码；普通 Mac 变更不得顺带触碰 N100。

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

## N100 不在本流程内

本 skill 不包含 N100 的部署或恢复命令。即使用户明确要求恢复或迁回 N100，也先停止 Mac 部署流程，将其作为新的远端生产边界，建立或加载经审核的独立恢复合同，再重新确认授权、磁盘与文件系统健康、备份完整性、代码来源、服务依赖和资金状态。不得沿用历史 runbook 的旧实例清单、timer、mid_price 参数或 raw 路径，也不得临时拼 SSH/scp/rsync 命令。用户没有明确提出 N100 灾备时，不得进入该方向。

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
