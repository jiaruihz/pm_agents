# Weather Dashboard 数据模型分层与缺口审计

Status: current-reference
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; reference only, not production source of truth

> 最后更新: 2026-05-17 (P0 已完成，见 §7)
> 范围: 本机 `weather_dashboard/` SQLite + ingest + API。**不涉及** N100 生产链。
> 目的: 在数据格式僵化前先识别缺口，避免后期回填困难。
> 关联文档: 核心量化系统架构 spec → [WEATHER_STRATEGY_QUANT_DESIGN.md](WEATHER_STRATEGY_QUANT_DESIGN.md)
> 早期实盘历史与回填治理 → [WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md](WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md)

---

## 1. 量化系统典型分层

参考一个完整的策略生命周期，数据应分 8 层。**括号里是当前实现状态**：

| # | 层 | 内容 | 当前状态 |
|---|---|---|---|
| L1 | **Raw Market Data** | 30 分钟 Polymarket snapshot, orderbook | ❌ 完全不存 |
| L2 | **Raw External Data** | 天气观测 (IEM/Wunderground), 预报 (GFS/ECMWF) | ❌ 完全不存 |
| L3 | **Feature/Signal** | 每个 snapshot 派生的 edge / model_prob / market_price | ⚠️ 只存 chosen side，未存所有 bracket / 双 side |
| L4 | **Strategy Config** | 策略参数（min_edge, sizing 等） + 模型版本 | ✅ `strategy_config` 表 |
| L5 | **Decision/Plan** | 风控/筛选后的 trade plan | ⚠️ 只有 desired_shares + skip_reason，缺 sizing 上下文 |
| L6 | **Execution** | order → fill, 含 venue response | ⚠️ 缺 exchange_response / venue / execution_id |
| L7 | **Settlement/Outcome** | 真实结果 + PnL | ⚠️ 只有 0/1，缺 final_price / token_id |
| L8 | **Run/Experiment** | 串起 L3-L7 的可复现实验单元 | ✅ `runs` + `repro_key` |

外加一层横切：

| # | 层 | 内容 | 当前状态 |
|---|---|---|---|
| L9 | **Monitor/Alert** | contract_alerts, doctor, pause/resume, balance preflight | ❌ 不存 |

---

## 2. 当前可消费的源数据（实际存在的字段）

### 2.1 当前 dashboard ingest 的最完整源 — `t24_paper_ledger_trades.csv` (48 字段)

> 这是 dashboard 实际 ingest 的源。本机镜像里同目录的 `paper_orders.jsonl` **首行只有 37 字段**，缺 `city_pool / settlement_status / final_yes / won / pnl_usd / order_source / eligible_for_paper_order / entry_price_min,max,window / execution_policy` 这 11 列（由后续 settle / 分析脚本在 N100 上派生后写入 CSV）。
>
> **`paper_orders.jsonl` 是否含有这些字段取决于 N100 当前代码版本和同步状态**——不要假设 jsonl 一定有 CSV 的全部列。审计/迁移以本机镜像的 CSV 为准。

CSV 48 列分组：

```text
✓ 信号上下文: snapshot_file, snapshot_ts_utc, snapshot_ts_beijing,
              city, city_pool, eligible_for_paper_order, icao,
              event_date, event_slug, question, time_bucket, window,
              hours_to_settle, settle_utc, settle_local, unit
✓ 市场标识 : condition_id, market_id, market_key
✓ 模型输出 : model, model_prob, forecast_max_f, forecast_source,
              obs_source, obs_source_version, not_raw_wunderground
✓ 决策     : side, edge, abs_edge
✓ 报价     : market_yes_price, last_trade_price, entry_price,
              entry_price_max, entry_price_min, entry_price_window
✓ 执行     : order_id, order_source, shares, cost_usd, mode,
              execution_policy, status
✓ 结算     : settlement_status, final_yes, won, pnl_usd
```

