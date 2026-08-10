# PM Agent — 项目上下文（Codex）

> 工作风格见全局 `~/.codex/AGENTS.md`（做完再交 / 缺数据自己补 / 先修根因 / 信息够了就动手 / 输出给人看）。
> 本文件与 `CLAUDE.md` 应保持核心规范一致（两份共享主体，改一处同步另一处）。
> 历史 WSL 宿主命令约定（`wsl -d Ubuntu-24.04 -- ...`）已退场，需要时查 git 历史；当前机器是 Mac。

## 0. 当前主线

天气温度策略。**README 描述的是旧 PMM/ARB 框架，已不是活跃主线，别被它误导。**

当前研究姿态（**口径以 [STRATEGY_REGISTRY](docs/WEATHER_STRATEGY_REGISTRY.md) + 最新 reset/freeze report 为准，
不信本节硬编码日期**）：没有一条达到 confirmed（三门全过）、可真实扩 live 的 alpha。主研究是全量、连续的
`P(outcome)-market` residual——模型/物理特征先在同分母 PIT probability score 上 forward 打败 market，再谈
fee-adjusted 表达。快源只按 source→official/settlement→book 的 first-seen 链积累 collector/shadow 证据；
少量事件、运行中的 probe、历史正 ROI 都不自动升级为 live。

**研究状态与生产状态分开**：registry/freeze 只回答 alpha 是否确认，不能证明当前进程是否真实下单；
任何 `live/shadow/zero-notional` 判断以 production manifest、进程参数、raw order 与 exchange response 为准，
不得在系统提示词里硬编码某实例“当前未授权/当前 live”。

## 1. 系统主轴：一条量化血缘，所有工作都挂上去

本项目不是一堆独立脚本，是一个有完整血缘的量化系统：

```text
EventEnvelope → DecisionContext → ModelOutput → SignalCandidate → TradeIntent
→ shared execution handoff → plan → order → fill → settlement
```

canonical 事实表：`fact_signal_candidates`（机会粒度）、`fact_trades`（成交粒度），
由 `weather_dashboard/legacy_migration/*` 从当前 Mac raw、历史 N100 镜像和 canonical fill/fee 调整层重建到
`runtime/weather.db`。DB 是分析派生层；当前进程/订单状态仍以对应 Mac raw runtime 与 exchange evidence 为准。

这条血缘（含 `order → fill → live/shadow 对比 → PnL → strategy_config 参数 → 看板`）是**基础设施，与具体策略无关**：
换策略方向只动上层信号/特征，不重做这条链。暂时不用的策略/底表是 dormant（保留备用），不是 dead，不归档不删。

**做任何新分析 / 脚本 / 特征 / 看板前，先定位它在血缘哪一层：**
- 读数据 → 从 canonical 表读，不绕过去自算 fill / PnL / 漏单 / 滑点。
- 产新信号 / 特征 → 挂进 `fact_signal_candidates` 机会粒度，不另建并行的一次性表。
- 同一研究机制的不同日期、城市、训练窗和参数 → 复用一个 runner，以 config/run manifest 区分；不得复制成
  `research_<city>_*_vN.py`。只有算法或数据合同真正不同才新增入口，并优先抽公共 helper/adapter。
- 不确定结构往哪挂 → 先读 `WEATHER_STRATEGY_QUANT_DESIGN.md`，别先写脚本。

完整设计 [WEATHER_STRATEGY_QUANT_DESIGN.md](docs/WEATHER_STRATEGY_QUANT_DESIGN.md) ·
字段契约 [WEATHER_SYSTEM_CONTRACT.md](docs/WEATHER_SYSTEM_CONTRACT.md)

## 2. 模块边界与机器（细节见 [WEATHER_REPO_BOUNDARY.md](docs/WEATHER_REPO_BOUNDARY.md) · [WEATHER_DATA_FEED_MODULE.md](docs/WEATHER_DATA_FEED_MODULE.md)）

