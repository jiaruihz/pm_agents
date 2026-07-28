# Weather 研究：钱包 `0x919698…d934` 策略机制

## 结论与动作

这个地址的主要策略不是提前一天押天气预报，也不是单纯买便宜 YES。它更接近：

```text
目标日盘中观测温度路径
→ 更新最终 Tmax 在整条 exact-bracket ladder 上的分布
→ 大仓买已经高度确定的错误 bracket NO
→ 中仓买仍被低估的当前/邻近 bracket YES 或 NO
→ 小仓买低价 tail YES
→ 通过 maker 挂单和主动 SELL 动态回收，剩余赢家到期 REDEEM
```

可以简称为 **intraday Tmax ladder trading（盘中最高温全 ladder 库存交易）**。它同时包含天气方向 edge、negative-risk ladder 相对价值和做市执行，不是一条“看到某温度就买某一边”的简单规则。

动作：把它作为 `research / monitor candidate`，继续采集其交易前盘口和当时天气 state；当前不能只根据成交后 activity 复制。

## Target

```text
识别该地址在何时、以什么价格、对哪些 ladder outcome 建仓/退出，
并区分主要资金表达、主要利润表达和 maker 执行贡献。
```

- physical target：最终最高温 exact bracket；`X YES` 只有最终 Tmax 正好为 X 才赢。
- grain / universe：该钱包全部公开 `highest temperature` activity；按 outcome-position 和 city-day event 聚合。
- decision timestamp：public trade timestamp；按城市 timezone 转换为目标当地时间。
- label / settlement：public REDEEM、SELL 和当前 binary position value。
- executable expression / fee：public `usdcSize` 实际现金流，已反映真实成交金额与 taker fee。
- primary metric：机制覆盖率、资金占比和 fee-inclusive cash PnL；没有未交易机会分母。

## Data integrity / PIT

| 项目 | 值 |
|---|---|
| raw source and coverage | Polymarket Data API；2026-01-22 — 2026-07-28 |
| weather activity / trades | 12,856 / 12,153 |
| city-day events / target dates | 830 / 141 |
| current position rows | 959，均已 binary/redeemable |
| weather/forecast PIT state | 不可得 |
| historical book / queue | 不可得 |
| settlement | public SELL/REDEEM + $351.95 未赎回 binary value |
| fee | `activity.usdcSize` 现金流主口径 |

Activity 使用 `start/end` 时间窗递归分页绕开单窗口 offset 5,000 上限，并按 transaction/activity 身份键去重。复跑产物为
[`summary.json`](generated/wallet_0x919698_weather_strategy_v1/summary.json)。

## Signal funnel

| 层 | grain | rows | dates |
|---|---|---:|---:|
| public weather activity | activity | 12,856 | 141 |
| BUY trades | trade | 8,078 | 141 |
| distinct city-day event | event | 830 | 141 |
| unselected opportunity | opportunity | NA | NA |

该地址的天气 source、概率和未下单机会不公开，因此只能重建成交后的 selected funnel，不能声称已恢复完整策略信号。

## Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| public trade | trade | 12,153 | 141 | 无订单/取消/queue |
| activity cashflow | activity | 12,856 | 141 | 可见 BUY/SELL/REDEEM |
| settled event | city-day | 830 | 141 | 当前无 unsettled |
| maker/taker | trade | 12,153 | 141 | maker 为集合差推断 |
| PIT weather/book | opportunity | NA | NA | 主要 blocker |

## 机制一：目标日盘中交易，不是 T-1 forecast bet

| entry timing | BUY rows | 买入现金流 | 资金占比 |
|---|---:|---:|---:|
| 目标日前 | 95 | $3,158.19 | 1.30% |
| 目标当地日 | 7,983 | $239,720.96 | **98.70%** |

目标日买入资金按当地小时分布：

| local hour | BUY rows | 买入现金流 | 目标日占比 |
|---|---:|---:|---:|
| 00–06 | 90 | $4,362.80 | 1.82% |
| 06–10 | 919 | $26,308.97 | 10.97% |
| 10–14 | 3,515 | $97,379.25 | 40.62% |
| 14–18 | 3,349 | $106,491.50 | 44.42% |
| 18–24 | 110 | $5,178.44 | 2.16% |

**85.0% 的目标日资金集中在当地 10:00–18:00。** 这正是 Tmax 路径逐渐显形、但仍存在 reheat/overshoot 风险的窗口。它依赖的是盘中观测和市场重定价，不是夜间静态 forecast。

## 机制二：资金主腿是高价 NO carry

所有 BUY 的成交价资金分布：

| entry price | BUY rows | 买入现金流 | 资金占比 |
|---|---:|---:|---:|
| ≤1c | 1,499 | $312.05 | 0.13% |
| 1–5c | 866 | $887.41 | 0.37% |
| 5–20c | 1,198 | $3,730.43 | 1.54% |
| 20–80c | 2,613 | $40,241.21 | 16.57% |
| 80–95c | 667 | $44,987.74 | 18.52% |
| ≥95c | 1,235 | $152,720.32 | **62.88%** |

其中 `NO ≥95c` 单独占约 **$131,205**。结合目标日 10–18 点入场，它最主要的资金表达是：当温度路径已经使某些 exact bracket 很难成立时，以 95–99c 买这些 bracket 的 NO，赚剩余的薄 residual。

但这不是无风险捡钱：exact bracket 会受 late reheat、source-to-settlement basis 和 overshoot 影响；一次错误的 99c NO 会损失大量薄利。

## 机制三：主要利润不是 99c carry，而是中价动态 edge

按每个 outcome-position 的加权平均买入价归组：

