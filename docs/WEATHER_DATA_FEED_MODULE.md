# Weather Data Feed Module

Status: current-source
Updated: 2026-06-19
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
| `observation_clock.py` | METAR cadence、obs age、pre-update blackout guard |
| `observation_sources/` | 多源官方观测层架子：source alias、METAR parser、AviationWeather/AWC cache parser、adapter router 协议 |
| `observation_sources/iem.py` | IEM ASOS 参数构造、本地日 CSV 解析、温度观测抽取 |
| `snapshot_protocol.py` | 标准 snapshot 字段和 legacy alias normalization |
| `models.py` | SourceProfile、ObservationRecord、RunningMaxState、MarketSnapshotRecord 等共享 dataclass |

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

3. **稳定后**: 再拆独立部署。
   - 可以拆成独立 git repo 或独立 systemd service。
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

待推进:

- 从抢单 bot 抽出真正的 `official_observation_feed` fetcher：IEM、NOAA tgftp、HKO、weather.gov/Synoptic、LDM 的拉取、缓存、source latency 和 failover。
- 给数据模块增加 CLI: `weather-data-feed snapshot-health`, `source-profiles audit`, `scan-plan`。
- 在 N100 上为数据层增加独立 health/status 文件，再让策略 loop 只消费健康的数据产物。

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
