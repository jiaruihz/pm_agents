# 从 MM 的第一性原理出发：Weather-first Market Making 研究与完整执行评审包

Status: `design-draft / GPT Pro review-ready / no-live-change`

Updated: `2026-08-30`

Scope: Polymarket weather liquidity、selective weather MM、generic two-sided MM 的研究与阶段设计

Owner question: 应优先做传统市场的中性双边做市，还是先在 weather 下用 maker 获取仓位、卖出/退出并管理库存，再逐步演化为做市？

Provisional decision: **weather-first 学习路径 + generic-MM-compatible 共享底座；两个策略头、三类机制与独立 PnL，不把 maker entry 直接称为做市。**

Safety boundary: 本文只授权研究、shadow、paper 与设计评审；**不授权真实下单、生产配置变更、资金划拨、split/merge/redeem 或 generic MM 上线。** 任何 tiny-live 仍需独立、明确确认。

Source cutoff: `2026-08-30 Asia/Shanghai`。平台 fee、rebate、reward、holding reward 和市场级参数会变化，执行时必须重新读取官方/venue truth，不能把本文数字硬编码。

---

## 0. 给 GPT Pro 的审阅任务

你现在是本项目的 Principal Market Microstructure Researcher、Adversarial Strategy Reviewer 与 Execution Architecture Gatekeeper。

请从 market making 的第一性原理重新推导，不要默认接受本文的 weather-first 结论，也不要因为仓库里已经有 PMM 代码就推定它有正收益。你要回答的不是“代码能不能跑”，而是：

> 在我们已有 weather alpha、真实订单/成交血缘、盘口采集、legacy PMM 与两个开源参考实现的条件下，哪条路径最可能形成**可验证、可执行、fee-adjusted、capital-adjusted** 的正期望？正确的阶段顺序、共享基建、策略边界、数据合同和 stop conditions 是什么？

请给出一个明确 disposition：

```text
ACCEPT_WEATHER_FIRST_STAGING
ACCEPT_WITH_REQUIRED_CHANGES
REWORK_FROM_FIRST_PRINCIPLES
```

### 0.1 你必须挑战的前提

- maker 订单不会创造预测质量；它只改变成交价格、成交条件与机会成本。
- “挂到了更好的价”不等于改善了 EV；fill 是内生选择，可能只在我们最错时成交。
- maker entry、passive exit、连续双边报价是三件不同的事。
- weather directional liquidity 与 generic neutral MM 可以复用执行底座，但不能共用一套盈利假设、研究分母、库存指标或 PnL。
- synthetic replay 跑通只证明软件路径可运行，不证明真实 queue、partial fill、adverse selection 或盈利。
- rewards、rebates、holding rewards 都是可变化、竞争依赖的收入；必须与 trading PnL 分账，不能用估算值当到账现金。
- quote touch/cross、下一时刻 BBO、resting duration 都不能单独当作真实 fill。
- 本文给出的 gate 是待审草案。请删除无意义的阈值、补上遗漏的 gate，并解释理由。

### 0.2 你必须交付的结果

请严格按第 14 节模板输出一份完整 Markdown，并至少完成以下工作：

1. 重新写出不重复计数的 MM 净收益恒等式。
2. 分别裁决 weather maker acquisition、weather inventory lifecycle、selective weather MM、generic two-sided MM。
3. 对本文“现有事实表”逐项纠错：已真实验证、只在 fixture/synthetic 验证、部分接线、尚缺失、明确不授权。
4. 排序所有可能盈利来源，并为每项给出最小可证伪实验、所需数据、最大风险与 kill criterion。
5. 给出唯一推荐 DAG 和阶段顺序；若 generic MM 应独立并行，明确共享层与隔离层。
6. 指出所有 look-ahead、fill-model、queue、fee、reward、capital、inventory 和 accounting 偏差。
7. 给出开始 controlled tiny-live 前的 blocking findings；不要把 live 当成本轮默认动作。
8. 给出 exact document/code changes，但不要在审阅回答中直接下单或部署。

---

## 1. Owner 问题与当前推荐

### 1.1 先把三个概念拆开

| 机制 | 决策对象 | 主要收益 | 主要风险 | 是否等于 MM |
|---|---|---|---|---|
| Weather maker acquisition | 已有 weather signal 要买多少；选择 maker/taker/skip | 价格改善、可能的 rebate | 漏掉正 alpha、条件成交逆选、信息时钟跳变 | 否，是 execution policy |
| Passive exit / inventory lifecycle | 已有仓位何时 hold/reduce/exit/flip；用 maker、taker、merge 或 settlement | 降低退出成本、回收资金、压缩尾部风险 | 退出不成、二次逆选、错误平仓 | 否，是 inventory/execution policy |
| Continuous two-sided MM | 每个市场持续生成 bid/ask TargetOrderSet | spread、完整集合/merge、rebate/reward、可能的 fair-value edge | 单腿成交、库存、跳价、被知情流 pick off | 是 |

因此，“在 weather 里用 maker 获取质量”需要改写为：

> weather 模型提供 signal quality；maker 只尝试改善 execution price。maker fill 可能让成交样本质量变差，所以必须和 taker/skip 在同一决策分母上比较。

### 1.2 当前推荐不是二选一

推荐结构是：

```text
共享 Market/Order/Execution/Accounting substrate
├── Weather Liquidity Head
│   └── P(outcome vector) -> TargetInventoryVector -> acquire/hold/reduce/exit/flip
└── Generic Two-sided MM Head
    └── fair value + flow + incentives -> TargetOrderSet
```

优先顺序是 **weather-first**，原因不是 weather 天生更适合做市，而是我们已经有 weather 的 PIT 数据、概率模型、真实 signal/fill/settlement 血缘和一批 maker 反例，能更快回答“maker 到底增加还是减少净 EV”。Generic MM 的市场筛选与 paper 研究可以在共享底座完成后并行，但它的资金、inventory、PnL 和 live gate 必须独立。

### 1.3 当前推荐允许失败

- Stage 1 maker acquisition 失败：保留 taker baseline，可继续研究 reduce-only passive exit；**不强行进入双边 MM**。
- Stage 2 inventory lifecycle 有价值、Stage 3 双边报价失败：weather 仍可是一套 directional liquidity strategy。
- Weather 全部失败：共享 truth/execution/accounting 层仍可供 generic MM 使用。
- Generic MM 只有 rewards 后为正：必须作为 incentive-dependent business 单独核算，不能写成纯 spread alpha。

---

## 2. MM 的第一性原理

### 2.1 最小决策单元不是一笔 fill，而是一个 QuoteDecision

一个可比较单元应是：

```text
QuoteDecision = market_state_as_of_t
              + information_state_as_of_t
              + own_inventory_and_orders_as_of_t
              + target_shares
              + maker/taker/skip counterfactuals
              + fixed horizon / terminal settlement
```

在同一个 `QuoteDecision × target_shares` 上比较：

```text
EV_maker_buy(q_bid)
  = P(fill | q_bid, state)
    × E[value_h - q_bid - maker_fee - exit_cost | fill, q_bid, state]
    + expected_incremental_incentive_allocation_forecast
    - quote/cancel/merge/ops_cost
    - unhedged_tail_cost

EV_maker_sell(q_ask)
  = P(fill | q_ask, state)
    × E[q_ask - value_h - maker_fee - replacement_cost | fill, q_ask, state]
    + expected_incremental_incentive_allocation_forecast
    - quote/cancel/merge/ops_cost
    - residual_inventory_tail_cost

EV_taker_buy(a)
  = E[value_h - a - taker_fee - exit_cost | state]

EV_skip = 0
```

其中 `value_h` 必须明确是固定短期 markout、可执行 unwind value，还是最终 settlement payoff。三者不能混写。`expected_incremental_incentive_allocation_forecast` 只用于 decision-time
policy 比较，必须有 model/config/epoch identity，**不进入 realized PnL**；actual incentive 只能在
episode/period ledger 按 payout identity 统一入账一次。

### 2.2 建议的净收益分账

