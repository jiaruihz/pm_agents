# Korea stacked trade lifecycle casebook v1

## 结论先行

这次最值得优化的第一项不是再加天气特征，而是修正交易表达证据：
holdout `174` 个 state 中 YES ask+depth 可执行 `174` 行，NO ask+depth
只有 `1` 行，双边同时可执行也只有 `1` 行。模型在 `85` 行给出理论正 NO edge，
但没有 NO depth；10 笔回放中有 `9` 笔在最终买入前
经历过这种“想买 NO、只有价格没有深度”的状态。当前 9 YES/1 NO 的买入顺序因此
主要反映 archive coverage，不是已验证的模型 side preference。

第二项是 position-aware full-ladder：当前模型只估计“当时市场 favorite bracket”
的 exact YES 概率；首个正 edge 买入后只 HOLD，不再估持有 bracket、止损、换档或
退出。Busan 07-22 买 33 YES 后，favorite 后来切到 34，最终也结算 34，但模型没有
产生“持有的 33 YES 还值多少”的连续概率。全量 10 笔中有
`5` 笔入场后 favorite 改变，结果 `2`
胜 `3` 负、PnL
`$-6.4364`；favorite 未改变的
`5` 笔全胜、PnL `$+6.9335`。
这是 post-hoc 诊断，不是换档止损 gate，但明确说明 held-bracket telemetry 是缺口。

## 数据与口径

- window：`2026-07-21..2026-07-28`。
- grain：`174` PIT states / `8`
  target dates / `15` city-days。
- model：`market_offset_routine_short`。
- policy：每个 city-day 第一个 fee-adjusted executable edge `>0`，
  最多 5 shares，之后 hold-to-settlement，不加仓、不退出。
- trade_class：`research_replay`；actual fills=`0`。市场快照与 settlement 有证据，
  但所有 BUY/PNL 均是假设回放。
- case selection：全量最差、Busan winner PnL 中位数、最早 Seoul、最小正 edge；
  同时列出全部 10 笔，不以案例代替总分母。
- manifest：canonical DB route healthy；production manifest overall warning，
  无 critical。本报告只使用已冻结 JRS research artifact，未重建 DB。

## 全部 10 笔回放

| date | city | KST | replay BUY | model P(win) | ask | edge | final bracket | result | fee PnL | 入场前 NO 正 edge但缺 depth | 入场后 favorite 改变 |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-07-21 | Busan | 10:45 | 32 YES | 51.8% | 0.480 | 2.6% | 32 | W | $+2.5376 | 2 | N |
| 2026-07-22 | Busan | 12:46 | 33 YES | 83.5% | 0.790 | 3.6% | 34 | L | $-3.9915 | 3 | Y |
| 2026-07-23 | Busan | 11:28 | 33 YES | 56.1% | 0.540 | 0.8% | 34 | L | $-2.7621 | 4 | Y |
| 2026-07-24 | Busan | 11:33 | 35 YES | 46.0% | 0.390 | 5.8% | 35 | W | $+2.9905 | 4 | N |
| 2026-07-24 | Seoul | 14:06 | 31 YES | 78.7% | 0.730 | 4.7% | 31 | W | $+1.3008 | 6 | N |
| 2026-07-25 | Busan | 15:57 | 35 YES | 99.6% | 0.980 | 1.5% | 35 | W | $+0.0951 | 6 | N |
| 2026-07-26 | Busan | 10:58 | 36 YES | 56.1% | 0.530 | 1.8% | 37 | L | $-2.7123 | 3 | Y |
| 2026-07-26 | Seoul | 14:59 | 31 YES | 97.0% | 0.960 | 0.8% | 31 | W | $+0.1904 | 5 | Y |
| 2026-07-27 | Busan | 11:17 | 35 YES | 55.7% | 0.420 | 12.5% | 35 | W | $+2.8391 | 3 | Y |
| 2026-07-28 | Busan | 09:08 | 34 NO | 99.9% | 0.998 | 0.1% | 35 | W | $+0.0095 | 0 | N |

其中 edge `<2c` 有 `5` 笔，`3` 胜，
fee-adjusted PnL `$-5.1794`、ROI `-25.67%`。
这是看过 holdout 后的执行脆弱性诊断，不注册成新 threshold。

