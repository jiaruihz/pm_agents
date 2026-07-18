# Cross-NO 城市机制与 Event-Time Repricing v1

## 数据快照

- 数据源：Mac JRS raw `/Volumes/jrs/weather_data_feed_service_runtime/output/source_event_ladder_repricing_shadow` + canonical `/Users/deepsleep/projects/pm_agents/runtime/weather.db`；未同步、未 rebuild。
- 生成时间：`2026-07-18T08:12:20.807406+00:00`；collector event 覆盖 `2026-07-13..2026-07-18`，`219` 个去重事件、`9968` 行盘口。
- DB mtime `2026-07-18T08:02:26.657999+00:00`，fact built `2026-07-18T08:02:26.459826+00:00`，fact_trades `4616`。
- unsettled `0/4616`；missing_bracket `55`；settlement_outcomes 截止 `2026-07-15`。本研究不发布 fill PnL，标签直接来自 `settlement_outcomes`；这 55 个全局 fact missing_bracket 未进入本研究分母。

## 大白话结论

首轮最干净的是 **Helsinki / FMI 的 proxy cross**，不是 authoritative cross。FMI 本身更新约 10 分钟一次，系统 first-seen 延迟约 3.2 分钟，相对下一份 METAR 的中位领先约 **10.3 分钟**。但这 10 分钟不是可交易窗口：盘口 collector 在系统 first-seen 后才建 episode，首个可用 direct NO quote 的中位延迟是 **35.017 秒**，而且大多数旧档 NO 已经在 `0.99` 附近或没有 ask。

冻结在 collector 启动前的 Helsinki calibration：single cross 最终旧档失效 `18/18`，95% Wilson 下界 `0.824`；下一份 METAR 立即确认只有 `14/18`。因此它不能按确定性票算。collector 窗口内 single cross 有 `16` 个 / `5` 天，但结算对齐只有 `6` 个 / `2` 天；按 Wilson 下界、官方 fee 再加 1c buffer，只有 `2` 个事件仍为正。最好的一行是 `2026-07-17 old 24 NO ask=0.44, conservative edge=0.361795/share, top size=14.43`，它是 forward collector 捕获但尚未结算的一行，只能记机会，不能升级策略。

persistent cross 更准但更慢：collector 窗口只有 `2` 个对齐事件；一个首次可见 ask 已到 `0.994`，另一个没有卖盘。也就是说，**等两次观测确认后，盘口基本已经吃完 edge，5/10-share 可执行容量为 0 或不稳健。**

authoritative 轨道本轮没有冻结城市：Moscow 的 62/62 规则映射只证明结算语义，不证明 WRH first-seen 比盘口快；当前 ladder collector 没有 Moscow episode。HKO 当前两条事件属于 Shenzhen/Lau Fau Shan cross-station proxy，不是 HongKong authoritative event。这个轨道只能 `continue_collector`，不能拿历史规则回放冒充 event-time 证据。

失败层次很明确：Helsinki single 主要卡在 **盘口反应快 + 样本/settlement 覆盖薄**；persistent 进一步卡在 **确认延迟**。不是天气特征还不够多，也不是需要再加价格 hard gate。

## 城市选择

完整 scorecard 见 `city_mechanism_scorecard.csv`。选择不看 ROI：

- proxy 主样本：Helsinki/FMI，exact-1C、同站 alternate feed、first-seen 和 direct book 都存在。
- 负面对照：Istanbul/MGM，只用于 source/detection 对照；约 19 分钟 detect lag、single next-METAR precision 约 0.21，明显不适合深入盘口。
- authoritative：Moscow/HongKong 均不冻结，原因是当前 source-first-seen→direct-book 分母为 0。

## 两种机制分开 verdict

| mechanism | denominator | 结果 | verdict |
|---|---:|---|---|
| authoritative_invalidated | 0 event-time direct-book events | 语义干净，但没有 first-seen→book 证据 | `continue_collector` |
| proxy single cross, Helsinki | 16 events / 5 dates | 少数早期 quote 有 edge；结算对齐仅 2 天 | `continue_collector` |
| proxy persistent cross, Helsinki | 2 events | ask 0.994 或无 ask；确认后容量消失 | `continue_collector` |

significance=FAIL；baseline=NA（只有两个 settled event-to-book dates）；forward=FAIL；conclusion=`inconclusive`。不改 live。

## 准确度与 false cross

- Helsinki frozen pre-collector single：next official `14/18`，final settlement `18/18`。
- Helsinki frozen pre-collector persistent：next official `7/8`，final settlement `8/8`。
- 全部 Helsinki/Istanbul next-official false crosses 已写入 `false_crosses.csv`。天气原因字段在该 event ledger 中不完整，所以原因保持 `unknown/source-basis-or-transient-spike`，没有事后编故事。

## Event-time 与容量口径

- decision clock 是 source `first_seen/detect`，不是 report timestamp。
- `0/30/60/120/300s` 只有实际 quote 落在容差内才算 supported；collector 后期变成 600 秒 cadence 的行不会伪装成 30 秒数据。
- fee 使用 Weather 官方 `0.05 * price * (1-price)` 每股；另报 +1c execution buffer。
- collector 只有 top ask/size，没有完整多档深度，所以只能证明 top-level 1/5/10-share capacity；不能声称 5/10-share VWAP。

## 双漏斗

详见 `funnels.csv`。盘口缺失、结算截止和 authoritative 事件缺失都列在 `coverage_gaps.csv`，没有作为策略筛选条件。

## 8 环覆盖

- covered：source basis、PIT first-seen、next-official/final label、direct book、official fee、top-level capacity、event-time curve、双漏斗。
- partial：显著性（日期太少）、天气 false-cross 解释（字段未进入 collector ledger）、capacity（仅 top level）。
- missing：authoritative source-to-book episodes、同城 non-cross matched baseline、10 个新 active dates frozen forward、真实 fills。

## 动作

保持当前 zero-notional collector，冻结 Helsinki/FMI 的 `single` 与 `persistent` 两个定义分别记账；不要把 persistent 当 entry gate，也不要扩城市。先补到至少 10 个新的 settled active dates，并让 Moscow/HKO authoritative collector 产生真实 first-seen→direct-book episode，再复核。当前不启动新 shadow、不改 live、不下单。
