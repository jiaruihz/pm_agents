# H1 Late-Carry Maker / Timing / Book Structure v1

Status: `research`
Date: 2026-07-20
Verdict: `maker_filled_only_positive_but_planned_denominator_negative; do_not_expand_live`

## 结论与动作

当前 maker 不是主要增益来源，扩大 city 池也不是当前瓶颈。H1 已消费
`all_canonical_weather_state_v2`，近期 direct H1 分母已覆盖 21 城；真正问题是尾部风险和 maker
漏成交。6 个同 signal 的 live taker/maker pair 中，真正 passive maker 只成交 3 个，成交条件下省
`0.571c/share`，但 2 个 maker 漏掉已获胜的 taker leg 后，planned denominator 总体少赚
`$0.15699`，ROI delta `-0.493pp`。

动作：**不扩大 maker 比例、不按城市扩 live、不改当前 live selector。** 继续同 signal 的
queue-aware execution shadow；策略侧优先复核已经存在的 `15-17 local + assigned peak passed`
challenger，盘口侧先补 queue/depth/markout 和准确 fill event time，再决定 maker quote/lifecycle。

当前进程（2026-07-20 01:59 CST 检查）实际参数为 `5 taker + 10 maker`，但 canonical 样本全部来自
旧的 `5 + 5`；10-share maker 的有效证据为 0 个 opportunity，不能把旧 fill rate直接外推到现配置。

## Target / 固定分母

```text
在 H1 physical_confirmation_strong、current YES direct ask 0.95-0.99 的首次 city-day signal 上，
比较同一 signal 的 taker child 与 maker child；主指标为 maker-chain realized PnL / planned notional
相对 taker 的 delta，同时保留 unfilled winner 和 taker fallback。
```

- signal grain：首次有效 strong `(city, target_date)`；recent shadow quote 只取首次 direct H1 quote。
- execution grain：同 `signal_id/comparison_group_id` 的 child root chain；reprice 合并到 maker root。
- settlement：`settlement_outcomes.source_system='pm_history'`；exact bracket，触到 current 不等于最终赢。
- taker fee：Weather 官方 `0.05 * price * (1-price)`；maker fee=0；rebate 不计。
- 不能把 H1/H2 当 maker/taker A/B；这里只比较一个 H1 opportunity 内的两个 children。

## 数据快照与完整性

| 项目 | 值 |
|---|---|
| canonical DB | `runtime/weather.db`，fact build `2026-07-19T21:29:59Z` |
| shadow raw | `state_decisions.jsonl`，11,769 行，mtime `2026-07-19T21:31:27Z` |
| full ladder raw | JRS `full_ladder_output/orderbook_snapshots`；H1 fill token 421 个 `status=ok` snapshots |
| H1 actual fill window | target date `2026-07-15..18`；9 city-days、13 fill rows、3 独立日期 |
| H1 actual settled | 13/13；unsettled=0；missing_bracket=0 |
| 全库 settlement | settled 4,596；missing_bracket 38 |
| live CLOB gate | `gate_pass=true`；DB/raw 1,089/1,089 fills，差异 0 |
| 本次同步/重建 | 未手工同步/重建；DB build 晚于 H1 最新 order，目标结算已覆盖到 7/18 |

H1 actual fills 合计 principal `$78.305`、fee `$0.05952`、realized PnL `+$1.63548`、fee-adjusted
ROI `+2.09%`。13/13 fills 获胜，但只有 3 个 target dates，且这是 fill-selected cohort，不代替全信号分母。

## 双漏斗

Signal funnel：

| 层 | unit | rows | dates | cities |
|---|---|---:|---:|---:|
| raw shadow decisions | state row | 11,769 | 6 | 29 |
| 首次有效 strong | city-day signal | 68 | 6 | 29 |
| 首次 direct H1 quote `0.95-0.99` | city-day expression | 26 | 5 | 21 |
| live submitted H1 | city-day opportunity | 9 | 3 | 9 |

