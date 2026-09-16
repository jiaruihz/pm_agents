# opopv.：D-2/D-1 mixed-inventory repricing 全历史剖析 v1

## 结论

`opopv.` 值得参考，但**不适合普通账户直接复制**。它不是 LMVM 那种“选一个主档、短持仓卖掉”，而是：

```text
D-2 开始在全球 city-day 建 mixed YES/NO 多腿库存
→ 约 33.6 小时持续 maker-style 积累和双向换手
→ 中位 7.82 小时才开始 SELL，约 41.2 小时后仍在卖
→ target day / near-binary 阶段继续变现，少量 REDEEM/MERGE 收尾
```

全历史 public-cashflow ROI 为 **4.15%**（95% target-date CI **[1.61%, 6.97%]**），历史显著为正；但可事前描述的低复杂度子模式没有复现这个优势：

- 单 condition + active SELL：173 个 event，ROI 3.88%，CI **[-10.83%, 20.95%]**；
- 最接近 LMVM 的 `D-2/D-1 + 单 YES + active SELL`：57 个 event，ROI **-5.59%**，CI **[-34.66%, 38.99%]**。

因此，从 opopv 能学习的是**完整 ladder inventory、maker/taker 分账、边买边卖与多腿风险预算**，不能学习成一条“买一个低估 bracket 再等几小时卖”的现成规则。普通散户的第一复制对象仍应是 LMVM 生命周期；opopv 只作为复杂做市/库存对照。

动作：`research / zero-notional execution reference`，不改 live。

## 目标与分母

目标是检验：在完整 public selected-fill 历史上，能否从 opopv 的 mixed inventory 中抽出一个普通账户可执行、低腿数、主动退出的 repricing 子模式，并与 LMVM 的 D-2/D-1 单档 YES repricing 做同口径描述性对照。

- 主 grain：`wallet × city × target_date × complete mutually-exclusive ladder`；
- 收益：`SELL + REDEEM + MERGE + CONVERSION - BUY - SPLIT`；
- 时间：public activity 的 fill timestamp，不是 private signal / order post time；
- settlement：只把 Gamma resolved 且 snapshot current value 基本归零的 portfolio 纳入 cashflow-complete PnL；
- 数据层：外部钱包 public activity + Gamma + Polygon receipt；未读取本项目 canonical facts，因此本报告不是 `fact_trades` live_real 发布，也不需要运行 live fill coverage gate。

## 数据快照与完整性

| 项目 | 结果 |
|---|---:|
| 钱包 | `0x7c63520c2ca9b336af0c205b9ccf68217bb393d4` |
| public activity 查询 | 2026-03-12 03:42:53 → 2026-08-04 13:34:18 UTC |
| UTC 日窗 | 146 / 146 完整 |
| API 请求 / 饱和窗递归拆分 | 829 / 8 nodes |
| weather activity | **319,518 rows** |
| weather transactions | 317,366 |
| city × target_date portfolios | 4,018 |
| Gamma resolved | 3,910 |
| cashflow-complete resolved | **3,894** |
| resolved 且有 BUY、进入形态分析 | 3,883 |
| 独立 resolved target_date | 135 |
| full-ladder metadata incomplete | 2 portfolios |

每日查询与递归拆分规避了 Data API 单次 offset 5,500-row 截断。closed-position endpoint 达到 10,000 行上限，但它只用于发现，不进入 PnL；PnL 使用完整 activity。Gamma 有 153 个 legacy 单-condition slug 无直接 metadata，合并回同 city-day 的完整 event 后，真正不完整只剩 2 个 portfolio。

机器产物：

- [全量画像](generated/opopv_repricing_profile_v1/summary.json)
- [逐 event 画像](generated/opopv_repricing_profile_v1/event_profile.csv)
- [本次代表案例](generated/opopv_repricing_lineages_v1/cases.json)
- [四案例 196/196 receipt 完整解码](../2026-07/generated/weather_wallet_new_five_lineages_20260731_v1/opopv.json)

## 长期表现与稳定性

| 指标 | 结果 |
|---|---:|
| resolved BUY cost | $880,434.58 |
| public-cashflow PnL | **+$36,509.36** |
| turnover ROI | **4.15%** |
| target-date block bootstrap 95% CI | **[1.61%, 6.97%]** |
| 正收益 target_date | 71.1% |
| 正收益 BUY portfolio | 53.9% |
| 去掉最佳五个 target_date | **2.22% ROI** |
| 后半段 | **3.02% ROI** |
| 最近 30 个 target_date | 6.43% ROI |
| 最大亏损 target_date | 2026-08-02，**-$2,286.58** |

月度并不稳定：3 月 4.49%、4 月 7.79%、5 月 17.05%、6 月 **-0.52%**、7 月 6.19%，8 月截至快照 **-1.47%**。历史显著为正不代表它是一条低回撤利差。

portfolio 分布也很宽：PnL 中位仅 +$0.90，P25 -$29.25，P75 +$25.84；ROI 中位 +0.95%，P10 -53.56%，P90 +63.55%。总利润来自大量库存事件与少量大幅 repricing 的组合，不是每单稳定赚几个点。

