# Alpha Capital Agent Strategy V1

状态：`IMPLEMENTED_OFFLINE_SHADOW_CORE`

执行上限：`READ_ONLY_SHADOW / NO_ORDER`

本文不改变 Alpha Gate R 当前 `CONTROLLED_MANUAL_RESEARCH` 状态，不批准每日扫描、
authenticated CLOB 扩权、生产 capture 扩容或真实下单。

截至 2026-08-29，仓库已实现并验证本策略的离线核心切片：

- `universe.py`：marketability 合同、hysteresis、formerly-ineligible crossover、held override、
  append-only admission episode；
- `scheduler.py`：固定 cadence policy、`updatedAt,id` keyset cursor、15 分钟 overlap、bounded
  scan、coverage/failure receipt，以及 deterministic collapsed catch-up work order；
- `capital.py` / `allocator.py`：authenticated coverage gate、NAV cash buffer、fractional Kelly、
  market/event/cluster/maturity hard caps、逐档 bid/ask、partial replacement、15 分钟两次确认和
  6 小时 cooldown；
- `storage.py` / `coordinator.py`：独立 additive SQLite V3、scan/cadence/evaluation 三套隔离
  durable lease/ACK，以及 scan → eligibility → admission → capital plan 的 caller-driven 编排；
- `books/microstructure.py`：绑定 frozen paired book + 本地 receipt available clock 的研究特征层；
  永久 `research_only / decision_use=PROHIBITED / NO_ORDER`。

这不是 operational scheduler：当前实现不启动 daemon、不调用 Gamma/CLOB 网络、不读取凭据、
不接管现有 account/book owner，也不生成订单。WP2 cockpit、approved read-only scheduled pilot、
真实 adapter 和任何 execution 仍服从后续独立 gate。

## 1. 项目裁决

把 Alpha Gate R（AGR）的市场发现与研究链，和当前资金效率/账户 ledger，组合成一个
新的上层产品：`Alpha Capital Agent`（ACA，Alpha 资本闭环 Agent）。

ACA 不是把两个页面拼在一起，也不是让一个 LLM 自主交易。它是一个可重放的组合决策
系统，持续回答五个问题：

1. 当前资金在哪里：free cash、open-order reserve、active positions、redeemable、
   pending resolution、退出流动性和预计释放时间；
2. 市场上有什么：哪些 Candidate 已经形成有来源、有概率区间、有 fresh executable book
   的 Opportunity；
3. 资金应如何调整：赎回、合并 complete set、继续持有、退出复核、观察、或新增 shadow
   allocation；
4. 决策是否有效：概率是否校准、资金是否被更高质量机会使用、实际/模拟收益是否覆盖
   fee、slippage、capital-days 和研究成本。
5. 机会集是否保持新鲜：新市场、重新满足条件的旧市场、规则/盘口变化和现有持仓，何时被
   增量重评、何时升级为 Candidate、何时触发组合重排。

推荐架构是：**一个 coordinator、两条隔离事实链、一个确定性 allocator、一个学习闭环**。
不采用自由漫游的多 agent swarm。

## 2. 为什么应当合并，但不能提前混合

现有两块资产互补：

| 资产 | 已有能力 | 当前缺口 |
|---|---|---|
| Alpha Gate R | Gamma/catalog、multi-recall、Candidate、Rule A/B、price-blind research、fresh book comparison、NO_ORDER ledger、resolution scoring | 不知道账户现金、已有仓位、open orders、组合集中度和资金机会成本；固定 simulation size 不是组合 sizing |
| 资金效率工具 | 当前仓位 mark/cost、期限、visible bid exit、redeemable、人工 cash/reserved 输入、静态 utilization | 没有独立概率、expected value、Candidate 机会集、相关性、组合约束、决策 ledger；公开 API 无法闭合 cash/open orders |
| Account ledger | account-scoped public activity、position snapshots、Relayer states、append-only lineage | 不等于 authenticated cash/open-order truth；目前不是完整 portfolio snapshot |

合并点必须在 `BLIND_RESULT_ACCEPTED + fresh formal book` 之后。账户持仓、方向、成本和
钱包身份不得进入 Blind research，否则会重新引入 anchoring、selection bias 和 side leakage。

```text
Market facts -> AGR discovery/research -> OpportunityRecord --+
                                                           |
Account facts -> AccountSnapshot -> PortfolioSnapshot ------+-> deterministic CapitalAllocator
                                                           |        -> CapitalPlan
Frozen CapitalPolicy + calibration state -------------------+        -> human action card
                                                                    -> shadow ledger
                                                                    -> outcome/evaluation
```

## 3. Truth ownership

ACA 只做组合，不复制上游事实 owner。

| Truth | 唯一 owner | ACA 的权限 |
|---|---|---|
| market/catalog/rule/research/probability/book comparison | `src/polymarket_alpha` | 读取冻结合同，生成 `OpportunityRecord` projection |
| account activity/position history | Polymarket account ledger | 读取指定 account 的 immutable snapshot |
| authenticated balance/open orders | 现有 CLOB/account owner | 只消费 receipt；不得新建第二个 authenticated collector |
| order book | 现有 single book owner | 继续使用 demand/receipt bridge |
| portfolio policy/plan/recommendation | ACA | append-only、hash-bound、可重放 |
| order/signing/private key | 独立 execution owner | V1 不依赖、不导入、不可调用 |

建议新增独立 composer 包和计划库，而不是把组合状态塞进 AGR Candidate 或 weather DB：

```text
src/alpha_capital_agent/
  contracts.py
  account_adapter.py
  opportunity_adapter.py
  universe.py
  marketability.py
  scheduler.py
  admission.py
  valuation.py
  allocator.py
  rebalancer.py
  policy.py
  coordinator.py
  projections.py

runtime/db/alpha_capital_agent.db   # 仅 aca_* plan/evaluation facts
```

已有 `weather_dashboard/api/capital_efficiency.py` 保留为 projection/UI 资产；它不升级为
canonical portfolio truth。第一阶段通过 adapter 复用，不做搬家式重构。

