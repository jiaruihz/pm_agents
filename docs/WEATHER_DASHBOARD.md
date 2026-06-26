# Weather 看板口径文档（Dashboard Caliber）

Status: `current-source`
Updated: 2026-06-27
Source of truth: yes（看板信息架构 / 页面口径 / 接口）
关联：设计 spec [2026-06-26-weather-dashboard-redesign-design.md](superpowers/specs/2026-06-26-weather-dashboard-redesign-design.md) ·
口径细则 [WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md) ·
字段契约 [WEATHER_SYSTEM_CONTRACT.md](WEATHER_SYSTEM_CONTRACT.md)

> 这份文档定义**重做后的天气看板**：它有哪些页、每个数字什么口径、数据从哪来、什么时候该信、什么时候别信。
> 看板是只读的，不下单、不碰资金/不可逆操作。前端 `frontend/strategy_dashboard`，后端 `weather_dashboard/api`（读 `runtime/weather.db` + `runtime/weather_edge_v1/*` JSONL）。

## 0. 一句话定位

不是 PnL 排行榜。是**「在跑探针的健康/执行质量 + 前向研究证据登记册」**。
当前没有已确认稳定盈利的 live alpha；在跑的都是 tiny-live 前向取证（$5–$10 微仓），
**按执行质量/滑点评估，不按早期 PnL**。

## 1. 怎么起

```bash
scripts/weather_dashboard/run_stack.sh [--no-rebuild]   # 建库+API+FE
# FE http://localhost:5174 · API http://localhost:8000/docs · 默认落地 “今日总览” /
```

## 2. 信息架构（按探针生命周期）

| 路由 | 页 | 职责 |
|---|---|---|
| `/` | 今日总览 | 脉搏：探针健康 / 在险资金 / 已结算盈亏 / 最近研究线 |
| `/probes` · `/probes/:instance` | 探针在跑 | live & shadow 探针健康、执行质量、参数口径 |
| `/research` · `/research/:lineId` | 研究证据 | 各策略线前向证据登记 + 策略思路 + 变体对比 |
| `/performance` | 绩效对账 | live_real 持仓 / 在险资金 / 已结算 PnL / 对账 |
| `/lineage/:date` | 单日血缘 | 某日逐笔 signal→plan→order→fill→settlement，按城市 + 策略 |
| `/data-sources` | 数据源 | 预测源 / METAR 观测源 / 实时盘口快照（来源·时间·城市） |
| `/glossary` | 术语字典 | canonical 字段→中文+口径，全站 hover 数据源 |
| `/archive` | 归档 | 旧 PMM/ARB + 上一版天气页（dormant 保留不删） |

## 3. 硬口径（踩坑换来的，违反就会误导）

### 3.1 镜像新鲜度 ≠ 生产健康（最容易误判）
本机是 N100 的**只读镜像**，可能滞后。看板任何"新鲜度"分两层，永不混：
- **行情快照年龄 `snapshot_age_min`** / **镜像心跳 `heartbeat_age_min`**：本机镜像的同步年龄。
- **生产是否断流**：**不**由镜像新旧推断。判断断流跑 N100 doctor：
  `ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && scripts/ops/doctor_restart.sh'`。
- 镜像旧 → 先 `scripts/ops/sync_weather_remote.sh`。N100 不可达（`No route to host`）时镜像无法更新，
  看板显示的是镜像里最后一份，**这不是看板 bug，是采集侧不可达**。

### 3.2 在险资金（绩效页 / 总览）
- **在险资金 = 近期未结算持仓的开仓成本**（`open_recent_cost_usd`，target_date 在近 7 天内、待结算）。
- **不是** `capital_deployed_usd`（那是含已结算的累计投入，会虚高）。
- **开仓成本 ≠ 亏损**：结算后才有 realized PnL。

### 3.3 陈旧未结算（疑似漏结算）
- `settled=0` 但 target_date 超过 7 天的旧持仓 = **幽灵未结算**：多为已停用的 `mid_price_core`，
  `settlement_join_method=none`，结算结果从未回填进 `fact_trades`。现实早已结算。
- 这些**不算真正在险**，绩效页单列「陈旧未结算·疑似漏结算」，不进在险资金。
- 修复 = 走结算回填（`weather-fact-rebuild` / settlement backfill）。