### fee / public-cashflow 边界

全历史 public activity 能重建实际 token/cash transfer，但不能把每笔 CLOB fee 从 `usdcSize` 中独立拆出；fee 可能已经通过到账 shares/cash 体现，公开字段不足以发布一条精确的全历史 `fee-adjusted PnL`。

可确定的边界：若假设另有一笔**尚未反映**的增量 fee，按 `BUY cost + SELL proceeds` 双边 notional 计，费率达到约 **2.24%** 才会吃掉全部 $36.5k public cashflow。这只是 break-even sensitivity，不是实际 fee 估计。

四个代表案例的 196/196 receipts 完整解码显示：

| side | passive maker orders / fills | taker orders / fills | decoded cash | explicit fee |
|---|---:|---:|---:|---:|
| BUY maker | 40 / 106 | — | $327.39 | $0 |
| BUY taker | — | 14 / 15 | $182.38 | $6.11 |
| SELL maker | 37 / 67 | — | $4,508.66 | $0 |
| SELL taker | — | 5 / 5 | $96.69 | $1.89 |

该样本是极赚/典赚/平/亏的有意抽样，且 SELL cash 被 Qingdao 极赚案例支配，不能外推成全历史 maker 占比。但它确认了 opopv 确实大量使用 passive maker partial fills，taker 更多用于补仓/退出，而不是像 LMVM 那样约 93% BUY cash、92% SELL cash 都是 taker。

## 选档与 inventory 形态

| 观察 | opopv | LMVM |
|---|---:|---:|
| BUY cost 在 YES | 52.0% | 94.3% |
| 单 condition portfolio | **5.95%** | **68.7%** |
| condition 数中位 / 众数 | 4 / 4 | 1 / 1 |
| 主腿成本占比中位 | 51.5% | 100% |
| 主腿 selected-side 成交 cash/share 中位 | 0.440 | 0.290 |
| 主 YES 最终 winner | 39.4% | 34.9% |

opopv 的 condition 数分布是：1 档 231、2 档 463、3 档 705、4 档 723、5 档 619、6 档 375，随后仍有 7–14 档。它通常同时持有相邻 YES、NO 和高/低尾腿；主腿只占一半左右资金，不能把它还原成“其实也是单档，只是拆单多”。

主 YES 与 winner 同档 39.4%，主腿到 winner 的 index distance 中位为 0、P25–P75 为 -1 到 +1。这个事后 proximity 比随机好看，但不是 PIT signal；公开数据没有未选择 ladder 和 private forecast，不能证明它按什么模型选中这些腿。

## 入场、持有与退出

按**每个 BUY fill**的 target-date offset：D-2 18.96%、D-1 **46.51%**、target day **34.49%**。按 event 第一次启动看，很多仓位早在 D-2 开始，之后跨两天持续补仓；这解释了“第一个 entry 在 D-2”与“大量实际成本在 D-1/D0”可以同时成立。

| 生命周期 | opopv | LMVM |
|---|---:|---:|
| BUY 建仓跨度中位 | **33.61h** | 12.9m |
| 首 BUY → 首 SELL 中位 | **7.82h** | 20.6m |
| 首 BUY → 最后 SELL 中位 | **41.24h** | 1.91h |
| 首 SELL 早于最后 BUY | **88.1%** | 43.9% |
| 有主动 SELL | 96.0% | 98.7% |
| BUY transactions 中位 | **40** | 4 |

opopv 的 BUY cost 分散在全天，没有 LMVM 的 03–07 UTC forecast-batch 集中形态。88.1% event 在最后一笔 BUY 前已经开始 SELL，说明它在持续做 inventory recycling，而非“建好仓再等一个退出信号”。

退出也不是纯短时 repricing：31.45% SELL proceeds 发生在价格 ≥0.95，28.87% ≥0.99，代表相当一部分仓位一直拿到 near-binary 阶段再变现。主腿有 SELL 的 event 中，cash/share return 中位 +8.17%，P25 -2.63%、P75 +23.10%；这只是已成交退出腿，未卖掉的坏库存仍由完整 portfolio PnL承担。

最合理的退出机制反推是：

```text
各腿 continuously quote / partial fill
→ 某腿价格达到库存 fair value 就卖，其他腿继续买
→ target-day 信息收敛后卖 near-binary winner / loser NO
→ 剩余 token REDEEM 或 MERGE
```

公开数据不能看到 limit post time、cancel、queue position 和未成交 maker，因此不能把这个推断当成已确认参数。

## 能否抽出普通散户子模式

### 1. 单 condition + active SELL

这是最宽松的低复杂度表达：173 events、77 个日期、BUY cost $20,041、PnL +$778.51、ROI 3.88%，但 target-date CI **[-10.83%, 20.95%]**。中位只有 3 笔 BUY、1.34 小时开始 SELL，看起来可做；问题是收益极不稳定且不显著。

### 2. 与 LMVM 最接近：D-2/D-1 单 YES + active SELL

