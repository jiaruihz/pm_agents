# HeadA Fresh-Thesis Entry Audit v1

## 结论

- 实际首次提交 `42` 笔；其中旧 decision 超过 30 分钟 `34` 笔。
- 严格 as-of 重放后，因 stale thesis 才会提交 `2` 笔，其中进入成交链 `2` 笔、赢家 `0` 笔、settled PnL `$-0.793`。
- 不属于 D-1 HeadA 范围的 D0 首次提交 `27` 笔，其中进入成交链 `23` 笔、赢家 `2` 笔、settled PnL `$5.172`；当地 13:00 后 `0` 笔。
- 真正 D-1 是 `15` 笔 submitted / `13` 笔进入成交链 / `1` 笔赢家，settled PnL `$-0.610`。
- 合并 fresh thesis + D-1 两项修复后，历史会挡掉 `30` 笔 submitted / `26` 笔成交链；其中有 `2` 笔赢家，故不能把“挡掉的历史 PnL”当作策略增益，修复依据是时点一致性和策略分母一致性。
- 正确口径是城市当地 D-1；D0 动态天气属于 METAR/reversal 线，不应由 HeadA 首次开仓。

## 时间分布

- scope: `{"D-1": 15, "D0": 27}`
- D0 local hour: `{"0": 5, "1": 6, "2": 4, "3": 6, "4": 6}`
- snapshot archive: `2026-07-01T17:49:00Z .. 2026-07-14T15:46:00Z`
- incident cutoff: `2026-07-14T15:48:00Z`（修复后订单不进入事故影响分母）

## Stale Thesis 逐笔

| placed local | city | target | bracket | old age min | fresh p | fresh edge | reasons | filled | won |
|---|---|---|---|---:|---:|---:|---|---:|---:|
| 2026-07-02T22:53:01-05:00 | Dallas | 2026-07-03 | 98-99 | 33.0 | 0.339 | 0.229 | asof_snapshot_too_stale | 1 | 0 |
| 2026-07-13T22:50:57+08:00 | Shanghai | 2026-07-14 | 34 | 46.6 | 0.408 | 0.360 | ask_outside_05_20 | 1 | 0 |

## D0 Scope Leak 逐笔

