# Seoul T-10 report-clock replay v1

Generated: `2026-07-23`
Status: `research/shadow_only`; no live authorization

## 结论

把 Seoul 从“routine METAR 前 20 分钟内立即执行”改成“最早等到 T-10，并在 T-10 用最新 AMOS 路径重新跑一次 v5（连续两次 >= X+0.5，最新一次 >= X+0.7）”，在当前可回放样本里会挡掉 7/16 的错误 `28 NO @0.64`。

按每笔固定 10 shares、首档 ask、同一 taker fee 口径：

- T-20/current execution-relevant cohort：`6` 笔已结算，`5/6` 正确，cost `$53.6077`，PnL `-$3.6077`，ROI `-6.73%`。
- T-10 revalidation：`3` 笔可执行，其中 `2` 笔 canonical settled、`1` 笔（7/22）WU/RKSI weather-confirmed；`3/3` 方向正确，cost `$28.5036`，PnL `+$1.4964`，ROI `+5.25%`。只算 canonical settled 的前两笔为 cost `$18.8177`、PnL `+$1.1823`、ROI `+6.28%`。

这不是足够上线的统计证据：只有 7 个 execution-relevant city-day/bracket 节点，且改善主要由挡住单个 7/16 大亏贡献。推荐把 T-10 做成 Seoul 的 shadow policy；不要据此把 Seoul 升 live。

## 完整节点回放

时间均为 UTC；Seoul local = UTC+9，北京 = UTC+8。`X NO` 是当时 routine METAR running max 对应的 exact bracket NO。

| date / X NO | 首次有效 cross | T-10 重新判断 | 下一份 routine METAR | 之后结果 | 盘口：首次 → T-10 → report 后 | T-10 动作 |
|---|---|---|---|---|---|---|
| 7/14 / 29 | T-1.5 左右；AMOS `29.7`，路径仍强 | 已在 T-10 内，仍为 `29.7` | `30`，立即跨档 | final/WU `32`，29 NO 赢 | `.96 x42.8` → 同一档 → `.97 x319.18` | 买 10；fee 后 `+$0.3808` |
| 7/15 / 26 | T-13.6；`26.8` | T-9.7 为 `27.0`，温度条件仍成立 | `26`，这一份**没变**；04:30 才到 `27` | final/WU `28`，26 NO 赢 | `.955 x82.42` → `.985 x50` → `.945 x13.66` | 不买：T-10 ask 已超过 `.97`；漏掉一笔最终正确单 |
| 7/16 / 28 | T-15.47；连续两次越线，最新 `28.9` | T-10 最新约 `28.2/28.0`，低于 strong `28.7`，连续 qualifying count 归零 | `28`，**没变** | final/WU `28`，28 NO 输 | `.64 x163.30` → `.64 x44.14` → `.44 x6.12` | 不买：由温度 revalidation 挡住；这是核心收益来源 |
| 7/17 / 29 | T-20；`29.7`（peak `29.8`） | T-10 升至 `30.3`，温度条件更强 | `31`，直接跨两档 | final/WU `31`，29 NO 赢 | `.94 x95.77` → `.97 x0.33`（约 1 分钟后 `.98`）→ `.99 x28.07` | 不买：`.97` 可见量不足 10，随后价格超过上限 |
| 7/18 / 25 | T-8.36；`25.7` | 已在 T-10 内，条件成立 | `26`，跨档 | final/WU `26`，25 NO 赢 | `.916 x22.94` → 同一档 → `.998 x8` | 买 10；fee 后 `+$0.8015` |
| 7/20 / 24 | T-19.43；`24.7` | T-9.4 为 `24.9`，条件成立 | `25`，跨档 | final/WU `27`，24 NO 赢 | `.924 x10` → `.975 x22.65` → `.997 x8` | 不买：等待期间 ask 上涨 5.1c，超过 `.97` |
| 7/22 / 26 | T-13.41；约 `27.0` | T-9.x 仍强 | `27`，跨档 | WU native max `27`；weather-confirmed 26 NO 赢，canonical settlement 当时未入表 | `.946 x3.51` → `.967 x30` → `.997 x77.56` | 买 10；原时点深度不足，T-10 反而等到可执行量；fee 后约 `+$0.3140` |

## 这张表说明什么

1. T-10 不是统一改善价格。正确事件通常被市场继续买走：7/17、7/20 分别从 `.94/.924` 涨到 `.97/.975`，7/15 从 `.955` 涨到 `.985`。等待会丢掉一部分便宜筹码。
2. T-10 真正过滤的是“短促 terminal overshoot”。7/16 AMOS 从 `28.9` 很快回到 `28.2/28.0`，下一份 METAR 仍是 `28`，最终也停在 `28`。盘口在 T-10 仍是 `.64`，若只按价格或深度判断照样会买错。
3. 下一份 METAR 不变不等于 final NO 一定错。7/15 下一份仍为 `26`，但半小时后到 `27`、最终到 `28`；所以 T-10 应重判 AMOS path，不应要求“上一份/下一份 METAR 已经跨档”。
4. T-10 也可能改善深度。7/22 首次只有 `3.51` shares，T-10 时变为 `30` shares，但为此多付 2.1c。

## 数据口径与已修正缺口

- signal denominator：现有 `persistent_candidate_margin_v5` first signal，以及 7/14--7/15 AMOS timing archive；同一 city-day/bracket 不用重复 polling 扩样本。
- revalidation：到 scheduled routine report 的 T-10 才允许执行；若 signal 本来就在 T-10 内则立即判断。使用当时 first-seen 的 preferred-runway AMOS observation，重新计算连续 `+0.5/+0.7` 条件。
- evidence：`high_frequency_observations` raw、runner `events.jsonl`、direct-book `quote_snapshots.jsonl`、routine METAR、WU native-C 和 canonical winning bracket。
- execution：首个可用 direct NO ask，`ask<=0.97`、top size `>=10`，固定 10 shares；fee=`0.05*p*(1-p)`。
- 初版 timing table 曾漏掉 7/16 04:44Z：它用“下一份实际 first-seen routine METAR”构造 episode，而该段 METAR first-seen 链有缺口，导致 raw AMOS 04:40--05:23 没进入 episode。最终结果改为以 runner 已记录的 scheduled clock 和 first signal 为锚，再回读 raw AMOS/book；因此 7/16 坏单已经回到分母，不能引用漏单版的 `2/2` 作为 T-10 结论。

## 建议动作

Seoul 保持 shadow，把 strict pre-report clock 修正后并行记录 `T-20 current` 与 `T-10 revalidate`。T-10 policy 本身定义为“执行窗口 + 最新路径重判”，不是简单 `minutes_to_report<=10` gate。至少再累计 20--30 个 settled execution-relevant 节点，并单列 terminal false-cross 与正确信号涨价/深度流失，才重新讨论 live。

Contract: signal funnel = first v5 city-day/bracket signal → T-10 path revalidation; evidence funnel = raw AMOS → scheduled routine METAR → direct book → WU/canonical settlement; baseline = current T-20 first executable; forward = shadow required; conclusion = promising filter, insufficient evidence, no live authorization.