## 4. 工作流：确定性骨架，Agent 只处理开放式问题

### 4.1 Discovery Loop

```text
continuous universe scheduler
  -> event ingest + incremental delta + periodic anti-entropy census
  -> deterministic lifecycle/eligibility/recall
  -> research admission episode
  -> Candidate seal
  -> cheap semantic triage
  -> Rule A
  -> isolated price-blind research
  -> accepted probability interval
  -> fresh size-aware paired book
  -> deterministic net edge
  -> OpportunityRecord
```

LLM 可以做：语义 triage、规则 parse proposal、联网 source research、证据摘要和人类卡片解释。

LLM 不可以做：改 identity/hash、绕过 Rule、读取 Blind 禁止字段、直接计算权威 edge、直接
决定 size、写账户 truth、调用 order/signing。

### 4.2 Capital Loop

```text
account tick / position change / fill / order change / resolution
  -> AccountSnapshotSeal
  -> completeness + freshness gate
  -> mark / cost / visible liquidation / release-date projection
  -> exact event exposure + semantic cluster exposure
  -> PortfolioSnapshot
```

优先级固定为：

1. `REDEEM` 已结算可赎回资金；
2. `MERGE_COMPLETE_SET` 同一 condition 可无风险释放的 YES/NO 对；
3. `CANCEL_STALE_ORDER_REVIEW` 明显过期且占用资金的挂单；
4. 对已有持仓做 hold-vs-exit 复核；
5. 再研究和分配新机会；
6. 最后才做低价值探索性扫描。

### 4.3 Allocation Join

只有以下输入同时 fresh、complete、hash-bound 时才生成计划：

- AccountSnapshot；
- PortfolioSnapshot；
- 一个或多个 `OpportunityRecord`；
- versioned CapitalPolicy；
- 当前 calibration/model-risk state；
- 同一 `decision_as_of` 下的 executable depth/cost curve。

输出是建议，不是订单：

```text
DATA_BLOCKED
REFRESH_RESEARCH
REDEEM
MERGE_COMPLETE_SET
CANCEL_STALE_ORDER_REVIEW
HOLD
REDUCE_REVIEW
EXIT_REVIEW
ENTER_SHADOW
WATCH
PASS
```

## 5. 必要合同

### 5.1 AccountSnapshotSeal

至少包含：

- `account_id`、proxy/funder identity（敏感值只留受控引用）；
- free collateral、reserved collateral、open orders；
- active/redeemable positions；
- fill/order/position source receipts；
- source/observed/ingested clocks；
- public-only 或 authenticated-complete 的 coverage class；
- raw artifact ids/hashes、schema/policy version、snapshot id/hash。

现金或 open orders 缺失时，仍可展示公开仓位，但不得生成资金闭合的 `CapitalPlan`。

### 5.2 PositionExposure

每个仓位至少包含：

- condition/token/outcome、shares、cost basis、current mark；
- best bid、policy-size exit VWAP、visible exit coverage；
- redeemable/pending/dust/unknown-end；
- expected release interval，而不是只信 Gamma `endDate`；
- event group、互斥/complete-set 关系、semantic risk cluster；
- current probability lineage 或 `RESEARCH_STALE/MISSING`。

### 5.3 OpportunityRecord

这是 AGR 和 allocator 的唯一 join seam：

- Candidate/Rule/Blind result/MarketComparison/Rule B 的 ids + hashes；
- YES/NO conservative probability；
- 多档 executable cost curve（fee、slippage、depth 后）；
- conservative edge、central edge、uncertainty width；
- evidence quality、rule risk、fresh-until；
- event/topic/maturity cluster proposal；
- `NO_ORDER` capability receipt。

Recall score 只用于研究排队，不直接进入资金 sizing。

### 5.4 CapitalPolicy

全部 versioned，不允许 prompt 临时改数：

- cash buffer；
- 单 market、单 event、semantic cluster、maturity bucket 上限；
- model/calibration haircut；
- minimum conservative edge；
- fractional-Kelly multiplier；
- executable depth participation cap；
- turnover/exit cost；
- stale data TTL；
- research/day、source、token、elapsed-time budgets；
- allowed recommendation types 和 human approval policy。

### 5.5 CapitalPlan

必须绑定全部 input hashes、policy hash 和 prior plan：

- before/after cash、reserved、exposure、capital-days；
- 每项 action、direction、target size、all-in cost；
- conservative/central expected PnL；
- hold-vs-exit incremental value；
- marginal capital efficiency；
- hard-cap headroom 和 blocking reasons；
- deterministic recomputation receipt；
- `SHADOW_ONLY/NO_ORDER`；
- human review/approval/rejection receipt。

## 6. 资金策略

### 6.1 不把 utilization 当目标函数

资金利用率只描述“用了多少”，不描述“用得好不好”。100% utilization 会消灭等待更好机会、
处理 drawdown 和盘口变化的 optionality。

北极星指标定为：

```text
fee_adjusted_incremental_pnl / capital_days
```

并同时约束 calibration、drawdown、concentration、liquidity、turnover 和数据完整性。

保留三种账：

- `mark NAV`：便于看当前账面；
- `liquidation NAV`：按 visible executable bids 估计可退出权益；
- `terminal scenario NAV`：按互斥 event outcome 和 stress scenario 算最坏损失。

### 6.2 新机会的边际价值

对 YES：`q_cons = p_low`；对 NO：`q_cons = 1 - p_high`。若历史 calibration 显示对应
bucket 过度自信，先按冻结 policy 向 0.5 shrink，再进入 sizing。

对于一份 payout 1 的 outcome token：

```text
conservative_edge = q_cons - all_in_executable_cost
conservative_ev_per_dollar = conservative_edge / all_in_executable_cost
marginal_capital_efficiency =
    conservative_edge /
    (all_in_executable_cost * expected_release_days / 365)
```