snapshot replay CSV (`t24_paper_snapshot_replay_trades.csv`) 是 35 列，结构相似但缺 `entry_price_min/max/window`、`execution_policy`、`order_source`、`eligible_for_paper_order`，因为 replay 不走真实下单链路。

### 2.2 N100 live_TIMESTAMP_orders.jsonl — Live 专有字段

```text
✓ Lineage   : execution_id, plan_id, signal_id  ← 显式血缘 ID
✓ 风控/订单 : entry_price_window, execution_policy, notional, size,
              limit_price, order_side (BUY/SELL),
              token_id, venue (polymarket_clob), strategy
✓ 状态     : status (error/...), exchange_response (完整 venue JSON)
```

### 2.3 N100 live_cycle/TIMESTAMP.json — 运行时元数据

```text
✓ config       : 这一周期使用的全部 live 参数（max_order_notional, sizing_mode, ...）
✓ contract_alerts: 不变式违反列表（必须告警的项）
✓ planner.live_dedup: 跨周期去重情况
✓ executor.balance_preflight: 钱包余额/授权预检
✓ paths        : 这一周期所有产物的绝对路径
```

### 2.4 N100 pm_history/{City}_{date}.json — 结算原始

```text
✓ unit, brackets[].label, brackets[].final_price (0.0–1.0),
  brackets[].token_id, brackets[].closed, brackets[].question
```

### 2.5 本机镜像额外数据

```text
✓ cache/iem_v2_<ICAO>_<start>_<end>.csv         天气观测原始 (L2)
✓ cache/wu_obs/wu_obs_<ICAO>.csv                Wunderground 代理 (L2)
✓ paper_snapshots/snapshot_*.json               30 分钟 Polymarket snapshot (L1)
```

---

## 3. 当前 DB 覆盖范围 vs 源数据缺口

### ✅ 已覆盖 (够用)

| 字段类 | DB 字段 | 说明 |
|---|---|---|
| 信号基础 | `signals(target_date, city, bracket, side, model_version, model_p_yes, market_price, edge, abs_edge)` | 够做基本统计 |
| 订单/成交 | `orders(side, entry_price, shares, cost_usd)`, `fills(filled_shares, filled_price, status)` | 够算 PnL |
| 结算 | `settlements(final_yes, status)` | 够算 win/loss |
| 运行 | `runs(config_id, universe_id, execution_mode, state, repro_key, metrics)` | 够做对比 |

### ✅ P0 — 已完成 (2026-05-17)

以下 6 列已加入 `signals` 表，ingest/API/前端全栈已更新。DB 已用当时的 rebuild 输入重建；下表计数只表示
2026-05-17 该次 `signals` materialization scope 内已填充的 rows，不表示项目全部历史或当前 canonical coverage。

| 字段 | 类型 | 状态 | 验证数据 |
|---|---|---|---|
| `signals.city_pool` | TEXT (t1_trading/t2_research) | ✅ 已上线 | t1=463, t2=362 |
| `signals.forecast_source` | TEXT (open_meteo_live_ecmwf/gfs) | ✅ 已上线 | ecmwf=435, gfs=390 |
| `signals.condition_id` | TEXT (0x...) | ✅ 已上线 | 从 CSV 透传 |
| `signals.market_id` | TEXT | ✅ 已上线 | 从 CSV 透传 |
| `signals.icao` | TEXT (ZSPD/LFPG 等) | ✅ 已上线 | 从 CSV 透传 |
| `signals.hours_to_settle` | REAL | ✅ 已上线 | 从 CSV 透传 |

P0 同步完成的端点和前端：
- `GET /api/runs/{id}/metrics/slice?group_by=city_pool|forecast_source|model_version|side|city` — 新增，返回每个切片的 PnL/ROI/win_rate
- `GET /api/runs/{id}/trades?city_pool=&forecast_source=` — 已加过滤参数，返回新字段
- `WeatherHistoryPage` — 已加 Pool / Source 两个下拉过滤器（URL 同步）

