# Near maker 执行诊断

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