| avg entry band | positions | cash cost | PnL | ROI | position win rate |
|---|---:|---:|---:|---:|---:|
| ≤1c | 294 | $258.59 | -$83.20 | -32.17% | 4.4% |
| 1–5c | 360 | $913.89 | +$417.82 | +45.72% | 12.8% |
| 5–20c | 537 | $3,476.33 | +$718.20 | +20.66% | 22.7% |
| 20–80c | 876 | $47,645.09 | **+$8,985.11** | **+18.86%** | 59.7% |
| 80–95c | 262 | $58,292.71 | **+$5,091.90** | +8.74% | 91.6% |
| ≥95c | 400 | $132,292.54 | +$3,039.57 | +2.30% | 98.0% |

因此：

- `≥95c` 是**资金主腿**，提供高胜率、低 ROI carry。
- `20–95c` 是**利润主腿**，合计贡献约 $14,077，占总 PnL 约 78%。
- `≤5c` 是小额 tail optionality；笔数多但资金不到 0.5%，其中 ≤1c 整体亏损。

这说明真正的 edge 不只是判断“哪个 bracket 已经死了”，还包括对仍有不确定性的 current/adjacent brackets 做动态定价。

## 机制四：全 ladder 双边库存，不是单边押注

| event 结构 | events | cash cost | PnL | ROI |
|---|---:|---:|---:|---:|
| 同 event 同时 BUY YES 和 NO | 461 | $204,201.10 | +$14,316.81 | +7.01% |
| 只 BUY YES | 219 | $13,626.12 | +$2,012.46 | +14.77% |
| 只 BUY NO | 150 | $25,891.93 | +$1,725.84 | +6.67% |
| 至少 3 个 condition/bracket | 418 | $191,264.01 | +$12,290.63 | +6.43% |
| 同一 binary condition 两个 token 都交易 | 276 | $153,110.75 | +$9,583.44 | +6.26% |

55.5% 的 events 同时买过 YES 和 NO，50.4% 覆盖至少三个 bracket，33.3% 甚至交易同一 condition 的两个 token。这与“先形成完整 Tmax 分布，再在多条腿之间做相对价值和库存调整”一致。

不能把“同时买 YES/NO”简单解释成方向矛盾：在 exact-bracket negative-risk ladder 中，不同 bracket 的 YES/NO 是对完整分布不同区域的表达；同一 condition 两边都交易还可能来自跨时点 repricing、maker 成交或 complete-set 管理。

## 机制五：主动退出和 maker 执行是重要组成

- 830 个 events 中 656 个发生过 SELL，占 **79.0%**。
- 累计 BUY/SPLIT 现金流 $243,719；SELL 回收 $133,801，REDEEM 回收 $127,178。
- 12,153 笔 trades 中约 5,532 笔推断为 maker，占 **45.5%**。
- 有主动退出的 656 个 events 贡献 +$16,536.75，占总 PnL 约 91.6%。

所以它并不是全部持有到结算。它会在温度路径、盘口概率或库存发生变化时卖出；maker 价格改善也可能是其薄 edge 能留下来的关键。复制者在 activity 出现后追单，通常拿不到相同 spread 和 queue。

## Wide-denominator sanity

全钱包 weather 分母的 fee-inclusive PnL 为 +$18,055、turnover ROI +7.41%，不是从少数盈利事件挑出来的。机制切片也覆盖数百 events；但切片互相重叠，只用于解释策略，不相加、不作独立 alpha 检验。

当前最值得注意的是边际下降：

| target month | events | cash cost | PnL | ROI |
|---|---:|---:|---:|---:|
| 2026-03 | 65 | $12,345 | +$1,400 | +11.34% |
| 2026-04 | 152 | $16,437 | +$1,703 | +10.36% |
| 2026-05 | 149 | $32,596 | +$3,762 | +11.54% |
| 2026-06 | 214 | $86,496 | +$6,240 | +7.21% |
| 2026-07 | 248 | $95,843 | +$4,950 | **+5.16%** |

规模增加时 ROI 从约 11% 降到 5%，可能来自市场竞争、容量、更多高价 carry 或策略扩张；公开数据不能识别因果，但不能假设历史 7.4% 会原样延续。

## Model / residual

该地址的 PIT weather state、probability forecast 和未成交机会不可见，无法计算同 rows 的 logloss/Brier 或 `p_wallet - market`。从成交行为推断，它大概率维护的是一个随盘中观测更新的 Tmax ladder 分布，但具体输入可能包括：

- 当前官方/快源 running max；
- 剩余加热时间与 forecast peak clock；
- overshoot/reheat 风险；
- source 到 Polymarket settlement source 的 basis；
- 每条 bracket 的盘口与流动性。

这些属于机制假设，不是已验证的模型复原。

## Frozen forward

- 历史绩效前 70 target dates ROI +11.67%，后 71 dates +6.62%，同号但边际下降。
- 去掉盈利最高 10 天仍为 +6.13%。
- blocker：没有交易前 weather/book snapshot、未选 opportunity、真实 order queue 和跨地址 entity 信息。

结论等级：

```text
strategy_mechanism=strongly_inferred
historical_profitability=confirmed
same_denominator_market_residual=NA
copyability=inconclusive
```

## Bloodline placement

- shared data logic：不改；本研究只读外部 public API。
- feature layer：不把推断出来的 wallet 行为写成天气事实。
- `fact_signal_candidates`：不写入；外部钱包没有完整 opportunity universe。
- shadow/collector：若继续，应采集 wallet trade 前最近 PIT book + 本地 weather state，做 zero-notional followability replay。
- registry：不注册为本项目 live 策略；它是外部机制参考。

可复跑脚本：
[`research_external_wallet_strategy_v1.py`](../../../scripts/analysis/wallet_weather/research_external_wallet_strategy_v1.py)。
