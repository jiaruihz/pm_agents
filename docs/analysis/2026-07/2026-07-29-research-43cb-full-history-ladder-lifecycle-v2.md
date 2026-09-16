# 0x43cb 全历史完整 ladder 生命周期复盘 v2

## 数据快照

- wallet：`0x43cb4ae1f4ddc9e671486c79c9f40a6fd98b84df`
- snapshot：`20260729T121249Z`
- 覆盖：2025-10-28 13:31:03 UTC 至 2026-07-29 12:12:49 UTC
- 全账户 public activity：353,096 rows；weather activity：212,340 rows
- 分析 grain：`city × target_date × complete mutually-exclusive ladder`
- 2,989 个 portfolios、114 个 target dates、50 个城市
- Gamma 完整 ladder：2,987/2,989；缺 Austin / Dallas 2026-07-09
- 时区覆盖：50/50 城市、2,989/2,989 portfolios
- JRS raw：
  `/Volumes/jrs/pm_agents/research/external_wallet_weather/raw/`
  `wallet=0x43cb.../snapshot=20260729T121249Z`
- JRS 文件：3,545 个、237,802,205 bytes；未创建 SQLite，未写 canonical
  `fact_trades` / `fact_signal_candidates`

复现：

- [artifact locator](generated/wallet_43cb_full_history_v1/README.md)；完整 summary、逐 event
  portfolio、月度漂移和城市切片均位于其中登记的 immutable archive snapshot，Git 不提交本机绝对 symlink。
- [采集与 JRS 流程](../../WEATHER_EXTERNAL_WALLET_RESEARCH_PIPELINE.md)
- [全历史 replay 脚本](../../../scripts/analysis/wallet_weather/research_external_wallet_full_ladder_history_v1.py)

## 结论

全历史确认它的核心不是单次挑一个 exact bracket，而是：

> **target day 从上午开始铺连续 YES strip，下午继续分批补齐；长期中位宽度
> 6 档。多数 event 同时包含共同 range 底仓和中心加权，最终主要等结算，
> SELL 只是少量辅助退出。**

但策略不是静态的。3–4 月更像“早入场、宽 strip、重中心、长时间累积”；6–7 月
明显演化成“晚入场、较窄 strip、range 底仓更均匀、交易批次更少”。

## 入场时间

按全部 BUY cost：

| 指标 | 结果 |
|---|---:|
| target day | 99.9807% |
| target day 前一天 | 0.0193% |
| 当地 10–14 时 | 37.11% |
| 当地 14–18 时 | 49.70% |
| 当地 10–18 时合计 | 86.81% |
| cost-weighted fill hour 中位数 | 14:05 |
| p10 / p25 | 09:59 / 12:32 |
| p75 / p90 | 15:35 / 16:52 |

按 event 首笔 BUY：

- 中位数 11:59；
- p25 08:41；
- p75 13:56；
- p90 15:26。

所以它通常不是 14 点才开始判断，而是中午前先建仓，主要资金在 12:30–15:35
逐步打进去。公开 activity 只能看到 fill；maker 单的真实 post time可能更早。

## 分批与执行

每个 `city × target_date`：

| 指标 | p25 | 中位数 | p75 | p90 |
|---|---:|---:|---:|---:|
| unique BUY transactions | 15 | 34 | 80 | 171 |
| gap `>60s` bursts | 3 | 7 | 13 | 23 |
| gap `>5m` sessions | 3 | 5 | 9 | 13 |
| first→last BUY | 61m | 169m | 364m | 712m |

这不是“一次买入”。典型 event 有 34 个 BUY transactions，分成约 7 个一分钟级
bursts、5 个五分钟级 sessions，持续约 2.8 小时。p90 会持续近 12 小时。

结合跨城市、秒级批量 transaction 和长期一致的 shares 形态，执行 bot 是确定的。
更准确的描述是 **multi-session basket accumulator**；它可能同时使用 maker 和
taker，但 public activity 没有原始挂单时间与未成交订单，不能从 fill 间隔反推
完整 queue 策略。

## 档位宽度

表达分布：

| 表达 | events |
|---|---:|
| 多档 YES strip | 2,917 |
| single YES | 47 |
| mixed YES/NO | 12 |
| NO only | 13 |

YES 占 BUY cost 的 99.71%。完整 ladder 指标：

- 98.46% portfolios 的 positive YES exposure 是连续 strip；
- 档位数中位数 6，p25=5、p75=7、p90=10；
- ladder coverage 中位数 54.55%，p25=45.45%、p75=72.73%；
- winner 落在 positive YES strip 内的 cashflow-complete settled share 为 97% 左右。

因此“买中间档 YES”不能拆成六次独立预测。经济表达是覆盖约一半 ladder 的
bounded support 区间。

## 共同底仓与中心加权

定义：

```text
range_base_fraction
  = min(positive YES shares) × bracket_count / total positive YES shares

modal_overweight_fraction
  = 1 - range_base_fraction
```