## 所选案例的时间段摘要

天气路径依次为 `source temp / source running max / routine running max`；
market/model 均是当时 favorite exact-bracket YES。

| case | 时段 | KST | n | 天气路径 °C | favorite | market YES mid | model P(YES) | policy |
|---|---|---|---:|---|---|---|---|---|
| C2_busan_median_win | 09-12 | 09:09–11:50 | 6 | 30.3→32.0 / 30.3→32.0 / 30.0→32.0 | 32 | 0.475→0.515 | 0.219→0.650 | WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING → WAIT_NO_POSITIVE_EXECUTABLE_EDGE → REPLAY_BUY_YES → HOLD_NO_ADD_NO_EXIT_RULE |
| C2_busan_median_win | 12-14 | 12:21–13:58 | 4 | 29.6→30.8 / 32.0→32.0 / 32.0→32.0 | 32 | 0.845→0.979 | 0.910→0.986 | HOLD_NO_ADD_NO_EXIT_RULE |
| C2_busan_median_win | 14-16 | 14:30–15:36 | 3 | 29.5→31.5 / 32.0→32.0 / 32.0→32.0 | 32 | 0.986→0.993 | 0.992→0.997 | HOLD_NO_ADD_NO_EXIT_RULE |
| C2_busan_median_win | 16+ | 16:09–16:43 | 2 | 31.5→31.7 / 32.0→32.0 / 32.0→32.0 | 32 | 0.972→0.985 | 0.992→0.996 | HOLD_NO_ADD_NO_EXIT_RULE |
| C1_worst_loss | 09-12 | 09:30–11:41 | 5 | 31.5→32.6 / 31.5→33.2 / 31.0→33.0 | 33→34 | 0.415→0.470 | 0.200→0.473 | WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING → WAIT_NO_POSITIVE_EXECUTABLE_EDGE |
| C1_worst_loss | 12-14 | 12:13–13:50 | 4 | 31.3→32.5 / 33.2→33.2 / 33.0→33.0 | 33 | 0.520→0.905 | 0.645→0.952 | WAIT_NO_POSITIVE_EXECUTABLE_EDGE → REPLAY_BUY_YES → HOLD_NO_ADD_NO_EXIT_RULE |
| C1_worst_loss | 14-16 | 14:23–15:30 | 3 | 32.7→33.8 / 34.1→34.1 / 33.0→34.0 | 33→34 | 0.480→0.982 | 0.846→0.997 | HOLD_NO_ADD_NO_EXIT_RULE |
| C1_worst_loss | 16+ | 16:03–16:37 | 2 | 32.2→32.3 / 34.1→34.1 / 34.0→34.0 | 34 | 0.990→0.997 | 0.998→0.999 | HOLD_NO_ADD_NO_EXIT_RULE |
| C4_minimum_positive_edge | 09-12 | 09:08–09:08 | 1 | 31.6→31.6 / 31.8→31.8 / 32.0→32.0 | 34 | 0.004→0.004 | 0.001→0.001 | REPLAY_BUY_NO |
| C3_seoul_earliest | 09-12 | 09:21–11:33 | 5 | 28.1→29.7 / 28.4→30.2 / 28.0→30.0 | 31 | 0.445→0.505 | 0.129→0.369 | WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING |
| C3_seoul_earliest | 12-14 | 12:05–12:38 | 2 | 29.9→30.2 / 30.3→30.5 / 30.0→30.0 | 31 | 0.485→0.595 | 0.359→0.583 | WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING → WAIT_NO_POSITIVE_EXECUTABLE_EDGE |
| C3_seoul_earliest | 14-16 | 14:06–15:48 | 4 | 30.1→30.9 / 31.0→31.1 / 31.0→31.0 | 31 | 0.715→0.970 | 0.787→0.985 | REPLAY_BUY_YES → HOLD_NO_ADD_NO_EXIT_RULE |
| C3_seoul_earliest | 16+ | 16:22–16:58 | 2 | 30.2→30.2 / 31.1→31.1 / 31.0→31.0 | 31 | 0.975→0.985 | 0.985→0.991 | HOLD_NO_ADD_NO_EXIT_RULE |

## 逐时点复盘

