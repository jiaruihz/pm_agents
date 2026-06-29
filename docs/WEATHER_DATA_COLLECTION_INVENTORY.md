# Weather Data Collection Inventory

Status: current-audit
Updated: 2026-06-30 02:55 Asia/Shanghai
Source of truth: runtime audit on Mac + N100
Superseded by / Used by: WEATHER_DATA_FEED_MODULE.md; WEATHER_REPO_BOUNDARY.md; WEATHER_DATA_PIPELINE.md

## 结论

现在不是“有了 `weather_data_feed` 还故意乱写脚本”，而是迁移正在收口：

- `weather_data_feed/` 已经是共享数据逻辑包，但它本身不是运行进程。
- `weather_data_feed_service/` 已在 N100 跑，目标是唯一采集运行；其中 `observations` 和 `full-snapshot` 已接管运行态，`daily` 仍是包装旧 `weather-predict` runner。
- 旧 `weather-predict` 的 snapshot timer 已停；daily 仍保留为迁移期 fallback。
- `pm_agent` 侧仍有若干研究/策略脚本会自己拉 weather source 或 orderbook；这些不是 canonical 生产采集，但会产研究日志，容易和主数据链混在一起。
- Mac 本机当前不跑天气采集；只跑 dashboard API 和一条给 N100 用的反向 proxy tunnel。

应该统一。推荐目标边界：

```text
weather_data_feed/          = 数据逻辑：source profile、parser、fetcher、schema
weather_data_feed_service/  = 唯一采集运行：snapshot、orderbook、fast obs、source cadence
pm_agent/                   = 策略、风控、下单、盘口反应研究；不直接拥有 canonical weather fetch
Mac pm_agents/              = 分析/看板/镜像；不作为生产采集源
```

## 当前 N100 运行态（按职责分层）

审计命令时间：`n100`，`2026-06-29T15:16-16:58Z`。

### Producer 层

| 入口 | 当前状态 | 频率/方式 | 归属 | 产物 | 口径 |
|---|---:|---:|---|---|---|
| `weather-data-feed-observations.timer` | active/waiting | every 5 min | **目标 producer** | `/home/jiarui/projects/weather_data_feed_service_runtime/output/observations/latest.json` | 已经是新链路；fast obs cache，给 live 策略用 |
| `weather-data-feed-source-events.timer` | **not enabled yet** | target 2 min after inactive | **目标 source timing producer，已版本化待启用** | `.../output/source_events/{sources.jsonl,latest.json,state.json}` | `source-events`；只记录天气 source first-seen/cadence/payload hash，不拉盘口、不下单；默认直连天气源 |
| `weather-data-feed-snapshot.timer` | **disabled/inactive** | paused | **目标 targeted producer，待策略显式消费后再启用** | `.../targeted_output/paper_snapshots/` + `.../targeted_output/orderbook_snapshots/` | `snapshot-targeted`；`strategy_live` 盘口子集：current YES/current NO/D1 NO/D2 NO；与 canonical full output 隔离，避免窄快照污染全量消费者 |
| `weather-data-feed-full-snapshot.timer` | **enabled/active** | every 30 min after inactive | **目标 full producer** | `.../output/paper_snapshots/` + `.../output/orderbook_snapshots/` | `snapshot-full -- --orderbook-budget-sec 600 --orderbook-workers 8`；2026-06-29 16:45Z 验证 47 城/800 records/1600 books/non_ok=0 |
| `weather-data-feed-daily.timer` | active/waiting | daily | **目标 producer，但未迁完** | `.../cache/pm_history`, `.../cache/gfs_daily`, `.../cache/wu_obs` | 仍包装 legacy `daily_pipeline` |
| `weather-predict-snapshot.timer` | **disabled/inactive** | paused | 旧 producer / fallback only | `/home/jiarui/projects/weather-predict/output/paper_snapshots/` + `output/orderbook_snapshots/` | 2026-06-29 16:26Z 起停用；旧数据保留，不删 |
| `weather-predict-daily-pipeline.timer` | active/waiting | daily | **旧 producer / 临时 fallback** | `/home/jiarui/projects/weather-predict/cache/*` | settlement/history/forecast cache fallback；新链路验证通过前不 disable |

### Consumer 层

这些进程消费 producer 产物或写策略日志，不应该被当成数据采集 producer：

