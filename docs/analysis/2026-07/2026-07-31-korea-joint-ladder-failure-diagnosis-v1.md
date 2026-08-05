# Korea joint-ladder failure diagnosis v1

## 数据快照

- 数据源：`/Volumes/jrs/pm_agents/research/korea_joint_ladder_residual/v3/holdout_joint_predictions_long.csv`、
  `holdout_first_signal_trades.csv` 与 `summary.json`；均为 JRS research artifact，
  未读取或重建 canonical fill facts。
- 原 v3 artifact 时间：`2026-07-30T17:12:06.881910+00:00`；本诊断：
  `2026-07-31T03:56:39.187724+00:00`。
- `1,897` bracket rows / `173` PIT states / `7` settled target dates /
  `14` city-days；unsettled=`0`，missing_bracket=`0`。
- trade_class=`research_replay`，actual fills=`0`。
- full-ladder simplex 最大误差：
  `1.110e-15`。

## 目标与结论

目标：在同一批 PIT full-ladder states 上，把概率误差、入场时机与执行成本分开，
判断 v3 输给 market 的主因。

**主因是模型的校准/信息集不足，不是手续费或 spread。** 入场推迟到中午以后
出现正 ROI 点估，但这是看过结果后的4个 timing 切片之一，只有6–7个日期且
CI很宽；它不能推翻概率层在所有预定义时段都输 market 的事实。

范围边界：这只否定 v3 的“全天每个 PIT state 都重估 full ladder”表达，
**不否定 Busan CrossNO 的 source-event latency 方向**。后者在新档 first-seen
后的短窗口交易 previous-bracket NO，分母、时钟与收益机制都不同，必须单独评估。

## 概率层：市场到底赢在哪里

- headline joint logloss：v3=`0.7019`，
  market=`0.6109`；
  delta=`+0.0909`，
  95% CI `[+0.0505, +0.1375]`。
- `7/7` holdout dates 上 model logloss 都高于 market。
- 在 `78.0%` states，模型给最终
  winner 的概率低于 market。
- market favorite accuracy=`81.5%`，
  model favorite accuracy=`69.9%`。
- 两者 favorite 不同时占 `16.8%` states；
  market对/model错=`24` states，
  model对/market错仅=`4`。
- 即使事后在holdout上重选全部150组参数，最优仍为
  `alpha=0.0`，即完全退回 joint market。
  这是诊断，不是合法的holdout调参结果。
- train expanding OOF 也未支持天气层：model logloss
  `1.5207`，
  market `1.3507`。

| slice | states | dates | model−market logloss | 95% date-block CI |
|---|---:|---:|---:|---:|
| city:Busan | 86 | 7 | +0.1222 | [+0.0635, +0.1834] |
| city:Seoul | 87 | 7 | +0.0597 | [-0.0091, +0.1189] |
| time_block:09-12 | 78 | 7 | +0.1499 | [+0.0851, +0.2312] |
| time_block:12-14 | 40 | 6 | +0.0589 | [+0.0051, +0.1166] |
| time_block:14-17 | 55 | 7 | +0.0238 | [-0.0039, +0.0536] |
| source_cross:False | 111 | 7 | +0.0895 | [+0.0500, +0.1273] |
| source_cross:True | 62 | 7 | +0.1042 | [+0.0417, +0.1750] |

这里越晚模型差距越小：09–12 为
`+0.1499`，
14–17 为
`+0.0238`；
但两个时段仍都没有打赢 market。Busan 和 Seoul、source-cross 与 non-cross
也都同方向。

## 入场时机

固定规则仍是“该时段内每 city-day 首个正 fee-adjusted edge，最多5 shares”：