盘口格式为 favorite YES `bid/mid/ask`；NO 只在 depth 存在时才算可执行。
模型的“理论 edge”允许只有价格，“可执行 edge”同时要求 ask 与 depth。

### C1_worst_loss：Busan 2026-07-22

入场：`12:46`，模型胜率 `83.5%`，ask `0.790`，fee 后 edge `3.6%`，`5.00` shares。最终 winning bracket `34`；回放买 `33 YES`，输，fee-adjusted PnL `$-3.9915`。

| KST | 天气路径（source/max/routine） | 当前 favorite 与盘口 | 模型 | 策略动作 |
|---|---|---|---|---|
| 09:30 | 31.5/31.5/31.0°C；30m slope 0.8 | 33 YES 0.410/0.415/0.420；NO ask 0.590；Y深度 5.0 / N深度 NA | P(YES)=20.0%；理论 NO 19.8%；可执行 YES -23.2% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 10:02 | 31.7/31.9/31.0°C；30m slope 0.6 | 34 YES 0.460/0.470/0.480；NO ask 0.540；Y深度 30.2 / N深度 NA | P(YES)=25.5%；理论 NO 19.2%；可执行 YES -23.7% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 10:35 | 32.5/32.8/32.0°C；30m slope 1.4 | 34 YES 0.420/0.440/0.460；NO ask 0.580；Y深度 8.0 / N深度 NA | P(YES)=39.2%；理论 NO 1.6%；可执行 YES -8.1% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 11:08 | 32.6/32.8/33.0°C；30m slope 0.2 | 34 YES 0.420/0.460/0.500；NO ask 0.580；Y深度 3.0 / N深度 NA | P(YES)=47.3%；理论 YES -4.0%；可执行 YES -4.0% | `WAIT_NO_POSITIVE_EXECUTABLE_EDGE` |
| 11:41 | 32.6/33.2/33.0°C；30m slope 0.0 | 34 YES 0.390/0.445/0.500；NO ask 0.610；Y深度 3.0 / N深度 NA | P(YES)=44.0%；理论 NO -6.2%；可执行 YES -7.2% | `WAIT_NO_POSITIVE_EXECUTABLE_EDGE` |
| 12:13 | 32.3/33.2/33.0°C；30m slope -1.6 | 33 YES 0.320/0.520/0.720；NO ask 0.680；Y深度 9.1 / N深度 NA | P(YES)=64.5%；理论 YES -8.5%；可执行 YES -8.5% | `WAIT_NO_POSITIVE_EXECUTABLE_EDGE` |
| 12:46 | 31.8/33.2/33.0°C；30m slope -1.6 | 33 YES 0.670/0.730/0.790；NO ask 0.330；Y深度 36.0 / N深度 NA | P(YES)=83.5%；理论 YES 3.6%；可执行 YES 3.6% | `REPLAY_BUY_YES` |
| 13:18 | 31.3/33.2/33.0°C；30m slope -0.8 | 33 YES 0.880/0.905/0.930；NO ask 0.120；Y深度 18.1 / N深度 NA | P(YES)=94.4%；理论 YES 1.1%；可执行 YES 1.1% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 13:50 | 32.5/33.2/33.0°C；30m slope 2.0 | 33 YES 0.880/0.900/0.920；NO ask 0.120；Y深度 32.0 / N深度 NA | P(YES)=95.2%；理论 YES 2.8%；可执行 YES 2.8% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 14:23 | 33.8/34.1/33.0°C；30m slope 2.4 | 33 YES 0.400/0.480/0.560；NO ask 0.600；Y深度 5.5 / N深度 NA | P(YES)=86.6%；理论 YES 29.4%；可执行 YES 29.4% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 14:56 | 33.7/34.1/33.0°C；30m slope -0.2 | 34 YES 0.420/0.580/0.740；NO ask 0.580；Y深度 22.0 / N深度 NA | P(YES)=84.6%；理论 YES 9.6%；可执行 YES 9.6% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 15:30 | 32.7/34.1/34.0°C；30m slope -1.6 | 34 YES 0.968/0.982/0.996；NO ask 0.032；Y深度 59.5 / N深度 NA | P(YES)=99.7%；理论 YES 0.1%；可执行 YES 0.1% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 16:03 | 32.3/34.1/34.0°C；30m slope -0.4 | 34 YES 0.989/0.990/0.990；NO ask 0.011；Y深度 5.0 / N深度 NA | P(YES)=99.8%；理论 YES 0.7%；可执行 YES 0.7% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 16:37 | 32.2/34.1/34.0°C；30m slope 0.2 | 34 YES 0.995/0.997/0.999；NO ask 0.005；Y深度 465.8 / N深度 NA | P(YES)=99.9%；理论 YES 0.0%；可执行 YES 0.0% | `HOLD_NO_ADD_NO_EXIT_RULE` |

