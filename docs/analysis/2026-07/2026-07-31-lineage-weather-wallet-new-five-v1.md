# Weather 候选钱包新增五人全历史与典型链路 v1

> 钱包：`opopv.` / `balthazar` / `LMVM` / `neo7777` / `macau.weather`
> 快照截止：前四个为 2026-07-30 18:07:40 UTC，`macau.weather` 为 18:20:28 UTC
> 主 grain：`wallet × city × target_date × complete mutually-exclusive ladder`
> 研究边界：外部公开钱包；private signal / plan、未成交订单、原始 post time、cancel 和 queue rank 不可见

## 结论与建议

这五人里，**最值得学习的不是同一个人**：

1. **综合复制参考：`neo7777`**。162 个独立 target_date，长期 ROI 3.47%，去掉最佳五日仍有 2.34%，后半段 3.73%；中位 6 笔 BUY、4 个 session，复杂度可接受。
2. **最简单的执行模板：`LMVM`**。96.0% BUY cost 在 YES，绝大多数是单档 YES；中位 4 笔 BUY、2 个 session、13 分钟建仓，长期 ROI 3.39%。但 98.7% event 都主动 SELL，核心更像短周期 repricing，不是持有到天气结算。
3. **最值得单独深挖的结构套利：`balthazar`**。以整条 ladder 的 NO basket、SPLIT/MERGE 和 NegRisk `CONVERSION` 为主；64 个 target_date 全部正收益，长期 ROI 3.79%，去掉最佳五日仍有 3.54%。它不是简单天气预测，复制前必须先实现完整 conversion 会计和 executable full-set pricing。
4. **做市/库存参考：`opopv.`**。长期 ROI 4.45%、CI 显著为正，但中位 39 笔 BUY、23 个 session、33 小时建仓，最大日期回撤 -$7.54k；不适合直接照搬。
5. **香港同城机制参考：`macau.weather`**。96.9% 近期 BUY cost 集中香港，长期 ROI 13.86%，但 CI 跨零，去掉最佳五日变成 -6.58%。它最像 WeatherHK2 的地域专注，却最不适合按历史收益复制。

推荐研究顺序：

```text
balthazar 的 full-NO-set / CONVERSION 定价
→ neo7777 的低复杂度 mixed expression
→ LMVM 的 pre-target single-YES repricing
→ macau.weather 与 WeatherHK2 的香港 source-to-settlement basis 对照
```

全部先做同分母 zero-notional shadow，不改 live。

## 五个地址与发现依据

| 名称 | 地址 | 入选原因 | 近期形态 |
|---|---|---|---|
| `opopv.` | `0x7c63520c2ca9b336af0c205b9ccf68217bb393d4` | 当前月度天气榜高位、长期活跃 | 全球分散，YES/NO 混合，高 maker inventory |
| `balthazar` | `0x5a218c7ad04135830a45c41aaed7294df7809318` | 月度与长期榜均稳定 | 85.1% cost 在 NO，密集 conversion/merge |
| `LMVM` | `0xc9ded4d5f6eec0a75907ebb924c164a4aea381d9` | 当前月度高位、交易结构简单 | 96.0% YES，D-2/D-1 单档主动 SELL |
| `neo7777` | `0xd25156e222c9b907b128e27c36821fdb41db4d37` | 长历史、当前月度持续盈利 | YES/NO 混合，方向与主动退出并用 |
| `macau.weather` | `0x4989bfed5900ba096b08ba1f9b718464527c983e` | 香港专注，可与 WeatherHK2 对照 | 96.9% 香港，YES strip，maker+taker 混合 |

排行榜数字只用于发现，不进入下文绩效。主结果重新从完整 public activity、Gamma complete ladder、结算 winner 和 conversion cashflow 计算。

## 数据快照与完整性

| 钱包 | public activity 覆盖 | weather activity | city-day portfolios | cashflow-complete | metadata missing |
|---|---|---:|---:|---:|---:|
| `opopv.` | 2026-03-12 → 2026-07-30 | 300,777 | 3,784 | 3,658 | 3 |
| `balthazar` | 2026-05-26 → 2026-07-30 | 179,572 | 3,095 | 3,013 | 2 |
| `LMVM` | 2026-05-11 → 2026-07-30 | 3,703 | 307 | 292 | 0 |
| `neo7777` | 2026-02-12 → 2026-07-30 | 32,754 | 1,346 | 1,320 | 2 |
| `macau.weather` | 2026-06-10 → 2026-07-30 | 5,541 | 85 | 82 | 0 |
| 合计 | — | **522,347** | **8,617** | **8,365** | **7** |

