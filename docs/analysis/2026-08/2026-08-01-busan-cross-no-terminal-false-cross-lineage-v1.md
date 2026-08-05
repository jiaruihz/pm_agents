# Busan cross NO 2026-08-01 terminal false cross 复盘 v1

## 结论

今天不是执行错误，而是 **AMOS→WU settlement basis 的结构性误判**。Busan CrossNO 共形成两笔真实持仓：

- `38 NO`：15 shares @ `0.90`，成本 `$13.50`；WU hourly running max 已到 `39°C`，该腿天气方向正确，预计 payout `$15`、PnL `+$1.50`。
- `39 NO`：8.3 shares @ `0.65`，成本 `$5.395`；AMOS 虽持续打印 `39.5–39.9°C`，WU/RKPK hourly running max 仍为 `39°C`，该腿天气方向错误，预计 payout `$0`、PnL `-$5.395`。

若 2026-08-01 的 WU 最终值不再修订，全天两腿合计成本 `$18.895`、payout `$15`、PnL `-$3.895`、ROI `-20.61%`。两笔 authenticated CLOB trade 均为 `fee_rate_bps=0`。本文截点为 `2026-08-01 16:52` 北京时间；市场尚未正式结算，因此这是 weather-confirmed / market-near-binary 的预计结果，不是 canonical realized PnL。

核心判断：`persistent_candidate_margin_v5` 确认的是“AMOS 持续跨过 arithmetic-round 阈值”，不是“WU 结算源一定离开旧档”。今天的 `39.7/39.9°C` 不是单点噪声，连续两次、`+0.5`、`+0.7` strong print 都无法排除 alternate-sensor terminal false cross。7 月 29 日复盘里“v5 避开 terminal false cross”的表述至此被 forward 反例否定。

## 口径与分母

- target slice：Busan，`fast_source_prev_no_trial_v1`，`persistent_candidate_margin_v5`，target date `2026-08-01`。
- signal grain：首次跨入一个新的 previous-NO bracket；fill grain：authenticated CLOB trade。
- signal funnel：36、37、38、39 共 4 个首次 bracket candidate。
- evidence funnel：4 个都有 fresh book；36 NO ask `0.999`、37 NO ask `0.98–0.998`，超过 `max_no_ask=0.97` 未下单；38、39 两档可执行并形成 2 个实际 fill。
- settlement-facing evidence：weather.com/WU historical API，location `RKPK:9:KR`，抓取于 `2026-08-01T08:52:19.679966Z`，19 条 hourly rows，最高 `39°C`；routine METAR running max 同为 `39°C`。

## 完整血缘

### 38 NO：成功腿

| 阶段 | 证据 |
|---|---|
| source | 12:53–12:55 UTC+9，AMOS `39.1 → 38.9 → 39.2°C`；候选旧档为 38 |
| signal | 12:55:19 UTC+9 检测；2 个 qualifying observations，已见 `>=38.7°C` strong print；距离下一 routine METAR 4.625 分钟 |
| book | fresh best bid/ask `0.86 / 0.90`，ask size 20 |
| order/fill | 12:55:23 UTC+9，taker BUY 15 shares 38 NO @ `0.90`，CLOB trade confirmed；order `0x25a9…442c` |
| next routine | 13:00 UTC+9 的 RKPK METAR 打印 `39°C`，正式离开 38 档 |
| current result | 38 NO 当前 best bid `0.999`；预计 `+$1.50` |

这腿说明 AMOS first-seen 确实有信息：在 routine 39°C 发布前约 5 分钟完成买入，且结算方向随后被 routine/WU 证实。

### 39 NO：失败腿

| 阶段 | 证据 |
|---|---|
| pre-cross | 13:28 UTC+9 AMOS 首次到 `39.6°C`，但没有 `>=39.7°C` strong print，v5 未下单；13:37–13:44 多次 `39.5°C` |
| signal | 13:45 UTC+9 AMOS `39.7°C`；13:46:20 检测时有 3 个 qualifying observations、strong print 已满足，routine running max 仍为 `39°C`，距离下一报告 13.647 分钟 |
| book | fresh best bid/ask `0.56 / 0.65`，ask size 8.3；市场只给 NO 约 65% 而非近确定性 |
| taker fill | 13:46:23 UTC+9，BUY 8.3 shares 39 NO @ `0.65`，成本 `$5.395`，CLOB trade confirmed；order `0x46fa…ed6` |
| maker child | 6.7 shares @ `0.64`，order `0x2d12…ff7f2`；GTD 到 13:50:06 UTC+9，authenticated trades 中无该 order，0 fill |
| source peak | 14:38 UTC+9 AMOS 最高 `39.9°C`，但始终未到 `40.0°C` |
| next routine/WU | 14:00 UTC+9 仍为 `39°C`；15:00 UTC+9 回落到 `38°C`。WU hourly 当日最高为 `39°C` |
| current result | 39 NO 当前无 bid、best ask `0.001`；预计 `-$5.395` |

