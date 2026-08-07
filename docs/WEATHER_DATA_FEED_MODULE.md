# Weather Data Feed Module

Status: current-source
Updated: 2026-07-14 high-frequency airport source audit
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; WEATHER_REPO_BOUNDARY.md; WEATHER_SYSTEM_CONTRACT.md

## 结论

新数据层先放在 `pm_agents` 同一个 git 仓库里，作为独立 Python package:

```text
weather_data_feed/
```

理由很直接：现在 `pm_agents` 是有 git 审计、测试和 N100 `pm_agent` 部署链的那一侧；`weather-predict`
当前仍存在非 git worktree 的生产现实。先把共用数据逻辑收进一个可测试、可 review、可提交的包，比新建一个孤立 repo 更稳。

这不等于部署上永远绑死。目标边界是:

```text
weather_data_feed  ->  标准化城市日历 / source profile / official obs / forecast / market snapshot 协议
weather-predict    ->  调用数据层生产 snapshot/cache，不包含策略或下单
pm_agent           ->  消费标准数据，做策略、风控、下单、事实表和看板
```

后续要做到“数据模块不变，只改策略部署”，靠的是包边界和服务边界，不一定靠第一天就拆 git repo。

## 当前落地范围

`weather_data_feed` 当前包含:

| 模块 | 职责 |
|---|---|
| `city_calendar.py` | IANA timezone、DST、本地交易日、按城市扫描 target dates |
| `source_profiles.json` | 52 城 official source / station / live eligibility registry |
| `source_registry.py` | 读取 source profile，兼容 settlement-source registry 格式 |
| `source_policy.py` | 从 profile 生成 live-capable city configs |
| `market_brackets.py` | bracket label 解析与 contains 判断 |
| `observation_cache.py` | fast observation cache 协议、latest pointer 读写和 `(city,target_date)` 索引 |
| `observation_clock.py` | METAR cadence、obs age、pre-update blackout guard |
| `observation_sources/` | 多源官方观测层架子：source alias、METAR parser、AviationWeather/AWC cache parser、adapter router 协议 |
| `observation_sources/iem.py` | IEM ASOS 参数构造、本地日 CSV 解析、温度观测抽取 |
| `observation_sources/fetchers.py` | 数据层 fetcher：AviationWeather METAR、AWC cache、IEM ASOS、NOAA TGFTP、weather.gov latest、Synoptic、CheckWX |
| `forecast_sources.py` | 预测增强数据层：Open-Meteo multi-model per-model 温度、Open-Meteo hourly weather context、AviationWeather TAF、TAF peak-window signal、vertical profile heating/suppression signal |
| `runway_sources.py` | 机场跑道级观测增强层：AMSC AWOS、韩国 AMOS 跑道点位气温；用于 microclimate/METAR-WU 对齐研究，不作为 settlement truth |
| `high_frequency_observation_sources.py` | 机场/官方高频观测增强层：AMOS、NOAA MADIS HFMETAR、Singapore MSS、JMA、HKO、CoWIN、MGM、IMS、FMI 等；需要 key 的 CWA/KNMI/NCM/AEROWEB 显式返回 auth/config 状态 |
| `snapshot_protocol.py` | 标准 snapshot 字段和 legacy alias normalization |
| `models.py` | SourceProfile、ObservationRecord、RunningMaxState、MarketSnapshotRecord 等共享 dataclass |

`weather_data_feed_service/` 当前是同仓库的生产服务层，负责把数据包产物写成稳定文件协议：