代码变更范围：`weather_dashboard/db/schema.sql`, `ingest/real_ledger_adapter.py`, `ingest/ledger_csv.py`, `api/routers/runs.py`, `api/schemas.py`, `frontend/.../weather-http.ts`, `frontend/.../WeatherHistoryPage.tsx`

### ❌ 缺口 — 按影响优先级分

#### P1 — 限制下一阶段扩展 (1-2 个月)

| 缺口 | 影响 | 修复 |
|---|---|---|
| **L1 Market Snapshot 表** | 无法回测"如果换个 entry timing 怎样" / 无法 replay | 新增 `market_snapshots(snapshot_ts_utc, city, bracket, yes_price, no_price, volume, ...)` |
| **`signals` 只存 chosen side** | 无法回答"如果 BUY_YES 而不是 BUY_NO 结果如何"——丢失对侧信号 | 改 ingest：每个 (snapshot, city, bracket) 同时插 YES 和 NO 两条 signal |
| **`orders.exchange_response` / `venue` / `execution_id`** | live 错误归因/复盘失败 | 加三列；execution_id 是 live 唯一 ID |
| **`plans` 缺执行上下文** | 没有 `notional`, `sizing_mode`, `entry_price_window`, `execution_policy`——重跑同一 plan 不可复现 | 加 4-5 列 + 把 `plan_id` 改用源头的 execution_id |
| **`settlements.final_price`** (0.0-1.0 float) | 现在只存 0/1，丢失部分结算的精度 | 改 `final_yes INTEGER` → `final_price TEXT`，向后兼容 |
| **`settlements.token_id`** | 无法 join 到 pm_history | 加一列 |

#### P2 — 长期完整性 (季度级)

| 缺口 | 影响 | 修复 |
|---|---|---|
| **L2 Weather 数据入库** | 无法在 DB 里做特征工程 | 新增 `weather_observations`, `weather_forecasts` 表 |
| **`contract_alerts` 表** | live 不变式违反只在 jsonl，没集中告警 | 新增 `run_alerts(run_id, alert_ts, severity, kind, payload)` |
| **`run_state_log`** (pause/resume/error 历史) | 看不到 live run 中间发生过什么 | 新增 append-only 状态变更表 |
| **账户/仓位快照** | 当前 PnL 是基于 fill，不是基于真实仓位 mark-to-market | 新增 `account_snapshots(account_id, ts, balance_usd, positions_json)` |
| **L3 完整特征向量** | 模型再训练时缺特征 | `signal_features(signal_id, feature_name, value)` 长表 |

#### P0 Live 历史回填缺口 — 2026-05-14 到 2026-05-17 早期实盘

早期 weather live 不是一个干净 run：本机和 N100 都真实下过单，且期间发生过重复下单风险、城市池错误、sizing 改动。Dashboard ingest 不能把这些混成一个 live run。

必须补的 live 维度：

| 字段 | 作用 |
|---|---|
| `execution_host` | 区分 `local_pm_agent` / `n100_pm_agent`。 |
| `run_family` | 区分 local duplicate-risk、local wrong-universe、N100 legacy、N100 current T1。 |
| `run_quality` | 标记 `active_candidate` / `invalid_process_wrong_universe` / `invalid_process_duplicate_risk` / `legacy_metadata_or_universe_guard_incomplete`。 |
| `source_path` + `source_row_hash` | 保留原始 JSONL 血缘，禁止改历史源文件。 |
| `actual_city_pool` + `intended_city_pool` | 解决 T1 策略 PnL 与钱包总 PnL 混淆。 |
| `external_order_id` | Join CLOB actual fills 的主键。 |
| `fill_source` | 标记来自 CLOB trades / Data API positions / fallback。 |

早期 live 回填必须同时展示两套指标：

```text
account_live_pnl = 所有真实成交的 weather 仓位
strategy_clean_pnl = 指定 run_family/config 的干净策略子集
```

