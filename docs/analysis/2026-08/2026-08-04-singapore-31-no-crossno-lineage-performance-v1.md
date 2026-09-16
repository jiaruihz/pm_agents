# Singapore 31°C NO / CrossNO lineage and performance v1

## Final update — supersedes the intraday provisional label

2026-08-04 17:00 SGT（09:00 UTC），WSSS routine METAR 正式打印 `32°C`；WU historical
最终日高也为 `32°C`。因此 31 NO **获胜**，不是 source-basis false cross。本文下面 15:02/15:36
章节保留当时 PIT 现场，但其中 `WU max=31`、`18/19 correct`、`4/5 live correct` 与“今天暂错”只允许解释为
盘中 provisional，不得进入 settled 统计。

最终修正：

- **不能把中午首次 cross 解释成提前 5 小时预测 WSSS。** S24 下午已回落到
  16:09–16:29 的 `30.7–30.9°C`，中午 episode 已失去连续性；16:30 后发生的是一个独立的
  reheat/re-cross episode。
- 16:30 SGT WSSS 仍为 `31°C`，同时最近 S24（16:29）为 `30.9°C`，raw basis `-0.1°C`，
  rounded bracket 同为 31。S24 随后在 16:49/16:54 打印 `31.6/31.8°C`；两笔 first-seen 分别为
  16:51:24/16:56:14，后者才完成 current-policy persistence + strong confirmation。
- 17:00 SGT WSSS 打印 `32°C`（collector first-seen 17:04:18）；最近 S24（16:59）为 `31.5°C`，
  raw basis `-0.5°C`，rounded bracket 同为 32。因此有效 actionable lead 是约 **8 分钟**，不是 5 小时。
- 17:30 SGT WSSS 又回到 `31°C`；最近 S24（17:29）为 `31.2°C`，raw basis `+0.2°C`，
  rounded bracket 同为 31，但 daily max 已不可逆地成为 32。
- current-policy first expressions：`19/19 correct`；terminal city-days：`14/14 correct`；实际 live matched
  expressions：`5/5 correct`。
- 今日 fill：13.5 shares @ 0.926，cost `$12.501`、canonical fee `$0.04625`；若按最终 NO payout
  计，fee-adjusted PnL `+$0.95275`。连同截至 7/30 已知结束的 `+$3.226821`，累计 `+$4.179571`。
- Gamma 当前仍 `closed=false`，但 outcome price 已为 YES/NO `0.0005/0.9995`；在 market 正式关闭、
  canonical settlement 写入前，上述今日 PnL 是 WU-confirmed，不冒充 canonical realized。

治理修正：2026-08-04 17:00 SGT 以前的 Singapore/WSSS `daily_max=31` 一律标为 intraday provisional；
不能用它给当天 candidate 贴 terminal false-cross/settled loss 标签。此前“今天是第一次错”的口头结论撤回。
同时按 episode 重置 attribution：中午 first-cross、回落、下午 re-cross 分开记；本单最终盈利不能记成
“S24 提前 5 小时打印”，只能记成“中午旧仓位一直存续，下午约 8 分钟领先的 re-cross 最终确认”。
Singapore 已按用户授权移到 zero-notional shadow；本次最终翻转不自动恢复 live。

## Forecast / expression evidence（合并）

独立 forecast-path 与 forecast-centered basket runner 的耐久结论合并在本事故报告，
不再保留两份平行日期文档：

- `2026-05-12..2026-07-07` 的 56 个 city-days 中，GFS daily Tmax bias
  `-0.664°C`、MAE `0.979°C`、exact bracket `28.6%`；严格 D-1 12Z 的 55 日
  path MAE `1.255°C`、peak-clock median absolute error `1h`。ECMWF 对应 Tmax
  bias `-1.708°C`、path MAE `1.668°C`，不能把 forecast center 当确定性档位。