三个模块边界（不是按机器分，是按职责分）：
- **数据层 = `weather_data_feed/` 包**（本仓库；N100 vendoring 仅是历史部署形态）：标准化城市日历 / source profile / 官方观测 / forecast / snapshot 协议。**新的共享数据逻辑只进这个包，别再长在 strategy 目录下。**
  forecast 是**每城固定模型**（`CITY_MODEL`：31 城 ECMWF / 49 城 GFS，按城市历史误差选定），全部 train 证据基于此口径；
  模型 fallback 必须显式告警——7/02-05 曾因 Mac cache 缺 `ecmwf_v4_*` 静默 fallback GFS 污染三天信号（见
  [heada-review-work-order-v1](docs/analysis/2026-07/2026-07-05-heada-review-work-order-v1.md) P0），别再让它静默。
- **采集 = `weather_data_feed_service` / 历史 `weather-predict`**：调用 `weather_data_feed` 生产 snapshot/cache，**不含策略 / 下单**；当前生产实例在 Mac。天气、raw 盘口和消费视图是三个边界：`weather_forecast_curve_collector_v1` 独占 Open-Meteo/forecast 网络刷新；`weather_market_books` 独占 Gamma/CLOB market discovery 与 book 请求，先按 live token hot priority 再补全 event ladder，写 `market_books` 单一 raw 正本；`weather_data_feed_jrs` 只把已落盘 forecast/observation 与 `market_books/latest.json` join 成 `strategy_snapshots`。完整市场分布另物化为 `market_ladder_snapshots`。forecast/METAR 缺失不得阻塞 raw 盘口落盘，任何消费视图不得重新请求同一盘口；`targeted_output` / `full_ladder_output` 是只读历史目录，生产入口、同步和策略均不得继续写入或依赖。三者都由 production controller 管理。
- **执行 = `pm_agent` strategy runners**：消费标准数据 → signal → plan → CLOB order → fill；当前实例在 Mac，N100 只保留历史/恢复边界。实例是否 live 必须从进程参数、pause/state、raw order 和 exchange response 动态核对，不能从旧文档标签推断。