全历史中位数：

| 指标 | 中位数 |
|---|---:|
| range base fraction | 54.74% |
| modal overweight fraction | 45.26% |
| event 内 YES shares CV | 0.267 |
| 最大单档 shares / 总 shares | 21.36% |
| shares-weighted ladder center | 70.0% |

它不是纯等 shares range，也不是单纯重押 mode。更接近：

```text
约 55% shares 用于共同连续区间底仓
+ 约 45% shares 追加到更看好的中心/上侧档位
```

weighted center 在 ladder 的 70% 位置，说明所买 support 通常偏向 ladder 上半部。
这与 target-day 仍存在 remaining-heat/overshoot 的交易背景一致。

## SELL、持仓与结算

SELL 很少：

- 329/2,989 events 有 SELL，event share 11.01%；
- 645 SELL rows、642 transactions；
- proceeds `$3,013.25`，仅为总 BUY cost 的 0.55%；
- cost-weighted SELL price 69.63¢；
- SELL proceeds 只有 22.34% 在 `>=95¢`，14.03% 在 `>=99¢`；
- first BUY → first SELL 中位 2.57h，p25=1.14h、p75=5.20h。

所以它不像 yourthos 那样主要在 99¢ 卖确定性腿。SELL 更像少量日内
rebalance、减仓或回收错误中心加权，不能定义主退出机制。

结算行为：

| 指标 | 结果 |
|---|---:|
| 有 public REDEEM | 2,890 events，96.69% |
| 无主动 SELL、直接进入结算 | 2,649 events，88.62% |
| active SELL 后仍结算 | 329 events |
| first BUY → first REDEEM 中位 | 13.93h |
| last BUY → first REDEEM 中位 | 10.60h |
| target day 起算 REDEEM 中位 | +25.27h |

REDEEM 通常发生在目标日后的当地约 01:16；p25 约次日 00:36，p75 约次日
03:35。它的“持仓周期”本质上是上午/中午开始建仓，持到次日结算，不是几小时
波段清仓。

## 策略存在明显月度漂移

| 月份 | events | 首笔当地时刻中位 | BUY span中位 | tx中位 | YES档位中位 | base fraction中位 |
|---|---:|---:|---:|---:|---:|---:|
| 2026-03 | 564 | 08:45 | 358m | 116 | 8 | 27.1% |
| 2026-04 | 316 | 08:39 | 394m | 120 | 8 | 29.7% |
| 2026-06 | 884 | 12:15 | 140m | 28 | 6 | 61.0% |
| 2026-07 | 1,201 | 13:00 | 112m | 21 | 5 | 69.7% |

3–4 月的版本很早开始、买得宽、重心追加明显，而且持续约 6 小时。6–7 月版本
推迟约 3–4 小时，strip 缩到 5–6 档，transactions 减少约四分之三，共同 range
底仓提高到约 60–70%。

因此复制策略时不能把全历史混成一个固定模板。当前更接近：

```text
target-day 午后较窄 support strip
+ 较均匀的 common shares
+ 较轻的 modal overweight
```

而不是 3 月那种全天宽分布重加权。

## 描述性盈利与证据边界

主 PnL 不使用 `winner × direct activity shares`。审计发现 NegRisk conversion
会改变最终 redeemable shares；例如 Los Angeles 2026-04-08 的 public REDEEM
约 1,097 shares，而普通 TRADE activity 中该 winning YES 直接买入仅约 230
shares。

因此主口径是：

```text
Gamma resolved
+ position currentValue < $0.01
+ public TRADE / SELL / REDEEM / MERGE / SPLIT cashflow
```

结果：

- cashflow-complete：2,966 events / 113 target dates；
- BUY cost `$544,942.37`；
- public cashflow PnL `+$26,443.56`；
- turnover ROI `+4.85%`；
- target-date block bootstrap 95% CI `[+4.07%, +5.64%]`；
- 正 PnL events `2,306/2,966 = 77.75%`，负 PnL events 660；
- 正 PnL target dates 85.84%；
- 另有 resolved current value `$3,227.57` 未混入 realized。

winner 落在 positive YES strip 内的 2,882 个 events 中，79.49% 为正 PnL，
public-cashflow turnover ROI 为 `+4.96%`；winner 落在 strip 外的 84 个 events
合计 ROI 为 `-57.87%`。因此“覆盖 winner”非常重要，但仍不足以保证 event
盈利：不均匀中心加权、组合成本与 SELL/conversion 都会改变最终现金流。

按 BUY span 做纯描述性切片，`>60m` 的 2,249 个 events 正 PnL 率 79.77%、
ROI `+4.97%`；`<=60m` 的 717 个 events 正 PnL 率 71.41%、ROI `+2.96%`。
这与“长时间等待 maker/市场流量、再补齐篮子”一致，但不构成概率横跳的因果
证据，因为时期、城市、仓位和选择分母均不同。

