---
name: weather-strategy-deploy
description: 部署、启停或变更 Mac mini 上的 weather 当前生产行为，包括 runner、data-feed、market proxy、选择性 CLOB WebSocket capture、city/source policy、strategy instance、entry/sizing/execution policy 与 live/shadow switch。必须以 Mac production controller、manifest 和 production.yaml 为当前真相，git-first，保留资金安全和显式确认，并验证代码、进程、raw runtime、exchange、API/页面。本 skill 不部署 N100；不得读取或执行历史 N100/mid_price 运维链路，显式 N100 灾备请求需要独立恢复合同。
---

# Weather strategy deploy

生产行为变更是高风险动作。代码修改、测试和提交可直接完成；真正启停 live、重启生产、远端 checkout 或改变资金行为前必须有用户对该动作的明确授权。

## 当前边界

- 当前生产主机是 Mac mini。所有普通部署、启停、健康检查和 runtime 恢复默认且仅以 Mac mini 为目标；不得为“保险起见”同时检查、同步、修改或重启 N100。
- `/Users/deepsleep/projects/pm_agents` 是控制/开发仓库，不证明 live 进程从该 checkout 运行。实际 checkout、HEAD、loaded SHA 必须由 manifest 和进程核对。
- 当前 data-feed runtime：`/Volumes/jrs/weather_data_feed_service_runtime`。
- N100 不属于当前生产拓扑。除非用户明确提出“N100 灾备恢复/迁回”，否则不得 SSH N100、读取其旧 live 配置、运行其 timer/service、把其 raw 当当前真相，或将任何代码部署到 N100。
- 策略状态以进程 + raw runtime + authenticated exchange evidence 为准；registry 是路由，不是 present-state proof。

默认只读：`AGENTS.md`、`src/strategies/runtime/production.yaml`、`docs/WEATHER_REPO_BOUNDARY.md` 的当前 Mac 边界、目标策略 living doc，以及与本次参数直接相关的配置/测试。

需要生产接手背景时，只读 `docs/WEATHER_STRATEGY_ENTRYPOINT.md` 的“当前接手口径”且在 `Historical Production Posture` 前停止。需要控制器/JRS 细节时，只读 `docs/OPS_RUNBOOK.md` 的“Mac JRS 常驻进程”当前章节。普通 Mac 部署不得继续读取两个文档中的 N100 历史章节，也不得从旧命令复制部署步骤。策略研究状态仅在本次变更涉及 eligibility/live 授权判断时读取 `docs/WEATHER_STRATEGY_REGISTRY.md`。

## 先定义部署对象

写清：

```text
host / repo SHA / strategy instance / process manager / execution mode
source policy / signal policy / sizing / entry band / fee policy / caps
expected raw output / stop-pause mechanism / rollback
```

不能只写 `execution_policy`。同名 policy 的不同 instance、source、band、size 是不同生产对象。

### Market proxy 与选择性 CLOB WS

- market proxy 切换只使用 `docs/OPS_RUNBOOK.md` 当前章节登记的
  `scripts/ops/weather_market_proxy_ctl.py`；不得逐脚本修改 `.env`、`HTTP_PROXY` 或
  `--market-proxy`。成功标准包括 Gamma probe、新 market-books batch、全部 consumer
  proxy binding、依赖 freshness 和失败回滚，不是端口可连接。
- 变更 WS collector 时，把 REST canonical book 与 `ws_incremental` 视为两个输出合同；不得让
  WS selective coverage 取代完整 ladder 或让消费视图重新请求盘口。
- 部署对象必须额外冻结 selector/capture-policy version、城市、hot bracket/window、subscription
  set、代理流量预算/日、落盘预算/日、保留期和 stop condition。
- 后验分别验证 REST raw freshness、WS baseline/delta reconstruction、gap/reconnect、当前订阅集合、
  collector bytes/messages 与预算；无消息只可按 policy-valid window 判定，不能直接当作健康或无变化。
- 当前 WS collector 是 capture-only：只落 raw frame 与 combined health。若本次没有同时交付并验证
  deterministic reconstructed-book materializer、append-only subscription/capture manifest 和 parity test，
  部署结论只能写“capture healthy”，不得写“feature/model-ready”；baseline/delta reconstruction 项明确记为未完成。

## 变更前动态盘点