机器：
- **短期生产主机 = Mac**。`/Users/deepsleep/projects/pm_agents` 是控制/开发仓库，业务进程实际 checkout 必须从 `production.yaml`、manifest 与 PID 动态确认；不要把控制仓库路径误当成所有 live 进程的加载路径。当前 JRS runtime 为 `/Volumes/jrs/weather_data_feed_service_runtime`（旧路径 `/Users/deepsleep/projects/weather_data_feed_service_runtime` 是 symlink；2026-07-04 起事故接管，2026-07-06 数据盘迁到 JRS APFS）。Mac 目前跑 data-feed snapshot/orderbook、dashboard，以及若干 live probe / paper executor / zero-notional shadow；具体清单每次用 `weather_production_ctl.py health/plan` + production manifest + raw order files 动态盘点，不在本文件硬编码。data-feed 因 macOS 对外置卷的 TCC/process-context 限制，当前通过 canonical JRS tmux 承载；这只是当前运行架构，**不是永久权限保证**。默认 `zsh`/Darwin，**不要套 `wsl`**。分析“最新/今天”先读取对应 Mac runtime raw；只有 canonical DB 缺目标窗口时才增量同步。全量重算必须显式同意并使用 `run_stack.sh --rebuild`。
- **生产 identity 先跑 manifest**：`src/strategies/runtime/production.yaml` 只声明期望拓扑；`scripts/ops/weather_production_manifest.py --strict` 用 `ps/lsof/tmux/launchctl/runtime summary` 生成当前事实。`/Volumes/jrs` 是生产热层的兼容 mount contract，必须同时匹配 `production_storage_volume_uuid`（NVMe），不能只凭卷名；物理 canonical DB 为 `/Volumes/jrs/pm_agents/runtime/weather.db`。旧盘只可按 `archive_storage_volume_uuid` 挂到 `/Volumes/jrs-archive`，仓库 `runtime/weather.db` 只是兼容入口，健康时必须与物理 canonical 解析为同一 device/inode。出现 volume UUID 错配、split、非 canonical DB consumer、异常生产 checkout 或失败的 LaunchAgent 时，先处理 P0，不得根据旧文档继续分析、部署或重建。
- **生产代码按 release identity 管理**：`production.yaml.production_releases` 是 checkout root + full `expected_repo_sha` 的唯一映射，业务 runtime 只引用 `release_id`，不得各自重复路径或跟随 branch HEAD。controller 启动 runtime 时通过 `WEATHER_PRODUCTION_CONFIG` 注入控制仓库的当前合同；release checkout 不得把自身旧 `production.yaml` 变成第二份 desired state。部署先提交代码、显式推进 release pin，再由 controller 重载对应 runtime；manifest 的 `production_release_checkout_sha_mismatch` 或 `process_release_sha_mismatch` 均表示 desired/loaded identity 未收口，live 进程命中时按 critical 处理。分支名、checkout 干净或 session 存在都不能替代 loaded SHA 验证。
- **worktree 也有生命周期**：`/Users/deepsleep/projects` 顶层只保留控制仓库和 `production.yaml` 当前登记的生产 checkout；研究、修复、staging worktree 不是长期项目目录。任务收口时必须先提交或对脏文件做 content-addressed snapshot，把 ignored 研究产物用 `weather_research_artifact_ctl.py archive --source-root ...` 迁入 archive，再移除 worktree 并 prune 失效登记；不得因“以后可能有用”保留整棵 checkout，也不得为清目录丢失未提交代码或 raw/canonical 数据。manifest 的 `unregistered_persistent_worktrees` warning 必须归因并收口。
- **存储和写入目标也由 production contract 管**：current raw root、canonical DB、兼容入口、active live order journal 与 health artifact 只能从 `production.yaml`/共享 loader 解析，不在 health、refresh、analysis 脚本维护第二份策略名或路径清单，也不静默 fallback 到 `weather-predict`、N100 mirror 或 repo-local 历史 runtime。NVMe 只承载当前 mutable runtime；大型研究机器产物与历史 archive/backups 以 `production.yaml.research_artifact_root` 和 `archive_storage_root` 为物理正本保留在旧 JRS。热层可为旧硬编码提供指向归档层的只读语义兼容链接，但生产进程不得依赖归档盘存活。仓库 `docs/` 只保留结论、紧凑 metadata、权威 case evidence 与仍被活跃代码消费的显式例外，恢复使用 `scripts/ops/weather_research_artifact_ctl.py restore`。迁移机器产物不等于完成知识整理：每次实验还必须把耐久结论、证据边界和当前动作回写 `WEATHER_DOCS_INDEX.md` 指向的家族 living doc 与 `WEATHER_STRATEGY_REGISTRY.md`；日期报告只作 snapshot，不新增平行“当前结论”。一个 mutable target 只能有一个 owner；`latest.json` 是可覆盖 cache，历史证据用 append-only JSONL 且必须带 `schema_version`、稳定 event/order identity、event/observed/ingested 时钟和 writer/build identity。用 `scripts/ops/weather_storage_identity_audit.py` 区分同 inode alias、历史只读副本和危险的独立可写正本；发现 distinct `weather.db`、共享 live journal 或 schema/shape drift 时先修 P0，不凭文件名猜哪份是真的。
- **代理入口与路由也只有一个 owner**：所有生产调用只经过共享 `weather_market_proxy` 接口；未传 `route_key` 一律走 `default`，只有公共不可逆执行 handoff 可指定 `stable`，不得把路由 profile 下沉到每个策略或逐进程改代理。当前 `default` 是 Clash `7897`，`stable` 是同一 Clash 进程的命名 listener `7896`，其 `PM-STABLE` fallback 由 TAG 本机 `7890` 优先、Allblue 兜底；切上游不重启 consumer。Allblue selector 连续失败时只由 control-plane 有界切换已有候选并做 Gamma/CLOB 后验，失败恢复原节点并写审计。controller 只通过 Clash 既有 Unix socket 读写 route/group，不启用 TCP external controller、不创建第二套 secret、不恢复历史 failover LaunchAgent；所有上游失败时显式降级，禁止静默 direct。
- **JRS 常驻进程只有一个运行上下文**：凡是读取或写入 `/Volumes/jrs` 的 collector、strategy、shadow、monitor、patrol，一律通过 `scripts/ops/weather_jrs_tmux_env.sh` 解析并复用 `tmux -L weather-data-feed-jrs`；不使用默认 tmux、`weather-jrs`、独立常驻 socket、screen、nohup 或让 LaunchAgent 直接承载 JRS 子进程。canonical socket、固定 binary path 和 SHA-256 只证明入口/程序 identity，**不能证明当前 tmux parent 仍有 TCC/JRS 权限**。每次 health、部署和恢复都必须以目标 server 内的 read/write probe、canonical DB 可读、producer freshness 和下游 latest 为事实；“设置里 FDA 为 on”“进程/session 存在”或“昨天 probe 成功”均不算当前健康。公共 helper 使用 tmux `-N` attach-only；只有 controller 的 `recover-jrs-context` 可以创建/recreate canonical server，persistent session mutation 也必须带 controller authority。LaunchAgent 只可请求 bounded one-shot，server 缺失时 fail closed。公共 start/stop 脚本不暴露 socket 或 process-manager/start-mode 开关；新增或修改入口先登记 `production.yaml` 合同并改共享 helper/一致性测试，禁止再逐脚本发明启动上下文或直接调用底层脚本改变生产状态。
- 归档派生产物只可在明确删除授权下用 `weather_research_artifact_ctl.py prune-reproduced` 清理：必须 clean replay SHA 精确一致、无下游消费者，并把 producer/code、完整 replay args、archived inputs 与 mutable runtime identity/inventory 写入 tombstone；raw、canonical 与仅语义近似的结果不得借此删除。
- **Dashboard/API 与 refresh 也服从控制面**：API 是 `production.yaml` 登记的 `weather_dashboard_api`，由 controller 在 canonical JRS context 管理；不得恢复旧 `com.pm-agents.weather-api` LaunchAgent。FE 可由非 JRS LaunchAgent 承载。canonical refresh 是唯一登记的 DB 刷新 one-shot；LaunchAgent 只能请求这个 bounded job，不能直接执行 ingest/rebuild 子链。运行状态看 `runtime/weather_edge_v1/canonical_refresh/{last_exit_status,tmux.log}`，任务仍在运行时不得重复触发。
- **canonical tmux 禁止 `run-shell`**：2026-08-04 的真实 crash report 证明 tmux 3.6b 在 `cmd_run_shell_callback -> cmd_run_shell_print` 发生 `SIGSEGV`，一次 probe/status callback 即可带走整个 server 及全部 session。JRS probe、mkdir、status bridge、prospective-host check 只能走共享 helper 的 checked detached session；新增入口必须由一致性测试扫描 `run-shell`，不能用“只是一条短命令”作为例外。
  当前权限宿主 pin 为 Homebrew Cellar tmux binary；升级时必须先授权新 binary、更新 pin 并在维护窗口重建 canonical server，禁止跟随 symlink 静默切换。但 pin 通过后仍必须做真实 probe。canonical context 失败时先用 `weather_production_ctl.py health/plan` 和 manifest 保存现场；`recover-jrs-context` 只能在已授权维护窗口、prospective-host probe 成功且保存完整 restore manifest 后执行，它不能授予 macOS 权限，也不能绕过锁屏/TCC。普通 LaunchAgent 和业务 start script 不得抢建一个新的无权限 canonical server。
