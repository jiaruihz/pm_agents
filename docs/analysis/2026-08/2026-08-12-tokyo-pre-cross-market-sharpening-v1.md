# Tokyo first pre-cross market sharpening（2026-08-12）

Status: `shadow_candidate / point-profitable / live gates FAIL / zero-notional only`

## 结论与动作

Tokyo 当前最值得继续积累的表达不是“天气模型取代盘口”，而是：每档只在 JMA 首次进入
`current +0.3/+0.4°C`、但 JMA native rounding 仍未跨到下一档时判断一次；盘口若仍偏 YES，保持原概率，
盘口已偏 NO 时才把同刻 market odds 有界强化。7 月开发窗选择的冻结式为：

```text
P_market_NO < 0.5: P_post = P_market_NO
P_market_NO >= 0.5: logit(P_post) = 2 × logit(P_market_NO)
BUY 5 NO iff P_post > five-share ask VWAP + official Weather fee
```

8/1–8/11 raw-exact 重放为 29 笔、28 胜，投入 `$136.8457`，fee 后 PnL `+$3.1543`、
ROI `+2.30%`。但 target-date bootstrap 95% CI 为 `[-1.19%, +5.65%]`，Brier 相对同 rows market
的改善 CI 也跨 0；并且这个窗口在形成方向前已经被查看过，所以只能叫 reused audit，不能叫 clean forward。
候选已冻结从 8/12 起离线 zero-notional 评分；不接 intent/order/fill，不改 live。

## 固定目标、时钟与 grain

- 目标：`P(final official exact Tmax leaves current official bracket upward)`，即 current exact bracket 的 NO。
- source：JMA AMeDAS collector exact first-seen；不使用 late backfill、issue time 或后到 settlement 信息。
- 决策时钟：source first-seen 后 180 秒内首个 causal、two-sided active-bracket book。
- grain：每个 `target_date × current exact bracket` 的首次 pre-cross proximity state；是否入场只在这个时钟判断一次，
  不能因为后续价格变便宜再入场。
- expression：只买 current bracket NO、每档最多一次、5 shares、taker ask depth 与官方 fee。

## 数据与双漏斗

输入 scope 不是项目“全部历史”，而是下列可核验切片：

| 层 | rows | target dates | 角色 |
|---|---:|---:|---|
| 历史 JMA/RJTT feature rows | 63,384 | 819 | 物理模型输入 universe，2024-04-30～2026-07-30 |
| 首次 pre-cross 历史候选 | 2,244 | 737 | train/calibration/physical audit |
| 7 月 market development expression | 124 | 12 | archive+15m 57 rows + hash-verified exact 67 rows |
| 8/1–8/11 strict raw-exact expression | 554 | 11 | reused audit |
| development 首次 pre-cross 候选 | 9 | 6 | exponent 选择 |
| reused-audit 首次 pre-cross 候选 | 34 | 11 | probability 与 execution 复核 |
| reused-audit selected trades | 29 | 11 | 真实 5-share depth + fee，28 胜 1 负 |

Signal funnel：`678 expression rows → 68 mechanism rows → 43 first date-bracket candidates → 36 selected
development/audit expressions`。Evidence funnel：开发 7 笔只有 top-of-book proxy、无 5-share depth；严格审计
29 笔全部有 exact 5-share ask depth、binary settlement 和 fee。

## 结构性修复

本轮先修了三个会制造假结论的问题：

1. development adapter 过去只收 `archive_reconstructed_plus_15m`，漏掉 7/22–7/29 已存在的
   `collector_exact_hash_verified`。现在可显式组合 clock classes；同一物理 checkpoint 重复时优先 exact。
2. runtime feature frame 中 `jma_current_minus_current_bracket` 是相对模型 checkpoint 的旧 anchor；直接把它套到
   另一条 market expression 会在 8/8 产生 `1.3～6.3°C` 假 margin。现在 source bracket 和 margin 都按目标
   expression 现场重算。
3. strict exact 窗内 JMA wind/precip feature 覆盖为 0；prior-METAR age 也超出历史训练支持。它们不进入当前候选，
   不能用 imputation 把“字段不存在”伪装成多气象特征模型。

## 概率层

历史 source-only logistic 使用 temperature path、remaining heat、slope、pullback、solar clock 等 13 个有 PIT
parity 的特征。固定 train `≤2025-06-30`（1,179 rows/380 日），2025H2 calibration（479/166），
2026-01-01～07-15 physical audit（529/176）；calibration 选 `C=0.03`。它在 physical audit 上
Brier/logloss=`0.08036/0.26939`，说明路径特征能预测天气，但在 market overlap 上仍明显输 market：

| evaluation | model | Brier | logloss |
|---|---|---:|---:|
| development 9 rows/6 日 | market | 0.01854 | 0.07563 |
| development | source-only physical | 0.07364 | 0.25407 |
| development | bounded posterior | **0.01643** | **0.05555** |
| reused audit 34 rows/11 日 | market | 0.05307 | 0.17065 |
| reused audit | source-only physical | 0.08204 | 0.26161 |
| reused audit | bounded posterior | **0.04961** | **0.14541** |

开发窗在预注册四个 exponent `1/1.25/1.5/2` 中选 2（K=4）。reused audit 相对 market 的
Brier delta `-0.00346`，95% CI `[-0.00792,+0.00081]`；logloss delta `-0.02524`，
CI `[-0.03821,-0.01351]`。主指标 Brier 仍未显著通过，因此 baseline gate 记 FAIL。

## 交易层与错误

29 笔中唯一失败是 8/11 12:26:55 JST 的 `32 NO`：JMA `32.3°C`，market NO `0.6295`，
posterior `0.7427`，5-share fee 后成本 `$3.63085`，终局停在 32，亏 `$3.63085`。同日其余五档 NO
虽胜，整日仍净亏 `$1.31435`。这正说明“升温过程中多个低档 NO 可以同时获胜”的现金流结构成立，
但每日最后 terminal bracket 的一次错误足以吃掉许多 99c 尘埃利润；不能用 28/29 胜率替代日期级风险。

本轮不根据这一个 8/11 错例追加 remaining-hour 或价格 hard filter。后续 clean forward 必须完整记录全部
selected/unselected candidate，等新 settled dates 后只做一次冻结复核。

## 三门与产物

- significance：FAIL，ROI CI 下界 `<0`。
- same-denominator market baseline：Brier 点估 PASS、CI FAIL；主门 FAIL。
- clean frozen forward：NA，8/12 才开始。
- live：不具备资格。

可重复入口仍是 `weather_model_evaluation.tokyo_market_prior_adapter`，没有新增平行 runner。主要产物：

- research artifact：`tokyo_pre_cross_market_sharpening/run_20260812_v1/pre_cross_research`
- frozen spec：`frozen_candidate_spec.json`
- 8/12 首次离线 forward：3 个 exact expression、0 pre-cross candidate、0 signal；未使用 settlement，notional=0。
