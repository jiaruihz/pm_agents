# Korea joint-ladder residual v3

## 结论

结构性错误已修复并重训。v3 对同一 PIT state 的每个固定 exact bracket 同时输出
概率，逐 state 严格和为1；market favorite 改变不会改变 held bracket identity。

训练后选择参数：market/weather log-pool `alpha=0.50`；
AMOS source cross 时 source-state prior 权重 `0.75`，未cross时
`1.00`。当 alpha=0 时模型会诚实退回 joint market baseline。

## 数据快照

- raw AMOS-book replay：`298` states。
- raw full-ladder books：`297` states。
- exact join：`297` states /
  `13` dates /
  `26` city-days。
- train：`124` states / `6` dates，
  `<= 2026-07-20`。
- historical holdout：`173` states /
  `7` dates /
  `14` city-days。
- full-ladder平均双边mid覆盖：
  `53.62%`；
  单边档位使用可见bound与自然0/1边界的区间中点，再对整条ladder归一化。
- prior mapping fallbacks：`288 / 2,970` class-to-ladder mappings
  （`9.70%`，主要是五档 prior 落入市场上下 tail condition 后合并到该 tail，
  不是丢概率）。
- trade_class=`research_replay`，actual fills=`0`。

## 正确的概率结构

1. routine/source long prior 各自产出五档 remaining-heat distribution。
2. 分别映射到当时市场列出的固定 absolute brackets。
3. source cross 与 non-cross 使用train选择的不同 source-prior权重，允许学习
   AMOS terminal false-cross。
4. weather distribution与normalized market full ladder做log-pool。
5. 最后只做一次softmax归一化；不存在逐favorite独立binary probability。

## 同分母 proper score

| model | joint logloss | multiclass Brier |
|---|---:|---:|
| normalized full-ladder market | 0.610921 | 0.334464 |
| coherent v3 | 0.701851 | 0.377019 |

v3 − market logloss：
`+0.090930`，target-date block 95% CI
`[+0.050456,
+0.137480]`。

v3 − market Brier：
`+0.042555`，95% CI
`[+0.013334,
+0.074372]`。

这个holdout已经在v2语义排错中看过，故 `forward=NA`；只能验证修复后的结构和
historical结果，不能晋升live。

## 用户点名的两个时点

### Busan 07-22 12:46

- joint market P(33)：`73.96%`。
- routine/physical joint prior P(33)：
  `70.69%` /
  `78.06%`。
- v3 P(33)：`77.15%`。
- v3 P(34+)：`22.26%`。

33与更高档来自同一个simplex，不能再同时虚高。

### Busan 07-22 14:23

- joint market P(33)：`47.93%`。
- routine prior P(33)：`81.92%`。
- source-state prior P(33)：`0.87%`。
- v3 P(33)：`33.32%`。
- v3 P(34)：`58.81%`。
- 其余档合计：`7.87%`；整条 ladder 概率和
  `0.9999999999999997`。

AMOS cross不会机械把33置0，也不会再通过绝对温度线性系数把33推到86.6%；
它只改变source/routine mixture，最终仍在整条ladder共同归一。

## 全 ladder fee-adjusted expression replay

- orders：`14` /
  `7` dates /
  `14` city-days。
- wins：`3`。
- fee-adjusted PnL：`$-8.4782`。
- ROI：`-36.11%`，target-date block 95% CI `[-82.61%, +24.72%]`。
- 每个state同时比较所有bracket的YES/NO可执行ask+depth，取最大正edge；
  每city-day仅首单、最多5 shares、hold-to-settlement。

## 双漏斗与动作

Signal funnel：

`297 joined PIT full-ladder states → coherent distribution →
all-bracket YES/NO positive edge → 14 first city-day entries`。

Evidence funnel：

`298 AMOS replay states → 297 raw full-ladder states →
297 exact timestamp joins → 173 holdout states →
0 actual fills`。

`model_semantics=PASS significance=FAIL
baseline=FAIL forward=NA
conclusion=joint_residual_does_not_pass_market_historical_audit`。

动作：v2 favorite-binary residual继续停用；v3保留为正确结构的research baseline。
只有新的未查看日期在同分母 joint proper score 上打赢market，才进入
position-aware zero-notional forward。