每日 activity 窗口都低于 API 5,500-row 上限；饱和日递归拆分。closed-position endpoint 对大钱包存在 10,000-row 截断，但 PnL 使用完整 activity，不从 closed-position 列表相加。

20 个代表案例合计 1,445 个 transaction，1,445/1,445 receipt 找到；解出 1,455 个当前 CLOB V2 fill event，其中 903 个 passive-maker fill、552 个 taker fill。`opopv.` Seoul 3/19 的 4 个交易和 `neo7777` Ankara 3/16 的 25 个交易属于旧合约，receipt 完整但没有当前 V2 topic，maker/taker 保持 unclassified。

机器可读结果：

- [五钱包统一绩效](generated/weather_wallet_new_five_summary_20260731_v1/comparison.json)
- [opopv. cases](generated/weather_wallet_new_five_lineages_20260731_v1/opopv.json)
- [balthazar cases](generated/weather_wallet_new_five_lineages_20260731_v1/balthazar.json)
- [LMVM cases](generated/weather_wallet_new_five_lineages_20260731_v1/lmvm.json)
- [neo7777 cases](generated/weather_wallet_new_five_lineages_20260731_v1/neo7777.json)
- [macau.weather cases](generated/weather_wallet_new_five_lineages_20260731_v1/macau_weather.json)

## 数据治理：NegRisk CONVERSION 漏记修复

本轮首次遇到 conversion-heavy 钱包。旧 full-ladder 公式是：

```text
SELL + REDEEM + MERGE - BUY - SPLIT
```

它漏掉了 `CONVERSION.usdcSize`。该字段是完整 NO 套组兑换出的抵押现金 inflow；`balthazar` 的 8,102 条 conversion 合计 $301,497.98。修复后的公式为：

```text
SELL + REDEEM + MERGE + CONVERSION - BUY - SPLIT
```

影响半径：

| 钱包 | 受影响原始时间窗 | conversion rows | conversion cash | cashflow-complete PnL 影响 |
|---|---|---:|---:|---:|
| `balthazar` | 2026-05-26 → 2026-07-30 | 8,102 | $301,497.98 | **-$263,914.02 → +$30,557.61** |
| `neo7777` | 2026-02-12 → 2026-03-12 | 40 | $1,380.27 | +$23,749.47 → **+$25,129.73** |
| `macau.weather` | 2026-07-18 | 1 | $0 | 无 PnL 影响 |
| `opopv.` / `LMVM` | — | 0 | $0 | 无影响 |

总 conversion cash 与 cashflow-complete PnL 变化不完全相等，因为未结算或当前价值未归零的 portfolio 不进入已结算分母。

旧一批报告的影响检查：

- WeatherHK2 有 1 条 conversion，但 `usdcSize=0`，重放前后 PnL/ROI 不变；
- HighTempTation、badatmath、MidYes56b、jjavi 的对应快照均为 0 条 conversion，不受影响；
- 因此污染只涉及本批原始的 `balthazar` 和 `neo7777` 中间结果；本报告和机器产物均已全量重放修复。

## 长期盈亏、ROI 与稳健性

主 ROI：

```text
Σ(SELL + REDEEM + MERGE + CONVERSION - BUY - SPLIT) / ΣBUY cost
```

平均日 ROI 先把同钱包同一 `target_date` 的全部城市合并，再对日期等权平均；它不是资本回报率，主决策仍以资金加权长期 ROI 为准。

| 钱包 | resolved city-day | 日期 | BUY cost | PnL | 长期 ROI | 平均日 ROI | 中位日 ROI | 95% target-date CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `macau.weather` | 82 | 50 | $26,079 | **+$3,613.58** | **13.86%** | 14.83% | 8.27% | **[-6.17%, 36.71%]** |
| `opopv.` | 3,658 | 130 | $804,773 | **+$35,772.98** | 4.45% | 10.03% | 3.84% | **[1.95%, 7.33%]** |
| `balthazar` | 3,013 | 64 | $806,675 | **+$30,557.61** | 3.79% | 3.95% | 3.58% | **[3.39%, 4.20%]** |
| `neo7777` | 1,320 | 162 | $724,701 | **+$25,129.73** | 3.47% | 9.80% | 3.76% | **[1.73%, 5.17%]** |
| `LMVM` | 292 | 64 | $104,038 | **+$3,522.86** | 3.39% | 10.83% | 7.22% | **[0.80%, 5.76%]** |