成交成本必须来自同一份 synchronized paired-book `ExecutableCostCurve`，逐档计算：

```text
taker_fee_at_level = shares * fee_rate * price * (1 - price)
all_in_buy_cost = sum(level_notional + level_taker_fee) + frozen_slippage_buffer
```

新增的 `MarketComparisonV2` 保存被消费的档位、fee 和 all-in price；旧 `MarketComparison`
payload 仍可按 V1 读取。decision ledger 必须绑定 V2 comparison 的 id/hash 与 cost policy，
禁止再从 raw VWAP 用另一套公式重算。实际成交 fee 仍以
exchange fill reconciliation 为最终事实。

`marginal_capital_efficiency` 只做排序特征，不单独决定交易，因为短期限、错误 deadline 或
尾部小概率可把它机械放大。

### 6.3 已有仓位按 hold-vs-exit 重算

不以历史买入价为继续持有理由。当前能退出的 bid 是可重新分配资本的 opportunity cost：

```text
hold_incremental_ev_per_share = q_cons - executable_exit_value
hold_hurdle = executable_exit_value * annual_hurdle * release_days / 365
```

若 `hold_incremental_ev` 不能覆盖 hurdle、模型误差和流动性缓冲，输出 `EXIT_REVIEW` 或
`REDUCE_REVIEW`；缺概率或数据陈旧则输出 `REFRESH_RESEARCH`，不能假装算出结论。

### 6.4 Sizing

V1 shadow policy 用 conservative fractional Kelly，再取全部硬上限的最小值：

```text
raw_kelly = max(0, (q_cons - cost) / (1 - cost))

target_notional = min(
    NAV * kelly_fraction * raw_kelly,
    free_cash_after_buffer,
    market_headroom,
    event_headroom,
    semantic_cluster_headroom,
    maturity_bucket_headroom,
    executable_depth_headroom
)
```

建议的**shadow 起始 policy**，不是 live 参数：

| 项目 | 起始值 |
|---|---:|
| free-cash buffer | 25% NAV |
| fractional Kelly | 0.10 |
| 单 market 上限 | 3% NAV |
| 单 event 上限 | 8% NAV |
| semantic cluster 上限 | 15% NAV |
| 单 maturity bucket 上限 | 25% NAV |
| conservative edge floor | `max(3pp, calibration_error_95, unpriced_cost_buffer)` |
| visible-depth participation | 最多 10%，且 proposed size 必须全深度覆盖 |

这些值只能通过 frozen-forward shadow 结果版本化调整。不能因为“资金空着”降低 edge floor。

### 6.5 组合相关性

同一 Polymarket event 的互斥/嵌套关系使用确定性 payoff matrix。跨 event 的共同风险因子
（同一人物、战争升级、选举、crypto beta、同一 deadline）可以由 LLM 提议 cluster，但必须
冻结为结构化 proposal，经 deterministic identity/dedupe 和人工接受后才进入 policy。

在 correlation 尚不可靠时，用保守 cluster cap，而不是假装有精确 covariance matrix。

## 7. 持续市场调度与增量准入

### 7.1 当前缺口裁决

现有链路还不是持续运行的 market universe pipeline：

- `research/scheduler.py` 只调度已经生成的 research job，没有负责 census 或市场轮询；
- `pipeline/refresh.py` 只刷新已有 Candidate，无法重新发现从未进入 Candidate 的市场；
- `change/detector.py` 有意把 volume/liquidity 排除在结构变更之外，因此 liquidity、spread、
  executable depth 跨过准入线时不会产生现有 `MarketChange`；
- 20-market scan 只抓取 `/events?closed=false&limit=50&offset=0` 的首批数据，实际
  preselection 在 `/private/tmp` 一次性脚本中，阈值和运行器都没有成为可复用、版本化的
  repository contract。

所以不能直接给现有 scheduler 加一个 cron。必须在 Candidate 上游新增持久化的
`Universe Service`，分别管理“市场存在”“当前可研究”“值得花研究预算”“可进入组合”四件事。

### 7.2 分层调度，而不是一个扫描频率

以下是 `READ_ONLY_SHADOW` 的默认 cadence。它们是 pilot 起始值，不构成当前每日生产授权；
任何 operational scheduler 仍需单独过 Gate R 的 read-only pilot gate。

| Lane | 覆盖范围 | 默认触发/cadence | 产出 |
|---|---|---|---|
| Lifecycle event | Polymarket `new_market`、`market_resolved`，以及已订阅 token 的 book/price 事件 | WebSocket continuous；事件后立即定向 hydrate；WS 不可用时由 5 分钟 delta 兜底 | 新市场/终态/盘口 dirty signal |
| Gamma metadata delta | 全部 open/nonterminal markets | 每 5 分钟；按 `updatedAt,id` 倒序 keyset 分页，扫描到 watermark 前 15 分钟 overlap 即停止 | immutable MarketSnapshot revision、结构 change |
| Marketability watch | 持仓、open orders、active Candidate、近阈值 dormant 市场 | 前三类沿用 single book owner 的实时订阅；近阈值每 5 分钟；其他动态失败每 30 分钟 | spread/depth/liquidity/volume observation 与 eligibility transition |
| Full active census | 全部 active universe | 每 6 小时完整 keyset walk | 补漏、重建 tier、coverage receipt |
| Daily anti-entropy | 全部已知 nonterminal、当日 closed/resolved、所有 held markets | 每 24 小时固定 UTC 窗口 | cursor/WS 丢失、退订、生命周期漏报对账 |
| Research admission | newly eligible、materially changed、stale high-VOI、held-under-review | 事件触发；队列每 10 分钟 drain | Candidate episode 或 `PASS_LOW_VOI` |
| Portfolio refresh | fill/order/account/resolution/opportunity/meaningful book change | 事件触发，30 秒 debounce；5 分钟 fallback；计划用的 account/book 必须不老于 60 秒 | new PortfolioSnapshot / CapitalPlan |
| Account reconciliation | 当前账户 | authenticated user event continuous；60 秒 snapshot fallback；每 15 分钟 full reconciliation | cash/reserved/order/position completeness seal |

