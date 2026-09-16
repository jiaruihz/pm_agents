# Cross NO V2 逐笔血缘：2026-08-31 Chicago / NYC

> 目标日期：2026-08-31  
> 策略：`cross_no_v2_metar_v1`  
> 实例 release：`1ac0edf698bd6e0dc57d66b3f862683064b63dfb`  
> 观察时间：2026-09-01T01:33:05Z  
> 结论口径：raw authenticated exchange response + METAR.ws evidence SQLite；canonical `fact_trades` 尚未 materialize 这三笔 fill，因此本报告不发布 fee-adjusted realized PnL。

## 数据快照

| 项目 | 值 |
|---|---|
| strategy runtime | `/Volumes/jrs/weather_data_feed_service_runtime/output/cross_no_v2_metar_polymarket_48h_v2` |
| source evidence | `runtime/metarws_polymarket_48h_20260901_v2/evidence.sqlite3` |
| Chicago / NYC authenticated fills | 3（NYC 1、Chicago 2） |
| 按 official 当日最高温判断的错误 fills | 2（NYC 1、Chicago 第二笔 1） |
| 错误 fill shares / principal | 10 shares / 7.50 USD（不含最终 canonical fee） |
| canonical settlement/fact join | 未覆盖；不得把当前 raw 推断写成 canonical realized PnL |

## 全链路表

### NYC：买入 `76-77°F NO`

| 阶段 | 时间（UTC / 北京） | 证据 |
|---|---|---|
| 造成 source peak 的 HF 事件 | 2026-08-31 17:25 / 2026-09-01 01:25；本机首次 actionable 17:37:37 / 01:37:37 | `METAR_WS_HFMETAR`, KLGA, 26°C = 79°F |
| official 报文 | 报文 17:51 / 01:51；本机首次 actionable 17:54:12 / 01:54:12 | `KLGA 311751Z ... 25/18 ... T02500178 ...`，25°C = 77°F |
| 实际触发 candidate 的 HF 事件 | 报文 17:45 / 01:45；transport 17:58:37.574 / 01:58:37.574 | 当前事件本身 25°C = 77°F；但策略沿用此前 `source_running_max=79°F`，与 `prior_official_running_max=77°F` 比较 |
| execution book | fetched 17:58:40.322 / 01:58:40.322 | 5 shares NO 可在 0.68 成交 |
| authenticated fill | exchange response recorded 17:58:41.514 / 01:58:41.514 | order `0x36bc...2fb3`，BUY 5 shares，price 0.68，principal 3.40 USD，status `matched` |
| 后续 official 确认 | 报文 18:51 / 02:51；首次 actionable 18:54:34 / 02:54:34 | 仍为 25°C = 77°F |
| 当日 official max | — | 25°C = 77°F，命中 `76-77°F YES`，因此该 NO 为错误方向 |

异常：下单时使用的“跨档值”不是触发事件的当前温度，而是约 21 分钟前 HF 事件留下的 running max。且 25°C 的 authoritative official 报文已在下单前约 4 分 29 秒进入本机。

### Chicago：买入 `86-87°F NO`（第二笔）

| 阶段 | 时间（UTC / 北京） | 证据 |
|---|---|---|
| 首个 31°C HF 事件 | 2026-08-31 18:35 / 2026-09-01 02:35；本机首次 actionable 18:42:57 / 02:42:57 | `METAR_WS_HFMETAR`, KORD, 31°C = 87.8°F，native lattice 88°F |
| authoritative special METAR | 报文 18:44 / 02:44；本机首次 actionable 18:46:48 / 02:46:48 | `KORD 311844Z ... 30/20 ... T03000200`，30°C = 86°F |
| 实际触发 candidate 的 HF 事件 | 报文 18:40 / 02:40；transport 18:48:18.222 / 02:48:18.222 | 31°C = 88°F；策略比较 `source_running_max=88°F` 与 `prior_official_running_max=86°F` |
| execution book | fetched 18:48:20.566 / 02:48:20.566 | 5 shares NO 可在 0.82 成交 |
| authenticated fill | exchange response recorded 18:48:21.419 / 02:48:21.419 | order `0x2d36...7126`，BUY 5 shares，price 0.82，principal 4.10 USD，status `matched` |
| 后续 regular METAR | 报文 18:51 / 02:51；首次 actionable 18:53:27 / 02:53:27 | 29°C = 84.2°F；不改变 official 当日 max |
| 当日 official max | — | 30°C = 86°F，命中 `86-87°F YES`，因此该 NO 为错误方向 |

异常：30°C 的 authoritative special METAR 已在下单前约 1 分 33 秒进入本机；随后迟到的 18:40 HF 31°C proxy 仍被当作确定跨档信号，直接以 0.82 买入 NO。

## 不属于错误单的 Chicago 第一笔