| 服务输出 | N100 路径 | 消费者 |
|---|---|---|
| `output/source_events/latest.json` + append-only `sources.jsonl` | `~/projects/weather_data_feed_service_runtime/output/source_events/` | source/orderbook timing monitor、METAR crossing prev-NO bot |
| `output/observations/latest.json` | `~/projects/weather_data_feed_service_runtime/output/observations/` | current-YES / regime-routed 等需要 5 分钟级 observation cache 的策略 |
| `output/forecast_enrichment/latest.json` + append-only `forecast_enrichment.jsonl` | `~/projects/weather_data_feed_service_runtime/output/forecast_enrichment/` | forecast-quality / current-YES / NO carry / reheat/overshoot research 的预测侧 shadow feature capture |
| `output/runway_observations/latest.json` + append-only `runway_observations.jsonl` | `~/projects/weather_data_feed_service_runtime/output/runway_observations/` | 跑道点位气温与 METAR/WU 报文温度的对齐、滞后、bias 建模研究 |
| `output/high_frequency_observations/latest.json` + append-only `high_frequency_observations.jsonl` | `~/projects/weather_data_feed_service_runtime/output/high_frequency_observations/` | 机场/官方高频参考站与 METAR/WU/source-events/settlement outcome 的对齐、滞后、bias 建模研究 |
| `output/wu_history_latency/latest.json` + append-only `wu_history_latency.jsonl` | `~/projects/weather_data_feed_service_runtime/output/wu_history_latency/` | WU/weather.com history 行的 first-seen 延迟、row 特征、与同 timestamp METAR/source-events 的对齐；首轮历史行标记为 `backfill`，不计入真实延迟 |
| `market_books/latest.json` + append-only `batches/` | `/Volumes/jrs/weather_data_feed_service_runtime/market_books/` | 唯一 raw Gamma/CLOB book 正本；hot token 优先、其余 ladder 后台补齐，不依赖 forecast/METAR |
| `market_ladder_snapshots/` | `/Volumes/jrs/weather_data_feed_service_runtime/market_ladder_snapshots/` | event/rung/two-sided 完整市场分布；研究按 batch completeness 读取 |
| `output/fast_source_stale_book/latest.json` + append-only `events.jsonl` / `quote_snapshots.jsonl` | `~/projects/weather_data_feed_service_runtime/output/fast_source_stale_book/` | 高频源跨过 METAR/WU running max 后，记录 `t-1 NO` 是否仍未被盘口重定价；telemetry only，不下单 |
| `output/hko_official_tminus1_no_live/latest.json` + `events.jsonl` / `opportunities.jsonl` / `orders.jsonl` | `/Volumes/jrs/weather_data_feed_service_runtime/output/hko_official_tminus1_no_live/` | HK 专用 live：HKO Observatory 已到 floor(T) 后，只买 exact `T-1 NO`；不消费 VHHH/METAR，不买 current YES。 |
| full market snapshot outputs | `~/projects/weather_data_feed_service_runtime/output/` | mirror / analysis / dashboard sync |

旧路径:

```text
src/strategies/weather_edge_v1/official_observation_feed/
src/strategies/weather_edge_v1/tools/official_observation_clock.py
```

现在是兼容入口，re-export `weather_data_feed`。这样 current-YES 等现有脚本不用马上改 import，也不会继续让新数据逻辑长在 strategy 目录下面。

## Repo 与部署策略

当前按三步推进:

1. **已完成**: 同仓库独立包。
   - 所有数据层代码进 `weather_data_feed/`。
   - 旧 strategy 路径只保留兼容 re-export。
   - `pm_agent` 新策略直接 import `weather_data_feed`。

2. **已在 N100 接入**: 让 `weather-predict` 使用同一数据层。
   - 当前生产 `weather-predict` 通过 sibling `~/projects/pm_agent/weather_data_feed` 接入。
   - 保持现有 snapshot 输出路径和字段兼容。
   - 使用 `scripts/ops/weather_data_feed_parity_check.py` 验证新 snapshot 协议字段。

3. **正在推进**: 拆成独立运行服务。**具体执行计划见 [WEATHER_DATA_FEED_STEP3_MIGRATION_PLAN.md](WEATHER_DATA_FEED_STEP3_MIGRATION_PLAN.md)**
   （独立 checkout `~/projects/weather_data_feed_service/`，weather-predict 退役转 dormant）。
   - 第一阶段已落地同 repo 的 `weather_data_feed_service/`，不是新 git repo；部署上会是独立 checkout + 独立 systemd + 独立 runtime。
   - 后续是否拆新 git repo，等服务边界和产物协议稳定后再判断。
   - 输出标准 JSON/JSONL/cache/latest pointers。
   - `pm_agent` 只读数据产物；策略部署不需要重启数据采集。

是否拆 repo 的判断标准不是“模块名字变独立”，而是:

- `weather-predict` 和 `pm_agent` 都已经通过稳定 API/文件协议消费它；
- schema version、health report、latest pointer、source failover 都稳定；
- 数据模块发布一个 SHA 后，策略侧可以只升级策略代码而不动数据采集。

## 必须保持的字段

所有 snapshot record 至少要有:

```text
city
target_date
market_local_date
city_local_date_at_snapshot
snapshot_ts_utc
```

这些字段解决的是之前美洲市场被北京时间/机器日期滚动误过滤的问题。`target_date` 是市场结算日期，
`city_local_date_at_snapshot` 是采集时城市本地日，两者不能硬等同。

Market snapshot 必须保留 Gamma event 的完整 bracket inventory，包括 `outcomePrices` 接近 `0/1` 的
near-binary siblings。价格是否可交易属于 strategy/execution policy，不能在数据层用 ask/price 阈值删档；
否则 running max 会被错误判成 `below_ladder` / `top_two`，full-ladder 和 relative-step 模型都会在错误几何上运行。

## 迁移状态

已完成:

- `pm_agent` active scripts 直接 import `weather_data_feed`；旧 strategy 路径继续作为兼容 re-export。
- `weather-predict/paper_snapshot.py` 本机副本和 N100 生产副本都通过 sibling `pm_agent` / `pm_agents` path 使用同一个 `weather_data_feed` 包。
- `paper_snapshot.py` 的 city scan dates、city local date、settle UTC 和 METAR local-day 口径已迁到 IANA timezone / DST。
- 新增 `scripts/ops/weather_data_feed_parity_check.py`，用于检查 snapshot 是否满足 `market_local_date` / `city_local_date_at_snapshot` 等协议字段。
- 已预留 `weather_data_feed/observation_sources/` 架子，供抢单 bot / current-YES / NO carry / station-basis 共用多源 METAR adapter。
- 第一批旧解析已迁入新模块：timing monitor、station-basis、current-YES 复用共享 METAR/AviationWeather/AWC/IEM parser；网络请求、proxy、orderbook 和策略判断仍留在原脚本。
- N100 `pm_agent` 已部署到 `bb8d21b9`，`weather-predict-snapshot.service` 已生成并验证 `weather_data_feed_snapshot_v1` snapshot。
- `weather_data_feed_service/` 已在 N100 以独立 checkout + 独立 systemd timer 并行运行，写入
  `~/projects/weather_data_feed_service_runtime`；旧 `weather-predict` 暂时保留为并行/回滚来源。
- 本机镜像同步入口 `scripts/ops/sync_weather_remote.sh` 已支持 `--market-source=weather-data-feed`，可从
  `~/projects/weather_data_feed_service_runtime` 同步 snapshot / orderbook / cache 到原 canonical mirror：
  `runtime/weather_edge_v1/market_data/`。pm_agent 的 signal builder 仍消费这个本地 mirror，不迁入数据服务。
- 2026-07-06 起 Mac 临时生产的实际 runtime 在 `/Volumes/jrs/weather_data_feed_service_runtime`，旧
  `~/projects/weather_data_feed_service_runtime` 是 symlink；本地 canonical mirror
  `runtime/weather_edge_v1/market_data` 也是指向 `/Volumes/jrs/pm_agents/runtime/weather_edge_v1/market_data`
  的 symlink。macOS LaunchAgent 写外置卷会触发 `Operation not permitted`，当前 data-feed 用
  `scripts/ops/start_mac_weather_data_feed_jrs_tmux.sh` 在 `tmux -L weather-data-feed-jrs` session
  `weather_data_feed_jrs` 中常驻。
- 多源 METAR fetcher 已从 timing monitor 迁入 `weather_data_feed/observation_sources/fetchers.py`。当前 timing monitor
  的实际 source fetch 路径已委托给数据模块；orderbook timing、价格反应和策略判断仍留在原脚本。
- 新增 `scripts/ops/weather_observation_source_cadence_audit.py`，用于持续记录 city/source 的 report time、first-seen time、
  source age、fetch latency 和 estimated cadence。单次 cycle 可看当前 source age；要回答“10:00 的报文 10:xx 才拿到”
  必须让 `loop` 跑过多个观测更新周期。
- 新增 `weather_data_feed_service observations` 和 `weather-data-feed-observations.timer`，用于生产 5 分钟级
  `weather_data_feed_observation_cache_v1` latest cache：
  `~/projects/weather_data_feed_service_runtime/output/observations/latest.json`。current-YES 优先消费该 cache；
  full paper snapshot 里的 `metar_latest_*` 只作为 cache 文件不存在时的兼容回退。
- 新增 `weather_data_feed_service source-events` 和 `weather-data-feed-source-events.timer`，用于生产抢单/测速用
  `source_events/latest.json`：每行是一个 city/source/station 的最新观测事件，包含 `source_report_ts_utc`、
  `local_detect_ts_utc`、`detected_after_report_sec`、`temp_c`、`raw_metar` / `raw_payload_hash`、`payload_hash`、
  source profile 审计字段和 `changed_since_last`。生产 timer 用 `OnUnitInactiveSec=2min`，避免固定周期重入。
- `weather_source_orderbook_timing_monitor.py` 和 `weather_metar_cross_prev_no_shadow.py` 的默认天气信号输入已经切到
  `source-events` 文件协议。旧的直抓天气源路径只允许显式 `--source-input live-fetch` 或
  `--signal-input live-fetch` 调试使用；策略信号不再默认各自重复抓 AviationWeather/TGFTP/CheckWX 等天气源。