| placed local | city | target | bracket | old age min | fresh p | fresh edge | reasons | filled | won |
|---|---|---|---|---:|---:|---:|---|---:|---:|
| 2026-07-03T00:03:50-05:00 | Houston | 2026-07-03 | 96-97 | 103.9 | 0.316 | 0.201 | not_city_local_d1;asof_snapshot_too_stale;dist_le0_or_missing | 1 | 1 |
| 2026-07-03T01:39:45-05:00 | Chicago | 2026-07-03 | 94-95 | 199.8 | 0.533 | 0.474 | not_city_local_d1;dist_le0_or_missing | 1 | 0 |
| 2026-07-03T03:25:23-04:00 | NYC | 2026-07-03 | 104-105 | 260.2 | 0.327 | 0.207 | not_city_local_d1 | 1 | 0 |
| 2026-07-03T02:51:42-07:00 | LA | 2026-07-03 | 70-71 | 193.7 | 0.607 | 0.417 | not_city_local_d1;dist_le0_or_missing | 1 | 0 |
| 2026-07-04T01:25:40+09:00 | Busan | 2026-07-04 | 24 | 143.5 | 0.306 | 0.239 | not_city_local_d1;dist_le0_or_missing | 1 | 0 |
| 2026-07-06T04:49:02+03:00 | Helsinki | 2026-07-06 | 18 | 353.7 | 0.164 | 0.054 | not_city_local_d1;edge_below_020 | 1 | 0 |
| 2026-07-06T04:49:03+03:00 | Istanbul | 2026-07-06 | 25 | 353.7 | 0.397 | 0.359 | not_city_local_d1;ask_outside_05_20 | 1 | 0 |
| 2026-07-06T03:49:04+02:00 | Munich | 2026-07-06 | 24 | 304.2 | 0.314 | 0.254 | not_city_local_d1 | 1 | 0 |
| 2026-07-07T02:04:38+09:00 | Busan | 2026-07-07 | 32 | 174.9 | 0.371 | 0.259 | not_city_local_d1 | 1 | 0 |
| 2026-07-08T03:20:38-06:00 | Denver | 2026-07-08 | 94-95 | 261.7 | 0.456 | 0.364 | not_city_local_d1 | 1 | 0 |
| 2026-07-08T02:20:39-07:00 | LA | 2026-07-08 | 78-79 | 203.0 | nan | nan | market_missing_in_asof_snapshot | 1 | 0 |
| 2026-07-09T03:25:33-04:00 | NYC | 2026-07-09 | 88-89 | 262.1 | 0.031 | -0.006 | not_city_local_d1;side_not_buy_yes;ask_outside_05_20;edge_below_020 | 0 | 0 |
| 2026-07-09T02:25:34-05:00 | Austin | 2026-07-09 | 100-101 | 204.8 | 0.259 | 0.164 | not_city_local_d1;edge_below_020 | 1 | 0 |
| 2026-07-09T00:25:35-07:00 | Seattle | 2026-07-09 | 78-79 | 89.9 | 0.305 | 0.180 | not_city_local_d1;edge_below_020 | 1 | 1 |
| 2026-07-09T01:11:20-07:00 | SanFrancisco | 2026-07-09 | 64-65 | 135.7 | 0.234 | 0.160 | not_city_local_d1;edge_below_020;dist_le0_or_missing | 1 | 0 |
| 2026-07-10T00:32:04+03:00 | Helsinki | 2026-07-10 | 21 | 96.4 | 0.295 | 0.261 | not_city_local_d1;ask_outside_05_20 | 1 | 0 |
| 2026-07-11T00:02:39+09:00 | Seoul | 2026-07-11 | 29 | 65.7 | 0.296 | 0.216 | not_city_local_d1 | 0 | 0 |
| 2026-07-11T04:32:41+02:00 | CapeTown | 2026-07-11 | 18 | 334.2 | 0.394 | 0.234 | not_city_local_d1 | 1 | 0 |
| 2026-07-11T04:32:42+02:00 | Munich | 2026-07-11 | 28 | 334.3 | 0.295 | 0.277 | not_city_local_d1;ask_outside_05_20 | 1 | 0 |
| 2026-07-12T01:04:41+08:00 | Manila | 2026-07-12 | 28 | 118.5 | 0.408 | 0.349 | not_city_local_d1 | 1 | 0 |
| 2026-07-13T01:53:05+09:00 | Seoul | 2026-07-13 | 31 | 172.4 | 0.099 | 0.009 | not_city_local_d1;edge_below_020 | 1 | 0 |
| 2026-07-13T00:53:06+08:00 | Chongqing | 2026-07-13 | 36 | 110.6 | 0.210 | 0.125 | not_city_local_d1;edge_below_020 | 1 | 0 |
| 2026-07-13T03:24:59+08:00 | Shanghai | 2026-07-13 | 32 | 262.5 | 0.402 | 0.257 | not_city_local_d1 | 1 | 0 |
| 2026-07-14T01:32:50+08:00 | Guangzhou | 2026-07-14 | 34+ | 208.5 | 0.422 | 0.277 | not_city_local_d1 | 0 | 0 |
| 2026-07-14T04:38:28+02:00 | Milan | 2026-07-14 | 36 | 336.3 | 0.385 | 0.365 | not_city_local_d1;ask_outside_05_20 | 1 | 0 |
| 2026-07-14T04:38:29+02:00 | Warsaw | 2026-07-14 | 24 | 336.3 | 0.059 | -0.012 | not_city_local_d1;side_not_buy_yes;ask_outside_05_20;edge_below_020;dist_le0_or_missing | 1 | 0 |
| 2026-07-14T03:44:31-07:00 | SanFrancisco | 2026-07-14 | 72-73 | 292.2 | 0.192 | 0.132 | not_city_local_d1;edge_below_020;dist_le0_or_missing | 0 | 0 |

## 口径

实际订单来自 live order journal 的首次 `maker_first` submitted 行；成交/结算来自 canonical `fact_trades`，按 city-target-bracket 成交链聚合。反事实只使用 `snapshot_ts <= order_ts` 的最新标准 data-feed snapshot，freshness 上限 30 分钟，规则为 D-1、BUY_YES、ask 5-20c、edge >=20pp、dist>0、至少 1 小时到结算。该报告是事故影响审计，不是策略绩效确认。

## 修复

live 首次入场改为直接读取最新标准 data-feed snapshot；缺失或超过 30 分钟显式失败，不回落 canonical 历史候选。maker lifecycle 在改价或 taker fallback 前重取同 snapshot 的概率、方向、dist 和 fee edge；thesis 失效时只撤单。所有新订单持久化 `decision_snapshot_ts_utc` 和 `source_snapshot_path`。HeadA 首次入场限定城市当地 D-1，目标日动态天气留给 METAR/reversal family。
