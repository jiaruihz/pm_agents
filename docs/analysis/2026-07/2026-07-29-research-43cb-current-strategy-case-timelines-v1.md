# 43cb 当前天气策略：完整 ladder 典型案例时间线

钱包：
`0x43cb4ae1f4ddc9e671486c79c9f40a6fd98b84df`

## 数据快照

- public wallet latest activity：5,500 行；最近 50 个完整 event 中 39 个已结算、
  11 个未结算（未结算占 22%）。
- 本报告典型案例：3 个 `city × target_date`，共 13 个 execution phases。
- 上海 7/29：public `/trades` 共 89 个 fills；成交角色通过
  `takerOnly=false` 与 `takerOnly=true` 两个公开端点做差推断。
- 盘口来自 JRS archived full-ladder orderbook；天气 PIT 来自 JRS archived
  AviationWeather observations。外部钱包数据不进入本项目 canonical
  `fact_trades`，因此 canonical missing-bracket 口径为 N/A。

## 结论

当前策略不是单一“猜最高温”，而是一个 bot 执行的分层组合：

1. 用等 shares 连续 YES strip 表达 bounded range；
2. 随 running max 和盘口变化，把新增 shares 压到更窄的内层 strip；
3. 尽量让同批多腿的实际 VWAP 之和低于 1；
4. 极高概率腿偶尔在约 99.7¢ 主动卖出，尾部用极低价格继续挂单；
5. 大多数仓位仍持有到 settlement，不是双边做市。

最值得复制的是完整 ladder 的 payoff 优化和 maker-assisted 执行。不能直接复制
的是它对“最终仍落在这个区间内”的天气概率判断。

## 口径

- 分析 grain：一个 `city × target_date` 的完整互斥 ladder。
- 10 分钟内连续成交合成一个 execution phase。
- 盘口：phase 首笔成交前最近一张 archived full-ladder YES orderbook。
- public fill 时间是成交时间，不是挂单时间。
- weather PIT 是本地 archive 中 phase 前可见的 AviationWeather METAR。它是研究
  对齐，不证明钱包实际使用该源，也不等同于 Polymarket settlement source。
- 盘口快照一般相差 10–26 分钟；釜山 14:24 phase 只有 97.6 分钟旧快照，不用于
  判断当时盘口。

## 模式一：上海 7/29，纯 bounded-range carry

PIT station 为 ZSPD。13:47 入场时：

- observed / running max 都是 35°C；
- `dT_1h=0`、`dT_3h=+1.8°C`、CAVOK、风速 10kt；
- 它买入 `35/36/37/38 YES`，第一 phase 每档 46 shares；
- 第一 phase 四腿实际 VWAP 分别约
  `20.5/70.0/5.8/0.8¢`，合计 `97.10¢`。

随后温度一直停在 35°C，盘口概率从 36°C 向 35°C 迁移，但钱包继续按接近相同
shares 补四腿：

| 当地时间 | 成交前盘口 ask（35/36/37/38） | phase 实际四腿 VWAP 和 | phase shares |
|---|---|---:|---:|
| 13:47 | 53/48/2/0.7¢ | 97.10¢ | 各 46 |
| 14:11 | 48/56/4.4/0.8¢ | 97.29¢ | 约 207–212 |
| 14:46 | 83/20/1.9/0.2¢ | 97.90¢ | 约 92 |
| 15:24 | 91/11/0.3/0.2¢ | 98.14¢ | 各 64.59 |

这不是连续追买 35 YES。价格中心从 36 移到 35 时，它保持 payout 数量近似不变，
说明执行目标在 payoff space，而不是单腿 notional。

最终：

- 35/36/37/38 分别约 413.5/414.6/414.3/414.3 shares；
- 总成本 `$404.23`；
- 区间内最小 payout `$413.53`；
- winner 为 35°C，PnL `+$9.30`，按 buy cost 为 `+2.30%`；
- 区间外仍是全损。

因此这是“认为 35–38 的物理覆盖率足够高，再用 maker fills 把区间成本压到
97–98¢”的 bounded-range carry。

### 上海篮子与 `39 NO` 的严格区别

设每档都持有 `q` shares：

| 最终结果 | `35–38 YES` 等 shares 篮子 | exact `39 NO` | 假想的 `39-or-higher NO` |
|---|---:|---:|---:|
| 35–38 | q | q | q |
| exact 39 | 0 | 0 | 0 |
| 40+ | 0 | q | 0 |

所以该篮子在“当天已经触达 35°C”的条件下，经济上等价于
`final Tmax < 39°C`，即假想的 `39-or-higher NO`；它不等于 exact `39 NO`。
该 event 只有 exact 39 和 `40°C or higher` 两个独立 outcome，没有一张可直接
交易的 `39°C or higher` binary market。