| 入口 | 当前状态 | 归属 | 说明 |
|---|---:|---|---|
| `weather_theta_current_yes_tiny_live.py` | active process | pm_agent live strategy | 消费 snapshot + observation cache，执行 tiny-live；不拥有 canonical CLOB/weather 采集 |
| `station-basis-shadow-v1.service` | active/running | pm_agent shadow strategy | 消费数据并写 shadow runtime |
| `weather_theta_higher_no_carry_shadow.py` | active process | pm_agent shadow strategy | 消费数据并写 shadow runtime |
| `regime_routed_no_tiny_live.py` | active process | pm_agent live strategy | 已重启，默认按最新 snapshot producer 读取；不拥有 canonical CLOB/weather 采集 |
| `range_rv_shadow_v0.py` | manual shadow consumer | pm_agent shadow strategy | 已验证默认读取新 data-feed full snapshot |
| `pm-agent-weather-dashboard.service` | active/running | dashboard | 展示/读取 |
| `pm-agent-weather-dashboard-refresh.timer` | active/waiting | dashboard metadata | runtime registry 刷新 |
| `weather_telegram_control.py` | active process | ops control | 控制/通知，不是采集 |

### Research residue 层

这些脚本/目录可以保留，但默认不该常驻，也不该进 canonical ingest：

| 入口 | 当前状态 | 说明 |
|---|---:|---|
| `weather_source_orderbook_timing_monitor.py` | 当前无进程；N100 日志最后到 2026-06-29 15:12Z | research-only；历史 `sources.jsonl` 5.8GB、`books.jsonl` 2.6GB，最近 `book_rows=0` |
| `weather_metar_cross_prev_no_shadow.py` | 当前无进程；最后文件 2026-06-26 | strategy/research 日志，不是 producer |
| `weather_ldm_metar_notify_monitor.py` | 当前无进程；只有 2026-06-18 probe/config | LDM/IDD 探针，等待权限 |

## 当前产物统计

### N100 `weather_data_feed_service_runtime`

| 路径 | 文件数 | 今日文件 | 最新文件 | 说明 |
|---|---:|---:|---|---|
| `output/paper_snapshots/` | 400+ | 45+ | `snapshot_20260630_0045.json` at 16:52Z | 新 full snapshot；47 城、800 records、`clob_top_of_book_all` |
| `output/orderbook_snapshots/` | 338+ | 6+ | `orderbook_snapshot_20260630_0045.jsonl.gz` at 16:52Z | 新 full orderbook；1600 rows、47 城、`status=ok` 1600 |
| `output/observations/latest.json` | 1 | 1 | 16:52Z | 5 分钟 fast observation cache；schema `weather_data_feed_observation_cache_v1` |
| `output/logs/` | 6 | - | `observations.log` at 15:36Z, `snapshot.log` at 15:23Z | 新服务日志 |
| `cache/` | 29409 | - | `gfs_daily/Wuhan_2026-06-28.json` at 2026-06-27 09:27Z | daily cache，沿用 legacy cache 命名 |

### N100 `weather-predict`

| 路径 | 文件数 | 今日文件 | 最新文件 | 说明 |
|---|---:|---:|---|---|
| `output/paper_snapshots/` | 2641+ | 47+ | `snapshot_20260630_0000.json` at 16:15Z | 旧 snapshot runner 已停；最后基线 835 records |
| `output/orderbook_snapshots/` | 1935+ | 4+ | `orderbook_snapshot_20260630_0000.jsonl.gz` at 16:15Z | 旧全量 orderbook 已停；最后基线 1670 rows/47 城/non_ok=0 |
| `output/paper_trades/paper_orders.jsonl` | 1 | append-only | 15:15Z | paper ledger |
| `cache/wu_obs/` | 52 | - | 2026-06-10 | WU historical observed cache，偏结算/历史，不是实时 trigger |
| `cache/pm_history/` | 26539 | - | 2026-06-27 | Polymarket history / settlement cache |
| `output/logs/` | 4 | - | `paper_snapshot.log` at 15:15Z | 旧 runner logs |

### N100 `pm_agent`

