# PM Agent — 轻量项目入口（Claude）

> 工作风格：做完再交、缺数据自己补、先修根因、信息够了就动手、输出给人看。
> 本文件只保留长期安全边界、上下文路由和当前系统主轴；详细历史在 `docs/CLAUDE_CONTEXT_REFERENCE.md`，默认不全文读取。
> 本文件与 `AGENTS.md` 的核心规范保持一致。

## 0. 上下文与 Token 纪律

- 从完成当前任务所需的最小上下文开始：先读对应 skill，再读它直接指向的必要文件；不要先读完整项目历史。
- 根目录 `CLAUDE.md` 是路由和安全边界，不是生产事故百科。历史事故、具体命令、研究方法和字段细节放在对应 runbook、skill 或 living doc。
- 除非任务直接涉及生产拓扑、JRS/TCC、canonical identity、历史复发或文档缺口，不读 `docs/CLAUDE_CONTEXT_REFERENCE.md` 全文；需要时只精确检索相关段落。
- 不把整段旧对话、长工具输出、全量 PR/仓库列表反复带入后续轮次。保留结论、未决项、文件/产物指针和必要证据即可。
- 一个独立目标优先一个对话线程。目标完成后，新目标开新线程；不要用一个超长线程长期混跑研究、部署、review 和其他项目。
- 同一轮只做一次全量 snapshot（仓库、open PR、网页搜索或数据清单），下游分析与 reviewer 复用该快照；除非数据已变化或用户明确要求，不重复抓取。
- 多 agent 只用于可独立并行的窄任务。每个 worker 必须写明职责、输入边界、预期产物、模型和 token/usage；没有 usage telemetry 的结果视为未完整交付。
- 默认一个 coordinator 加少量窄 worker。机械扫描使用低成本模型；高成本模型只用于关键证据裁决和最终综合，禁止低价值 reviewer 级联与重复全仓搜索。

## 1. 当前主线与系统主轴

- 当前主线是天气温度策略；README 的旧 PMM/ARB 框架不是活跃主线。
- 研究状态以 `docs/WEATHER_STRATEGY_REGISTRY.md` 和最新 reset/freeze report 为准；生产状态必须动态核对 manifest、进程参数、raw order 与 exchange response，不能从研究标签或旧对话推断。
- 统一量化血缘：`EventEnvelope → DecisionContext → ModelOutput → SignalCandidate → TradeIntent → plan → order → fill → settlement`。
- canonical 分析表是 `fact_signal_candidates` 和 `fact_trades`。当前运行/订单事实仍以 Mac raw runtime 与 exchange evidence 为准。
- 新特征、新信号和新 runner 必须接入既有血缘；策略变更不重做 order/fill/PnL 基础设施。暂时不用的内容标为 dormant，默认保留。

## 2. 任务路由：先读对应 Skill

| 任务 | 必用 skill |
|---|---|
| 新策略、物理机制、概率模型、快源、特征、PIT 设计 | `weather-strategy-research` |
| PnL、ROI、胜率、绩效、A/B、回测切片 | `weather-strategy-performance` |
| 单日/单城/单 order 血缘与逐笔复盘 | `weather-strategy-lineage` |
| 持仓、开放订单、未结算与风险敞口 | `weather-strategy-exposure` |
| 余额、USDC、CLOB fill、现金流与账户对账 | `weather-live-account-reconcile` |
| 部署、启停、城市池、参数、live/shadow 行为 | `weather-strategy-deploy` |
| 同步、补全、重建、重新结算、数据陈旧 | `weather-fact-rebuild` |
| JRS/TCC、外置卷权限、runtime 接管与迁回 | `weather-jrs-runtime-failover` + `weather-strategy-deploy` |

skill 是默认方法入口。只有 skill 明确要求或当前任务确实触及相应风险时，才继续加载完整生产/研究文档。

## 3. 不可妥协的安全边界

