# 0x43cb 低频 bounded-range strip 全方位体检 v1

## 结论

`0x43cb4ae1f4ddc9e671486c79c9f40a6fd98b84df` 的长期盈利和策略形态都是真实、稳定的，且不依赖抢 METAR 第一跳；但它也不是可以手工跟单的静态套利。它的天气判断、完整 ladder 组合、maker-assisted 多腿累积共同产生收益。

对没有实时数据和网络优势的账户，值得研究的版本是：

> target day 固定 1–2 个当地时点，用 PIT 终值分布选择连续 support strip，先只做等 shares common base；按同刻真实 ask、官方 fee、滑点和深度验算 `P(winner in strip) - effective basket cost`，不复制钱包成交、不做 modal overweight。

当前判定：

- 钱包描述性盈利：`PASS`
- 机制与近期形态稳定性：`PASS`
- 对低延迟数据的依赖：`LOW`
- 对机器人执行的依赖：`MEDIUM/HIGH`
- 我们自己的同分母 probability residual：`NOT TESTED`
- taker-only 低频可执行 alpha：`NOT TESTED`
- 复制 live gate：`FAIL`
- 推荐动作：固定时点、zero-notional frozen-forward shadow

## 数据与系统健康

- 新采集快照：`runtime/analysis_snapshots/wallet43cb_20260804`
- 覆盖：2025-10-28 至 2026-08-04 15:16 UTC
- public weather activity：218,947 rows
- 完整 grain：3,261 个 `city × target_date` portfolio、120 个 target dates、50 个城市
- Gamma metadata：3,260/3,261 完整；唯一缺口为 Austin 2026-07-09
- canonical DB route：healthy；repo compatibility path 与 JRS physical canonical 为同一 device/inode
- production health 有与本研究无关的 critical：dashboard API timeout、两个 health artifact stale、data-feed health command timeout。钱包复盘直接使用新 public snapshot，不把这些告警解释成 0x43cb 数据结论。

## 它实际在做什么

经济表达不是“同时猜多个 exact bracket”，而是：

```text
约 55% shares：连续区间 common base
+ 约 45% shares：中心/上侧 modal overweight
→ winner 在区间内至少兑付 common shares
→ 主要持有到 settlement
```

全历史结构：

| 指标 | 最新结果 |
|---|---:|
| YES strip | 3,178 / 3,261 |
| 连续 strip share | 98.56% |
| strip 宽度中位数 | 6 档 |
| winner 落在 positive strip 内 | 97.05% |
| common-base fraction 中位数 | 55.56% |
| modal-overweight fraction 中位数 | 44.44% |
| target-day BUY cost | 99.98% |
| cost-weighted fill hour 中位 | 14:06 local |
| first→last BUY 中位 | 164 分钟 |
| BUY transaction 中位 | 32 |
| 无主动 SELL、进入结算 | 88.26% |

近期已经从 3–4 月的“早、宽、重中心、长累积”演化为 6–8 月的“午后、约 5 档、common base 更高、批次更少”。8 月前四天中位 5 档、16 transactions、126 分钟，仍不是一次点击成交。

## 盈利健康

主口径只用 cashflow-complete resolved portfolios：

| 指标 | 2026-07-29 快照 | 2026-08-04 快照 |
|---|---:|---:|
| events | 2,966 | 3,220 |
| BUY cost | $544,942.37 | $572,326.80 |
| public cashflow PnL | +$26,443.56 | +$27,580.54 |
| turnover ROI | +4.85% | +4.82% |
| target-date block 95% CI | +4.07%～+5.64% | +4.08%～+5.61% |
| positive target-date share | 85.84% | 86.55% |

结论不是由最后几天或单一城市撑起。更新 6 个 target dates 后，点估和置信区间几乎不变。

但近期收益率低于早期：3 月约 6.73%、4 月约 5.51%、6 月约 2.33%、7 月约 3.31%；8 月约 3.38% 仍含大量未结算 portfolio，只能视为 provisional。方向未失效，但不能按全历史 4.82% 估计新资金回报。

