# Remaining Realtime Source City Audit v1

Status: `collector/shadow research only`; no live authorization

## 结论

上一轮只列了 Tokyo、Helsinki、Seoul、Busan、Singapore，确实漏了已经有 raw 数据的 Ankara、Istanbul、TelAviv、HongKong/Co-WIN，以及不能混入 HongKong 官方口径的 Shenzhen proxy；Moscow 和 Austin/Dallas/Houston 也已有 source-event 数据，但还没有可裁决的 source-first-seen → fresh-book 分母。

本轮建议只继续三条：

1. **HongKong/HKO official**：结算语义最干净，继续独立 official-lock shadow；当前问题不是准确度，而是官方确认时没有可买盘口。
2. **TelAviv/IMS**：public 10-minute 路径样本小且慢，只保留 collector；拿到 IMS 1-minute token 后才值得重新评估速度。
3. **Ankara/MGM**：比 Istanbul 干净，但 first-seen 约 20 分钟且 persistent 只有 2 次，只作低优先级 feature shadow。

Istanbul/MGM、Co-WIN、Shenzhen/Lau Fau Shan 都不进入 exact-bracket live：前者单次 cross 对下一 routine report 只有 4/16，Co-WIN persistent 对 HKO 只有 14/21，Shenzhen 是 cross-station proxy，不是 ZGSZ 结算站。

## 已有数据的遗漏城市

| city/source | source days | cadence / first-seen | source → settlement evidence | PIT book evidence | action |
|---|---:|---:|---|---|---|
| Ankara/MGM | 11 | 10m / 19.57m | single 11/15；persistent next-report 2/2；settled bracket 2/2 | runner 只到 7/14：6 first crosses，2 个有 ask，1 个在 cap 内 | `low-priority feature shadow` |
| Istanbul/MGM | 9 | 10m / 19.39m | single 4/16；persistent next-report 2/3；settled proxy 3/3 | runner 只到 7/14：8 crosses，7 个有 ask，2 个在 cap 内；两次可交易事件均未得到 previous-NO 结算 | `negative control / collect only` |
| TelAviv/IMS public | 7 | 10m / 16.46m | single 4/4；persistent next-report 1/1；settled proxy 1/1 | runner 只到 7/14：4 crosses，1 个有 ask，0 个在 cap 内 | `collect; wait for IMS 1m` |
| HongKong/HKO official | 8 completed overlapping days | 10m / 8.1m | 官方结算 feed；历史 source profile 的 Daily Extract 映射 27/27 | dedicated runner 7/11..7/16：24 locks、22 fresh books、6 个有 ask，全部 0.999，0 个 ≤0.93 | `official-lock shadow only` |
| HongKong/Co-WIN 6087 | 8 completed days | 1m / 2.2m | same-minute n=687，r=0.9415，MAE 0.71C；daily max 差 -0.6..+2.9C；bias-adjusted persistent 14/21 | 不能把更早的跨档当 HKO 已观察事实 | `probability feature only` |
| Shenzhen/HKO Lau Fau Shan | 4 runner days | 10m / ~8m | source profile 明确为 `cross_station_proxy`；ZGSZ/WU API basis 尚未解决 | 117 runner rows/15 nominal executable rows没有结算语义 | `blocked_basis`; 不评估交易胜率 |

`settled proxy` 对 Istanbul/TelAviv 指规则指定的 WRH/Synoptic station series，不是 WU。Ankara 使用规则站 LTAC 的 WU whole-degree bracket。所有比例都是 first event / distinct observation 口径，不把轮询重复行当样本。

## 其他已经找到的城市

| state | cities/source | 当前判断 |
|---|---|---|
| active source-event, evidence gap | Moscow/WRH-Synoptic | 规则结算映射历史 62/62，但当前没有 authoritative first-seen → direct-book episode；继续 collector |
| active source-event, label gap | Austin/Dallas/Houston/Synoptic 5m | first-seen 约 8.8m，但 primary switch 后缺同分母 AWC label，不能给 cross precision |
| P1 credential pending | Paris/Météo-France 6m；Amsterdam/KNMI 10m | 同结算机场、当前最值得拿 key 后接 zero-notional collector |
| P2 credential/contract pending | Madrid/AEMET；TelAviv/IMS 1m；Wellington/MetService 1m | adapter/profile 已有；先补 raw first-seen，不预设 edge |
| defer | Munich/DWD；Toronto/ECCC；Taipei/CWA；Jeddah/NCM | Munich/Toronto 没速度，Taipei/Jeddah 先解决 station/access basis |

## Atlanta negative control

所有上述源继续沿用 Atlanta 2026-07-17 admission test：必须分别报告 source print、同 timestamp routine/official reference、最终结算 bracket、terminal false cross，以及 correct/false 两组 fresh-book/fill 分母。QC pass、same-airport、persistent 或“官方源”都不能单独批准 previous-bracket NO。

## Funnels

- Signal funnel（distinct observation/event）：raw observation → causal first-seen → single cross → persistent cross → first city-day expression。
- Evidence funnel（event/city-day）：routine/official label → rules settlement bracket → fresh direct book → executable expression → canonical fill。MGM/IMS 的 book runner 在 7/14 停止、HKO dedicated book runner 在 7/16 停止，之后属于 coverage gap，不是策略过滤。

## Data snapshot and verification

- high-frequency raw: `/Volumes/jrs/weather_data_feed_service_runtime/output/high_frequency_observations/high_frequency_observations.jsonl`, mtime `2026-07-19 10:58 Asia/Shanghai`。
- canonical DB: `runtime/weather.db`, mtime `2026-07-19 10:52 Asia/Shanghai`；`settlement_outcomes` 覆盖到 `2026-07-17`，`fact_signal_candidates` 到 `2026-07-20`，`fact_trades` 到 `2026-07-19`。
- reruns: `research_high_frequency_strategy_eligibility_v2.py`、`research_mgm_ims_cross_book_v1.py`、`research_cowin_hko_alignment_v1.py`；HKO direct-book denominator 读取 dedicated runner `events.jsonl`。
- source script corrections: eligibility city set 补回 Ankara；Co-WIN 报告剔除未结束的 Hong Kong local day，覆盖天数、lead、false count、daily-max range 改为由当前数据计算，不再硬编码旧快照。

## Contract

significance=NA; baseline=same-time routine/official source + fresh market; forward=FAIL; conclusion=HKO/TelAviv/Ankara keep shadow, no new live city