### C2_busan_median_win：Busan 2026-07-21

入场：`10:45`，模型胜率 `51.8%`，ask `0.480`，fee 后 edge `2.6%`，`5.00` shares。最终 winning bracket `32`；回放买 `32 YES`，赢，fee-adjusted PnL `$+2.5376`。

| KST | 天气路径（source/max/routine） | 当前 favorite 与盘口 | 模型 | 策略动作 |
|---|---|---|---|---|
| 09:09 | 30.3/30.3/30.0°C；30m slope 2.2 | 32 YES 0.470/0.475/0.480；NO ask 0.530；Y深度 767.4 / N深度 NA | P(YES)=21.9%；理论 NO 23.8%；可执行 YES -27.3% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 09:41 | 30.5/30.5/30.0°C；30m slope 0.4 | 32 YES 0.470/0.475/0.480；NO ask 0.530；Y深度 467.4 / N深度 NA | P(YES)=30.6%；理论 NO 15.1%；可执行 YES -18.6% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 10:13 | 31.2/31.5/31.0°C；30m slope 1.4 | 32 YES 0.470/0.475/0.480；NO ask 0.530；Y深度 751.2 / N深度 NA | P(YES)=46.1%；理论 NO -0.3%；可执行 YES -3.2% | `WAIT_NO_POSITIVE_EXECUTABLE_EDGE` |
| 10:45 | 32.0/32.0/31.0°C；30m slope 1.0 | 32 YES 0.470/0.475/0.480；NO ask 0.530；Y深度 373.1 / N深度 NA | P(YES)=51.8%；理论 YES 2.6%；可执行 YES 2.6% | `REPLAY_BUY_YES` |
| 11:17 | 32.0/32.0/32.0°C；30m slope 0.8 | 32 YES 0.470/0.475/0.480；NO ask 0.530；Y深度 719.5 / N深度 NA | P(YES)=62.8%；理论 YES 13.5%；可执行 YES 13.5% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 11:50 | 31.1/32.0/32.0°C；30m slope -1.0 | 32 YES 0.510/0.515/0.520；NO ask 0.490；Y深度 521.4 / N深度 NA | P(YES)=65.0%；理论 YES 11.7%；可执行 YES 11.7% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 12:21 | 30.8/32.0/32.0°C；30m slope -0.2 | 32 YES 0.800/0.845/0.890；NO ask 0.200；Y深度 40.0 / N深度 NA | P(YES)=92.2%；理论 YES 2.7%；可执行 YES 2.7% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 12:53 | 30.6/32.0/32.0°C；30m slope 0.4 | 32 YES 0.820/0.845/0.870；NO ask 0.180；Y深度 8.0 / N深度 NA | P(YES)=91.0%；理论 YES 3.4%；可执行 YES 3.4% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 13:25 | 29.6/32.0/32.0°C；30m slope -1.7 | 32 YES 0.950/0.955/0.960；NO ask 0.050；Y深度 27.7 / N深度 NA | P(YES)=96.5%；理论 YES 0.3%；可执行 YES 0.3% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 13:58 | 30.1/32.0/32.0°C；30m slope 0.6 | 32 YES 0.970/0.979/0.988；NO ask 0.030；Y深度 373.4 / N深度 NA | P(YES)=98.6%；理论 YES -0.3%；可执行 YES -0.3% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 14:30 | 29.5/32.0/32.0°C；30m slope -1.2 | 32 YES 0.980/0.986/0.992；NO ask 0.020；Y深度 18.2 / N深度 NA | P(YES)=99.2%；理论 YES -0.1%；可执行 YES -0.1% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 15:03 | 30.3/32.0/32.0°C；30m slope 1.5 | 32 YES 0.992/0.993/0.994；NO ask 0.008；Y深度 74.5 / N深度 NA | P(YES)=99.6%；理论 YES 0.1%；可执行 YES 0.1% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 15:36 | 31.5/32.0/32.0°C；30m slope 2.1 | 32 YES 0.991/0.993/0.995；NO ask 0.009；Y深度 0.0 / N深度 NA | P(YES)=99.7%；理论 YES 0.2%；可执行 YES 0.2% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 16:09 | 31.7/32.0/32.0°C；30m slope 0.6 | 32 YES 0.954/0.972/0.990；NO ask 0.046；Y深度 458.7 / N深度 NA | P(YES)=99.2%；理论 YES 0.2%；可执行 YES 0.2% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 16:43 | 31.5/32.0/32.0°C；30m slope 0.4 | 32 YES 0.980/0.985/0.990；NO ask 0.020；Y深度 458.7 / N深度 NA | P(YES)=99.6%；理论 YES 0.6%；可执行 YES 0.6% | `HOLD_NO_ADD_NO_EXIT_RULE` |