| 钱包 | 最近 30 日期 ROI | 去掉最佳五日 | 后半段 ROI | 最大 target-date 回撤 | BUY 复杂度中位 | 首 BUY→首 SELL 中位 |
|---|---:|---:|---:|---:|---:|---:|
| `balthazar` | 3.66% | **3.54%** | 3.53% | **$0** | 17 tx / 6 sessions | 8.90h |
| `neo7777` | 2.84% | **2.34%** | **3.73%** | -$3,668 | 6 tx / 4 sessions | 6.02h |
| `opopv.` | 5.87% | 2.35% | 3.32% | **-$7,541** | 39 tx / 23 sessions | 7.62h |
| `LMVM` | 3.14% | 1.62% | 3.13% | -$888 | **4 tx / 2 sessions** | **0.37h** |
| `macau.weather` | **18.57%** | **-6.58%** | 19.97% | -$1,039 | 33 tx / 14 sessions | 29.45h |

`balthazar` 的 target-date 回撤为零不是“预测每天都正确”，而是 full-NO-set conversion 的结构收益在日期汇总后覆盖方向损失；需要用 executable full-set ask、conversion gas/fee、实际 fill probability 验证，不能把 selected fills 当无风险套利。

五人都缺同一 opportunity universe、同分母 PIT market baseline 和 frozen replication forward：

```text
opopv.:        significance=PASS, baseline=NA, forward=NA, conclusion=inconclusive
balthazar:     significance=PASS, baseline=NA, forward=NA, conclusion=inconclusive
LMVM:          significance=PASS, baseline=NA, forward=NA, conclusion=inconclusive
neo7777:       significance=PASS, baseline=NA, forward=NA, conclusion=inconclusive
macau.weather: significance=FAIL, baseline=NA, forward=NA, conclusion=inconclusive
```

## opopv.：全球 mixed inventory 与 maker repricing

长期形态：

```text
D-2/D-1 同时买相邻 YES/NO
→ 多 session 被动积累 maker inventory
→ target day 主动卖出重定价腿
→ 少量 REDEEM/MERGE 收尾
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：Qingdao 5/05 | 21°C | +$4,224.46 / 1,847.64% | 7 maker + 19 taker orders；35 tx | 21 YES 仅花 $77.44，路径重定价后卖回 $4,243.44；同时持有 24 NO、20-or-below YES 和少量 22/23 YES，主收益几乎全来自 winner YES convexity |
| 亏损：Seoul 3/19 | 9°C | -$1,020.49 / -90.52% | 4 legacy tx，role unclassified | 直接买 9 NO $1,127.38；winner 正好为 9，只回收 $106.88。低复杂度不代表低风险 |
| 接近平：Wuhan 7/13 | 37°C | +$0.01 / 0.01% | 19 maker orders；57 maker fills | 同时做 36 YES/NO、37 YES、35 NO、38 YES；source max 与 winner 都为 37，但多腿库存和往返成本把结果磨到零 |
| 典型盈利：Sao Paulo 6/14 | 22°C | +$34.83 / 18.34% | 51 maker orders；101 maker fills | 21/22 NO 与 22/23/24 YES 组成相邻档库存，全部通过 maker partial fill 建立；多腿 repricing 合计小幅盈利 |

**可借鉴**：相邻 bracket 的 inventory state、maker/taker 分开计费、每腿 repricing 和日期级风险预算。

**不值得直接复制**：中位 39 笔 BUY、23 个 session、建仓跨度 33 小时；June 月度 ROI 为 -0.52%，且最大日期回撤 -$7.54k。

## balthazar：full-NO-set、CONVERSION 与结构套利

长期形态：

```text
买完整或接近完整的 NO ladder
→ NO full set 价格低于可兑换 collateral 时累积
→ SPLIT / MERGE / NegRisk CONVERSION 回收现金
→ 少量 winner/adjacent YES 再做主动 SELL
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：NYC 7/05 | 78–79°F | +$215.10 / 21.18% | 63 maker + 145 taker orders；195 tx | 买全梯 NO，并以 $7.02 买 winner YES 后卖回 $304.81；12 次 CONVERSION 提供 $209.83 collateral，另有 43 MERGE、11 SPLIT，结构现金流而非单一预测贡献主收益 |
| 亏损：Seoul 6/14 | 26°C | -$137.43 / -7.98% | 29 maker + 65 taker orders；86 tx | NO 主要压在 28/26/25/24/29+，同时持少量 26/27 YES；conversion cash 为零，错误库存和执行成本没有被结构回收覆盖 |
| 接近平：Guangzhou 6/30 | 34°C | -$0.003 / -0.004% | 3 maker + 4 taker orders；7 tx | 买 34/35/36+ NO，执行 1 CONVERSION、3 MERGE、1 SPLIT、1 REDEEM；完整 event 几乎精确归零，是 conversion 会计的良好 control |
| 典型盈利：Munich 5/31 | 29°C | +$16.12 / 2.37% | 36 maker + 96 taker orders；124 tx | 买 21-or-below 到 31+ 的 NO ladder，6 次 CONVERSION 回收 $242.54；再卖出转换所得的 27–31 YES，形成小而稳定的结构 edge |

