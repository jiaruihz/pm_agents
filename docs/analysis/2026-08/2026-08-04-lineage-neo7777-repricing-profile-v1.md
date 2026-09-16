# neo7777：不是纯 LMVM-like，属于混合天气交易账户 v1

## 结论

neo7777 **不适合作为第二个 LMVM 直接照抄**。它确实包含 D-2/D-1 单档 YES 的主动退出交易，但账户主形态明显更复杂：

```text
51 城批量交易
→ YES/NO 混合，多档和跨阶段补仓很常见
→ 22.0% 资金在 target day 才入场
→ 只有 63.9% portfolio 有主动 SELL
→ 首次 SELL 中位 4.48 小时，最后 SELL 中位 26.03 小时
→ 34.5% resolved portfolio 完全不 SELL、直接等 settlement
```

它适合参考的不是整套下单轨迹，而是两个局部机制：

1. `D-1/D-2 单档 YES → 主动 SELL` 的 repricing 子集；
2. 用完整 ladder 动态调整仓位、胜者留到 REDEEM 的组合管理方式。

但第一个子集在全体事前可定义的 `single YES + D-1/D-2` 分母上 ROI 只有 **-0.04%**；事后再筛“最终有 SELL”才变成 +1.73%，因此不能把“有 SELL”倒过来当入场策略。动作是：**不复制 neo7777 整体策略，不据此改 live；只把它作为 LMVM shadow 的异质对照账户。**

## 数据快照与口径

| 项目 | 结果 |
|---|---:|
| 钱包 | `0xd25156e222c9b907b128e27c36821fdb41db4d37` |
| Public API 查询窗口 | 2026-01-04 10:30:13 UTC → 2026-08-04 13:33:47 UTC |
| weather activity | 34,276 rows |
| weather transaction | 32,936 |
| city × target_date complete-ladder portfolios | 1,471 |
| Gamma resolved / cashflow-complete | 1,405 / 1,405 |
| 独立 resolved target_date | 167 |
| 城市 | 51 |
| metadata incomplete portfolio | 1 |
| closed positions endpoint truncated | 否（3,075 rows） |
| Polygon receipts | 31,309 / 31,709 trade tx（98.74%） |
| decoded wallet-side orders | 7,376 |

采集使用按 UTC 日切窗、超限递归拆分、跨日去重的 public wallet pipeline，不使用会在 5,500/10,000 rows 截断的单次 recent-activity 样本。主 grain 是 `wallet × city × target_date × complete mutually-exclusive ladder`。PnL 是 BUY/SELL/REDEEM/CONVERSION 的实际 public cashflow；公开 activity timestamp 是成交时刻，不是原始挂单时刻。

机器产物：

- [全量 public snapshot](generated/neo7777_repricing_source_v1/manifest.json)
- [完整 ladder summary](generated/neo7777_repricing_source_v1/analysis/full_ladder_history_v1/summary.json)
- [逐 portfolio](generated/neo7777_repricing_source_v1/analysis/full_ladder_history_v1/event_portfolios.csv)
- [repricing profile](generated/neo7777_repricing_profile_v1/summary.json)
- [逐 event profile](generated/neo7777_repricing_profile_v1/event_profile.csv)
- [极赚/典赚/平/亏四案例](generated/neo7777_repricing_lineages_v1/cases.json)

## 长期现金流表现

| 指标 | 结果 |
|---|---:|
| resolved BUY cost | $781,440.50 |
| cashflow PnL | **+$27,794.15** |
| turnover ROI | **3.56%** |
| target-date block bootstrap 95% CI | **[1.60%, 5.38%]** |
| 正收益 target_date | 68.9% |
| 正收益 portfolio | 62.1% |
| 前半段 / 后半段 ROI | 2.77% / 3.88% |
| 去掉最佳五个 target_date | **2.30%** |

历史结果本身稳定为正，但这只证明 neo7777 的 **selected public fills** 赚钱，不证明我们能恢复它的 private signal。最佳五日之外仍有 +$17,059，说明不是单个 jackpot 解释全部利润；与此同时最差 target date（2026-08-01）亏 **-$2,140.51**，账户有明显日级尾部风险。

单 portfolio 的中位 PnL 为 +$5.96、中位 ROI 2.35%；P25/P75 ROI 为 -6.43%/+11.00%，P10 为 -53.03%。这不是低波动做市。

## 与 LMVM 的同分母比较

| 形态 | neo7777 | LMVM | 判断 |
|---|---:|---:|---|
| BUY cost 在 YES | **43.36%** | 94.34% | neo 是 YES/NO 混合 |
| 只买 1 个 condition | **41.49%** | 68.71% | neo 多档/多腿更多 |
| 主档成交价中位 | **0.559** | 0.290 | neo 更偏高价和接近确定性仓位 |
| D-2 BUY cost | **19.94%** | 62.95% | neo 不是以 D-2 为核心 |
| D-1 BUY cost | **55.55%** | 36.02% | neo 主要 D-1 |
| D0 BUY cost | **22.03%** | 0.86% | neo 有大量 target-day 判断 |
| 有主动 SELL 的 portfolio | **63.91%** | 98.71% | neo 经常等结算 |
| 首 BUY→首 SELL 中位 | **4.48h** | 20.6m | 生命周期不同 |
| 首 BUY→末 SELL 中位 | **26.03h** | 1.91h | neo 长时间动态管理 |
| 主 YES 最终命中 winner | **41.33%** | 34.88% | neo 更依赖最终 outcome |