### C3_seoul_earliest：Seoul 2026-07-24

入场：`14:06`，模型胜率 `78.7%`，ask `0.730`，fee 后 edge `4.7%`，`5.00` shares。最终 winning bracket `31`；回放买 `31 YES`，赢，fee-adjusted PnL `$+1.3008`。

| KST | 天气路径（source/max/routine） | 当前 favorite 与盘口 | 模型 | 策略动作 |
|---|---|---|---|---|
| 09:21 | 28.1/28.4/28.0°C；30m slope 0.2 | 31 YES 0.420/0.445/0.470；NO ask 0.580；Y深度 13.0 / N深度 NA | P(YES)=12.9%；理论 NO 27.8%；可执行 YES -35.3% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 09:54 | 28.8/29.2/28.0°C；30m slope 1.0 | 31 YES 0.500/0.505/0.510；NO ask 0.500；Y深度 29.2 / N深度 NA | P(YES)=18.0%；理论 NO 30.8%；可执行 YES -34.3% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 10:27 | 29.6/29.6/29.0°C；30m slope 1.8 | 31 YES 0.450/0.460/0.470；NO ask 0.550；Y深度 6.5 / N深度 NA | P(YES)=24.5%；理论 NO 19.3%；可执行 YES -23.7% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 10:59 | 29.3/30.2/30.0°C；30m slope -0.8 | 31 YES 0.480/0.485/0.490；NO ask 0.520；Y深度 10.0 / N深度 NA | P(YES)=34.8%；理论 NO 12.0%；可执行 YES -15.5% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 11:33 | 29.7/30.2/30.0°C；30m slope 0.8 | 31 YES 0.470/0.480/0.490；NO ask 0.530；Y深度 18.0 / N深度 NA | P(YES)=36.9%；理论 NO 8.9%；可执行 YES -13.4% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 12:05 | 29.9/30.3/30.0°C；30m slope 0.0 | 31 YES 0.470/0.485/0.500；NO ask 0.530；Y深度 71.3 / N深度 NA | P(YES)=35.9%；理论 NO 9.9%；可执行 YES -15.4% | `WAIT_NO_PRICE_VISIBLE_DEPTH_MISSING` |
| 12:38 | 30.2/30.5/30.0°C；30m slope 0.4 | 31 YES 0.580/0.595/0.610；NO ask 0.420；Y深度 10.0 / N深度 NA | P(YES)=58.3%；理论 NO -1.6%；可执行 YES -3.9% | `WAIT_NO_POSITIVE_EXECUTABLE_EDGE` |
| 14:06 | 30.9/31.0/31.0°C；30m slope 1.2 | 31 YES 0.700/0.715/0.730；NO ask 0.300；Y深度 10.0 / N深度 NA | P(YES)=78.7%；理论 YES 4.7%；可执行 YES 4.7% | `REPLAY_BUY_YES` |
| 14:40 | 30.1/31.1/31.0°C；30m slope -2.0 | 31 YES 0.930/0.945/0.960；NO ask 0.070；Y深度 185.6 / N深度 NA | P(YES)=96.7%；理论 YES 0.5%；可执行 YES 0.5% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 15:14 | 30.2/31.1/31.0°C；30m slope -0.8 | 31 YES 0.960/0.970/0.980；NO ask 0.040；Y深度 83.0 / N深度 NA | P(YES)=97.8%；理论 YES -0.3%；可执行 YES -0.3% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 15:48 | 30.1/31.1/31.0°C；30m slope 0.0 | 31 YES 0.960/0.970/0.980；NO ask 0.040；Y深度 69.0 / N深度 NA | P(YES)=98.5%；理论 YES 0.4%；可执行 YES 0.4% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 16:22 | 30.2/31.1/31.0°C；30m slope -0.2 | 31 YES 0.970/0.975/0.980；NO ask 0.030；Y深度 91.5 / N深度 NA | P(YES)=98.5%；理论 YES 0.4%；可执行 YES 0.4% | `HOLD_NO_ADD_NO_EXIT_RULE` |
| 16:58 | 30.2/31.1/31.0°C；30m slope -0.6 | 31 YES 0.980/0.985/0.990；NO ask 0.020；Y深度 17.7 / N深度 NA | P(YES)=99.1%；理论 YES 0.0%；可执行 YES 0.0% | `HOLD_NO_ADD_NO_EXIT_RULE` |

