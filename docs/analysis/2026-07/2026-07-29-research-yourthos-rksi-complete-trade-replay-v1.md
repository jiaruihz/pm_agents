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

## 全过程案例：监控状态 × 盘口 × 操作

以下盘口均为成交 phase 之前最近一张 archived full-ladder book，不是钱包的原始
挂单簿。公开 activity 只给 fill time，不给 order-post / cancel time；因此盘口
超过约 30 分钟时只保留作背景，不作为 contemporaneous executable quote，也不
用成交价与旧盘口的差异反推 maker/taker。

### 成功一：7/24，先做概率仓，cross 后确认

当天 winner 为 31°C。12:32 首轮时，routine RKSI 仍为 30°C、running max
已经 122 分钟没有更新；最新 AMOS 30.1°C，尚未 half-cross / persistent。
钱包已经开始买 30 NO：

- 12:32–13:00：买 575.01 shares `30 NO`，VWAP 0.814；同时用小仓配置
  `30 YES` 和 `31 YES`。最近 book 已旧 26.7 分钟，30 NO 为
  `0.73/0.76`，只能说明盘口处于快速重定价期。
- 13:12：再买 383.62 shares `30 NO @0.839`；routine 仍是 30°C，
  AMOS 30.3°C，仍未 persistent。
- 13:28–13:39：继续补 238.74 shares `30 NO @0.624`，并配少量上尾
  `32+ YES`。
- 13:55：AMOS 到 31.0°C，首次出现 `above_half=1 / persistent=1`；
  它再买 63 shares `30 NO @0.761`，同时清理 30 YES 与 32+ YES。

最终 BUY cash `$1,065.44`，SELL `$11.69`，settlement/reconstructed payoff
使 indicative pre-fee PnL 为 `+$306.61`，约为 BUY cash 的 `+28.78%`。
关键不是它有一个“过 30.5 才买”的硬触发，而是先持续更新跨档概率，persistent
cross 只是后续确认。

### 成功二：7/21，terminal exact

当天 winner 为 27°C。12:43 时 routine 已回落到 25°C，但 running max 仍为
27°C；最高点已经出现约 764 分钟，AMOS 仅 24.7°C，没有任何 cross 信号。它买
38.2 shares `27 YES @0.85`；成交前约 22 分钟的 book 为 `0.89/0.92`。
13:50 仍是 25°C / max 27°C，又买 41.67 shares `27 YES @0.85`，随后持有到
结算。

BUY cash `$67.89`，兑付 `$79.87`，indicative pre-fee PnL `+$11.98`
（`+17.65%`）。这是与 source-event current-NO 完全不同的第二套状态：
running max 很老、已经明显 pullback、剩余加热窗口有限，于是买 final exact，
而不是再押 overshoot。

7/28 是同类更大成功：逐步买入 `30 YES`，BUY cash `$2,274.74`，
indicative pre-fee PnL `+$503.79`。但本地 first-seen weather 覆盖在首笔时
明显陈旧，因此它只能证明操作和结果，不能用来证明钱包当时看到了什么天气输入。

### 失败：7/16，平台期仍持续加 current-NO

当天 winner 仍为 28°C。12:03 首轮时 routine / running max 都是 28°C，
最高点已经 93 分钟没有更新；AMOS 只有 27.0°C，距成交 4.4 秒，但
`above_half=0 / persistent=0`。

- 12:03–12:16：买 399.99 shares `28 NO`，VWAP 0.570；19.3 分钟旧 book
  的 28 NO 为 `0.80/0.83`。
- 12:30–12:31：routine 仍为 28°C、AMOS 仍为 27.0°C，又买
  1,170.62 shares，VWAP 0.671；15.7 分钟旧 book 已跌到 `0.51/0.55`，
  表明市场本身在剧烈重估。
- 13:38–13:57：routine 仍为 28°C，running max age 已到 188.5 分钟；
  AMOS 27.8°C，仍未 half-cross。它继续买 28 NO，并开始配置 29 NO、
  29 YES、30 YES。
- 14:29–14:41：running max age 接近 240 分钟、AMOS 27.3°C，依然没有
  cross，却又买 408.18 shares `28 NO @0.206`，只回收少量 29 NO。

最终 BUY cash `$1,223.49`，SELL `$72.76`，indicative pre-fee PnL
`-$1,150.73`。失败根因是跨档概率判断持续偏高，并且新 observation 没有触发
有效的 thesis invalidation / 降仓；不是简单的“价格买贵了”。

### 结构性成功：7/11，NegRisk conversion