- forecast-center `±1/±2` symmetric YES strips 在两个 D-1 窗口均为负；
  cold-bias-aware `[center, center+1, center+2]` 点估为正但 date-block CI 跨 0。
  lower-distance2 NO 的早窗 38/38 仍不足以覆盖 unseen-tail（95% binomial loss
  upper bound `9.25%`），晚窗已出现 1/35 loss、ROI `-0.60%`。
- expression 是看过 Singapore forecast error 后选择的 in-sample diagnostic；结论仅为
  `inconclusive/shadow_candidate`，不恢复 live。

## 数据快照

- observed at：2026-08-04 15:02 CST（07:02 UTC）。
- production manifest：canonical DB route `healthy`；`runtime/weather.db` 与
  `/Volumes/jrs/pm_agents/runtime/weather.db` 为同一 device/inode。manifest 仅有一个与本单无关的
  running checkout SHA warning。
- raw lineage：`/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/`
  的 `state.json`、`events.jsonl`、`orders.jsonl`、`execution_journal.jsonl`；订单文件覆盖到
  2026-08-04 04:10 UTC。
- settlement-facing evidence：直接读取 Weather.com/WU historical API，station `WSSS`、native unit C；
  2026-08-04 当前返回 30 rows，日高暂为 31°C，覆盖到 04:00 UTC。Polymarket market 仍
  `closed=false`，因此今天只能报 provisional/MTM，不能报 realized PnL。
- 历史分母：当前 `persistent_candidate_margin_v5` 自 2026-07-16 起，按
  `(target_date, previous exact bracket)` first signal 去重，共 19 expressions / 14 target dates；
  WU 覆盖 19/19，missing bracket 0。实际 live expression 5 个，其中 4 个已结束、今天 1 个未结算。
- 本次未 sync、未 rebuild。canonical DB 正在高 IO/refresh，CLOB coverage gate 未在本次查询窗口完成；
  因此不发布 canonical fee-adjusted live PnL。

## 直接结论

今天的 31 NO 是 `source-basis false cross`，不是正常的 exact-bracket overshoot 判断失误。
S24 在 12:04 新加坡时间打印 32.1°C，但结算面对的是 WSSS/WU；当前 WSSS/WU 日高仍是 31°C。
两次/三次 persistence 只证明 S24 自己持续高于 31.5/31.7，不证明 WSSS/WU 已离开 31 档。

Singapore CrossNO 不应继续真实下单；保留 MSS collector，并把 S24 只作为 WCIR 概率特征/zero-notional
shadow。这个动作依据是既有 source contract 已明确 `cross_station_basis_unverified`，不是从 19 个小样本做
city alpha keep/cut。

## S24 → WSSS 累积 basis 更新（2026-07-08..2026-08-03）

同一 PIT raw 分母扩展到 27 个日期后，S24 仍适合做快特征，不适合直接充当 settlement cross：

- S24 distinct observations：4,556 / 27 dates；实际新内容 cadence p50/p90 `5/6m`，观测到 first-seen
  lag p50/p90 `2.41/3.44m`。虽然历史字段名是 `DBT 1M F`，线上公开 API 实际主要每约 5 分钟产生一个新值。
- WSSS routine/special reports：1,295 / 27 dates；report cadence p50/p90 `30/30m`，report 到 collector
  first-seen lag p50/p90 `2.90/7.06m`。S24 observation 到下一份 WSSS report 的 lead p50 `14.93m`。
- 逐 observation 对下一份 WSSS：exact whole-C `3,305/4,533 = 72.9%`，within ±1°C
  `4,493/4,533 = 99.1%`；raw signed difference mean `+0.006°C`、median `0°C`、p10/p90
  `-0.6/+0.6°C`。所以它是很好的连续温度 proxy。
- 但 S24 observation 已暗示越过 prior WSSS running max 的 300 行里，下一份 WSSS 只确认
  `197/300 = 65.7%`。这是 observation 行描述性分母，含同日重复状态，不能当独立交易胜率。
