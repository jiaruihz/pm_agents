# LMVM：D-2/D-1 单档 YES repricing 全历史剖析 v1

## 结论

LMVM 的主策略不是“猜中最终 winner”，而是：

```text
D-2 / D-1 批量扫描全球城市
→ 主要买一个 0.20–0.40 左右的 YES 主档
→ 大部分资金直接 taker 成交
→ 中位 20.6 分钟开始 SELL、中位 1.91 小时基本卖完
→ 极少依赖最终 REDEEM
```

最值得复制的是**单主档、短持仓、主动退出的生命周期**；目前不能复制的是 bracket 信号本身。公开链上数据没有 private forecast、未成交订单、同时点完整盘口和未选择机会，因此无法证明他究竟按哪个模型选档，也不能把事后表现最好的价格/持仓时长切片直接做成 live gate。

建议先做 `zero-notional LMVM repricing shadow`，以 taker 为主基准，固定记录全部 D-2/D-1 候选在 5/15/30/60 分钟的 markout。当前不直接小额实盘。

## 数据快照

| 项目 | 结果 |
|---|---:|
| 钱包 | `0xc9ded4d5f6eec0a75907ebb924c164a4aea381d9` |
| Public API 快照 | 2026-05-09 08:10:45 UTC → 2026-08-03 11:28:59 UTC |
| weather activity | 3,905 rows |
| city × target_date portfolios | 316 |
| Gamma resolved / cashflow-complete | 311 |
| 有 BUY、进入选档分析 | 310 |
| 独立 resolved target_date | 69 |
| Polygon trade receipts | 3,841 / 3,841 |
| 解码钱包侧订单 | 3,129 |
| metadata missing | 0 |
| unsettled / 非完整 portfolio | 5 / 316 |

主 grain 是 `wallet × city × target_date × complete mutually-exclusive ladder`。PnL 使用 public activity 的 BUY/SELL/MERGE/REDEEM/CONVERSION 完整现金流；activity timestamp 是 fill time，不是原始挂单时间。

机器产物：

- [全量画像](generated/lmvm_repricing_profile_v1/summary.json)
- [逐 event 画像](generated/lmvm_repricing_profile_v1/event_profile.csv)
- [四个典型全链路](generated/lmvm_repricing_lineages_v1/cases.json)

## 长期表现

| 指标 | 结果 |
|---|---:|
| resolved BUY cost | $111,674.07 |
| PnL | **+$3,966.85** |
| turnover ROI | **3.55%** |
| target-date block bootstrap 95% CI | **[1.18%, 5.76%]** |
| 正收益 target_date | 85.5% |
| 正收益 BUY portfolio | 75.8% |
| 去掉最佳五个 target_date | **2.00% ROI** |
| 后半段 | **3.32% ROI** |

早期 18.4% ROI 只对应 $1,671 BUY cost，不能与后期相比。真正放量发生在 7 月：180 个 BUY portfolio、$102,947 cost、+$3,156、ROI 3.07%。8 月快照内只有 9 个 resolved BUY portfolio，ROI 7.34%，样本太小。

最大亏损日期是 7 月 6 日：-$710.95；最大单 event 是 Wellington 7 月 6 日：-$745.68 / -89.50%。因此 3.55% 不是低波动利差，一次主档判断错误可以吞掉很多普通盈利单。

单个 BUY fill 无法独立归因 PnL，因为一个主档常由多笔 BUY 建仓、多笔 partial SELL 退出。合法的最细收益单位是完整主档 round trip 或完整 city-day portfolio：

| 完整 city-day portfolio PnL | P10 | P25 | 中位 | P75 | P90 | 最差 / 最佳 |
|---|---:|---:|---:|---:|---:|---:|
| USD | -$6.27 | +$0.14 | **+$4.21** | +$21.70 | +$59.64 | -$745.68 / +$242.93 |
| ROI | -3.95% | +0.26% | **+6.46%** | +17.67% | +44.41% | -100% / +350% |

## 他怎么选 bracket

链上能证明的规则：