首个 phase 前最近一张完整盘口快照约旧 23.2 分钟。当时：

- exact 39 YES 约 `0.003 / 0.004`，对应 exact 39 NO 约
  `0.996 / 0.997`；
- 40+ YES 为无 bid / `0.001` ask，对应 40+ NO 约 `0.999` bid、
  ask 接近 1 或不可得；
- 35–38 YES 的可见 asks 合计约 `1.037`，直接 taker 吃单并不存在 underround。

因此它不是看到一个静态的 `<1` 篮子后原子套利。第一 phase 实际成交价合计
`0.9710`，来自后续价格迁移和 maker-assisted fills。最终全 event 主成本
`$400.3089`，按 Weather taker fee 曲线约 `$3.9226`，实际现金成本
`$404.2313`；以区间最小 payout `$413.5264` 计算，fee-inclusive 篮子成本率
为 `0.97752`。若 35°C 下界已经完全锁定，则 break-even 要求其主观
`P(final >=39°C)` 低于约 `2.25%`；仍不是无风险套利。

### 上海的 maker / taker 与成腿方式

公开成交端点做差显示：

| role | fills | shares | principal | principal 占比 |
|---|---:|---:|---:|---:|
| maker（推断） | 20 | 322.43 | $222.63 | 55.62% |
| taker | 69 | 1,334.31 | $177.68 | 44.38% |

结构上非常有规律：

- 35°C 的 413.53 shares 中约 310.12 是 maker fills；
- 36°C 只有约 12.31 shares 为 maker，其余主要 taker；
- 37°C、38°C 全部是 taker；
- 贵的中心腿用 maker 节省 spread / fee，0–几 cents 的便宜尾腿直接 taker，
  因为后者的绝对 fee 很小。

它不能保证四腿原子成交。89 个 fills 跨约 2 小时 19 分钟完成；即使使用 batch
order，Polymarket 也只是并行处理各订单，FOK/FAK 只约束单笔订单，不保证跨
market 篮子全部成交。近乎相等的最终 shares 更像一个 target-share controller：
每次有腿成交后重算缺口，贵腿继续 maker 等待/改价，便宜腿 taker 补齐，并控制
已完成篮子的 fee-inclusive 总成本。过程中始终承担 legging risk；public 数据
也看不到未成交和撤销订单，不能把最终完整的成功篮子误当成每次都能完整成交。

### 上海 `<1` 成本是怎样形成的

前四个 phase 的 archived visible asks 始终大于 1，但最终成交 VWAP 始终小于 1：

| 当地开始时间 | archived 35–38 ask 和 | 实际四腿 VWAP 和 | 其中 35+36 实际 VWAP 和 | winner=35 的 phase PnL |
|---|---:|---:|---:|---:|
| 13:47 | 1.037 | 0.9710 | 0.9051 | +$1.332 |
| 14:11 | 1.092 | 0.9729 | 0.9559 | +$4.594 |
| 14:46 | 1.051 | 0.9790 | 0.9712 | +$1.898 |
| 15:24 | 1.025 | 0.9814 | 0.9777 | +$1.199 |

这些 archived book 分别旧约 15–26 分钟，不能把 ask 与实际 VWAP 的差额全归因
于 maker。能确认的机制是：

1. 39 与 40+ 没有被买入，只是被钱包当作可接受的遗漏上尾；它们的 0.1–0.4¢
   报价支持“市场也认为上尾很小”，但不直接制造篮子利润。
2. 35/36 是成本核心。13:47 的实际中心组合为
   `0.2052 + 0.6998 = 0.9051`；14:11 后概率中心反向迁回 35，新增组合变为
   `0.7519 + 0.2039 = 0.9559`。钱包不是追一个 winner，而是在不同价格状态
   持续补等 shares。
3. 全 event 55.62% principal 是 maker-inferred，主要集中在最贵的 35 腿；
   37/38 全部 taker。即“中心腿被动收货，便宜尾腿主动补齐”。
4. 因此这不是静态盘口套利，而是 path-dependent basket accumulation：
   预先在多腿放低于 ask 的 bids，利用 35/36 概率迁移和卖方流量分段成交，
   再用 taker 修复 shares 缺口。若只有一部分 maker 腿成交，钱包就承担未完成
   篮子的方向风险。

最终 `+$9.295` 中，首 phase 只贡献约 `$1.332`；其余约 `$7.963` 来自后续
补篮子。核心同时需要天气上尾判断、中心腿 maker 执行与后续概率迁移，不能归因
为单独某一项。