这是五人里统计最稳的机制，但复制难点不是天气模型，而是：

- 同一时点完整 NO ladder 的 executable cost；
- conversion 前后 token inventory 守恒；
- CLOB fee、gas、partial fill 和漏腿风险；
- 只有部分 NO 成交时，不能把残缺 basket 当完整套利。

推荐把它单独作为 `full_ladder_no_conversion_arb` research/shadow，不与方向性天气 alpha 混成一条策略。

## LMVM：pre-target single-YES repricing

长期形态：

```text
D-2/D-1 选择单个或相邻两个 YES
→ 10–60 分钟内主动 SELL
→ 几乎不等 target-day source 或最终 REDEEM
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：Shanghai 7/05 | 31°C | +$242.93 / 29.59% | 7 maker + 27 taker orders；47 tx | D-2 买 31 YES $623.14、30 YES $197.80；四小时内分别卖回 $823.87 和 $240.00，结算方向正确但收益已通过 repricing 锁定 |
| 亏损：Wellington 7/06 | 13°C | -$745.68 / -89.50% | 6 maker + 12 taker orders；50 tx | 主仓 14 YES 成本 $827.59，只卖回 $77.12；winner 为 13，少量 13 YES 无法覆盖错误主仓 |
| 接近平：Cape Town 7/17 | 20°C | -$0.31 / -0.11% | 2 maker + 9 taker orders；11 tx | 买卖 19 YES：$278.75 → $278.44。source 后来达到 20，提前退出避免方向损失但没有交易 edge |
| 典型盈利：Taipei 7/22 | 36°C | +$34.81 / 5.82% | 9 taker orders；9 tx | D-2 买 35 YES $598.22，27 分钟内卖回 $633.02；最终 winner 为 36，说明这笔赚的是短时价格变化，不是猜中 bracket |

**最容易复制的部分**：单档仓位、少量 transaction、明确 SELL 退出器。

**不能直接照搬的部分**：公开 fill 已是事后选择样本；需要在所有候选 YES 上比较同一时间的 market move、fee 和 missed fills，才能判断 22 分钟 repricing 是否真有 alpha。

## neo7777：低复杂度 mixed expression

长期形态：

```text
D-1 选择一个主 bracket 或少量 NO basket
→ 4 个左右 session 更新
→ 一部分主动 SELL，一部分持有 REDEEM
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：Houston 7/29 | 94–95°F | +$1,552.54 / 197.73% | 1 maker + 3 taker orders；6 tx | 买 winner YES $785.17，先卖回 $73.99，剩余通过 REDEEM 兑现；本地快源 max 36°C 约 96.8°F，与 WU winner 不同，提醒快源只作概率特征 |
| 亏损：Warsaw 7/04 | 20°C | -$916.82 / -100% | 1 maker order；2 fills | D-1 重仓 21 YES，无主动退出；winner 停在 20，整仓归零 |
| 接近平：Denver 7/01 | 90–91°F | -$0.006 / -0.0005% | 3 maker + 25 taker orders；58 tx | 在 90–91 YES 和 92–93 YES 间轮动，前腿小亏、后腿小赚，完整 event 归零 |
| 典型盈利：Ankara 3/16 | 14°C | +$86.45 / 6.63% | 25 legacy tx，role unclassified | 买多个低档 NO，同时用 $34.96 买 winner 14 YES；NO payout 与 winner YES 共同覆盖成本，属于小型 mixed distribution |

`neo7777` 是复制性最平衡的对象：样本最长、后半段没有衰减、去极值仍为正、执行复杂度不高。主要 negative control 是 Warsaw：单档 YES 没有退出时仍可 -100%。

## macau.weather：香港 YES strip 与 source-basis 风险