关键原则：**全市场不每分钟抓完整 order book，research 也不随价格每跳一次重跑。** 广域层只做
便宜的 catalog/marketability 判断；只有持仓、active Candidate 和已通过 Blind research 的
Opportunity 才进入实时 book watch 和 size-aware formal book。

`new_market`、`market_resolved` 和 best bid/ask 等 public WebSocket 事件需要先在 pilot 验证
`custom_feature_enabled` 和断线恢复。若不可用，系统退化到 5 分钟 delta，但不得把 WS 静默
缺失当作“市场没有变化”。

### 7.3 Universe 状态必须独立于 Candidate

新增 append-only `UniverseMarketState`。下列是可能路径，不是要求每个市场逐级经过所有状态：

```text
DISCOVERED -> {INELIGIBLE_DORMANT, NEAR_ELIGIBLE_WATCH,
               ELIGIBLE_UNRESEARCHED}
INELIGIBLE_DORMANT <-> NEAR_ELIGIBLE_WATCH <-> ELIGIBLE_UNRESEARCHED
ELIGIBLE_UNRESEARCHED -> RESEARCH_ADMITTED -> CANDIDATE_ACTIVE
CANDIDATE_ACTIVE <-> CANDIDATE_STALE
any nonterminal state -> TERMINAL
```

三道门必须分开：

1. `Market eligibility`：规则、生命周期、可交易性和最低 marketability 是否允许研究；
2. `Research admission`：预期 VOI 是否值得消耗 source/token/elapsed budget；
3. `Capital admission`：在当前 account、风险 headroom 和 executable curve 下是否值得配置。

`INELIGIBLE_DORMANT` 不等于 Candidate 的 `REJECTED`。每次 eligibility evaluation 保存：

- market/snapshot ids、observed/source/ingested clocks；
- exact reason codes 和原始观测值；
- eligibility policy version/hash、阈值与距阈值距离；
- prior/new universe state、trigger、`next_due_at`；
- `marketability_fingerprint` 与 input receipts。

市场永不因一次失败从 universe 删除。不同失败原因按可恢复性安排复扫：

| 失败原因 | 重新评估策略 |
|---|---|
| quote missing、spread/depth 接近阈值 | 5 分钟 watch；book event 可提前唤醒 |
| liquidity/volume 低但在准入线 20% 内，或趋势快速改善 | 5 分钟 |
| liquidity/volume 明显偏低、spread 明显偏宽 | 30 分钟 |
| rule/source 不完整 | 6 小时 census + metadata/rule change 立即唤醒 |
| inactive/closed/resolved、deadline 已过 | 不做周期 marketability scan；只响应 lifecycle/rule revision 和 daily reconciliation |
| unsupported policy/category | 只在 policy version 或 market metadata 变化时重评 |

### 7.4 盘口跨线与防抖

结构 change 和动态 marketability 是两条事件流：

- 现有 `MarketChange` 继续只表达 rule/lifecycle/metadata/family 等结构变化；
- 新增 `MarketabilityObservation` / `EligibilityTransition` 表达 liquidity、volume、spread、
  executable quote/depth 和 tradability 的变化。

入选 fingerprint 至少包括：active/closed、end/deadline、rule revision、restricted/
enable-order-book、双边 executable quote、policy probe-size depth、spread、liquidity、recent
volume、fee/tick size。headline liquidity 只能做 cheap filter，真正进入 Opportunity 必须按目标
size 的完整 depth curve 复核。

动态指标必须有 hysteresis，防止在阈值附近反复建 Candidate：

```text
enter: 连续 2 次合格，间隔 >= 5 分钟；或持续合格 >= 10 分钟
exit:  连续 2 次不合格；closed/resolved/rule-invalidated 立即生效

liquidity_exit = 0.80 * liquidity_enter
volume_exit    = 0.80 * volume_enter
spread_exit    = 1.25 * spread_enter
```

这些 multiplier 是 shadow 初值；绝对的 liquidity/volume/spread/depth thresholds 必须先从
当前临时 market20 selector 还原并冻结到 `EligibilityPolicy`，再通过 frozen-forward 调整。
不能让阈值继续藏在一次性脚本或 prompt 中。

### 7.5 增量算法

每个 tick 的确定性顺序固定为：

1. 接收 WS dirty ids，并从 Gamma/CLOB owner 定向 hydrate；
2. 每 5 分钟从最新端开始做 `updatedAt,id` keyset delta，使用 15 分钟 overlap 消化迟到或
   同 timestamp 数据；如果 endpoint ordering 在 pilot 中不满足合同，fail closed 并退化为
   full keyset walk，禁止回到单页 offset；
3. 把 raw response 写入 artifact store，再 append immutable MarketSnapshot；
4. 对结构字段运行现有 change detector；
5. 对 `changed ids + due dormant + held/open-order + active candidate` 计算 marketability；
6. 比较 prior state，只有 fingerprint、状态或 reason material change 才 append transition；
7. newly eligible 产生 `MARKETABILITY_CROSSOVER` admission trigger，进入 VOI queue；
8. 对仍 active 的 lineage，若 Blind probability fresh 且只有 book 变化，只刷新 formal
   book/MarketComparison；对已经因 no-edge 终止的 lineage，material price displacement 可创建
   re-entry episode，但仍复用 fresh Blind result；只有 rule、新 public evidence、probability TTL
   或 material research input 变化才重跑 research；
9. 新/更新 Opportunity 触发 allocator，使用 30 秒 debounce 合并事件风暴；
10. 保存 coverage、cursor/watermark、counts、errors 和 next-run receipt。

watermark 用 `(updatedAt, market_id)`，不只用时间；每个 run 有 lease 和 idempotency key，full
census 不并发，重复 WS/REST 事件可安全重放。HTTP 429/5xx 指数退避并加 jitter；coverage 不完整
时保留 last-known universe 供展示，但不得盖 `COMPLETE` seal，也不得据此生成新增资金计划。