Evidence funnel：

| 层 | unit | rows | gap |
|---|---|---:|---|
| recent direct quote + settlement | city-day expression | 26 | 0 |
| paired split live | opportunity | 6 | 旧 3 单是 taker-only，不能配对 |
| taker fill | child | 6/6 | 0 |
| true passive maker fill | child root chain | 3/6 | 2 unfilled；1 taker fallback |
| current 10-share maker evidence | opportunity | 0 | sizing 刚变化，尚无新单 |

盘口、queue、fill-event-time 缺失属于 evidence gap，不是策略过滤。

## 0.99 case

近期 shadow 的 0.99 direct H1 有 3 行/3 日，3/3 赢，fee-adjusted ROI `+0.959%`；样本太小，且
0.99 每次亏损会吞掉约 104 次正常盈利。

唯一 paired live 0.99 是 Kuala Lumpur 7/17：

- taker：`5 @ 0.99`，fee `$0.00247`，PnL `+$0.04753`；
- maker root：初始 book `0.973 / 0.990`，经历 7 个 attempts，最终是
  `h1_maker_taker_fallback @ 0.99`，不是 passive maker fill；
- 实际 price/fee 改善为 0。

若只改善一个实际 tick 到 0.989 且成交，maker 相对 0.99 taker 只多约
`0.15c/share`：5 shares `$0.0075`，当前 10-share maker `$0.015`。可承受亏损率只从约
`0.95%` 提到 `1.10%`，不改变 H1 的尾部本质。真正有意义的是像 Jeddah/Chongqing 那样取得
`0.6-1.0c/share`，但这取决于 queue fill，不是看到大 spread 就能计入收益。

### 入场时并非“只剩 0.99”

9 个历史 fill opportunity 的 submit-time fresh book 全部有 ask：只有 Kuala Lumpur 和 Chongqing 两个
ask 是 `0.990`，其余 7 个 ask 在 `0.950-0.988`。真正成交在 `0.990` 的只有 Kuala Lumpur；Chongqing
taker 成交 `0.989`，maker 则在 `0.979` 成交。

6 个 paired maker 的初始 ask 与首挂之间仍有 `1.6-5.8c`，中位数 `2.8c`：

| city/date | submit book | initial maker | highest maker | maker outcome |
|---|---:|---:|---:|---|
| KualaLumpur 7/17 | `0.973 / 0.990` | 0.974 | 0.980 | 未被动成交，最终 fallback 0.990 |
| Jeddah 7/17 | `0.965 / 0.988` | 0.971 | 0.982 | 0.982 passive fill |
| Guangzhou 7/17 | `0.918 / 0.963` | 0.905 | 0.930 | unfilled |
| Tokyo 7/18 | `0.973 / 0.975` | 0.942 | 0.973 | 0.973 passive fill |
| Chongqing 7/18 | `0.969 / 0.990` | 0.958 | 0.979 | 0.979 passive fill |
| Taipei 7/18 | `0.962 / 0.987` | 0.963 | 0.987 | unfilled |

Guangzhou、Tokyo、Chongqing 的 initial maker 分别比 submit-time best bid 低 `1.3c / 3.1c / 1.1c`；
约 30-38 秒后的首次 reprice 才回到 fresh book。根因不是盘口没有空间，而是 split child 沿用了前一份
plan/root quote，maker submit 时虽然已经读到新 book，却没有据此重算首挂。下一轮 shadow 应优先比较
`stale-root initial` 与 `submit-time fresh post-only`，仍受同一 EV ceiling 约束。

### ask 为空的持续时间

maker 活跃生命周期共 30 次 fresh quote（每个 attempt 的 book），30/30 都存在 best ask；所以这些挂单
等待的 2-4 分钟内没有出现 ask-side vacuum。

