# Weather Repo Boundary

Status: current-source
Updated: 2026-08-08 Mac controller, NVMe runtime and historical recovery boundary
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed

## 结论

边界按职责划分，不按旧机器或相似文件名划分：

```text
weather_data_feed/          shared data logic and schemas
production collectors      forecast / observation / raw market producers
pm_agent runners            signal / risk / execution / order lineage
weather_dashboard           canonical derived facts / API / UI
N100 + old JRS archive      historical evidence and explicit recovery inputs only
```

当前生产主机是 Mac；NVMe `/Volumes/jrs` 承载 mutable hot runtime，旧 JRS 只作为 `/Volumes/jrs-archive`
的历史归档层。`src/strategies/runtime/production.yaml` 声明期望 identity，strict manifest 报告当前事实。

## Runtime Roles

| physical/code boundary | 当前职责 | 不得承担 |
|---|---|---|
| `weather_data_feed/` | city/source/calendar/parser/schema 等共享数据逻辑 | 常驻调度、策略、下单、钱包、PnL |
| `/Volumes/jrs/weather_data_feed_service_runtime` | 当前 forecast/observation/market raw 与消费视图 runtime | 策略选择、资金逻辑、历史归档正本 |
| `/Volumes/jrs/pm_agents/runtime` | 当前策略 runtime、canonical DB、health artifacts | 大型历史研究归档 |
| `/Users/deepsleep/projects/pm_agents` | controller/development checkout、代码、文档 | 被硬编码为所有 running process checkout 或 physical DB |
| `/Volumes/jrs-archive` | historical archive、backups、large research artifacts | 当前 producer/consumer 依赖、mutable live journal |
| N100 `weather-predict` / `pm_agent` | 历史正本与独立恢复输入 | 当前生产 truth、默认 fallback、直接部署目标 |
| historical WSL paths | git history 中的旧环境证据 | 当前命令或路径合同 |

业务进程的实际 checkout、PID、tmux session、LaunchAgent 与文件句柄必须动态读取 manifest，不能从表中的开发路径推断。

## Data Boundary

当前 physical products：

```text
/Volumes/jrs/weather_data_feed_service_runtime/
  forecast/forecast_hourly_curves/
  output/observations/ and source-specific append-only families
  market_books/{latest.json,batches/}
  strategy_snapshots/
  market_ladder_snapshots/

/Volumes/jrs/pm_agents/runtime/
  weather.db
  weather_edge_v1/ strategy/order/health runtime
```

仓库 `runtime/weather.db` 只是兼容入口，健康时必须与 physical canonical DB 解析到同一 device/inode。
发现独立可写 DB、共享 live journal、volume UUID 错配或 consumer 指向 archive/N100 mirror 时均视为 P0。

当前 mutable root、canonical DB、active order journal 与 artifact roots 只从 production loader 解析；禁止在 health、refresh、
analysis 或 strategy 脚本维护第二份路径/策略清单。旧硬编码若暂时需要兼容，只可通过明确的只读 alias，并注明删除条件。

N100 mirrors、旧 `weather-predict/output`、`targeted_output`、`full_ladder_output` 和 repo-local historical runtime
只用于明确的历史恢复/研究；不得被 freshness fallback 自动选中。

## Code Boundary

新的共享逻辑按以下归属：

| 需求 | owner |
|---|---|
| source adapter、timezone、calendar、raw normalization、schema | `weather_data_feed/` |
| forecast/observation/market 网络采集与落盘 | production manifest 登记的 collector |
| feature/probability/signal/eligibility/sizing/order | strategy/feature/execution packages |
| fill/fee/settlement/canonical analysis/API | canonical ETL + `weather_dashboard/` |

旧 `src/strategies/weather_edge_v1/official_observation_feed/` 只允许 compatibility re-export。研究 cache bridge 只能显式
用于历史数据操作，不能让 live runner import 或启动 `weather-predict`。一个新城市/策略不得另建 collector、盘口 cache、
回放时钟或 order/fill/PnL 链。

## Process Ownership

所有读写 JRS 的常驻 collector、strategy、shadow、monitor、patrol 与 API 必须由 controller 管理，并复用 canonical
`tmux -L weather-data-feed-jrs` context。禁止默认 tmux、`weather-jrs`、私有 socket、screen、nohup、直接常驻
LaunchAgent 或手工脚本绕过 controller。

LaunchAgent 只可请求 production contract 登记的 bounded one-shot。server 缺失时 fail closed；只有 controller 的
`recover-jrs-context` 可在授权维护窗口创建或重建 canonical server。canonical tmux 内禁止 `run-shell`。

## Deployment Boundary

生产行为变更使用 `weather-strategy-deploy`，遵守 git-first：

- 先提交 scoped code/config change；
- controller plan/health 确认实例、依赖与实际 checkout；
- 资金行为变更保留显式确认、pause、notional cap 与可追溯 raw order；
- 重载后验证 code SHA、PID、JRS write/read、producer/consumer freshness、raw order 与 exchange response；
- 不用 `scp`/`rsync` 直推，不直接启动底层 runner，不把 N100 当默认目标。

## Strategy And Historical Ownership

策略研究状态以 `WEATHER_STRATEGY_REGISTRY.md` 为准；当前实例是否 live/shadow/paused 以 manifest、进程参数、state、
raw order 与 exchange response 为准。旧文档里的 active label、`weather-predict` paper policy 或 systemd service 名称
都不是 present-state evidence。

历史 dashboard migration 可以识别旧 strategy id 以保持旧行可读；这不授权生成新行。暂停或转向的研究默认标
`dormant` / `superseded-for-now`，不因不在主线就删除其 canonical facts 或血缘。

## Verification

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_storage_identity_audit.py
```

只有 DB route、volume UUID、canonical JRS context 与目标 producer/consumer 均通过，才能把系统描述为当前健康。
历史 N100/systemd 迁移细节见 `WEATHER_DATA_COLLECTION_INVENTORY.md` 与 git history；不得从中复制当前操作命令。
