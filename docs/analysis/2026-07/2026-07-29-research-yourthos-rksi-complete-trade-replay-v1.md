# yourthos：RKSI 完整交易与 source-event 复盘

## Data snapshot

- snapshot：2026-07-29 05:05:07 UTC
- wallet：`0x4f1164d1531b8fb77919df285c576628318553b0`
- public activity：1,914 rows，未触及 API 上限；其中 Seoul weather 1,787 rows
- event grain：30 个 Seoul × target_date，29 个已结算，覆盖 2026-06-16 至 2026-07-29
- weather join：14 个日期有 RKSI timestamped path，13 个首笔时点有可用 PIT observation；其余日期只分析交易，不事后补天气特征
- settlement：canonical `settlement_outcomes`
- PnL：完整 ladder payoff 重建的 indicative pre-fee cash PnL；未扣 protocol fee、gas，也不把公开 activity 未展示的 NegRisk conversion 当成额外收益

## 结论

它不是经典的双边做市商。主策略是 **RKSI source-event directional arbitrage + NegRisk inventory conversion**，近期又明显转向 **terminal exact YES 持有到结算**。

29 个已结算 event：

- 16 个：主动 round trip，卖出现金覆盖主要买入、结算只留极小 residual；
- 9 个：主要持有到 settlement；
- 4 个：未充分退出且结算兑付不足，属于止损不足或到期亏损。

策略有明显换挡：

| 时期 | events | 主行为 | BUY cash | indicative pre-fee PnL |
|---|---:|---|---:|---:|
| 6/16–7/16 | 22 | 16 次 active round trip，3 次 hold，3 次失败 | $55,369 | +$1,217 |
| 7/21–7/28 | 7 | 6 次 hold settlement，1 次小额失败 | $6,853 | +$1,653 |

因此不能用最近仓位把它概括成“只等结算”，也不能用早期 SELL 把它概括成纯做市；它近期改变了 exit policy。

## 为什么必须合并完整 ladder

公开 activity 不展示中间 NegRisk conversion。典型路径是：

1. 买 `31°C NO`；
2. 该 payoff 等价于持有其他所有 bracket 的 YES；
3. 温度跨到 32 后，把 inventory 转换并卖 `32°C YES @0.999`。

如果逐 token 计算，会错误地认为它同时“保留 31 NO”又“凭空卖出 32 YES”，从而重复计算 inventory 和 PnL。按完整互斥 ladder 的 payoff vector 重建后，30 个 event 中有 10 个能直接看到这种 converted short asset 痕迹。

## 一般交易生命周期

### 1. 入场

- 91.81% BUY cost 在 target day。
- 53.84% 在首尔 10–14 时，27.57% 在 14–18 时；两段合计 81.41%。
- 首个 15 分钟 entry VWAP 的 event 中位数是 0.590。
- 它常先小仓试探，再随着 source confirmation 加仓，因此全部买入成本中 72.97% 最终成交在 0.80 以上。

### 2. 两种核心方向

在有 RKSI 路径的可分类资金中：

| expression | BUY cost share | VWAP | 含义 |
|---|---:|---:|---|
| current NO | 59.78% | 0.790 | 押当前 running Tmax 至少再升 1°C，或已经从更快 AMOS 看到 cross |
| current YES | 24.26% | 0.711 | 押当前 running Tmax 已成为 final exact |
| next YES | 5.83% | 0.529 | 押下一档 |
| upper-tail YES | 5.50% | 0.226 | 便宜尾部/跨档腿 |
| future-bracket NO | 4.63% | 0.657 | 对更高档做 NO |

全历史按 cash 看是 67.99% NO、32.01% YES。NO 占优不是简单“看空温度”，主要是买 current-bracket NO 表达 overshoot/cross。

### 3. source-event 后退出

current-NO 资金中：