- city-day 层，S24 rounded daily max 落在 WU/WSSS winning bracket 只有
  `17/27 = 63.0%`；S24 假越过到更高档的 terminal false-cross 为 `8/27 = 29.6%`。
  旧窗口是 `9/14 = 64.3%`，增加数据后没有改善。

这不是一个可用固定 offset 修正的稳定 bias。S24 raw daily max - WU native max 均值约 `+0.115°C`，
但范围为 `-2.3..+1.0°C`；主要误差来自 S24 较密采样捕获短峰、WSSS/WU 半小时采样是否恰好捕获该峰，
再叠加 whole-degree reporting lattice。研究表达应改成
`P(next/final WSSS leaves bracket | S24 path, minutes_to_next_report, slope/pullback, basis)`，而不是
`S24 rounded cross => previous bracket NO`。

官方 source census：S24 是公开的 Changi Meteorological Station / Upper Changi Road North 自动站，
不是远距离城市 proxy；WSSS 航空气象公开产品为 half-hourly METAR 加按条件触发的 SPECI。Changi
Voice/Datalink ATIS 的 MET INFO 同样半小时更新；MSS Aviation Weather Services Portal 需要注册的
airline/flight-operator 账户，公开目录只声明 METAR/SPECI 等产品，没有发现可公开接入的 WSSS
分钟级原始温度传感器 feed。

## 今天订单血缘

| 层 | 证据 |
|---|---|
| source event | 2026-08-04 04:04 UTC，MSS S24 `32.1°C`，rounded 32 |
| settlement-facing state | 最新 routine WSSS METAR 04:00 UTC `31°C`；running max 31 |
| trigger | candidate previous bracket 31；连续 qualifying observations=3（要求2）；`>=31.5` 且 latest `>=31.7`；距下一份预计 METAR 19.968m |
| contract conflict | profile 同时写明 `live_eligible=false`、`cross_station_basis_unverified`、`calibration_status=blocked_cross_station_basis` |
| override | `user_approved_tiny_live_trial_cross_station_basis_pending` 清空 live blocker，使订单进入 live |
| first execution | 10 shares @ 0.849，CLOB order `0xb68a…6d57`，0 fill 后立即取消 |
| hot retry | 约 2 秒后 ask 跳到 0.928；13.5 shares matched，order `0xd3b7…3460`，principal `$12.501`，tx `0x33d6…055` |
| current result | WU 日高暂为 31；market 尚未关闭。NO best bid 0.051、ask 0.077，按 bid 的 principal MTM 为 `$0.6885`，未实现 principal PnL `-$11.8125`，未含 fee |

根因有两层：

1. 信号层把 cross-station S24 decimal high 当成 WSSS/WU exact-bracket 的确定 invalidation；persistence
   没有解决 station basis。
2. 授权层已知道 profile 不具备 live eligibility，却用 pending-basis trial override 放行。今天的错误正是该
   已知风险实现。

0.849 未成交后在 0.928 追入放大了损失半径，但不是方向错误的根因。

## Singapore 历史触发与正确率

当前规则 first-expression 分母如下；correct 定义为 WU 最终/当前最高温不等于所买 NO 的 exact bracket。

| target date | previous NO brackets | WU max °C | correct |
|---|---|---:|---:|
| 07-16 | 30 | 32 | 1/1 |
| 07-17 | 29 | 30 | 1/1 |
| 07-19 | 31 | 32 | 1/1 |
| 07-21 | 28 | 31 | 1/1 |
| 07-22 | 29 | 30 | 1/1 |
| 07-23 | 28 | 30 | 1/1 |
| 07-24 | 28 | 32 | 1/1 |
| 07-26 | 28 | 30 | 1/1 |
| 07-27 | 30 | 32 | 1/1 |
| 07-30 | 29, 30 | 31 | 2/2 |
| 07-31 | 31 | 32 | 1/1 |
| 08-01 | 29, 30 | 33 | 2/2 |
| 08-03 | 29, 30 | 33 | 2/2 |
| 08-04 | 29, 30, 31 | 31 provisional | 2/3 provisional |