实际买入 condition 数：

| 档数 | 1 | 2 | 3 | 4 | 5 | 6+ |
|---|---:|---:|---:|---:|---:|---:|
| portfolios | 583 | 247 | 186 | 158 | 89 | 142 |
| 占比 | 41.5% | 17.6% | 13.2% | 11.2% | 6.3% | 10.1% |

neo7777 买入跨度中位 **386.8 分钟**，每个 portfolio 中位 6 个 BUY transaction；30.0% 的 portfolio 在第一次 SELL 后仍继续 BUY。这更像动态仓位管理，而不是 LMVM 的“一次选主档、短等市场重定价、快速退出”。

## 入场分布

### 距目标日

| offset | D-2 | D-1 | D0 | 更早 D-3～D-5 | D+1/误差 |
|---|---:|---:|---:|---:|---:|
| BUY cost share | 19.94% | **55.55%** | **22.03%** | 2.46% | 0.02% |

### UTC 和当地时间

| 时段 | 00–06 | 06–12 | 12–18 | 18–24 |
|---|---:|---:|---:|---:|
| UTC BUY cost | 2.20% | **53.58%** | 32.09% | 12.13% |
| 当地 BUY cost | **31.41%** | 19.07% | 23.34% | 26.19%（14–24 合计） |

UTC 峰值为 09 点（14.19%），随后是 08 点（10.87%）、11 点（9.15%）、10 点（9.01%）。它存在批量扫描时钟，但跨 51 城映射到当地后并不是统一的“forecast 发布后 20 分钟”窗口；31.4% 资金甚至发生在当地 00–06。

### 价格和城市

主 condition 成交价中位 0.559，P25/P75 为 0.376/0.960；按成本，价格 ≥0.60 的 selected portfolio 占 $416,311，ROI 3.54%。因此不能把它概括成“薄盘买 0.20–0.40 低估档”。

资金最大的城市：

| 城市 | BUY cost | PnL | ROI | portfolios |
|---|---:|---:|---:|---:|
| Miami | $172,699 | +$7,960 | 4.61% | 156 |
| Los Angeles | $76,748 | +$5,106 | 6.65% | 78 |
| Houston | $46,860 | +$4,543 | 9.69% | 74 |
| Dallas | $39,788 | +$5,854 | 14.71% | 47 |
| Mexico City | $35,455 | +$1,033 | 2.91% | 66 |
| Austin | $33,837 | +$1,361 | 4.02% | 47 |

美国城市高度集中，公开数据无法证明这是模型质量、数据源速度、流动性还是选择性幸存结果。城市 ROI 是事后多重切片，不能直接做 allowlist。

## 退出和执行

有主动 SELL 的 898 个 portfolio：

| 指标 | P10 | P25 | 中位 | P75 | P90 |
|---|---:|---:|---:|---:|---:|
| 首 BUY→首 SELL | 1.5m | 6.8m | **4.48h** | 25.64h | 45.30h |
| 首 BUY→最后 SELL | 0.97h | 9.84h | **26.03h** | 37.58h | 55.59h |

分布是双峰/混合的：部分 portfolio 很快开始做价差，另一批明显持有到目标日附近。主 condition 有 SELL 的 692 个 portfolio 中，首次主档 SELL 中位 41.6 分钟；只有 475 个基本完整退出，完整退出者主档 round-trip 中位回报 +1.43%，其中 61.3% 为正。

链上执行角色（receipt 已覆盖 98.74% trade tx）：

| side | maker cash | taker cash | maker / taker cash share |
|---|---:|---:|---:|
| BUY | $140,688.82 | $453,803.51 | **23.67% / 76.33%** |
| SELL | $127,992.03 | $242,581.28 | **34.54% / 65.46%** |

neo7777 仍以 taker 资金为主，但 maker 使用明显高于 LMVM；尤其 SELL maker order 被大量 partial fill。role share 分母只包含能从 receipt 解码回 wallet-side order 的现金（BUY 约 $594k），不是全部 $781k public BUY cash。receipt 能证明已成交 order 的角色，但看不到 post time、撤单、未成交订单和 queue rank，因此不能把这 23.7% maker cash 解释成真实挂单成功率或完整 maker policy。

## LMVM-like 子集：有，但不是账户主 alpha

为了避免事后故事，先用事前可定义形态看子集：

| 子集 | portfolios | BUY cost | PnL | ROI | 正收益率 |
|---|---:|---:|---:|---:|---:|
| single YES（全部） | 477 | $175,626 | +$3,316 | 1.89% | 50.3% |
| single YES + D-1/D-2 | 377 | $160,427 | **-$59.88** | **-0.04%** | 50.4% |
| 上述且事后有主动 SELL | 316 | $154,867 | +$2,678 | 1.73% | 52.5% |
| 上述且 2h 内开始 SELL | 275 | $147,061 | +$1,560 | 1.06% | 49.5% |