先固定一个且仅一个 terminal valuation basis：`fixed-horizon executable unwind`、
`actual closed episode` 或 `final settlement`。Primary ledger 只用现金流和该 terminal
value 计算：

```text
Core trading PnL
  = trading cash received
  - trading cash paid
  + terminal inventory value under the single frozen basis
  - actual trading / hedge / merge / settlement fees

Net PnL
  = Core trading PnL
  + realized maker rebates
  + realized liquidity rewards
  + realized holding rewards
  - financing / capital charge
  - separately measured operational cost
```

其中 split/merge/redeem 的 principal transfer 是 balance-sheet reclassification，不是额外
trading income：

```text
split:  -1 collateral +1 YES +1 NO = 0 wealth change before fees
merge:  -1 YES -1 NO +1 collateral = 0 wealth change before fees
redeem: -winning token +settlement collateral = terminal conversion, counted once
```

如果两腿通过交易以总成本 `<1` 获得，利润已经体现在 `cash paid` 与 merge 后 collateral
的差额；不能再加一条“merge edge”。split/merge/redeem fee 只扣一次，terminal inventory
value 也不能继续保留已经被 merge/redeem 消耗的 token。

`spread capture`、`signal/fair-value edge`、`adverse selection`、`execution improvement`、
`missed alpha`、`merge edge` 和 `inventory-policy delta` **不是额外 PnL 行**，而是对同一个
`Core trading PnL` 的 counterfactual attribution。每次分析只能选一个固定 baseline，例如：

```text
Core trading PnL
  = same-row taker baseline PnL
  + maker execution delta including non-fill opportunity cost
  + inventory-policy delta
```

或者在 closed round trip 上，用一个守恒 waterfall 把 core PnL 拆成 spread、markout 与
closing cost；所有 attribution 加总必须精确回到 core PnL。Reviewer 必须检查：

- `spread capture` 与 `fair-value edge` 是否重复计数；
- mark-to-mid 是否错误替代可执行退出或 settlement；
- rebate/reward 是否用 estimate 冒充 realized cash；
- missed maker fill 后本可由 taker 获得的 alpha 是否计入机会成本；
- inventory revaluation 是否只是 signal PnL 的另一种名字；
- merge edge 是否扣除了单腿等待和不完整配对风险。
- tail/event loss 是否已经包含在 terminal inventory value 中，避免再次扣除。

### 2.3 MM 能成立的五个必要条件

1. **Fair-value condition**：在报价 horizon 内，reservation value 的误差足够小；否则 spread 只是给知情交易者的选择权。
2. **Fill condition**：能用真实 trade/order lifecycle 估计 `P(fill | quote, queue, regime)`，而不是用 touch 当 fill。
3. **Selection condition**：条件于 fill 后的 markout/settlement 没有吃掉 spread、rebate 与 signal edge。
4. **Inventory condition**：单腿成交后能以可接受成本 offset、merge、sell 或持有到结算，且 worst-case payoff 不越界。
5. **Operational condition**：book truth、own-order truth、idempotency、cancel confirmation、unknown submit recovery、fee/reward reconciliation 能可靠工作。

缺任何一个，策略都可能“看起来一直赚 spread，偶尔一次把全部利润吐回去”。

### 2.4 Prediction market 的特殊性

- payoff 有界但会在信息事件时跳变，短期低波动不等于低风险。
- YES/NO 是互补结果。完整集合可 split/merge；但两腿不同步成交时，仍是方向性库存。
- exact-weather ladder 不是一个简单 YES-minus-NO 数字。多个 bracket 的 NO payoff 互相重叠，必须按每个可能 settlement scenario 计算组合 payoff vector。
- 距离天气观测、forecast、官方 report、market close 和 resolution 的 event clock，比普通 clock-time volatility 更重要。
- 稀疏盘口会让 queue、cancel latency、min size、tick change 和一次大单影响被放大。
- “BUY YES + BUY NO 且总成本低于 1”在完整配对并成功 merge 时有确定性毛边际；在只成交一腿时没有。

对 city × target_date 的 K 档结果，库存风险的最小合同应是：

```text
payoff_vector[k] = cash
                 + payoff(all YES/NO positions if bracket k settles)
                 - unsettled liabilities
                 - estimated close/merge/settlement costs

inventory_risk = {
  worst_case_payoff,
  expected_payoff_under_model,
  executable_liquidation_value,
  capital_locked,
  concentration_by_city_target_date,
}
```

策略层输出应是 `TargetInventoryVector`，而不是只看某个 token 的 position。

---

## 3. 当前平台机制：动态输入，不是固定常量

以下只用于本轮设计，进入实验前必须从官方/venue 重新获取 market-level truth：