Chicago 18:22:04Z 买入 5 shares `84-85°F NO`，price 0.96，principal 4.80 USD。official max 后来达到 86°F，因此这笔方向正确；不能与上面的 `86-87°F NO` 合并成一笔错误交易。

## 四笔方向正确的对照订单

四笔全部由 `METAR_WS_HFMETAR`（raw payload `source=madis`）触发，不是 D-ATIS 或普通 official METAR。这里的“正确”按当天 official METAR max 判断；canonical settlement row 仍待 materialize。

| city | HF observation / transport | order（UTC） | 买入 | 后续首次 official 确认 | official 当日 max | 方向 |
|---|---|---|---|---|---|---|
| Chicago | 18:15 30°C / 18:22:00.684 | 18:22:04.266 | `84-85°F NO`，5@0.96 | 18:44 special 30°C，18:46:48 first-seen | 30°C=86°F | 正确 |
| Austin | 19:35 37°C / 19:47:57.794 | 19:48:00.601 | `96-97°F NO`，5@0.97 | 20:53 regular 37°C，20:55:18 first-seen | 37°C=98.6°F→99 lattice | 正确 |
| San Francisco | 19:40 18°C / 19:47:58.604 | 19:48:06.621 | `62-63°F NO`，5@0.93 | 19:56 regular 18°C，20:00:24 first-seen | 19°C=66.2°F→66 lattice | 正确 |
| Seattle | 21:30 24°C / 21:37:57.142 | 21:38:02.749 | `72-73°F NO`，5@0.99 | 21:53 regular 24°C，21:55:50 first-seen | 24°C=75.2°F→75 lattice | 正确 |

这些订单说明 HF/MADIS 并非系统性错误：有时它提前捕捉到后来被 official 确认的升温。但四笔的 observation→transport 延迟仍为约 7–13 分钟；“方向正确”不等于 source 低延迟，也不等于 0.96–0.99 的 taker 价格有足够 fee-adjusted EV。

## D-ATIS 反事实执行漏斗

截至本报告补充时，corrected runtime 中 D-ATIS 固定分母为 82 rows：66 rows 未跨入新 bracket，8 rows 缺 prior official baseline，8 rows 为严格 upward bracket cross。8 个 strict crosses 覆盖 5 个 city-days，按当天 official METAR max 判断，买旧 bracket NO 的方向均正确；但没有任何一条同时通过实际执行 gate，因此 D-ATIS 为 0 candidates / 0 orders / 0 fills。

| 分类 | strict crosses | 说明 |
|---|---:|---|
| HF 已先执行同一 economic race | 2 | San Francisco、Austin；但两条同时也缺 fresh ask / 5-share sweep，不能把 0 order 单独归因于 HF 抢先 |
| 未被 HF 抢先、盘口不可执行 | 6 | Seattle 2、Austin 1、Dallas 1、Atlanta 1、San Francisco 1；均为 fresh book top missing / insufficient five-share ask sweep |
| 可执行 candidate | 0 | 无 |

D-ATIS 并非全程没有盘口：82 rows 中有 60 rows 的一般 5-share book coverage；问题是“形成 strict cross”的 8 个时点恰好都没有可执行 NO ask。方向正确只是低样本反事实证据（8 signals / 5 city-days），不能替代真实 fill 或 fee-adjusted EV。

## 根因

1. `METAR_WS_HFMETAR` 明确只是 proxy（raw 也记录 `source_cross_is_proxy_only=true`），但 live policy 把它的 1°C 跨档当成近似确定事件，以 taker 价格直接买 NO。
2. source 采用日内 running max。NYC 当前 HF 已回落到 25°C 时，仍用旧的 26°C peak 触发；没有要求“当前触发报文自身跨档”或 authoritative confirmation。
3. authoritative official report 已先到并显示较低温度时，策略没有把这种 source/official 冲突当 blocker。两笔错误单都不是“买完以后才被真实报文反转”，而是下单前 authoritative 报文已经存在。

## HF transport 延迟拆分

`metar.obs10` 的 5 分钟表示 observation grain，不是 5 分钟 delivery SLA。目标 raw payload 明确标记上游为 `source=madis`。消息到达 `stream.metar.ws` MQTT 后，本机从 transport receive 到 actionable 只需 2.1–3.4 ms；主要延迟发生在上游 observation timestamp 到 broker 推送之间，当前证据无法继续无损拆成 MADIS ingestion 与 METAR.ws relay 两段。

| station / report | observation→本机 transport | transport→actionable |
|---|---:|---:|
| KLGA 17:25 | 757.115 s | 3.199 ms |
| KLGA 17:45 | 817.574 s | 3.441 ms |
| KORD 18:35 | 477.818 s | 2.567 ms |
| KORD 18:40 | 498.222 s | 2.084 ms |

