# Weather Data Canonical Sources

Status: current-source
Updated: 2026-06-30 source-events signal boundary
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed

**单页拓扑** — 解决"到底从哪读数据"的问题。任何 weather 分析 / 报告 / 模型评估前必读。

> 与之配套：[WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md)（口径定义）、[WEATHER_DATA_PIPELINE.md](WEATHER_DATA_PIPELINE.md)（脚本职责）、[WEATHER_REPO_BOUNDARY.md](WEATHER_REPO_BOUNDARY.md)（仓库职责）。本文只回答"用哪份数据 / 不要用哪份"。

> **重要前提（2026-06-05 核实）**：
> - **N100 上没有活跃的 SQLite DB**。所有生产数据以 JSONL/JSON 文件形态存在 `output/`（weather-predict）和 `runtime/weather_edge_v1/`（pm_agent）下。
> - **本机 `runtime/weather.db` 是唯一的 weather SQLite DB**，由本机 ingest 脚本从两个 N100 镜像独立重建，**不是** N100 任何 DB 的拷贝。
> - 如果你在 N100 上看到 `*.db` 文件，要么是 0 字节残留（已清理），要么是非 weather 用途（chatgpt-web-bot 之类）。任何分析都不要去 N100 上抓 SQLite。
>
> **2026-06-06 口径勘误**：`pm_history` 已结算价格可能是 `0.9995 / 0.0005`，不是精确 `1.0 / 0.0`。本机 ingest/builder 必须按 near-binary 规则归一化结算；旧口径生成的大量 `missing_bracket` 报告需要重算。
>
> **2026-06-07 CLOB fill 勘误**：Polymarket public activity 不是逐 order 权威 fill 来源。真实成交优先读本地 `exchange_response.place.status='matched'` 和 authenticated CLOB 数据；public activity 只能作受 order cap 约束的 partial-fill fallback。任何 live_real 分析前必须确认 `weather_clob_fill_coverage_gate.py` 通过。

---

## 0. TL;DR — 不要再被搞混了

| 想做的事 | **只读** | **绝对不要读** |
|---|---|---|
| 历史绩效 / PnL / ROI / 胜率 | `runtime/weather.db` 的 `fact_trades` 表 | `t24_paper_*_summary.json`、raw `paper_orders.jsonl`（这些是 legacy 派生层，跳过 `fact_trades` 直读会得到旧口径） |
| 全机会 alpha / 成交质量 / 漏单 / 滑点 | `runtime/weather.db` 的 `fact_signal_candidates` 表 | raw paper_snapshots/ JSONL |
| 单笔血缘 (signal→plan→order→fill→settle) | `runtime/weather.db` 的 `signals/plans/orders/fills/settlements` | 任何 raw JSONL（除非确认底表丢字段） |
| 实盘下单凭证（真金 CLOB 提交记录） | `runtime/weather_edge_v1/live/*.jsonl` + `runtime/weather_edge_v1/remote_pm_agent/live/*.jsonl` （已被 ingest 到 `orders` 表，venue=`polymarket_clob`） | — |
| 实盘成交（真金 CLOB fills） | `fills` 表 join `orders WHERE venue='polymarket_clob'`，并用 raw `clob_fills.jsonl` + `weather_clob_fill_coverage_gate.py` 做 fill_id / order cap reconciliation | public activity 不能单独当 order-level 真相 |
| 抢单/测速实时天气信号 | N100 `weather_data_feed_service_runtime/output/source_events/latest.json`；历史审计读同目录 `sources.jsonl` | 策略脚本默认不要自己直抓 AviationWeather/TGFTP/CheckWX；只有显式 `live-fetch` 调试可以绕过 |
| 5 分钟级 live observation feature/cache | N100 `weather_data_feed_service_runtime/output/observations/latest.json` | full snapshot 里的旧 `metar_latest_*` 字段只作兼容回退 |
| 概率模型 / 错误分布 cache | N100 `cache/gfs_365d_*.json`（**实际 ~735 天，不是 365 天**）；本机镜像 `runtime/weather_edge_v1/market_data/cache/` | — |
| 结算（pm_history） | `settlements` 表用于 condition_id trade join；`settlement_outcomes` 表用于 city/date/bracket basket 或 source-grain research | 旧 `t24_paper_ledger_summary.json` 的 "by_date" 块；策略脚本临时直读 raw pm_history |