### 3.4 PnL 口径
- 只有 `settled=1` 才报 **已实现盈亏 `pnl_usd_at_fill`**；未结算只报 **MTM `unrealized_pnl_mid`** + `val_snapshot_ts_utc`。
- 绩效只统计 **`trade_class='live_real'`**（真金）。
- 现金流口径用 `fill_date_bj`，不用 `order_date_bj`（后者受回填污染）。
- 发布任何 live PnL/ROI 前过 `gate_pass`（`weather_clob_fill_coverage_gate.py`）；红就别发。
- near-binary `0.9995/0.0005` 已归一化 `1/0`。

### 3.5 探针（probe）口径
- 探针 = 在跑的 live/shadow 策略实例。白名单 = `weather_strategy_runtime_registry`（排除 smoke/tmp），
  **不靠扫目录**。实时脉搏来自各 `runtime/weather_edge_v1/<instance>/latest_summary.json`。
- 分组：**实盘 live**（真实下单）/ **影子·遥测 shadow**（零 notional 只采证据）/ **受阻·陈旧**。
- 字段：**候选数** = 从盘口筛出的机会；**可执行** = 再过新鲜度/仓位/穿价门槛后真会下单的；
  **主要拦截** = 本轮最多候选被挡下的原因（如观测过旧 / 无当日市场 / 不在决策时段）。
- 缺脉搏文件 → 标 `no_pulse_file` 显式暴露，不静默丢。

### 3.6 研究证据口径
- 一行一条策略线，聚合 `docs/analysis/**/generated/*/summary.json`。
- **够 live?** 优先取报告自带 `verdict.live_ready`；没有则按 CI 不跨 0 且 forward ROI>0 推断。
- **CI 跨 0 即不是可 live 规则**，只是 shadow 候选。
- 代表 ROI：top-level 没有就取最优非 baseline 变体（详情页给完整变体表）。
- 状态中文化；`summary.json` 没写 verdict/status 的显「研究中（未标注结论）」。
- 详情页顶部「策略思路·为什么这么做」抓对应分析 `.md` 的结论叙述。

## 4. 数据源（/data-sources）

- **预测数据源**（来自 fact_trades）：`open_meteo_live_ecmwf` / `open_meteo_live_gfs`，含城市数 / 用到笔数 / 最近快照时间。
- **观测源 / METAR**：13 个 canonical 源（注册表 `weather_data_feed/observation_sources/aliases.py`，
  权威文档 [WEATHER_DATA_CANONICAL_SOURCES.md](WEATHER_DATA_CANONICAL_SOURCES.md)）——
  aviationweather_metar、checkwx_html、iem_asos*、ldm_metar、noaa_tgftp_station_txt、
  synopticdata_timeseries、weather_gov_latest、weather_com_* 等。
- **实时盘口快照**：`runtime/.../paper_snapshots/snapshot_*.json`，**更新周期约 30 分钟/次**；
  显示的是本机镜像，时间偏旧=未同步（见 §3.1）。

## 5. 后端接口（复用 + 新增只读）

新增（这次重做）：
- `GET /api/probes/health`、`GET /api/probes/{instance}` — 探针脉搏（registry + latest_summary.json）。
- `GET /api/research/lines`、`GET /api/research/lines/{line_id}` — 研究证据聚合 + 叙述。
- `GET /api/live/book`、`GET /api/live/book/strategies` — live_real 持仓（canonical fact_trades，带 poly_url / stale 标记）。
- `GET /api/data-sources` — 预测源 / 观测源 / 盘口快照。
- `GET /api/glossary` — 字段字典。

复用：`/api/live/summary`、`/api/runs/*`、`/api/configs/strategies/*`、`/api/compare`、`/api/strategy-runtime/*`。

修过的真 bug：`/api/live/positions` 的 `_SETTLEMENTS_DEDUP` NameError；
`/api/live/summary` 的 `open_count` 漏掉 NULL 结算行（`NULL != 'settled'` 在 SQL 里是 NULL）。

## 6. Polymarket 跳转
绩效/持仓行的 **↗** 链接：`fact_trades` 只有 `condition_id`，slug 在 snapshot 记录里（`event_slug`），
由 `/api/live/book` 用最近快照做 `condition_id→event_slug` 映射拼 `https://polymarket.com/event/<slug>`；
旧市场不在近期快照里则无链接。

## 7. 已知缺口
- 结算回填：§3.3 的陈旧未结算需要 settlement backfill。
- 研究 `summary.json` schema 不统一，部分字段/ROI 缺 → 显 `—` 并直链 doc，不编数。
- glossary 只覆盖看板出现过的字段，逐步补全。