- 新增 `weather_data_feed/forecast_sources.py` 和 `weather_data_feed_service forecast-enrichment`，用于预测侧 shadow capture：
  - Open-Meteo multi-model per-model daily/hourly temperature（ECMWF、AIFS、GFS、HRRR、NBM、NAM、GraphCast、AI-GFS、ICON、GEM、JMA、AROME 等）；
  - Open-Meteo hourly weather context（shortwave、dew point、pressure、10m/180m wind、precip probability、cloud cover、CAPE、CIN、lifted index、boundary layer height）；
  - AviationWeather TAF 原文和 peak-window 云雨、低云底、风向切换 signal；
  - vertical profile heating/suppression signal。
  `forecast_sources.py` 也提供 PIT forecast-run primitives：`fetch_open_meteo_single_run`
  （精确 archived run，带 `requested_run_time_utc` / `issue_time_utc` / `decision_time_utc`）、
  `fetch_open_meteo_previous_runs` 和 `fetch_open_meteo_historical_forecast`。历史策略研究默认应优先使用
  `single_run`；generic `historical_forecast` 只能作为显式 fallback，且 metadata 会标记 `pit_exact=false`
  除非调用方提供可审计的 `issue_time_utc`。
  该产物只落盘为研究/feature capture，不改变现有 snapshot、策略 selector 或 live 下单逻辑；进入策略前必须走 fact-table 同分母回放和 shadow 验证。
  研究脚本应通过 `weather_data_feed.load_forecast_enrichment` / `index_forecast_enrichment` 读取该产物，避免各自重复 live-fetch Open-Meteo/TAF。
  Mac tmux loop 预留了 `WEATHER_DATA_FEED_FORECAST_ENRICHMENT_ENABLED=1` 开关，默认关闭；开启后写入
  `output/forecast_enrichment/latest.json` 和 append-only `forecast_enrichment.jsonl`。
- 新增 `weather_data_feed/runway_sources.py` 和 `weather_data_feed_service runway-observations`，用于跑道点位观测 capture：
  - AMSC AWOS：北京、上海、广州、成都、重庆、武汉、青岛的 runway-point air temperature、RVR/MOR、风、湿度和原始 METAR；
  - 韩国 AMOS：Seoul/RKSI、Busan/RKPK 的跑道级气温，并保留页面里的 METAR 温度锚点；
  - 这些字段是机场 microclimate feature，不是 WU/官方结算源替代。研究时应按 city/station/time 与 `source-events`
    中 METAR-like / WU-like 观测对齐，建模 `runway_temp - metar_temp`、`runway_temp - wu_temp`、滞后和日内 bias。
  - AMSC 需要网页登录态 `sessionId`。本地/生产不要把明文写进 git；放在 data-feed service checkout 的 `.env`：
    `WEATHER_DATA_FEED_AMSC_SESSION_ID=<sessionId>`。sessionId 里若有 `$$`，Mac loop 会按 `.env` 原文字面量重读该 key，避免 shell 展开污染。
    Mac tmux loop 可用
    `WEATHER_DATA_FEED_RUNWAY_OBSERVATIONS_ENABLED=1` 打开周期采集，默认 interval 是 60 秒。
- 新增 `weather_data_feed/high_frequency_observation_sources.py` 和 `weather_data_feed_service high-frequency-observations`，
  用于机场/官方高频参考站 capture。当前覆盖：
  - 韩国 AMOS（Seoul/RKSI、Busan/RKPK）；
  - NOAA MADIS HFMETAR（美国 11 个机场站，当前通过 IEM `MADISHF` family 轻量接入）；
  - Singapore MSS S24、JMA AMeDAS RJTT/Haneda、HKO、CoWIN 6087、MGM Ankara/Istanbul、IMS Lod、FMI Helsinki；
  - CWA Taipei、KNMI Amsterdam、NCM Jeddah、AEROWEB Paris 已进入统一 registry，但无 key/账号时只产
    `auth_required` / `not_implemented` 状态，不静默当作可用数据。
  Mac tmux loop 默认每 60 秒调度一轮 curated direct sources：
  `amos_runway noaa_madis_hfmetar singapore_mss jma_amedas hko_obs cowin_obs fmi mgm ims_lod`。
  其中 NOAA MADIS/IEM、JMA、FMI、MGM、IMS 默认加 300 秒 source-level cadence，避免 60 秒主 loop 对慢更新/
  易限流源重复拉取；cadence 内跳过的 source/city 会从上一份 `latest.json` 保留最近记录，并写入
  `skipped_cadence_jobs` / `cadence_preserved_rows`。CWA Taipei、KNMI Amsterdam、NCM Jeddah、AEROWEB Paris
  仍保留在 registry，但无 key/账号或未完成实现时不进入默认 loop，避免把 `auth_required` / `not_implemented`
  噪声混进实时链路。
  独立 fast-observation tmux loop 默认只在每个城市本地 `06:00 <= hour < 22:00` 采集，Seoul/Tokyo 当前通过
  `--always-active-cities` 全天保留；窗口由 producer
  按 city timezone 逐 job 判断，而不是按单一 UTC cron 停整条服务。可用
  `WEATHER_FAST_OBS_ACTIVE_LOCAL_START_HOUR` / `WEATHER_FAST_OBS_ACTIVE_LOCAL_END_HOUR`
  调整。夜间/凌晨跳过会写入 `skipped_inactive_jobs`，不把源错误伪装成空数据。
  `weather_data_feed_service/scheduling.py` 是 runway/high-frequency 生产器共用调度层，当前统一提供 local daytime
  window、UTC parse 和 source min-interval filtering；后续新增机场源应复用这层，而不是在各 producer 里各写一份 cron/window 逻辑。
  这些产物统一使用 `weather_high_frequency_observation_v1`，研究脚本
  `scripts/analysis/forecast_quality/research_high_frequency_settlement_alignment_v1.py`
  可把它们和 `source-events` 的 METAR-like/WU-like 行、`settlement_outcomes` 的最终落点拼接。
  它们不是 `output/observations/latest.json` 的替代，也不自动进入 live 策略决策。