**唯一 DB**：`runtime/weather.db`。其他 `.db` 文件已搬到 `runtime/_legacy/`（见 §3）。

---

## 1. 数据流拓扑

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ N100 (192.168.0.200) — 生产采集 / 实盘执行                                 │
│                                                                            │
│   weather-predict/                                                         │
│     output/paper_snapshots/   ← 每 30 分钟 PM snapshot                     │
│     output/paper_trades/      ← paper_orders.jsonl 全池 paper ledger       │
│     output/research/          ← t24_paper_*_trades.csv（结算后 derived）   │
│     cache/pm_history/         ← 每日 settlement JSON                       │
│     cache/wu_obs/             ← WU 实测温度                                │
│     cache/iem_v2_*.csv        ← IEM 历史观测（每 ICAO 一份）               │
│     cache/gfs_365d_*.json     ← GFS 历史预测 cache（实际 ~735 天）         │
│                                                                            │
│   weather_data_feed_service_runtime/                                       │
│     output/source_events/latest.json  ← 最新 source-event 信号层          │
│     output/source_events/sources.jsonl ← append-only source-event 审计     │
│     output/observations/latest.json   ← 5 分钟级 observation cache         │
│                                                                            │
│   pm_agent/runtime/weather_edge_v1/                                        │
│     live/live_<inst>_<run>_orders.jsonl  ← 真金 CLOB 提交凭证（JSONL）    │
│     paper/live_<inst>_<run>_paper_orders.jsonl                             │
│     signals/, plans/, live_cycle/                                          │
└────────────────────────┬───────────────────────────────────────────────────┘
                         │ rsync (scripts/ops/sync_weather_remote.sh)
                         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ pm_agent 本机镜像（runtime/weather_edge_v1/）                              │
