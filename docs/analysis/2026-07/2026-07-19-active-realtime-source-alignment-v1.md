# Active realtime source alignment v1

Generated: `2026-07-22T18:06:31.914686+00:00`
Window: `2026-07-08..2026-07-22` settled city-days
Status: `research/shadow_only`; no live authorization

## 结论

现有无新 key 的实时源里，**Helsinki/FMI 是下一轮最值得继续 forward shadow 的城市**。Seoul/Busan AMOS 和 Singapore/MSS 虽然 first-seen 很快、对下一份 routine METAR 也有信息，但不能把小数快源的日内最高直接当 WU 整数结算事实；它们出现了与 Atlanta 同结构的 terminal overshoot，应只作为概率特征。Tokyo/JMA 保留作已知 benchmark，不因本轮重复样本升级 live。

## 同口径结果

| city/source | first-seen p50/p90 | next METAR exact / within1 | source daily max in WU winner | terminal false-cross days | persistent event correct | correct executable | false executable | action |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `Helsinki/fmi` | 2.8/6.4m | 1016/1373 (74.0%) / 1352/1373 (98.5%) | 9/10 (90.0%) | 1 | 15/15 (100.0%) | 2/15 (13.3%) | NA | `P1_research_feature_shadow` |
| `Seoul/amos_runway` | 0.4/0.6m | 13849/18031 (76.8%) / 17946/18031 (99.5%) | 9/13 (69.2%) | 4 | 33/35 (94.3%) | 9/33 (27.3%) | 2/2 (100.0%) | `feature_only_not_cross_trigger` |
| `Busan/amos_runway` | 1.4/1.8m | 5758/8041 (71.6%) / 7922/8041 (98.5%) | 8/13 (61.5%) | 5 | 35/37 (94.6%) | 5/35 (14.3%) | 1/2 (50.0%) | `feature_only_not_cross_trigger` |
| `Singapore/singapore_mss` | 2.4/3.5m | 1798/2576 (69.8%) / 2558/2576 (99.3%) | 9/14 (64.3%) | 5 | 14/15 (93.3%) | 1/14 (7.1%) | 1/1 (100.0%) | `feature_only_not_cross_trigger` |
| `Tokyo/jma_amedas` | 7.4/8.0m | 1235/1537 (80.4%) / 1530/1537 (99.5%) | 11/13 (84.6%) | 2 | 41/43 (95.3%) | 10/41 (24.4%) | 1/2 (50.0%) | `benchmark_shadow` |

这里的 daily denominator 只保留快源时间覆盖 WU peak 的 city-day。WU 用 market native `units=m` 直接取摄氏度日高，**没有 C→F→C 或 double rounding**；canonical winning bracket 是 label。routine METAR、WU native 和快源日高分开列，不能相互替代。

错误 persistent event 更容易成交的形态在非美国源也出现：Seoul `2/2`、Busan `1/2`、Singapore `1/1` 错误事件首读可成交；对应正确事件只有 `7/26`、`2/23`、`0/12`。因此不能拿 90%+ persistent correctness 当可交易胜率。

## Terminal false city-days

| city | date | fast raw→round max | WU native max | winner |
|---|---|---:|---:|---|
| `Busan` | `2026-07-09` | 30.6→31°C | 30°C | `30` |
| `Busan` | `2026-07-11` | 34.5→35°C | 34°C | `34` |
| `Busan` | `2026-07-13` | 31.7→32°C | 31°C | `31` |
| `Busan` | `2026-07-14` | 30.6→31°C | 30°C | `30` |
| `Busan` | `2026-07-19` | 30.0→30°C | 29°C | `29` |
| `Helsinki` | `2026-07-17` | 24.5→25°C | 24°C | `24` |
| `Seoul` | `2026-07-09` | 27.6→28°C | 27°C | `27` |
| `Seoul` | `2026-07-11` | 32.8→33°C | 32°C | `32` |
| `Seoul` | `2026-07-16` | 28.9→29°C | 28°C | `28` |
| `Seoul` | `2026-07-19` | 26.6→27°C | 26°C | `26` |
| `Singapore` | `2026-07-08` | 31.9→32°C | 31°C | `31` |
| `Singapore` | `2026-07-15` | 33.0→33°C | 32°C | `32` |
| `Singapore` | `2026-07-16` | 32.9→33°C | 32°C | `32` |
| `Singapore` | `2026-07-18` | 32.6→33°C | 32°C | `32` |
| `Singapore` | `2026-07-19` | 32.8→33°C | 32°C | `32` |
| `Tokyo` | `2026-07-15` | 33.5→34°C | 33°C | `33` |
| `Tokyo` | `2026-07-21` | 34.6→35°C | 34°C | `34` |

## Atlanta negative control

- `terminal_false_cross_daily=1` 定义为：快源完整覆盖 WU peak、快源取整日高高于 WU native 日高、且快源落在错误 bracket，而 WU native 落在最终 winning bracket。
- persistent event 固定为 runner 的首个 `city-day + previous bracket` 事件，不能用重复 polling 扩大命中率。
- execution 固定为 source first-seen 后 direct best ask，`max_no_ask=0.97`、top size >=10。正确与错误事件分开报，盘口缺失是 coverage gap，不是策略过滤。
- fill 只从 canonical `fills` 回连 exchange order id；maker 在 submission journal 中尚未成交时也不会再漏记。

## 研究动作

1. Helsinki/FMI：继续 zero-notional forward shadow，累计至少 30 个 settled persistent events；重点看 terminal false cross 和可成交正确事件，而不是只看 next-METAR exact。
2. Seoul/Busan AMOS：保留 1-minute collector，但把 runway decimal max 作为 `P(WU leaves bracket)` 特征；禁止 raw cross 直接触发 previous-NO。
3. Singapore/MSS：S24→WSSS 本身是 cross-station basis，再叠加 terminal overshoot，只做 feature shadow。
4. Tokyo/JMA：继续现有 shadow/小样本观察；本报告不改变它的生产授权。
5. 新 key 到位后，再把 Paris/Amsterdam/Madrid 接入同一份 admission test；不要另造一套只看 source accuracy 的口径。

## Files

- `daily_source_wu_settlement.csv`: source daily max → WU native-unit max → winning bracket
- `next_routine_metar_alignment.csv`: distinct source observation → next routine AWC METAR
- `first_persistent_bracket_events.csv`: first bracket event → settlement → direct book → canonical fills
- `summary_by_city_source.csv`: city/source scoreboard
- `wu_native_fetch_status.csv`: WU request coverage/status without credentials

## Contract

signal funnel = distinct observations → first persistent city-day/previous-bracket event; evidence funnel = next routine METAR → native-unit WU → canonical settlement → direct book → canonical fill; baseline = same-time market; forward = collecting; conclusion = Helsinki P1 shadow, AMOS/MSS feature-only, no new live city