| 观察 | 全历史结果 |
|---|---:|
| BUY cost 在 YES | **94.34%** |
| 只买一个 condition 的 portfolio | **68.71%** |
| 只买一个 YES condition | **68.39%** |
| 主 condition 占 event 成本 | 中位 **100%**；P25 **84.59%** |
| 主档成交价 | 中位 **0.290**；P25 0.204；P75 0.360 |
| 0.20–0.40 主档 | 206 events；占总成本 **67.52%**；ROI 4.09% |
| 0.40–0.60 主档 | 50 events；占总成本 **28.83%**；ROI 2.37% |
| 主 YES 最终成为 winner | **34.88%**（301 个有主 YES 的 BUY portfolio） |

因此最合理的可观察描述是：他通常不是买极便宜尾档，而是在市场认为有一定概率的中价主档上集中仓位，偶尔带一个相邻 YES 或很小的尾档。主 YES 最终只约 35% 成为 winner，但 portfolio 约 76% 盈利，证明选档目标更像**预测短时 repricing**，不是要求最后精确结算正确。

实际买入 condition 数量：

| 买几档 | 1 | 2 | 3 | 4 | 5–7 |
|---|---:|---:|---:|---:|---:|
| events | **213** | 72 | 17 | 4 | 4 |
| 占 310 个 BUY portfolio | **68.7%** | 23.2% | 5.5% | 1.3% | 1.3% |

完整 ladder 位置以最低档为 0、最高档为 1：主档位置中位 0.60，P25–P75 为 0.40–0.70，说明略偏向中高温侧；40%–80% ladder 区间覆盖 228/310 = 73.5%。事后看，主档与 winner 同档 110/310，距离 winner 不超过一档 74.8%，不超过两档 91.3%。这些是事后形态，不是可用于入场的事前 label。

### 初始 market-implied probability

以主档实际成交价作为市场概率 proxy（不是 LMVM 私人 `p_win`）：

| 分位 | P10 | P25 | 中位 | P75 | P90 | P95 |
|---|---:|---:|---:|---:|---:|---:|
| 主档成交价 | 0.150 | 0.204 | **0.290** | 0.360 | 0.461 | 0.521 |

按价格段的 selected-fill 表现：

| 主档价 | events | BUY cost | portfolio 正收益率 | turnover ROI |
|---|---:|---:|---:|---:|
| <0.10 | 20 | $93 | 65.0% | 19.72% |
| 0.10–0.20 | 29 | $1,087 | 86.2% | 8.88% |
| 0.20–0.40 | **206** | **$75,400** | 75.7% | 4.09% |
| 0.40–0.60 | 50 | $32,201 | 78.0% | 2.37% |
| ≥0.60 | 5 | $2,893 | 40.0% | 0.08% |

低价段 ROI 很高但资金量极小且集中早期，不能解释为低价 gate；真正放量核心是 0.20–0.40。

不能从公开数据证明：

- 是 ECMWF/GFS、第三方 forecast、盘口 order flow，还是多者结合；
- 买入时该档是不是完整 ladder 的 favorite；
- 他没买的 bracket 同时表现如何；
- 未成交 maker、撤单和 queue position。

价格带结果只是描述性切片，不能直接解释为“0.20–0.40 就买”。

### 为什么 30 YES 和 31 YES 可以同时涨

`30 YES` 与 `31 YES` 互斥，但它们不是一对二元互补合约。完整 event 还有 28、29、32、33 等档位；如果新信息让市场把概率从两侧尾档移入 30–31 的中心区间，两者可以同时上涨：

```text
旧分布：30=22%，31=25%，其他档=53%
新分布：30=30%，31=32%，其他档=38%
```

Shanghai 实际现金价变化正是这种形态：

| 档位 | 平均 BUY cash/share | 平均 SELL proceeds/share | 变化 |
|---|---:|---:|---:|
| 31 YES | 0.2244 | 0.2967 | **+7.23c / +32.2%** |
| 30 YES | 0.2666 | 0.3235 | **+5.69c / +21.3%** |

两档合计隐含质量从约 0.49 重定价到约 0.62，资金来自其他未持有档位。各 condition 又分别交易，短时间还可能因 spread、深度和更新时间不同而不完全满足概率和为 1。

结合 62.95% D-2、36.02% D-1、03–07 UTC 的集中批量成交，当前最强但仍未证实的推断是：LMVM 在全球 forecast 更新窗口批量重算最终温度分布，买入尚未完全吸收新 forecast 的一个或两个中心 bracket，随后等市场重新定价。另一个可能是纯盘口 momentum/order-flow；没有同时点 PIT forecast 和未选择 ladder，暂时不能把两者区分开。

