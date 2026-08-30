# Weather-first Market Making：Stage 0A–W2.1 执行总账

Status: `current-source / Selective Maker V2.1 zero-notional ready / profitability INCONCLUSIVE / no-live-change`

Updated: `2026-08-30`

Scope: Weather execution truth、defensive inventory lifecycle、weather-alpha acquisition 的 maker/taker/skip 路由；不包含 W3 连续双边报价或 generic MM live。

Safety: 本轮只完成代码、历史回放和 zero-notional shadow 验收；没有新真实订单、资金划拨、split、merge、redeem、生产重启或 production release。任何真实资金 measurement 仍需独立明确授权。

## 1. 当前结论

路线已经从“恢复传统双边 PMM”收敛为：

```text
Stage 0A venue / fee / accounting truth
→ Stage 0B own-order truth + sole TargetOrderSet reconciler
→ W1 defensive inventory lifecycle
→ W2 V1 narrow maker falsification（历史 seal，保留）
→ W2.1 weather-first selective maker router（当前）
→ 独立授权 micro-live measurement
→ frozen-forward profitability gate
→ W3 continuous two-sided MM（仅在以后有独立证据时再研究）

并行但隔离：
generic market observation / selector
→ 独立资金、库存、PnL 和 admission
```

GPT Pro 的 `ACCEPT_WITH_REQUIRED_CHANGES` 已作为外部咨询意见吸收，而不是执行指令：W1 提前为第一业务阶段；已有 broad maker acquisition 保持 `drop-for-now`；W2 只保留少数预注册 quiet/low-toxicity regime；maker/taker/skip 必须在同一 signal 分母比较。

截至本次 seal：

- Stage 0A/0B 的代码 substrate 和真实历史审计已完成，但 correctness gate 没有闭合。
- W1 五臂历史 fixed-denominator 回放已完成；没有一臂通过 economic promotion gate，真实 exit/merge measurement 为 0。
- W2 V1 历史 seal 保持不改：旧分类器把 28 个 signal 全标为 unknown，actual maker fill 为 0。
- W2.1 修复了“signal 出现后才启动 capture，却要求 signal 前窗口”的不可能依赖。只用严格因果 t+30s action 后恢复 `14/28` 个 transient-dislocation candidates，14/14 都有同钟、同股数 full-depth taker baseline，zero-notional readiness 通过。
- 盈利尚未证明：真实 calibrated transition hazard、passive fill probability、actual own-order fill/non-fill 和 realized incentives 仍为空。当前 profit router 为 `0 maker / 4 taker / 10 skip`；即使假设 hazard=0 且 conservative fill probability=100%，也只有 `10 maker / 1 taker / 3 skip`。该 sensitivity 不是 promotion evidence。
- “完成 W2”表示该阶段已经得到可复现、可证伪的结果，不表示 maker 已盈利或获准 live。

## 2. 三种机制必须分开

| 机制 | 决策 | 收益来源 | 当前 disposition |
|---|---|---|---|
| maker acquisition | 已有 weather signal 上选择 maker/taker/skip | execution price 改善、实际 rebate | broad policy `drop-for-now`；V2.1 `zero-notional ready / profitability inconclusive` |
| defensive inventory lifecycle | 已有仓位选择 exit/fallback/complement+merge/hold | 降退出成本、回收 capital-time、降低 tail | W1 historical counterfactual 完成；仅 passive arm 可继续收集真实 measurement，未晋级 |
| continuous two-sided MM | 持续生成 bid/ask `TargetOrderSet` | trading spread、完整配对、实际 incentives | W3 未开始；不能借 W1/W2 结果提前进入 |

Maker 不创造 weather signal quality。Maker fill 是内生选择；touch、cross、显示深度和静态 queue simulation 都不是 actual fill。

## 3. 唯一经济与证据合同

```text
Accounting Net PnL
  = closing wealth - opening wealth - external capital flows

Accounting components
  = core trading PnL
  + position-operation PnL
  + realized maker rebate
  + realized taker rebate
  + realized LP / holding incentive
  + rounding residual

Economic Profit
  = Accounting Net PnL
  - incremental financing cost
  - incremental operating cost
```

规则：

