# Core Carry 外部二次审阅包与提示词（2026-08-23）

Status: `current consultation packet / no live authorization`

> 目的：向上一次审阅者准确汇报审阅后的实现，区分“数据能否被相信”、
> “盘口处于什么状态”和“该状态应采取什么执行动作”，请其审计目前的解释与实现是否偏离原意。
> 本文不宣称 market-state alpha 已成立，也不授权改变真实下单行为。

## 一句话结论

上次审阅后，我们已经把双 maker exposure 收敛为共享 5-share budget，建立了 selective WS tape/full-ladder
采集、确定性盘口重建、最小 market-state classifier、五路径同分母 counterfactual 与 zero-notional forward gate。
但当前 classifier 只完成了原合同的最小子集：有 current-token trade flow、120s shock/recovery 和 ladder
`q_up/q1/alpha1`，尚无 edge 穿越归因、add/cancel OFI、support survival、up-rung active flow、maker queue/fill
hazard 和 information-risk context；并已发现 one-sided horizon book 仍可被误标 `healthy`。因此现在适合做一次
设计审阅，而不是继续把不完整 v1 直接推向 live。

## 1. 上一次审阅后的动作

### 1.1 真实执行边界

- 旧行为：`10 taker + 5 staged maker + 5 pullback maker`，两个 maker arm 可在同一 signal 叠加，最大 20 shares。
- Shanghai 2026-08-22 实际吃满：`10@0.87 taker + 5@0.86 staged + 5@0.85 pullback`；总计 20 shares，
  principal `$17.25`。staged fill 后约 239 秒 midpoint markout 为 `-4.5c/share`。
- 已部署行为：`10 taker + 共享 5 maker`。staged 未成交且确认撤单后，pullback 才能接管同一个 root；
  最大 exposure 从 20 降至 15 shares。
- 这只修复双 arm exposure bug，保留 maker 的价格改善/成交能力；**没有证明 maker adverse selection 已解决**。
- 上次审阅建议“默认 maker 退出生产资金，只留 1–2 shares probe”。我们没有完全采纳，因为当前产品目标仍希望
  保留 5-share maker execution sleeve；这是有意识的部分采纳，不应包装成已经遵从审阅结论。

### 1.2 数据与 as-of contract

已经完成：

- immutable first-positive decision packet：信号、last negative、first positive、forecast/book/raw clock、模型/配置 hash；
- first-positive 后 30 分钟 full-ladder YES-token capture demand；
- candidate-stage current-token capture，以及在固定 token budget 内对一个最接近触发的候选抓 full ladder；
- append-only subscription epoch、raw WS frame、exchange/receive clock；
- `book/snapshot` 基线 + `price_change` delta 的确定性重建；reconnect/gap/out-of-order/parity fail closed；
- immutable reconstructed `book_snapshot_id`、baseline/delta hash 与 producer build lineage；
- capture group atomic allocation：full ladder 要么完整接纳，要么完整拒绝。

尚未完成：

- `weather_time_edge_delta / market_feature_edge_delta / ask_cost_edge_delta` 和 old-ask/old-market counterfactual；
- actor/wallet recurrence；
- 多年 station-level next-report hazard；
- Amsterdam KNMI→METAR state-space nowcast；
- policy-conditional intercept-only calibration；
- 同日第一次 overshoot 后关闭后续高风险第二 sleeve 的 portfolio replay。

### 1.3 market-state v1 已实现的计算

在 event-time 到 120s 窗口，当前代码计算：

- current bid/ask midpoint 的 baseline、窗口最低值、120s 值、shock、recovery fraction；
- exchange-reported `last_trade_price` 的 current-token BUY/SELL volume 与 imbalance；
- full-ladder spread-weighted simplex：`q_up / q1 / alpha1` 及 120s delta；
- bid-side depth / 5-share order 的 `support_depth_ratio`；
- 输出候选状态：`healthy_passive / transient_dislocation / cross_bracket_adverse /
  persistent_adverse / mixed / unknown`；
- 所有状态仅写 zero-notional shadow，不创建 TradeIntent、plan、order 或 venue call。

已冻结五条同分母 execution 路径：

1. immediate taker；
2. actual shared maker（只认真实 accepted maker order 与真实 fills，不做 future-touch fill simulation）；
3. 120s confirm current；
4. 120s next-bracket hedge；
5. skip incremental sleeve。

主指标为 router 相对 actual shared maker 的 1800s fee-adjusted implementation surplus，并同时保留相对 immediate
taker 的差值；按 target_date block bootstrap。

