# Korea 入场顺序与交易表达 A/B v1

## 数据快照

- 数据源：`/Volumes/jrs/pm_agents/research/korea_intraday_residual/v4/full_train_holdout_opportunity_replay.csv`，archived PIT AMOS + executable taker books +
  canonical settlement。
- 生成时间：`2026-07-30T15:27:07.094980+00:00`。
- trade_class：`research_replay`，不是 actual fills。
- rows：`483` states / `21` target dates /
  `34` city-days。
- settled ratio：`100%`；unsettled：`0`；missing_bracket：`0`。
- 固定 5 shares；entry 用 selected-side ask，Weather taker fee
  `shares × 0.05 × p × (1-p)`。

## Target 与 A/B

在相同 city-day、相同 PIT quote/label 上，比较：

1. entry ordering：首个 executable quote、当地 12–16 点首次 quote、首次
   source/routine running max 创新高、首次 favorite 换档；
2. expression：买 favorite exact bracket YES，或买其互补 NO。

共看 `18` 个 policy×side candidates；未做多重检验修正，
checkpoint 只允许 train 选择后在 frozen holdout 复核。

## 宽分母首单

| expression | orders | wins | win rate | fee PnL | ROI | date-block 95% CI |
|---|---:|---:|---:|---:|---:|---:|
| favorite YES | 34 | 15 | 44.12% | $-4.4047 | -5.55% | [-43.61%, +33.19%] |
| favorite NO | 34 | 19 | 55.88% | $-5.8941 | -5.84% | [-37.29%, +22.58%] |

首个 state 的平均 YES spread 是
`3.87%`，YES ask + NO ask 的平均
overround 是 `3.87%`；两边
Weather taker fee 再合计
`2.19%`/share。也就是没有
概率 residual 时，单靠换 YES/NO side 无法消除约 6c 的双边 taker friction。

## 入场顺序：frozen holdout

| first quote at/after local time | orders | YES ROI | YES CI | NO ROI | NO CI |
|---|---:|---:|---:|---:|---:|
| checkpoint_12 | 14 | +21.44% | [-16.88%, +56.18%] | -66.17% | [-100.00%, -6.99%] |
| checkpoint_13 | 14 | +7.81% | [-1.68%, +18.16%] | -61.60% | [-100.00%, -12.65%] |
| checkpoint_14 | 14 | +5.69% | [+1.60%, +12.36%] | -37.78% | [-100.00%, -23.34%] |
| checkpoint_15 | 14 | +4.07% | [+0.83%, +8.49%] | -26.09% | [-100.00%, -5.56%] |
| checkpoint_16 | 11 | +3.74% | [-0.15%, +7.81%] | -22.07% | [-100.00%, -1.78%] |

## Train 选 checkpoint → frozen holdout

| side | train-selected policy | train orders | train ROI | holdout orders | holdout ROI | holdout CI |
|---|---|---:|---:|---:|---:|---:|
| NO | checkpoint_16 | 17 | +20.32% | 11 | -22.07% | [-100.00%, -1.78%] |
| YES | checkpoint_14 | 19 | -3.20% | 14 | +5.69% | [+1.60%, +12.36%] |

## Event-driven entry 诊断（全窗口）

| entry event | orders | dates | YES ROI | YES CI | NO ROI | NO CI |
|---|---:|---:|---:|---:|---:|---:|
| first_source_new_high | 29 | 19 | +11.74% | [-33.13%, +58.59%] | -20.11% | [-55.42%, +14.68%] |
| first_routine_new_high | 29 | 18 | -0.42% | [-30.75%, +32.34%] | -13.23% | [-48.03%, +18.95%] |
| first_favorite_change | 24 | 16 | -0.62% | [-40.35%, +41.40%] | -16.03% | [-51.47%, +18.91%] |

这些事件是 PIT 可得的机制标签，但本轮是 post-hoc diagnostic，不注册成 hard gate。

## 双漏斗

Signal funnel：

`483 executable states → 34 first city-day states → checkpoint/event policy
selection`。

Evidence funnel：

`35,469 AMOS states → 885 book states → 483 as-of executable states
→ 483 settled expressions → 0 actual fills`。

Seoul `2026-07-08..14` 和 `2026-07-28` 的盘口仍是 coverage gap，不计为策略过滤。

## 8 环与结论

- 已覆盖：描述性 replay、target-date block CI、概率 baseline 的既有结果、
  taker execution cost、同 rows YES/NO 反事实、日期相关性。
- 未覆盖：actual fills、maker queue/fill probability、market prints、容量扩展、
  新 collector frozen forward。

模型层 raw market 仍显著优于 weather residual。入场 checkpoint 和 YES/NO
expression 若没有在 train 选择后于 frozen holdout 同号且 CI 不跨 0，就不能替代
模型 baseline 的失败。

## 优化优先级

1. **先改模型 target/结构**：把 remaining-heat 的完整 bracket distribution 接入
   AMOS residual，而不是继续优化 binary favorite classifier。现模型 proper score
   明确输 market，这是主瓶颈。
2. **交易表达要扩成 router，不要固定换边**：同一 distribution 同时评估
   `current YES / current NO / d1 YES / upper strip / skip`。本轮 favorite YES 与
   favorite NO 全窗口都亏，且 train/holdout 赢家反转，没有固定 side 优势。
3. **不优化固定钟点**：12–16 点 checkpoint 相对首单的 paired timing delta 均未
   稳定通过；train 选出的 NO 16点从正 ROI 翻成 holdout 负 ROI。继续调时间只会
   过拟合。
4. **执行层先去重复、再研究 maker**：483 states 只有 34 个 city-days，同一 thesis
   重复 taker 会重复支付 spread/fee。zero-notional 中应记录 information-state
   identity，只在 distribution 或可执行表达实质变化时生成新 candidate。maker
   是否更优仍缺 queue、fill probability 与 adverse-selection 证据。
5. **补 round-trip objective**：当前只评 hold-to-settlement；若被跟踪地址主要赚
   盘中 repricing，需要保存同 token 全 ladder 路径、真实买卖/撤单与 5/15/30/60m
   markout，另评 entry→exit PnL，不能用本报告代替。

`significance=FAIL baseline=FAIL forward=FAIL
conclusion=inconclusive_no_entry_or_expression_promotion`；
动作：不改 live。保留 remaining-heat 模型，不把本轮最优时间或 side 写成
eligibility gate。

后续状态（2026-07-30）：distribution prior 已由
`2026-07-30-korea-stacked-remaining-heat-residual-v2.md` 完成真实串联和重训；
primary stacked residual 的 Brier/logloss 均显著输 raw market，因此没有进入
zero-notional/live。这里的“下一版先接入”已经完成，不再是待办。
