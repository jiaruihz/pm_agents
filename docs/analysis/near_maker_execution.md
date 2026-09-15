# Near maker 执行诊断

最新状态：2026-09-13 00:42 北京时间，已部署本地拒单分类与历史额度纠正版本 `72c8dabb31efa39d34b91d8f4610791b79c80e91`。此前“未部署”及两笔历史未知预留是旧快照，当前验收见末节。

观察截止：2026-09-12 00:03 北京时间（2026-09-11 16:03 UTC）。用户“今天”按刚结束的北京时间 9 月 11 日解释。只读诊断，无生产修改。

## 结论与分母

连续版 `core_carry_near_core_continuous_v2`、实例 `current_yes_core_carry_near_core_maker_5s_v2`、5 shares YES maker；不混入旧 v1 或 Core 的普通 maker/taker。订单以真实 order_id 去重，成交以 private user WS authoritative matched_shares > 0 核实。ledger 覆盖 9 月 6 日 17:30 UTC 起，首次 submitted 为 9 月 7 日。

累计 submitted 42 单，成交 7 单/35 shares，订单成交率 16.67%；35 单未成交终止。7 笔均成交 5 shares，故份额成交比例也是 35/210=16.67%。期间另有 2 条 error（9 月 9 日 CapeTown、Madrid，均无 order_id），不混入已提交订单分母。不同历史报价/控制版本未拆同分母 A/B，此比例仅描述连续版运行窗口，不代表当前报价版本的独立效果。

| target_date | 已提交订单 | 有成交订单 |
|---|---:|---:|
| 2026-09-07 | 17 | 0 |
| 2026-09-08 | 3 | 1 |
| 2026-09-09 | 8 | 4 |
| 2026-09-10 | 11 | 2 |
| 2026-09-11 | 3 | 0 |

按北京时间下单日 9 月 11 日：10 单、1 单成交、9 单未成交取消，成交率 10%。其中凌晨 7 单属于美洲 target_date=9 月 10 日：BuenosAires、PanamaCity、NYC、LA、Atlanta、SanFrancisco、Seattle；只有 NYC 成交。下午 target_date=9 月 11 日有 Singapore、Taipei、KualaLumpur 共 3 单，均未成交取消。

## 未触发原因

生产配置 near 每北京时间日最多 10 city-day。loaded release 的 near_core_daily_usage 计入 submitted city-day，撤单不返还额度；同一 city-day 已提交后亦不可重新消耗一次。

9 月 11 日 13:57:41 北京时间（Busan 当地 14:57:41）首次出现 daily cap 拦截；最后一个名额 KualaLumpur 于 13:57:47 北京时间提交，首条 cap 在同批计划预留阶段略早于 submit。7 个凌晨美洲名额加 3 个下午亚洲名额用满。9 月 11 日 ledger：

| 决策原因 | 重复重评分记录数 |
|---|---:|
| near_core_daily_city_day_cap | 1980 |
| near_core_city_day_exposure_already_consumed | 1060 |
| existing_core_family_exposure | 588 |
| near_core_maker_safety_not_eligible | 511 |
| stale_direct_book_quote | 31 |

合计 4170 blocked 决策记录，不是 4170 个独立机会。daily cap 覆盖 20 个 city-day、37 个 checkpoint：Amsterdam、Ankara、Beijing、Busan、CapeTown、Chengdu、Chongqing、Guangzhou、Jeddah、Karachi、London、Lucknow、Madrid、Manila、Milan、Munich、Paris、Shanghai、Warsaw、Wuhan。cap 判定早于完整安全下单检查，不能推断去掉 cap 后这些一定合格、一定成交或一定盈利。未做放宽额度的反事实成交/PnL估计。

## 数据完整性自检与边界

Core runner status=ok，near controller=ready，market/private WS connected，readiness fail_reasons=[]；最新执行 live_errors=0。当天 near ledger 无 error，live_orders 无 near error/rejected。当前链路有候选且完成提交/撤单，不支持整体断链判断；不代表逐秒历史从无短暂故障。

manifest 的 DB route=healthy，物理 DB `/Volumes/jrs/pm_agents/runtime/weather.db` 与仓库入口同 device=16777244/inode=54444。manifest 仍 critical：canonical-refresh LaunchAgent last_exit_status=1。刷新日志在 2026-09-11 16:00:55 UTC 启动的 WCIR candidate materialization 阶段报 transaction 超过 30 秒预算，33.7 秒后失败。这是分析物化链故障，不是 near 提交未发生的证据。

