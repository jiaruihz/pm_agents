# Busan 31 NO live lineage v1

Status: weather outcome confirmed at 32 C; exchange settlement pending
Target date: 2026-07-20
Evidence cutoff: 2026-07-20 08:03:36 UTC / 17:03:36 Busan local

## 结论

Busan `31 NO` 的最终天气方向已经获胜：15:55--15:59 local 的 AMOS 连续打印 `32.0--32.1 C`，
16:00 routine METAR 正式报 `32 C`，08:03 UTC 重取 Weather.com/WU native-metric history 时，
daily running max 也已经是 `32 C`。合约仍未完成 exchange settlement，所以当前只能写
weather-confirmed、不能发布 realized PnL；若 WU 后续口径不回修，15 shares 的最终 PnL 为约
`+$2.23625`（principal `$12.70`，已确认 taker fee `$0.06375`，maker fee `0`）。

上午的 signal 不能再称为“最终结算 false cross”。它准确的标签是 **next-report transient false**：
12:50 AMOS 短暂到 `32.0 C`，但报文前已经回到 `31.0 C`，13:00 routine METAR 没有确认；下午则是
**terminal retained / report-confirmed cross**。这一天揭示的核心不是 AMOS 完全无效，而是必须分开估计：
`P(next routine METAR crosses)` 与 `P(final WU leaves the current bracket)`，不能拿前一个标签直接代表最终赔付。

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
| 03:49:32 post / 03:49:36 fill | 12:49:32 / 12:49:36 | post-only maker order `0xd9ed...6d28`：`5 @ 0.84`；Polymarket public activity timestamp 显示约 4 秒后成交。canonical 的 `04:02:35` 是迟到的 first-seen/ingest time，不是实际 fill time。 |
| 03:50 obs | 12:50 | AMOS 单分钟最高 `32.0 C`。 |
| 03:58:41 book | 12:58:41 | `31 NO` bid/ask 已升至 `0.86/0.96`；市场先跟随快源。 |
| 04:00 report | 13:00 | 下一份 routine METAR 仍为 `31 C`，fast-source thesis 的第一项验证失败。 |
| 04:02:35 canonical ingest | 13:02:35 | canonical 此时才发现 maker fill；累计仓位仍是 `15 shares`、principal `$12.70`，但不能用该时间解释盘口或订单生命周期。 |
| 05:00 report | 14:00 | routine METAR 仍为 `31 C`。 |
| 05:15 book | 14:15 | `31 NO` bid/ask `0.19/0.33`；YES last trade `0.90`。 |
| 05:24 obs | 14:24 | AMOS 已回落到 `30.3 C`。 |
| 05:37 obs | 14:37 | AMOS reheat 到 `31.4 C`，但仍未再次跨 31.5。 |
| 05:39 WU fetch | 14:39 | WU native-metric daily running max `31 C`，最新有效小时已覆盖 14:00 local。 |
| 05:39:41 fresh book | 14:39:41 | direct CLOB `31 NO` bid/ask `0.16/0.27`，top bid `41` 足以覆盖 15 shares。 |
| 06:45--06:58 obs | 15:45--15:58 | AMOS 由 `31.6` 稳定抬升到 `32.1 C`，跨档不再回吐。 |
| 06:59:56 book | 15:59:56 | `31 NO` bid/ask 收敛到 `0.96/0.99`。 |
| 07:00 report | 16:00 | routine METAR `RKPK 200700Z ... 32/27 ...`，正式确认 32 C。 |
| 07:01:56 book | 16:01:56 | direct/PIT book `31 NO` bid/ask `0.99/0.997`。 |
| 08:03 WU fetch | 17:03 | WU native-metric daily running max 已为 `32 C`。 |

## 上午与下午的可区分模式