长期形态：

```text
D-2 起在香港铺 2–5 档 YES
→ target day 把质量迁到 current/adjacent bracket
→ maker partial fill + taker 调仓
→ winner YES REDEEM，错误尾腿低价 SELL
```

| 案例 | winner | 现金流 | order/fill | 全链路 |
|---|---|---:|---|---|
| 极端盈利：Hong Kong 7/14 | 28°C | +$1,809.97 / 194.88% | 35 maker + 31 taker orders；207 tx | 28 YES 成本 $673.24，另铺 29–32；winner 28 兑付。source archive observed max 为 29°C，与 WU winner 28 不同，直接展示香港 source-to-settlement basis |
| 亏损：Hong Kong 7/28 | 29°C | -$660.35 / -49.22% | 35 maker + 31 taker orders；283 tx | 虽持有 winner 29 YES，但把 $556.56 压在 27、$283.02 压在 28、$183.13 压在 30；winner 收益无法覆盖错误 strip，source max 又为 30 |
| 接近平：Hong Kong 6/23 | 32°C | +$5.35 / 1.07% | 18 maker + 21 taker orders；82 tx | winner 32 YES 买 $187.05、卖回 $411.46；但错误 33 YES 成本 $293.80，几乎吃光 winner 利润 |
| 典型盈利：Hong Kong 7/23 | 33°C | +$317.27 / 43.99% | 11 maker + 32 taker orders；57 tx | 33 YES 为核心，辅以 35 NO 与 32/34 YES；source max 与 winner 都为 33，路径与表达对齐 |

它和 WeatherHK2 的共同点是香港 source-basis、target-day 相邻档轮动和 maker partial fill；差异是 `macau.weather` 更早铺宽 strip、持仓周期更长。高 ROI 主要来自少数日期，不能因为地域相似就直接复制。

## 跨钱包共性与我们的落地方式

### 三条独立研究线

1. **结构套利线**：`balthazar` 的 full-NO-set → CONVERSION。主指标是 full basket executable margin、漏腿概率和 conversion 后 token 守恒，不是天气命中率。
2. **方向/分布线**：`neo7777`、`macau.weather` 的主 bracket + adjacent tails。主指标是同分母 `P(bracket)-market`、Brier/logloss 和 fee-adjusted ROI。
3. **短周期 repricing 线**：`LMVM`、`opopv.` 的主动 SELL。主指标是 entry 后固定 5/15/30/60 分钟 market move、maker/taker fee、missed fill 和 adverse selection。

### 必须保留的 negative controls

- single-bracket miss：LMVM Wellington、neo7777 Warsaw；
- strip winner 命中但 sizing 错：macau.weather Hong Kong 7/28；
- inventory churn：opopv. Wuhan、neo7777 Denver；
- conversion basket 不完整：balthazar Seoul；
- source-to-settlement basis：macau.weather Hong Kong 7/14、neo7777 Houston 7/29。

### 推荐 shadow

```text
A. full-NO-set executable cost vs conversion collateral
B. market-implied distribution vs calibrated weather distribution
C. distribution + target-day path update
D. single-YES fixed-horizon repricing
```

每条保持独立 denominator，不把结构套利、方向预测和做市收益混成一个总 ROI。maker 分支必须记录 post time、queue proxy、missed fills、fill probability 和 adverse selection。

## 双漏斗与证据边界

```text
signal funnel:
11 个初筛候选
→ 5 个机制互补的新钱包
→ 522,347 complete weather activity rows
→ 8,617 city-day portfolios
→ 20 representative cases

evidence funnel:
public TRADE / SPLIT / MERGE / CONVERSION / REDEEM cashflow
→ Gamma complete ladder and winner
→ resolved zero-current-value cross-check
→ 1,445 Polygon receipts
→ 1,455 current CLOB V2 fill events + 29 legacy unclassified transactions
→ source first-seen where local archive exists
```

公开数据能确认 order hash、maker/taker role、partial fill、conversion cash、完整 event cashflow 和 winner；不能确认钱包的 private probability、原始限价单 post time、未成交/撤单、queue rank、当时完整 depth 或未被钱包选择的机会。

最终判断：`balthazar`、`neo7777`、`LMVM`、`opopv.` 的 selected-fill 历史显著为正，`macau.weather` 不稳健；但五人都没有同分母 market baseline 或 frozen replication forward，状态均保持 `inconclusive`。动作是机制拆解与 zero-notional shadow，不改 live。