| allowed local time | orders | dates | wins | ROI | 95% date-block CI |
|---|---:|---:|---:|---:|---:|
| 09-12 | 14 | 7 | 3 | -36.1% | [-82.9%, +24.6%] |
| 12-14 | 12 | 6 | 5 | +21.6% | [-45.5%, +94.1%] |
| 14-17 | 14 | 7 | 3 | +13.6% | [-100.0%, +61.5%] |
| 12-17 | 14 | 7 | 5 | +18.9% | [-47.9%, +89.3%] |

中午以后点估转正，说明“不要太早把弱天气 residual 变成仓位”值得继续收集；
但四个 exploratory timing policy 均 CI 跨0，且没有未查看 forward。不能把
`12:00` 再做成新的 hard gate。

## 执行能不能救

当前14笔合计 `66.99` shares，实际手续费仅
`$0.5643`：

| execution counterfactual | PnL |
|---|---:|
| observed_taker_fee | $-8.4782 |
| same_ask_no_fee | $-7.9139 |
| all_fill_mid_no_fee_optimistic | $-7.0465 |
| same_ask_minus_1c_no_fee | $-7.2440 |
| same_ask_minus_5c_no_fee | $-4.6644 |

即使假设14笔全部能在 side midpoint 成交且零手续费，PnL仍为
`$-7.0465`。
实际要打平需要平均每股改善
`12.7%`
（约 `12.66c`），
而当前平均 half-spread 只有
`1.29c`。
所以 maker/少付fee只能小修，救不了错误的方向选择。

normalized-market router 在同样“首个正edge”规则下点估 PnL
`$+7.6455`；
model router相对它少
`$16.1236`，
paired date-block CI
`[-34.9401, +5.0637]`，仍因7日样本而跨0。

## 为什么模型会输，以及更多数据有没有用

1. v3虽修正了联合分布结构，但 weather head仍很薄：portable source prior只有
   hour、温度/running max、距高点时间、湿度、露点差、风速和季节；routine
   prior更只有hour、running max、季节。cloud regime、forecast revision/
   peak clock、source→routine/WU basis、路径斜率等没有进入v3 joint head。
2. market residual只有6个训练日期，却要选择
   `alpha × source-cross weight × non-cross weight` 共150组；fold选择不稳定，
   且 expanding OOF已经输market。这是典型的小独立日期数+过拟合收缩不足。
3. 更多数据有用，但用途首先是让模型学会**何时退回market**、校准
   AMOS terminal false-cross/station basis，并做真正未查看forward；不是把同一
   模型多喂几天就自然变成alpha。
4. 建议继续积累至少30个、最好60个完整 settled target dates，逐 state 保存
   source first-seen、routine/WU、forecast revision、full ladder双边quote/depth。
   训练按 target_date expanding，参数只在train选，后续日期冻结评分。

## 市场是不是有很强的人

数据支持的是“**聚合市场价格在这7天、两个城市、各时段都比当前模型校准得好**”。
这与有专业天气交易者、自动化机器人或更好的source-basis经验一致，也可能只是
市场把公开预报、AMOS、routine与订单流综合得更好。

仅凭orderbook snapshot无法归因到某个强交易者；当前缺 market-wide prints、
maker identity、queue/fill与钱包级同步持仓。要证明“某些人在定价”，需要把
目标地址的逐笔成交与同刻 ladder repricing、source first-seen做事件研究。

## 双漏斗与动作

Signal funnel：
`173 PIT states → joint residual → positive executable edge →
14 first city-day model trades`。

Evidence funnel：
`1,897 bracket quotes → 173 complete settled states → 7 target dates →
14 research trades → 0 actual fills`。

8环：覆盖概率分布、统计推断、同分母market baseline、taker execution与
date-block相关性；缺真实fill/queue、容量和未查看forward。

`significance=FAIL baseline=FAIL forward=NA conclusion=inconclusive`。

动作：保留v3作为结构正确的baseline；不通过execution优化上线。继续采集完整
joint score，先测试market-shrinkage + source-basis/forecast-revision增量；
timing只并行zero-notional记录，不变成hard gate。