- 官方 order lifecycle 当前列出 `GTC/GTD/FOK/FAK`；post-only 若会立即成交会被拒绝。[Official Order Lifecycle](https://docs.polymarket.com/concepts/order-lifecycle)
- 官方提供 authenticated User WebSocket，可收到 order placement/update/cancel 与 trade match/status change。[Official User Channel](https://docs.polymarket.com/api-reference/wss/user)
- 官方当前说明 weather fee-enabled markets 的 maker fee 为 0，maker rebate pool 比例为 25%，按每个市场实际被吃的 maker liquidity 竞争分配；比例由平台决定、可能变化。[Official Maker Rebates](https://docs.polymarket.com/programs/maker-rebates)
- Liquidity Rewards 按 market-level `min_incentive_size`、`max_incentive_spread`、相对参与者份额和双边程度计分；单边是否计分与价格区间有关，reward allocation 也会变化。[Official Liquidity Rewards](https://docs.polymarket.com/programs/liquidity-rewards)
- 官方目前描述 eligible positions 有年化 4% 的 variable holding reward；资格与费率必须动态核验。[Official Positions & Tokens](https://docs.polymarket.com/concepts/positions-tokens)
- split 将 1 单位 collateral 变为 1 YES + 1 NO；等量 YES/NO 可 merge 回 1 collateral。它是库存工具，不自动等于无风险收益。[Official Positions & Tokens](https://docs.polymarket.com/concepts/positions-tokens)

**当前实现漂移提示：** 本地 `LivePolymarketTransport` 仍声明
`collateral_asset="USDC"`、`fee_schedule_ref="polymarket-token-fee-bps-v1"`，并把
pre-trade estimated fee 计算为 `notional × rate`；当前官方页面使用 pUSD 表述，fee/rebate
公式为 `shares × feeRate × p × (1-p)`，并要求读取 market `feeSchedule`。这不自动说明
历史 actual fill/cash 记账错误，但说明**本地 capability/estimated-fee contract 已有明显
version-drift 风险**。在新 MM 研究或订单 admission 前，必须用当前 client/API raw response
确定 collateral identity、fee formula、per-market parameters，并以 actual charged fee/cash
reconciliation 为最终真相。

会计要求：

- fee schedule、reward config、holding eligibility 都要带 `fetched_at`、market/condition identity 与 raw snapshot/hash；
- estimated maker rebate 只能进入 forecast ledger；实际到账后才进入 realized ledger；
- liquidity reward、maker rebate、holding reward 三者分别入账；
- 每笔 incentive accrual 必须有 `program_id + market/condition_id + wallet +
  accrual_window + eligibility_epoch + denominator/config snapshot + expected_amount`；realized
  时另存 `payout_date + payout_asset + chain + transaction_hash/original payout identity +
  accounting_direction + gross_amount + net_received_amount + adjustment/clawback/reversal_ref`，
  跨日/跨 epoch 不回填到错误 trading episode；
- baseline trading PnL 必须先不含 incentives，随后单列 `+ realized incentives`。

---

## 4. 我们已有的基建与真实状态

### 4.1 事实状态表

| 能力 | 当前事实 | 状态 | 本路线怎么用 |
|---|---|---|---|
| Weather 数据与概率研究 | 已有 weather source、PIT/OOF/frozen-forward 研究、city/target_date/exact-bracket lineage；研究状态由 registry/living docs 管理 | `ready for research; model families individually gated` | Weather Liquidity Head 的信息输入，不替代 execution evidence |
| Weather 量化血缘 | `EventEnvelope → DecisionContext → ModelOutput → SignalCandidate → TradeIntent → plan → order → fill → settlement`；canonical 有 `fact_signal_candidates`、`fact_trades` | `used in current system` | 复用 signal/fill/fee/settlement 和 fixed denominator |
| Public L2/trade truth | `ws_incremental_book.py` 支持 baseline/delta、sequence/gap fail-closed、REST/WS parity 与 immutable book identity；weather 已有选择性 hot-token/full-ladder capture | `implemented and used selectively` | Stage 0 的 market truth；要扩展 quote universe 与长期 trade prints coverage |
| Executable book evidence | 有 executable book truth、execution evidence、book age/depth/tick/min-size 合同 | `implemented` | maker/taker 同刻可执行价与 evidence seal |
| Shared execution contracts | Decimal contracts、ExecutionIntent/ChildOrderPlan、dynamic tick/min shares、fee provenance、post-only validation、deterministic client order ID、unknown submit/cancel semantics、cancel confirmation；但 current official fee/collateral semantics 与本地 transport 存在 version-drift 待核 | `implemented and focused-tested; venue semantics audit required` | 两个策略头共用，不另造执行器；actual fee/cash 优先于 estimate |
| Weather live transport | `PolymarketVenueAdapter` 已在 Core carry order runtime 中实例化，接 `LivePolymarketTransport`，支持真实 book/order submit/cancel/authenticated REST order lookup 和 partial-fill reconstruction | `weather-specific path wired` | 证明共享 runtime 可接 venue；仍需抽离 Core-carry compatibility 与连续 TargetOrderSet 管理 |
| Own-order truth | 当前 weather path 有 authenticated REST lookup、raw order/fill、journal recovery；官方 private User WS 在平台可用 | `partial` | MM 前必须补 private User WS + REST reconcile 的单一权威状态机；REST polling 不能独担高频 order truth |
| Target quote reconciliation | Legacy `quote_runtime/order_manager` 有 target diff/deadband/in-flight guard；modern weather runtime 是 intent/child-order + lifecycle | `partial / two lineages` | 收口成一个 `TargetOrderSet → reconcile → ExecutionIntent` adapter，不让两个 owner 同时管同一订单 |
| Risk | Legacy SafetyGuard/CircuitBreaker；weather 有 live exposure key、shared maker budget、pause/authorization、production controller | `components exist; policy-specific` | 共享 primitive，weather payoff-vector 与 generic-MM inventory policy 分开 |
| Accounting/reconciliation | Weather 已有 raw/canonical fill reconciliation、coverage gate、fee-adjusted settled PnL、account cash reconcile | `strong weather-specific base` | 扩充 open inventory MTM、rebate/reward/merge ledger，不绕开 canonical truth |
| Legacy PMM strategy | single/multi-level quote、weighted mid/OFI/momentum/volatility/inventory skew、paper/live broker、order diff、merge、replay | `dormant historical strategy; offline runnable` | 复用纯函数与 replay ideas；不把 manifest loadability 当 production status |
| Generic quote runtime | `src/platform/quote_runtime` 已有 strategy protocol、broker/order manager、paper broker、risk primitives | `implemented substrate; not a complete modern MM system` | 与 modern execution contracts 对齐，避免再复制第三套 runtime |
| Generic market selection | 没有经真实 denominator 验证的 current universe selector、queue/fill model、reward/capital optimizer | `missing` | Stage 4 单独建设 |
| Open-source `poly-maker` | 本地 clean pin `f35e79030dcc7f6afc0d933d9e7440a3e5b40efd`；其 README 声称 maker-only、TargetQuotes reconcile、market/user WS、heartbeat、risk、paper、merger；无 journal replay backtester | `reference-only; upstream claims not independently accepted` | 借鉴 contracts/ops；通过 adapter/port 复用，不直接替换我们的 truth/ledger |
| Open-source NautilusTrader | 本地 clean pin `105456e4988b0988c821dd84f90c044a325e5754`，含 Polymarket adapter/venue semantics | `reference-only` | 对照 order lifecycle、partial fill、unknown state、tick/min-size；不整框架迁移 |

### 4.2 2026-08-30 离线可运行证据

Legacy PMM 当前重新验证：

```text
Repository source snapshot:
  HEAD=4ed8a3ee154cbf9bf5f0ba9e0911791353c496e5
  branch=codex/market-ladder-kink-v1
  note=verified against the 2026-08-30 working tree; HEAD alone is not the packet identity
```

复现命令：

```bash
.venv/bin/python -m pytest -q \
  tests/pmm_tests/test_config.py \
  tests/pmm_tests/test_live_broker.py \
  tests/pmm_tests/test_order_manager.py \
  tests/pmm_tests/test_pricing_sizing.py \
  tests/pmm_tests/test_safety_guard.py \
  tests/pmm_tests/test_signals.py \
  tests/pmm_tests/test_strategies.py \
  tests/pmm_tests/test_tick_engine_maker_only.py

.venv/bin/python scripts/python/generate_backtest_scenarios.py \
  --catalog src/strategies/pmm/backtest/case_catalog.json \
  --out-dir /tmp/pmm-review/scenarios \
  --seed 20260830

.venv/bin/python scripts/python/pmm_backtest.py validate-dir \
  --scenarios-dir /tmp/pmm-review/scenarios --strict

.venv/bin/python scripts/python/pmm_backtest.py run \
  --scenario /tmp/pmm-review/scenarios/b50_long_regime_switch.json \
  --out-dir /tmp/pmm-review/replay-long
```

本次命令输出摘要：

```text
Focused legacy PMM unit tests: 62 passed
Synthetic scenario generation: 26 scenarios
Strict scenario validation: 26/26 passed, 0 errors, 0 warnings
Representative replay: b50_long_regime_switch, 1,440 ticks
  placed=4,250; fills=4,095; errors=0
  synthetic fill rate/order=96.35%
  synthetic PnL=+$34.9380
```

这只证明：策略纯函数、paper broker、scenario validator 和 replay runner 能跑完。它**不能**证明：

- 真实市场有 96% fill rate；
- queue position、cancel race、partial fill 和 adverse selection 已校准；
- zero fee 的 synthetic PnL 可迁移到真实 venue；
- legacy live broker/order state 满足 modern production contract；
- `single_level_v1` 或 `multi_level_v1` 有真实正期望。

### 4.3 已修复但不等于完整的旧问题

Legacy PMM 当前代码已有：

- asymmetric inventory skew；
- sizing 扣除 open-order exposure；
- volatility pause/circuit behavior；
- in-flight order guard、target diff/deadband；
- conservative/optimistic paper fill model。

仍缺或未达到 MM admission 标准：

- depth-aware anchoring 与多档真实 queue position；
- private User WS 驱动的 authoritative order/fill state；
- 单一 target-state reconciler 与 crash-safe recovery；
- 真实 queue/partial fill/cancel-latency/adverse-fill 校准；
- realized fee/rebate/reward/holding/merge PnL；
- whole-ladder correlated inventory risk；
- generic market selection、capital allocator 与 multi-market portfolio risk；
- market close/resolution/clarification/rate-limit 等 event risk 的统一状态机。
- 当前 official `feeSchedule`/pUSD 语义与本地 `USDC + token fee bps + notional×rate`
  pre-trade estimate 的版本对齐；在对齐前不能把 estimated fee/rebate 用于 admission。

---

## 5. 现有研究证据：先承认反例

### 5.1 Weather maker 的强负证据

Core carry maker 审计窗口 `2026-07-25..2026-08-18`：

| 证据 | 结果 | 含义 |
|---|---|---|
| Maker vs taker 实际贡献 | maker 约 `-$12.46`；同期 taker `+$10.73` | maker 腿不是自然增益 |
| Maker fill rate | replay 约 `28.8%..41.1%` | 价格改善被大量未成交正 alpha 抵消 |
| Early-maker actual EV | `-0.0351/share`；taker `+0.0261/share` | 提前挂单绕开了有效的价格触发过滤器 |
| 条件成交选择 | filled checkpoint win rate 约 `75.2%`，unfilled `91.4%` | fill 明显不是随机样本 |
| 亏损 fill 形态 | 5/5 是“闪吃型” | 看到 bid 跌破再撤无法挽救第一口 fill |
| 毛价格改善 | paired 约 `1.5c/share`、全期约 `+$2.75` | 远小于单次 `-$4..-$8.25` tail loss |

这组证据否定的是“把当前正确的 taker signal 机械改成 maker 就会更赚钱”，不是永久否定所有 selective maker、reduce-only exit、incentive-dependent MM 或 generic MM。

### 5.2 描述性 microstructure 证据仍不够做 policy

- Weather book atlas：`47,508 states / 17 target dates`，能描述 spread/depth/repricing，但缺 market-wide prints、queue 与 unfilled denominator。
- Five-city microstructure：`1,024 target-day states`，quote-cross conditional markout 偏负，但没有真实 fill denominator。
- D-1 maker probe：development/secondary conditional-maker ROI 曾为 `+2.84%/+0.96%`，但只看 selected rung，actual fills=`0`；**路线状态**已被后续 forecast-repricing 研究 superseded-for-now，原始 probe evidence 仍保留，并没有被改写成“已证伪”。
- `current-YES maker-then-taker` 仍是 design draft；旧 `mid_price_core v1/v2/maker_queue` 因 2026-06 live losses 被停用，属于 shelved，不是永久证伪。

### 5.3 Synthetic 与 real evidence 的关键鸿沟

```text
Legacy representative synthetic fill rate   96.35%
Weather maker-vs-taker real-print fill rate 28.8%..41.1%
```

两者不能直接比较不同场景的绝对数字，但这个数量级差异足以说明：**在 queue/fill/adverse-selection 校准前，synthetic PMM PnL 不能参与 live admission。**

---

## 6. 可能的盈利来源地图

下面是研究候选，不是已确认利润。GPT Pro 需要重排优先级并补充遗漏。

| 盈利来源 | 机制 | 我们的先验 | 最小可证伪实验 | 主要失败方式 |
|---|---|---|---|---|
| Weather signal 的 execution savings | 同一正 EV signal 用 maker 获得更低成本 | **已有总体负证据；只可能 regime-selective** | 同一 QuoteDecision 上 maker/taker/skip，计 missed alpha、actual fill、markout、settlement | 好 signal 不成交，坏 signal 被吃 |
| Passive reduce/exit | 用 maker 卖出或获取互补腿，降低退出成本 | **尚未系统验证；优先级高于连续 MM** | 对已有 inventory 比较 maker sell / taker sell / merge / hold-to-settle | 无法退出、价格继续恶化、资本锁定 |
| Spread capture | 双边成交后平仓或完整配对 | **generic MM 核心，但 weather 未证实** | 成交 episode 闭环 PnL，不能只看单腿 markout | 单腿暴露、event jump、cancel race |
| Complete-set / merge edge | YES+NO 总成本低于 1，merge 回 collateral | **机制真实，执行 edge 未证实** | 原子或带单腿风险的全 lifecycle replay | 一腿不成交、min size、merge latency/失败 |
| Maker rebate | 被吃的 maker liquidity 分享 fee pool | **官方当前 weather 有项目级机制；现金贡献未知** | 拉取 per-market config + actual fill + actual wallet rebate | 竞争稀释、规则变化、逆选大于 rebate |
| Liquidity rewards | 靠 tight/size/two-sided/uptime 获得 market reward | **可能是独立 business，不应藏进 spread PnL** | 每分钟可验证 score、竞争份额、实际到账、风险资本占用 | 奖励拥挤、需要危险报价、配置变化 |
| Holding rewards | eligible inventory 获得 variable holding reward | **只可作 secondary carry** | eligibility snapshot + hourly position sample + actual distribution | 年化小于尾部/资本成本，资格变化 |
| Weather residual / stale quote | 物理模型或快源领先 market | **已有研究资产，模型 family 逐项 gated** | PIT 同分母 model-vs-market + executable quote + actual fill | 信息已经 price in、source clock/basis 错误 |
| Event-clock selective quoting | 在 quiet 时段做市，报告/观测前撤 | **合理但固定 buffer 已有负证据** | 以 first-seen/event hazard 分层，不做事后阈值 | 对手更早知道、撤单来不及 |
| Inventory recycling across ladder | 全梯度上把错误/冗余仓位转到更优表达 | **值得研究** | payoff-vector 下 optimize hold/reduce/flip，计相关性与滑点 | 把相邻 bracket 当独立、NO payoff 重叠 |
| Full-ladder underround/overround | 全部 exact outcomes 的可执行总价偏离 1 | **已有 scanner/research，不能把静态价差当套利** | full depth、同时性、fees、partial-fill、token identity、settlement basis | 非原子成交、错误 outcome completeness |
| Cross-market / lead-lag | 同一天气事件、相邻城市、相关来源或 venue 间定价差 | **未形成 confirmed MM edge** | 预注册映射、PIT lead-lag、可执行容量、独立 holdout | 伪相关、不同 settlement basis、腿风险 |
| Market selection edge | 只进入 spread/reward/flow/toxicity 最合适的市场 | **generic MM 必需，当前缺失** | 全市场固定分母 scan + paper/shadow + capital-adjusted rank | 事后挑市场、竞争者进入后 edge 消失 |
| Operational edge | 更可靠的 user WS、reconcile、heartbeat、cancel、capital reuse | **能减少损失，不自动产生 alpha** | incident/rejection/cancel-race counterfactual | 把少亏写成主动盈利来源 |

必须单独处理的相邻方向：weather proposal reward/oracle 收益不是 CLOB MM PnL；即使共用 weather 数据，也不能混入本策略成绩。

---

## 7. 推荐目标架构

### 7.1 唯一 truth/decision/execution DAG

```text
Weather sources ───────┐
Market metadata ───────┼──> PIT InformationState ───────────────┐
Public L2 + trades ────┘                                         │
                                                                  ├─> Weather Liquidity Head
Private User WS ───────┐                                         │     └─> TargetInventoryVector
Authenticated REST ────┼──> Authoritative OwnOrderState ─────────┤
Wallet/onchain facts ──┘                                         │
                                                                  └─> Generic MM Head
Market fee/reward config ─> IncentiveState                              └─> TargetOrderSet

TargetInventoryVector / TargetOrderSet
  -> sole Target-State Reconciler
  -> ExecutionIntent + ChildOrderPlan
  -> portfolio/inventory/risk admission
  -> PolymarketVenueAdapter
  -> submit/cancel/replace/merge side effects
  -> user WS + REST + wallet receipts
  -> append-only order/fill/reward/merge ledger
  -> maker/taker/skip evaluation + payoff-vector risk + PnL
```

### 7.2 Sole-owner 合同

| 状态 | Sole owner | 禁止的平行 owner |
|---|---|---|
| Public book/trade truth | deterministic market-data reconstruction | 策略自己维护第二份 book |
| Weather information truth | weather source/PIT canonical layer | 用 ingest 后未来数据回填 decision state |
| Own order/fill truth | private User WS state machine + authenticated REST reconcile | legacy broker、weather runner 各自宣称最终状态 |
| Desired quote/inventory | 对应 strategy head | execution 层自行发明 alpha/target |
| Live exposure reservation | shared execution runtime | 每个 child arm 独立越过总 budget |
| Venue side effect | `PolymarketVenueAdapter` + single transport | strategy 直接调用 client |
| Cash/fill/rebate/reward/merge ledger | canonical accounting/reconcile layer | Markdown/estimate 直接改 realized PnL |
| Production desired state | existing production controller/manifest | 新建第二套 scheduler/daemon owner |

### 7.3 Build / adapt / reuse

**直接复用：**

- deterministic WS book、trade evidence、executable book truth；
- weather lineage、facts、fill coverage/account reconcile；
- modern execution contracts、risk claims、journal、venue adapter；
- legacy PMM 的 pure pricing/sizing/signals 与 scenario/replay ideas；
- `platform/quote_runtime` 的 strategy protocol、order diff/paper/risk primitives。

**适配后复用：**

- legacy `QuoteTarget` 与 modern `ExecutionIntent/ChildOrderPlan`；
- Core-carry live transport，去掉策略专属 compatibility；
- poly-maker 的 user WS/TargetQuotes/heartbeat/merger 设计；
- Nautilus 的 venue semantics 和 edge-case tests。

**必须新增或收口：**

- private User WS + REST reconcile 的 authoritative own-order state；
- 单一 `TargetOrderSet` reconciler，支持 partial fill、replace、unknown state、restart；
- per-market fee/reward/holding config snapshot 与 realized incentive ledger；
- queue/fill/cancel/adverse-selection calibration 与真实 journal replay；
- weather whole-ladder `TargetInventoryVector` / payoff-vector risk；
- generic market selector、capital allocator 和独立 PnL namespace。

**不建议：**

- 直接把 poly-maker 或 Nautilus 整仓替换当前 weather 系统；
- 再造第三套 order/fill/accounting owner；
- 先开 live 再靠日志反推 queue/fill 模型；
- 把 legacy PMM 的 synthetic leaderboard 当 market selection。

---

## 8. 最小数据合同与研究分母

### 8.1 `QuoteDecision`

至少包含：

```text
decision_id, comparison_group_id
market_id, condition_id, token_id, outcome/bracket identity
city, target_date, settlement_basis
ts_event, ts_ingest, decision_ts, information_cutoff
book_epoch_ref, book_sequence, bid/ask/depth, trade-flow window
fee_schedule_ref, reward_config_ref, holding_eligibility_ref
model/fair_value vector + provenance
inventory payoff vector + open/pending order exposure
target_shares, target_horizon, event hazard clocks
maker quote, taker executable quote, skip arm
eligibility and all blocker reasons
```

### 8.2 `OwnOrderLifecycle`

```text
client_order_id, expected_venue_order_id, venue_order_id
decision_id, intent_id, child_role, identity/dedupe key
submit_attempt_before_side_effect
accepted/live/partial/filled/cancel_pending/cancelled/expired/rejected/unknown
requested/matched/remaining shares
queue proxy and order-ahead evidence if available
private_ws_event_ts, rest_lookup_ts, reconcile provenance
cancel_requested_ts, cancel_confirmed_ts, fill_match_ts
raw venue status + immutable journal refs
```

`unknown` 不是 `cancelled`，`cancel requested` 不是 `cancel confirmed`，`MATCHED` 的观察时刻不是必然等于真实成交时刻。

### 8.3 `EpisodeLedger`

```text
episode_id, strategy_head, capital_sleeve
all fills and liquidity role
principal, fees
incentive program/market/wallet/accrual-window/eligibility-epoch/config-denominator identity
estimated incentive accrual kept outside realized PnL
realized rebate/reward/holding payout asset/chain/tx-or-payout identity
gross/net cash, accounting direction and adjustment/clawback/reversal refs
merge/split/redeem receipts
short-horizon markouts at fixed horizons
executable liquidation value
terminal settlement payoff
missed-alpha counterfactual
worst-case payoff and capital-time integral
```

### 8.4 固定分母

Weather maker/taker/skip 的主分母：

```text
all eligible QuoteDecision rows × fixed target_shares
```

至少同时报告：

- signal funnel：所有 model/market decisions；
- evidence funnel：有完整 book/trades/own-order/fill/settlement 的 rows；
- maker fills；
- maker non-fills；
- taker executed counterfactual；
- skip；
- target_date/city/regime/side 分层。

Generic MM 的主分母不能是“有 fill 的订单”，而应是：

```text
all admitted quote episodes + all quoted time + all capital committed
```

### 8.5 验证方法

- 双时钟：`ts_event` 与 `ts_ingest`；所有 state 都必须 as-of decision。
- 固定 market universe 与 admission rule，避免事后挑赢家。
- target_date block bootstrap；多城市/多市场还要检查 group/event correlation。
- frozen-forward 参数不随结果改动；threshold research 与 confirmation 分离。
- 同分母 market/taker baseline；短期 markout 和 final settlement 分开。
- fill model 用真实 private order events + prints 校准，并报告 calibration error。
- incentives 做 `trading-only`、`+rebate`、`+liquidity reward`、`+holding` 四层 waterfall。
- 同时报 ROI、PnL、capital-time return、drawdown、worst-case payoff、capacity。

---

## 9. 阶段设计与分支

### Stage 0 — Truth substrate 与 replay 可信度

**假设：** 在不改变策略的前提下，我们能重建市场、自己的订单、成交、fee/incentive 和库存状态。

**范围：** 只做 shared substrate；不授权新真实订单。

**交付：**

- public L2/trades 的 sequence/gap/REST parity 和选择性 capture；
- private User WS 订单/成交状态 + authenticated REST reconcile；
- deterministic `TargetOrderSet` reconcile、idempotency、unknown submit/cancel recovery；
- dynamic tick/min-size/fee/reward/holding snapshots；
- current collateral/fee formula contract audit：API raw `feeSchedule`、本地 estimate、actual
  charged fee 与 wallet/canonical cash 四方对账；
- actual fill、partial fill、cancel race、rebate/reward/merge ledger；
- legacy replay 接入真实 captured journals，校准 queue/fill model；
- restart/reconnect/crash-safe recovery 与 heartbeat/cancel-all 演练。

**进入下一阶段的 provisional gate：**

- decision/book/order/fill identity 无 orphan；
- controlled fault fixtures 覆盖 unknown submit、unknown cancel、partial fill、duplicate event、WS gap、restart；
- estimate 与 actual fill/reward/cash 明确分层；
- replay 对已知真实订单 lifecycle 可逐笔重建；
- 任何 gap 显式 fail closed，不 silent fallback。

**真实 evidence floor 分两层：**

- 进入 research/shadow：冻结并逐笔重建至少 `100 actual orders / 30 target_dates`，覆盖
  maker、taker、cancel、reject 与所有历史中自然出现的 partial/unknown/reconcile 状态；
  orphan 必须为 0，不能只靠 fixture 过门。
- 进入任何新 controlled tiny-live：private User WS、REST lookup、journal 与 wallet/canonical
  对同一批真实 placement/cancel/fill events 达成 100% identity parity；若自然样本没有
  partial/unknown/cancel-race，只能证明处理逻辑，不能估计这些状态的发生率。必要的 exchange
  preflight 本身属于另行授权的 tiny-live packet，不由本文授权。

**Stop：** order truth 或 PnL ledger 不闭合时，不研究 quote alpha。

### Stage 1 — Weather maker acquisition

**假设：** 在某些预先定义的 information/market regimes，maker 相对 taker/skip 有正的 incremental net EV。

**策略：** weather model 与最终 target position 不变；只比较 execution arm。

```text
same weather decision
├── maker: post-only, explicit price/lifetime/event deadline
├── taker: same-time executable depth/fee counterfactual
└── skip: zero exposure
```

**顺序：**

1. PIT historical replay：只筛可支持 actual print/book 的历史；不把 touch 当 fill。
2. Zero-notional forward shadow：冻结 quote、记录 market/queue/markout，不提交。
3. Controlled tiny-live measurement：只在前两步过门且 owner 独立授权后，用极小固定 shares 获取真实 queue/fill evidence。

**主要指标：**

- incremental net PnL vs taker 和 vs skip；
- maker fill rate 与 fill calibration；
- maker price improvement；
- conditional-on-fill 10s/60s/300s markout 与 settlement EV；
- missed-taker-alpha；
- event clock、city、price band、spread/depth、flow/toxicity 分层；
- actual rebate 单列。

**Provisional evidence floor（不是显著性保证）分两层：**

- 形成 frozen shadow hypothesis：至少 `100 eligible decisions / 30 target_dates`，有同刻
  book/trade evidence，覆盖 quiet 与 information-hazard regimes；没有 actual own-order fills
  时只能得到 shadow verdict，不能声称 fill-adjusted 盈利。
- profitability admission：在另行授权的 controlled measurement 中累计至少 `20 actual maker
  fills`，并保留所有 non-fills、partial/cancel/unknown rows；在达到 actual-fill floor 前只能
  停留于 shadow/tiny-live measurement，不能给 Stage 1 maker-acquisition 盈利结论，也不能
  把它当成 Stage 3 admission。Stage 2 的 reduce/exit 可用已有真实 inventory 独立启动，但
  必须通过自己的 evidence floor。

最终仍以 target-date block CI、effect size、capacity 为准。请 GPT Pro 判断数量与 sequential
design 是否应改。

**晋级：** 预注册 policy 的 maker incremental net EV 相对 taker 的 point estimate 与 target-date-block lower bound 均为正，且不是由单一城市、单一日期、单一 incentive payment 支撑；fill calibration 与 lifecycle coverage 过门。

**Kill / fallback：** 若 maker 不胜 taker，保持 taker；只允许另开 reduce-only exit 假设，不能通过改分母救结果。

### Stage 2 — Weather inventory lifecycle

**假设：** 基于完整 outcome/payoff vector 的 hold/reduce/exit/flip 能提高 capital-adjusted net PnL 并降低尾部风险。

**新增策略输出：**

```text
P(outcome vector) + market state + current payoff vector
  -> TargetInventoryVector
  -> {buy, add, hold, reduce, exit, flip, merge, settle}
```

**比较臂：**

- passive maker exit；
- immediate taker exit；
- acquire complementary leg then merge；
- hold to settlement；
- no-action baseline。

**主要指标：** realized PnL、executable liquidation value、capital-time return、worst-case payoff、expected shortfall、unfilled exit age、inventory concentration、merge completion rate。

**Provisional evidence floor：** 至少 `100 inventory decisions / 30 target_dates / 20 actual
exit-or-merge episodes`；primary terminal basis 冻结为 final settlement，固定 60s/300s executable
liquidation 只作诊断。请 reviewer 按自然频率重设数字，但不得用 filled-only 分母。

**晋级：** paired target-date-block CI 显示 fee-adjusted PnL 相对 no-action/taker baseline
不低于预注册 non-inferiority margin `ε_pnl`，同时 worst-case/expected-shortfall 或
capital-time return 至少一项的 block-CI 改善；所有 action 有完整 lineage。`ε_pnl` 必须在
看结果前按可承受成本冻结，不能事后设为刚好过门。

**Kill：** 若 passive exit 只是延迟确认亏损、增加 exposure 或没有可执行容量，回退 taker/settlement policy。

### Stage 3 — Selective weather two-sided MM

**假设：** 只有在信息风险低、fair value 稳定、spread/reward 足够、库存可管理的 weather regimes，连续双边 quote 才有正 EV。

**关键限制：**

- 不是所有 weather market 全天连续报价；
- forecast/observation/official-report hazard window 默认退出或 reduce-only；
- 两侧共享一个 market/event inventory budget；
- filled one-side 后立即重算 payoff vector，不机械补另一腿；
- rewards-aware 与 trading-only 策略分别出账；
- self-cross、negative spread、duplicate owner、stale book 一律阻断。

**候选 quote：**

```text
reservation value
  = weather probability / calibrated market blend
  - payoff-vector inventory shadow cost
  - event hazard premium

half spread
  = base spread
  + volatility/toxicity/queue/cancel premium
  + capital and tail premium
```

Reward-aware policy 可以额外施加 `max width / min size / uptime` 约束，但不能在 quote
公式里把**尚未到账**的 incentive subsidy 直接减掉后，又在 realized ledger 加回。若它让
quote 变窄，必须作为独立 policy identity 比较 trading loss 与 actual reward。

**Provisional evidence floor：** 至少 `200 eligible quote episodes / 30 target_dates / 30
actual maker fills / 20 closed two-sided-or-merge episodes`；quiet 与 event-boundary 两类 regime
都进入固定分母，后者可全部 skip/cancel，但不能从 denominator 删除。

**晋级：** target-date-block 下 closed-episode trading PnL 的 lower bound 在不含 incentives
时不低于预注册 `-ε_trading`；含 actual incentives 后 capital-time net PnL lower bound 为正；
tail loss、inventory age、cancel race 不集中在 event window。若目标是纯 trading MM，必须取
`ε_trading=0`；若允许 incentive-dependent business，要单独声明可接受 subsidy dependency。

**Kill：** 只有未实现 mark-to-mid 为正、只有 reward estimate 为正、或利润由一次未重复 incentive 支撑时停止。

### Stage 4 — Generic traditional two-sided MM

**假设：** 不依赖 weather model，仅凭市场选择、microstructure、complete-set mechanics 和 incentives，能找到可持续的正期望市场。

**与 weather 隔离：**

- 独立 market universe、fair-value hypothesis、capital sleeve、inventory ledger、PnL 和 kill switch；
- 只复用 Stage 0 substrate 与 execution/accounting primitives；
- 不把 weather 成绩作为 generic MM admission evidence。

**先做 market selection：**

- spread/depth/volume/trade-arrival；
- reward/rebate/holding configuration 与竞争强度；
- event schedule、news toxicity、resolution horizon；
- tick/min size、inventory split/merge 可用性；
- cross-market correlation 与 worst-case capital；
- 实际可获得 queue share 和 cancel latency。

**顺序：** full-universe observation → fixed selector → paper/shadow → isolated tiny-live（另行授权）。工程研究可在 Stage 1/2 期间并行；资金 admission 不与 weather 共用。

**Provisional evidence floor：** 固定 universe/selector 下至少 `500 admitted quote episodes / 20
markets / 4 independent event-or-time blocks / 50 actual maker fills / 30 closed episodes`；同时报告
quoted capital-time，没有 fill 的市场不能消失。请 reviewer 根据市场自然 cluster 重设 block。

**晋级：** market/event-block lower bound 显示固定 selector 在 unseen markets/time blocks 上
产生 positive closed-episode capital-adjusted net PnL，实际 incentives 可对账，去掉 top
market/top day 后仍稳健；trading-only 与 incentive-dependent verdict 分开。

**Kill：** 只靠事后挑市场、synthetic fills、gross spread 或未到账 rewards 才为正时停止。

### 9.1 依赖图

```text
Stage 0 shared truth/execution/accounting
├── Stage 1 weather maker acquisition
│   └── Stage 2 weather inventory lifecycle
│       └── Stage 3 selective weather MM
└── Stage 4 generic MM research
    └── isolated admission and capital sleeve
```

Stage 4 的研究不必等 Stage 3 结论，但不得绕过 Stage 0。

---

## 10. 预注册指标、gate 与 stop conditions

### 10.1 每轮必须冻结

- hypothesis、decision grain、market universe、eligible rule；
- quote price/lifetime/reprice/cancel policy；
- target shares 与 capital cap；
- event hazard windows；
- fee/reward/holding snapshot rule；
- fill evidence hierarchy；
- markout horizons 与 settlement definition；
- bootstrap block 和 missing-data policy；
- live/shadow/paper identity。

### 10.2 主指标

| 家族 | Primary | Secondary | Guardrail |
|---|---|---|---|
| Maker acquisition | incremental fee-adjusted PnL vs same-row taker | fill rate、price improvement、missed alpha、markout | lifecycle coverage、tail loss、event concentration |
| Inventory lifecycle | capital-time adjusted net PnL / expected shortfall improvement | exit age、merge rate、liquidation discount | worst-case payoff、concentration |
| Selective weather MM | closed-episode trading PnL + separate actual incentive waterfall | spread capture、round trips、inventory turnover | event-tail loss、cancel race、stale quote |
| Generic MM | unseen-block capital-adjusted closed-episode PnL | selector stability、capacity、actual rewards | top-market dependence、drawdown、portfolio payoff |

### 10.3 不能作为晋级证据

- synthetic PnL 或 synthetic fill rate；
- quote count、posted notional、resting time；
- touch/cross-based fill 没有 queue/print/order evidence；
- 只看 filled rows；
- gross ROI 未扣 fee、missed alpha、capital lock-up；
- estimated rebate/reward；
- 单日、单城、selected rung 或事后 price band；
- mark-to-mid 代替 executable exit/settlement；
- “代码有 live mode”或开源 README 声称 live-verified。

### 10.4 统一立即停止条件

- book gap、private order state gap、unknown submit/cancel 无法 reconcile；
- duplicate side-effect owner 或同一 exposure 被两个 sleeve 重复占用；
- actual shares/cost/cash 与 canonical ledger 不闭合；
- fee/reward/instrument identity 缺失或过期；
- event/market resolution、token/bracket identity 或 settlement basis 不清楚；
- worst-case payoff、daily loss、capital、inventory age 任一越界；
- strategy 需要改分母、回填未来信息或删除 losing rows 才为正。

---

## 11. 有界实施包

这些是审阅后的候选工作包，不代表本轮已经授权实现或部署。

| WP | 单一职责 | 主要产物 | 验收证据 | 依赖 |
|---|---|---|---|---|
| WP0 | 冻结 current truth 与 schemas | current-state manifest、contract map、strategy/PnL namespace | source/test/status truth table | 无 |
| WP1 | Own-order truth | private User WS state machine + REST reconcile + restart journal | duplicate/gap/partial/unknown/cancel-race fixtures | WP0 |
| WP2 | Target state reconciliation | `TargetOrderSet` adapter 到 ExecutionIntent/ChildOrderPlan | idempotent diff、single owner、restart parity | WP1 |
| WP3 | Real journal replay/calibration | captured book/trades/orders replay + queue/fill model | predicted-vs-actual fill/partial/cancel calibration | WP1–2 |
| WP4 | Weather maker/taker/skip evaluator | fixed-denominator dataset、frozen policy、shadow report | target-date bootstrap + missed-alpha ledger | WP3 |
| WP5 | Weather payoff-vector inventory | TargetInventoryVector、scenario payoff ledger、exit/merge arms | all K settlement scenarios and lineage tests | WP2–4 |
| WP6 | Selective weather MM | quiet-regime TargetOrderSet policy | trading-only and incentive waterfall | WP4–5 |
| WP7 | Generic market selector | full-universe snapshots、fixed selector、paper/shadow | unseen-block capital-adjusted report | WP0–3 |
| WP8 | Controlled tiny-live measurement | explicit authorization packet、caps、rollback、reconcile | actual fill/cash/rebate evidence; no orphan | corresponding prior gates |

原则：一个 order truth owner、一个 execution side-effect owner、一个 realized ledger。Open-source 代码只能通过 WP0/contract review 后进入对应 package。

---

## 12. 需要 GPT Pro 重点回答的问题

1. Weather-first 是否真是最快的学习路径？如果不是，哪个 generic market cohort 提供更便宜、更干净的 fill/inventory 证据？
2. Stage 1 应先研究 maker acquisition，还是先做 reduce-only passive exit？现有负证据是否已经足以把 acquisition 降为低优先级？
3. 对 weather exact ladder，正确的 reservation value、TargetInventoryVector 和 worst-case payoff 形式是什么？
4. BUY YES + BUY NO / split / merge 的完整集合策略，在哪些订单与资本条件下才是真实确定边际？
5. Rewards-only、rebate-assisted、holding-assisted MM 是否值得作为独立 business？怎样防止 incentive 变化让策略瞬间失效？
6. Stage 0 中 private User WS 是否是 tiny-live 前 blocker？哪些研究可以在没有它时继续，哪些绝对不行？
7. 应怎样从 actual order lifecycle 推断 queue position 与 fill hazard？最低数据量是什么？
8. Weather event hazard 应以哪些 clock 表示：forecast publish、source first-seen、official observation、market update、close/resolution？
9. 短期 markout、executable unwind、terminal settlement 应分别如何进入 maker policy，而不 double count？
10. 如何把 market-implied fair value、weather model 和 microprice 组合，避免既追 stale market 又被 informed flow 反向选择？
11. Generic market selector 最可能的 profit pools 是 spread、rebate、liquidity reward、holding、complete-set，还是某种尚未列出的机制？请排序。
12. 100 decisions / 30 target_dates / 20 fills 的 evidence floor 是否合理？请给更好的 sequential/frozen-forward 设计。
13. 哪些现有 legacy/open-source 组件应该 port，哪些应该只当测试 oracle，哪些应明确弃用但保留历史？
14. 如何隔离 weather directional inventory 与 generic MM neutral inventory，避免共享资金后隐藏 tail correlation？
15. 最容易被我们忽略、但最可能带来利润或灾难的三件事是什么？
16. 当前本地 `USDC/token-fee-bps/notional×rate` 与官方 `pUSD/feeSchedule/p(1-p)`
    语义漂移应如何验证？它是 research blocker、tiny-live blocker，还是需要回溯既有订单的
    accounting incident？请给出最小影响半径审计。

---

## 13. 证据与代码指针

### 13.1 单文件 evidence digest

若 GPT Pro 只收到本文件、无法访问本地 repo，请使用下表作为随包 evidence snapshot；
相对链接只是 owner 后续复核的 provenance。你可以把本表标为
`PROVIDED_EVIDENCE_NOT_INDEPENDENTLY_REPRODUCED`，但不能假装自己读过不可访问的本地文件。

| Evidence item | 本包内可用事实 | 原始证据性质 | 允许的推论 |
|---|---|---|---|
| Legacy PMM offline smoke | §4.2 给出完整命令、source snapshot 与 `62 passed / 26 of 26 valid / 1,440-tick replay / 96.35% synthetic fill / +$34.938 synthetic PnL` | 2026-08-30 本地命令输出；临时 replay artifact 不作为 durable profitability evidence | 软件路径可离线运行；不能推论真实盈利 |
| Modern market truth | deterministic book code含 baseline/delta、sequence gap fail-closed、REST/WS parity、immutable epoch；已有 selective weather capture | code + focused tests + weather runtime journals | 可复用 market truth primitive；不能推论全 universe coverage |
| Modern order runtime | adapter tests覆盖 dynamic tick、post-only rejection、deterministic client ID、partial state、unknown submit/cancel；Core carry runtime 实际实例化 adapter/live transport | code + fixtures + weather-specific live path | venue path已接通；不能推论 generic continuous MM 已完成 |
| Current venue-semantics drift | local transport 宣告 `USDC`、legacy token-fee-bps、`notional×rate` estimate；当前官方页面为 pUSD/market feeSchedule/`shares×rate×p×(1-p)` | 当前本地 code 对官方 current docs 的直接对照 | pre-trade fee/collateral contract 必须重核；不自动重写历史 actual cash |
| Core carry maker audit | §5.1 数字来自 canonical DB、Mac raw runtime、data-api/onchain trades、weather raw reports；窗口 `2026-07-25..08-18` | 多层真实 execution/settlement evidence；报告内明确部分 settlement cutoff | 机械 maker 替代 taker 在该分母失败；不永久否定所有 selective/reduce-only MM |
| Weather microstructure | atlas `47,508 states/17 dates`；five-city `1,024 states`；D-1 probe `+2.84%/+0.96%`, actual fills 0 | historical book/selected-rung descriptive research | 可形成 hypothesis；不能形成 live execution policy |
| `poly-maker` reference | clean pin `f35e...efd`；README 声称 TargetQuotes、market/user WS、heartbeat、risk、paper、merger；明确缺 journal replay | upstream README + 本地 source snapshot，未由本项目 live 验收 | 只作 design/port 参考 |
| NautilusTrader reference | clean pin `1054...5754`；本地 adapter 文档/代码覆盖 Polymarket venue semantics 和 partial/unknown edge cases | upstream code/docs，本项目未整框架集成 | 只作 semantic/test oracle |

证据冲突时优先级：actual venue/wallet receipts → raw order/fill/trade events → canonical reconciled
facts → deterministic replay → fixture → synthetic → README/设计声明。GPT Pro 应把无法由本文件
支持的新断言标为 `NEEDS_SOURCE`。

### 13.2 本地复核指针

#### Current strategy/research truth

- [Weather strategy registry](../../WEATHER_STRATEGY_REGISTRY.md)
- [Research knowledge system](../../RESEARCH_KNOWLEDGE_SYSTEM.md)
- [Weather system contract](../../WEATHER_SYSTEM_CONTRACT.md)
- [Weather quant design](../../WEATHER_STRATEGY_QUANT_DESIGN.md)
- [Execution module refactor plan](../../WEATHER_EXECUTION_MODULE_REFACTOR_PLAN.md)

#### Maker/microstructure evidence

- [Core carry maker adverse-selection audit](../../analysis/2026-08/2026-08-20-core-carry-maker-adverse-selection-review-v1.md)
- [Core carry market-state execution P2](../../analysis/2026-08/2026-08-23-core-carry-market-state-execution-quality-p2-v1.md)
- [External consult round 2 packet](../../analysis/2026-08/2026-08-23-core-carry-external-consult-round2-prompt-v1.md)
- [Weather book microstructure atlas](../../analysis/2026-07/2026-07-30-weather-book-microstructure-atlas-v1.md)
- [Five-city weather microstructure](../../analysis/2026-07/2026-07-30-five-city-weather-microstructure-v1.md)
- [D-1 maker probe](../../analysis/2026-08/2026-08-09-lmvm-d1-repricing-maker-probe-v1.md)
- [Open-source execution review](../../analysis/2026-07/2026-07-07-order-execution-quality-open-source-review-v1.md)

#### Legacy PMM baseline

- [PMM architecture](../../pmm/ARCHITECTURE.md)
- [PMM strategy playbook](../../pmm/STRATEGY_PLAYBOOK.md)
- [PMM replay/scenario method](../../pmm/BACKTEST_SCENARIO_METHOD.md)
- [PMM known gaps](../../pmm/TODO_IMPROVEMENTS.md)

#### Core implementation

- [`src/platform/market_data/ws_incremental_book.py`](../../../src/platform/market_data/ws_incremental_book.py)
- [`src/platform/market_data/execution_evidence.py`](../../../src/platform/market_data/execution_evidence.py)
- [`src/platform/market_data/executable_book_truth.py`](../../../src/platform/market_data/executable_book_truth.py)
- [`src/platform/quote_runtime/`](../../../src/platform/quote_runtime/)
- [`src/strategies/weather_edge_v1/execution/`](../../../src/strategies/weather_edge_v1/execution/)
- [`src/strategies/weather_edge_v1/runtime/order_runtime.py`](../../../src/strategies/weather_edge_v1/runtime/order_runtime.py)
- [`scripts/ops/weather_polymarket_live_transport.py`](../../../scripts/ops/weather_polymarket_live_transport.py)
- [`scripts/ops/weather_core_carry_order_runtime.py`](../../../scripts/ops/weather_core_carry_order_runtime.py)
- [`src/strategies/pmm/`](../../../src/strategies/pmm/)

#### Local external reference pins

- `runtime/_external_research/poly-maker @ f35e79030dcc7f6afc0d933d9e7440a3e5b40efd`
- `runtime/_external_research/nautilus_trader @ 105456e4988b0988c821dd84f90c044a325e5754`

---

## 14. GPT Pro Required Output Template

请严格使用以下结构输出，文件建议命名：

`WEATHER_FIRST_MM_GPT_PRO_REVIEW_RESULT_V1.md`

### A. Overall disposition

从三项选择一项：

```text
ACCEPT_WEATHER_FIRST_STAGING
ACCEPT_WITH_REQUIRED_CHANGES
REWORK_FROM_FIRST_PRINCIPLES
```

明确 readiness scope：research、shadow、paper、controlled tiny-live 中的哪一级；不得默认授权 live。

### B. First-principles economics audit

给出你认为不 double-count 的收益/成本恒等式，指出本文公式的错误、遗漏与不可识别项。

### C. Reconstructed current-state truth table

逐项标为：

```text
REAL_VERIFIED
IMPLEMENTED_FIXTURE_ONLY
SYNTHETIC_ONLY
PARTIALLY_WIRED
MISSING
REFERENCE_ONLY
NOT_AUTHORIZED
```

每个纠错必须引用本文 section 或 source pointer，并写明 acceptance evidence。

### D. Strategy-family verdicts

分别裁决：

- weather maker acquisition；
- weather passive exit/inventory lifecycle；
- selective weather two-sided MM；
- generic two-sided MM；
- incentive-dependent MM；
- complete-set/merge strategy。

每项给 `KEEP / REORDER / SPLIT / MERGE / DROP-FOR-NOW`、理由和 stop condition。

### E. Profit-source ranking

用一张表排序所有 profit pools：mechanism、expected robustness、data readiness、capital intensity、competition sensitivity、tail risk、minimum falsification test。补充本文遗漏项。

### F. Recommended final DAG and ownership

给出唯一推荐 DAG、sole-owner 表、共享层与 weather/generic 隔离层。禁止给多个不裁决的备选架构。

### G. Data and execution contract audit

审查 `QuoteDecision`、`OwnOrderLifecycle`、`EpisodeLedger`、payoff vector、fee/reward config。列出开始有效研究前的最小字段和 evidence hierarchy。

### H. Stage and gate redesign

逐阶段给：hypothesis、fixed denominator、deliverables、primary metric、evidence floor、admission gate、kill/rollback。明确哪些阶段可并行。

### I. Blocking findings

用 `BF-xx` 编号，分成：

- research/shadow 前必须解决；
- controlled tiny-live 前必须解决；
- 可在 pilot 内验证的非 blocker。

每项写 acceptance evidence。

### J. Adversarial failure-mode review

至少覆盖：informed flow、event clock、queue illusion、cancel race、partial fill、unknown state、reward crowding、rule change、whole-ladder correlation、capital lock-up、settlement basis、self-cross、restart/duplicate side effects。

### K. Bounded work packages

给出 4–10 个有依赖的 package：single owner、inputs、deliverables、tests/evidence、rollback；不得创建平行 order/accounting owner。

### L. Exact document/code changes

列出 file/section、exact change、why、acceptance evidence。区分 port、adapt、reuse、retain-dormant。

### M. Final owner-facing recommendation

不超过 20 行中文：

- 先做哪一条；
- 为什么最可能赚钱或最快证伪；
- 另外两条放在哪个阶段；
- 最大 blocker；
- 下一项可执行但不涉及 live 的动作；
- 哪些仍未授权。

---

## 15. 本文当前结论（供 reviewer 推翻）

1. 不先恢复一个“传统双边 PMM live bot”；先收口 Stage 0 的 truth/order/accounting substrate。
2. 第一条可证伪业务线是 weather execution，而不是 generic neutral MM：先 maker/taker/skip，再 inventory lifecycle。
3. 现有真实证据对机械 maker acquisition 不利，所以 passive reduce/exit 可能比继续加仓型 maker 更值得优先评审。
4. selective weather MM 只能是 quiet-regime 的上层策略，不能把 information-event 前的 resting quote 当常态。
5. generic MM 应作为共享底座上的独立 head 和 capital sleeve；market selection 比调 quote 参数更先。
6. rebates、liquidity rewards、holding rewards 可能构成真实 profit pool，但必须动态核验、实际到账、单独出账。
7. 我们不是从零开始：selective market truth、weather lineage、modern execution、weather-specific actual fill reconciliation、legacy replay 和开源参考都在；但这**不等于 MM admission 已闭合**。真正缺的是把它们收成**一个 own-order truth、一个 target-state reconciler、一个涵盖 incentives/merge 的 realized ledger**，再用真实 fill denominator 做决策。