- **历史复发优先于“已修好”叙述**：JRS/TCC、DB split、collector 中断、重复入口等稳定性问题，开始修复前先在 git history、事故治理记录和 runtime logs 中检索同症状历史，列出每次时间窗、当时修了哪一层、为何仍复发。必须区分 `symptom containment`、`entrypoint unification`、`recovery automation` 和 `root-cause elimination`；前面三者不得称为“根治”“永久解决”或“一劳永逸”。
- **永久解决的验收标准**：只有同时具备真实生产证据才能使用上述表述：①旧故障窗口和影响半径已量化；②根因可由复现或系统证据支持，不是从单次恢复反推；③fresh permission host、canonical server death/recreate、锁屏/解锁以及 reboot/login 场景在维护窗口通过；④controller 从保存现场到依赖顺序恢复、process/raw/exchange/API 后验全闭环；⑤测试至少包含不 mock 的 Mac/JRS integration smoke，unit test mock 通过不能替代。未完成时明确写 `contained` 或 `recovery improved`，不得写 `resolved permanently`。
- **N100** `ssh jiarui@192.168.0.200 '<command>'`：7/1 发生 ext4 emergency read-only / IO error 事故后，不再当作当前生产 truth；修复前只作为历史正本和备份抢救对象。恢复 N100 生产前先确认 `smartctl`/备份完整性/服务链路，而不是直接重启 timers。
- 数据层健康用 `scripts/ops/weather_data_feed_prod_health_check.py`；它默认检查 Mac 临时生产 snapshot、orderbook 和 active live order files。