该窗口的首次到达中位数：KLGA 800.5 s（约 13 分 21 秒），KORD 470.3 s（约 7 分 50 秒）。生产配置允许 `max_observation_delay_sec=3600`，因此这些对低延迟交易已陈旧的事件仍通过 event-time freshness gate。

## 数据完整性自检

- [x] 每笔 fill 均有 matching authenticated order response、order id、transaction hash、5-share quantity 与 actual fill cost。
- [x] source event、transport first-seen、execution book 与 order response 时钟已分开。
- [x] public trade 未被当作 own fill。
- [x] Chicago 两笔订单已拆开，未把 10 shares 合并成一笔。
- [ ] canonical `fact_trades` 尚未包含这些 fills；fee-adjusted settled PnL 待增量 materialization 后再发布。
- [ ] Polymarket settlement outcome 尚未进入 canonical settlement tables；当前“错误方向”依据当日 authoritative METAR max，而非已发布 canonical settlement row。

## 补充治理：Helsinki V1 maker fill 的成交时钟误读

该补充不是 Cross V2 订单，而是 `fast_source_prev_no_trial_v1` 的 Helsinki maker
订单 `0x45a54d8866e6d47e23aec7263ead70765f50195f36bd7599d42763909ae0c733`。
它暴露了同一套 canonical fill 时钟的治理缺口，因此与本事故一并登记。

| 时钟 | UTC | 证据含义 |
|---|---|---|
| order placed | 2026-09-01 13:02:09.665183 | 本地/exchange 提交 |
| Polygon block fill | 2026-09-01 13:02:22 | tx `0x329d90835d8392ce2353c6f02059b2ec065419e8dfc470370ae64bdd3046b33b`，block `93041806` |
| GTD expiry | 2026-09-01 13:05:53 | exchange order expiration epoch |
| canonical 原 `filled_at_utc` | 2026-09-01 13:47:18.996284 | canonical 首次发现/写入时间，不是成交时间 |

实际成交在 GTD 到期前 3 分 31 秒。误读发生是因为复盘最初把 canonical
`filled_at_utc` 当成 exchange event time，没有先核对 tx/block；当时 canonical refresh
又因 release `3d486291fa0f9552e56b1acbb9f3e925fcb208e9` 缺 `.venv/bin/python`
中断，恢复后的 MATCHED order 没有 trade timestamp，ingest 便把 reconciliation 当前时间
写入 fill。该行经济字段未受影响，但 fill latency/GTD 判断被污染。

治理口径：GTD 和 fill latency 必须按 `placed → expiry → tx/block → exact public activity
→ authenticated trade → reconciliation created_at` 的证据顺序复盘；历史修正只写
append-only `fill_timestamp_adjustments`，禁止改写原始 `fills`。

## 2026-09-01 Cross V2 live policy 切换与影响清单

已加载 release `6561553bb9b2da7ee14ea4c8350b1b95b6328b4e`：HF-METAR 继续采集并保留
shadow/lineage，但 `live_source_arms` 仅含 `metar_ws_datis`；D-ATIS 以当前报文本身跨入新
bracket 为 live signal，不再要求 0.7 margin。官方 supersession、causal ordering、fresh 5-share
book、price/cap/dedupe/pause 等执行 gate 保持不变。

该 policy 若反事实应用于切换前 runtime，会阻断 9 笔 HF exchange orders：NYC 1、Chicago 2、
Austin 1、SanFrancisco 1、Seattle 1、Amsterdam 2、Houston 1。最后一笔 Houston 在
2026-09-01 14:48:17 UTC 提交，order
`0x2fe652ea019a06e3b56991671de48bf127909c2b8dacc062c519fdcafcdcc7f8`；它发生在部署切换前，
不是新 policy 下的订单。新 policy 不撤销历史 fill，只改变后续 execution authority。

本次时间修正还发现 canonical refresh 的 release-boundary 缺口：旧启动器只把 operational
`clob_fills.jsonl` 绑定进 immutable release，未绑定 fee/price/timestamp/validity 四类 adjustment
journal，造成 journal 已 append、refresh exit 0 且 coverage gate pass，但 adjustment row 未进入 DB。
启动器现统一绑定这四类 operational journal；以后必须同时核验 DB adjustment row 和
`fact_trades` effective value，不能只看 refresh/gate 总状态。

目标 Helsinki adjustment 最终以 canonical class `exact` 物化，同时在 `evidence_json` 保留 raw
class `exact_onchain`。原始 `fills.filled_at_utc` 保持 13:47:18.996284；派生
`fact_trades.fill_ts_utc` 为 13:02:22，校正 2696.996 秒。受影响 grain 为 1 个 fill；shares
9.72、price 0.77、fee 0、cost 7.4844 均未变化，未结算 PnL 仍为空，coverage gate pass。
