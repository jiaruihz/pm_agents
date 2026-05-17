# Weather Dashboard 数据模型分层与缺口审计

> 最后更新: 2026-05-17  
> 范围: 本机 `weather_dashboard/` SQLite + ingest + API。**不涉及** N100 生产链。  
> 目的: 在数据格式僵化前先识别缺口，避免后期回填困难。

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

### 2.1 N100 paper_orders.jsonl — 最完整的源（46 字段）

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

### ❌ 缺口 — 按影响优先级分

#### P0 — 现在就影响研究 (1-2 周内必须补)

| 缺口 | 影响 | 修复 |
|---|---|---|
| **`signals.city_pool`** (t1_trading / t2_research) | 无法做 T1 vs T2 切片对比，而这是当前最重要的分层 | 加一列 `city_pool TEXT` |
| **`signals.forecast_source`** (gfs/ecmwf/open_meteo_live_gfs) | 模型对比只能看 model_version (gfs/ecmwf)，看不到具体预报源版本 | 加一列 `forecast_source TEXT` |
| **`signals.condition_id` + `market_id`** | 无法回溯到 Polymarket 真实合约，无法 join 后续盘口数据 | 加两列 |
| **`signals.icao`** | 无法 join 天气观测/预报数据做特征工程 | 加一列 |
| **`signals.hours_to_settle`** | 重要回测特征（不同 ttm 表现差异大） | 加一列 |

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

---

## 4. 当前管线对 3 类 run 的支持度

| 场景 | snapshot_replay (回测) | paper | live | 备注 |
|---|---|---|---|---|
| 基本 PnL/胜率 | ✅ | ✅ | ✅ | 已验证 (636/608 trades) |
| 城池切片 T1 vs T2 | ❌ 无 city_pool | ❌ | ❌ | **P0** |
| 模型源对比 (ecmwf vs gfs) | ⚠️ 仅 model_version | ⚠️ | ⚠️ | P0 缺 forecast_source |
| 天气准确度归因 | ❌ 无观测/预报数据 | ❌ | ❌ | P2 |
| Live 失败归因 | ❌ 无 exchange_response | ❌ | ❌ | P1 必须 |
| 可复现实验 | ⚠️ repro_key 有，但 plan 不全 | ⚠️ | ⚠️ | P1 |
| 盘口 timing 优化 | ❌ 无 market_snapshots | ❌ | ❌ | P1 |
| 对侧 what-if 分析 | ❌ 只存 chosen side | ❌ | ❌ | P1 |

---

## 5. 建议的演进路线

### Phase 1 — P0 补字段（~ 半天工作量，向后兼容）

1. 给 `signals` 加 `city_pool / forecast_source / condition_id / market_id / icao / hours_to_settle` 6 列（都允许 NULL）。
2. `weather_dashboard/ingest/real_ledger_adapter.py` 透传这些字段。
3. 重新 `ingest-real` + `ingest-paper`（content-addressable hash 会保持去重）。
4. 前端 `RunsPage` / `HistoryPage` 加 `city_pool` / `forecast_source` 过滤器。
5. 后端 `/api/runs/{id}/metrics?group_by=city_pool` 切片端点。

### Phase 2 — P1 (~ 1 周)

1. **新表** `market_snapshots`（30-min 全量盘口）。新建 ingest 脚本扫 `paper_snapshots/snapshot_*.json`。
2. **改 signals ingest**：每个 (snapshot, market) 同时插 YES + NO 双 signal。
3. **改 orders**：加 `exchange_response / venue / execution_id / limit_price / notional`。
4. **改 plans**：把 `plan_id` 用 N100 源头的 execution_id，加 `sizing_mode / entry_price_window / execution_policy / notional`。
5. 新建 `weather_dashboard/ingest/live_cycle_jsonl.py`，专门吃 live 的 signals/plans/orders jsonl 三件套。
6. **改 settlements**：`final_yes INTEGER` → `final_price TEXT`，old code 用 `int(round(float(final_price)))` 转。

### Phase 3 — P2 (~ 1 个月)

1. `weather_observations` + `weather_forecasts` 表 + ingest from `cache/iem/`、`cache/wu_obs/`。
2. `run_alerts` 表 + ingest from `live_cycle/*.json`。
3. `account_snapshots` 表（从 N100 拉 `weather_live_status.py status --json`）。
4. `signal_features` 长表 + 模型重训管线对接。

---

## 6. 重要约定（任何 agent 看到这份文档都要遵守）

- **不要破坏 append-only**: 所有"补字段"操作走 `ALTER TABLE ... ADD COLUMN ... DEFAULT NULL`，不要 DROP/重建表。已存历史数据保留。
- **不要在 DB 里做派生计算的源头**: pm_history 是 N100 的真实源，本机 DB 是分析镜像，不要在 DB 里"修正"final_price。
- **保持 ingest idempotent**: `ingestion_log(source_path, row_hash)` 是去重 key，加新字段时 row_hash 会变——这是想要的行为（被识别为新行）。
- **回填策略**: 加字段后第一次重 ingest，旧数据 city_pool/forecast_source 仍是 NULL。这是 OK 的——用 `WHERE city_pool IS NOT NULL` 过滤即可。不要为了"补齐历史"去 UPDATE，触发 append-only 保护。

---

## 7. 决策记录

- **2026-05-17**: 当前 schema 通过 paper(608) + snapshot_replay(636) 验证，基本 PnL 准确。下一步必须先补 P0（特别是 `city_pool`），否则 T1/T2 分层研究无法继续。