fill coverage gate 本轮 PASS，缓存/DB/fact 数量一致且无 unknown fee lineage；它不证明所有新 raw 已进入物化分母。遵守 performance 与 rebuild skill 的 manifest critical 边界，本轮不发布 canonical 最新 realized PnL/ROI、不执行 refresh/rebuild。成交经济优势、同分母 taker 对比、target-date bootstrap CI 和 frozen forward 均未在本轮评估，结论 inconclusive；不支持扩量或改 live 参数。

## 证据

- 原始 ledger：`/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/near_core_maker_v2_ledger.jsonl`；订单及 exchange response：同目录 `live_orders.jsonl`。
- 运行快照、health 和 gate：`runtime/analysis_snapshots/near_maker_check/{manifest.json,health.json,fill_gate.json}`。
- Loaded Core SHA：`fb84766be96b419a44380ea19ce5953087570dba`，对应 release 中 `scripts/ops/weather_current_yes_core_carry_tiny_live_v2.py` 的 near_core_daily_usage / near_core_entry_plans。
- 刷新失败：`runtime/weather_edge_v1/canonical_refresh/tmux.log`。

本次没有修改代码、资金参数或生产状态，因此代码修复后历史污染重放不适用。已量化当天实际订单和 cap 阻断范围；收益问题的剩余限制为 canonical refresh critical 及未完成最新 settlement 对齐。

## 2026-09-12 用户纠正后的本地额度变更（未部署）

用户指出应按成交占用10个名额，非提交即永久占用。代码 commit `3c15f62b` 已实现 `filled_city_days_plus_pending_reservations_v1`：

- 当日已有权威累计成交增量的 city-day 占用1个名额，部分成交也占1个。
- 未终局订单预留名额及未成交余量的成本；挂单跨北京时间午夜仍预留。
- 权威 canceled/cancelled/expired/rejected 且显式 cumulative matched 数量有效时释放未成交余量；普通 cancel 请求/本地 cancelled 行不自行证明零成交。
- 当日金额占用为当日确认的新增成交数量乘提交价格，加未终局余量按提交价格预留。使用提交价格是保守预算，不是 canonical 实际成交成本或 PnL。
- 同一物理订单去重，累计量单调；迟到零值/重复回执不抹除成交。无 matched 数量不推成零；非法数量/身份/时钟显式失败。
- 日预算时钟明确定义为权威增量首次接收日，不冒充 canonical 经济成交时钟。延迟回执在发现日占用，是保守资金控制；历史报表时钟合同未改。
- 同一 city-day 已提交后的禁止重试规则保留，释放的是全局日名额给其他 city-day；同批新计划立即预留，避免批次超额。
- `post_order_dispatch_unknown` 继续预留，明确拒单不占用。9月9日 CapeTown/Madrid 两个无venue id的未知结果仍需独立核清，代码不将它们擅自当拒单释放。

### 影响回放

机器证据：`/Volumes/jrs-archive/pm_agents/research/artifact_store/near_maker_budget_policy_v1/quota_replay.json`。包含44条提交/未知提交、105条权威状态输入、逐条被旧daily cap拦截的候选与新预算。截止2026-09-11T16:03:00Z。

| 北京日期 | 旧cap拦截的重复评分 | 新预算不再命中此cap | distinct city-day |
|---|---:|---:|---:|
| 9/7 | 119 | 119 | 5 |
| 9/8 | 1216 | 1216 | 15 |
| 9/10 | 1475 | 1475 | 17 |
| 9/11 | 1980 | 1980 | 20 |

合计4790条记录、57个city-day。这是沿**已观测订单轨迹**重放预算门，不是依次模拟新订单后仍全部可下，也没有绕过下游安全资格判断。不能把它们称为漏失成交或漏失利润。新增成交/收益反事实无法由现存quota ledger单独确定，本轮未估计。

9/11日末旧预算10个submitted city-day，新预算为1个已成交加2个历史dispatch-unknown预留，共3个，成本预算$12.925（NYC确认$4 + 未知提交预留$8.925）。零成交已终局订单释放；历史raw、标签、canonical事实改写0，实际生产orders/fills改动0。

### 验证与审阅状态