---

## 4. 当前管线对 3 类 run 的支持度

| 场景 | snapshot_replay (回测) | paper | live | 备注 |
|---|---|---|---|---|
| 基本 PnL/胜率 | ✅ | ✅ | ✅ | 已验证 (636/608 trades) |
| 城池切片 T1 vs T2 | ✅ city_pool 已加 | ✅ | ✅ | **P0 完成** · t1 ROI +11.8% · t2 ROI +5.0% |
| 模型源对比 (ecmwf vs gfs) | ✅ forecast_source 已加 | ✅ | ✅ | **P0 完成** · ecmwf ROI +10.1% · gfs ROI +8.2% |
| 切片 API + 前端过滤 | ✅ metrics/slice + 下拉 | ✅ | ✅ | **P0 完成** |
| 天气准确度归因 | ❌ 无观测/预报数据 | ❌ | ❌ | P2 |
| Live 失败归因 | ❌ 无 exchange_response | ❌ | ❌ | P1 必须 |
| 可复现实验 | ⚠️ repro_key 有，但 plan 不全 | ⚠️ | ⚠️ | P1 |
| 盘口 timing 优化 | ❌ 无 market_snapshots | ❌ | ❌ | P1 |
| 对侧 what-if 分析 | ❌ 只存 chosen side | ❌ | ❌ | P1 |

---

## 5. 建议的演进路线

### ✅ Phase 1 — P0 补字段（**已完成，2026-05-17**）

1. ✅ `signals` 加了 6 列：`city_pool / forecast_source / condition_id / market_id / icao / hours_to_settle`
2. ✅ `real_ledger_adapter.py` 透传全部 6 字段（含 hours_to_settle float 转换）
3. ✅ `ledger_csv.py` `INSERT OR IGNORE` 写入 6 列；DB 已用 rebuild 路径重建（data 全部填充）
4. ✅ `GET /api/runs/{id}/metrics/slice?group_by=city_pool|forecast_source|model_version|side|city` 已上线
5. ✅ `GET /api/runs/{id}/trades?city_pool=&forecast_source=` 已支持过滤，response 含 4 个新字段
6. ✅ `WeatherHistoryPage` 加了 Pool + Source 下拉，URL 同步

**RunsPage 不加 city_pool/forecast_source 过滤器**（run 级页面，一个 run 含 T1+T2，在 run 级过滤无意义；切片看 History 页或 metrics/slice 端点）。

### Phase 2 — P1 (~ 1 周)

1. **新表** `market_snapshots`（30-min 全量盘口）。新建 ingest 脚本扫 `paper_snapshots/snapshot_*.json`。
2. **改 signals ingest**：每个 (snapshot, market) 同时插 YES + NO 双 signal。
3. **改 orders**：加 `exchange_response / venue / execution_id / limit_price / notional`。
4. **改 plans**：把 `plan_id` 用 N100 源头的 execution_id，加 `sizing_mode / entry_price_window / execution_policy / notional`。
5. 新建 `weather_dashboard/ingest/live_cycle_jsonl.py`，专门吃 live 的 signals/plans/orders jsonl 三件套。
6. **改 settlements**：`final_yes INTEGER` → `final_price TEXT`，old code 用 `int(round(float(final_price)))` 转。
7. 新建 live reconciliation ingest/report：吃 local + N100 live JSONL、CLOB fills、Data API current/closed positions、pm_history，输出 unmatched submitted/fill/position rows。

### Phase 3 — P2 (~ 1 个月)

1. `weather_observations` + `weather_forecasts` 表 + ingest from `cache/iem/`、`cache/wu_obs/`。
2. `run_alerts` 表 + ingest from `live_cycle/*.json`。
3. `account_snapshots` 表（从 N100 拉 `weather_live_status.py status --json`）。
4. `signal_features` 长表 + 模型重训管线对接。

---

