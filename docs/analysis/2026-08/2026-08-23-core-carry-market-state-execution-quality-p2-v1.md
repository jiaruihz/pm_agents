# Core Carry 市场状态与 execution quality P2 合同（2026-08-23）

## 结论

Core Carry 的问题不是“maker 或 taker 二选一”，而是目前没有把 **信号质量** 与
**执行状态** 分开建模。概率模型回答 current bracket 是否会赢；execution layer 还必须回答：

1. first-positive 是天气/时间信息逐步改善，还是 current token 突然被卖穿造成；
2. 低价是短暂流动性折价，还是知情流开始后的第一口；
3. current 的概率质量是否迁移到 next/up ladder；
4. maker 的价格改善能否覆盖 fill 后 markout、未成交机会成本和尾部 loss。

因此 P2 不新增价格 hard gate，也不把 maker 整体删除。先把 execution quality 建成独立、
连续、可复用的证据层，再在同一 signal 分母上比较 maker / event-time confirm / hedge / skip。

## 已落地的生产边界

- Core profile `split_taker_shared_maker_staged_to_pullback_v7`：固定 `10 taker + 5 shared maker`；
  staged 与 pullback 共享同一个 5-share root，只有 staged 已确认撤销后才能 handoff，最大 exposure 15。
- Core release `c6d9027e7d2ea46f077e92c8620921124f5c2e1e`；WS release
  `34eb44c702a9538dd85ef1a179bd5ba2d4ad7c41`。
- candidate capture 只覆盖 frozen selector 中“唯一 blocker 为 `non_positive_taker_ev`”的候选：
  最多 8 个 current token；其中 edge 最接近 0 且有完整 token lineage 的一个候选申请 full ladder。
- candidate demand 是 P1、30 分钟、research-only；first-positive full ladder 仍为 P0。WS selector v8
  按 `priority → requested_at` 分配 24-token 预算，且 full-ladder group 要么完整接纳、要么完整拒绝。
- 采集不改变 selector、core taker、maker eligibility、shares 或 venue action。

## Shanghai 2026-08-22 anchor

grain：`current_yes_core_carry_tiny_live_v2 / Shanghai / 2026-08-22 / bracket 30`。

触发时 `06:51:36Z`，book `0.85 / 0.87`，model p `0.90441`，10-share effective cost
`0.87566`，net edge `+0.02875`。旧 v6 同时创建三个独立 child：

| fill UTC | role | qty | price | cost | fee |
|---|---:|---:|---:|---:|---:|
| 06:51:45 | taker | 10 | 0.87 | 8.70 | 0.05655 |
| 06:54:24 | staged maker | 5 | 0.86 | 4.30 | 0 |
| 06:59:54 | pullback maker | 5 | 0.85 | 4.25 | 0 |

权威 `fact_trades` 因而确认总计 20 shares、principal `$17.25`。可见 quote 路径为：

| UTC | bid | ask | 事件 |
|---|---:|---:|---|
| 06:52:05 | 0.85 | 0.87 | 两个 maker 已同时在场 |
| 06:53:34 | 0.85 | 0.88 | staged 尚未成交 |
| 06:54:24 | — | — | staged `5 @ 0.86` 被吃 |
| 06:58:23 | 0.78 | 0.85 | bid 下降 7c，明显 adverse move |
| 06:58:44 | unavailable | unavailable | runner 尝试 pre-report terminal handling |
| 06:59:54 | — | — | pullback `5 @ 0.85` 最终也成交 |

staged fill 到 `06:58:23` 的约 239 秒 markout，以 midpoint 计为 `0.815 - 0.86 = -4.5c/share`；
单看“比 trigger ask 改善 1c”会把这笔执行错误地评为优质。该窗口的 current token 不在 WS
subscription epoch，且无 full-ladder WS，因此不能可靠识别 aggressive side、OFI、support cancellation、
`q_up/q1/alpha1` 或 actor。这正是 P2 采集层要补的缺口。

## 统一 feature contract

所有特征必须以 trigger/candidate 的 event-time 为零点，固定输出
`30/60/120/300/900/1800s`，不以最终结果反选窗口。

### 1. Coverage 与 clocks

- `candidate_at / trigger_at / exchange_event_at / receive_at`
- subscription epoch、baseline ref、delta hash、reconnect/gap、reconstruction parity
- current token `t-15m..t+30m`；full ladder `t-5m..t+30m`
- coverage 不完整时状态只能是 `unknown`，不能回退到事后 REST 伪造高频状态

### 2. Edge 穿越原因

- `weather_time_edge_delta`
- `market_feature_edge_delta`
- `ask_cost_edge_delta`
- `old_ask_counterfactual_edge`
- `old_market_counterfactual_edge`

输出连续贡献和 `quote_driven_candidate` 标签；标签只是解释变量，不直接阻止下单。

### 3. Current-token microstructure