### 1.4 当前 v1 明确缺失或实现不足

| 原审阅要求 | 当前状态 | 问题 |
|---|---|---|
| first-positive edge 原因分解 | 未实现 | 还不能区分 weather/time-driven 与 ask 被打低导致的 quote-driven trigger |
| current active sell | 部分实现 | 有 exchange-reported BUY/SELL volume，但没有 trade count、size distribution、burst intensity |
| upper-rung active buy | 未实现 | 只有 ladder midpoint mass delta，没有“current sell 与 next/up buy 同时发生”的 flow join |
| add/cancel 与 OFI | 未实现 | 无 cancellation-adjusted OFI、support add/cancel velocity |
| support quality | 弱 proxy | 当前 `support_depth_ratio` 求和范围接近全部 bid depth，不是 top-3/挂价附近支撑；无 age/survival |
| maker queue/fill hazard | 未实现 | 只有事后 actual order/fill join；决策时没有 queue ahead、fill hazard、time-to-fill state |
| 30/60/120/300/900/1800 state path | 部分实现 | classifier 主要只用 120s；其他 horizon 目前主要保存 current-mid markout |
| report phase / forecast divergence | 未实现 | 未接距下一报告、forecast revision、late-high/reheat hazard |
| one-sided book fail closed | 有 bug | horizon snapshot 可存在但 midpoint 为 null，当前仍可能落入 `healthy` |
| state 阈值验证 | 未完成 | 1 tick shock、1pp q-up、50%/80% recovery 是简单预注册值，无 forward surplus 证明 |

## 2. 两个实际样本说明当前边界

### 2.1 Seattle candidate：机制路径确实运行

- baseline midpoint `0.805`；窗口最低 `0.710`；120s `0.720`；recovery `10.5%`；
- current exchange-reported SELL `5 shares`，BUY `0`；
- `Δq_up=+6.70pp`，`Δq1=+3.09pp`；
- v1 输出 `cross_bracket_adverse_candidate`。

这是符合原假设的“current 被卖、价格持续恶化、上方概率质量增加”案例。但它是 candidate-stage 样本，不是
first-positive 实盘 signal，也没有证明按该状态 skip/hedge 会产生正 implementation surplus。

### 2.2 San Francisco first-positive：暴露两层不同问题

- current BUY prints `60`，SELL `0`；`Δq_up=-0.95pp`；v1 输出 `healthy_passive_candidate`；
- 但 120s reconstructed horizon book 是 one-sided，`horizon_mid=null`；当前分类器仍允许 `healthy`，这是 state
  evidence 的 fail-closed 缺口；
- 1800s subscription/reconnect transport 不完整，但旧 performance gate 仍使用 as-of terminal mid 把它计为
  `complete`；这是 evaluation coverage bug，不是 market-state alpha 问题；
- 修复后只读 replay：`1 forward signal / 0 complete / 0 target dates / coverage 0%`，该行保留在分母但不进入 paired CI。

针对 1800s coverage bug，已准备 commit `176e17e9`：六个固定 horizon 必须全部 `transport_verified`，且 terminal
current book 必须双边可估值，才能进入 performance/promotion。15 个 focused tests 通过；截至本文快照，production
仍运行旧 release `56c04c05`，该修复尚未重启部署。

## 3. 对上次“最终执行顺序”的逐条采纳状态

| 上次意见 | 采纳状态 | 我们实际做了什么 |
|---|---|---|
| 停默认 maker sleeve，保留 taker core | **部分采纳** | 由两个 5-share maker arm 收敛成共享 5 shares；没有退到 0 或 1–2 probe |
| 修 canonical/as-of contract | **大体采纳** | decision packet、capture demand、epoch/raw frame、确定性重建和 parity 已落地 |
| 重建 edge 原因分解 | **未采纳/待做** | 尚无 quote-driven attribution |
| 建 `q_up/Δq_up/alpha1` | **部分采纳** | baseline→120s simplex delta 已实现；无多 horizon path、up-rung active flow |
| replay core / confirm / hedge | **基础设施完成、证据未形成** | 五路径 evaluator 已有；有效 first-positive paired rows=0 |
| policy-conditional intercept calibration | **未做** | Core probability 未改 |
| 同日 overshoot portfolio stop | **未做** | 尚无固定 replay |
| token tape + trigger-window ladder | **已部署采集** | selective P0/P1 capture 正在累积；coverage 仍受 token budget/reconnect 影响 |
| actor/adverse-flow score | **部分采纳** | simple flow/state candidate 有；actor、OFI、support survival、fill hazard 没有 |
| Amsterdam nowcast | **未做** | 不在本轮 execution P0/P1 主线 |
| station late-high hazard | **未做** | 保留未来概率层方向，未与 execution state 混合 |

