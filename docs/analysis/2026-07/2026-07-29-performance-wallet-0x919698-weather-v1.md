# 绩效分析：钱包 `0x919698…d934` 天气策略

> 窗口：2026-01-22 — 2026-07-28  
> 钱包：`0x919698b19427cbe6945b0dc823f2d9e126a4d934`（Polymarket 用户 `wuxiuming`）  
> evidence layer：Polymarket Public Data API `activity` / `positions` / `trades`

## 结论与动作

这个地址的天气交易历史上确实赚钱，而且不是靠一两笔大运气：逐笔 USDC cashflow 加未赎回结算价值后，fee-inclusive PnL 为 **+$18,055.11**，累计买入现金流 $243,719.15，turnover ROI **+7.41%**。按 `target_date` block bootstrap 的 ROI 95% CI 为 **[+6.19%, +8.87%]**；后半段 71 个 target dates 仍为 **+$13,617.93 / +6.62%**。去掉盈利最高的 10 天后仍为 **+$13,257.52 / +6.13%**。

但这不能直接推出“照着地址跟单也能赚”。该钱包约 45.5% 成交推断为 maker，且大量做双边、分档、卖出和到期赎回；跟单者看见 public activity 时，价格与 queue 已经不同。公开数据也没有它的全机会分母、天气概率、未下单机会和完整 entity 级对冲。因此结论分两层：

- `historical_profitability=PASS`：该地址在本窗口已验证为 fee-inclusive 正收益。
- `transferable_alpha=inconclusive`：可以列入跟踪/拆解候选，不能把历史 ROI 直接当可复制收益率。

```text
significance=PASS; baseline=NA; forward=PASS; conclusion=inconclusive_for_copying
```

## 数据快照

| 项目 | 值 |
|---|---|
| API 查询时间 | 2026-07-29 01:03 北京时间 / 2026-07-28 17:03 UTC |
| raw 覆盖 | 2026-01-22 07:00 UTC — 2026-07-28 16:41 UTC |
| weather activity | 12,856 rows |
| event / target date | 830 / 141 |
| 当前 position rows | 959 |
| 未赎回结算价值 | $351.95 |
| unsettled | 0（无非 binary 且不可赎回仓位） |
| missing settlement | 0 |
| CLOB coverage gate | NA：外部钱包，无 authenticated order/fill 权限 |
| fee evidence | `activity.usdcSize` 真实现金流；BUY 示例已反映 `size×price` 之外的 taker fee |