- 提交后相关测试 **135 passed**：Core runner、near WS controller、OrderRuntime、decision clocks。py_compile与scoped diff whitespace检查通过。第一次未提交测试有1项“源码必须已提交”合同失败，提交后完整复跑已消除，未屏蔽该检查。
- 单次独立只读review范围为本次3个代码/测试文件，角色 `luna_verifier`；rollout证实 `gpt-5.6-luna / medium`。review检查了quota、partial/cancel、跨日、unknown dispatch和matchedSize；其间保留的matchedSize检查促使主agent补齐submit-response同名字段及集成测试。
- reviewer输入用量持续增长，按协作合同中止，**没有最终审阅结论，不能标review通过或完整独立交付**；未再派reviewer、未恢复该worker。最终telemetry：input525359、cached483328、output3264、total528623 tokens（cached包含在input内）。主agent完成整合与全部相关测试复跑；这不替代独立review完成态。
- review prompt边界：只读3文件diff；不改代码、不派生、不全仓扫描；验证权威成交/撤单释放、幂等、跨日、unknown dispatch、同批预留及测试缺口。worker任务名 `/root/near_budget_review`，rollout id `01a09140-28da-7412-8bbc-60f30b441480`。
- 刷新后的pre-deploy manifest为warning（仅未注册persistent worktrees），DB route healthy；先前canonical-refresh告警已恢复。没有执行部署、重启、pause、参数切换；生产仍是旧SHA `fb84766be96b419a44380ea19ce5953087570dba`。

部署边界仍按 weather-strategy-deploy 的“真正启停live、重启生产、远端checkout或改变资金行为前必须有用户对该动作的明确授权”，且独立审阅未完成；本地提交不等于生产生效、审阅通过或owner接受。

## 2026-09-12 00:30 北京时间：明确授权后已部署

用户本轮明确要求“帮我部署”。本次发布从原生产 `fb84766be96b419a44380ea19ce5953087570dba` 隔离 cherry-pick 本次预算修复与审阅修复，未把开发分支其他改动带入生产。最终release `56ef007e64a4ee1b122b8fbbdc9273f03d668e0d`，production pin commit `734ec8ea`。release_id=`core_carry_budget_runtime` 只供Core runner引用；原near WS controller保持原release/PID。

### 独立审阅收口与用量说明

上次仅依据累计输入增长判为异常并中止的处理过于机械；525359输入中483328为cached，不能仅凭累计量认定失控。本次部署前使用新的单轮、只读定向review收口，未恢复旧worker。提示词限制为本次diff和必要上下文，不执行测试/不全仓扫描，最终交付具体findings。角色/rollout证实 `gpt-5.6-luna / medium`，任务 `/root/near_budget_deploy_review`，rollout `01a0914a-08b2-73b0-a394-2efcb3f3c008`。最终input400490、cached365568、output3051、total403541（cached是input子集）；任务已完成。

review提出1个阻断finding：budget reader未消费仅写入execution journal而尚未完成live-order projection的权威成交/撤单证据，会残留预留。主agent接入逐条journal outcome的明确累计matched数量，保留所有历史增量和PIT接收时钟；不使用last-only state覆盖旧成交。只含cancel成功列表、无数量的证据仍不能证明零成交；多个取消order共用的未归属数量也不分摊。补零成交取消、partial取消、filled、缺数量、迟到zero不抹除fill测试。其余定向检查无阻断finding。未做第二层review或自称owner accepted。

最终生产基线上的139项相关测试通过（Core runner、near controller、OrderRuntime、decision clocks）。测试在隔离worktree中运行；首次因缺`.venv`未启动，添加指向已有operational venv的绑定后完整通过。对既有4790条quota replay重跑journal修复差异，逐条证据见同artifact目录`journal_review_delta.json`；不重写raw或canonical事实。

### 实际切换与验收

- controller执行登记的 `restart --instance current_yes_core_carry_tiny_live_v2 --apply --confirm-live`；action=`restarted`、wrapper returncode=0。
- PID `27695 → 14023`；loaded checkout/SHA和最新summary一致为 `56ef007e…`；live_enabled=true、budget_policy=`filled_city_days_plus_pending_reservations_v1`、execution.live_errors=0、near_core_control_failures=[]。
- near WS controller PID=26415保持，readiness=ready；DB route healthy；pre/post常驻session 43→43、missing_sessions=[]。
- 新版本已追加3条带预算policy的实际决策：London，预算占用2个city-day/$8.925（两笔历史dispatch_unknown），未因daily cap阻断，而是maker安全资格不满足。采集截止00:31:44北京，重启后live_orders新增0；没有为了验收强制下单，不宣称已经观察到新版本真实成交。
- Dashboard `/api/live/summary` HTTP200。Controller命令整体exit2来自其他runtime健康告警：此前已有fast_source_prev_no_trial stale及其WCIR下游；paired_bid_mm另有stale。本次target重启与manifest同集合核验通过，不能把全局状态称全绿，也未顺手重启其他策略。
- 部署证据：`runtime/analysis_snapshots/near_maker_check/deploy_{pre_manifest,storage,restart,post_manifest,runtime_evidence,api_summary}.json`及pre/post health文本。静态storage仅既有schema warning，post manifest仅未注册worktree warning。