- 38.8% 在下一次 routine RKSI observation 跨过该 bracket 之前 30 分钟内成交；
- 76.0% 在跨档前 60 分钟内成交；
- 10.8% 后续没有跨档，集中在失败日。

这与 AMOS 比 routine METAR 更早显示 runway temperature cross 的机制高度一致。它等 routine report/市场重定价后，通过 NegRisk conversion 或直接 SELL 释放 inventory；有卖出的 event，首次卖出距首次买入的中位数约 134 分钟。

### 4. terminal exact 后持有

current-YES 资金约 94% 是在当日首次 running max 已出现至少 90 分钟后买入。其行为不像追第一条高温打印，更像在判断 remaining heat、reheat 与 overshoot 风险已经下降后做 exact lock。

近期例子：

- 7/21：买 `27 YES @0.85`，持结算；
- 7/23：买 `30+ YES`，持结算；
- 7/26：13:34 起买 `31 YES`，VWAP 0.782，持有 1,641 shares，结算；
- 7/28：12:31 起买 `30 YES @0.50`，之后加到约 0.91，持有 2,778 shares，结算。

## 三个代表性 event

### 7/11：source-event round trip

- 11:31 首买 `31 NO @0.50`；
- 11:31–12:02 连续加仓，`31 NO` BUY cost 约 $3,176；
- routine RKSI 随后跨到 32；
- 18:24 卖出约 3,839 份 `32 YES @0.999`，这是 `31 NO` NegRisk conversion 后的经济退出；
- 完整 ladder 重建的 indicative PnL 约 +$672。

这不是同时押多个互相矛盾的档，而是“买 previous/current NO → source cross → 转换并卖确定性 YES”。

### 7/16：current-NO 失败

- 12:03 买 `28 NO @0.67`，后续大量加仓；
- RKSI 当日 final 仍为 28，没有 overshoot；
- 只回收约 $72.8，indicative loss 约 -$1,151。

说明它并非总能提前知道确定性结果；部分仓位是有真实 overshoot 风险的方向交易。

### 7/28：terminal exact hold

- RKSI path 已打印 30；
- 12:31 买 `30 YES @0.50`，随后逐步加仓到高赔率；
- 没有卖出，持有到 30°C settlement；
- indicative PnL 约 +$504。

## 做市、波段还是结算

- **不是经典做市**：没有持续、对称地在完整 ladder 两边报价，风险主要集中在 current/next bracket。
- **早期主要是 source-event 波段/延迟套利**：16/22 个早期 event 主动 round trip，依靠 cross 后快速重定价和 NegRisk conversion。
- **近期主要等结算**：7/21–7/28 的 7 个已结算 event 中 6 个主要持有到 settlement。
- 它可能使用 maker limit 获得部分 fills，但 public activity 没有 maker/taker flag，不能据此证明订单角色；策略层面显然不是 delta-neutral market making。

## 与我们的 RKSI 经验合并

它做的核心与我们的 `previous-NO source-event` 研究非常接近，但 exit 更主动：

1. AMOS/source persistence 暗示 current bracket 已经或即将被跨过；
2. 在 routine METAR/WU 完全重定价前买 current/previous NO；
3. routine cross 后转换/卖出，而不是无条件把 NO 持到 final WU；
4. 若进入 terminal regime，则改为 current exact YES 并持结算。

这也解释了为什么只看最终胜负会误判：它早期交易的直接目标常是 `next routine METAR cross` 与盘口重定价，不一定是 `final WU leaves prior bracket`。

当前最值得复制进研究的不是钱包信号，而是状态机：

`AMOS persistent cross → current-NO executable residual → next routine confirmation/repricing → NegRisk conversion exit`

以及独立的：

`running max age / remaining heat / reheat hazard → current exact YES terminal hold`

结论保持 `research / shadow only`。我们的 AMOS 历史 first-seen 只覆盖部分 wallet 日期，尚不能确认它使用的具体 feed，也不能从 13 个 PIT entry dates 直接生成 live gate。