数据流、镜像目录逐条映射、备份 → [WEATHER_DATA_PIPELINE.md](docs/WEATHER_DATA_PIPELINE.md) ·
[WEATHER_DATA_CANONICAL_SOURCES.md](docs/WEATHER_DATA_CANONICAL_SOURCES.md) · [OPS_RUNBOOK.md](docs/OPS_RUNBOOK.md)

## 3. 硬边界与工程姿态

**硬边界（不可逆操作，必须守）**：涉及 N100 live / 私钥 / 余额 / 真实 CLOB 下单 / 删数据 / 远端部署，
保留显式确认、暂停开关、notional 上限、可追溯日志。生产行为变更（city_pools / paper_policy /
execution_policy / live_cycle）走 [weather-strategy-deploy] 的 **git-first** 流程，**不许 `scp`/`rsync` 直推**
——直推绕过版本审计，事故无法回溯。

**工程姿态（个人研究项目，默认直接推进）**：少写与当前目标无关的防御性兜底；**显式失败优于静默 fallback**
（数据缺失 / 字段不一致 / 盘口不可用时报错暴露原因，不偷偷换旧字段旧数据继续跑）；兼容逻辑要标注删除时机。
**但"少兜底" ≠ 绕过资金安全或不可逆操作的硬边界。**

**不要默认加 gate/guard/filter**：排查到问题时，第一反应是修根因、理清模块边界、调整数据流或暂停有问题的采集/策略，
不是给下游再套一层 gate。长期干净方案可以接受短期停数据、停 shadow、停 live 或停旧 runner；不要为了不断流而把临时
限制、兜底和例外堆进系统。只有当 gate 本身就是正确的业务/资金安全边界，或是发布 PnL/实盘动作前的既有硬口径时，才保留
或新增，并且要写清楚它保护的不可逆风险。

**生产参数变更走快路径，不把小改动扩成全面研究**：
- 先用一次精确搜索锁定参数的权威配置、runner default/assert、启动参数、风险/notional cap、artifact entry-cost
  quantity、runtime identity 和直接测试；只读这些命中文件与部署要求的最小文档集。
- 若参数不进入 signal eligibility / 概率 / PIT 时钟，只做执行层修改、定向测试、git-first 单实例重启和
  process/raw/exchange 四项验证，不重跑宽口径研究、不顺手整理无关文档。
- 若 sizing 会改变 full-ladder cost/EV 或深度可执行性，才补同分母容量重放；结果沿用现有研究产物，不在部署环节
  重做已完成分析。
- 同一参数不得长期在 artifact、runner、start script 和 executor limit 多处各自写死。`execution_profile` 应成为
  sizing/refresh/TTL/cap 的单一权威配置；迁移完成前每次参数变更必须做 hardcode scan，并把发现的重复点一次改齐。
- 交付只报：改了什么、发现了什么耦合、测试结果、生产 SHA/PID/健康状态、开放订单；阅读文件清单不当作成果。

## 4. 研究防跑偏

### 研究指令分层（避免每轮重复一整套研究章程）

研究任务按三层写，低层规则不要在每次 prompt 重复或另起版本：

- **本文件**只放永久边界：canonical/PIT/资金安全、血缘、证据和交付标准。
- **`skills/weather-strategy-research/SKILL.md`**只放可复用的研究方法：readiness、固定分母、双漏斗、market baseline、验证与产物路由。
- **单轮 prompt**只引用对应 skill，并填写 `docs/analysis/templates/research.md` 的 brief：一个假设、数据/日期范围、验收门槛、唯一动作；没有这些信息时，先做最小 read-only readiness，而不是重述全套规则。

