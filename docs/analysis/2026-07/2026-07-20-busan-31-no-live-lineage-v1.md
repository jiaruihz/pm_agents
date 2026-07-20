# Busan 31 NO live lineage v1

Status: current incident review; position unsettled
Target date: 2026-07-20
Evidence cutoff: 2026-07-20 05:39:41 UTC / 14:39:41 Busan local

## 结论

Busan `31 NO` 的 AMOS signal 对“下一份 routine METAR / 当前 WU running max 已离开 31”而言已经是
false cross：AMOS runway-air 在 12:48–12:50 local 打印 `31.9, 31.9, 32.0 C`，但 13:00 和
14:00 routine METAR 都仍为 `31 C`，05:39 UTC 直接重取 Weather.com/WU native-metric history 时，
最新有效小时已经覆盖 14:00 local、daily running max 仍为 `31 C`。

合约尚未结算，之后若 WU 最终打印 32，`31 NO` 仍可反转获胜；但截至本快照，forecast peak 已过、
AMOS 虽有小幅 reheat、但尚未再跨 31.5，盘口也把 NO 从成交时 `0.84/0.85` repricing 到
fresh executable bid `0.16`。
因此不能写成 realized loss，但这是一个已经通过 next-routine negative control 的 source-basis failure。

更重要的根因是生产策略状态错位：source registry 明确写着 Busan AMOS `live_eligible=false`，
2026-07-19 完整研究也已给出 `feature_only_not_cross_trigger`，但生产 city policy 仍保留历史
`user_approved_tiny_live_trial_pending_source_alignment` override，start 入口仍把 Busan 放在 live cities。
runner 按旧 policy 正常执行；错误在于研究降级没有落到生产配置。

## 逐笔血缘

所有时间为 UTC；Busan local = UTC+9。

| time UTC | local | evidence |
|---|---:|---|
| 03:00 report | 12:00 | RKPK routine METAR `31 C`；settlement-facing running max 为 31。 |
| 03:47 obs | 12:47 | AMOS `31.6 C`，进入 qualifying margin。 |
| 03:48 obs / 03:49:21 detect | 12:48 / 12:49:21 | AMOS `31.9 C`；满足两个 distinct observations 且至少一个严格高于 `31.7 C`。 |
| 03:49:27 decision | 12:49:27 | 选择 previous exact bracket `31 NO`；fresh ask `0.85 x 22.49`，无 live blocker。 |
| 03:49:31 fill | 12:49:31 | taker order `0x4864...173f`：`10 @ 0.85`，principal `$8.50`，exact fee `$0.06375`。 |
| 03:49:32 post | 12:49:32 | post-only maker order `0xd9ed...6d28`：`5 @ 0.84`。 |
| 03:50 obs | 12:50 | AMOS 单分钟最高 `32.0 C`。 |
| 03:58:41 book | 12:58:41 | `31 NO` bid/ask 已升至 `0.86/0.96`；市场先跟随快源。 |
| 04:00 report | 13:00 | 下一份 routine METAR 仍为 `31 C`，fast-source thesis 的第一项验证失败。 |
| 04:02:35 fill | 13:02:35 | maker order 后续全成 `5 @ 0.84`；累计 `15 shares`、principal `$12.70`。 |
| 05:00 report | 14:00 | routine METAR 仍为 `31 C`。 |
| 05:15 book | 14:15 | `31 NO` bid/ask `0.19/0.33`；YES last trade `0.90`。 |
| 05:24 obs | 14:24 | AMOS 已回落到 `30.3 C`。 |
| 05:37 obs | 14:37 | AMOS reheat 到 `31.4 C`，但仍未再次跨 31.5。 |
| 05:39 WU fetch | 14:39 | WU native-metric daily running max `31 C`，最新有效小时已覆盖 14:00 local。 |
| 05:39:41 fresh book | 14:39:41 | direct CLOB `31 NO` bid/ask `0.16/0.27`，top bid `41` 足以覆盖 15 shares。 |

## 当前敞口 `[UNSETTLED]`

- position：`15 shares 31 NO`；没有剩余未成交 child quantity。
- principal：`$12.70`；加权入场价 `0.84667`。
- 可确认 entry fee：taker `$0.06375`；post-only maker 应为 fee `0`。
- 05:39 UTC fresh executable bid `0.16`：gross liquidation value `$2.40`，仅按 entry principal +
  confirmed entry fee 计，mark-to-bid 为 `-$10.36375`；若假设立即 taker exit，官方曲线 exit fee
  约 `$0.10080`，则 net hypothetical exit PnL 约 `-$10.46455`。这不是 realized PnL。