- split/merge/redeem 是资产重分类或终值转换，不另造一条“merge edge”。
- incentive 没有 immutable payout/eligibility identity 时记 0，并阻断 promotion。
- `shares × rate × (p × (1-p))^exponent` 只可作 V2 forecast；actual fee 服从 fill/cash evidence。
- W1、W2 都保留 signal funnel 与 evidence funnel；缺书、non-fill、reject、unknown 不从 signal denominator 删除。
- 推断按 `target_date` block 等权；MERE 在看结果前冻结为 `$0.005/share`，每新增 10 个独立 target dates 才更新一次。

## 4. Stage 0A — venue / accounting truth

最终只读 artifact：

`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/weather_mm_stage0_truth_audit_v1/stage0_truth_20260830T074002Z/report.json`

固定窗口为 CLOB V2 cutover `2026-04-28` 之后：

| 项目 | 结果 |
|---|---:|
| order rows / actual venue-side-effect rows | `3,650 / 1,569` |
| canonical live_real fill rows | `1,597` |
| actual fee exact / estimate | `1,535 / 62` |
| actual fill fee total | `$22.31393` |
| legacy estimate rows | `220` |
| legacy estimate / 正确 V2 estimate | `$84.79200 / $3.20735` |
| estimate overstatement | `$81.58465` |
| 受 estimate 影响的历史 admission decisions | `0`；错误 estimate 在 submit 后生成，未被 admission/risk 消费 |
| post-2026-05-28 taker rebate candidate fills | `1,048` |

Journal 证明 220 个已提交 side effects 全部曾写 `collateral_asset=USDC`、旧 fee schedule ref、`clob-v2` 与 `py_clob_client_v2`；canonical DB 没完整保留 collateral 字段，因此两层证据分开报告。

Gate 为 `FAIL_CLOSED`：62 笔 fee 仍是 curve estimate；历史 raw V2 fee details 不完整；实际 taker rebate payout ledger 缺失。旧 estimate 已从新 admission 语义中隔离，但不能据此声称历史 fee/incentive truth 已完全闭合。

## 5. Stage 0B — own-order truth / sole reconciler

真实 execution journal 审计：

| 项目 | 结果 |
|---|---:|
| journal rows / unique attempt identities | `1,460 / 324` |
| submitted rows / unique venue orders | `220 / 220` |
| orphan / duplicate side effect / shared venue ID | `0 / 0 / 0` |
| identity parity | `100%` |
| authenticated REST clean terminal replay | `33 / 33` |
| historical unknown | `10`；其中 `9` 是显式 HTTP 403 reject，`1` 是未解决 network exception |
| private User WS events | `0` |

已实现但尚未接生产：

- Decimal-only `OwnOrderTruthReducer`：WS/REST、trade 去重、partial/late fill、terminal reopening、owner/order/size conflict、REST-after-WS reconcile。
- sole-owner `TargetOrderSet → deterministic diff`：generation、idempotency、self-cross guard、foreign/ambiguous owner block、cancel-confirmed 后才允许 replace。
- reconnect book root fix：null predecessor 视作新 subscription epoch；非 null prev-hash mismatch 仍报错。

Gate 仍为 `FAIL_CLOSED`：private User WS 未接；sole reconciler 未接 runtime；1 个 unknown 未 reconcile；没有 heartbeat/cancel-all/restart fault 的生产级验收。纯函数已实现不等于 production owner 已切换。

## 6. W1 — defensive inventory lifecycle

预注册：`src/strategies/weather_edge_v1/config/weather_mm_w1_inventory_prereg_v1.json`

最终 artifact：

`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/weather_mm_w1_inventory_v1/w1_inventory_20260830T074002Z/report.json`

分母是 Core Carry 的真实 `BUY_YES` fills，在 condition grain 聚合；116 个 actual positions、34 个 target dates，日期范围 `2026-07-25..2026-08-30`。232 个 YES/NO tokens 全部找到 book，扫描 8,686 个 archived files、82,397 个目标 token book rows；116/116 condition topology 已验证。五臂为：

```text
immediate taker exit
passive sell with 60m hard fallback
same-condition NO acquisition then merge
hold to settlement
no action
```

92 个 episodes / 31 个 target dates 具备五臂完整 economic evidence。结果相对 `no_action`：