同一机制的变体必须复用同一 brief/runner，并以 `run_id` 或 manifest 区分；不要把“新城市 / 新日期 / 调一个参数”伪装成新的研究方法或新 prompt 章程。

用户提出的往往是一个很具体的失败模式 / 交易形态。先把问题收敛成一句明确的 **target metric / target slice**，
再跑数据写脚本：先复述目标指标 → 先锁分母（事前持仓形态 vs 事后结算形态，不混用）→ 结论先给交易动作
（保留 / 过滤 / 降 size / shadow / 不改 live）再给证据。**不要擅自把"某个坏场景怎么优化"扩大成"整个分支砍不砍"。**
发现口径漂移立刻暂停纠正，不继续堆结果。

研究结论不要建立在单次随手阈值实验上。发现某个方向点估不好时，先做同分母 A/B、合理微调和必要反事实
（例如固定 label/rows 后比较 source policy、城市池、阈值、forward 窗口），再下结论；但不能为了调参把
forward 失败或非 PIT 数据包装成可 live 的证据。

**禁止把阈值堆叠和数据覆盖混成“策略漏斗”**：报告必须分开列 `signal funnel`（原始机会 → 机制候选 → 首个
city-day signal）与 `evidence funnel`（PIT 盘口覆盖 → settlement 覆盖 → executable expression → fill），并写清每层单位是
event、city-day、expression 还是 fill。盘口/结算缺失只能记为 coverage gap，不能伪装成策略筛除；没有预注册机制或执行依据、
且没有同分母证据支持的价格带 / support count / 多条件 AND，不得作为 eligibility hard gate。若 direct ask 只覆盖到偏晚盘口，
由此产生的高平均入场价只能解释为 archive/collector timing bias，不能解释成“模型必须等到该价格才确认”。样本因 coverage 缩小
时，结论是证据不足并补采集，不是把少量剩余行包装成“精筛策略”。

**“全量历史 / 全部数据 / full history / long history”必须带作用域**：不得用这些词指代未声明过滤条件的数字。
训练或评测产物必须同时写 `denominator_scope`、输入 artifact/表、原始 rows/日期/城市范围，以及按时间、城市、模型、季节、
label、PIT/盘口/settlement 可用性逐层过滤后的 funnel；最终训练集只能称 `training slice`。例如 `best_model + May–Aug`
得到的 5,561 rows 不是“全部历史”，原 artifact 的 42,705 rows 也不等于项目拥有的全部历史数据。所谓“全量”只允许表示
一个明确定义且可核验的 universe（如某公开账户端点的完整可分页窗口），并紧邻写出覆盖起止、来源和已知缺口。

天气策略研究优先从第一性原理构造连续信号，再用切片解释信号，不要把切片当策略本体。比如 no-reheat /
remaining-heat 这类问题，先定义物理目标（剩余时间是否还能打穿当前高点 / bracket）、机制特征（剩余加热能量、
forecast ceiling margin、plateau 可靠性、reheat 机制、观测 cadence/source）和可校准概率，再用市场价格计算 EV。
hard filter 只用于机制边界、资金安全、执行质量或已知无效数据，不用于追着坏例子一条条补洞；否则会把样本切碎成
看似漂亮但永远不能 live 的过拟合规则。

临场天气持仓判断必须先回答 **forecast peak clock / 剩余加热窗口 / 当前路径状态**：预报峰值在几点、决策时刻距离峰值
还有多久或已过多久、当前温度路径是 fresh runway / plateau / pullback / fade 哪一种，再结合云雨风湿度和 source bias
判断未来是否还能打穿当前 bracket。不要把已经发生的下一份 METAR 当成事前判断依据；如果某个温度已经打印出来，
就直接更新该 market 的 YES/NO 胜负状态和剩余风险，不再说“下一份报文很关键”这种事后无交易意义的话。

温度最高值 market 是 **exact bracket**，不是 touch market。`X YES` 只有在最终最高温正好结算为 `X` 时才赢；
如果后面继续升到 `X+1` 或更高，`X YES` 输、`X NO` 赢。持仓判断时必须先按这个语义重估：已经“到过 X”
不等于 `X YES` 安全，反而要重点评估 overshoot 到下一档的风险；不要把“触到当前档”误说成“当前档 YES 锁定”。