- 真实 CLOB 下单、余额/私钥、删数据、远端部署、N100 live 等不可逆动作必须保留显式确认、pause、notional 上限和可追溯日志。
- 生产行为变更走 `weather-strategy-deploy` 的 git-first 流程；禁止 `scp`/`rsync` 直推。
- 缺数据或数据陈旧时，按 `weather-fact-rebuild` 补齐后再下结论；全量 rebuild 必须显式授权。
- 修复影响决策的数据/代码问题后，默认量化受影响窗口并反事实重放，给出数字和逐条清单。
- 先修根因；不要用新的 gate、fallback 或阈值掩盖异常。数据缺失、schema 不一致和盘口不可用应显式失败。
- 清理默认做标签、指针和去重。删除文件、表、脚本、raw/canonical 数据或把 dormant 改成 dead 前必须确认。

## 4. 生产 Truth 最小合同

- 当前生产主机是 Mac；N100 仅是历史/恢复边界。默认使用 Darwin/zsh，不使用历史 WSL 命令。
- 任何生产判断先运行 `scripts/ops/weather_production_manifest.py --strict`，并以 `src/strategies/runtime/production.yaml`、manifest、PID/process、raw runtime 和 exchange evidence 收口。
- canonical physical DB 是 `/Volumes/jrs/pm_agents/runtime/weather.db`；仓库 `runtime/weather.db` 只有在 manifest 证明同 device/inode 且无相关 critical 时才可读。
- 生产 release 以 `production.yaml.production_releases` 的 checkout + full SHA 为唯一 identity；branch 名、worktree 干净或 session 存在都不能替代 loaded SHA。
- 所有 `/Volumes/jrs` 常驻进程只通过 canonical `tmux -L weather-data-feed-jrs` 上下文；session 存在不代表 TCC/JRS 权限健康，必须做目标上下文 read/write probe。
- canonical tmux 禁止 `run-shell`。JRS/TCC、DB split、collector 中断等复发问题先查历史窗口，只有满足真实维护窗口验收才能称为永久解决。
- Dashboard/API、canonical refresh、weather proxy 和可靠性监督均服从现有 controller/manifest，不创建第二套 owner、scheduler 或 desired state。

## 5. 分析与研究最低口径

- 发布 `live_real` PnL/ROI 前必须通过 `weather_clob_fill_coverage_gate.py`，并区分 submitted、posted、actual fill cost、open cost 与 realized PnL。
- 现金流按 `fill_date_bj`；未结算只报带估值时间的 MTM；不绕过 canonical 表自算 fill PnL 或成交质量。
- 研究先锁定一个假设、固定分母和 target metric；分开报告 signal funnel 与 evidence funnel，并使用同分母 market baseline、PIT 和 frozen forward。
- exact bracket 不是 touch market；快源跨档不是 settlement truth；所有源先保留 raw unit/value，再映射到 settlement native lattice。
- 同一机制的城市、日期、窗口和参数变体复用 runner，以 config/run manifest 区分。机器明细进 artifact root，仓库只留紧凑 metadata、结论和指针。

## 6. 按需文档入口

- 文档权威性与完整索引：`docs/WEATHER_DOCS_INDEX.md`
- 策略当前状态：`docs/WEATHER_STRATEGY_REGISTRY.md`
- 生产接手：`docs/WEATHER_STRATEGY_ENTRYPOINT.md`
- 系统与字段合同：`docs/WEATHER_SYSTEM_CONTRACT.md`
- 量化设计：`docs/WEATHER_STRATEGY_QUANT_DESIGN.md`
- 数据源与流水线：`docs/WEATHER_DATA_CANONICAL_SOURCES.md`、`docs/WEATHER_DATA_PIPELINE.md`
- 运维：`docs/OPS_RUNBOOK.md`
- 迁移前完整上下文快照：`docs/CLAUDE_CONTEXT_REFERENCE.md`

## 7. 交付

- 默认中文，先给结论与动作，再给必要证据。完成动作型请求后逐条对账：每项附执行证据，或明确说明未做及阻塞点。
- 完整编码任务在交付前必须先起一个独立 subagent 对本次新增/修改代码做一轮只读 review（边界：owned scope 内的正确性、边界条件、幂等/append-only 语义、测试覆盖缺口、明显性能与可读性问题），主 agent 按 findings 修复并优化后复跑全部相关测试；review prompt、findings、修复清单与复跑结果并入交付报告或 evidence seal。保持单个 reviewer 单轮结束，不做 reviewer 级联，review worker 不直接改代码。
- 不把“读了哪些文件”、计划、机制解释或新增文档当作动作完成。不要在结尾机械追问是否继续；没有真正决策岔路就做完再交。