| Arm | paired episodes / dates | mean delta / share | target-date bootstrap 95% CI | Gate |
|---|---:|---:|---:|---|
| immediate taker exit | `102 / 31` | `-$0.04176` | `[-$0.08363, +$0.00506]` | `INCONCLUSIVE` |
| passive then fallback | `92 / 31` | `-$0.03517` | `[-$0.07357, +$0.00970]` | `INCONCLUSIVE` |
| complement then merge | `102 / 31` | `-$0.04176` | `[-$0.08474, +$0.00626]` | `INCONCLUSIVE` |
| hold / no action | `103 / 31` | `$0` | `[0, 0]` | baseline self-comparison `FUTILITY` vs positive MERE |

执行 evidence：115 个 immediate exits 和 115 个 complement acquisitions 在 archived ladder 上可执行；passive arm 有 87 个 public-book cross/depth proxies、17 个 hard fallback、其余缺证据。它们全是 counterfactual，不是 own-order fill。实际 exit-or-merge episodes=`0`，低于 first-look floor 20，因此 measurement gate 失败；所有 counterfactual rewards 记 0。

当前动作是保持 `no_action / hold-to-settlement` 为默认。`passive_then_fallback` 只保留为下一轮 zero-notional/controlled measurement candidate；其点估仍为负，不能写成盈利策略。

## 7. W2 — narrow maker acquisition falsification

预注册：`src/strategies/weather_edge_v1/config/weather_mm_w2_narrow_maker_prereg_v1.json`

最终 artifact：

`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/weather_mm_w2_narrow_maker_v1/w2_narrow_maker_20260830T080000Z/report.json`

只允许两个事前 state：

```text
healthy_passive_candidate
transient_dislocation_candidate
```

固定分母使用 live append-only file 的精确 consumed-prefix hash，按 `signal_id` 保留全部 revision lineage并显式选择最后一条 append revision；target_date/city identity 变化会 fail closed。本次 prefix 有 161 decision rows、489 action rows，其中 28 个 unique first-positive signals / 9 个 target dates：

| 项目 | 结果 |
|---|---:|
| 窄 regime selected | `0`；28 行均为 `candidate_state=unknown` |
| evidence-complete / actual maker fills | `0 / 0` |
| narrow-regime target-date blocks | `0` |
| maker/taker realized rebate identities | `0 / 0`；均记 `$0` |
| frozen MERE | `$0.005/share` |
| zero-notional audit | `650/650` rows 显式 notional=0、TradeIntent=false、venue_call=false |
| promotion | `FAIL_CLOSED` |

主要 blocker：未达到 `100 decisions / 30 target_dates / 20 actual fills`；没有 hazard/price/depth 多 strata；没有 actual own-order lineage、realized maker/taker rebate、tail/capacity evidence；Stage 0A/0B gate 未闭合；target-date interval 不可估。

Reconnect 影响半径已复制进 W2 artifact：旧逻辑在 4 个事件产生 6 个 reconstruction errors、0 terminal decisions；patch replay 后 errors=0，Madrid/Warsaw 恢复 2 个 terminal decisions，4 个事件生成 12 个 t0/t30/t120 actions。但恢复的 decisions 仍因 ladder evidence 不完整而 blocked。Patch 未部署；当前 production manifest 存在 desired/runtime checkout mismatch 与传播 critical，不能在该状态下把离线修复称为生产恢复。

### 7.1 W2.1 — Weather-first Selective Maker

冻结配置：`src/strategies/weather_edge_v1/config/weather_mm_w2_selective_maker_prereg_v2_1.json`

核心实现：`src/strategies/weather_edge_v1/execution/selective_maker.py`

最终 evidence seal：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/weather_mm_w2_selective_maker_v2_1/w2_selective_maker_v21_20260830T120000Z/report.json`

V2.1 不把 maker 当成默认执行方式。对每一条原 weather signal 同时构造 maker、full-depth taker、skip 三臂，并按下面的非对称成本路由：

```text
taker fair = weather fair value - uncertainty - inventory risk
maker fair = taker fair - transition hazard × waiting penalty

maker expected edge
  = conservative P(full fill) × maker conditional edge
  + (1-P(full fill)) × non-fill fallback edge
  - maker order cost

只有 maker expected edge
  ≥ minimum retained edge
  且 ≥ taker edge + frozen maker-selection margin