## 数量与拆单

全历史受 5–6 月小额试单影响很大。更接近当前放量形态的是 7 月：

| 7 月指标 | P25 | 中位 | P75 | P90 |
|---|---:|---:|---:|---:|
| 单 city-day BUY cost | $295 | **$459** | $739 | $1,100 |
| 主 condition cost | $278 | **$441** | $694 | $1,096 |
| 主 condition shares | 840 | **1,303** | 1,902 | 2,637 |
| 最大单笔 transaction cost | $265 | **$395** | $592 | $831 |
| BUY transactions | 4 | **7** | 12 | 21 |
| 主档成交价 | 0.279 | **0.326** | 0.386 | 0.462 |

这不是固定 shares，也不是严格固定 notional；更像按机会强度/盘口容量决定 event size，再用若干 taker 单和小额 maker partial fills完成。普通账户若学习，应该按相同**比例结构**缩小，而不是照抄 1,000+ shares。

## 交易时机与持有周期

入场相对 target_date：

| 入场日 | BUY cost share |
|---|---:|
| D-2 | **62.95%** |
| D-1 | **36.02%** |
| target day | 0.86% |
| D-3 | 0.18% |

UTC 时钟也非常集中：52.27% BUY cost 在 00–06 UTC，36.09% 在 06–12 UTC；其中 03–07 UTC 合计约 60.9%。这更像固定 forecast/market 批量扫描窗口，而不是等目标城市临场升温。

| UTC 小时 | 02 | 03 | 04 | 05 | 06 | 07 | 09 | 10 | 17 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BUY cost share | 7.0% | 13.2% | 12.5% | **17.6%** | 8.6% | 9.0% | 7.2% | 6.5% | 6.9% |

跨 42 个城市时，UTC 分布比当地时间更有解释力。当地时间呈两头分布：00–06 占 30.65%、18–24 占 40.75%，主要是同一批 UTC 扫描映射到不同时区的结果。

| 生命周期 | 结果 |
|---|---:|
| 有主动 SELL 的 BUY portfolio | **98.71%** |
| 首 BUY → 首 SELL | 中位 **20.6 分钟**；P25 2.6 分钟；P90 6.72 小时 |
| 首 BUY → 最后 SELL | 中位 **1.91 小时**；P75 7.57 小时 |
| BUY 建仓跨度 | 中位 **12.9 分钟** |

按首 SELL 时间做的事后切片：小于 5 分钟的 97 个 event 占 52.4% cost、ROI 5.06%；5–30 分钟占 25.5% cost、ROI 2.88%；30 分钟–2 小时 ROI -0.76%。这些组的风险和选档不同，不能据此直接规定“5 分钟卖”；正确验证方式是对同一候选做固定 5/15/30/60 分钟 paired markout。

## 退出条件反推

公开数据不暴露 private fair value 或订单参数，因此不能看到一条明文 `take_profit=...`。全历史路径能排除两种过度简化：

- **不是固定持仓时间**：主档首 SELL 延迟 P25 2.8 分钟、中位 25.0 分钟、P75 119 分钟、P90 460 分钟。
- **不是固定 5% 止盈**：283 个接近完整退出的主档，净 cash/share return P25 约 0%、中位 +6.79%、P75 +20.74%、P90 +49.38%；其中 71 个以负 return 退出。

| 退出行为 | 结果 |
|---|---:|
| 有主档 SELL | 305 events |
| 主档卖出 ≥99% | **283 / 305 = 92.79%** |
| 首 SELL 发生在最后一笔 BUY 之前 | **43.87%** |
| 主档退出 return 中位 | **+6.81%** |
| 主档退出 return P10 / P90 | -4.36% / +50.84% |
| 283 个完整主档 round trip 中正收益 | **212 / 283 = 74.91%** |

43.87% 边买边卖说明它更像持续更新 `forecast fair value − market price` 和库存：价格先达到某档 fair value 就卖一部分，其他腿继续建；如果 forecast/盘口反向，也会负收益退出。最可能的退出条件组合是：

```text
edge 收敛到 0 / 达到更新后的 fair value
或 forecast/market move 反向使 thesis 失效
或批量交易窗口结束、主动清库存
```

