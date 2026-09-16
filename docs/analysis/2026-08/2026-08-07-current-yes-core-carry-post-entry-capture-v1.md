# Core Carry post-entry carry-capture v1

## 数据快照

- 数据源：Core raw `pre_live_scores/state_decisions`、PIT full-ladder orderbook、canonical `settlement_outcomes`。
- 冻结 DB build：`2026-08-07T08:42:37.856550+00:00`；fact_trades `4948` rows。
- 冻结 signal entries：`47`；canonical settled `33`；missing/open `14`。
- 冻结 executable replay：`29` entries / `10` target dates。
- 本报告是 signal-policy replay，不是 actual live realized PnL；unsettled 不计入收益。
- 2026-08-08 follow-up 时旧 hot orderbook 已迁移到 archive；缩水后的 18-row replay 不替换上述冻结分母。新增 confirmation 诊断只使用 Core raw 中同刻双边 top book 且 ask depth≥10 的 11-row 子集。

## 结论与策略动作

**保留 Core 入场与 fixed-10 不动；`post-entry carry capture` 不部署，代码保留为 dormant research runner。**

执行语义：每份新官方报文到达后，重新读取原 YES token；若完整可卖 bid ladder 扣官方 exit fee 后，已比入场成本多锁定至少 `3c/share`，则全平；否则继续持有。没有 book 或深度不足只记 coverage gap，不触发 fallback。

在冻结可执行同分母上，10 股 hold PnL `$+10.75`，candidate `$+16.37`，delta `$+5.62`；target-date bootstrap CI `[-6.42, +25.41]`。
触发 `21` 笔，其中 final loss `1`、winner `20`；saved loss capital `$8.93`，sacrificed winner profit `$3.31`。剔除 Lucknow 后策略只剩 `$-3.31`，因此不是可推广的止盈 alpha。

这个点估改善主要来自 Lucknow：它在跨到 33 之前出现过足够深度的 90c 附近 bid，+3c carry capture 可以把最终全损改成小幅已实现盈利。它救不了所有错误：Singapore、Chengdu 和 Manila 在跨档前没有达到相同的 fee-adjusted 盈利退出条件。

## 阈值敏感性（同一已看过窗口）

| net gain floor/share | exits | candidate PnL | delta vs hold |
|---:|---:|---:|---:|
| 0c | 28 | $+11.27 | $+0.52 |
| 1c | 27 | $+11.56 | $+0.81 |
| 2c | 23 | $+15.92 | $+5.17 |
| 3c | 21 | $+16.37 | $+5.62 |
| 5c | 16 | $+8.76 | $-1.99 |
| 8c | 9 | $+9.76 | $-0.99 |
| 10c | 6 | $+10.19 | $-0.56 |

`3c` 是本窗口诊断后选出的 shadow operating point，不是 clean forward 结果；因此不能据此上线。

## 持仓后 model-edge 反转

更原则化的候选是每份新报文后用冻结 Core v2 重新算 held-bracket probability，并仅在完整 10-share sell ladder 的净回收高于该概率时退出。该比较使用同一份执行盘口重算 market-anchored probability，不使用固定盈利目标。

| sell net − refreshed model p | exits | final winners | final losses | candidate PnL | delta vs hold |
|---:|---:|---:|---:|---:|---:|
| 0c | 0 | 0 | 0 | unchanged | $+0.00 |
| 1c | 0 | 0 | 0 | unchanged | $+0.00 |
| 2c | 0 | 0 | 0 | unchanged | $+0.00 |
| 3c | 0 | 0 | 0 | unchanged | $+0.00 |

同一执行盘口可重算的 `18` entries / `9` dates 中结果为 `0` 次退出：冻结 Core 的 market-offset 概率始终高于 fee-adjusted sell bid。因此同一个 Core 不能同时充当 entry scorer 和独立 stop model；它会随 market prior 一起移动。这是结构性限制，不是再调一个退出阈值能解决的。

## 首份新报文确认后再入场

反事实策略：原 Core 首次正 EV 只建立 pending intent；等下一份官方报文。若 held bracket 已改变则取消；若未改变，则用新状态和完整 10-share ask ladder 重算 frozen Core，只有 post-report taker EV 仍为正才买入。

严格使用新报文同刻双边 top book 且 ask depth≥10，可评估 `11` 笔 / `7` dates；实际重新入场 `6` 笔（winner `5` / loss `1`），跳过 `5` 笔（winner `4` / loss `1`）。hold baseline `$-10.36`，confirmation candidate `$-6.47`，delta `$+3.89`，date-block CI `[-4.40, +18.70]`。它避开 Chengdu，但仍重新买入 Lucknow并因价格更高多亏 `$0.58`；同时牺牲/跳过 winner profit `$4.83`。因此也不改 entry。

## 失败机制与模型含义

| case | entry→first-known cross | entry forecast state | 可修层 |
|---|---:|---|---|
| Manila 28 | 26m | forecast 仍有 +0.7°C room | entry hazard / sizing；退出来不及 |
| Chengdu 29 | 43m | forecast 仍有 +1.0°C room，second peak 未表达 | entry hazard；one-report confirmation 可避开但代价过大 |
| Singapore 31 | 58m | running 已高于 forecast max 0.8°C | forecast 已失准，不应把负 margin 当“安全 cap” |
| Lucknow 32 | 165m | running 已高于 forecast max 1.1°C；peak 已过约 5.8h | 晚尾部；可退出但事前状态仍显示 plateau/heating-done |

现有 transition challenger 只把“forecast 足以越下一档”的正 margin 当风险；负 margin 会进入 plateau/safe 语义。follow-up 增加连续 `forecast_underprediction_ticks` 后，在固定历史 forward 138 state entries / 8 dates 上仍未改善：vs Core Brier delta `-0.000154` CI `[-0.001150,+0.000896]`，logloss delta `+0.000151` CI `[-0.004040,+0.004949]`。所以语义修正是对的，但单加这一列没有可交易增量。

真正需要的不是第三个退出阈值，而是把模型拆成两头：保留当前 market-offset Core 做 entry residual；另训一个**不含 market price**、按每份新报文更新的 upward-exit survival/hazard head，专门输出剩余 overshoot 风险，供 entry sizing 与持仓风险使用。该 head 尚未通过验证，不能先写进 live。

## 已跨档案例

| city | target_date | held | capture before cross | capture report |
|---|---|---:|---|---|
| Singapore | 2026-07-27 | 31 | False |  |
| Chengdu | 2026-07-27 | 29 | False |  |
| Lucknow | 2026-07-31 | 32 | True | 2026-07-31T10:00:00Z |
| Manila | 2026-08-07 | 28 | False |  |

## 证据门与下一步

- significance=`FAIL`；baseline=`PASS`；forward=`FAIL`；conclusion=`inconclusive`。
- 新天气字段不作为 entry hard gate：此前同分母 forward 没有增量。它们在这里负责定义“新报文/新状态”的 event clock；真正退出动作由可成交净收益决定。
- 固定 +3c exit 与 one-report delay 均不部署；runner 保留但不登记生产。
- 下一研究动作只保留 market-free event-time survival/hazard head；不继续给 Core market-offset 线性 residual 叠单字段或 case gate。
- 2026-08-08 复核时 production health 仍为 `CRITICAL`：Core checkout root mismatch，observation cache not ok；本研究未重启或修改生产。

## 8 环

描述性=PASS；统计推断=PASS；信号判别=NA；概率分布=NA；执行微结构=PASS（archived full ladder）；容量=PASS 到 10 shares；组合=PASS（target-date block）；基准/反事实=PASS；frozen forward=FAIL。