│                                                                            │
│   market_data/paper_snapshots/        ← N100 output/paper_snapshots/ 镜像  │
│   market_data/paper_trades/           ← N100 output/paper_trades/ 镜像     │
│   market_data/research/               ← N100 output/research/ 镜像         │
│   market_data/cache/{pm_history,wu_obs,iem,gfs}/  ← N100 cache/ 镜像       │
│                                                                            │
│   live/*.jsonl                        ← 本机 weather_live_cycle.py 产物    │
│                                         （目前未被部署执行；保留 84 文件） │
│   remote_pm_agent/live/*.jsonl        ← N100 pm_agent 的 live 提交镜像     │
│   remote_pm_agent/{signals,plans,live_cycle}/                              │
└────────────────────────┬───────────────────────────────────────────────────┘
                         │ scripts/weather_dashboard/run_stack.sh
                         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ Canonical DB: runtime/weather.db (~44MB SQLite, 唯一权威源)                │
│                                                                            │
│  原始事实层（append-only, BEFORE UPDATE/DELETE trigger 锁定）：            │
│    signals       — 信号（含 model_p_yes / market_yes_price / 决策上下文） │
│    plans         — 计划（sizing / entry_band / execution_policy）          │
│    orders        — 订单（venue ∈ paper / snapshot_replay / polymarket_clob)│
│    fills         — 成交（status ∈ filled/partial/cancelled/expired/        │
│                          simulated）                                       │
│    settlements   — 结算（condition_id/bracket trade join）                 │
│    settlement_outcomes — pm_history 源头粒度 city/date/bracket outcome     │
│    runs          — 跑批身份（execution_mode ∈ snapshot_replay/paper/live） │
│                                                                            │
│  派生分析层（derived facts, 由 build_weather_*.py 物化）：                 │
│    fact_trades              — fill 粒度宽表（PnL/win/cost/trade_class…）   │
│    fact_signal_candidates   — 机会粒度宽表（universe→intended→actual）    │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 表 vs 文件 — 谁是源、谁是镜像、谁是派生

| 文件 / 表 | 角色 | 写者 | 读者 | 注意 |
|---|---|---|---|---|
| **N100** `output/paper_snapshots/*.jsonl` | source | N100 半小时 snapshot timer | rsync→镜像 | T1+T2 全池 |
| **N100** `output/paper_trades/paper_orders.jsonl` | source | N100 paper engine | rsync→镜像 | 全池 paper ledger（**不是** live intent） |
| **N100** `output/research/t24_paper_*_trades.csv` | derived（结算后） | N100 `settle_t24_paper.py` | run_stack.sh ingest | 不是结算源头，源头是 `pm_history` |
| **N100** `cache/pm_history/<City>_<date>.json` | source | N100 `daily_pipeline.py` | `pm_history_settlements` ingest → `settlements` + `settlement_outcomes` | 结算唯一权威源；raw `0.9995/0.0005` 必须按 near-binary 规则归一化为 `1/0` |
| **N100** `cache/gfs_v4_<City>_*.json` | source | N100 GFS fetcher | `compute_error_distribution` / 校准 | **实际 ~735 天**（2 年）。本机镜像 `market_data/cache/gfs_v4/`（2026-06-05 起加入 sync） |
| **N100** `cache/gfs_daily_*.json` | source | N100 GFS daily fetcher | 校准辅助 | 本机镜像 `market_data/cache/gfs_daily/`（2026-06-05 起加入 sync） |
| **N100** `cache/ecmwf_v4_<City>_*.json` | source | N100 ECMWF fetcher | 多模型 ensemble | 本机镜像 `market_data/cache/ecmwf_v4/`（2026-06-05 起加入 sync） |
| **N100** `cache/jma_v5_<City>_*.json` | source | N100 JMA fetcher | 亚洲城市备份模型 | 本机镜像 `market_data/cache/jma_v5/`（2026-06-05 起加入 sync） |
| **N100** `cache/hrrr_v5_<City>_*.json` | source | N100 HRRR fetcher | 美国短期高分辨率 | 本机镜像 `market_data/cache/hrrr_v5/`（2026-06-05 起加入 sync） |
| **N100** `cache/icon_eu_v5_<City>_*.json` | source | N100 ICON-EU fetcher | 欧洲城市备份模型 | 本机镜像 `market_data/cache/icon_eu_v5/`（2026-06-05 起加入 sync） |
| **N100** `cache/arome_v5_<City>_*.json` | source | N100 AROME fetcher | 法国专用 | 本机镜像 `market_data/cache/arome_v5/`（2026-06-05 起加入 sync） |
| **N100** `cache/iem_v2_<ICAO>_<start>_<end>.csv` | source | N100 IEM fetcher | 概率模型/校准 | HongKong 用 `VHHH`（不是 VHKO）。本机镜像 `market_data/cache/iem/` |
| **N100** `output/logs/*.log` | log | N100 systemd timer | 故障排查 | 本机镜像 `market_data/logs/`（2026-06-05 起加入 sync）。判断 timer 跑没跑的唯一来源 |
| **N100** `weather_data_feed_service_runtime/output/source_events/latest.json` | source | `weather-data-feed-source-events.timer` | timing monitor / METAR crossing bot | city/source/station 最新观测事件；默认天气信号入口。包含 report_ts、detect_ts、payload hash、raw METAR、source profile 审计字段 |
| **N100** `weather_data_feed_service_runtime/output/source_events/sources.jsonl` | source log | `weather-data-feed-source-events.timer` | latency research / source-vs-market audit | append-only；用于判断哪个源先更新、市场是否领先天气源 |
| **N100** `weather_data_feed_service_runtime/output/observations/latest.json` | source cache | `weather-data-feed-observations.timer` | current-YES / regime-routed live feature layer | 标准 `weather_data_feed_observation_cache_v1`；策略优先读它，不再重复抓天气 API |
| **N100** `pm_agent/runtime/logs/*.log` | log | N100 pm_agent live cycle | 故障排查 | 本机镜像 `remote_pm_agent/logs/`（2026-06-05 起加入 sync） |
| **N100** `/home/jiarui/weather-predict-backups/*.tar.zst` | backup | N100 `backup_data.sh` | 灾难恢复 | 本机镜像 `runtime/_backups_n100/`（2026-06-05 起加入 sync，独立脚本 `sync_n100_backups.sh`） |
| **本机** `runtime/weather_edge_v1/live/*.jsonl` | source（本机产物，已停） | 本机 `weather_live_cycle.py`（最后写入 2026-06-01） | `migrate-live-cycle` → orders | 84 文件，本机 loop 已停。仍被 ingest 扫描（兼容历史），可以原地保留 |
| **本机** `runtime/weather_edge_v1/remote_pm_agent/live/*.jsonl` | mirror | rsync from N100 | `migrate-live-cycle` → orders | N100 真金 CLOB 提交凭证镜像 |
| **本机** `runtime/weather.db` | **canonical operational DB** | `weather_dashboard_refresh.sh` 增量 ingest；`run_stack.sh` 全量 rebuild | 所有分析 / API / 前端 | **唯一分析 DB**。日常不删库；全量 rebuild 只在确认 raw 输入 + CLOB fill cache / 外部 CLOB 同步可用时执行。 |
| **本机** `runtime/weather.db.fact_trades` | derived（唯一已成交 PnL 源） | `build_weather_fact_trades.py` | 所有绩效分析 | grain = 每 fill 一行 |
| **本机** `runtime/weather.db.fact_signal_candidates` | derived（唯一全机会源） | `build_weather_signal_candidates.py` | 成交质量 / 漏单 / 城市 alpha 分析 | grain = 每 `(condition_id,side,event_date)` 一行 |
| **本机** `runtime/weather.db.fact_forecast_hourly_curves` | derived（PIT 预报曲线附表） | `build_weather_signal_candidates.py` | reheat / ceiling margin / forecast slope 等曲线特征 | grain = 每 `(city,target_date,snapshot_ts_utc,forecast_values_hash)` 一行；候选行用 `forecast_values_hash` 关联 |
| **本机** `runtime/weather.db.settlement_outcomes` | canonical source-grain layer | `pm_history_settlements.py` | basket / city-day / source-sensitive settlement research | grain = 每 `(source_system, city, target_date, bracket)` 一行；不要再让每个策略脚本自己读 raw pm_history 定义 fallback |
| **本机** `runtime/weather_decision_journal.db` | **活跃 sidecar** | `scripts/ops/weather_decision_journal.py` | `weather_position_monitor.py` | 仍在用，不动 |
| **本机** `runtime/_legacy/strategy_runtime.db` | legacy（PMM 已退役） | （已无 writer） | （已无 reader） | 保留作历史参考 |
| ~~`runtime/weather_v2.db`~~ | **已删除（2026-06-05）** | — | — | 完全被 `runtime/weather.db` 替代，无独有数据 |
| ~~`runtime/weather_edge_v1/weather.db`~~ | **已删除（2026-06-05）** | — | — | 完全被 `runtime/weather.db` 替代，无独有数据 |

---

## 2.5 日常刷新 vs 全量重建

日常 dashboard/report 刷新用：

```bash
scripts/ops/weather_dashboard_refresh.sh
```

这是**增量流程**：

1. `sync_weather_remote.sh --live-only` 拉 N100 新的 live_cycle / signals / plans / live orders；
2. `migrate-live-cycle` 对现有 `runtime/weather.db` 做 `INSERT OR IGNORE`，不会删库；
3. `clob_fill_sync` 先导入本地 CLOB fill cache，再从 Polymarket 补新增真实 fills；
4. `build_weather_fact_trades.py` / `build_weather_signal_candidates.py` 从 canonical 表重建派生宽表；
5. `metrics-refresh` 重算 dashboard metrics。

所以：**raw/canonical 层是增量积累，fact/metrics 层是每次按当前 DB 重新物化。**

全量重建用：

```bash
scripts/weather_dashboard/run_stack.sh
```

这是开发/修复用的 rebuild：会 `clean-db -> db-canonical-init`，然后重新 ingest 所有镜像文件。只有在确认 raw 镜像、pm_history、CLOB fill cache / CLOB API 都可用时才应该跑。否则可能丢掉 rebuild 当下无法从 raw 文件恢复的 live fill 状态。

---

## 3. Legacy 数据隔离

| Legacy 文件 | 状态 | 退役原因 | 替代 |
|---|---|---|---|
| `runtime/_legacy/strategy_runtime.db` (956KB) | 保留 | PMM/ARB 框架已退役 | 无（新策略不走该 schema） |
| ~~`runtime/weather_v2.db`~~ | **已删除 2026-06-05** | 早期 canonical schema 实验 | `runtime/weather.db`（同 schema 但完整数据） |
| ~~`runtime/weather_edge_v1/weather.db`~~ | **已删除 2026-06-05** | 早期 v1 schema | `runtime/weather.db` |

`runtime/_legacy/README.md` 记录搬运/删除时间、原因、最后 mtime。

> **判断"是不是 legacy"**：如果 `grep -rn '<dbname>' src/ scripts/ weather_dashboard/` 在非 archive/non-pmm 路径下有命中且活跃，**不要**搬。

### N100 上的 DB 注意事项

N100 `/home/jiarui/projects/pm_agent/runtime/runtime/weather.db`（**注意路径有双 `runtime/`**，是历史 cwd 错位留下的）存在但**不是分析源**。本机 `weather.db` 由本机 ingest 脚本从镜像独立重建，与 N100 那份是两条平行链路；不要 scp 它回来当数据源用。

---

## 4. 已知缺口 / rebuild 风险

### 4.1 真金 CLOB fills 不是 live JSONL 自带字段

**关键点**：N100 / 本机 `live/*.jsonl` 记录的是 CLOB 订单提交凭证（`orders.status='submitted'`），不是成交回报。真实成交必须进入 `fills` 表后，`fact_trades` 才会出现 `trade_class='live_real'`。

pipeline：
```text
live/*.jsonl → orders(venue=polymarket_clob, status=submitted)  [via migrate-live-cycle]
             → clob_fill_sync 查 Polymarket CLOB API → fills(status=filled)
             → build_weather_fact_trades → trade_class=live_real
```
`_derive_trade_class(execution_mode='live', fill_status='filled') = 'live_real'`，逻辑正确。

**当前保护**：

- `clob_fill_sync` 会先导入 `runtime/weather_edge_v1/clob_fills.jsonl` 持久化 fill cache；
- 新拉到的真实 CLOB fill 会追加写入这个 cache；
- 如果 Polymarket CLOB / activity / trades API 连接失败，`clob_fill_sync` 返回 `data_incomplete=true` 并 exit 1；
- `run_stack.sh` / `weather_dashboard_refresh.sh` 遇到该失败会 hard fail，不再继续产出一个误导性的 `live_real=0` DB。
- `run_stack.sh` / `weather_dashboard_refresh.sh` 在 build facts 后会运行 `scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py`；只要出现 mismatched order_id、fill 超过 order cap、DB/cache fill_id 不一致、或 `fact_trades` 成本不等于 `fills` 成本，就 hard fail。

**历史事故**：2026-06-04 曾经用 full rebuild 清空 DB 后，Polymarket API `ConnectionResetError(104)`，导致 rebuilt DB 中 `live_real=0`。已从旧 `clob_fill_sync.log` 恢复 67 条可证明真实 fills，并写入 `runtime/weather_edge_v1/clob_fills.jsonl`。

**2026-06-07 事故复盘**：旧 fallback 把 Polymarket public activity 当成逐 order 权威 fill 来源。public activity 实际是账户级成交活动，不可靠携带本地 CLOB `order_id` 粒度；在 split child order、同 token 多笔订单、partial fill 场景下会少算或多算。修复后真实 fill 优先 `exchange_response.place.status='matched'` 和 authenticated CLOB order/trade 数据；public activity 只能作受 token/side/price/time/order cap 约束的 fallback。

**2026-06-06 验证状态**（历史快照）：当时重建后 `fact_trades live_real` 和 raw `clob_fills.jsonl` 完全一致：

```text
db_live_real_distinct_fills=852
raw_clob_distinct_fills=852
db_not_in_raw=0
raw_not_in_db=0
```

**2026-06-07 当前标准**：`live_real` 行数会随新增真实成交变化，最终以 `weather_clob_fill_coverage_gate.py` 为准。可发布 live PnL 的最低条件是 `gate_pass=true`、`missing_order_rows=0`、`over_order_keys=0`、DB/cache fill_id 差异为 0、`db_fill_cost_minus_fact_cost=0`。

如果再次看到 `"data_incomplete": true`：

```text
runtime/_dashboard_logs/clob_fill_sync.log
"checked": <n>, "filled": <m>, "still_open": <k>,
"external_fetch_errors": 2, "data_incomplete": true
```

优先处理：
- 在 N100 上跑 `clob_fill_sync`（N100 网络稳定），把 fills 表 dump 出来再同步回本机；
- 或在本机配置 HTTP 代理。

**不要做**：拿 `live_simulated` 冒充 `live_real`，或拿 paper PnL 冒充实盘 PnL。

### 4.2 pm_history near-binary settlement 旧口径污染

Polymarket 已结算 bracket 在 `pm_history` 里常见 raw `final_price=0.9995` 或 `0.0005`。2026-06-06 前旧 ingest/builder 只认精确 `1.0 / 0.0`，会把这些已过期、实际可结算的合约误标为 `missing_bracket`，进而低估 settled rows 并污染 realized PnL / ROI / win rate / city-side rank。

修复位置：

```text
weather_dashboard/ingest/pm_history_settlements.py
scripts/etl/build_weather_fact_trades.py
scripts/etl/build_weather_signal_candidates.py
```

修复后基线：`missing_bracket` 从 725 行降到 0。凡是引用旧报告中 `missing_bracket=725/734/28` 等数值的结论，都要先重建 DB 再重算。

### 4.3 概率模型 cache 文件名误导

N100 `cache/gfs_365d_*.json` 文件名写 365 天，**实际包含 ~735 天**（2 年）。在 `compute_error_distribution()` 代码里有注释 `# Load GFS 365d cache` 同样误导。这两处都待修。

### 4.4 `decision_window_missing` 占 ~44%

`fact_signal_candidates` 里 `decision_window_missing=1` 占比 ~44%（T-22~24h 决策窗内无 snapshot）。所有反事实 alpha 结论的有效样本只覆盖另一半，必须在报告里点明。

---

## 5. 启动前自检（5 行 SQL）

任何分析前先跑：

```sql
-- 1. DB 是否最新
SELECT MAX(fact_built_at_utc) FROM fact_trades;

-- 2. trade_class 分布（是否有 live_real / 缺口在哪）
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;

-- 3. settled / unsettled 比例
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status;

-- 4. 机会底表覆盖
SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled)
FROM fact_signal_candidates;

-- 5. CLOB live 提交是否有对应 fills
SELECT o.status, COUNT(*) orders,
       SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill
FROM orders o LEFT JOIN fills f USING(execution_id)
WHERE o.venue='polymarket_clob' GROUP BY o.status;
```

任何一行返回结果与预期严重背离（如 live_real 应该 > 0 却 = 0，或历史窗口 `missing_bracket` 突然大量出现），先回 §4 查已知缺口或运行 `run_stack.sh` 重建，**不要**直接绕开 `fact_trades` 跑 raw 自算。

---

## 6. 维护规则

- 新增 raw 数据源（新文件夹 / 新 cache）→ 在 §2 表里加一行。
- 新增 derived 表 / 派生 view → 在 §2 表里加一行，并更新 `WEATHER_ANALYSIS_CONTRACT.md`。
- 退役旧 DB / 旧文件 → 搬到 `runtime/_legacy/`，更新 `runtime/_legacy/README.md` 和本文 §3。
- 发现新已知缺口 → §4 加一节。
- 不要让本文长过 300 行；详细 schema 去 `WEATHER_STRATEGY_QUANT_DESIGN.md`，详细脚本职责去 `WEATHER_DATA_PIPELINE.md`。