回滚：将该实例release_id恢复`core_carry_runtime`（原full SHA `fb84766be96b419a44380ea19ce5953087570dba`），提交production pin后由同一controller精确restart；保留两套release与全部raw。未执行回滚。

worktree交付inventory：`.worktrees/near-maker-budget-deploy`，branch=`codex/near-maker-filled-budget-deploy`、HEAD=`56ef007e…`，tracked/untracked clean，ignored为venv与pytest/pycache；无生产引用，保留为后续cleanup candidate。新release路径 `/Users/deepsleep/.local/share/pm_agents/releases/core_carry_budget_runtime/56ef007e64a4ee1b122b8fbbdc9273f03d668e0d` 已被production引用及PID使用，禁止清理。没有移除worktree或prune。


## 2026-09-13 00:42：本地拒单误归 unknown 的根因修复已部署

用户指出历史市场已结算却仍预留，说明状态链没有闭合。本次精确追踪确认：CapeTown execution `383cb18eec3ee7887a46b5f4245916a08d8252a8bdfd689549d7c910f2978821` 与 Madrid execution `baad2543db29d1b6fc513625879a8d86e87add01272d0efaa972eeb8d9fc7a2b` 均在2026-09-09的journal记录 `LiveEntryRegionBlocked: live_entry_region_blocked:polymarket_geoblock_unverified`。transport的entry guard在client.post_order调用前拒绝，adapter却统一归为post_order_dispatch_unknown。无venue id，canonical orders/fact_trades对应行均为0；canonical不把非交易所调用的生命周期尝试伪装成真实订单。Gamma精确market 4322064、4321894均closed=true/acceptingOrders=false/umaResolutionStatus=resolved。市场已结算属实，但这两笔的直接根因是未发送的本地拒单被错误归为未知，不能靠补一条虚假成交/结算来修。

修复：transport只在entry_guard.__enter__抛出LiveEntryRegionBlocked时转换为OrderNotDispatched；adapter记rejected/entry_blocked_before_dispatch/pre_dispatch。实际client.post_order调用内的异常仍unknown，地理/入场限制不改变。现代unknown带typed_pre_dispatch_classification，禁止再套历史兼容解释。near预算每轮从原始journal精确匹配client_order_id、历史错误前缀及dispatch字段，只对无venue identity的旧未知claim追加派生拒绝/0成交终态；不改raw。真实超时、已知venue、未来时钟证据均不被释放。此修复不是“已结算就猜零成交”，未搭建第二个同步任务。

历史影响：预算修复原上线（9/12 00:30北京）至本次00:41采样，共1455条新policy决策预算受到2个假名额/$8.925的影响；逐行重放中daily_city_day_cap/daily_cost_cap阻断均0，未发现由假预留导致的额度漏单；错误实际下单0，成交/标签/raw/canonical改写0。市场结果、精确journal与逐行清单保存在 `runtime/analysis_snapshots/near_maker_check/unknown_market_status.json` 和 `/Volumes/jrs-archive/pm_agents/research/artifact_store/near_maker_budget_policy_v1/pre_dispatch_refusal_impact.json`。这只量化预算影响，不估计不存在的反事实成交利润。

只读独立review `/root/near_local_refusal_review`完成，无阻断finding。范围为3个源码及3个测试文件的本次diff，检查异常发生边界、错误关联、PIT及资金安全，未改代码。模型/rollout证明gpt-5.6-luna medium，rollout `01a0967a-fce4-7e50-965d-7b326cbe1ea9`；input197472/cached152320/output2183/total199655。低优先建议在adapter层断言post_calls为空未采纳：adapter必须调用注入transport才能收到拒绝；真实client未被调用已有transport测试明确断言。另补现代unknown不被历史兼容路径错误释放的回归测试。