| 路径 | 文件数/大小 | 最新 | 说明 |
|---|---:|---|---|
| `runtime/weather_edge_v1/source_orderbook_timing/` | `sources.jsonl` 5.8GB；`books.jsonl` 2.6GB | 15:12Z | 研究 timing monitor；当前无进程，最近 loop 盘口失败 `book_rows=0` |
| `runtime/weather_edge_v1/ldm_metar/` | probe/config files | 2026-06-18 | LDM/IDD 探针，不是实时数据流 |
| `runtime/weather_edge_v1/metar_cross_prev_no_shadow/` | 8 files | 2026-06-26 | METAR cross 策略日志；当前未跑 |
| `runtime/weather_edge_v1/live/` | 266 files | 2026-06-25 | live order logs |
| `runtime/weather_edge_v1/live_cycle/` | 3257 files | 2026-06-29 | policy/live-cycle logs |
| `runtime/weather_edge_v1/station_basis_shadow_v1/` | 10 files | 2026-06-29 | station-basis shadow |
| `runtime/weather_edge_v1/theta_higher_no_carry_shadow_v1/` | 5 files | 2026-06-29 | shadow strategy |

### Mac 本机镜像

| 路径 | 文件数 | 最新 | 状态 |
|---|---:|---|---|
| `runtime/weather_edge_v1/market_data/paper_snapshots/` | 2690 | local mtime 2026-06-29 17:52 BJ, `snapshot_20260629_1730.json` | 镜像滞后 N100 |
| `runtime/weather_edge_v1/market_data/orderbook_snapshots/` | 1994 | 2026-06-28 11:13 BJ | 本机 orderbook 镜像明显滞后 |
| `runtime/weather_edge_v1/market_data/cache/` | 29410 | 2026-06-27 | 镜像缓存 |
| `runtime/weather_edge_v1/remote_pm_agent/source_orderbook_timing/` | 3 | 2026-06-29 17:56 BJ | N100 pm_agent timing 研究日志镜像 |
| `runtime/weather_edge_v1/wu_basis_us/` | 8 | 2026-06-29 23:06 BJ | 本机先前 WU/RMK basis 研究残留；当前无 Mac 进程 |

Mac 当前 `tmux` 只有 `cc` / `codex-phone`，`ps` 没有天气采集脚本；只有 dashboard API 和一条 N100 reverse proxy tunnel。

## 本次 producer 根因

当前重复请求主要来自这两条 30 分钟 snapshot producer：

```text
weather-data-feed-snapshot.timer  -> weather_data_feed_service snapshot-targeted -> strategy_live scoped paper_snapshot
weather-predict-snapshot.timer    -> weather-predict run_paper_snapshot.sh -> paper_snapshot.py
```

它们都请求 Gamma/CLOB，但盘口 scope 不一致：

| producer | snapshot records | orderbook rows | 盘口 scope | 结论 |
|---|---:|---:|---|---|
| `weather_data_feed_service` old targeted run | 737 | 6 | `current_d1` | 旧轻量盘口只适合 current-YES，已被 `strategy_live` 替代 |
| `weather-predict` final baseline run | 835 | 1670 | effectively full/all | 15 分钟左右完成；已停用但数据保留 |
| `weather_data_feed_service snapshot-full` validated run | 800 | 1600 | `all` | 6 分 54 秒完成；47 城、non_ok=0；已接管 full snapshot timer |

所以短期正确动作不是让两个 timer 继续并行，也不是直接把新 producer 的 budget 拉大。CLOB/盘口采集已经开始
收口成 `weather_data_feed_service` 的明确入口：

```text
data-feed snapshot producer:
  - snapshot-targeted: strategy_live，给 live/near-live 策略的轻量盘口；输出隔离在 `targeted_output`
  - snapshot-full: all，给 canonical/research 全量盘口；systemd unit 已版本化，待 N100 parity 验证
  - latency/research book join: 独立 research output，不进入 canonical snapshot
```

当前 full snapshot 已切到 `weather-data-feed-full-snapshot.timer`。旧 `weather-predict-snapshot.timer`
已 disable，但旧数据保留；如果新 full 连续失败，可以重新 enable 旧 timer 作为回滚。

## 存在但当前没跑的采集/研究入口