### 高频同机场源接入审计（2026-07-14）

这里严格区分 `runtime active`、`adapter only` 和 `research candidate`。进入 registry、source profile 或能手工
fetch 都不等于生产正在采集。

| 数据源 / 机场 | 当前状态 | 2026-07-14 运行证据或缺口 | 研究结论 |
|---|---|---|---|
| MADIS OMO / Synoptic HF-ASOS；KLGA/KMIA/KDAL/KSEA/KLAX/KSFO 等美国机场 | **部分 active** | `noaa_madis_hfmetar` 已覆盖 11 城，但当前走 IEM `MADISHF`；当天这些站观测 timestamp 的中位间隔约 20–25 分钟。真正 Synoptic adapter 已实现，生产 primary 目前仅 Austin/Dallas/Houston；手工探针显示 KLGA/KMIA/KLAX/KSEA/KSFO 最近 4 小时各有约 50–53 条、约 5 分钟 cadence，当前 WRH token 请求对应 `ICAO1M` station 均为空。 | 当前 IEM 路径不能称为 1 分钟 OMO。Synoptic 文档称完整 OMO 使用 `ICAO1M` 网络、正常延迟约 2–5 分钟且仍是 experimental；下一步应接正式 token/`*1M` station，保留 IEM 只作 fallback。 |
| NOAA WRH timeseries；LTFM/UUWW | **adapter active，城市生产状态不同** | 现有 `synopticdata_timeseries` 实际是从 WRH 页面取 token 后请求 Synoptic API，不是独立 NOAA 观测源。LTFM 已进入 source-events research capture；同一探针 LTFM 最近 4 小时 8 条、UUWW 7 条。Moscow 仍未进入生产，因为 settlement basis 尚未解释。 | Istanbul 可继续做 source-specific first-seen；Moscow 只能先采研究数据，不能因 endpoint 可用就解除结算口径 block。 |
| AWC API + AWC cache | **runtime active** | `aviationweather_metar` 与 `aviationweather_cache_csv` 已进入 `source-events`；AWC cache 官方每分钟更新。 | 已具备两路 first-seen 对照，不需要再建一套 METAR 明细。应在同一 event envelope 内比较 direct API/cache 的 `local_detect_ts_utc`。 |
| WIS2 aviation first-arrival | **research candidate，未实现** | 仓库无 WIS2 subscriber、topic routing、BUFR/IWXXM decoder 或消息去重状态。航空数据在 WIS2 通常属于 recommended data，可能有 license/access token，且不一定进入 Global Cache。 | 价值在第三条独立传输路径，不是新的温度口径。先做一个全球 broker topic inventory 和 5–10 个机场的可访问性/延迟 probe，再决定是否常驻。 |
| Météo-France 6 分钟；Paris/LFPB | **research candidate，未采集** | 当前 `aeroweb` 只是 auth-required placeholder；它与 Météo-France `obs-infrahoraire-6m_{station}` API 不是同一个实现。 | P1。先确认 LFPB 的 Météo-France station id 与 API access，再实现 6 分钟 adapter。 |
| KNMI 10 分钟；Amsterdam/EHAM (06240) | **adapter only，未采集** | registry 和 `fetch_knmi` 占位已存在；无 `KNMI_API_KEY`，NetCDF/EDR parser 未接。 | P1。官方同时提供 Open Data API、EDR 和 MQTT；优先 EDR + MQTT first-seen，避免轮询整包 NetCDF。 |
| FMI 10 分钟；Helsinki/EFHK (100968) | **runtime active** | `fmi` 当天 77 个 distinct observation timestamp，全部为 10 分钟间隔。 | 已接好，不重复建设；继续做 FMI first-seen -> METAR/WU/盘口 lineage。 |
| DWD 10 分钟；Munich/EDDM (station 01262) | **research candidate，未采集** | 官方 `now` 文件存在且含 10 分钟值；本次探针 15:20 UTC 看到的最新观测为 14:50，约慢 30 分钟。DWD 公共说明将 `now` 描述为小时更新；未发现可作为 1 分钟机场气温的公开产品。 | P2，不应按“1 分钟快源”排优先级。先连续测一周 file mtime/last-observation lag，再判断是否比 AWC 有领先。 |
| AEMET 10 分钟；Madrid/LEMD | **research candidate，未采集** | 当前只有 AWC METAR。AEMET OpenData 有 conventional current observations，需 API key；自动站可提供 10 分钟数据，但尚未验证 LEMD 的 station id、实测 cadence 和发布时间。 | P1 probe，验证同机场和 first-seen 后再写 adapter。 |
| IMS 10/1 分钟；Tel Aviv/LLBG (Lod 225) | **10 分钟 runtime active；1 分钟未接** | 当前 `ims_lod` 使用公开 `hourly_observations_full` payload，实测 43 个 distinct timestamp、中位 cadence 10 分钟；没有配置 IMS `APIToken`，未使用官方 10/1-minute API。 | 现有 10 分钟链可继续 shadow；申请 token 后再对照 1 分钟 API 是否确实更早，注意官方文档特别说明时间字段按 UTC+2 解读。 |
| MetService 1 分钟；Wellington/NZWN、Auckland/NZAA | **未采集，商业授权** | 仓库无 adapter/key。官方 1-minute API 包含 AWS/机场跑道传感器、约在每分钟后 30–40 秒可用，但当前仅 commercial access。 | 数据质量和延迟价值高，但先询价/申请 trial；拿不到授权不投入生产实现。Auckland 还需先补 city/source profile。 |
| ECCC SWOB/AMQP；Toronto/CYYZ | **未采集** | 仓库无 SWOB parser/AMQP subscriber。ECCC 支持 SWOB `AUTO-minute` 与 AMQP first-arrival，但 CYYZ 2026-07-14 目录实际只有 hourly `MAN`，约每整点后 0–2 分钟发布。 | AMQP 能减少轮询 overhead，但 CYYZ 本身不是分钟温度快源；只在 first-arrival METAR/SWOB 传输研究中列 P2，不作为高频 cross 源。 |