每次运行至少写四类合同：`UniverseScanPolicy`（cadence/tier/rate budget）、
`UniverseScanCursor`（cursor/watermark/overlap）、`UniverseScanRunReceipt`（requested/seen/new/
changed/evaluated/promoted/rejected/error counts）和 `NextEvaluation`（market/reason/due/priority）。

read-only pilot 的初始运行 SLO：WS 正常时新市场事件分钟内入库，WS 故障时最迟 5 分钟发现；
near-eligible crossover 因两次防抖最迟 10 分钟确认；全 active universe 的 coverage gap 不超过
6 小时；同一 material trigger 的 duplicate episode 为 0。未达到 SLO 只说明 scanner degraded，
不能自动放宽阈值或扩大并发。

cursor 的 operational 发布采用 durable inbox：完整 scan 先把 receipt、pending cursor 与所有
selected work 原子落库；worker 按 lease claim，逐项 ACK。只有该 receipt 全部 ACK 后才更新
lane 的 committed cursor。失败或 bounded scan 保留 immutable attempt receipt，但不推进
committed cursor；重启从未 ACK work 继续，而不是重新猜测已处理范围。

周期 lane 与 market re-evaluation 不复用这套 inbox。V3 增加两套独立 operational queue：

- `CadenceWorkOrder` 以 policy release clock 为锚点，把漏掉的多个 tick 收敛成“最近一个 due
  slot”，避免停机后制造历史任务风暴；`GAMMA_DELTA` 独占自己的 mutex，`FULL_ACTIVE_CENSUS`
  与 `DAILY_ANTI_ENTROPY` 共享 `UNIVERSE_RECONCILIATION` mutex；
- `aca_cadence_policy_current` 只允许最新 policy 产生/claim 工作；更新为 disabled policy 会
  supersede 尚未开始的旧工作，历史 policy replay 不能回滚 desired state；
- `NextEvaluation` 仍是 immutable schedule，`aca_next_evaluation_current` 每个 market 只投影
  最新 schedule；due worker claim lease 后必须用 `due_at` 之后的新 frozen facts 重评，写入
  新 observation/history/next schedule 与 ACK 旧 work 在同一事务完成；
- scan cursor ACK、cadence ACK 和 evaluation ACK 三者语义隔离。任何 queue 的 ACK 都不能冒充
  另外一条事实链完成；lease 判定使用 scheduler 注入的独立 operational clock，不拿可回填的
  source/facts 时间冒充 ACK 时间；lease 过期可回收，超过重试预算进入显式 `DEAD_LETTER`。

这里仍然没有常驻 scheduler。外部 owner 只能注入 frozen clock/page/facts 并调用 coordinator；
是否启动 5 分钟/6 小时/24 小时 operational cadence 仍属于 WP5 单独审批。

### 7.6 Candidate re-entry 用 episode，不复活旧终态

当前 Candidate lifecycle 的 `REJECTED`/`SIMULATION_RECORDED` 是终态。重新满足资格时不能改写
或复活旧 Candidate；新增 additive `MarketAdmissionEpisode`：

```text
episode_id = hash(market_id, eligibility_transition_id,
                  admission_trigger, eligibility_policy_hash)
```

一个 episode 生成一个新的 Candidate lineage，旧 episode 原样保留。允许新 episode 的 material
trigger 仅包括：首次发现、rule/lifecycle revision、marketability crossover、new public evidence、
probability TTL、显著 price displacement、held-position bootstrap 或 policy version change。
普通 book tick 只重算 comparison，不能制造无限 Candidate 或重复 LLM research。

新 episode 不等于必然重做 research。若 market/rule/evidence identity 未变且 prior BlindResult 仍在
TTL 内，新 episode 写 `BlindResultReuseReceipt` 并直接获取 fresh formal book；只有 reuse gate 不通过
时才进入 research queue。这样既允许“以前无 edge、现在有 edge”重新入选，又不为价格噪声反复
支付研究成本。

### 7.7 盘口微观结构：先做研究证据，不做准入或资金信号

每份 `BookMicrostructureFeatureRecord` 只接受一个已 `ACCEPTED` 的
`BookCaptureReceipt` 和其精确绑定的 paired `OrderbookSnapshot`。PIT 可用时间固定为
`receipt.received_at`；调用时 `as_of` 只负责证明当时已经可见，不进入静态特征 identity，
所以同一 snapshot/receipt/policy 在未来重放仍得到相同 record id/hash。

V1 冻结以下 Decimal-only 特征：

- YES/NO best bid/ask、mid、spread、top size；
- policy top-N 的 size、notional、depth imbalance 与 size HHI；
- `yes ask + no ask - 1`、`1 - yes bid - no bid`；
- 固定 target size 的四条 BUY/SELL effective-price impact，以及 complete-set paired buy
  premium / sell discount；不足 depth 的 target 显式列入 `insufficient_target_sizes`，不补值；
- source/capture 到 receipt 的 latency、stale/one-sided/crossed/clock quality quarantine。

这些字段现在只允许做离线分层、样本诊断和 frozen-forward 假设生成；不得写入
`MarketabilityFacts`、`MarketComparisonV2`、rank、allocator、replacement 或 live gate。
在有连续 PIT 样本、同分母 market baseline、稳定性和 out-of-sample 增量证据前，盘口不平衡、
HHI、momentum 都不能被解释成 alpha，更不能推断 queue priority、fill probability 或 adverse
selection。

本轮用封存 paired-book fixture 跑通了静态样本：状态 `USABLE`，YES spread `0.05`，bid/ask
size HHI 分别 `0.52/0.68`，top complete-set ask premium `-0.02`；target=5 的 depth-aware
paired buy cost `1.018`、premium `+0.018`，target=11 明确标为 insufficient。这个结果只验证
计算、PIT、hash 和缺深度语义，不代表真实市场收益。此前 5-market pilot 的原始工件仅存于已
清理的 `/private/tmp` 路径，仓库证据 seal 只有汇总和 hash，因此本轮没有拿汇总数字伪造逐档
特征。下一次获批 read-only capture 应直接 append feature record，并至少保留两个 receipt
时间点后再研究 temporal delta/volatility。

