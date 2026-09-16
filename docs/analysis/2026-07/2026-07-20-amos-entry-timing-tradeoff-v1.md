# AMOS previous-NO 入场时机：Busan / Seoul

结论：上午 Busan 的核心异常确实是 **source path 在下单后立刻反转**，但干净修复不是“只在 METAR 前 N 分钟买”。更合适的 shadow 候选是：source-only persistence 触发后等待 5 分钟，在**实际可成交 quote 时刻**重新检查 latest 仍在阈值上、路径 retained；报文时钟只作为概率特征。固定等到 T-5 会显著牺牲价格，固定限制 T-20 又会错过下午早期 re-cross 的便宜盘口。

## 数据快照

- 生成时间：2026-07-20 18:01 CST；研究扫描窗口 `2026-07-08..2026-07-20`，实际 Busan/Seoul target dates 为 `2026-07-09..2026-07-20`，各 12 天。
- 快源：`/Volumes/jrs/weather_data_feed_service_runtime/output/high_frequency_observations/*/high_frequency_observations.jsonl` 中 Busan RKPK / Seoul RKSI AMOS runway observations。
- routine METAR：同 runtime `output/source_events/*/sources.jsonl` 的 `aviationweather_metar` first-seen 链。
- 盘口：`output/fast_source_stale_book/quote_snapshots.jsonl`，共 4,341 行；执行口径为 previous exact bracket NO `ask<=0.97` 且 ask depth `>=10 shares`。
- 结算标签：`active_realtime_source_alignment_v1/daily_source_wu_settlement.csv`；截至 7/17 的 WU/canonical labels，加 Busan 7/20 weather-confirmed 32C provisional label。Seoul 7/18-20 尚无最终结算标签。
- 对齐后 source→next-METAR rows `21,240`；raw recurrent threshold episodes `402`，满足 source persistence（连续两份 distinct observations `>=X+0.5C` 且最新 `>=X+0.7C`）为 `95`。
- 产物：[summary.json](generated/amos_entry_timing_tradeoff_v1/summary.json) · [逐策略行](generated/amos_entry_timing_tradeoff_v1/delay_policy_rows.csv) · [策略汇总](generated/amos_entry_timing_tradeoff_v1/delay_policy_summary.csv) · [报文窗口行](generated/amos_entry_timing_tradeoff_v1/report_window_rows.csv) · [报文窗口汇总](generated/amos_entry_timing_tradeoff_v1/report_window_summary.csv)。

## 目标与口径

目标 metric 是：在同一 source episode 上，等待确认能否提高 `P(next routine METAR cross)`，以及价格/深度是否仍允许 10-share NO 表达。`P(final WU leaves prior bracket)` 单独报告，不能拿下午 reheating 后的最终胜利来证明上午 next-METAR trigger 正确。

当前 runner v5 的 source 条件外，还有 `距下一份 METAR <=20m`。本研究显式拆开：

- `current_v5_immediate_le20m`：复现当前规则，source persistence 后且 T<=20m 立即看第一份 quote。
- `wait5_retained`：source persistence 后等 5 分钟；在实际 quote 时刻要求 observations 中至少 80% 仍 `>=X+0.5C`、latest 仍在阈值上、peak drawdown `<=0.2C`，不强制 T<=20m。
- 同一 city-day / prior bracket / policy 只选第一份可执行表达；quote 缺失是 evidence gap，不作为策略过滤。

## 今天 Busan 的反事实

| local time | episode / state | 31 NO book | next METAR | 判断 |
|---|---|---:|---:|---|
| 12:49 | AMOS `31.9C`，当前 v5 在 T-10.6 触发 | replay ask `0.85 x 12.49` | 13:00 仍 31 | 立即买是 next-METAR false trigger |
| 12:51 | 等 2m，AMOS 一度 `32.0C` | ask `0.88 x 33` | 仍未跨 | 两分钟不够识别 reversal |
| 12:54-55 | 等 5m，latest `31.1C`，从 peak drawdown `0.9C` | policy reject | 仍未跨 | 5m revalidation 会挡掉实盘事故 |
| 15:10 | 下午新 episode source-confirmed，距 16:00 约 49m | ask `0.41 x 85.52` | 16:00 到 32 | 现有 T<=20m 不允许进入 |
| 15:15 | 等 5m 后 latest `31.5C`、drawdown `0.2C`、retained | ask `0.23 x 14.53` | 跨档 | 仍有很大价格空间 |
| 15:20 | 等 10m 后 latest `31.8C` | ask `0.31 x 22` | 跨档 | 仍可执行 |
| 15:50 | T-9 | ask `0.35 x 10` | 跨档 | 空间已缩小但尚有 |
| 15:55 | T-4 | ask `0.91 x 20.52` | 跨档 | 接近报文才买，edge 基本消失 |