| 脚本/入口 | 当前状态 | 产物 | 口径 | 是否该并入统一链 |
|---|---|---|---|---|
| `scripts/ops/weather_source_orderbook_timing_monitor.py` | N100 当前无进程；最近曾跑到 2026-06-29 15:12Z | N100 `runtime/weather_edge_v1/source_orderbook_timing/{sources.jsonl,books.jsonl,state.json}` | 多源天气 first-seen + CLOB book join；最近 `book_rows=0`，天气 rows 仍在写 | 天气 source timing 应迁到 data-feed-service；book join 留 research |
| `scripts/ops/weather_rmk_source_basis_opportunity_monitor.py` | 当前无 Mac/N100 进程 | 本机 `runtime/weather_edge_v1/wu_basis_us/.../source_basis_rmk_proxy_*.jsonl` | 消费 timing monitor 的 `sources.jsonl`，拉盘口判断 RMK/WU basis | 只保留为 research consumer，不再直接定义源 |
| `scripts/ops/weather_wu_source_basis_market_scan.py` | 当前无进程；本机 untracked research script | 默认 `runtime/weather_edge_v1/source_orderbook_timing/source_basis_market_scan.jsonl`，当前本机/N100默认路径无文件 | WU vs MADIS/METAR basis + orderbook scan | 如果继续用，先纳入 git/文档；否则标 research scratch |
| `scripts/ops/weather_observation_source_cadence_audit.py` | N100 无目录；本机样本停在 2026-06-20 | 本机 `runtime/weather_edge_v1/observation_source_cadence/sources.jsonl` | city/source report time、first-seen lag、cadence | 这类字段应该进入 data-feed-service `source_cadence` |
| `scripts/ops/weather_ldm_metar_notify_monitor.py` | 当前无进程 | N100 `runtime/weather_edge_v1/ldm_metar/ldm_probe.jsonl` and config | LDM/IDD access probe + pqcat parser；等待上游权限 | parser 可留数据层；daemon/access 管理不要混进策略 |
| `scripts/ops/weather_metar_cross_prev_no_shadow.py` | 当前无进程 | N100 `runtime/weather_edge_v1/metar_cross_prev_no_shadow/` | 策略执行/机会日志，会拉 weather source 和 CLOB | 执行逻辑留 pm_agent；weather fetch 改读统一 source events/cache |
| `scripts/ops/weather_edge_market_data.py capture-live` | 当前无进程 | 默认 `runtime/weather_edge_v1/market_data/live_orderbook/` | 旧 Polymarket orderbook capture 工具 | 不作为 canonical；需要时改造成 data-feed-service orderbook producer 或退 dormant |
| `scripts/ops/weather_market_snapshot.py` | wrapper，无常驻进程 | 依赖旧 strategy tool | 手动市场查询 | research/manual |
| `scripts/ops/weather_source_probe.py` | 手动，无常驻进程 | 可选 `--out` | 城市 source 探测表 | source profile 审计工具，不是生产采集 |

## 还需要一起迁移的点

优先级不是“脚本多就全搬”，而是按生产数据血缘和重复请求风险排：

| 优先级 | 入口 | 要迁到哪里 | 原因 |
|---:|---|---|---|
| P0 | `weather_data_feed_service/legacy_weather_predict/pm_edge_compare.py`、`daily_pipeline.py`、`paper_snapshot.py` | 拆成 data-feed-service 原生 producer：market map、forecast cache、full snapshot、targeted live book cache | 现在仍是 legacy wrapper；targeted 已隔离，但仍会全城市 forecast scan，不是真正低延迟 producer |
| P0 | `weather-predict-daily-pipeline.timer` | data-feed-service 原生 daily/cache pipeline | 旧 daily 仍是 fallback；forecast/WU/pm_history cache 还没完全从 legacy runner 脱离 |
| P1 | `weather_source_orderbook_timing_monitor.py` 的天气 source 部分 | `weather_data_feed_service source-events/source-cadence` | source first-seen/cadence 是数据层事实，不该由 pm_agent 研究脚本拥有；2026-06-30 已新增 `source-events` producer，待 N100 启用与 pm_agent consumer 切读 |
| P1 | `weather_source_orderbook_timing_monitor.py` 的盘口 join 部分 | 保留 pm_agent research consumer，但只读 data-feed source events + market map | orderbook reaction 是研究层；不要再同时负责天气源采集 |
| P1 | `weather_metar_cross_prev_no_shadow.py` 的天气 fetch | 改读 observation/source-events，触发时只做 exact token fresh book/order | 执行脚本可以临场查盘口，但不应维护独立天气源链 |
| P2 | `weather_rmk_source_basis_opportunity_monitor.py`、`weather_wu_source_basis_market_scan.py` | 保留 research consumer；输入改成 data-feed source-events + WU/current audit | source-basis 是研究判断，不应该自建天气采集链 |
| P2 | `weather_edge_market_data.py capture-live`、micro snapshot 脚本 | 标 dormant 或改成 data-feed orderbook producer 的调用入口 | 旧市场采集工具容易和 canonical orderbook snapshot 重复 |