| 特征 | 上午短刺（下一报未确认） | 下午终场跨档（下一报确认） |
|---|---:|---:|
| 报文前 10 分钟 `>=31.5 C` | `2/9 = 22.2%` | `8/8 = 100%` |
| 报文前最新 / 窗口峰值 | `31.0 / 32.0 C` | `32.0 / 32.1 C` |
| peak-to-latest drawdown | `1.0 C` | `0.1 C` |
| 最后 5 分钟 `>=31.5 C` | `0/5` | `4/4` |
| 盘口形态 | 12:58 `0.86/0.97`，bid size `5.79`、ask size `87.05`；12:59 ask size 增至 `123.41`，随后反转 | 15:49 `0.29/0.32`，随 `31.8 -> 32.1` 连续 repricing；15:59 收敛到 `0.96/0.99` |
| routine METAR | 13:00 仍为 31 | 16:00 正式为 32 |

最强的可辨识特征是 **terminal retention**，不是单点 peak。上午的 32.0 是孤立短刺，离 routine report
还有 10 分钟时已经完整回吐；下午在最后 10 分钟没有一条跌回 31.5 以下。风/露点也有同日差异：上午末段
风约 `13--15 kt` 且温度、露点同步急回落，下午末段风约 `11--12.5 kt`、路径更稳定；但这只是单日机制证据，
不能直接写成通用风速 gate。

盘口能增强判断，不能代替 source/settlement label。上午 ask 同样触到 `0.97`，所以固定 `ask>=0.94/0.97`
无法区分真假；真正的差异是盘口深度偏斜、spread 与是否随着 terminal observations 持续收敛。更重要的是，
我们的 aviationweather collector 到 13:13 才 first-see 13:00 的 31 C，而盘口 13:07 已明显反转，说明市场中
至少有玩家拥有比我们该链路更快的 official/reliable reference。等待盘口完全确认也会把正确事件的入场价推到
`0.96--0.99`，基本吃掉 alpha。

逐分钟重新 join 原始 AMOS 后另发现两个链路口径问题：第一，quote observer 的 `source_temp_c/source_obs_ts`
是触发事件 anchor，12:53 后仍显示 `31.6 C @ 12:51`，不能当作 fresh source；实际 AMOS 在 12:53:31
已被采到 `31.1 C`。第二，高频 AMOS payload 在 13:00:15 已带回 `RKPK 200400Z ... 31/27 ...`，
但策略依赖的独立 aviationweather source-event 链到约 13:13 才 first-see 该 routine report。也就是说，
official negative confirmation 已在系统某条 raw 链上存在，却没有进入策略的即时 state。该盲区没有影响 maker fill
（exchange activity 证明 maker 在 12:49:36 已成交），但会影响后续 cancel/exit/再入场判断。

maker fill 的 canonical 时间也有 1 条污染：actual public activity timestamp `12:49:36 local`，canonical
`filled_at_utc=04:02:35` 实际是晚 `12m59s` 的 ingestion first-seen。受影响仅此 `1 fill / 5 shares @0.84`；
仓位、principal 和最终方向不变，但依赖 fill clock 的盘口归因必须使用 exchange timestamp。

## 当前敞口 `[WEATHER-CONFIRMED / EXCHANGE-UNSETTLED]`

- position：`15 shares 31 NO`；没有剩余未成交 child quantity。
- principal：`$12.70`；加权入场价 `0.84667`。
- 可确认 entry fee：taker `$0.06375`；post-only maker 应为 fee `0`。
- 16:00 routine METAR 与 17:03 local WU running max 均已确认 `32 C`；在 WU 不回修的前提下，
  `31 NO` 最终 PnL 为约 `+$2.23625`。
- exchange 尚未 settlement，因此该数字仍不是 realized PnL；不再使用 14:39 的 `0.16` interim bid
  表述当前经济结果。

canonical 当前把 maker fill 记为 `weather_fee_curve_estimate $0.03360`，与 post-only maker 语义冲突；
这会把最终/MTM 亏损多报 `$0.03360`，需要单独 fee adjustment，但不是本次大额 adverse move 的原因。
canonical valuation timestamp 仍是 2026-07-19，故本报告没有使用其中的 `val_mid=0.30`，而是直接读取
2026-07-20 05:39 UTC CLOB book。