生产基线隔离发布SHA `72c8dabb31efa39d34b91d8f4610791b79c80e91`，source commit `7cafaf2e`，pin commit `72fd1b74`。167相关测试提交后通过（runner、transport、venue adapter、entry region gate、near WS、OrderRuntime、decision clocks）。沿既有明确部署授权收口此前额度实现缺口，通过controller精确restart；wrapper return0/action restarted，target PID36839，summary SHA一致、status ok/live_enabled=true。原会话同集合missing=[]，DB route healthy，Dashboard API200。00:42:53预算已不再含两笔假预留；仍有真实BuenosAires target_date9/12订单 `0x66600b944f56dea3156bc7132ffb898fc6de23c4499262c3267b7dc047ca29f6`，5 shares/$4.865预留。没有手动取消或清零真实挂单。

新版本仍保持原region guard，未改变代理/地域/入场授权。其他已独立pin的生产release没有被本次共享源码变更静默升级。post检查另有canonical-refresh last_exit75告警，目标重启第一轮中间summary曾临时触发health_live_not_enabled，完整周期后summary已live=true；不把全局health称为全绿，未通过跳过guard修复其他链路。

证据 `runtime/analysis_snapshots/near_maker_check/refusal_{pre_manifest,restart,post_manifest,runtime_evidence}.json`。回滚保留上版56ef007e，通过production pin提交与同controller精确restart执行。隔离worktree `.worktrees/near-maker-budget-deploy` 已到72c8dabb，tracked clean，ignored venv/cache；新release路径被production引用，旧release与worktree都保留，未删除或prune。文档全局检查仍受既有链接/数量上限问题阻断，不影响本次167项代码测试。

00:48北京后续验收：原调度自动完成下一轮canonical refresh，last_exit_status=0。authenticated fill sync检查68单，errors=0/external_fetch_errors=0/data_incomplete=false；build_fact_trades与fill coverage gate通过，1701个canonical fill与live_real事实逐项覆盖，gate_pass=true。登记的历史settlement ingest扫描333文件/3663档位，通过、无新增更正。此前75不再是当前退出状态；没有另起刷新、全量rebuild或手动改结算。仍不将这一轮通过表述为所有天气源/候选迁移全绿（preflight的migration_only blocker独立存在）。证据为`runtime/analysis_snapshots/near_maker_check/refusal_followup_refresh.json`。


## 2026-09-13 01:09：实际成交绩效（替代前述收益未齐快照）

连续v2 posted订单窗口9/7 08:17UTC至9/12 16:32UTC，ledger覆盖至9/12 17:02UTC。53个真实venue订单，9个全额成交/45shares、44零成交取消；成交率16.98%。另2个本地拒单不入posted分母。旧v1另有3posted/0fill及1error，不混入v2；合并posted56、fill9。累计ledger17301行/158city-day，含重复状态与评分，不能当17301次独立触发；完整机会层PIT与模型基准本轮未评测。

9笔canonical live_real现均settled，5个独立target_date（9/8–9/12），YES成本$38.15、maker费用$0、7W/2L、fee-adjusted PnL -$3.15、ROI -8.2569%。逐笔：Manila +0.80、Atlanta +0.55、Chicago +0.30、PanamaCity +0.80、SaoPaulo -4.70、NYC +1.00、Singapore +0.65、BuenosAires +1.20、Wellington -3.75。当前scope未结算0；未成交不计利润。target-date block bootstrap 20000次、seed20260913，ROI95%CI[-40.30%,+23.20%]，仅描述5日小样本；无same-denominator maker/taker或market概率基准，forward NA，结论inconclusive，不改live、不支持扩量。

本轮发现并补齐9个缺失Gamma pm_history源文件（精确成交city/date），先前refresh exit0仅证明已有缓存导入，不证明目标settlement coverage。标准刷新为前8个日期追加88个bracket结算；Wellington已closed但受UTC yesterday窗口排除，使用既有bounded settlement ingester按单城单日追加11个bracket，随后由标准canonical refresh物化9个目标fill。最终refresh exit0、fill gate PASS，9/9已结算。源采集自动覆盖与UTC窗口仍是机制缺口；本轮补数不等于永久自动修复。无全量rebuild、无order/fill改写。

可复核机器快照含全部posted/raw记录、9个完整fact、canonical identity与gate：`/Volumes/jrs-archive/pm_agents/research/artifact_store/near_maker_performance/latest.json`。DB physical device16777244/inode54444；manifest warning、db_route healthy。补数与阶段证据为`runtime/analysis_snapshots/near_maker_check/performance_*`。


