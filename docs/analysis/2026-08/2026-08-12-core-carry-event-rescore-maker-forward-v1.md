# Core Carry event-rescore 与 additive maker forward v1

Status: `zero-notional event shadow / existing +5 maker live evidence / no selector change`

## 动作

- 新增独立 `current_yes_core_carry_event_rescore_shadow_v1`：在新 METAR/官方观测、forecast curve 内容变化、exact-bracket/token 变化后，用冻结 Core v3 和最新十股完整 ask ladder 重新评分。
- event shadow 完整记录所有 state transition；研究 selector 使用 `market_mid >= 0.50`，但不创建 TradeIntent/order，当前 live 的 `0.80` 授权线和固定小时 checkpoint 均不变。
- maker 不再受“必须固定10股总风险”的研究约束。当前生产本来就是 `10 taker + 5 maker`，这五股继续作为真实 additive capacity probe；本轮不叠加第二个五股 sleeve，因此单信号仍最多15股。

## 可证伪假设

1. 在与固定小时 Core 相同的城市、时段、模型和十股执行成本下，first-seen state transition 能产生新的正 net-EV city-day，而不是只重复现有 Core entry。
2. `0.50 <= mid < 0.80` 的 event-selected first-positive city-days 在 frozen forward 上相对同一时点 market executable cost 有正的 fee-adjusted residual。
3. 当前额外五股 staged maker 在实际成交条件下能增加绝对 PnL；paired price improvement、fill 后 adverse selection 和最终 settlement PnL 分开评估，不用 future touch 代替 fill。

## 固定分母

### signal funnel

```text
Core PIT state rows
-> first observed state transition (METAR / forecast hash / exact bracket-token)
-> local 13:00–17:59 + valid observation clock
-> fresh ten-share executable book
-> first positive event per city-day
```

每个 transition 都进入 `event_scores.jsonl`。不在时段、观测 stale、盘口失败或达到采集预算属于 coverage/blocker，不伪装成 selector 过滤。

### evidence funnel

```text
event score -> PIT fresh book -> settlement -> executable ten-share replay
maker root intent -> authenticated fill/cancel -> paired price -> post-fill markout -> settlement
```

event 结果按 target_date block；与当前 fixed-hour Core 在相同 city-day 上做 paired A/B，并单列真正新增 city-day。maker 只认 authenticated terminal/canonical fill。

## 资源与停止条件

- 不新增天气或盘口 collector；tail 当前 Core append-only state journal，从部署时 EOF 开始，不扫描634MB历史文件。
- 仅 transition 触发 CLOB book；每轮最多40次、每 UTC 日最多2,000次请求。按约2KB/response估计，代理响应量上限约4MB/日；event journal 预计低于10MB/日。
- 首次冻结复核：event shadow 累计30个新的 settled target dates。maker 在至少30个新的 maker-eligible intents、10个 target dates 后做一次预注册复评；这只是 review clock，不自动升 live。

## 当前证据边界

- event checkpoint 在 `>=0.80` 的旧短窗没有新增 city-day；`0.50–0.80` 历史点估为正但 CI 跨0，最近窗口为负，因此只能 shadow。
- 当前 live maker 最近15/28 root intents 成交，paired price improvement `1.18c/share`，但 maker leg ROI `2.85%` 低于 taker-only `7.77%`；它目前证明了价格改善和绝对 PnL 增量，没有证明组合 ROI 提升。

```text
significance=FAIL
baseline=PARTIAL
forward=IN_PROGRESS
conclusion=inconclusive / zero-notional event shadow only
```