## 根因

### Signal 层

当前 `persistent_candidate_margin_v5` 只证明 AMOS runway sensor 相对整数 METAR running max 有
持续小数超越，不证明下一份 routine METAR 或 Weather.com/WU 最终最高温必然立刻跨档。今天不是 `.5`
边界 bug：上午 `31.9, 31.9, 32.0` 已通过 `+0.7 C` strong margin，却没进入下一份 routine METAR；
但下午晚 reheat 最终又把 WU 推到 32。说明把 margin/persistence 当 deterministic settlement label 的机制不成立。

历史 negative control 仍然成立：2026-07-08..17 的完整 peak-coverage 日里，Busan AMOS daily max
只有 `5/9` 落入 WU winning bracket，已有 `4/9` terminal false city-days；persistent first-event 虽为
`23/25` correct，但正确事件只有 `2/23` 首读可成交，错误事件反而 `1/2` 可成交。今天作为 forward addition
增加了 1 个最终正确且首读可成交事件，但不能据此把结论升级为 live。

本次另按每个 cross episode 做了 exploratory checkpoint replay（2026-07-08..17、9 个日期，episode 高度相关，
不能当独立样本）：5 分钟后仍满足 `>=80%` observations 在 `X+0.5` 以上、latest 仍在阈值以上、drawdown
`<=0.2 C` 的 retained cross，下一份 METAR 确认为 `26/28 = 92.9%`；再要求 latest `>=X+0.7 C`，也只有
`18/19 = 94.7%`，仍存在 false event。因此 retention 是概率特征，不是可靠 hard trigger；阈值来自同样本探索，
尚未 forward 校准。

以后应输出两个独立 score：

1. `P(next METAR cross | source path, report clock, market path)`：判断当前快源能否进入下一报。
2. `P(final WU leaves bracket | current official max, remaining heat window, forecast/path state)`：判断实际赔付。

交易只使用第二个概率与 executable price/fee 的 residual；第一个是 latency-chain feature。上午是
`next-METAR false / final-WU true`，正好证明两者不能共用一个 label。

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
AMOS observations -> morning transient cross -> afternoon terminal-retained cross -> 1 final WU bracket leave

evidence funnel:
fresh PIT book -> child orders 2 -> fills 2 / 15 shares -> 13:00 routine METAR no-cross
-> 16:00 routine METAR cross -> WU max 32 -> exchange settlement unresolved
```

Evidence layers：当前 JRS raw fast-source events/orders、高频 AMOS raw、current observation cache、
targeted PIT books、direct Weather.com/WU metric fetch、direct CLOB book、canonical orders/fills/fact_trades。
未做 sync/rebuild；`weather.db` 在 05:25 UTC 已覆盖两笔 fill，fill coverage gate `gate_pass=true`。
缺环：exchange settlement；WU weather outcome 已确认 32，因此只写 weather-confirmed，不发布 realized PnL。
额外 health evidence：05:36 UTC patrol `status=critical`、`reason=runner_latest_stale`；未把该 health alert
误当成 signal failure，也未因分析动作停止/重启生产进程。

## 动作边界

既有完整研究仍支持把 Busan AMOS raw previous-NO 保持在 zero-notional feature-shadow；本次下午最终正确不构成
重新 live 的证据，也不需要再加一个更高 margin/price hard gate。保留 1-minute AMOS collector，把 terminal retention、
report clock、market repricing 作为概率 residual features；等独立 forward city-days 足够后再判断是否存在 fee-adjusted edge。

复现脚本：`scripts/analysis/forecast_quality/research_busan_cross_confirmation_v1.py`；生成物：
`docs/analysis/2026-07/generated/busan_cross_confirmation_v1/summary.json`、`today_event_windows.csv`、
`today_no_book_timeline.csv`、`historical_episode_checkpoints.csv`。脚本已运行并通过 `py_compile`。
逐节点 raw-source/book join 见 `morning_amos_book_joined_timeline.csv`。