### 亏损形态

2,966 个 cashflow-complete events 中有 660 个亏损，gross loss
`$5,714.31`；单次亏损中位 `$2.91`、均值 `$8.66`、p90 `$19.49`、最大
`$166.95`。对应 2,306 个盈利 events 的 gross gain 为 `$32,157.87`，
profit factor `5.63`。

按完整 ladder 与最终净 YES shares 做互斥诊断：

| 亏损形态 | events | gross loss | 单次中位 | 单次均值 | p90 | 最大 |
|---|---:|---:|---:|---:|---:|---:|
| winner 在整个持仓区间之外（真上/下尾） | 49 | $313.36 | $1.39 | $6.40 | $13.68 | $96.28 |
| winner 位于区间内部、但该 exact bracket 最终无正 YES（internal hole） | 20 | $222.26 | $2.54 | $11.11 | $11.64 | $138.73 |
| winner 在 strip 内、但不是最终最大 shares 档（modal miss） | 572 | $5,032.74 | $3.22 | $8.80 | $20.51 | $166.95 |
| winner 正好是最大 shares 档、但成本/现金流仍亏 | 19 | $145.94 | $0.49 | $7.68 | $19.97 | $62.24 |

亏损比例口径：

| 亏损形态 | 亏损事件投入 | 加权亏损率 | 单次亏损率中位 | 占全部 gross loss |
|---|---:|---:|---:|---:|
| winner 在整个持仓区间之外 | $313.36 | -100.00% | -100.00% | 5.48% |
| internal hole | $222.26 | -100.00% | -100.00% | 3.89% |
| modal miss | $64,377.55 | -7.82% | -5.44% | 88.08% |
| modal hit 仍亏 | $4,257.30 | -3.43% | -3.12% | 2.55% |

全部 660 个亏损 events 共投入 `$69,170.47`、亏 `$5,714.31`，亏损事件
加权亏损率 `-8.26%`；单次亏损率中位 `-6.14%`，p75 `-23.68%`，因为尾部与
internal-hole 事件均为 `-100%`，p90/p95 都是 `-100%`。相对全部已结算投入
`$544,942.37`，gross loss 占 `1.05%`。

作为对照，2,306 个盈利 events 共投入 `$475,771.90`、gross gain
`$32,157.87`，加权盈利率 `+6.76%`、单次盈利率中位 `+3.80%`。高胜率
`77.75%` 与 gross profit factor `5.63` 最终把全样本净 turnover ROI 推到
`+4.85%`。

真尾部进一步拆分：

- lower-tail 38 次，合计 `$206.09`，单次中位 `$1.50`、最大 `$55.75`；
- upper-tail 11 次，合计 `$107.27`，单次中位 `$1.33`、最大 `$96.28`；
- 49 次全部是 100% buy-cost loss。美元中位数小，是因为很多尾部 strip 本身
  以低价小成本买入，不代表风险率低。

`internal hole` 是 public fills 能观察到的“某腿没形成”的最接近代理，但不能
直接命名为 maker non-fill：也可能是主动跳档、SELL/NegRisk conversion 后的净
仓位形态。全样本 non-contiguous strip 共 44 个，正 PnL 率 43.18%、ROI
`-6.29%`；contiguous strip 为 2,922 个，正 PnL 率 78.27%、ROI `+4.91%`。
因此缺腿形态明显危险，但未成交 maker 的真实尝试分母仍不可见。

最大损失来源不是尾部，而是 modal miss：它占 gross loss 的 88.08%。典型形态是
winner 仍在宽 strip 内，但资金大量压在相邻中心档，winning bracket shares 不足以
覆盖整个篮子成本。另有 19 次 modal hit 仍亏，说明即使天气中心判断正确，组合
买贵、fee/执行或 conversion 现金流也可令 event 亏损。

这能确认该钱包的已成交 temperature portfolio 长期赚钱，不只是一两天样本。
但它仍是 **selected wallet fills**：

- 看不到未交易 city-days；
- 看不到私有 forecast / signal；
- 没有同一时点 executable market baseline；
- 没有 unfilled/cancelled maker orders；
- official WEATHER leaderboard 还可能包含非 highest-temperature weather markets，
  volume 定义也不同。

所以 `+4.85%` 是该钱包已成交组合的 turnover ROI，不是可直接复制的
opportunity-level alpha。结论仍是：

```text
descriptive_wallet_profitability = confirmed
same_denominator_market_residual = NA
copy_strategy_live_gate = FAIL
```

## 动作

- 这份全历史结果替代旧 5,500-row sample 作为 0x43cb 长期行为基准；
- 后续地址统一走 JRS pipeline 和 whole-ladder replay；
- 对 0x43cb 的复制研究应按 3–4 月、6–7 月分 regime，当前版本优先研究
  “午后窄 strip + 高 common-base”；
- 仍只做 zero-notional research，不跟单、不改 live。