## 代理使用边界

默认策略：

```text
Polymarket Gamma/CLOB       -> 可以走市场代理：WEATHER_PREDICT_PROXY / WEATHER_PREDICT_MARKET_PROXY / WEATHER_DATA_FEED_MARKET_PROXY
天气源/forecast/cache 下载  -> 默认直连，并且 trust_env=False，不吃系统 HTTPS_PROXY
天气源如果确实要代理      -> 必须显式设置 WEATHER_DATA_FEED_WEATHER_PROXY 或 WEATHER_PREDICT_WEATHER_PROXY
timing/research monitor     -> 已有 WEATHER_PROXY_MODE / MARKET_PROXY_MODE 分离；默认 weather=direct、market=direct/显式配置
```

2026-06-30 已修正 legacy data-feed runner：Open-Meteo/GFS 这类天气请求不再因为 `WEATHER_PREDICT_PROXY`
存在而自动 fallback 到市场代理，避免不必要代理流量。

2026-06-30 Mac 直连实测：在故意设置坏 `HTTPS_PROXY=http://127.0.0.1:9` 和
`WEATHER_PREDICT_PROXY=http://127.0.0.1:9` 的情况下，Open-Meteo GFS、AviationWeather METAR、
AviationWeather cache CSV、NOAA tgftp、weather.gov latest、IEM ASOS 均可由 data-feed 直连成功。

## 数据口径分层

| 层 | Canonical 口径 | 当前实际情况 | 统一方向 |
|---|---|---|---|
| source profile / 城市准入 | `weather_data_feed/source_profiles.json` + `source_policy.py` | 已是共享逻辑；策略和 monitor 基本在读它 | 保持唯一入口 |
| fast observation cache | `weather_data_feed_service_runtime/output/observations/latest.json` | 已 5 分钟运行；40 records；direct weather fetch | 提升为 live 策略唯一观测 cache |
| paper snapshot | 目标应是 `weather_data_feed_service_runtime/output/paper_snapshots/` | 新旧两套并行：data-feed-service 和 weather-predict | parity 后切 sync/default consumers 到 data-feed-service，停 weather-predict |
| orderbook snapshot | 目标应是 data-feed-service 统一输出 | 新旧两套并行，且今天覆盖都偏少 | 保留一个 canonical producer；研究脚本只读或追加研究字段 |
| settlement/history cache | `cache/pm_history`, `cache/wu_obs`, forecast caches | 新旧两边都有 cache；data-feed-service 仍包装 legacy daily | daily 逻辑迁出 legacy wrapper 后再停 weather-predict daily |
| source timing research | `source_orderbook_timing/sources.jsonl` + `books.jsonl` | pm_agent 研究脚本直接拉多源天气和 CLOB；日志巨大，盘口失败多 | 拆成两段：天气 first-seen 进 data-feed-service；盘口反应留 pm_agent research |
| RMK/WU basis research | `source_basis_*.jsonl` | 消费 timing monitor 的 `sources.jsonl`，再拉 orderbook | 不再自己拉天气；只消费 data-feed source-events |
| 策略执行 | `pm_agent/runtime/weather_edge_v1/*` | current-YES / station-basis / theta 等运行中 | 只消费标准数据产物；不新增独立 fetcher |
| 本机分析 | `runtime/weather.db` + mirror | 镜像可滞后，不能当生产健康来源 | 分析前显式 sync，且默认 source 需切到目标 producer |

## 为什么现在看起来分散

1. `weather_data_feed` 先抽的是“逻辑包”，不是“服务”。所以旧 runner 还得继续跑。
2. `weather_data_feed_service snapshot/daily` 当前是 wrapper，产物沿用 `paper_snapshots/`、`orderbook_snapshots/`、`cache/`，名字容易让人误以为还是 weather-predict 独占。
3. latency-arb 研究需要秒级 source timing + orderbook timing，所以历史上写了 pm_agent 研究 monitor；这类日志不该和 canonical snapshot 混为一谈。
4. live 策略为了执行 freshness，会有少量“临场查 fresh book / fresh observation”的代码；这属于策略执行证据，不该升级成新的生产数据源。
5. 本机 mirror 和 N100 producer 不是同一层。本机文件旧，不代表 N100 生产断流；N100 有新文件，本机没同步也不会自动出现。

## 收束计划

优先级按事故风险和收益排：

