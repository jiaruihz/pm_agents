# Weather Strategy Entrypoint

Status: current-source
Updated: 2026-08-06 controller-only production handoff
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md

这是 weather 生产接手的第一入口。它只负责把人和模型路由到当前事实，不维护第二份进程清单、城市池、策略参数或
live/shadow 状态。

## 1. 先确认当前事实

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_production_ctl.py plan
.venv/bin/python scripts/ops/weather_storage_identity_audit.py
```

停止继续操作并先修 P0 的情况：

- manifest `critical`；
- `db_route.status != healthy`；
- physical canonical 与 `runtime/weather.db` 不是同一 device/inode；
- 存在异常 production checkout、非 canonical DB consumer 或失败的生产 LaunchAgent；
- canonical JRS context 内 read/write probe、canonical DB、producer freshness 或下游 latest 失败。

不要用 `ps`、PID、session 名、FDA 设置、registry、日期报告或“昨天正常”替代以上事实。

## 2. 权威来源

| 问题 | 唯一入口 |
|---|---|
| 应该运行什么 | `src/strategies/runtime/production.yaml` |
| 实际运行什么 | strict production manifest |
| 健康、依赖与建议动作 | production controller `health/plan` |
| live/shadow、参数与当前订单 | 进程参数 + pause/state + Mac raw order journal + exchange response |
| canonical 分析 DB | `production.yaml` 解析出的 JRS physical canonical；仓库路径仅为同 inode 兼容入口 |
| 当前 raw、order journal、health artifact | production contract / shared loader；不在文档写死 |
| 策略研究结论 | `WEATHER_STRATEGY_REGISTRY.md` + family living doc +最新 freeze/reset evidence |
| 事故与恢复验收 | `WEATHER_JRS_RUNTIME_INCIDENTS.md` + `OPS_RUNBOOK.md` |

研究状态和生产状态必须分开：registry 的 `inconclusive/shadow_candidate` 不能证明进程是否真实下单；运行中的
`live` 字样也不能证明 alpha confirmed。

## 3. 生产变更

生产行为变更必须使用 `weather-strategy-deploy` skill，走 git-first：

1. 从 manifest 与 production contract 锁定准确实例、checkout、参数来源、依赖和资金边界；
2. 修改权威配置/代码并做定向测试与 hardcode scan；
3. 提交代码；
4. 用 controller 的已登记 `restart/reconcile` 合同执行；涉及 live 必须显式 `--confirm-live`；
5. 验证 production SHA、PID/session、raw signal/plan/order、exchange response、开放订单和 API；
6. 比较 prechange manifest，非目标 session 消失即视为失败。

禁止用 SSH、`scp`/`rsync`、raw tmux、screen、nohup、LaunchAgent 直启、底层 start/stop 脚本或从正在运行的 pane
抄命令改变 weather 生产状态。

## 4. JRS 与恢复

所有 JRS 常驻消费者只使用 canonical `tmux -L weather-data-feed-jrs` context，但人工和普通脚本不直接操作底层
tmux。公共 helper 为 attach-only；只有 controller `recover-jrs-context` 可以在已授权维护窗口创建或重建 permission
host。

```bash
scripts/ops/after_reboot.sh
```

无参数只做 health/plan。是否使用 `--apply`、`--confirm-live` 或 `--recover-jrs-context`，必须按
[`OPS_RUNBOOK.md`](OPS_RUNBOOK.md) 与
[`WEATHER_JRS_RUNTIME_INCIDENTS.md`](WEATHER_JRS_RUNTIME_INCIDENTS.md) 的当前合同执行。

server/session 存在、binary/hash 正确或系统设置显示 FDA enabled 都不等于 JRS 权限健康；以目标 context 内真实
read/write probe 和下游 freshness 为准。

## 5. 分析与研究

- “最新/今天”先看对应 Mac raw；canonical 缺目标窗口时走 `weather-fact-rebuild` 的增量同步，不随意全量重建。
- 绩效、血缘、敞口、账户对账、部署和重建分别使用 AGENTS.md 登记的 weather skill。
- canonical 研究从 `fact_signal_candidates` / `fact_trades` 读取；不从历史 split DB 或日期报告自算并行真相。
- 新机制按 `EventEnvelope → DecisionContext → ModelOutput → SignalCandidate → TradeIntent → plan → order → fill → settlement`
  接入；不另建 collector、回放时钟或 fill/PnL 旁路。

研究入口：

- [`WEATHER_STRATEGY_REGISTRY.md`](WEATHER_STRATEGY_REGISTRY.md)
- [`WEATHER_STRATEGY_REVIEW_PIPELINE.md`](WEATHER_STRATEGY_REVIEW_PIPELINE.md)
- [`WEATHER_ANALYSIS_CONTRACT.md`](WEATHER_ANALYSIS_CONTRACT.md)
- [`WEATHER_STRATEGY_QUANT_DESIGN.md`](WEATHER_STRATEGY_QUANT_DESIGN.md)

## 6. 历史资料边界

N100、WSL、`weather-predict`、mid_price/T1-T2 allowlist、旧 PMM/ARB、旧 doctor/proxy/timer 和早期 live rollout
只作历史/恢复证据，不是当前命令或配置：

- 生产与 PnL 事故史：[`WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md`](WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md)
- 历史城市池决策：[`WEATHER_CITY_POOL_DECISIONS.md`](WEATHER_CITY_POOL_DECISIONS.md)
- repo/机器边界：[`WEATHER_REPO_BOUNDARY.md`](WEATHER_REPO_BOUNDARY.md)
- 数据与历史镜像：[`WEATHER_DATA_PIPELINE.md`](WEATHER_DATA_PIPELINE.md) 与
  [`WEATHER_DATA_CANONICAL_SOURCES.md`](WEATHER_DATA_CANONICAL_SOURCES.md)
- 被清理的逐条旧命令、allowlist 和部署记录：git history

恢复 N100 生产需要独立恢复合同：先验证磁盘健康、备份完整性与服务链，再决定是否恢复；不得从历史文档复制命令直接启动。