这不是 v5 实现 bug：规则、fresh book、signed share cap 和 CLOB fill 都按设计工作。错误发生在规则上游的标签映射——它把同机场 alternate runway sensor 的 decimal arithmetic-round 当成 settlement-facing WU integer bracket。

## 盘口全过程与可执行反事实

39 NO 的 book 显示 source event 有短时重定价价值，但 hold-to-settlement thesis 错了：

| 时间（UTC+9） | 已知信息 | 39 NO bid / ask | 8.3 shares 可执行含义 |
|---|---|---:|---:|
| 13:26:13 | strong print 前 | `0.29 / 0.39` | 市场认为跨 40 的概率不高 |
| 13:46:23 | `39.7°C` strong print，实际入场 | fill @ `0.65` | 成本 `$5.395` |
| 13:46:37 | source event 已传播 | `0.69 / 0.79` | bid depth 9.82，若立即退出可得 `$5.727`，该腿 `+$0.332` |
| 14:01:56 | 下一份 routine 仍为 39 | `0.56 / 0.83` | bid depth 21；若以“下一份未确认”退出可得 `$4.648`，该腿 `-$0.747` |
| 14:16:20 | AMOS 仍高但 WU 未跨 | `0.60 / 0.61` | 仍可把该腿损失控制在 `$0.415` |
| 14:33:27 | terminal risk 上升 | `0.43 / 0.70` | 退出损失 `$1.826` |
| 14:50:03 | 市场已大幅转向 39 YES | `0.06 / 0.09` | 退出仅回收 `$0.498` |
| 15:05:40 | routine 已回落到 38 | `— / 0.02` | 基本失去止损流动性 |

固定用 14:01:56 的真实 top bid 做反事实，39 NO 损失可由 `$5.395` 降到 `$0.747`，全天两腿由 `-$3.895` 变成 `+$0.753`，节省 `$4.648`。这是可执行 book 反事实，不是已实际成交。

但不能据此直接上线“下一份 METAR 未确认就卖”：2026-07-20 Busan 31 NO 的下一份 routine 也未确认，下午 reheat 后最终仍跨档。正确做法是把 next-routine confirmation 与 final-WU probability 分成两个 head，并把 peak clock / remaining heat 纳入退出模型。

## 根因归因

1. **主因：source→settlement 概率模型缺失。** 当前 live rule 是 deterministic cross，只有 persistence/margin，没有 `P(WU leaves bracket)`；`max_no_ask=0.97` 只是价格上限，不是 EV gate。
2. **不是 transient print。** AMOS 在 `39.5–39.9°C` 持续约 70 分钟，v5 的 2-print 和 strong-margin 都会通过；继续堆同类 persistence threshold 不能修根因。
3. **市场在表达 terminal basis 风险。** 入场时 39 NO 仅 `0.65`，并未把 AMOS arithmetic cross 定价成确定事件；今天市场最终方向更准。但入场后 14 秒 bid 到 `0.69`，说明 first-seen 仍有短时信息价值。
4. **执行不是主因，且 depth-adaptive 降低了损失。** taker 只吃到可见 8.3 shares，剩余 6.7 maker 未成交；若强吃满 15 shares，失败本金会更大。
5. **没有 exit expression。** 当前策略只买入并持有，无法把 first-seen reprice alpha 与 settlement alpha 分开。今天的盘口表明这两种 alpha 可能方向不同。
6. **生产身份与研究 verdict 不一致但属于显式 trial override。** raw order 同时记录 `source_profile_live_eligible=false`、`blocked_reason=requires_source_to_settlement_alignment` 和 `source_profile_override_reason=user_approved_tiny_live_trial_pending_source_alignment`。因此不是隐藏代码绕过，而是 unresolved alignment 风险在 tiny live probe 中真实兑现。

## 对策略的影响

- 不否定 Busan AMOS first-seen，也不否定 38 NO 这种被下一份 routine 快速确认的跨档；今天同一天已有一个成功腿。
- 否定“v5 persistence 已解决 terminal false cross”。Busan raw arithmetic cross 不能继续被当作近确定性 settlement signal。
- 后续研究应统一输出两个概率：`P(next routine crosses)` 与 `P(final WU leaves bracket)`；入场用后者与实时 ask 比较 fee-adjusted EV，前者只用于短时 reprice/exit。
- 退出表达至少做同分母历史回放：立即 reprice take-profit、下一 routine 未确认退出、source reversion 退出，并按 `forecast peak clock + remaining heat + source/WU basis` 分层；先 frozen shadow，不从今天单例直接造 live stop。

## 证据路径

- signal/order raw：`/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/orders.jsonl`
- opportunities/events：`/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/{opportunities,events}.jsonl`
- AMOS + routine METAR：`/Volumes/jrs/weather_data_feed_service_runtime/output/live_cross_observations/2026-08-01/high_frequency_observations.jsonl`
- minute book：`/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/orderbook_snapshots/2026-08-01/`
- authenticated CLOB trades：38 NO trade `40054097-9c58-4334-be6e-ae8b271c3fb3`；39 NO trade `f12e6e67-d9fd-4801-bcfc-8925ba20cd2b`
