# yourthos：NegRisk、RKSI 触发条件与自动化推断

## Data snapshot

- wallet replay snapshot：2026-07-29 05:05:07 UTC
- trigger analysis：1,561 trade activity rows，折叠为 1,512 个独立 Polygon transactions，30 个 target dates
- source evidence：Seoul AMOS 28,991 个 preferred-runway rows / 50,294 个 all-runway-max snapshots，覆盖 21 天；routine METAR 873 rows / 19 天
- exact entry-source join：7 个有完整 AMOS first-seen 的 current-NO 日期
- nearby station check：[AviationWeather API](https://aviationweather.gov/data/api/) RKSS/Gimpo historical METAR，覆盖 2026-07-15 以后
- boundary：只推断可观察行为；无法看到 wallet 私有 forecast、代码、订单 maker/taker flag 或人工审批动作

## NegRisk 是什么

首尔每日 Tmax 的多个 bracket 只有一个能赢，Polymarket 把它们作为一个互斥 multi-outcome event。官方 NegRisk 机制允许：

`1 share of bracket X NO → every other bracket 各 1 share YES`

例如只有 `30 / 31 / 32` 三档时：

`1 × 31 NO ≡ 1 × 30 YES + 1 × 32 YES`

两边在任何结算结果下的 payoff 都相同。虽然转换后得到多个 YES token，但因为最终只有一档能赢，总 payoff 仍最多为 1，不会凭空创造 collateral。

天气实战中，如果先以 0.80 买 `31 NO`，随后32已成为确定性新高，可以转换并卖 `32 YES @0.99`；其他 YES 已接近0。gross spread 约0.19，之后还要扣交易 fee、conversion fee/friction。

官方说明：[Polymarket Negative Risk Markets](https://docs.polymarket.com/advanced/neg-risk)；合约实现：[neg-risk-ctf-adapter](https://github.com/Polymarket/neg-risk-ctf-adapter)。

## 入场条件：不是单一“AMOS 跨档后买”

可严格对齐的7个 current-NO 首次入场中：

- 只有1次发生在 preferred-runway AMOS persistent cross 之后；
- 全部 current-NO 资金中，只有14.8% 在该 persistent-cross 状态后成交；
- 但7次首次入场里，6次发生在一条全跑道 AMOS 新 snapshot 出现后的30秒内，3次在5秒内，source age 中位数约10秒。

所以更合理的推断是：

`每条 AMOS snapshot 到达 → 自动重算 next-cross / final-Tmax 概率 → 有 edge 就下单`

而不是：

`等 AMOS 已经明确跨整数 → 无脑买 previous NO`

它在 source 尚未跨档时也会下 current NO，说明模型包含预测成分。

## 更可能使用的连续特征

### 1. 当前 RKSI 路径

已确认的主状态仍是：

- current NO：押 running Tmax 后续至少再升1°C；
- current YES：押 running Tmax 已成为 final exact；
- next/upper YES：表达下一档或尾部。

有路径覆盖的可分类资金中，current NO 占59.8%，current YES 占24.3%。

### 2. 时间与 remaining heat

- 91.8% BUY cost 在 target day；
- 81.4% 在首尔10–18时；
- 30个 event 的首笔入场中，11次发生在每小时`:30–39`，7次在`:50–59`。

这不像固定每天某一时刻下单，更像围绕 AMOS/METAR cadence 定时重算。时间是概率特征：上午/中午偏 next-cross，running max 稳定后偏 exact lock。

current-YES 资金约94%是在首次 running max 已出现至少90分钟后买入，支持 `running-max age + remaining heat/reheat hazard` 逻辑。

### 3. 可能使用 RKSS/Gimpo 等附近站

附近 RKSS 对其部分方向具有解释力：

| 日期 | 首笔附近状态 | wallet 表达 | 结果 |
|---|---|---|---|
| 7/16 | RKSS 30°C，RKSI running max 28°C | 买 `28 NO` | RKSI最终仍28，失败 |
| 7/24 | RKSS 31°C，RKSI 30°C | 买 `30 NO` | RKSI最终31，成功 |
| 7/23 | RKSS 31°C，RKSI约29°C | 买 `30+ YES` | 成功 |
| 7/26 | RKSS 32°C，RKSI 31°C | 买 `31 YES` | RKSI最终31，成功 |
| 7/28 | RKSS 31°C，RKSI 30°C | 买 `30 YES` | RKSI最终30，成功 |

这排除了“直接把 RKSS 温度当 RKSI 结算温度”的简单规则。更像将 RKSS–RKSI spatial gradient、海陆风/云层差异或热区领先作为协变量，再结合 RKSI 自身 path 判断是否会追上。

钱包行为不能证明具体用了 RKSS，但“其他站/区域温度场作为输入”的假设是合理的，而且比“只等 RKSI AMOS 跳变”更符合数据。

## 卖出条件

SELL cash 共约 $54,081，其中：

- $51,415，即95.1%，成交价在0.99以上；
- 低于0.01的163条 SELL 只回收约$36.6，主要是清理失败/失效 legs。

因此它通常不是在 `0.50 → 0.70` 做普通短波段止盈。主要 exit 更像：

1. source/routine report 令跨档或 winner 接近确定；
2. NegRisk conversion；
3. 以0.99–0.999卖确定性 YES、提前释放资金；
4. 失败腿在接近0时清理，或直接到期。

近期 terminal-exact event 则不卖，直接 redemption。

## 是机器还是人工

执行层面几乎可以判定为机器：

- 1,561 trade rows 中有1,512个独立 transaction hash，不是单笔订单被拆成大量 fills；
- event 内独立 transaction 间隔中位数22秒；
- 23.5% 间隔不超过2秒，42.9%不超过10秒；
- 峰值1分钟25个独立 transactions，5分钟96个；
- current-NO 首次入场对 AMOS snapshot 的 source age 中位数约10秒。

纯人工网页点击无法稳定完成这种速度和密度。最可能是：

`自动采集 + 自动信号计算 + 自动执行`

但仍不能排除人设定当日 regime、风险预算或手动启停。比较准确的表述是“bot execution 高可信，signal fully autonomous 中高可信，human-supervised 仍可能”。

## 最可能的状态机

当前证据支持：

```text
AMOS/RKSI snapshot 或附近站更新
  → 更新 running max、升温速度、station gradient、remaining heat
  → 估计 P(next RKSI cross) 与 P(final exact)
  → current NO / current YES / next YES 中选择 residual 最大的一腿
  → 分批加仓
  → routine METAR/盘口接近确定后：
       cross trade → NegRisk convert + sell ≥0.99
       terminal exact → hold to settlement
       thesis failure → 小额清理或承受到期损失
```

结论保持 research。仅凭7个 exact source-aligned current-NO 日期，不能复制成 live hard rule；但足以否定“固定时间下单”和“只在 AMOS 已跨档后买”这两个简单解释。