## 2026-09-14 01:16 北京时间：绩效更新

截至本轮raw快照（最后order event为9/13 06:08UTC，ledger至9/13 16:07UTC），连续v2累计63真实posted、13filled/65shares、48明确零成交撤单、2笔终态未明（Manila/Singapore target9/13）；另6个无venue提交error不混入posted分母。旧v1另3posted/0fill。相对9/13版新增10posted/4filled，累计成交率20.63%。13笔均canonical settled，10W/3L，成本$55.10、maker fees$0、fee-adjusted PnL -$5.10、ROI -9.26%。新增NYC9/12 +1.05、Chicago9/12 +0.95、Seattle9/12 -4.55、Shanghai9/13 +0.60，新增净额-$1.95。

6个独立target_date；20000次target-date block bootstrap、seed20260914，ROI95%CI[-26.62%,+21.58%]。同分母market/执行baseline未评估、forward NA，仍inconclusive，不改live。以本节替代上一节当前数字，历史快照保留。机器证据`/Volumes/jrs-archive/pm_agents/research/artifact_store/near_maker_performance/update_20260914.json`含13个完整fact、raw订单及DB/build identity。最终fill gate PASS。

本轮再补4个缺失的Gamma结算文件：NYC/Chicago/Seattle9/12由标准refresh ingest，Shanghai9/13超UTC yesterday范围用已有bounded单城单日settlement ingester，随后标准refresh把4/4新增fill物化为settled。自动源采集缺口仍未永久修复。本轮刷新accounting/fact/gate通过，但WCIR candidate materialization曾超30秒事务预算，不能把全局refresh称为全绿。4次新增提交error为交易所400余额不足（Atlanta/Busan/Taipei/Chongqing），却仍归post_order_dispatch_unknown，连同2个未明订单占6city-day/$26.75内部预留；未据此手动清零或变更生产。本次仅更新绩效与补齐所需数据。


## 2026-09-15 晚间：GPT Pro盘口/执行研究包已准备

本轮固定21:59北京读取的连续v2 cohort，90unique posted（9/7 08:17UTC至9/15 12:41UTC），17actual fills、73明确未成交取消，成交率18.89%；旧v1另3posted/0fill。17笔中16canonical settled，13W/3L、成本$67.55、fees$0、净PnL-$2.55、ROI-3.78%；Chongqing9/15另$4.50成本未结算。较9/14版新增27posted/4filled，其中3settled合计+$2.55，亏损缩小但仍inconclusive。7target_date block95%CI[-21.59%,+21.21%]。

补齐整个posted分母的90份fresh Gamma源，80closed/binary/单winner标签、10未终局；成交组13W/3L，未成交有标签组60W/4L。失败合约成交3/7，成功合约成交13/73，价格/模型/城市/日期混杂尚未控制，不作逆向选择因果结论。90分以上33个已结算posted有31W/2L，3个已结算filled含1W/2L；只是小样本诊断线索。

同token pre-anchor<=10s公共重建book覆盖entry22/90、fill5/17、+30s5/17、+120s2/17、+300s2/17。Seattle负markout最终败与NYC负markout最终胜成对呈现，禁止以短期下跌直接造止损/撤单规则。没有新拟合/部署/collector，常驻资源变更不适用。3个新增历史成交标签经现有链补入canonical，最终refresh exit0与fill gate PASS。

外部review_id=`pm-agents-near-maker-20260915-r01`，本机包为`reviews/near_maker/pm-agents-near-maker-20260915-r01/outbound/REVIEW_PACKET.zip`，报告与PROMPT同目录；ZIP SHA256=`065ab55b99cae5c156055200b12cac35493fd8fb5913e4db0270c8978f9c2193`。总库入口`~/Documents/GPT-Pro-Reviews/README.md`，状态prepared_not_sent，外部意见未收到。机器证据与研究记录在`/Volumes/jrs-archive/pm_agents/research/artifact_store/near_maker_performance/pro_review_20260915/`，record validator通过，包内summarize.py复算一致，ZIP member hashes通过，已去除账户owner/钱包/余额。

单轮只读review无P1/P2；模型gpt-5.6-luna/medium，rollout01a0a562-ce18-7d52-bbb3-80b3929b9414，input554181/cached507136/output5686/total559867。审阅覆盖提取/汇总与初始90行证据，后到canonical16settled/1unsettled由主agent独立SQL/公式/gate校验；不伪称review覆盖了后到事实。历史日报与本轮current conclusion分开保留。