## 8. 现有持仓接管与机会替换

### 8.1 先把已有仓位变成强制评估对象

ACA 启动时，所有 active position、open order 和 redeemable position 都必须进入 capital universe，
不受 broad-scan eligibility filter 排除：

- redeemable/complete-set/open-order 先走确定性 release lane；
- active holding 建立 `HELD_UNDER_REVIEW` admission episode；
- 对持仓做 price-blind research 时隐藏 account、方向、shares、cost basis 和历史盈亏；
- research 未闭合前，只允许 redeem、risk/data review 和保守的 liquidity 标记，不给出基于伪概率
  的强制换仓结论；
- 旧仓若无 executable bid，标记 `CAPITAL_LOCKED`，不能把 mark 或理论 notional 当成可释放现金。

这不是“把旧池仓清掉换成新池”。旧仓、free cash 和新机会是同一次优化里的可选动作，
`do nothing` 永远是正式 baseline。

### 8.2 用组合重优化决定 keep/reduce/replace

allocator 对每个 rebalance 同时建立：

- 旧仓的分档 sell bid curve 和继续持有的 terminal payoff；
- 新机会的分档 buy ask curve；
- free/reserved cash、release time、fee、slippage；
- market/event/semantic/maturity caps 和 scenario loss；
- turnover/model-risk penalty。

然后在硬约束内最大化 conservative portfolio utility：

```text
maximize
    conservative_expected_pnl(after_execution)
  - concentration_penalty
  - maturity/liquidity_penalty
  - model_risk_buffer
  - residual_turnover_uncertainty

subject to
    cash_after >= cash_buffer
    market/event/cluster/maturity caps
    sell_size <= executable_bid_depth
    buy_size  <= executable_ask_depth * participation_cap
```

输出可以是 `KEEP`、`REDUCE`、`EXIT`、`ENTER` 或一个明确的 partial `REPLACE`。优化器按 marginal
depth 分段决策，因此不会假设旧仓能按 mid 全部卖出，也不会要求整仓换掉。
其中 fee、spread 和可见 slippage 已进入 bid/ask curves，不能再在 penalty 中重复扣减；
`residual_turnover_uncertainty` 只覆盖深度之外尚未定价的执行不确定性。

### 8.3 换仓最低门槛与防 churn

对每个 proposal 同时保存 `do_nothing_plan` 和 `after_plan`：

```text
switch_delta_ev = conservative_EV(after_plan)
                - conservative_EV(do_nothing_plan)

switch_return = switch_delta_ev / executable_capital_released
```

普通换仓只有在以下条件全部满足时才输出 `REPLACE_REVIEW`：

- sell/entry 两边的 fee、spread、slippage 和 depth 已按同一 as-of 计入；
- `switch_return >= max(3pp, calibration_error_95, unpriced_cost_buffer)`；
- after-plan 的 hard caps、cash buffer 和 stress loss 不劣化；
- 新旧 research freshness 可比较，或旧仓已明确 `RESEARCH_STALE` 并先完成 refresh；
- 同一 replacement 连续两次 plan tick 成立且跨越至少 15 分钟；
- 普通成交后 6 小时内不因小幅价格摆动反向换回。

`REPLACE_REVIEW` 只是 shadow 建议，不能自行写入 `last_executed_at`。只有独立 execution owner
提供外部 fill/receipt 引用后，才能重置 confirmation streak 并启动 6 小时 cooldown；未成交的
review 不得冒充成交。

shadow 初值 3pp、15 分钟和 6 小时都必须在 frozen-forward 中单独做 turnover sensitivity；
resolved/redeemable、rule invalidation、risk breach 和数据错误不受 churn cooldown 阻挡。

### 8.4 资金替换的执行顺序与血缘

每轮先后顺序固定：

1. redeem/merge 可确定释放的资本；
2. resolution、invalidated rule、hard-cap breach 等 mandatory review；
3. 取消 stale open-order 的 review；
4. 用 free cash 给净优势最高的新机会分配；
5. 再比较旧仓 marginal hold value 与新机会 marginal value，做 partial replacement；
6. 未达到 switch buffer 的资金保持 cash 或原仓，不为提高 utilization 强行换仓。

`ReplacementRecommendation` 必须绑定 old position lot/fill ids、sell curve receipt、预计释放现金、
new Opportunity/plan ids、buy curve receipt、before/after risk、switch delta 和 blocking reasons。
后续实际 execution 即使另行获批，也只能把“已成交释放的现金”用于新腿；旧腿未成交时不能假设
资金已经可用。历史仓位和旧 Candidate 只追加状态，不删除、不改写。

## 9. Agent 的研究预算与自调度

Coordinator 不按“最新市场最有趣”排队，而按近似 Value of Information（VOI）排序：

```text
priority =
    decision_impact_usd
  * uncertainty_reduction_potential
  * urgency
  * data_completeness
  / estimated_research_cost
```

其中：

- 已有大仓位且 research stale，优先级高；
- redeemable/complete-set 不需要 LLM，直接走确定性 capital-release lane；
- 新 Candidate 的 possible edge × executable capacity 是研究价值上界；
- 研究成本超过可改变决策的价值上界时，直接 `PASS_LOW_VOI`；
- 每次调度保存 jobs、tokens、searches、elapsed time、source coverage 和 terminal disposition。

## 10. 状态机与人类审批

```text
SNAPSHOT_PENDING
  -> DATA_COMPLETE
  -> OPPORTUNITY_JOINED
  -> PLAN_COMPUTED
  -> RISK_CHECKED
  -> HUMAN_REVIEW_REQUIRED
  -> SHADOW_RECORDED
  -> OUTCOME_OBSERVED
  -> EVALUATED
```