快源跨档只更新概率，不等于 settlement truth。相关研究必须同时报告 terminal false cross、source→routine/WU
basis、correct-vs-false executable/fill 分母，并按 order id 回连 canonical fills；完整 Atlanta 反例和口径在
[STRATEGY_REGISTRY](docs/WEATHER_STRATEGY_REGISTRY.md) 的 `metar_cross_prev_no` 行。

所有源先保留 raw unit/value，再映射到 settlement-source native lattice；禁止用摄氏小数直接解释 native-F exact
bracket 距离，也禁止把单一 condition 现金流冒充整个 event。Ankara 逐笔反例与对账见
[casebook](docs/WEATHER_INTRADAY_DECISION_CASEBOOK.md) Case 3。

跨城市细粒度温度模型的公共框架正式名为 **Weather City Intraday Runtime（WCIR）**，稳定标识
`weather_city_intraday_runtime_v1`，策略族为 `weather.city_intraday_probability`。不强制共用算法、特征、训练模块或是否把盘口作为模型输入；城市插件可以实现纯天气、
market-offset 或天气与盘口联合模型，但必须按
[WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md](docs/WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md) 复用采集 profile、
事件/四时钟、PIT checkpoint/replay、`SignalCandidate`、`TradeIntent` 和公共执行链，不得为新城市另建 collector、
回放时钟或 order/fill/PnL 链。研究评测服从
[WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md](docs/WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md)：统一 prediction table、
PIT 时间切分、概率指标、同分母 market baseline 和有盘口时的 fee-adjusted 信号/ROI；城市内部不能自然复用的代码不强行合并。
正式接入前必须拿实际 deployed raw/runtime 做 contract census：区分 point/interval/revision payload，验证业务日期与物理
shard、source/official/expression 三类 bracket anchor、one-sided/cross-day 状态和 producer/consumer code/config/schema hash；
repo 单测通过不能替代 running process sample parity。
Amsterdam、Busan、Helsinki、Seoul、Tokyo 及之后新增的每个城市都必须登记为 WCIR `CaptureProfile + city adapter`；
模型尚未冻结时先以 `coverage-only` adapter 输出结构化 checkpoint blocker，不得伪造概率、bracket、candidate 或 intent，
也不得另建 collector、回放、下单、fill/PnL 旁路。公共 contract 不足时扩展 WCIR，并给既有城市补 regression fixture。

## 5. 分析必走的 skill + 硬口径（细则见 [WEATHER_ANALYSIS_CONTRACT.md](docs/WEATHER_ANALYSIS_CONTRACT.md)）

weather 分析请求先 invoke 对应 skill，别直接写一次性 pandas 脚本：

| 触发 | skill |
|---|---|
| 新策略 / 物理机制 / 概率模型 / 快源 / 特征 / PIT 研究设计 | `weather-strategy-research` |
| 绩效 / PnL / ROI / 胜率 / 切片 / 对比 / 回测结果 | `weather-strategy-performance` |
| 单日血缘 / 逐笔复盘 / 为什么下这单 | `weather-strategy-lineage` |
| 持仓 / 敞口 / 未结算 / 风险 | `weather-strategy-exposure` |
| 余额 / 钱包 / USDC / CLOB 对账 / fill 对不上 | `weather-live-account-reconcile` |
| 部署 / 上线策略 / 城市池 / 参数 / T1/T2 | `weather-strategy-deploy` |
| 补全 / 重建底表 / 同步 / 数据陈旧 / 重新结算 | `weather-fact-rebuild` |
| JRS 写入失败 / runtime 本机接管 / 迁回 / 单一正本恢复 | `weather-jrs-runtime-failover`（同时 invoke `weather-strategy-deploy`） |