### C4_minimum_positive_edge：Busan 2026-07-28

入场：`09:08`，模型胜率 `99.9%`，ask `0.998`，fee 后 edge `0.1%`，`5.00` shares。最终 winning bracket `35`；回放买 `34 NO`，赢，fee-adjusted PnL `$+0.0095`。

| KST | 天气路径（source/max/routine） | 当前 favorite 与盘口 | 模型 | 策略动作 |
|---|---|---|---|---|
| 09:08 | 31.6/31.8/32.0°C；30m slope 0.2 | 34 YES 0.002/0.004/0.006；NO ask 0.998；Y深度 100.0 / N深度 1500.0 | P(YES)=0.1%；理论 NO 0.1%；可执行 NO 0.1% | `REPLAY_BUY_NO` |

## 从案例得到的优化顺序

1. **先补对称盘口证据**：每个 bracket 同时保存 YES/NO token 的 ask、bid、
   shares/depth、snapshot age；不能用 NO price 可见代替 NO 可成交，也不能让
   173/174 的单边 coverage 决定 side。
2. **输出固定 full-ladder probability**：每个时点对所有 bracket 保存
   `P(final exact X)`，而不是市场 favorite 改了以后 target 也跟着改。这样才能持续
   重估已持仓 token，并区分 hold、exit、switch、skip。
3. **把 entry 与 position management 分开**：现策略只有 first-entry/hold。
   下一版研究应在同一 full-ladder distribution 上回放 entry→5/15/30/60m
   markout、held-token bid/depth、exit fee 和 adverse selection；缺 exit depth
   时只报 indicative mark，不伪造成可成交退出。
4. **低 edge 只作摩擦敏感性**：`<2c` 的 5 笔本窗 ROI 为负，但这是 post-hoc
   观察；先加入 snapshot age、slippage 和双边 depth 后，在新 forward 预注册
   friction floor，不直接把 2c 写成 gate。
5. **不从时段案例加 hard filter**：早盘 flip 在 Busan 07-21 赢、07-22 输，
   单靠“等待/不等待”无法区分。需让模型显式学习 source→routine settlement basis、
   overshoot 与 held-bracket invalidation，再在固定同分母 forward 检验。

## 双漏斗与动作

Signal funnel：

`174 PIT states → 58 positive executable candidates → 10 first city-day entries`。

Evidence funnel：

`174 PIT weather+favorite-book states → 174 YES-executable →
1 NO-executable → 174 settled labels → 0 actual fills`。

8 环：覆盖描述性回放、概率、基准、部分 taker entry 微结构；缺真实 fills、对称
NO depth、exit depth、容量、position-aware full ladder 与新 forward。

`significance=FAIL baseline=FAIL forward=NA conclusion=inconclusive`。
动作：不改 live；优先补对称 full-ladder/held-token telemetry，再重做
position-aware router。原 stacked model proper score 仍显著输 raw market，
案例优化不能绕过这一点。