显式非推进状态：

```text
DATA_INCOMPLETE
RESEARCH_STALE
BOOK_STALE
RULE_INVALIDATED
POLICY_CONFLICT
RISK_BLOCKED
RETURN_QUARANTINED
APPROVAL_REJECTED
```

所有状态 append-only；同一输入和 policy 重放必须得到同一 plan hash。长等待、人工审批、
进程重启和 provider retry 均从 immutable checkpoint 恢复，不重做已完成副作用。

V1 的人工卡片必须同时显示：

- 这是 held position 还是 new opportunity；
- 概率区间及来源 freshness；
- all-in cost/exit value 和 depth；
- hold/new incremental EV，而非 gross “若赢收益”；
- before/after concentration 与 cash buffer；
- blocked/unknown 字段；
- 复制可复放的 plan/policy/input ids。

## 11. Evals 与治理

### 11.1 三套 eval，不能只看最终 PnL

1. `Research eval`：Brier、log loss、calibration、interval coverage、source/PIT coverage；
2. `Portfolio eval`：fee-adjusted PnL、capital-days、drawdown、concentration、turnover、
   opportunity capture、hold-vs-exit regret；
3. `Agent eval`：任务成功率、quarantine/refresh/人工介入率、重复/陈旧 work、token/search/
   elapsed cost、crash-resume 幂等性。

### 11.2 基准

每个结果使用同一 PIT opportunity set 比较：

- `cash-only`；
- `current holdings / do nothing`；
- `edge rank only, equal size`；
- `capital-aware allocator`；
- 可选 `market-implied probability baseline`。

不允许用事后全市场最优组合冒充可交易 baseline。regret 只能在当时已发现、已研究、当时
book 可执行的同一集合内计算。

### 11.3 晋级条件

`read-only unified view -> shadow allocator`：

- account snapshot coverage 明确；缺 cash/open orders 时 plan fail closed；
- Blind leakage adversarial tests 全过；
- same-input replay 得到相同 plan hash；
- complete-set、mutual-exclusion、stale book、partial depth、clock skew、duplicate plan 测试全过。

`shadow allocator -> daily read-only pilot`：

- AGR 正式 Gate R pilot 先完成；
- 单独通过现有 `READ_ONLY_OPERATIONAL_PILOT_GATE`；
- scheduler/load/storage/weather isolation/rollback 证据闭合；
- 仍然 `NO_ORDER`。

`shadow -> human-approved execution preview`：

- frozen-forward 样本覆盖足够的 resolved events 和不同市场类别；
- probability calibration、net PnL/capital-day、drawdown、turnover 均优于预注册 baseline；
- 独立 model/allocator validation 完成，限制和失败模式入册。

任何真实 order/signing 都是之后的独立项目和显式授权，不由本策略自动获得。

### 11.4 2026-08-30 P0 reliability/cost closure

- scheduler 增加 v2 durable inbox、lease/reclaim/ACK、连续 cursor chain 与 immutable
  `EligibilityHistoryCheckpoint`；历史 replay 不能倒退 current projection；
- Gate R 与 decision ledger 统一使用 additive `MarketComparisonV2` 和 Decimal 逐档
  `p * (1-p)` taker fee cost curve；ledger 重新对 frozen book 验算 fills，且 cost policy 必须显式
  提供，不一致时 fail closed；旧 V1 comparison payload 保持可读；
- 影响审计：当前保存的 Alpha/ACA pilot DB 中 `MarketComparison=0`、由该旧 flat-fee 路径产生的
  decision/order 均为 `0`；2026-08-30 的 5-market shadow pilot 使用独立的正确 fee 公式，重放
  口径下 `conservative_edge >= 3pp` 仍为 `0`、capital action 仍为 `DATA_BLOCKED`，因此污染清单为空；
- 本 closure 仍为本地 `READ_ONLY_SHADOW / NO_ORDER`，没有启动定时器或变更生产 desired state。

## 12. 实施路线

| Work package | 当前状态 | 边界 |
|---|---|---|
| WP0 contracts/storage | `IMPLEMENTED_OFFLINE_V3` | append-only facts + scan/cadence/evaluation durable projections；真实 adapters 未启用 |
| WP1 universe scheduler | `IMPLEMENTED_DURABLE_OFFLINE` | collapsed cadence、policy current、lease/ACK/committed cursor；无常驻网络 owner |
| WP2 cockpit | `PENDING` | 不在本次离线核心变更内 |
| WP3 allocator/replacement | `IMPLEMENTED_SHADOW_V1` | plan/review only，最多一个 replacement/plan，永久 `NO_ORDER` |
| WP4 coordinator | `IMPLEMENTED_CALLER_DRIVEN` | crash-safe scan/cadence/due-evaluation 编排；无 daemon/network/LLM 自动调用 |
| WP5 scheduled pilot | `GATED` | 需 Gate R 与 read-only operational pilot 单独批准 |
| WP6 execution | `OUT_OF_SCOPE` | 未实现、未授权 |

### WP0 — Contract and data closure

- 新增 ACA contracts 与独立 additive plan DB；
- 把 market20 临时 selector 还原为 versioned `EligibilityPolicy` fixture，锁定 reason/threshold
  semantics；
- 新增 UniverseMarketState、MarketabilityObservation、EligibilityTransition、
  MarketAdmissionEpisode 和 scan receipts；
- adapter 读取 Alpha frozen contracts、account ledger 和现有 book receipts；
- 定义 authenticated-complete 与 public-only coverage；
- 不启动 scheduler、不请求新网络数据。

验收：fixture replay、hash/clock/account identity、跨账户污染、missing cash/order fail-closed。

### WP1 — Offline universe scheduler

- 用保存的 Gamma/CLOB artifacts 重放 full census、delta overlap、formerly-ineligible crossover、
  hysteresis、episode creation 和 crash resume；
- 验证 keyset `updatedAt,id` ordering/cursor 合同及 fallback；
- 验证同一事件不会重复 Candidate/research，漏 WS 可由 full census 补齐；
- 不启动常驻网络任务。