发布任何 `live_real` PnL / ROI / 曲线前的硬 gate（踩过坑换来的，不是形式）：
- 先跑 5 行 SQL 自检（数据新鲜度 / trade_class 分布 / 结算 / 机会覆盖 / 订单成交）。
- `.venv/bin/python scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py` 必须 `gate_pass=true`，否则停下先修数据链。
- 现金流 / 余额用 `fill_date_bj`，**不用 `order_date_bj`**（后者受回填污染，只作下单归属诊断）。
- `submitted_notional` / `posted_notional` / `actual_fill_cost` / `open_cost` / `realized_pnl` 分开报；open cost 不是亏损。
- 已结算才报 `pnl_usd_at_fill`；未结算只报 MTM 并附 `val_snapshot_ts_utc`，估值旧就明说旧。
- pm_history near-binary `0.9995/0.0005` 必须归一化为 `1/0`（旧报告需重算）。
- 不读 `runtime/_legacy/*.db` 等退役库；不绕过 `fact_trades` 自算 fill PnL、不绕过 `fact_signal_candidates` 自算成交质量。

## 6. 高频命令

```bash
# 生产状态/恢复（API 与 JRS 进程只有 controller 入口）
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_production_ctl.py plan

# DB/写入目标 identity 与重复审计（只读）
.venv/bin/python scripts/ops/weather_storage_identity_audit.py

# run_stack 无参数仅查状态；全量重建必须显式授权 --rebuild，且不启停服务
scripts/weather_dashboard/run_stack.sh [--status|--rebuild]
#   入口 http://localhost:5173/weather/runs · /weather/live · http://localhost:8000/docs

# 同步当前 Mac 临时生产 market_data（分析"最新/今天"前先跑）
scripts/ops/sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only [--dry-run]

# 同步 N100 镜像（N100 恢复前主要用于历史抢救；--dry-run 预演）
scripts/ops/sync_weather_remote.sh [--dry-run]
```

先跑 production manifest；只有 `db_route.status=healthy`、兼容入口与 physical canonical 为同一 device/inode 且没有 manifest `critical` 时，才读 `runtime/weather.db`（WAL，只读）。全局 `warning` 必须逐项归因：与 DB identity/目标窗口无关的 warning 不阻断只读分析，但不得被忽略或冒充生产完全健康。不要用无界交互式 sqlite，不在只读连接里跑 checkpoint/WAL 修复 PRAGMA。
```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
sqlite3 -batch -cmd ".timeout 1000" runtime/weather.db "SELECT COUNT(*) FROM fact_signal_candidates;"
```
```python
conn = sqlite3.connect("file:runtime/weather.db?mode=ro", uri=True, timeout=1.0)
conn.execute("PRAGMA query_only=ON"); conn.execute("PRAGMA busy_timeout=1000")
```

## 7. 文档入口

完整索引与权威性分级：[WEATHER_DOCS_INDEX.md](docs/WEATHER_DOCS_INDEX.md)（新增 / 归档文档只维护那里）。
最高频：[STRATEGY_ENTRYPOINT](docs/WEATHER_STRATEGY_ENTRYPOINT.md)（实盘接手）·
[STRATEGY_REGISTRY](docs/WEATHER_STRATEGY_REGISTRY.md)（试过哪些策略/状态/血缘归属）·
[STRATEGY_REVIEW_PIPELINE](docs/WEATHER_STRATEGY_REVIEW_PIPELINE.md)（策略跑完怎么复盘）·
[CITY_POOL_DECISIONS](docs/WEATHER_CITY_POOL_DECISIONS.md)（历史决策账，不是当前实例 allowlist） · [ANALYSIS_CONTRACT](docs/WEATHER_ANALYSIS_CONTRACT.md) ·
[DATA_CANONICAL_SOURCES](docs/WEATHER_DATA_CANONICAL_SOURCES.md) · [REPO_BOUNDARY](docs/WEATHER_REPO_BOUNDARY.md) ·
[TMAX_DISTRIBUTION](docs/WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md)。

> 早期那组"盈利模式"**数字**（5 月 BUY_NO 胜率 / Warsaw / ECMWF / LA）是 near-binary 修复前口径，**已作废**——
> 但这是数字作废，不是方向被否（BUY_NO 等是 unconfirmed，不是 disproven）。各策略当前状态/灵感/血缘归属见
> [STRATEGY_REGISTRY](docs/WEATHER_STRATEGY_REGISTRY.md)，口径背景见
> [LIVE_RUN_HISTORY §1.1](docs/WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md)。