## 6. 重要约定（任何 agent 看到这份文档都要遵守）

### 6.1 表结构演进
- **不要破坏 append-only**: 所有"补字段"操作走 `ALTER TABLE ... ADD COLUMN ... DEFAULT NULL`，不要 DROP/重建表（除非走完整 rebuild 流程）。
- **不要在 DB 里做派生计算的源头**: pm_history 是 N100 的真实源，本机 DB 是分析镜像，不要在 DB 里"修正"final_price。

### 6.2 ingest 行为（重要、容易踩坑）

当前实现的事实：
- `signals` 用 `INSERT OR IGNORE`（按 `signal_id` 主键去重）
- `plans / orders / fills` 用普通 `INSERT`（依赖 ingest 前的去重逻辑 + ingestion_log）
- `ingestion_log` 按 `(source_path, source_row_hash, target_table)` 去重，`row_hash = sha256(canonical_json(row))`

加新字段后的行为：

| 场景 | 结果 |
|---|---|
| **走 `run_stack.sh` 默认（rebuild）** | DB 整个删掉重建，新字段对所有数据生效。**推荐路径。** |
| **走 `run_stack.sh --no-rebuild`** | adapter 透传新字段后，row_hash 变化 → ingestion_log 认为是新行 → 但 `signals.INSERT OR IGNORE` 看到相同 signal_id 直接跳过 → 旧 signal 的新字段仍为 NULL；同时 plans/orders/fills 可能会触发 PK 冲突或重复插入。**不推荐，会产生半新半旧的数据。** |

迁移建议：

- Phase 1 P0 字段补完后，**第一次必须 rebuild**（删 `runtime/weather.db` 重 ingest）。
- 如果未来必须做增量迁移（DB 很大重建代价高），先做这两件事之一：
  1. 把 `signals` 改成 `INSERT ... ON CONFLICT(signal_id) DO UPDATE SET city_pool=excluded.city_pool, ...`（白名单允许更新的字段）。
  2. 或加 `schema_version` migration 脚本，单独跑一次 `UPDATE signals SET city_pool=... WHERE signal_id IN (...)`——这会被 append-only trigger 拦截，需要先临时 `DROP TRIGGER signals_before_update`，迁移完再 `CREATE TRIGGER` 回来。这是有意为之的高摩擦设计，迁移必须显式、留痕。

不要在没想清楚之前就在 `--no-rebuild` 模式下加新字段重 ingest。

### 6.3 数据真相分层
- **N100 = 生产真相**: live 链路、settlement、pm_history 全在 N100。
- **本机镜像 = 分析快照**: `runtime/weather_edge_v1/market_data/` 是 rsync 镜像，不写回 N100。
- **本机 weather.db = 镜像派生**: 完全可以删了重建，**不要把它当源头**。研究/回测产物想长期保留的，落到独立目录或 git 跟踪的文件，别只留在这个 DB 里。

---

## 7. 决策记录

- **2026-05-17**: 当前 schema 通过 paper(608) + snapshot_replay(636) 验证，基本 PnL 准确。
- **2026-05-17**: 早期 live 历史确认不能合并成一个 run：target 2026-05-14 local duplicate-risk，target 2026-05-15/16 local wrong-universe，target 2026-05-16 N100 legacy，target 2026-05-17+ N100 current T1 `$5` notional。Dashboard 后续必须按 `execution_host/run_family/run_quality` 回填。
- **2026-05-17 (P0 完成)**: 6 列 P0 字段已加入 `signals`，全栈贯通。实测切片：T1 ROI +11.8%（397 trades）、T2 ROI +5.0%（239 trades）；ECMWF ROI +10.1%（327 trades）、GFS ROI +8.2%（309 trades）。`metrics/slice` API 和 HistoryPage 过滤器上线。下一步 Phase 2 (P1)：`market_snapshots` 表、双 side 信号 ingest、`orders.exchange_response`、live reconciliation。