所以上午是**错误的即时 source→next-METAR 判断**；最终 31 NO 获胜是下午独立 reheating episode 带来的，不能把它视为上午 entry thesis 的验证。反事实策略必须在上午拒绝后允许下午新的 independently-qualified re-cross，不能按 city-day 过早永久去重。

## Busan / Seoul 策略对比

以下是 observer-covered、10-share executable 后每 city-day/prior bracket 第一笔；样本很小，属于同样本探索：

| policy | Busan：selected / next确认 | Busan median ask | Seoul：selected / next确认 | Seoul median ask | 合计 final labels |
|---|---:|---:|---:|---:|---:|
| current v5，进入 T<=20m 后立即 | 2 / 1 | 0.865 | 5 / 4 | 0.940 | 5/5 final NO win |
| source 后等 2m，latest above | 5 / 3 | 0.880 | 3 / 2 | 0.930 | 6/6 final NO win |
| source 后等 5m，retained | 2 / 2 | 0.585 | 4 / 4 | 0.940 | 4/4 final NO win |
| source 后等 10m，retained | 2 / 1 | 0.605 | 0 / 0 | NA | 1/1 final NO win |

5m retained 在当前可执行样本把 next-METAR confirmation 从 current-v5 的 `5/7` 提到 `6/6`，但只有 6 个表达，且 quote coverage 是 observer-driven；这只能支持 shadow candidate，不能据此改 live hard gate。final labels 更少，而且 previous-NO 的最终胜率与 next-METAR correctness 不是同一目标。

同 episode 且首尾都可执行的 paired rows 只有 6 个；source-immediate→5m retained 的 ask 变化中位数为 `+0.043`。Seoul 4 个 paired rows 的中位数同为 `+0.043`；Busan 只有 2 个，分别 `0.41→0.23` 与 `0.35→0.91`，说明价格损耗主要取决于市场是否已经相信这次 cross，而不是机械的等待分钟数。

## 报文距离不是单调最优阈值

| window | Busan executable / next确认 / median ask | Seoul executable / next确认 / median ask |
|---|---:|---:|
| T-0..5m | 1 / 1 / 0.910 | 4 / 4 / 0.955 |
| T-5..10m | 2 / 1 / 0.575 | 3 / 3 / 0.911 |
| T-10..20m | 0 / 0 / NA | 3 / 2 / 0.954 |
| >T-20m | 3 / 2 / 0.680 | 0 / 0 / NA |

越贴近报文通常 confirmation 更高，但 ask 也接近 1；Busan 今天同时出现“很早但 false”和“很早且 true”，说明 clock 不能识别二者，path retention 才能。窗口样本不是随机盘口截面，不能用表中最高格反推 live 时间阈值。

## Funnel 与结论状态

- signal funnel：`21,240 aligned rows → 402 raw recurrent episodes → 95 source-persistent episodes → 67 current-v5 clock/path-pass / 56 wait5-retained path-pass`。
- evidence funnel：current-v5 `67 → 32 quote-covered → 9 executable → 7 first-selected`；wait5-retained `56 → 17 quote-covered → 8 executable → 6 first-selected`。coverage 缩小不是 eligibility improvement。
- baseline：same-time market ask；未证明 5m rule 的 calibrated probability 在同分母上打败 market。
- forward：尚未开始预注册 forward；显著性、独立日期数和 final-WU labels 均不足。
- conclusion：`inconclusive / research-shadow-only`。

建议的下一版 zero-notional shadow 状态机是：`source persistence → 等满5分钟 → 实际 quote 时刻 revalidate retained → 输出 P(next METAR cross) 与 P(final WU leaves bracket) → 用 market ask+fee 判断 residual EV`。若下一份 report 已不足 5 分钟，就跳过该 next-report expression；report clock 保留为连续特征，不再用固定 T-20/T-5 作为 alpha hard gate。生产维持 feature-only/shadow，不因本次同样本结果恢复 live。