验收：合成时钟下 cadence、lease、watermark、backoff、late event、完整覆盖与确定性 hash 全过。

尚未完成的 anti-entropy 证据合同：daily/full receipt 还需追加 expected/seen/missing/extra market
manifest（或这些集合的 hash），才能证明 universe reconciliation 完整，而不只是证明 scan
过程 coverage complete。该缺口不阻塞离线 cadence/lease 验证，但阻塞 WP5 的 coverage SLO seal。

### WP2 — Unified read-only cockpit

- 把当前 Capital Efficiency 页面升级为三栏：账户资金、已有持仓复核、AGR opportunity queue；
- 增加 universe funnel：discovered、dormant、near eligible、newly eligible、research admitted、
  active Candidate，以及每个失败 reason 的 next due；
- 展示 mark/liquidation/scenario NAV、cash buffer、event/cluster/maturity exposure；
- 仍无 sizing 和 action capability。

验收：同一 snapshot 的 API、CLI/JSON、UI 数字一致；public-only 明确不闭合。

### WP3 — Deterministic shadow allocator and replacement

- 实现 hold-vs-exit、capital release、fractional-Kelly + hard-cap allocator；
- 把 existing holdings、free cash 和 new opportunities 放入同一 depth-aware re-optimization，
  输出 keep/reduce/partial replace 与 do-nothing delta；
- 输出 immutable CapitalPlan 和 human cards；
- 接入现有 NO_ORDER Prediction/learning lineage，但不改 AGR probability。

验收：scenario/property tests、整数/Decimal、depth curve、互斥事件、幂等和 crash replay。

### WP4 — Agent coordinator

- 在现有 work-order/lease/usage harness 上组合 Discovery、Capital、Evaluation lanes；
- 只让 LLM 执行 research/semantic proposal/explanation；
- 增加 VOI budget、pause/resume、HITL 和 trace/evidence receipts；
- 不建立第二套 Candidate 或 task truth。

验收：partial provider failure、stale evidence、retry exhaustion、approval reject、cost overrun、
no duplicate work。

### WP5 — Read-only scheduled pilot and frozen-forward evaluation

- 预注册 baseline、policy、样本和时间窗口；
- 单独审批后才按第 7.2 节 cadence 启动 bounded read-only pilot；先 24 小时 canary，再 7 天
  stability window；
- 记录每 lane 的请求量、429/5xx、延迟、coverage、promotion/re-entry、重复抑制与 storage growth；
- 同时记录 selected 与 non-selected Opportunity；
- 结算后做 calibration、portfolio outcome、regret、资本占用和 agent cost 评估。

验收：同分母、PIT、fee-adjusted、按 event/date block 的不确定性报告。

### WP6 — Execution gate（不属于 V1 授权）

- 只有单独批准后才设计 order intent、pre-trade risk、human approval 和 kill switch；
- signing owner 独立，ACA 只能提交有 expiry 的 proposed intent；
- 每单与 aggregate capital/market/event/cluster limits 均在 tool 调用前 blocking 检查。

## 13. 业界工作流借鉴

不建议现在引入一个重型 agent framework 重写现有系统。借鉴模式，复用已有 append-only
contracts、scheduler、artifact store 和 harness：

| 业界实践 | 本项目采用方式 |
|---|---|
| workflow-first、复杂度渐进 | 固定 DAG 和 deterministic gates；只在开放式研究使用 agent |
| single coordinator + bounded specialists | 一个 ACA coordinator，AGR research 作为受控 work order，不建 swarm |
| tool/input/output guardrails | 每个 tool seam 都做 schema、identity、freshness、capability guard，不只检查最终文本 |
| durable pause/resume + HITL | immutable work order、lease、checkpoint、approval receipt、resume |
| evals before model downgrade/scale-up | 先建立强模型 baseline，再用成本更低模型做相同 eval |
| model inventory/independent validation/outcomes analysis | probability、calibrator、allocator、cluster proposal 分别登记、验证和持续监控 |
| pre-trade capital/size/duplicate controls | 即使以后 live，也在 order tool 之前做 aggregate + per-order blocking controls |

参考原始资料：

- OpenAI, [A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)
- Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- OpenAI Agents SDK, [Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/) 与 [Guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- LangGraph, [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)（借鉴 checkpoint/fault-tolerance 模式，不代表必须采用该框架）
- Federal Reserve/OCC, [SR 11-7 Model Risk Management](https://www.federalreserve.gov/boarddocs/srletters/2011/sr1107a1.pdf)
- SEC, [Rule 15c3-5 Market Access Risk Controls](https://www.sec.gov/rules-regulations/2011/06/risk-management-controls-brokers-or-dealers-market-access)（作为工程控制模式参考，不宣称其直接适用于本项目）
- Polymarket, [Market WebSocket Channel](https://docs.polymarket.com/api-reference/wss/market)（实时 book/price/new-market/resolution 事件及动态订阅）
- Polymarket, [Events keyset pagination](https://docs.polymarket.com/api-reference/events/list-events-keyset-pagination) 与 [Markets keyset pagination](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination)（全量/增量 census 的稳定 cursor 基础）
- Polymarket, [User WebSocket Channel](https://docs.polymarket.com/api-reference/wss/user)（authenticated order/trade event；只由既有 account owner 消费）

## 14. 最终产品定义

ACA V1 的完成定义不是“会聊天”或“能扫很多市场”，而是：

```text
在不泄漏 Blind research、不复制事实 owner、不拥有下单能力的前提下，
持续、增量地维护完整 market universe，允许动态恢复资格的市场建立新 admission episode，
并从同一 PIT 的账户快照、现有持仓与 AGR Opportunity 出发，
确定性地产生可重放、受风险约束、可人工审核的资金计划，
并在结算后量化概率质量、组合收益、capital-days、regret 与 agent 成本。
```

这才是市场挖掘与资金利用率合并后的完整闭环。