## 模式二：釜山 7/29，adaptive nested strip

PIT station 为 RKPK。它不是一次定死 35–40，而是随着当天升温路径逐层收窄：

| 当地时间 | weather PIT | 新增表达 | 实际 VWAP 和 |
|---|---|---|---:|
| 12:56 | max 34；3h +7.2°C | 35–39，各 14 shares | 97.19¢ |
| 14:24 | max 37；1h +1.8°C | 37–39，各 33 | 97.10¢ |
| 14:49–15:01 | max 37 | 继续补 37–39，约 74–85 | 98.32¢ |
| 15:17 | max 38；1h +1.8°C | 38–39，各 26 | 98.29¢ |
| 15:50–16:09 | max 从 38 升到 39 | 38/39 各约 1,184 | 92.03¢ |

最后一大 phase 最能说明机制：随着 39 变贵、38 崩到低价，它不是只追 39，而是
同时补齐 38/39 的 shares。它在维护内层 range payout，同时盘口迁移给了更低的
组合 VWAP。

16:57 后的动作又不同：

- running max 已到 39；
- 39 YES 盘口约 `99.0/99.7¢`，它卖出 40.79 shares，成交 99.7¢；
- 同时以 0.4¢ 买 300 shares 的 40 YES；
- 17:16 温度已回落到 38、running max 仍为 39，它又以 0.3¢ 买约 500 shares
  的 40 YES。

这不是做市。卖出的 39 只占最终 39 仓位很小一部分，更像 near-certainty cash
extraction；40 是极便宜的上尾扩展/部分损失回收仓。即使 40 胜出，约 800 的
payout 仍小于 event 的 `$1,194.76` net cash cost，因此它不是完整对冲。

最终 shares：

| bracket | shares |
|---|---:|
| 35 | 14.0 |
| 36 | 14.0 |
| 37 | 121.2 |
| 38 | 1,332.6 |
| 39 | 1,299.0 |
| 40 | 800.0 |

winner 为 39°C；buy cost `$1,235.42`，sell proceeds `$40.66`，PnL
`+$104.28`。按 buy cost ROI 为 `8.44%`。

它的真实表达是嵌套 strip：很小的宽区间底仓，37–40 的第二层，38–40 的第三层，
再加最重的 38/39 中心层。不能把 38、39、40 的成交分开解释。

## 模式三：伦敦 7/28，remaining-heat 判断失败

PIT station 为 EGLC。17:29 当地：

- current / running max 都是 29°C；
- `dT_1h=0`、`dT_3h=+1.8°C`、CLR、风速 5kt；
- 成交前 5.1 分钟盘口约：
  29 YES `77/79¢`、30 YES `21/24¢`、31 YES `0.5/1.8¢`。

钱包没有买已经触达的 29，而是买 30/31 YES 各 49.98 shares；实际 VWAP 合计
28.88¢。经济含义是它认为：

`P(final Tmax in 30–31 | 17:29 state) > 28.9%`

这不是 underround，因为区间不包含当前已实现的 29。当天没有继续升温，winner
为 29°C，`$14.43` 全损。

这个案例说明 directional overlay 的真正风险：盘口结构再漂亮，如果
remaining-heat / reheat hazard 高估，窄 future strip 会直接归零。

## 可借鉴程度

当前最近 50 events 中，98% 是连续 YES strip；但只有 18/50 的
`basket cost / common shares < 1`，而且只占 17.39% BUY cost。说明纯
bounded-range underround 是底层组件，不是全部 alpha。大部分资金仍依赖中心和
尾部概率判断。

建议按以下顺序借鉴：

1. **先复制结构层**：对完整 ladder 找连续区间，按 executable depth 计算等
   shares basket cost；只有在 fee 后小于 1 且物理区间覆盖率足够时进入。
2. **再复制执行层**：每条腿用统一 payout target，允许 maker 等待；新 observation
   到来后重新优化整个 ladder，不追单腿。
3. **modal overlay 单独 shadow**：只有我们的 PIT distribution 相对同分母 market
   baseline 在 frozen forward 稳定后，才增加中心档 shares。
4. **上尾 0.3–0.4¢ 不照抄**：它是小成本凸性，不是 40°C 主判断；必须和整个
   event payoff 一起限额。
5. **跟单不作为入口**：public fill 晚于挂单，且 archived book 有 10–26 分钟
   间隔。看到它成交后再追，通常拿不到它的 basket VWAP。

当前版本值得借鉴的是优化框架；天气方向本身还不能认为已经被我们复现。最新 PIT
详解样本只有两个 target dates，不能用这两天的 `7.11%` settled sample ROI 直接
升 live。
