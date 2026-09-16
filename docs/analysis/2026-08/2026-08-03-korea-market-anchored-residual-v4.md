# Korea market-anchored residual v4

## 结论

v4 把盘口 logit 固定为 offset，天气与升温路径只能学习 residual；expanding OOF 若未同时改善
logloss 和 Brier，模型必须精确退回 market。本次最优天气 challenger 在 train OOF 的
logloss delta 为 `+0.001185`，Brier delta 为 `+0.000449`，
因此 `accepted_weather_residual=False`。

这版表达已经干净，但天气 residual 仍未证明有效；当前选中模型是 `market_fallback`，
不会产生伪 edge，也不进入 forward shadow。

## 数据与目标

- target：decision-time favorite exact bracket 最终正好胜出的概率；不是 touch。
- train：`124` states / `6` dates，截止 `2026-07-20`。
- historical holdout：`173` states / `7` dates，
  `2026-07-21..2026-07-27`。
- path/湿度/风/雨覆盖均为 `100%`，cloud layers 覆盖 `95.62%`。
- 历史表只有 archived `snapshot_ts`，缺独立 feature/execution book snapshot ID；接 WCIR 前必须补齐。
- trade_class=`research_replay`，actual fills=`0`，forward=`NA`。

## 同分母 probability score

| probability | logloss | Brier | logloss − market | Brier − market |
|---|---:|---:|---:|---:|
| raw market | 0.479758 | 0.170340 | 0 | 0 |
| best weather challenger | 0.481520 | 0.171143 | +0.001762 | +0.000803 |
| selected policy | 0.479758 | 0.170340 | +0.000000 | +0.000000 |

best challenger holdout logloss delta 95% date-block CI：
`[+0.000139,
+0.003539]`。

## 表达

每个 PIT state 同时计算 favorite bracket：

`YES edge = p_exact - YES ask - fee`

`NO edge = (1-p_exact) - NO ask - fee`

选两者最大正 edge；没有正 edge 就 skip。每 city-day 首单、最多 5 shares。selected policy：
`0` orders / `0` dates，
PnL `$+0.0000`，ROI `None`。

## 双漏斗与动作

Signal funnel：`173 PIT states → 0`
positive-edge states → `0` first city-day orders。

Evidence funnel：`173 PIT weather+quote+settlement rows →`
`0` research orders → `0` actual fills。

`significance=FAIL baseline=FAIL forward=NA conclusion=inconclusive`。

动作：保留 v4 的 offset/fallback/expression 结构作为下一版基线；不接 forward shadow。
下一轮新增信息必须来自严格 PIT 的 forecast peak clock/revision、source→routine/settlement basis 与
路径 regime，而不是再调交易时段或 edge threshold。
