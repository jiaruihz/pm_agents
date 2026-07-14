# Airport Observation Source Integration v1

Status: `collector_integration_verified`

Generated: `2026-07-15 Asia/Shanghai`

## 结论

- 已接入并实测：`AWC cache first-arrival`、Moscow `WRH/Synoptic UUWW`、DWD EDDM、ECCC CYYZ；WIS2 已有可复跑的限时 MQTT probe。
- 已完成 adapter/parser、但缺凭证：Meteo-France LFPB、KNMI EHAM、AEMET LEMD、IMS LLBG 1-minute。
- MetService NZWN 需要 API key 之外的 trial/product tenant endpoint，当前只能显式报告 `contract_required`。
- 这些变更只进入 collector/source-event 层，没有把任何新城市升级为 live。新源目前没有足够的 forward source-to-settlement-to-book 样本。
- Moscow 结算口径已解决：规则源是 WRH `UUWW`，后端为 Synoptic；2026-05-01..2026-07-12 当前 62 个已结算日按 bracket 语义重放为 `62/62`。

## 本轮接入表

| 优先项 | 站点/城市 | 名义频率 | 代码/实测状态 | 现在缺什么 | 策略价值 |
|---|---|---:|---|---|---|
| Meteo-France | LFPB Paris | 6m | `meteofrance_6m` adapter + Kelvin/C parser 已完成 | `METEOFRANCE_API_TOKEN` 或 API key；至少 10 个 active days 的 first-seen 对齐 | **高潜力**：同机场、6m，适合 next-METAR，但现在 `n=0` |
| KNMI | EHAM Amsterdam | 10m | EDR CoverageJSON adapter 已完成，WIGOS `0-20000-0-06240` | `KNMI_API_KEY`；验证实际 EDR 字段和发布延迟 | **高潜力**：同机场、10m，`n=0` |
| AWC cache | 全球 METAR | 报文事件 | 已加入 source-event 并行 first-arrival；Paris smoke 同时落 API/cache | 持续积累各路 detect timestamp；比较 API/cache/WIS2 first seen | **高**：它是 next-METAR label 与传输竞速基准，不是独立温度预测源 |
| IMS API | LLBG Tel Aviv | 1m | `ims_1m` adapter/parser 已完成，夏令时按 `Asia/Jerusalem` | `IMS_API_TOKEN`；与现有 public 10m 并行采集 | **高潜力**：同机场 1m；当前 10m 路仅 5 天小样本 |
| MetService | NZWN Wellington | 1m | source profile 与显式 `auth_required/contract_required` 状态完成 | trial、API key、tenant/product endpoint | **中高潜力**：同机场 1m，但接入成本较高，`n=0` |
| AEMET | LEMD Madrid | 文档称近实时，需实测 | 两段式 manifest/data adapter + parser 完成 | `AEMET_API_KEY`；实测 cadence/lag，不预设为 10m | **中高潜力**：同机场；频率和 first-seen 尚未验证 |
| WIS2 | 10 个目标 ICAO | 事件 | MQTT TLS 可连接；semantic topic 60s 为 0，加入 GTS `S/A` 后 60s 收到 3 条 METAR bulletin | canonical payload 三条均为 401；需 recommended 数据授权和目标站长时覆盖 | **未知**：可能改善 METAR 传输 first-arrival，不提供新的物理温度信息 |
| DWD | EDDM Munich | 10m observation | 无认证直连接入成功 | 当前 `now.zip` 在 smoke 时最新观测约滞后 8h | **低（实时）/中（历史）**：不能做 stale-book 快源 |
| ECCC | CYYZ Toronto | 当前公开为 hourly MAN | 无认证 SWOB XML 接入成功 | 找到 AUTO/更高频产品或 AMQP subscription | **低（当前 endpoint）**：小时级不领先 routine METAR |

## 美国源汇总

严格 cross 定义：两个不同 observation timestamp 均高于当前 market bracket 上沿至少 `0.5` unit，最新至少 `0.7`，且在下一 routine METAR 时钟 20 分钟窗口内。准确率分母是 2026-07-08..14 的小样本 first event，不是重复轮询行。