`has SELL` 与 `2h 内 SELL` 是交易结束后才知道的生命周期标签，不能作为买入 eligibility。真正不泄漏的 `single YES + D-1/D-2` 总体接近零，说明 **neo7777 的全账户 +3.56% 主要不是靠 LMVM 那种简单单档短持有模板解释**；多 condition portfolio 反而贡献 +$24,097、ROI 4.05%。

## 四个典型全链路

### 极赚：Houston 2026-07-29

- D0 09:14 UTC 一笔 taker 买入 `94–95°F YES` 2,423.55 shares，public cost $782.90；随后补 5 shares。
- 约 9.87 小时后开始 SELL，只卖出 164.83 shares，包含一笔 taker 和一个被 3 次 partial fill 的 passive maker order。
- 最终 winner 正是 `94–95°F`，剩余 2,263.72 shares REDEEM。
- 总 BUY $785.17，SELL $73.99，REDEEM $2,263.72，PnL **+$1,552.54 / +197.73%**。

这不是 LMVM 短线：主要利润来自正确命中 winner 并持有到 settlement，主动 SELL 只处理 6.8% 左右仓位。

### 典型赚：Ankara 2026-03-16

- D-2 分多个阶段买入 `9/10/11/12°C NO`，并夹带 `14/15°C YES`；总 BUY $1,303.87。
- 建仓从 3 月 14 日持续数小时，最后直到 3 月 16 日才卖 `14°C YES`；其余主要靠 REDEEM。
- winner 为 `14°C`；SELL $47.25、REDEEM $1,343.07，PnL **+$86.45 / +6.63%**。

这是多腿 exact-ladder 组合，不是普通散户容易复刻的单档交易。

### 平：Denver 2026-07-01

- D-1 买 `90–91°F YES` $1,015.15，46 秒后就开始卖；随后跨 25.6 小时分十多个阶段逐步退出。
- 当日晚间又做一轮 `92–93°F YES`，买 $175.84、卖 $198.83。
- 总 BUY $1,190.99、SELL $1,190.98，PnL **-$0.006，基本为零**。

这个案例说明“很快开始卖”不等于“20 分钟完成一笔”；它可以是长达一天的 inventory workout。

### 亏：Warsaw 2026-07-04

- D-1 一次集中买 `21°C YES` 1,825.37 shares，cost $916.82。
- 没有主动 SELL；最终 winner 是 `20°C`。
- 全损 **-$916.82 / -100%**。

这是最干净的 exact-bracket 方向风险：只差一档仍然归零。

## 可复制性判断

### 普通散户可以参考

- 完整 ladder 而不是只看单个二元页面；
- 把“主动 repricing SELL”和“持有到 winner”拆成两套清晰的 position state；
- 在 D-1/D0 反复更新 bracket 概率，而不是把首次 forecast 当静态预测；
- 记录第一笔 SELL 后继续 BUY 的 inventory 状态，避免把动态管理误算成独立 round trip。

### 不应照抄

- 同时复制 51 城、多个 YES/NO condition 与跨 1–2 天的动态仓位；
- 按它的成交价格/城市/小时做硬 gate；
- 看到链上 BUY 后跟单。它不少大仓依赖 D0 outcome 判断或后续多阶段管理，跟单者看不到 private fair value 与剩余 inventory；
- 只复制入场不复制 settlement/exit。22% 资金在 D0，34.5% portfolio 不主动 SELL，尾部风险与 LMVM 完全不同。

## 双漏斗、8 环与结论等级

```text
signal funnel（外部 private，不可见）:
全部 city-day/ladders → private model/order-flow candidates（未知）
→ selected fills 1,471 portfolios → resolved BUY portfolios 1,405

evidence funnel:
public activity 34,276 rows
→ 1,484 event slugs / 1,471 complete ladders
→ 1,405 resolved cashflow-complete portfolios
→ 31,709 trade tx / 31,309 receipts（98.74%）
→ 7,376 decoded wallet-side orders
→ 4 个代表案例 receipt 91/91
```

覆盖：①描述性 selected-fill 绩效；②target-date bootstrap；⑤实际 public fill 与部分/全量链上 order role；⑦target-date block。缺失：③未选择机会与 private signal；④同时点 `p_win`/概率校准；⑤未成交、queue、live book；⑥深度容量；⑧同分母 market/random baseline；以及 frozen replication forward。

```text
significance=PASS（仅历史 selected cashflow ROI）
baseline=FAIL
forward=FAIL
conclusion=inconclusive（作为可复制 alpha）
action=作为 LMVM shadow 的异质对照；不复制整套、不改 live
```

在 2026-01-04 至 2026-08-04 的 public selected-fill 分母，neo7777 cashflow-complete turnover ROI 为 3.56%（target-date 95% CI [1.60%, 5.38%]）；但它的事前 `single YES + D-1/D-2` 子集 ROI 为 -0.04%，且缺同分母 market baseline 与 frozen replication forward，因此不能视为可直接复制的 LMVM-like 钱包。