- bid/ask/mid/spread 与 top-3 depth，全部转成 tick 和 shares
- aggressive buy/sell volume、trade count、size distribution
- add/cancel volume 与 cancellation-adjusted OFI
- support depth / order size、support age、support survival
- shock 后 30/60/120/300 秒 markout 与 recovery fraction
- maker queue age、queue ahead proxy、fill hazard

### 4. Full-ladder migration

- spread-weighted simplex 的 `q_up / q1 / alpha1`
- `Δq_up / Δq1 / Δalpha1`
- current aggressive sell 是否同时出现 next/up aggressive buy
- current 丢失的 midpoint mass 有多少被 next、far-up 或 residual 吸收

### 5. Information-risk context

- 距下一预计 report 的秒数、report phase
- last weather event 与 forecast revision 的四时钟
- forecast-actual divergence、late-high / reheat hazard
- actor/pseudonym recurrence 只做 shrinkage evidence；缺 wallet 时显式 unavailable

## 状态不是一个过拟合分数

P2 首先保存连续 feature vector，再产生可审计的候选状态；状态暂不控制 live：

| 状态 | 机制定义 | 第二 sleeve 的 shadow action |
|---|---|---|
| `healthy_passive_candidate` | current 无持续 aggressive sell；`q_up` 稳定/下降；支撑存活；冲击后恢复 | passive maker |
| `transient_dislocation_candidate` | current 短时下压，但 30–120s 恢复且无跨档质量迁移 | event-time confirm 后 incremental |
| `cross_bracket_adverse_candidate` | current sell 与 `q_up/q1` 或 up-rung buy 同向增加 | 不挂 current maker；评估 3–5 next hedge |
| `persistent_adverse_candidate` | current markout 持续恶化、支撑撤单、OFI 为负且无恢复 | 不增加第二 sleeve |
| `unknown` | pre-trigger、reconstruction 或 trade-side 证据不完整 | 不作为模型训练标签或 live 结论 |

这些名称刻意带 `candidate`：在 clean forward 完成前，它们不是 production gate。

## Execution-quality 目标函数

不能用 maker fill rate、成交价改善或 maker 单腿 PnL 单独做目标。每个 signal 固定比较：

1. immediate taker baseline；
2. shared passive maker；
3. event-time confirm 后 incremental；
4. selective next hedge；
5. 不增加第二 sleeve。

主指标是相对 immediate taker 的 fee-adjusted implementation surplus：

```text
price_improvement
- post-fill adverse markout
- unfilled opportunity cost
- hedge cost
- fee
```

并单列 fill probability、time-to-fill、30–1800s markout、settlement PnL、最大单 signal exposure。
maker 只有在同分母净 surplus 为正时才是 execution alpha；“成交更便宜”本身不够。

## 冻结与晋升

- historical 92-row ledger 只作诊断，Warsaw/Amsterdam/Shanghai 只作 anchor，不参与阈值选择后的验证。
- forward 按 target_date block；signal funnel 与 evidence funnel 分开。
- coverage/parity 未通过的行保留但不填值；不得用 REST hindsight 补 WS 缺口。
- 先比较连续特征方向与经济量级，再冻结一个简单 router；不在小样本上训练 GBM/RF。
- router 先 zero-notional，之后才可申请把 shared 5-share sleeve 按状态路由；10-share core 是否改变是另一项独立研究。
- live 晋升要求同分母 frozen forward 中 execution surplus 的 target-date block CI 支持正值，且 tail
  exposure、coverage、reconstruction parity 与取消/成交血缘同时通过；不设置事后最优价格门。

## 当前 readiness

| 项目 | 状态 |
|---|---|
| 共享 5-share maker exposure | live，已验收 |
| first-positive 后 full ladder | P0 active，等待新 trigger |
| candidate-stage current token | deployed，等待新有效 candidate |
| candidate-stage full ladder | deployed，按 P1/atomic budget 等待新有效 candidate |
| priority-safe WS allocation | live selector v8 |
| Shanghai current quote/fills | complete |
| Shanghai trade side/full ladder/actor | unavailable；当时未订阅 |
| market-state router | zero-notional 已部署；release `e13dcc5a`，controller health healthy |
| deterministic reconstruction | 复用 `ws_incremental_book` 唯一 truth；epoch/gap/reconnect/parity fail closed |
| 同分母 action replay | immediate taker / actual shared maker / 120s confirm / next hedge / skip 已冻结 |
| performance gate | 0 first-positive / 0 target dates；gate fail，正 notional 禁止 |

生产 runtime 每60秒自动刷新 feature、真实 maker order/fill join、1800秒 fee-adjusted implementation
surplus、target-date block CI 与 promotion gate。晋升仍要求至少30个独立 forward target dates、coverage
不低于95%、同分母 paired surplus 完整且CI下界大于0；在此之前不能声称已识别出稳定状态 alpha，
也不会创建 TradeIntent、plan、order 或 venue call。