官方入口：[Synoptic HF-ASOS](https://docs.synopticdata.com/services/high-frequency-asos)、
[AWC Data API](https://www.connect.aviationweather.gov/data/api/)、
[WIS2 recommended-data access](https://docs.wis2box.wis.wmo.int/en/latest/user/recommended.html)、
[Météo-France 6-minute observations](https://donneespubliques.meteofrance.fr/client/document/descriptiftechnique_observations_donneespubliques_v2_20250315_403.pdf)、
[KNMI 10-minute dataset](https://dataplatform.knmi.nl/dataset/access/10-minute-in-situ-meteorological-observations-1-0)、
[DWD 10-minute air temperature](https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/DESCRIPTION_obsgermany_climate_10min_air_temperature_en.pdf)、
[AEMET OpenData](https://opendata.aemet.es/dist/)、
[IMS 10/1-minute API](https://ims.gov.il/en/ObservationDataAPI)、
[MetService 1-minute API](https://developer.metservice.com/docs/api-catalog/1min-obs-api/)、
[ECCC SWOB/AMQP](https://eccc-msc.github.io/open-data/msc-data/obs_station/readme_obs_insitu_swobdatamart_en/)。

接入顺序：先把 Synoptic 美国 `*1M`、KNMI EDR/MQTT、Météo-France 6 分钟、AEMET LEMD 做成独立 probe；
FMI/IMS 继续现有采集；DWD/ECCC 先量化真实发布延迟；MetService 等授权；WIS2 先做 topic/access inventory。

策略可用性对比见
[`2026-07-15-high-frequency-strategy-eligibility-v2.md`](analysis/2026-07/2026-07-15-high-frequency-strategy-eligibility-v2.md)：
按 observation/report first-seen 对齐下一份去重 METAR；美国 2°F range 与摄氏 exact bracket 分开映射，严格信号要求
连续两次高于当前 bracket 上沿至少 0.5 market unit、最新至少 0.7，并处于下一 routine METAR 时钟前后 20 分钟。
报告同时列 next-METAR 与真实 winning-bracket 结算命中，明细位于
`docs/analysis/2026-07/generated/high_frequency_strategy_eligibility_v2/`。该报告只授权 shadow feature research；
美国 Synoptic 5 分钟源仍缺并行 AWC label，且 range executor 未实现，不直接改变 live city pool 或下单参数。

- 新增 `scripts/ops/weather_wu_history_latency_monitor.py` 和
  `scripts/ops/start_weather_wu_history_latency_monitor.sh`，用于记录 WU/weather.com history 每个 hourly row 第一次被我们看到的时间：
  - 输出 `output/wu_history_latency/latest.json` 和 append-only `wu_history_latency.jsonl`；
  - 字段包含 `valid_time_local/utc`、`first_seen_at_utc`、`first_seen_lag_sec`、`first_seen_type`、
    WU row 天气特征、日内 running max、payload hash、fetch latency、proxy、以及同 timestamp METAR 的
    `metar_report_ts_utc` / `metar_detect_ts_utc` / `wu_minus_metar_c`；
  - 首次启动时当天已存在的 WU rows 会标记 `first_seen_type=backfill`，只用于口径对齐，不用于估计真实发布延迟；
    真实延迟从 monitor 常驻后新出现的 `live_seen` rows 开始算。
- `scripts/ops/weather_fast_source_stale_book_observer.py` 用于把高频机场/参考站温度跨档事件和盘口反应对齐；
  当前运行入口由 production contract 登记为 `weather_fast_source_stale_book`，只通过 controller 管理：
  - 输入：`output/high_frequency_observations/latest.json`、`output/source_events/sources.jsonl`、
    latest paper snapshot 的 market/token 映射、latest orderbook snapshot 的 quote fallback；
  - 触发：同一 `city,target_date` 下 `source_round_c > metar_running_max_round_c`；
  - 交易表达：记录 `t_minus_1_no_bracket_c = source_round_c - 1` 的 NO bid/ask，并同时保留 source-round / source+1 档位上下文；
  - 输出：`events.jsonl` 记录首次跨档事件，`quote_snapshots.jsonl` 在 follow window 内每轮记录盘口，`latest.json` 给人工巡检；
  - 默认每 60 秒跑一轮，`fresh_scope=t_minus_1_no`，优先 fresh CLOB book，fresh 缺失时用 orderbook snapshot 作为
    `quote_basis=snapshot` fallback；若盘口代理不可用，会显式写 `quote_basis=missing` / `fresh_error`。
  - Polymarket market access 走统一 market-proxy helper（默认 `WEATHER_DATA_FEED_MARKET_PROXY` /
    `WEATHER_PREDICT_MARKET_PROXY` / `POLYMARKET_PROXY_URL`，缺省 `http://127.0.0.1:7890`），HTTP client 使用
    `trust_env=False`，避免全局 `HTTP_PROXY` / `ALL_PROXY` 污染“直连/代理”诊断。天气/机场源默认直连，不跟随 market proxy。
  该 watcher 是 shadow/telemetry，不提交订单、不碰钱包；任何 live buy 都必须另走策略部署与资金安全评审。
- 生产 observation cache 必须开启 `--include-station-diff --include-fallback-sources --max-workers 4`：
  station-diff 城市是把旧 city_pool 机场修正到 Polymarket 规则/WU 结算源对应站点，不是替代口径；
  fallback 链路按 `source_profiles.json` 展开。默认 AviationWeather 城市为
  `aviationweather_metar -> aviationweather_cache_csv -> iem_asos`；2026-07-04 实测 Austin/Dallas/Houston
  改用 `synopticdata_timeseries` 5 分钟主源，fallback 为 `aviationweather_metar -> iem_asos_madishf_latest -> iem_asos`。
  Denver/KBKF 当前 Synoptic 与 AviationWeather 都是 60 分钟级，仍保留 `aviationweather_metar` 主源。
- `source_profiles` 中 `official_station_diff_confirmed` 且 live eligible 的城市已进入 current-YES station map
  和 full paper snapshot METAR 拉取层：Chicago=KORD、PanamaCity=MPMG、London=EGLC、Paris=LFPB、
  Milan=LIMC、KualaLumpur=WMKK。snapshot 仍保留旧 `icao` 字段用于兼容，同时新增 `metar_icao` /
  `official_observation_station` / `settlement_source_class` 等审计字段。MexicoCity 仍是
  `default_source_watchlist`，不自动进入 live。

待推进:

- Phase 3：连续周期跑 snapshot health + daily parity；通过后把分析/看板同步默认 source 从 `weather-predict` 切到
  `weather-data-feed`。
- `paper_trades` / `research` 这类策略或研究产物不属于数据服务核心输出；迁移前继续由旧路径或 pm_agent 事实表负责。
- 继续抽 source-specific 的生产级缓存/限速/failover：IEM 当前容易 429，不适合作为高频主源；AWC cache 已支持 gzip 解压但只提供 cache 当前截面；LDM 仍是 parse-file/monitor 层，daemon 管理不进数据模块。
- 给数据模块增加 CLI: `weather-data-feed snapshot-health`, `source-profiles audit`, `scan-plan`。
- 在 N100 上为 fast observation cache 增加独立 health/status 文件；策略 loop 已优先消费标准 cache，后续再把健康门槛显式化。

## Source-Events 信号边界

`source-events` 是“最新观测事件”层，不是策略。它只回答：

```text
某城市、某 source、某 station 最新一次观测是什么；
这条观测报告时间是什么；
本机/服务第一次看到它是什么时候；
raw payload 是否相对上一轮变化。
```

生产默认源：

```text
profile_primary
aviationweather_cache_csv
```

其中 `profile_primary` 会按 `source_profiles.json` 展开成城市官方 live source，当前大多数 live-eligible 城市为
`aviationweather_metar`。`aviationweather_cache_csv` 作为同源 AWC cache 对照，用来度量 API/cache 的真实先后。

策略侧职责保持在策略里：

| 进入 source-events | 留在策略 |
|---|---|
| source profile expansion | crossing 判断 |
| report/local detect/fetch latency | running max 状态机 |
| raw METAR / payload hash / changed_since_last | 哪个 bracket 可交易 |
| station / settlement / mapping 审计字段 | orderbook 查询、notional、下单 |

`weather_metar_cross_prev_no_shadow.py` 读取 source-events 时只用最新观测 seed/更新 running max。冷启动没有 state 时，
它不会从 latest event 反推出当天历史最高温；这会漏掉已经发生过的 crossing，但避免凭不完整历史误触发真钱路径。

消费者默认使用 `source-events`，其 physical path 从 production contract/共享 loader 解析；文档和业务脚本不再写死
`~/projects/weather_data_feed_service_runtime`、N100 checkout 或第二份 fallback root。

如果 `source_events/latest.json` stale 或缺 city/source，消费者应该显式报 `source_event_missing` /
`source_event_wrong_date`，不要静默回退到直抓天气源。需要临时排查上游 source 时，才手动切到 `live-fetch`。

## 生产数据验证计划

每次数据层、snapshot 或 runner 输入合同改动后，在当前 Mac production context 验证：

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_data_feed_prod_health_check.py
```

检查内容:

- current Mac 最新 snapshot 是否满足 `weather_data_feed_snapshot_v1` 协议；
- snapshot record 是否缺字段、无法 normalize、或出现 `(city,target_date,token_id,bracket)` 重复；
- snapshot 是否 stale，默认阈值 45 分钟；
- current-YES split telemetry 是否 JSON 损坏、缺关键字段、或同一 decision key 重复；
- current-YES split live order 文件是否有重复 `order_id` 或重复 `(strategy_instance,city,target_date,token_id,side)`。

验收口径:

- `status=ok`: 可以继续让策略消费；
- `status=warn`: 字段/重复没坏，但存在 stale snapshot、summary stale、或非致命重复风险；
- `status=fail`: 协议、JSON、snapshot 重复或 order_id 重复等结构性问题，先修数据再谈策略信号。

source-event/测速链路改动也必须走 production contract 与 controller；不得恢复 N100 systemd、固定 `18089` proxy、远端
checkout 或手工常驻 cycle。验收以 manifest 登记 producer 的真实 latest、source/city coverage、first-seen clocks、book
join、结构化 errors 和下游 consumer freshness 为准，不硬编码某次城市数/row 数。需要一次性诊断时使用 read-only/dry-run
入口，不能借诊断脚本创建平行常驻进程。

## Observation Sources 迁移边界

抢单 bot 里的多源 METAR 逻辑应进入 `weather_data_feed/observation_sources/`，但只迁移数据层部分：

| 进入数据层 | 留在策略/抢单 bot |
|---|---|
| source alias / source profile expansion | orderbook timing / price reaction |
| METAR raw text parser | 是否抢单、买哪个 bracket |
| AviationWeather / IEM / TGFTP / Synoptic / LDM fetcher | notional、risk、dedup、下单 |
| source latency / fetch status / quality flags | 策略实例状态和 live execution |
| normalized `ObservationRecord` / `RunningMaxState` | PnL、settlement、策略评估 |

第一版抽取顺序：

1. 纯 parser 和 alias：已经有架子和测试。
2. AviationWeather / AWC cache / IEM / TGFTP adapter：从 timing monitor 搬纯数据代码。
3. Synoptic / weather.gov / CheckWX adapter：保留 token/proxy 配置，但输出统一 `ObservationSourceResult`。
4. LDM parser：先接 parse-file / pqcat 输出，不把 daemon 管理放进数据层。
5. 策略脚本逐个改成 `feed.day_observations(...)` / `feed.latest(...)`，旧函数保留兼容一段时间。