57 events、36 个日期、BUY cost $3,404.86、PnL **-$190.24**、ROI **-5.59%**，CI **[-34.66%, 38.99%]**；正收益 event 仅 43.9%，中位 10 笔 BUY、10.16 小时后开始 SELL。剔除“先卖后买、可能继承旧库存”的一个 event 后，ROI 进一步变成 **-6.64%**。

这直接否定了一个简单捷径：**不能因为 opopv 总体长期赚钱，就把它裁成 LMVM 单档生命周期并假设 alpha 仍在。**opopv 的收益更可能来自多腿 inventory selection、maker fill 质量、相对价值和 near-settlement recycling 的联合，而不是单档 forecast repricing。

## 四个典型全链路

### 极端盈利：Qingdao 2026-05-05

```text
target day 买 21 YES $77.44、20-or-below YES $48.93、24 NO $72.12
+ 少量 22/23 YES 和 21 NO
→ 2.19h 后开始 SELL
→ 21 YES 卖回 $4,243.44，另 MERGE $116.25
→ +$4,224.46 / +1,847.64%，winner=21°C
```

35/35 receipts 完整；7 maker + 19 taker orders。利润几乎全来自低成本 winner YES 的极端 convex repricing，不能作为普通日收益预期。

### 典型盈利：Sao Paulo 2026-06-14

```text
D-1 买 21/22 NO 与 22/23/24 YES
→ 23.1h 持续建仓，9.46h 后开始 SELL
→ BUY $189.95，SELL $224.78
→ +$34.83 / +18.34%，winner=22°C
```

100/100 receipts；51 个 maker orders、101 个 maker fill events，无 taker。它是最典型的多腿 passive partial-fill repricing，不是单档预测。

### 接近平：Wuhan 2026-07-13

```text
D-1 买 36 YES/NO、37 YES、35 NO、38 YES
→ 29.2h 建仓，7.20h 后开始 SELL
→ BUY $97.30，SELL $41.83，REDEEM $55.49
→ +$0.011 / +0.01%，winner=37°C
```

57/57 receipts 全是 maker fill；正确持有 winner 仍被多腿 churn 和错误库存磨到接近零。

### 亏损：Seoul 2026-03-19

```text
target day 直接买 9 NO $1,127.38
→ 1.42h 后卖回 $106.88
→ -$1,020.49 / -90.52%，winner=9°C
```

这是重要 negative control：低复杂度单腿并没有保护，反而是 opopv 最大 event 亏损。

## 我们能学习什么

可以借鉴并进入 zero-notional execution research：

1. 对 complete ladder 保存每腿 inventory、cost basis、fair value、可卖 bid；
2. 同一 candidate 做 maker/taker/skip A/B，记录 queue proxy、fill probability 和 adverse selection；
3. 允许边买边卖，但 PnL 必须回到完整 city-day portfolio，不只看已卖盈利腿；
4. target-date 统一风险预算，防止同日几十城的相关亏损伪装成分散。

不能直接复制：

- 4–6 条腿、40 笔 BUY、跨 33 小时的主形态；
- 从已成交 maker fills 反推“挂单就会成交”；
- 把 ≥0.95 的 near-settlement 出售当成短周期 forecast repricing；
- 把历史总体 4.15% ROI 归因给单档子策略。

与当前 LMVM research plan 的关系：LMVM 继续作为**低复杂度生命周期原型**；opopv 作为 maker inventory / multi-leg execution negative-control 和后续高级版本，不合并为同一策略分母。

## 双漏斗与证据等级

```text
signal funnel:
319,518 public weather activity rows
→ 4,018 city-day complete-ladder portfolios
→ 3,894 cashflow-complete resolved
→ 231 单 condition
→ 173 单 condition active SELL
→ 57 D-2/D-1 单 YES active SELL

evidence funnel:
public TRADE / MERGE / REDEEM cashflow
→ Gamma ladder / winner coverage
→ 135 independent resolved target dates
→ 196/196 selected-case receipts（maker/taker）
→ 缺同时点完整 book、private forecast、unfilled/cancelled orders 与 full-history fee split
```

覆盖环：描述性绩效、target-date bootstrap、完整 ladder、典型 settlement lineage、selected-case execution microstructure、简单子模式反证。缺失环：同分母未选择机会、PIT probability score、market baseline、全历史 maker queue/fill denominator、capacity 和 frozen replication forward。

```text
significance=PASS（opopv 总体 public-cashflow）
baseline=NA
forward=NA
conclusion=inconclusive
action=research / zero-notional execution reference；不改 live
```

在 2026-03-12 至 2026-08-04 的 public selected-fill 分母，opopv cashflow-complete turnover ROI 为 4.15%（95% CI [1.61%, 6.97%]）；但最接近 LMVM 的 D-2/D-1 单 YES 子模式 ROI 为 -5.59%（CI 跨零），且缺同分母 market baseline 与 frozen forward，因此 opopv 只能作为复杂 inventory / maker execution 参考，不能作为普通散户低复杂度复制模板。
