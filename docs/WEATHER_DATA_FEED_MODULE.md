# Weather Data Feed Module

Status: current-source
Updated: 2026-06-30 source-events signal boundary
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
| `snapshot_protocol.py` | 标准 snapshot 字段和 legacy alias normalization |
| `models.py` | SourceProfile、ObservationRecord、RunningMaxState、MarketSnapshotRecord 等共享 dataclass |

`weather_data_feed_service/` 当前是同仓库的生产服务层，负责把数据包产物写成稳定文件协议：

| 服务输出 | N100 路径 | 消费者 |
|---|---|---|
| `output/source_events/latest.json` + append-only `sources.jsonl` | `~/projects/weather_data_feed_service_runtime/output/source_events/` | source/orderbook timing monitor、METAR crossing prev-NO bot |
| `output/observations/latest.json` | `~/projects/weather_data_feed_service_runtime/output/observations/` | current-YES / regime-routed 等需要 5 分钟级 observation cache 的策略 |
| `output/forecast_enrichment/latest.json` + append-only `forecast_enrichment.jsonl` | `~/projects/weather_data_feed_service_runtime/output/forecast_enrichment/` | forecast-quality / current-YES / NO carry / reheat/overshoot research 的预测侧 shadow feature capture |
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
  `scripts/ops/start_mac_weather_data_feed_jrs_tmux.sh` 在 `tmux -L weather-jrs` session
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

消费者默认：

```text
weather_source_orderbook_timing_monitor.py
  --source-input source-events
  --source-events-path ~/projects/weather_data_feed_service_runtime/output/source_events/latest.json

weather_metar_cross_prev_no_shadow.py
  --signal-input source-events
  --source-events-path ~/projects/weather_data_feed_service_runtime/output/source_events/latest.json
```

如果 `source_events/latest.json` stale 或缺 city/source，消费者应该显式报 `source_event_missing` /
`source_event_wrong_date`，不要静默回退到直抓天气源。需要临时排查上游 source 时，才手动切到 `live-fetch`。

## 生产数据验证计划

每次数据层 / snapshot / current-YES runner 改动后，在 N100 跑:

```bash
cd ~/projects/pm_agent
.venv/bin/python scripts/ops/weather_data_feed_prod_health_check.py \
  --snapshot-dir ../weather-predict/output/paper_snapshots \
  --runtime-root runtime/weather_edge_v1
```

检查内容:

- `weather-predict` 最新 snapshot 是否满足 `weather_data_feed_snapshot_v1` 协议；
- snapshot record 是否缺字段、无法 normalize、或出现 `(city,target_date,token_id,bracket)` 重复；
- snapshot 是否 stale，默认阈值 45 分钟；
- current-YES split telemetry 是否 JSON 损坏、缺关键字段、或同一 decision key 重复；
- current-YES split live order 文件是否有重复 `order_id` 或重复 `(strategy_instance,city,target_date,token_id,side)`。

验收口径:

- `status=ok`: 可以继续让策略消费；
- `status=warn`: 字段/重复没坏，但存在 stale snapshot、summary stale、或非致命重复风险；
- `status=fail`: 协议、JSON、snapshot 重复或 order_id 重复等结构性问题，先修数据再谈策略信号。

抢单/测速链路改动后额外验证:

```bash
systemctl --user start weather-data-feed-source-events.service
systemctl --user list-timers --all | grep weather-data-feed-source-events

cd ~/projects/pm_agent
TIMING_MONITOR_MARKET_PROXY=http://127.0.0.1:18089 \
  .venv/bin/python scripts/ops/weather_source_orderbook_timing_monitor.py cycle \
  --cities Shanghai \
  --sources profile_primary aviationweather_cache_csv \
  --source-input source-events \
  --source-events-path ~/projects/weather_data_feed_service_runtime/output/source_events/latest.json \
  --bracket-radius 0 --max-workers 2

METAR_CROSS_MARKET_PROXY=http://127.0.0.1:18089 \
  .venv/bin/python scripts/ops/weather_metar_cross_prev_no_shadow.py cycle \
  --dry-run --cities Shanghai \
  --signal-input source-events \
  --source-events-path ~/projects/weather_data_feed_service_runtime/output/source_events/latest.json \
  --obs-source aviationweather_metar --max-workers 1
```

通过标准：source-events latest 足够新，`rows=80 / ok=80 / cities=40`（随城市池调整可变），timing monitor
有 `source_rows>0` 且能 join book，crossing dry-run cycle 写出 `signal_input=source-events` /
`obs_source_input=data_feed_source_events`，且 `errors=0`。

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