Public activity 的 offset 上限为 5,000；本次按时间窗口递归分页并按交易身份键去重，未用单个截断窗口。官方 Data API 字段与分页契约见 [user activity](https://docs.polymarket.com/api-reference/core/get-user-activity)、[trades](https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets) 和 [positions](https://docs.polymarket.com/api-reference/core/get-current-positions-for-a-user)。Weather taker fee 公式与 maker=0 口径见 [Polymarket fees](https://docs.polymarket.com/trading/fees)。

## Target metric 与固定分母

- unit/grain：主绩效为 public activity cashflow；稳健性按 `eventSlug` 汇总后，以 `target_date` 为 block。
- universe：标题含 `highest temperature` 的全部公开 activity，不挑盈利城市或价格带。
- 主指标：`SELL + REDEEM + MERGE - BUY - SPLIT + currentValue`；ROI 分母为累计 BUY/SPLIT 现金流。
- settlement：未赎回但已经 binary/redeemable 的 token 按 `currentValue` 计入；非 binary unsettled 单列。
- baseline：账户是否赚钱用 0 PnL；是否存在可复制 market residual 缺少 same-denominator opportunity baseline。
- forward：按 target date 排序前 70 天 / 后 71 天冻结切半，仅作稳定性复核，不调参。

`$243,719.15` 是累计买入 turnover，不是同时占用资金或钱包本金，因此 7.41% 不能解释成账户资金收益率。

## Signal funnel

外部钱包没有公开未下单机会与候选信号，无法重建完整 signal funnel。

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| public account activity | activity | 14,272 | — | 该地址全类别公开活动 |
| weather activity | activity | 12,856 | 141 | 最高温合约 |
| distinct weather event | city-day/event | 830 | 141 | 多 bracket、双边交易合并 |
| strategy selected | opportunity | NA | NA | 无公开机会分母 |

## Evidence funnel

| 层 | grain | rows | dates | coverage gap |
|---|---|---:|---:|---|
| public trades | trade | 12,153 | 141 | 无 order/queue/取消记录 |
| cash activity | activity | 12,856 | 141 | 包含 trade/redeem/split/merge |
| settled event | event | 830 | 141 | 当前无 unsettled |
| public positions | outcome token | 959 | — | $351.95 尚未赎回 |
| authenticated fills | fill | NA | NA | 外部地址不可得 |

## Probability / ranking quality

公开数据没有该钱包的 PIT 天气预测、`p_win`、候选 universe 或当时完整盘口，不能评估 logloss、Brier、calibration 或相对 market probability 的 residual。正 PnL 证明账户历史结果，不等于已识别其预测模型。

## Fee-inclusive trade performance

| slice | cash cost | PnL | ROI | 说明 |
|---|---:|---:|---:|---|
| 全部 weather | $243,719.15 | **+$18,055.11** | **+7.41%** | public cashflow 主口径 |
| YES outcome | $77,005.48 | +$10,709.32 | +13.91% | outcome 可归属活动 |
| NO outcome | $165,873.67 | +$7,741.99 | +4.67% | outcome 可归属活动 |
| SPLIT/MERGE transformation | $840.00 | -$396.21 | — | 不强行分给 YES/NO |
| 前 70 target dates | $38,027.27 | +$4,437.18 | +11.67% | train/早期描述 |
| 后 71 target dates | $205,691.88 | +$13,617.93 | +6.62% | frozen forward 复核 |

补充结果：

- 141 个 target dates 中 135 天为正，date win rate 95.7%；830 个 city-day events 中 599 个为正，event win rate 72.2%。
- target-date block bootstrap ROI 95% CI `[+6.19%, +8.87%]`。
- 去掉盈利最高 1/5/10 天后，ROI 分别为 `+7.17% / +6.69% / +6.13%`。
- 按 target date 归因的累计 PnL 最大回撤为 $20.30；这不是钱包现金日期的资金曲线回撤。

Public `closed-positions.realizedPnl` 汇总约 +$19.1k，但反复买卖会使 `avgPrice × totalBought` 难以对应单次持有成本，因此只作交叉检查，不作主 PnL。

## 策略形态

这不是简单“预测某个温度然后持有到期”：

- 12,153 笔 public trades 中，BUY 8,078、SELL 4,074；另有 686 次 REDEEM。
- 对比 `takerOnly=true/false` 的同笔交易身份，约 6,621 笔为 taker、5,532 笔推断为 maker，maker 占约 45.5%。
- BUY 中 YES 5,687 笔、NO 2,391 笔；它并非单一 BUY_NO 或 BUY_YES。
- BUY 价格分布很宽：`<=5c` 2,365 笔、`20–80c` 2,613 笔、`>=95c` 1,235 笔。
- 平均每个 city-day event 约 3.3 个 outcome-position rows，表现更像“天气判断 + 全 ladder 库存管理 + maker/taker 执行”，而不是一条可从 activity 直接复制的规则。

主要盈利城市为 Qingdao +$3,862、Shanghai +$3,110、Wuhan +$2,705、Chongqing +$2,025、Shenzhen +$1,807；亏损城市主要是 Tel Aviv -$61.56、Istanbul -$9.42、Ankara -$7.92，后三者样本和投入很小。城市切片为描述性结果，未做多重检验，不建议据此追着城市下单。

## Forward 与稳健性

| 检查 | 结果 |
|---|---|
| frozen forward same sign | PASS：后 71 天 +$13,617.93 / +6.62% |
| target-date block bootstrap | PASS：95% CI [+6.19%, +8.87%] |
| top-date removal | PASS：去掉 top 10 天仍 +$13,257.52 / +6.13% |
| multiple testing | 本轮只检验钱包整体；城市切片 K>1，未校正，仅描述 |
| position/activity cross-check | PASS 同号；position PnL +$19.1k，cashflow 主口径 +$18.1k |

## 三门与残余风险

| 门 | PASS/FAIL/NA | 证据 |
|---|---|---|
| significance | PASS | target-date block CI 全部高于 0 |
| same-denominator baseline | NA | 无未交易机会、PIT 预测和完整历史盘口 |
| forward | PASS | 冻结后半段同号且规模更大 |

残余风险：

1. public Data API 不是 authenticated order ledger；maker 身份通过 `takerOnly` 集合差推断，没有 queue、取消和挂单时长。
2. 钱包可能只是更大 entity 的一个地址；无法排除跨地址对冲。
3. follower 会晚于原地址成交，尤其 maker 腿无法复制，公开 activity 可能只展示成交后状态。
4. 当前样本证明的是这个地址历史上赚到钱，不证明它未来仍有同样 edge，也不证明跟单收益。

最终结论：

```text
在 2026-01-22..2026-07-28 的该地址全部公开最高温交易上，
fee-inclusive cash PnL 为 +$18,055.11、turnover ROI +7.41%
（target-date block bootstrap 95% CI [+6.19%, +8.87%]），
forward PASS；历史盈利 confirmed，但相对 same-denominator market baseline 为 NA，
因此复制策略结论为 inconclusive，动作是继续地址监控与成交后机制拆解，不直接跟单。
```