才选 maker；否则选 taker 或 skip。
```

这里有三个刻意的 fail-closed：

- transition hazard 只惩罚等待中的 maker，不错误惩罚即时 taker。
- maker 必须使用 authoritative own-order labels 校准的 conservative fill probability；measurement prior 不能进入 profit route。
- rebate、LP reward、holding reward 只有 immutable payout + eligibility identity 的 realized ledger 才入账，任何 forecast 在路由和 promotion 中都记 0。

历史 mechanism-repair replay 的边界是：t0 冻结 weather model probability，t30 使用 gap-checked WS as-of book；不读取 t120、terminal、settlement 或 future fill。V1 结果不被回写；V2.1 的正式 forward 起点冻结为 `2026-08-31T00:00:00Z`。

| 项目 | V2.1 结果 |
|---|---:|
| fixed denominator | `28 signals / 9 target dates` |
| causal t30 candidates | `14 transient / 14 direct unknown` |
| decision-packet fair value join | `28/28`，token conflict=`0` |
| exact t30 maker/taker/skip | `14/14`，WS reconstruction failures=`0` |
| zero-notional readiness | `PASS` |
| observed profit route | `0 maker / 4 taker / 10 skip` |
| optimistic sensitivity（hazard=0, fill lower=1） | `10 maker / 1 taker / 3 skip`；仅 what-if |
| actual maker fills / realized incentive labels | `0 / 0` |
| micro-live | `NOT AUTHORIZED` |
| profitability | `INCONCLUSIVE / FAIL_CLOSED` |

路由到执行的纯函数桥已接到 sole `TargetOrderSet`：maker 产生一个 post-only target；taker/skip 先产生空的 `CANCEL_ONLY` target set。taker 必须等 authoritative own-order truth 证明旧 maker 已全部 cancel-confirmed 后，才可在下一 generation handoff，避免 maker cancel 与 taker submit 重叠暴露。该代码没有创建 venue client、TradeIntent 或真实订单。

micro-live entry 与 profitability gate 明确分开：measurement 不要求事先已有 20 fills；否则会形成“必须先成交才能获准收集成交”的循环条件。measurement 只负责在独立资金授权下采集 fill/non-fill、transition hazard、queue/markout 和 realized incentive labels；`100 decisions / 30 dates / 20 actual fills` 只属于其后的 frozen-forward profitability gate。

## 8. 实现与复现入口

| 责任 | 稳定入口 |
|---|---|
| V2 fee forecast / provenance | `src/strategies/weather_edge_v1/execution/economics.py` |
| own-order state machine | `src/strategies/weather_edge_v1/execution/own_order_truth.py` |
| payoff vector / EpisodeLedger / W1 contracts | `src/strategies/weather_edge_v1/execution/inventory_economics.py` |
| sole TargetOrderSet diff | `src/platform/quote_runtime/target_order_set.py` |
| Stage 0 audit | `scripts/analysis/market_making/research_weather_mm_stage0_truth_audit_v1.py` |
| W1 replay | `scripts/analysis/market_making/research_weather_mm_w1_inventory_v1.py` |
| W2 seal | `scripts/analysis/market_making/research_weather_mm_w2_narrow_maker_v1.py` |
| V2.1 maker/taker/skip router + live authorization bridge | `src/strategies/weather_edge_v1/execution/selective_maker.py` |
| V2.1 frozen config | `src/strategies/weather_edge_v1/config/weather_mm_w2_selective_maker_prereg_v2_1.json` |

复现命令写在各 artifact 的 `research_record.json`；本 living doc 只保留当前判断和 immutable artifact pointer。W1 当前 artifact 记录 DB identity、prereg hash、runtime score hash 与 archive coverage，但没有 8,686 个 book files 的逐文件 content hash，因此其定位是 historical counterfactual effect estimate，不升级为不可变 raw-source snapshot。

## 9. 下一 gate，而不是自动下一部署

W1 若继续，只能新增真实、低损失预算的 reduce/exit measurement。W2.1 当前只继续 zero-notional forward。进入一次独立授权的 controlled micro-live measurement 前必须同时满足：

1. Stage 0A actual fee、taker rebate 与 asset truth 闭合。
2. Stage 0B private User WS、REST-after-reconnect、sole reconciler 和 unknown recovery 接生产并通过 fault drill。
3. production manifest critical 清零，release/config identity 与 collateral asset 经动态核对。
4. 独立资金 sleeve、single/active/daily/market/city-date notional cap、daily loss cap、fresh inventory snapshot、cancel-all、rollback 与 owner 显式授权全部就绪。
5. 只执行冻结的 measurement policy；不把 measurement prior 当 calibrated fill model，不把 public touch 当 fill。

measurement 完成后，profitability promotion 另需：至少 `100` 个窄 regime decisions、`30` 个独立 target dates、`20` 个 authoritative maker fills；private lifecycle 100%、evidence coverage 95%、hazard/price/depth 多 strata、realized incentives、tail/capacity 均完整，且 target-date lower bound 严格高于 `$0.005/share` MERE。

当前 Stage 0、production health 与 owner authorization 都未通过，所以 micro-live 仍是 `NOT AUTHORIZED`。未达到条件时保持 zero-notional，不调整城市、price band 或删除 non-fills 来“救活” maker。

## 10. 独立 review、修复与 evidence seal

独立 reviewer 的只读范围是本轮 owned code/tests，检查 correctness、边界条件、幂等/append-only 语义、测试缺口以及明显性能/可读性问题；reviewer 不改文件。首轮五项 findings 均已由主执行链修复：

| Finding | 修复 |
|---|---|
| 未确认 cancel 的旧单可能被当作不存在并直接 `CREATE` | `TargetOrderSet` 把 unconfirmed cancel 作为 blocker，replace 必须等待 cancel-confirmed |
| W2 相同 identity 只取首行，可能丢 late fill/rebate revision | 保存完整 revision lineage，按最后 append revision 求值；identity/invariant 冲突 fail closed |
| W2 bootstrap 被 shares 加权 | 先求每个 `target_date` 的 per-share statistic，再对 date blocks 等权 bootstrap |
| terminal fallback 暗含固定 5 shares | 仅在实际 shares=5 时使用该字段；其他 size 缺 direct terminal evidence 时 fail closed |
| W1 空 denominator 会 `IndexError` | 输出合法零分母 `FAIL_CLOSED` report，不再异常退出 |

V2.1 完成后又执行了一轮独立只读 review；三个 P2 findings 和一项相邻账本 hardening 均已修复：

| V2.1 finding | 修复 |
|---|---|
| router 接受 `SELL`，但授权桥只能生成 acquisition `BUY` target | V2.1 明确限定为 `BUY` acquisition；退出与减仓继续由 W1 ownership 管理，`SELL` fail closed |
| route audit 未再次验证 maker/taker 的相同 shares | audit 内重新校验同一 token、同一 shares，防止比较不同经济规模 |
| decision packet append revision 使用 last-wins，可能静默吞掉 scoped identity/model conflict | 保留 scoped revision lineage；当前 denominator 内 identity、model probability、token conflict 全部 fail closed；历史无关 revision 仅计数，不阻断当前分母 |
| batch payout allocation 可能把同一 immutable payout 重复超额分配 | 新增 immutable payout total 与 allocation identity；累计 allocation 不得超过 payout total，重复 allocation fail closed |

最终相关测试为 `90 passed`；所有 owned Python 入口通过 `py_compile`，tracked 与新增 owned files 的 whitespace check 无报错。V2.1 reviewer 共执行 10 次只读工具调用，运行环境未返回 usage telemetry，因此不把 token 数写成已核验。W1 的已发布非空回放结果不受空分母修复影响；其 artifact 记录的是运行时 producer hash，当前 runner 另含空分母 fail-closed 修复。

V2.1 首次 `20260830T113000Z` artifact 暴露出 packet revision conflict 未按当前 denominator scope 的问题；该 artifact 保留但标记为 superseded。影响半径仅为这一份 zero-notional research artifact，没有进入生产、真实订单或 live 决策。最终 seal 是 `20260830T120000Z`。

四份 schema-valid 研究记录随 artifact 保存：

- Stage 0：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/weather_mm_stage0_truth_audit_v1/stage0_truth_20260830T074002Z/research_record.json`
- W1：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/weather_mm_w1_inventory_v1/w1_inventory_20260830T074002Z/research_record.json`
- W2：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/weather_mm_w2_narrow_maker_v1/w2_narrow_maker_20260830T080000Z/research_record.json`
- W2.1：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/weather_mm_w2_selective_maker_v2_1/w2_selective_maker_v21_20260830T120000Z/research_record.json`

## 11. V2.1 forward 部署、采集与分析合同

### 11.1 部署对象

本阶段部署的是新的常驻 zero-notional observer，不复用或改写现有 market-state、scheduled-maker 或 live runner：

| 字段 | 冻结值 |
|---|---|
| host / process owner | Mac current production / `weather_production_ctl.py` |
| release / instance / session | `weather_first_selective_maker_v2_1@73f7e603505829158f64a78ed8049101fc6f0b2c` / `weather_first_selective_maker_v2_1_shadow` / 同名 canonical JRS tmux session |
| execution mode | `zero_notional_shadow`；`live_authority=false`，notional、TradeIntent、plan、order、fill、exchange call 恒为 0 |
| upstream | `current_yes_core_carry_market_state_shadow_v2`、`current_yes_core_carry_tiny_live_v2` decision packets、`weather_market_books` |
| formal forward start | `2026-08-31T00:00:00Z`；此前只报 `warming`，不回填旧行 |
| denominator | 每个 append-only `first_positive × t30` signal；unknown、缺 book、缺 packet、skip 与 non-fill 均保留 |
| state | 仅严格因果 `place_probe_maker + post_trigger_acute_window_admitted_probe + 30s` 映射 transient；其他为 unknown |
| sizing / price | maker 与 taker 使用同一 token、同一 route clock、同一 venue minimum shares；价格上限由 frozen fair value、uncertainty、inventory risk 和 MERE 共同决定 |
| fee / incentive | taker fee 只保存带 provenance 的 forecast；maker rebate、LP 与 holding reward 在 realized payout 前一律记 0 |
| dependencies | `weather_data_feed_jrs`、`current_yes_core_carry_tiny_live_v2`、`current_yes_core_carry_market_state_shadow_v2`、`weather_market_books` |
| output root | `/Volumes/jrs/pm_agents/runtime/weather_edge_v1/weather_first_selective_maker_v2_1` |
| rollback | controller 精确 stop 本 safe shadow；不停止上游，不撤改 live 单，不删除 append-only raw |

该 runtime 对同一 signal 只允许一次有效 append；后续 revision 保留 lineage。signal/token/model probability identity 变化时 fail closed，不用 last-wins 静默覆盖。route clock 之后才 available 的 REST/WS book 禁止进入本行；book age 超过 5 秒、epoch/gap/parity 不完整或单边盘口均保留 blocker。

decision packet 比 t30 action 更早出现，因此 capture demand 在 packet 首次可见时即发出，而不是等到 t30 action 落盘；共享 market-books 的原始记录同时按 capture identity 复制进本 runtime 的 append-only `book_snapshots.jsonl`。t30 route 只从这份 journal 中选择 `available_at <= route_clock` 的最后一帧，避免 action 延迟造成 future-book look-ahead。

### 11.2 必采数据

| 数据层 | grain 与关键字段 | 用途 |
|---|---|---|
| denominator | 每个 first-positive t30 signal；signal/event id、city、target_date、append revision/hash、producer SHA、四时钟 | 固定 signal funnel，阻止删 non-fill 或坏样本 |
| frozen weather value | packet id、model/artifact/config id、`model_probability_hold`、token/condition/market、feature clock | weather-only fair value 与 market baseline |
| execution book | route clock 前最后一个 immutable REST/WS reconstruction；完整 bids/asks、tick、minimum shares、snapshot/batch/epoch/raw refs、age、gap/parity、`feesEnabled` 与 per-market `fd={r,e,to}` | 同钟 maker/taker/skip 与 full-depth taker VWAP；不靠全局默认猜市场参数 |
| route arms | maker quote、queue level、taker VWAP/fee、skip、每臂 retained edge、选路与全部 blockers | 执行表达 A/B；不把 future touch 当 fill |
| selective WS tape | token、subscription epoch、baseline/delta refs、add/cancel/replace、public prints、0/10/30/60/120/300s checkpoint | spread/depth/quote-state、transition 与 markout labels；public activity 不是 own fill |
| safety / health | live authority、notional、intent/plan/order/fill/exchange-call counters、cursor、source freshness、daily bytes/messages | 证明 zero-notional 与捕获健康 |
| settlement | condition id join canonical settlement；final outcome、settlement source/build | probability quality 与 taker/skip terminal value；不进入事前路由 |
| future micro-live only | private User WS、REST reconciliation、submit/ack/partial/cancel/fill、own price/size/queue-ahead proxy、non-fill censor clock | authoritative fill probability、生存时间、adverse selection；必须另行资金授权 |
| realized economics only | fill fee、maker/taker rebate payout、LP/holding payout、eligibility/allocation identity、asset | fee-adjusted residual；估计奖励不入主结论。官方当前 Weather 类别 forecast 为 taker rate `0.05`、maker fee `0`、rebate pool `25%`，但每个市场仍以 CLOB market info 为准 |
| inventory / risk | decision-time inventory snapshot、open/reserved exposure、city-date/market/daily caps、loss state | 解释库存惩罚与容量，不把 open cost 当亏损 |

选择性 capture 不扩大静态城市池或 full-ladder 配置，只为进入分母的当前 token 发有界 demand。冻结 checkpoints 为 `[0,10,30,60,120,300]` 秒，单 demand TTL 10 分钟、V2.1 active token 上限 16、daily signal 上限 32；沿用 market-books 总 payload `3 GB/day` 与 execution-evidence `1 GB/day` 硬预算，V2.1 子预算为 proxy `100 MB/day`、hot disk `250 MB/day`。raw hot 保留 14 天后只按既有 archive policy 迁移，不由策略 runner 删除。达到任一预算、gap/parity 失败或 capture receipt 缺失时停止新增 demand并保留 coverage blocker。

fee 公式与类别参数依据 [Polymarket Fees](https://docs.polymarket.com/trading/fees)；maker rebate 只在 [Maker Rebates](https://docs.polymarket.com/market-makers/maker-rebates) 的实际日结 payout 出现后入账。`0.05 × shares × p × (1-p)` 在 runner 中只是 Weather 类别 route forecast，绝不伪装成单市场实际 fee 或 rebate。

### 11.3 分析计划

分析不重新挑城市、price band 或 state，统一按 `target_date` 等权：

1. **每日 coverage audit**：分别输出 signal funnel 与 evidence funnel；核对 denominator、packet join、PIT book、WS epoch/gap/parity、route readiness 和 safety counters。任何 side effect 非 0 立即停止实例。
2. **每 10 个新 target dates 固定复核**：在同 rows 上报告 `weather-only`、market probability、maker/taker/skip；maker 缺 calibrated fill/hazard 时只报告 conditional edge 与 sensitivity，不给盈利信用。
3. **概率层**：settled 后比较 frozen weather probability 与同钟 market 的 logloss、Brier 和 calibration；blocked rows保留在 coverage 分母，不进入 score 分子。
4. **微结构层**：按固定 checkpoints 估计 spread/depth、support withdrawal、transition probability 与 taker markout；frame/message 不等权，先落到 signal/checkpoint，再在 target_date 内等权。
5. **受控 micro-live 后的 fill 模型**：用 authoritative own-order lifecycle 构造 full-fill / partial / censored non-fill；按 price、spread、depth、hazard 至少各两个 strata 做 expanding/OOF calibration，输出 conservative lower fill probability。public touch、cross 或 quote delta不能当 fill。
6. **经济 A/B**：同一 signal、同一 shares 比较实际 maker wealth、同钟 full-depth taker counterfactual 与 skip；只入 realized fee/incentive。主指标是 maker 相对 taker的 fee-adjusted residual USD/share，按 target_date block bootstrap 95% CI。
7. **promotion**：必须同时满足 `100 decisions / 30 target dates / 20 authoritative maker fills`、evidence coverage ≥95%、private lifecycle=100%、hazard/price/depth strata、tail/capacity完整，且 residual lower bound严格高于 `$0.005/share` MERE。否则维持 `inconclusive`。

### 11.4 启停门禁

controller/manifest critical、DB route/JRS probe失败、上游 source/book stale、formal forward identity变化、revision conflict、未来 book 泄漏、任一 safety counter非零或预算超限，均立即 fail closed。zero-notional 部署不授权 micro-live；micro-live 仍需 Stage 0A/0B、production health、独立 sleeve/caps/loss/cancel-all/rollback 和用户对真实资金的再次显式授权。
