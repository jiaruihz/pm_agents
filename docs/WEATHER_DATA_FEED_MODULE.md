# Weather Data Feed Module

Status: current-source
Updated: 2026-08-08 canonical producer boundary
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; WEATHER_REPO_BOUNDARY.md; WEATHER_SYSTEM_CONTRACT.md

## 结论

天气数据链按职责拆成四层，但都由同一套 production contract 管理：

```text
weather_data_feed/                     标准化数据逻辑与协议
weather_forecast_curve_collector_v1    forecast 网络 owner
weather_market_books                   Gamma/CLOB discovery + book 网络 owner
weather_data_feed_jrs                  只读 join / 消费视图 owner
pm_agent strategy runners              信号、风控、下单与血缘
```

当前生产主机是 Mac，期望拓扑和路径只从
`src/strategies/runtime/production.yaml` 解析；实际状态只认 controller、strict manifest、raw runtime 与
exchange evidence。旧 `weather-predict`、N100 systemd、`targeted_output` 和 `full_ladder_output` 均不是当前 producer、
fallback 或恢复入口。

## 模块职责

`weather_data_feed/` 是共享 Python package，不是常驻进程。新的跨策略数据逻辑只进入这里：

| 模块 | 职责 |
|---|---|
| `city_calendar.py` | IANA timezone、DST、本地交易日、target date 扫描 |
| `source_profiles.json` / `source_registry.py` | official source、station、settlement 与 live eligibility registry |
| `source_policy.py` | 从 profile 解析城市/source policy |
| `market_brackets.py` | exact bracket label、边界与 contains 语义 |
| `observation_cache.py` | observation cache/latest 协议与 `(city,target_date)` 索引 |
| `observation_clock.py` | observation cadence、age 与时钟字段 |
| `observation_sources/` | source adapter、raw parser 与统一 `ObservationRecord` |
| `forecast_sources.py` | forecast run、PIT metadata 与 weather-context fetch primitives |
| `runway_sources.py` / `high_frequency_observation_sources.py` | runway/高频参考源 adapter；不是 settlement truth |
| `snapshot_protocol.py` | snapshot schema 与明确的 legacy alias normalization |
| `models.py` | 共享 dataclass 与协议类型 |

旧 strategy import 路径只允许作为有删除时机的 compatibility re-export，不能继续承载新的 fetch、parser、cache
或 schema 逻辑。

## 当前 producer 与唯一写入目标

| owner | 网络职责 | canonical product |
|---|---|---|
| `weather_forecast_curve_collector_v1` | 独占 Open-Meteo/forecast refresh | `forecast/forecast_hourly_curves/` |
| observation/source producers（以 manifest 为准） | official observation、source first-seen、runway/HF reference | `output/observations/`、`output/source_events/` 及各自 append-only family |
| `weather_market_books` | 独占 Gamma event discovery、token map 与 CLOB book | `market_books/latest.json` + `market_books/batches/` |
| `weather_data_feed_jrs` | 不发网络请求，只 join 已落盘产品 | `strategy_snapshots/` + `market_ladder_snapshots/` |

physical runtime root 当前为 `/Volumes/jrs/weather_data_feed_service_runtime`，但业务代码不得把它另写成第二份
配置；通过 production loader 解析。`/Volumes/jrs` 还必须匹配 `production_storage_volume_uuid`，只匹配卷名不够。

### 盘口合同

- `weather_market_books` 是唯一 raw 盘口 owner；先刷新 live-token hot set，再补齐 event ladder。
- forecast、METAR 或 join cache 缺失不得阻塞 raw 盘口落盘。
- `weather_data_feed_jrs` 和任何策略/研究 consumer 不得重新请求同一盘口。
- 完整 event/rung/two-sided 分布从 `market_ladder_snapshots/` 消费，并保留 batch completeness。
- `targeted_output` / `full_ladder_output` 只读历史目录；禁止继续写入、同步或作为策略输入。
- data layer 不得按 price/ask eligibility 删除 near-binary siblings；可交易性属于 signal/execution 层。

### 天气与 source-event 合同

- forecast 网络刷新与 observation/source-event 采集各自独立，不因盘口节奏放大天气请求。
- source-event 只表达 source/station 的观测、report/event/ingested clocks、raw identity 与 first-seen；crossing、
  running max、bracket 选择和下单留在策略层。
- consumer 遇到 stale/missing/wrong-date 必须显式失败；不得静默 live-fetch 或回退 N100/history cache。
- raw unit/value 先保留，再映射到 settlement-source native lattice；高频参考源不自动成为 official/settlement truth。
- forecast model fallback 必须显式告警，不能把缺失的 ECMWF cache 静默替换成 GFS。

## 文件协议

所有 mutable target 只能有一个 owner：

- `latest.json` 是可覆盖 cache，不是历史证据；
- 历史证据写 append-only JSONL/batch；
- append-only record 必须带 `schema_version`、稳定 event/order identity、event/observed/ingested 时钟和
  writer/build identity；
- snapshot 至少保留 `city`、`target_date`、`market_local_date`、`city_local_date_at_snapshot`、
  `snapshot_ts_utc`；
- market snapshot 必须保留 Gamma event 的完整 bracket inventory；
- compatibility alias 必须可识别、可测试并注明删除条件，不能产生独立可写正本。

## Consumer 边界

| 可以进入数据层 | 必须留在策略/执行层 |
|---|---|
| source profile expansion、fetch/parser、quality/latency、normalized records | crossing、running max 交易状态、概率和 eligibility |
| forecast/observation/market 文件协议 | bracket expression、notional、risk、dedup 与下单 |
| PIT clocks、raw identity、batch completeness | order/fill/settlement/PnL 与策略实例状态 |

研究需要新 source 或新特征时，先扩共享 adapter/schema，再让 runner 读取 canonical product；不得为单个城市或策略
另建 collector、回放时钟、market cache 或 PnL 链。

## 生产入口与验证

JRS 常驻 producer 和 consumer 只通过 production controller 管理，并复用 canonical
`tmux -L weather-data-feed-jrs` context。LaunchAgent 只可请求登记过的 bounded one-shot；不得直接承载 JRS 子进程，
也不得恢复 N100 systemd、默认 tmux、screen/nohup 或私有 socket。

数据层、snapshot 或输入合同改动后至少执行：

```bash
.venv/bin/python scripts/ops/weather_production_manifest.py --strict
.venv/bin/python scripts/ops/weather_production_ctl.py health
.venv/bin/python scripts/ops/weather_data_feed_prod_health_check.py
```

验收必须同时看：目标 volume UUID、JRS read/write probe、producer freshness、schema/duplicate audit、下游 latest，
以及涉及 live 时的 process/raw order/exchange evidence。session 存在、FDA 设置为 on 或昨天 probe 成功都不能替代当前证据。

## 历史资料边界

2026-06 的 N100/systemd、`weather-predict`、targeted/full 双入口迁移过程固定保存在
[WEATHER_DATA_COLLECTION_INVENTORY.md](WEATHER_DATA_COLLECTION_INVENTORY.md) 和
[WEATHER_DATA_FEED_STEP3_MIGRATION_PLAN.md](WEATHER_DATA_FEED_STEP3_MIGRATION_PLAN.md)。它们只用于事故/演进追溯，
不得用于当前部署或恢复。更早的实现细节查 git history，不在本 current-source 文档维护第二条现状。