## 4. 当前生产与 forward 快照

快照：2026-08-23 11:45 CST，Mac production。

- `weather_market_books`、Core tiny-live、market-state zero-notional shadow 均健康；canonical DB route healthy；
- market-state decision journal 10 rows：9 candidate、1 first-positive；全部 `declared_notional=0`、
  `trade_intent_created=false`、`venue_call_allowed=false`；
- 修正版口径：1 first-positive、0 evidence-complete、0 independent target dates、0 paired surplus rows；
- promotion gate 必须保持 FAIL；距 30 个独立 forward target dates、coverage≥95%、paired CI lower bound>0 尚远；
- 当前 market-state 绝不应控制 live maker。

## 5. 可直接复制给 GPT Pro 的二次审阅提示词

```text
你上一次审阅了我们的 Polymarket weather Core Carry 策略，并把首要问题判断为 first-positive 停止时点的
逆向选择，而不是继续提高全局天气概率精度。你建议：修复 as-of decision contract；采 filtered token tape 与
trigger-window full ladder；分解 edge 穿越原因；计算动态 q_up/q1/alpha1 与 active flow；让第二 sleeve 在
passive maker / event-time confirm / next-bracket hedge / skip 间路由；默认 maker 退出生产资金；先 zero-notional
forward，再以同分母 implementation surplus 晋升。

请对下面“审阅后实现”做严格的第二轮设计审计。不要因为系统已经部署就默认方案正确，也不要再泛泛建议
增加更多复杂模型。请特别区分：

A. coverage/reconstruction：我们是否可靠看见了盘口；
B. market-state inference：可靠看见后，盘口到底处于什么机制状态；
C. execution policy：该状态下，第二个最多 5-share sleeve 应 maker、confirm、hedge 还是 skip。

【审阅后已实现】
1. 旧 10 taker + 5 staged + 5 pullback 改为 10 taker + 共享 5 maker；staged 撤单确认后 pullback 才能接管，
   最大 exposure 20→15。我们没有完全采纳“maker 降为 0 或 1–2 probe”，因为仍希望保留价格改善能力。
2. 建立 immutable decision packet、first-positive full-ladder 30m capture、candidate-stage selective capture、
   append-only subscription epochs/raw WS frames，以及 snapshot+delta deterministic reconstruction；
   reconnect/gap/out-of-order/parity fail closed。
3. 当前 market-state v1 在 event→120s 计算：
   - current midpoint baseline/min/120s、shock、recovery；
   - exchange-reported current-token BUY/SELL volume 与 imbalance；
   - full-ladder spread-weighted simplex q_up/q1/alpha1 及 120s delta；
   - 一个 bid-depth/order-size support ratio；
   - 输出 healthy/transient/cross-bracket-adverse/persistent/mixed/unknown candidate state。
4. zero-notional evaluator 固定比较 immediate taker、actual shared maker、120s confirm current、120s next hedge、
   skip；maker fill 只认真实 accepted order/fill，不用 future touch。主指标是 1800s fee-adjusted router-minus-maker
   implementation surplus，并按 target_date block bootstrap。
5. 所有 router 结果只写 telemetry，不创建 TradeIntent、plan、order 或 venue call。

【明确未实现/实现不足】
- 没有 weather_time / market_feature / ask_cost edge 穿越归因和 quote-driven 标签；
- 没有 add/cancel volume、cancellation-adjusted OFI、top support age/survival；
- 没有 current active sell × next/up active buy 的同步 flow，只看 current prints 和 ladder midpoint mass；
- 没有 trade count/size/burst、maker queue-ahead/fill hazard；
- support ratio 当前接近全部 bid depth，不是真正 top-3 或挂价附近支撑；
- classifier 主要只使用 120s，30/60/300/900/1800 主要只是 markout telemetry；
- 没有 expected-next-report phase、forecast-actual divergence、late-high/reheat context；
- threshold 仍是简单预注册值：1 tick shock、q_up +1pp、recovery 50%/80%，没有 forward surplus 证明；
- one-sided 120s book 仍可能被判为 healthy，这是已确认的 fail-closed bug。

【两个实样本】
Seattle candidate：mid 0.805→min 0.710→120s 0.720，recovery 10.5%；current SELL 5、BUY 0；
Δq_up +6.70pp、Δq1 +3.09pp；被标 cross_bracket_adverse。它证明最小机制路径在跑，但不是 first-positive
实盘 signal，也没有 outcome/surplus 证明。

San Francisco first-positive：current BUY 60、SELL 0、Δq_up -0.95pp，被标 healthy；但 120s book one-sided、
horizon_mid=null。另有 1800s reconnect/transport gap，旧 evaluator 却从 as-of book 取 terminal mid 并误计 complete。
覆盖修复后，它保留在分母但被 blocked。当前正确 forward 口径为 1 signal、0 complete、0 dates、0 paired rows。

请按以下结构回答：

1. Verdict：我们的总体解释是否忠实于你上一次建议？哪些属于正确采纳、错误实现、过早实现或遗漏？
2. 三层边界审计：分别评估 coverage、market-state、execution policy；指出任何把“没采到”误当成“无卖压/健康”
   或把 coverage blocker 误当成市场风险信号的地方。
3. 最小可用 market-state v2：只给你认为在当前样本规模下不可缺少的连续字段、窗口和 fail-closed 条件。
   请明确哪些字段必须先实现，哪些可延期；避免 GBM/RF 或在少数 loss 上过拟合。
4. 状态定义审计：逐条修正 healthy、transient、cross-bracket adverse、persistent adverse、mixed、unknown。
   特别回答：没有 trade prints 是否可解释为无 flow？one-sided book、reconnect、选择性订阅时应如何处理？
5. Edge attribution：给一个最小、可 PIT 重放的 weather/time vs market-feature vs ask-cost 穿越分解；说明怎样定义
   quote-driven 而不制造新的价格 hard gate。
6. Order-flow 与 support：给出 current sell × up-rung buy、add/cancel OFI、support survival、maker queue/fill hazard
   的最小计算合同；说明哪些能从现有 WS frame 可靠推导，哪些必须有新的 exchange/order evidence。
7. Router/evaluation：审计 immediate taker、actual maker、120s confirm、next hedge、skip 的同分母比较是否正确；
   说明 unfilled opportunity cost、partial fill、cancel/replace、terminal markout、settlement PnL 应如何记账。
8. Maker policy：在我们坚持保留最多 5-share maker 价格改善能力的约束下，你是否仍建议立即降为 0/1–2 probe？
   如果不是，请给出可证伪的最小准入条件；不要用事后 price/p/hour 矩形阈值。
9. 下一执行顺序：给出最多 6 步的 P0/P1/P2 顺序。每步写明代码/数据产物、验证方式、停止条件，以及它是
   data readiness、mechanism validity 还是 live promotion evidence。
10. 明确列出：现在绝对不能声称什么；达到什么 forward 证据后才可让 router 控制真实 shared 5-share sleeve。

约束：
- 保留 frozen Core probability model，不在约 80 个 signals/7 losses 上训练复杂模型；
- 不新增事后价格阈值；
- coverage gap 必须留在固定分母中，但不得被解释为 adverse 或 healthy；
- historical Warsaw/Amsterdam/Shanghai 仅为机制 anchor，不用于冻结后验证；
- router 当前必须保持 zero-notional；
- live 晋升至少要求 30 个独立 forward target dates、coverage≥95%、actual maker paired surplus 完整，且
  target-date block CI 下界>0；如果你认为这些门槛仍不充分，请指出缺少什么。

最后请给一句明确判断：
“继续补完当前 v1” / “在继续采样前先重构 state contract” / “停止 maker expression，只保留数据采集”，
三者选择一个作为主建议，并说明决定性理由。
```

## 6. 本地代码与证据指针

- 上次审阅原文：[2026-08-21-core-carry-external-consult-gptpro-review-v1.md](2026-08-21-core-carry-external-consult-gptpro-review-v1.md)
- maker 全审计：[2026-08-20-core-carry-maker-adverse-selection-review-v1.md](2026-08-20-core-carry-maker-adverse-selection-review-v1.md)
- P2 合同：[2026-08-23-core-carry-market-state-execution-quality-p2-v1.md](2026-08-23-core-carry-market-state-execution-quality-p2-v1.md)
- classifier：`src/strategies/weather_edge_v1/execution/market_state.py`
- runner：`scripts/ops/weather_core_carry_market_state_shadow_v1.py`
- performance：`src/strategies/weather_edge_v1/execution/market_state_performance.py`
- market-state production release：`56c04c0532842de23853e3095889cf31bb2bb1b1`
- coverage fail-closed 修复候选：`176e17e9381d469a87cd2439b64eababe8bdeb95`