full-ladder 对 9 个 token 共 421 个 `status=ok` snapshots（每 token 20-65 个）。8/9 从未出现
`raw.asks=[]`。唯一一次是 Chongqing：入场后 `192.6min`，`2026-07-18T09:39:55Z` 首次看到 ask 为空；
前一份 `09:08:55Z` 仍有 `ask=0.999`。该空 ask 是 archive 最后一份 snapshot，属于 right-censored：只能说
transition 发生在这 31 分钟区间内，不能声称它持续了 31 分钟，也无法从现有 archive 给出结束时间。
这次盘口真空发生在成交三个多小时后，不解释入场 maker fill/unfill。

## Paired maker vs taker

| city/date | taker | maker chain | route | maker - taker PnL |
|---|---:|---:|---|---:|
| KualaLumpur 7/17 | 0.990 | 0.990 | taker fallback | `$0.00000` |
| Jeddah 7/17 | 0.988 | 0.982 | passive fill | `+$0.03296` |
| Guangzhou 7/17 | 0.962 | unfilled | unfilled winner | `-$0.18087` |
| Tokyo 7/18 | 0.973 | 0.973 | passive fill | `$0.00000` |
| Chongqing 7/18 | 0.989 | 0.979 | passive fill | `+$0.05271` |
| Taipei 7/18 | 0.987 | unfilled | unfilled winner | `-$0.06179` |

汇总：

- true passive fill：3/6 opportunities，15/30 planned maker shares；
- maker-chain completion（含 fallback）：4/6，20/30 shares；
- passive filled-only saving：`+$0.08567 / 15 shares = +0.571c/share`；
- 全 planned denominator：maker-chain PnL `$0.37753` vs taker `$0.53452`，delta `-$0.15699`；
- maker PnL/planned notional `1.322%` vs taker `1.815%`，delta `-0.493pp`。

所以“maker 成交的单看起来更赚钱”是真的，但不足以覆盖未成交赢家；filled-only 口径不能用于扩 maker。

## 宽分母与 timing

近期 first-direct-H1 quote 26 行/5 日/21 城，25 win、1 loss；fee-adjusted ROI `-1.51%`，
target-date block bootstrap 95% CI `[-19.58%, +2.67%]`。唯一亏损是 San Francisco 7/14：
14:16 local 时 running max 78.98°F、已回落 0.9°F、高点成熟 77m、forecast peak 已过，但最终升到
`80-81°F`，使 `78-79 YES @0.95` 全亏。一次损失吞掉其余 25 个 winners。

预先已存在的 `late15_assigned_peak_base` challenger 比扩 city 更值得继续：

- 历史冻结窗：102 行/16 日、0 loss、ROI `+1.80%`，但低于 120 个零亏损尾部门；
- 当前 recent direct H1 diagnostic：15-17 local 15/15 win、ROI `+2.14%`；15 点前 10/11 win、ROI
  `-6.52%`；
- recent 不是独立确认窗，只有 4 日，不能改 live；但方向与历史 challenger 一致。

这说明容量应通过“更晚、同物理状态的连续概率 challenger”扩，不应通过新增城市名单或降低 strong
条件硬扩。当前 runner 本来就没有 city allowlist。

## 盘口特征：能看见什么、还缺什么

近期 descriptive（事后、未校正多重检验）：

| slice | rows/wins | fee ROI | 解释 |
|---|---:|---:|---|
| spread `>4c` | 3 / 2 | `-30.92%` | 包含 San Francisco loss；宽盘可能是风险/陈旧报价，不是免费 maker 空间 |
| spread `<=4c` | 23 / 23 | `+1.6%..+2.6%` | 仅诊断，不能立刻变 hard gate |
| ask depth `<10` | 4 / 3 | `-21.93%` | 同样由一个 loss 主导；也暴露幽灵/薄盘口风险 |
| ask depth `>=10` | 22 / 22 | `+1.7%..+2.8%` | 可作为 execution-quality challenger，不是天气 alpha gate |

