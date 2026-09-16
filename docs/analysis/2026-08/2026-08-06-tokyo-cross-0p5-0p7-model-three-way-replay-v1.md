# Tokyo `.5` / `.7` / final-settlement model 三路回放

## 结论

固定同一份 Tokyo raw JMA cross-candidate 路径、真实各自触发时刻盘口、15-share target、
top ask 至少5 shares、max ask 0.97和官方fee后：`.7` 是当前更合理的规则基线；模型
positive-EV 点估 ROI 最高，但日期 CI 跨0、漏掉大量正确 `.5` 单且仍放行7/26错单，不能替换 live。
`.5` 的主要效果是扩大信号池，不是稳定提前入场。

## 三条路

1. `.5`：每个 target-date × previous exact bracket 首次 `JMA-basis >=0.5C` 直接taker。
2. `.7`：同一状态首次 `JMA-basis >=0.7C` 直接taker。
3. 模型：从每个 `.5+` raw event 开始，在已有 Tokyo weather-only `event_safe_selector_v2`
   给出 `P(final previous-NO wins)` 且 `p-ask-fee >= edge` 时首次taker。主路由edge=0，另报2c/5c。

模型路没有使用刚训练失败的 calibrated-market residual：trigger journal只有实际NO ask/size，绝大多数
没有同刻双边mid；拿ask冒充mid会改变模型输入合同。现有 final-settlement head 的 provenance 是
`historical_non_pit_observation_clock`，本次与真实first-seen event/ask相连只算research replay，不是
untouched forward。

## 双漏斗

signal funnel：窗口内105个scheduled `.5+` raw events → 63个首次 `.5` date-bracket；其中66个
`.7+` events → 44个首次 `.7` date-bracket。63/44均已结算，signal方向正确分别为58/63
（92.06%）与42/44（95.45%）。

evidence funnel：`.5` 有39个ask、20个满足5-share/max-ask执行条件；`.7` 有24个ask、10个可执行。
模型在完整`.5+`路径上只有52个settled scored events，0c positive-EV最终形成10笔/8日。
缺盘口或模型预测是coverage gap，不是策略筛除。

## fee-adjusted 结果

| 路由 | trades / dates | W-L | shares | cost | PnL | ROI | ROI 95% date CI | avg ask | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| first `.5` | 20 / 15 | 15-5 | 254.90 | $210.36 | +$1.44 | +0.68% | [-18.98%,+11.07%] | 0.8203 | -$18.41 |
| first `.7` | 10 / 10 | 8-2 | 128.04 | $104.28 | +$3.76 | +3.61% | [-18.52%,+20.17%] | 0.8092 | -$9.03 |
| model edge>=0 | 10 / 8 | 7-3 | 123.10 | $95.29 | +$4.71 | +4.94% | [-28.81%,+19.03%] | 0.7673 | -$9.70 |

在共同19个 settled target dates 上：`.7-.5` PnL delta `+$2.32`，95% CI
`[-$14.30,+$24.53]`；model-.5 `+$3.27`，CI `[-$9.89,+$24.00]`；model-.7
`+$0.95`，CI `[-$7.90,+$10.83]`。三组均不显著。

model edge>=0 相比20笔 `.5` 可执行单避免2笔错单，同时漏掉8笔正确单。2c与5c在现有离散
概率/ask上产生相同7笔/6日、5胜2负、PnL `+$3.52`、ROI `+5.29%`，但CI
`[-41.07%,+30.83%]`。这只是投入更少后的点估，不是阈值已验证。

## `.5` 是否真的更早

只有10个date-bracket在`.5`和`.7`各自时刻都满足执行条件；其中9个是同一份JMA观测直接
跨过两个阈值，wait=0。唯一真实等待是7/17 `29 NO`：约10.02分钟，ask从0.82升到0.90。
十对均值等待1.00分钟、中位0；`.7-.5` ask变化均值+0.8c、中位0。

所以全样本 `.5` 的20笔与`.7`的10笔不能用平均ask直接解释“早买便宜”：它们包含不同机会集合。
当前证据只证明`.5`多放进10笔可执行交易，而这批增量把总体ROI从3.61%稀释到0.68%。

## 错误与模型作用

model edge>=0 的三笔错误是7/15 `33 NO @0.24`、7/22 `34 NO @0.23`、7/26
`32 NO @0.77`。其中7/26模型给`p=0.8318`并以约5.29c edge放行，单笔亏`$7.79`；说明
现有 final-settlement head没有修复这类 terminal false cross。模型避开了7/29 `34 NO @0.119`
错单，但不能据此称为可靠过滤器。

## 工程修复与产物

本轮同时修复原runner的CI bug：旧 `roi_bootstrap` 会让同日多bracket相互覆盖；旧paired delta按
date-bracket抽样却标为date bootstrap。现在先按target_date汇总所有档，再在包含zero-trade日期的
固定19日分母上block bootstrap。点估不受影响，区间已重算。

- runner：`scripts/analysis/market_structure_edge/research_tokyo_jma_multivariate_market_v1.py`
- artifact：`/Volumes/jrs/pm_agents/research/artifact_store/tokyo_cross_three_way_20260806/`
- summary SHA-256：`c1bc92389f810c925c734f774722175c86521b8ee24528f8e1b1d54c308db3b1`
- targeted tests：9 passed；Tokyo相关整组测试见交付记录。

动作：`.7` 保持当前规则基线，`.5`不live；model三路只保留zero-notional同事件forward。后续collector
必须为每个`.5+` event连续保存双边mid、ask、完整depth和模型prediction，才能让calibrated-market
residual也进入同一三路回放。