- signal expressions：18/19 correct = **94.7% provisional**。
- terminal city-day（每天只看最后一档）：13/14 correct = **92.9% provisional**。
- 实际进入 live 且成交的 unique expressions：07-19、07-22、07-26、07-30、08-04，共 **5 次**；
  前四次正确，今天暂错，故 **4/5 = 80.0% provisional**。
- 高 signal accuracy 不等于正 alpha：错误事件更容易在价格还没完全重估时成交，且今天成交成本 0.926，
  一次错误会吞掉许多高价 NO 的小盈利。

## 漏斗、三门与动作

- signal funnel：current-policy first expressions 19 / 14 dates → live order keys 5 → matched expressions 5。
- evidence funnel：WU coverage 19/19 → historical settled 18 expressions + today provisional 1 → live fills 5。
- significance：FAIL（14 dates，低于 10 active days/30 settled fills 的 fill 门槛，且未做相对 market 的日期 bootstrap）。
- baseline：FAIL/NA（没有同 rows executable market baseline/proper probability score）。
- forward：FAIL（live forward 今天出现 source-basis false cross；今天仍未结算）。
- statistical conclusion：`inconclusive`；不得据此声称 Singapore 有 confirmed alpha。
- operational action：停止 Singapore CrossNO 的 real-money expression；继续 collector + feature-only shadow。
  这是执行既有 `live_eligible=false` source contract，不是统计 city cut。

8 环覆盖：①描述性切片=覆盖；②统计推断=不足；③信号判别=覆盖；④概率分布=缺；⑤执行微结构=今天覆盖；
⑥容量=仅 15-share cap；⑦组合相关性=未评；⑧同分母 market baseline=缺。

## 15:36 CST 成交总盈亏补充

- CLOB coverage gate：`gate_pass=true`；DB/cache fill id 双向差异 0，fee unknown 0。
- canonical settled：7 fill rows / 5 markets，cost `$47.624999`、fee `$0.11699`、
  fee-adjusted realized PnL **`+$2.369121`**。
- 2026-07-30：1 fill / cost `$14.10` / fee `$0.04230`；canonical settlement 尚为空，但 WSSS/WU
  最终 31°C 已证明 30 NO 获胜，对应 fee-adjusted PnL **`+$0.857700`**。
- 因此截至 7/30 的全部已知已结束成交合计：**`+$3.226821`**。
- 今天 13.5 shares 的 matched fill 尚未进入 canonical fill cache/fact_trades；raw principal `$12.501`，
  Weather fee curve estimate（按 5 位精度）`$0.04625`。15:36 CST NO book 为 bid/ask `0.231/0.385`，按 bid 的
  open MTM PnL **`-$9.428750`**；与历史已结束合并为 **`-$6.201929`**。这不是 realized PnL。
- 若今天 31 NO 最终输，累计 fee-adjusted 约 **`-$9.320429`**；若最终赢，累计约 **`+$4.179571`**。

refresh 影响：15:31 CST bounded canonical refresh 正常退出，但今天 matched order 被 authenticated sync
检查后仍未生成 fill，7/30 settlement 也未补入；所以本节明确分开 canonical realized、WU-confirmed pending
settlement 和 raw open fill，没有把缺口伪装成 canonical 数字。

## 19:18 CST 生产动作

- 用户明确授权停止 Singapore CrossNO live；S24 collector 保留。
- Mac production checkout commit：`ec27e65111010d8b2adad4459b36358a8f167f4e`。
- `fast_source_prev_no_trial_v1` 经 production controller 重启；PID `48748`。
- post-state：live cities = Busan/Helsinki/Seoul/Tokyo；shadow cities 包含 Singapore；Singapore policy
  `shares_per_trade=0`、`max_shares_per_market=0`，且移除 cross-station live override。
- pre/post manifest canonical sessions 无缺失；target runtime healthy；S24 latest observation 继续更新。
- 重启前后 authenticated CLOB 查询：今天 Singapore 三个 token 的 open orders 均为 0。