这是从路径反推的候选机制，不是已确认参数。Wellington 是重要反例：14 YES 平均买入 cash/share 0.4062，卖出的少量份额均价反而达到 0.4364（+7.4%），但只卖掉约 8.7%，其余仓位随价格崩掉，最终 event -89.5%。说明“挂到利润价”不等于能完整退出；复制时必须把未成交余仓和 stop/expiry 纳入分母。

## 市价还是 maker

3,841/3,841 个 trade receipt 全部找到。钱包侧 onchain 订单显示：

| 方向 | passive maker | taker | 按解码 cash 占比 |
|---|---:|---:|---:|
| BUY | 544 orders / 1,007 fill events | 1,399 orders / 1,416 fill events | **7.00% maker / 93.00% taker** |
| SELL | 181 orders / 444 fill events | 1,005 orders / 1,027 fill events | **8.10% maker / 91.90% taker** |

maker fill event 数看起来不少，是因为 passive order 被多次小额 partial fill；按资金量它只是辅助。LMVM 的主 edge 必须在 taker fee、spread 和跟随延迟后仍成立，不能假设靠 maker 省费救回来。

## 四个典型全链路

### 1. 极端盈利：Shanghai 7/05

```text
D-2 14:16 local
买 31 YES $623.14 + 30 YES $197.80
→ 21 分钟后开始卖
→ 31 YES 卖回 $823.87，30 YES 卖回 $240.00
→ +$242.93 / +29.59%
```

winner 最终确实为 31°C，但仓位在结算前已经基本卖光。BUY 解码 cash 约 97.6% 来自 taker；这是一笔中价主档重定价，不是等待 payout。

### 2. 极端亏损：Wellington 7/06

```text
target day 05:40 local
主买 14 YES $827.59
另买极小 13/15/16 YES tails
→ 1 小时 20 分后开始卖，14 YES 只卖回 $77.12
→ winner 为 13°C
→ -$745.68 / -89.50%
```

这里 maker BUY 占比显著高于平时，但没有保护作用：错误主档的大部分仓位未能在价格崩掉前退出。这是复制策略必须保留的 negative control。

### 3. 接近平：Cape Town 7/17

```text
D-2 08:41 local
买 19 YES $278.75
→ 27 分钟后开始卖，卖回 $278.44
→ -$0.31 / -0.11%
→ 最终 winner 为 20°C
```

它展示了策略核心：最终方向错，但及时 SELL 后只付出很小摩擦成本。

### 4. 典型盈利：Lucknow 7/05

```text
D-1 21:11 local
买 36 YES $1,621.90
→ 103 秒后开始卖，卖回 $1,712.84
→ +$90.93 / +5.61%
→ 最终 winner 为 38°C
```

BUY 几乎全是 taker；最终 bracket 错了两档仍赚钱，是最典型的短周期 repricing 证据。

## 我们怎么学习

第一版只复制生命周期，不冒充已经知道他的信号：

```text
signal funnel:
全部 D-2/D-1 complete ladder
→ 保存每档 PIT forecast、market probability、ask/depth
→ 连续计算 P(outcome)-market residual
→ 仅把模型候选送入 shadow，不按历史价格带硬筛

evidence funnel:
PIT forecast coverage
→ 同时点 complete-ladder book
→ taker executable cost + Weather fee
→ 5/15/30/60m paired markout
→ maker/taker/skip 三路 shadow
→ settlement 只作最终标签，不作为默认退出
```

最关键的复制验收不是“跟单后也赚钱”，而是：

1. 自己的 bracket probability 在同 rows 上打败 market；
2. taker 进入后固定时限 markout 仍为正；
3. 所有候选和未成交机会都在分母里；
4. wallet public fill 到我们可执行 ask 的延迟折损后仍有 edge。

## 证据等级

覆盖环：描述性绩效、target-date bootstrap、onchain 执行微结构、资金集中度和典型 settlement lineage。缺失环：同分母未选择机会、PIT probability score、market baseline、跟随延迟、未成交订单、capacity forward。

```text
significance=PASS
baseline=NA
forward=NA
conclusion=inconclusive
action=zero-notional LMVM repricing shadow；不改 live
```

在 2026-05-09 至 2026-08-03 的 public selected-fill 分母，LMVM cashflow-complete turnover ROI 为 3.55%（target-date 95% CI [1.18%, 5.76%]）；但没有同分母 market baseline 和 frozen replication forward，因此只确认其历史交易形态，不确认可复制 alpha。