| 源/城市 | 实测 cadence / detect lag | persistent next-METAR | 当前价值 | 还需工作 |
|---|---:|---:|---|---|
| Synoptic 5m: Austin/Dallas/Houston | 5m / 8.8m，8d | `NA`（此前缺并行 AWC label） | **中高**，比 IEM MADISHF 快 | 本轮已补 AWC cache 并行采集；继续 forward 标注 |
| IEM MADISHF: Atlanta | 20m / 21.0m，7d | 3/3 | **shadow candidate** | 2F range expression handler + 扩样本 |
| IEM MADISHF: Chicago | 25m / 21.6m，7d | 3/4 | **shadow candidate** | 同上 |
| IEM MADISHF: Miami | 20m / 20.8m，7d | 4/4 | **shadow candidate** | 同上 |
| IEM MADISHF: San Francisco | 20m / 22.9m，7d | 5/5 | **shadow candidate** | 同上 |
| IEM MADISHF: NYC | 20m / 21.9m，7d | 3/3 | **积累** | 样本小，未过 forward/live contract |
| IEM MADISHF: LA/Seattle | 20m / 21.9-22.7m，7d | 1/3、2/4 | **低到中** | 精度不足，继续 collector |
| NOAA WRH/Synoptic | 全球可用站 | Istanbul 相对 AWC 中位 `-0.64s` | **结算/辅助源高，速度 alpha 低** | Moscow 用作规则结算源；其他城市逐站验证规则 |
| AWC API + cache | 全球 METAR | 本轮开始并行 | **基础设施高** | 形成 API/cache/Synoptic/WIS2 first-arrival 分布 |
| WIS2 recommended aviation / GTS S/A | 全球、非 Global Cache | 60s 收到 3 条 bulletin，目标站 0 | **未知** | 三条 canonical URL 均 401；需授权与目标站长时探测 |

美国源不能直接从小样本 3/3、5/5 升 live：市场通常是 2F range ladder，现有 generic exact-bracket executor 也不是正确表达。

## 全部机场/高频源目录

| 源 | 城市/站点 | 当前状态 | 对 METAR/结算的价值 |
|---|---|---|---|
| Korean AMOS runway | Seoul RKSI、Busan RKPK | adapter/历史数据已有；网页权限受限 | 真正跑道/机场自动站，物理信息高；授权恢复前不可依赖 |
| Singapore MSS | Singapore S24/WSSS 附近 | active collector | 1m 邻近站；basis 好但不是跑道/结算站 |
| JMA AMeDAS | Tokyo RJTT | active collector | 10m 同机场/附近，适合 next-METAR shadow |
| FMI | Helsinki EFHK | active collector | 10m 同机场；7d persistent next-METAR `7/8`，settled `8/8`，仍属小样本 shadow |
| HKO + CoWIN | Hong Kong HKO/6087 | active shadow collector | HKO 是独立结算体系；不能套 generic METAR-cross |
| MGM | Ankara LTAC、Istanbul LTFM | active collector | 10m，但 detect lag 约 19m；Istanbul 速度优势很小 |
| IMS public | Tel Aviv LLBG/Lod | active collector | 10m、detect lag 约 16m；样本太小 |
| NOAA MADIS HFMETAR | 11 个美国机场 | active collector | 机场报文家族，不是跑道；城市差异大 |
| Synoptic station timeseries | Austin/Dallas/Houston/Moscow 等 | active source-event | 5m 美国站有价值；Moscow 是规则源；需 AWC 并行 label |
| AWC API/cache | 全球 ICAO | active source-event | 官方 METAR first-arrival/label 基准 |
| Meteo-France 6m | Paris LFPB | auth-ready | 高潜力，缺 token |
| KNMI 10m | Amsterdam EHAM | auth-ready | 高潜力，缺 key |
| IMS 1m | Tel Aviv LLBG | auth-ready | 高潜力，缺 token |
| AEMET | Madrid LEMD | auth-ready | 中高潜力，缺 key 且 cadence 待测 |
| MetService 1m | Wellington NZWN | contract-ready scaffold | 中高潜力，需 commercial trial/tenant endpoint |
| DWD 10m CDC | Munich EDDM | direct connected | 文件发布太慢，历史价值高于实时 |
| ECCC SWOB | Toronto CYYZ | direct connected | 当前公开 endpoint 小时级，实时价值低 |
| CWA | Taipei 466920 | adapter，缺认证 | 参考站/结算 basis 需另审 |
| NCM | Jeddah OEJN | profile/adapter 未完成授权流 | 潜力未知 |
| AEROWEB | Paris LFPB | 登录流未自动化 | 被 Meteo-France 官方 6m 路径替代优先级 |
| WIS2 | 全球 | bounded probe | 只研究传输 first-arrival，不视为独立 forecast/source-basis |