```bash
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_production_ctl.py plan
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_storage_identity_audit.py
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

storage audit 负责补 manifest 的静态写入合同：active raw root、health artifact、live journal 与 DB 路径只从 `production.yaml`/共享 loader 取值。出现独立 `weather.db`、多个 owner 共用 mutable journal、JSONL parse/schema/shape drift 或脚本内第二份 active path 清单时，先收口 writer/entrypoint，不以复制、同步或下游 guard 掩盖。

canonical tmux socket、binary path/hash 和系统设置中的 Full Disk Access 只证明期望 identity，不能证明当前 parent 仍能访问 JRS。必须以 server 内真实 read/write probe、canonical DB read 和 producer/downstream freshness 为准。开始处理重复故障前先搜索 incident/governance 与 git history；说明本次是入口统一、恢复自动化还是根因消除。没有 fresh-host、server recreate、锁屏/解锁和 reboot/login 的真实验收，不得称为永久解决。

`src/strategies/runtime/production.yaml.managed_runtimes` 是当前生产 desired state；
`instances.yaml` 仍是研究/历史 registry，不能代替 active production list。生产启停和恢复优先走
`scripts/ops/weather_production_ctl.py`。底层 start script 是 controller 的执行合同，不是 AI/操作员的默认直接入口；禁止手拼 tmux/live 命令绕过 desired-state、依赖和后置检查。
业务 runtime 必须通过 `release_id` 引用 `production_releases` 的 checkout root 与 full
`expected_repo_sha`。部署不是把 branch 往前推：先提交、更新 release pin，再由 controller
重载并核对 loaded SHA；release checkout 或 live process SHA mismatch 时不得声称部署完成。
共享 helper 是 attach-only，并用 tmux `-N` 保证 server 缺失时 fail closed，不会由业务脚本或 LaunchAgent 抢建；persistent
session mutation 必须由 controller 注入 authority。不得通过设置同名环境变量或直接执行 start/stop
脚本模拟 controller。canonical refresh 仅是已登记的 bounded one-shot，不拥有 permission-host 创建权。
Dashboard API 同样是 controller-managed runtime；不得恢复旧 `com.pm-agents.weather-api`
LaunchAgent。FE 的非 JRS LaunchAgent 与 canonical refresh 的 bounded one-shot 是不同合同，不能据此允许
LaunchAgent 直接承载 API、collector、strategy 或 DB 子链。

当前 controller 的真实边界是：`health/plan` 只读，`reconcile --apply` 只启动 desired-state 中缺失的
runtime，不停止、替换或重启已存在进程；`recover-jrs-context --apply` 是唯一允许重建 canonical
permission host 的有界事务，必须同时提供 reason、`--confirm-live`、保存的 restore manifest，并在杀旧
server 前通过 prospective-host JRS probe。它只能验证当前调用上下文，不能授予或修复 macOS TCC，Mac
锁屏或 prospective probe 失败时必须阻断。因此不得把 `reconcile` 或一次 recovery 成功说成完整部署/永久修复；对已存在实例的
pause/stop/restart 必须走 controller 并执行 pre/post manifest 对比。`safe` 且非 live 的实例可由
controller 使用精确 session identity 做 stop-then-registered-start；live/guarded 实例仍必须有独立、可审计的
pause/cancel/restart 合同，缺失时阻断，不允许 AI 手拼 kill/tmux 命令填空。

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
CLI smoke（至少 account reconcile、canonical refresh 状态文件/日志检查；若确需执行 refresh，只使用已登记
one-shot，并确认没有在跑的同名任务）。
公共参数新增应提供兼容默认值；不能只验证被改模块自身能启动。
3. 复核 diff，只纳入本次范围。
4. 创建 scoped commit，记录 SHA。
5. Mac 当前生产从这个已提交 checkout 重载；不得让未提交代码直接成为生产版本。
6. 不向 N100 复制或部署代码；普通 Mac 变更不得顺带触碰 N100。

worktree 已脏时保留用户改动。若目标文件已有无关修改，先分离范围；不能把整棵脏树一并提交。

部署或修复任务使用的临时 worktree 必须在交付时收口：已提交代码保留 branch/SHA，未提交代码先做
content-addressed snapshot，ignored 研究产物通过 `weather_research_artifact_ctl.py archive --source-root ...`
迁到 archive，然后移除非 `production.yaml` 登记的实体 worktree 并 prune 失效 metadata。不得长期在
`/Users/deepsleep/projects` 顶层留下 `pm_agents_<task>`；manifest 出现
`unregistered_persistent_worktrees` 时，先确认无进程/LaunchAgent/cwd 引用再清理。当前登记的生产 checkout
只能在明确生产维护授权下变更或移除。

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