- 若最终 WU max 仍为 `31 C`：31 NO 失败，按正确 maker-zero fee 的最终 PnL 为 `-$12.76375`。
- 若之后 WU max 到 `32 C` 或更高：31 NO 获胜，最终 PnL 为约 `+$2.23625`。

canonical 当前把 maker fill 记为 `weather_fee_curve_estimate $0.03360`，与 post-only maker 语义冲突；
这会把最终/MTM 亏损多报 `$0.03360`，需要单独 fee adjustment，但不是本次大额 adverse move 的原因。
canonical valuation timestamp 仍是 2026-07-19，故本报告没有使用其中的 `val_mid=0.30`，而是直接读取
2026-07-20 05:39 UTC CLOB book。

## 根因

### Signal 层

当前 `persistent_candidate_margin_v5` 只证明 AMOS runway sensor 相对整数 METAR running max 有
持续小数超越，不证明 Weather.com/WU 的最终整数最高温必然跨档。今天不是 `.5` 边界 bug：
`31.9, 31.9, 32.0` 已经通过 `+0.7 C` strong margin，但仍未进入下一份 routine METAR/WU。
说明把 margin/persistence 当 deterministic label 的机制本身不成立。

历史 negative control 已经足够明确：2026-07-08..17 的完整 peak-coverage 日里，Busan AMOS daily max
只有 `5/9` 落入 WU winning bracket，已有 `4/9` terminal false city-days；persistent event 虽为
`23/25` correct，但正确事件只有 `2/23` 首读可成交，错误事件反而 `1/2` 可成交。今天是同一 adverse-selection
形态：信号时可以买到 0.85，下一份 official-facing row 不确认后盘口反转。

### Production / governance 层

- `weather_data_feed/fast_event_source_profiles.json`：Busan `live_eligible=false`、
  `blocked_reason=requires_source_to_settlement_alignment`。
- `scripts/ops/weather_fast_source_city_policy.py`：仍设 `default_mode=live_trial`、15 shares，并用历史 override
  绕过 profile。
- `scripts/ops/start_weather_fast_source_prev_no_trial.sh`：默认 `LIVE_CITIES` 仍含 Busan。
- 2026-07-19 research verdict 已是 `feature_only_not_cross_trigger`，但没有转成生产 deploy。

因此这是“研究状态已降级、生产入口未同步”的配置事故，不是采集链路慢或 runner arithmetic 实现偏离。

### Runtime health 次生问题

05:36 UTC patrol 另报 `critical: runner_latest_stale`：fast-source runner 的 `latest.json` 已约 9–10 分钟未更新，
而 high-frequency producer 仍持续新鲜。进程当时持有 577 MB `source_events/sources.jsonl` 的 read fd；代码每个
`run_once` 分别调用 `metar_running_max()` 和 `metar_report_clocks()`，两次从头扫描完整 append-only JSONL。
这使配置中的 `30s/10s` loop cadence 随历史文件增长失效。它不是 03:49 这笔订单的直接原因（该单 source detect
lag 只有 1.36 分钟），但说明当前 runner 已不具备声明的实时性；降级 Busan 时还应把 METAR running state/cadence
改成增量索引或 producer latest state，不能继续全表扫描。

## 双漏斗与完整性

```text
signal funnel (city-day/event):
AMOS observations -> 2 qualifying persistent observations -> 1 first live event -> 1 selected previous-NO

evidence funnel:
fresh PIT book 1 -> child orders 2 -> fills 2 / 15 shares -> next routine METAR no-cross
-> current WU max 31 -> settlement unresolved
```

Evidence layers：当前 JRS raw fast-source events/orders、高频 AMOS raw、current observation cache、
targeted PIT books、direct Weather.com/WU metric fetch、direct CLOB book、canonical orders/fills/fact_trades。
未做 sync/rebuild；`weather.db` 在 05:25 UTC 已覆盖两笔 fill，fill coverage gate `gate_pass=true`。
缺环：最终 WU/market settlement；因此只把当前状态写为 `[UNSETTLED]`，不发布 realized PnL。
额外 health evidence：05:36 UTC patrol `status=critical`、`reason=runner_latest_stale`；未把该 health alert
误当成 signal failure，也未因分析动作停止/重启生产进程。

## 动作边界

既有完整研究已经支持把 Busan AMOS raw previous-NO 从 live 降为 zero-notional feature-shadow；
本事故不需要再加一个更高 margin hard gate。当前用户请求是诊断，未执行生产 pause/deploy，也未对未结算仓位
做卖出或对冲。生产变更应走 `weather-strategy-deploy`，同时清掉 city policy override 和 start 入口 live membership，
保留 1-minute AMOS collector 供概率 residual 研究。