11:31 起买 `31 NO`，BUY cost 约 `$3,176`；routine 随后跨到 32°C。
18:24 卖出约 3,839 shares `32 YES @0.999`，这是把 31 NO 的互斥 ladder
inventory 通过 NegRisk conversion 转成 winner YES 后退出。完整 event
indicative pre-fee PnL约 `+$672`。full-ladder book archive 从 7/15 才开始，
所以该日能复盘成交和天气路径，不能伪装成有 contemporaneous orderbook。

## 对我们的启发

1. 信号应拆成两个概率目标：`P(next routine cross / 30–60m repricing)` 与
   `P(final exact leaves current bracket)`。钱包早期主要交易前者，近期
   terminal hold 主要交易后者；混成一个标签会把 exit 逻辑学错。
2. 不应照抄 half-degree / persistent hard trigger。7 个有严格 AMOS
   first-seen 的 current-NO 首次入场中，只有 1 个发生在预定义 persistent
   cross 之后；更像是每条新 AMOS snapshot 都触发连续概率更新。
3. NegRisk conversion 是执行与资金回收层，不是新的天气信号。必须按完整
   ladder payoff vector 看 event，不能逐 token 把 31 NO 与后来卖出的
   32 YES 当两笔无关交易。
4. 最大可改进点是 state-aware invalidation：当 running max 持续变老、
   AMOS 不接近 settlement lattice、`p_final_leave` 低于 executable cost
   时停止加仓并减仓。7/16 说明没有这个更新会把早期小错放大成整场大亏。
5. 不做公开地址成交后的跟单。1,512 个唯一交易中，event 内中位交易间隔仅
   22 秒，峰值 25 tx/min；public fill 又晚于原始挂单时点，看到成交后通常
   已失去它的盘口与延迟优势。

下一步只做 zero-notional shadow：在同一批 PIT rows / quotes / settlements
上比较 `own probability model`、`wallet-follow`、`own + wallet confirm`、
`own + wallet veto` 与当前 hard trigger。至少积累 15 个新的 Seoul
target dates，先比较 logloss / Brier / calibration，再看官方 fee 后 EV/PnL
并按 target_date block bootstrap。现在不改 live：严格对齐的 current-NO
日期只有 7 个，外部 public cashflow 没有 canonical fee evidence，也没有完整
opportunity denominator。

## 新增产物

- case timeline：
  `generated/yourthos_rksi_replay_v1/case_timelines_with_book_v1.json`
- 可复跑脚本：
  `scripts/analysis/wallet_weather/research_yourthos_rksi_case_timeline_v1.py`

## JRS immutable snapshot 与长期绩效复核

2026-07-29 14:58:56 UTC 已按标准 external-wallet pipeline 直接采集并持久化到：

`/Volumes/jrs-archive/pm_agents/research/external_wallet_weather/raw/wallet=0x4f1164d1531b8fb77919df285c576628318553b0/snapshot=20260729T145856Z`

完整性 gate：

- 44/44 个 UTC day windows 完整；
- 全账户 public activity 1,944 rows，weather derivative 1,817 rows；
- 30 个 weather events、170 conditions、330 Gamma markets；
- 30/30 event metadata 完整，missing slug 为 0；
- 124 个 immutable artifacts、2,222,255 bytes，逐文件 SHA256 登记；
- 已在同一 snapshot 下生成 `analysis/full_ladder_history_v1/`。

按真实 public cashflow、`city × target_date × complete ladder` grain 复核：

| metric | value |
|---|---:|
| cashflow-complete settled dates | 29 |
| profitable / losing | 24 / 5 |
| win rate | 82.76% |
| BUY cost | $62,222.29 |
| public cashflow PnL | +$2,539.18 |
| turnover ROI | +4.08% |
| target-date bootstrap 95% CI | [-18.35%, +23.96%] |
| profit factor | 1.35 |
| average win / average loss | +$404.89 / -$1,435.63 |
| event PnL standard deviation | $1,231.24 |
| maximum drawdown | $5,649.25 |

胜率高，但 payoff 明显负偏：平均亏损约为平均盈利的 3.55 倍；6/26 单日
`-$5,649.25` 占全部 gross loss 的 78.7%，从 6/25 的 equity peak 到 7/28
才恢复。早期 6/16–7/16 为 22 dates、18 胜、ROI 仅 `+1.60%`；近期
7/21–7/28 为 7 dates、6 胜、ROI `+24.11%`，但样本太小且恰逢 exit regime
切换，不能外推为长期水平。

因此完整策略结论为 `inconclusive / shadow_candidate`：值得借鉴 source-event
与 terminal-exact 的状态机、每 observation 重算概率、NegRisk conversion 和
资金释放；不值得照抄成交、仓位或高胜率叙事。bootstrap CI 跨 0、没有
opportunity denominator / same-time market baseline / canonical fee evidence，
不满足 live promotion。