## Moscow 结算源修复

根因不是“查不到结算源”，而是 2026-06-18 已完成的规则审计没有同步回 source-profile generator，旧 JSON 仍保留 `blocked_unresolved_settlement_basis`。

本轮修复：

1. `source_profiles.json` 改为 `non_wu_source_by_rules`。
2. `official_station_or_feed=https://www.weather.gov/wrh/timeseries?site=UUWW`。
3. primary=`synopticdata_timeseries`；fallback=`aviationweather_metar,noaa_tgftp_station_txt`。
4. 当前 settlement labels 重放：62 settled days、62 paired、62 matched。
5. source-event smoke 已同时抓到 Synoptic/AWC/TGFTP；仍保持 `live_eligible=false`，因为 source-to-book forward latency 未达标。

## 双漏斗

Signal funnel：官方/机场 raw observation -> distinct observation minute -> bracket-aware persistent cross -> first city-day signal。新授权源当前都停在 raw coverage 之前，不能给 precision。

Evidence funnel：PIT fetch timestamp -> concurrent AWC METAR label -> PIT orderbook -> settlement bracket -> executable expression/fill。AWC cache 修复的是第二层 coverage gap，不是策略筛选条件。

## 验证

- focused tests: `41 passed`。
- source smoke: DWD `ok` 24 rows；ECCC `ok` 1 row；5 个凭证源均显式 `auth_required`。
- source-event smoke: Paris AWC API/cache 两路；Moscow Synoptic/AWC/TGFTP 三路，全部 `ok`。
- WIS2: semantic aviation topic 单独 60 秒为 0；宽 topic 20 秒有 86 条，确认 broker 正常；semantic + GTS `S/A` 60 秒得到 3 条 aviation bulletin，目标 ICAO 为 0，三个 canonical URL 均返回 401。
- Mac JRS collector 重启后：source-event `103` rows、`102 ok/1 non-ok`；Austin 已同时落 Synoptic/AWC cache，Moscow 已落 Synoptic/AWC API/AWC cache/TGFTP。
- 全局 prod health check 的 source/snapshot freshness 正常，但总状态仍为 `fail`，原因是既有 live-order duplicate telemetry 与非交易城市 Houston weather-state 缺失；不是本轮 source 接入失败。

## 官方入口

- Meteo-France DPObs: https://confluence-meteofrance.atlassian.net/wiki/spaces/OpenDataMeteoFrance/pages/853639294/API+Cibl+e+Donn+es+d+Observation
- KNMI 10-minute dataset: https://dataplatform.knmi.nl/dataset/access/10-minute-in-situ-meteorological-observations-1-0
- AWC Data API: https://www.connect.aviationweather.gov/data/api/
- IMS API: https://ims.gov.il/en/ObservationDataAPI
- MetService 1-minute API: https://developer.metservice.com/docs/api-catalog/1min-obs-api/
- AEMET OpenData: https://opendata.aemet.es/dist/
- ECCC SWOB: https://eccc-msc.github.io/open-data/msc-data/obs_station/readme_obs_insitu_swobdatamart_en/
- WIS2 overview: https://community.wmo.int/site/knowledge-hub/programmes-and-initiatives/wmo-information-system-wis/wis2-overview
- NOAA WRH UUWW: https://www.weather.gov/wrh/timeseries?site=UUWW