Polymarket WEATHER leaderboard 在 2026-08-04 15:19 UTC 仍显示 all/month/week/day 均为正，all rank 43；leaderboard volume 与本报告 BUY cost 分母不同，不混算 ROI。

## 最大风险来自哪里

历史 660 个亏损 events、gross loss $5,714.31 的既有完整诊断显示：

| 亏损形态 | 占 gross loss |
|---|---:|
| winner 在 strip 内，但 modal overweight 压错档 | 88.08% |
| winner 在整个区间外 | 5.48% |
| strip 内缺腿/internal hole | 3.89% |
| modal hit 但组合仍买贵 | 2.55% |

所以低频复刻的第一刀应是移除 modal overweight，而不是盲目缩窄区间。完整性也很重要：历史 non-contiguous strip ROI 为 -6.29%，contiguous strip 为 +4.91%；缺一条腿会把一个 range payoff 变成方向赌注。

## 对速度和执行的依赖

### 不依赖低延迟的部分

- wallet 首单对应 PIT source age 中位约 33.8 分钟；`<=10m` 仅约 6.1%。
- 首买到末买中位 164 分钟，不是毫秒或秒级抢跑。
- 固定 target-day 午后 checkpoint、公开 METAR/forecast、完整 ladder 都可以低频研究。

### 依赖机器执行的部分

- 钱包典型 32 笔 BUY transactions、多个 session；秒级多腿成交是明确 bot 行为。
- 典型案例中可见 ask 合计常高于 1，但钱包通过中心腿 maker、便宜尾腿 taker、等待价格迁移，把最终 basket VWAP 压到 0.97–0.98。
- 最近 50 个样本只有 18/50 的实际成交 common basket cost `<1`，只占 17.39% BUY cost；纯静态 underround 不是全部 alpha。
- 看到它 public fill 后再跟，既晚于原始挂单，也拿不到相同多腿 VWAP。

描述性低频代理同样提示执行会侵蚀收益：全历史 `buy span <=60m` 的 selected fills ROI 约 3.0%，`>60m` 约 5.0%；`<=5` 笔 BUY 的组合约 0.9%，`31+` 笔约 5.3%。这些切片受时期、仓位和事件选择混杂，不能当因果，但足以否定“少点几次就自然得到同样收益”。

## 可复制版本

### Signal funnel

```text
全部完整 target-day ladders
→ 固定当地 13:00 / 15:00 checkpoint
→ settlement-native running max + PIT 终值分布
→ 最小连续 support strip
→ 模型 P_band 与同刻 market baseline 比较
→ fee-adjusted residual 为正才形成 candidate
```

### Evidence funnel

```text
candidate
→ 同刻全腿 ask 可见
→ 目标 shares 在每腿都有 depth
→ fee + 1 tick/腿 stress 后仍正 EV
→ settlement 完整
→ frozen-forward outcome
```

第一版执行合同：

1. 每个 city-day 最多两个 decision checkpoints，不按 wallet fill 触发。
2. 只买完整、连续、等 shares strip；不做中心加仓。
3. 研究主结果按 taker asks 计价；maker 只作为独立执行 A/B，不能补救负的 taker alpha。
4. 任一腿缺失则整篮子不记作“完整成交”；单独报告 legging exposure。
5. 主指标为 `P_band - effective basket cost`、fee-adjusted PnL/ROI、coverage、Brier/log loss 相对同分母 market baseline。
6. 按 target_date block bootstrap，并保留至少 15 个完全冻结的 forward dates。

## 最终判断

它适合我们研究，但不适合跟单，也不能现在照抄 live。我们能绕开的是实时源和网速优势；暂时绕不开的是：自己的终值概率必须打败同刻市场，以及多腿 taker 成本是否仍留有 edge。

最有希望的低频版本不是“钱包策略的手工版”，而是一个新策略：**固定时点、物理概率选 band、等 shares、taker-conservative、无 modal overlay**。如果这个版本在 frozen forward 仍为正，再研究慢 maker；如果 taker 版本为负，就说明 0x43cb 的可复制收益主要在执行和私有概率中，不适合我们的约束。