1. **先定唯一 producer 目标**：`weather_data_feed_service_runtime` 做目标 canonical，`weather-predict` 明确标为 migration fallback。
2. **先修 consumer fallback**：live consumer 默认按最新 snapshot 文件选择 producer，显式 env 才强制指定目录。这样短暂停某条 producer 不会卡在 stale 目录。
3. **验证 orderbook producer scope**：`strategy_live` live 所需盘口已经对应 `snapshot-targeted`，覆盖 current YES/current NO/D1 NO/D2 NO；`all` research/canonical 全量盘口已经对应 `snapshot-full`。targeted 不写 canonical output，避免 Range RV / research 消费到窄盘口。
4. **做 2-3 天 parity validation**：比较新旧 `paper_snapshots` / `orderbook_snapshots` 的 city count、record count、token coverage、schema fields、latest lag。通过后再停旧 `weather-predict-snapshot.timer`。
5. **把 source timing 拆回数据层**：在 data-feed-service 增加 `source_events.jsonl` / `source_cadence.jsonl`，记录 `city/source/report_ts/detect_ts/payload_hash/changed_since_last/fetch_latency`。pm_agent 的 timing monitor 只做 orderbook join。
6. **切本机 sync 默认源**：`scripts/ops/sync_weather_remote.sh` 现在默认 `weather-predict`；parity 后默认改 `--market-source=weather-data-feed`。
7. **停旧 runner**：只在 parity 通过且 dashboard/fact rebuild 能消费新产物后，disable `weather-predict-snapshot.timer` / `weather-predict-daily-pipeline.timer`，不删数据，标 dormant。
8. **清理研究入口命名**：`weather_source_orderbook_timing_monitor.py`、RMK/WU basis scan、LDM probe 都保留，但文档和 registry 明确标 `research-only`，输出放到 research namespace 或至少不要进入 canonical ingest。

## 给迁移 agent 的干净迁移原则

这份 inventory 是迁移路线图，不是“继续堆 gate 保持不断流”的方案。长期目标是干净的数据边界；
如果为了修正血缘和生产采集，需要短暂停掉某条数据流，可以接受。不要把临时 fallback、阈值或 guard
写成长期架构。

迁移执行顺序必须是：

1. **先修 producer 根因**：`records=0` 不是正常“无机会”，而是 market 抓取失败或数据链路失败。正确修法是让 producer 明确失败、停止发布坏产物或暂停该数据流；不要靠下游层层 gate 把坏产物消化掉。
2. **先修代理来源**：N100 自身 xray/订阅/节点要恢复为主出口。本机 reverse proxy tunnel 只能是临时 fallback；如果启用，必须确认走 1x HK/JP 节点，不能走 5x 节点，也不能长期承载高频采集。
3. **高频 timing monitor 默认保持停用**：`weather_source_orderbook_timing_monitor.py` 只能在明确限频、明确 research-only 输出、明确代理成本后手动启动；不能在 migration 中自动恢复。
4. **parity 后再切默认消费者**：至少连续 2-3 天比较 `weather_data_feed_service_runtime` 与 `weather-predict` 的 `paper_snapshots` / `orderbook_snapshots` / daily cache，字段、record count、token coverage、latest lag 都一致到可解释范围后，才允许把 `sync_weather_remote.sh` 默认 source 切到 `weather-data-feed`。
5. **dashboard/fact rebuild 先验证**：切 source 前后都要能 rebuild `runtime/weather.db`，并确认 dashboard API、fact tables、live strategy summaries 能消费新产物。
6. **最后才 disable 旧 runner**：只有上面全部验证通过，才 disable `weather-predict-snapshot.timer` / `weather-predict-daily-pipeline.timer`；只 disable，不删数据、不清 cache、不改写历史。

## 当前不该做的事

- 不要直接删 `weather-predict` 数据；它现在还是迁移 fallback。
- 不要让 Mac 重新跑天气采集；Mac 只做分析/同步。
- 不要把 `source_orderbook_timing/books.jsonl` 当 canonical orderbook snapshot；它是研究 join 日志，而且最近 `book_rows=0`。
- 不要把 strategy runtime 里的 fresh fetch 当成新的数据源；它只能作为执行审计字段。
- 不要把本机 proxy fallback 写进长期生产依赖；它依赖 Mac 在线和本机代理节点选择，只能用于 N100 原代理事故兜底。
- 不要默认新增 gate/guard/filter 来维持表面不断流；干净迁移可以接受短期数据暂停，先修 producer 和模块边界。