live maker 6 对的 initial spread 与 fill 并不单调，样本也只有 2 天。当前缺少三项决定性字段：

1. 每次 reprice 的 `bid_size/ask_size`、自己价位 queue-ahead 和成交 tape；代码使用 ask size 决定
   fallback，却没有把 size 写进 lifecycle journal。
2. submit-time book 与 signal book 的 drift。6 个 maker root 中，3 个初始限价比 executor 看到的 fresh
   bid 低 `1.1-3.1c`，前 30 秒直接排在旧价位，随后 chase 又重置 queue。
3. 真实 exchange fill event time 与 1/5/15m、next-data-epoch markout。现有 `clob_fills.filled_at` 部分是
   reconciliation 时间；Tokyo 被报告为 2,225s latency，但 TTL cancel check 在 15m 已确认订单 MATCHED，
   因此不能用现字段拟合 fill hazard。

## 推荐的下一轮 frozen A/B（shadow）

同一个 H1 signal 同时记三条 planned denominator，不下新真钱：

1. `taker_now`：保持 5-share core，作为基准。
2. `current chase+fallback`：原 profile，完整保留漏单和 fallback。
3. `fresh-submit passive`：由 executor 用 submit-time fresh book 重新算 post-only quote；只在相对 taker
   cost 有可观测 saving 时挂，不追到 ask、不转 taker，并在下一 weather/source data epoch 前撤单。

第三条不要先拍一个新的 hard threshold；先输出 saving × queue-ahead × epoch-distance 的连续曲线，再冻结
quote rule。最低记录字段：top-3 depth、queue-ahead、bid/ask/size 每次变化、partial fill、cancel reason、
exchange trade time、1/5/15m 与 next-epoch markout。

## 八环与三门

| 环 | 状态 |
|---|---|
| 描述性绩效 | PASS：canonical H1 fills 与 recent shadow denominator 均已量化 |
| 统计推断 | FAIL：live pair 6 个/2 日；recent ROI CI 跨 0 |
| 信号判别 | FAIL：没有 calibrated `P(win)` |
| 概率分布 | NA：本轮只审 execution；不能证明 weather residual |
| 执行微结构 | PARTIAL：同 signal pair 已有，但 queue/markout/event-time 缺失 |
| 容量 | FAIL：10-share maker 尚无一笔新 evidence |
| 组合相关性 | FAIL_LOW_DATES：live 只有 3 个 target dates |
| 基准/反事实 | PASS_THIN：maker vs taker planned denominator 已配对，结果为负 |

```text
significance=FAIL_LOW_SAMPLE
baseline=FAIL_NO_PROBABILITY_RESIDUAL; execution paired baseline is negative
forward=FAIL_THIN; current 10-share maker has zero evidence
conclusion=inconclusive; continue shadow/collector, do not expand live maker or city scope
```

## 产物

- Script: `scripts/analysis/execution_quality/research_h1_late_carry_maker_v1.py`
- Summary: `docs/analysis/2026-07/generated/h1_late_carry_maker_v1/summary.json`
- Recent signal rows: `docs/analysis/2026-07/generated/h1_late_carry_maker_v1/recent_h1_shadow_signals.csv`
- Descriptive slices: `docs/analysis/2026-07/generated/h1_late_carry_maker_v1/recent_h1_shadow_slices.csv`
- Paired live execution: `docs/analysis/2026-07/generated/h1_late_carry_maker_v1/live_paired_execution.csv`
- Historical fill book structure: `docs/analysis/2026-07/generated/h1_late_carry_maker_v1/historical_fill_book_structure.csv`
- Historical full-ladder timeline: `docs/analysis/2026-07/generated/h1_late_carry_maker_v1/historical_fill_book_timeline.csv`
- Explicit empty-ask runs: `docs/analysis/2026-07/generated/h1_late_carry_maker_v1/historical_empty_ask_runs.csv`
