# Weather Dashboard 血缘与字段契约改造方案

> 日期: 2026-05-17  
> **实施状态: P0（字段补全 + metrics/slice API + 前端过滤器）已于 2026-05-17 完成。本文剩余内容为 P1 实施细节（live ingest、signal_id 统一算法、plans/orders 补字段）。**  
> 范围: 本机 `pm_agent` weather dashboard DB / ingest / API / FE。生产端 N100 字段改名只列依赖，不在本文直接执行。  
> 依赖契约: [WEATHER_SYSTEM_CONTRACT.md](WEATHER_SYSTEM_CONTRACT.md)

## 0. 改造目标

当前 dashboard 的 SQL 链路已经能连通:

```text
signals -> plans -> orders -> fills -> settlements
```

但多数 ID、配置和执行上下文来自本机 CSV ingest 的二次生成，不能完整还原 N100 的真实决策链。改造目标是:

1. 字段名按契约统一，保留短期兼容读取。
2. paper / snapshot_replay / live 共用同一套 canonical DB 字段。
3. live 的真实 `signal_id / plan_id / execution_id / exchange_response` 入库。
4. 前端能从单条 trade 纵向钻取完整血缘。
5. 所有改动保持 append-only，不 UPDATE 旧事实数据。

## 1. 当前不一致清单

| 概念 | paper CSV 当前 | live signal 当前 | live order 当前 | DB 当前/目标 |
|---|---|---|---|---|
| 模型名 | `model` | `model_version` | - | `model_version` |
| 模型概率 | `model_prob` | `model_probability_yes` | - | `model_p_yes` |
| YES 价格 | `market_yes_price` | `market_price` | - | `market_price` |
| 事件日期 | `event_date` | `target_date` | `target_date` | `target_date` |
| 预报源 | `forecast_source` | `profile` | - | `forecast_source` |
| 信号方向 | `side=BUY_YES/BUY_NO` | `signal_side=BUY_NO` | `signal_side=BUY_NO` | `signals.side=YES/NO`，另存 raw `signal_side` 可选 |
| 订单方向 | `side=BUY_YES/BUY_NO` | `order_side=BUY` | `order_side=BUY` | `orders.side=BUY_YES/BUY_NO` |
| signal_id | 无，本机算 32 位 | 64 位 SHA256 | 64 位 SHA256 | 目标直接透传 64 位 |
| plan_id | 本机算 32 位 | cycle plan 文件里有 | 64 位 SHA256 | 目标直接透传 64 位 |
| execution_id | 无 | - | 64 位 SHA256 | `orders.execution_id` |
| venue response | 无 | - | `exchange_response` JSON | `orders.exchange_response` |

## 2. 改造原则

- **契约优先**: 新代码只向 `WEATHER_SYSTEM_CONTRACT.md` 收敛；兼容旧字段时必须在 adapter 中显式写出别名。
- **不重写事实**: `signals/plans/orders/fills/ingestion_log` 继续 append-only。需要补字段时用 `ALTER TABLE ... ADD COLUMN ... DEFAULT NULL`。
- **rebuild 优先于原地修补**: 早期 dashboard DB 可由镜像文件重建。字段形态变更后优先 `run_stack.sh` rebuild，避免在 append-only 表上 UPDATE。
- **ID 只认源头**: 一旦源文件带 `signal_id/plan_id/execution_id`，pm_agent 不再自行重算。
- **旧 CSV 暂时可用**: N100 未完全改名前，`real_ledger_adapter.py` 继续接收旧名，输出 canonical 字段。

## 3. 分阶段改造

### Phase 1: P0 字段入库与切片

目的: 支持 T1/T2、forecast_source、market identity、ICAO、TTM 切片。

DB:

- `signals` 增加:
  - `city_pool TEXT`
  - `forecast_source TEXT`
  - `condition_id TEXT`
  - `market_id TEXT`
  - `icao TEXT`
  - `hours_to_settle REAL`

Ingest:

- `real_ledger_adapter.py` 输出以上 6 列。
- `ledger_csv.py` 插入 `signals` 时透传以上 6 列。
- 对旧字段保留别名:
  - `event_date -> target_date`
  - `model -> model_version`
  - `model_prob -> model_p_yes`
  - `market_yes_price -> market_price`

API/FE:

- `/runs/{run_id}/trades` 支持 `city_pool`、`forecast_source` 过滤。
- `/runs/{run_id}/metrics/slice?group_by=city_pool|forecast_source|model_version|side|city` 返回切片指标。
- History 页面先加 trade 级过滤器，不在 Runs 列表做 run 级过滤。

验收:

```bash
scripts/weather_dashboard/run_stack.sh --no-rebuild --status
scripts/weather_dashboard/run_stack.sh
sqlite3 runtime/weather.db "select city_pool, forecast_source, count(*) from signals group by 1,2 order by 3 desc limit 10;"
curl -s "http://localhost:8000/runs/<run_id>/metrics/slice?group_by=city_pool"
```

### Phase 2: 源头 ID 与 final_price 兼容迁移

目的: 让 paper/snapshot/live 的同一信号可以跨文件关联。

DB:

- `signals` 增加:
  - `source_signal_id TEXT` 或直接把 `signal_id` 切到 64 位源头 ID。
- `plans` 增加:
  - `source_plan_id TEXT`
  - `notional TEXT`
  - `sizing_mode TEXT`
  - `entry_price_window TEXT`
  - `execution_policy TEXT`
  - `limit_price TEXT`
- `orders` 增加:
  - `execution_id TEXT`
  - `venue TEXT`
  - `exchange_response TEXT`
  - `limit_price TEXT`
  - `notional TEXT`
  - `token_id TEXT`
- `settlements` 增加:
  - `final_price TEXT`
  - `token_id TEXT`

ID 策略:

- 如果源 row 有 `signal_id`，直接使用 64 位 `signal_id`。
- 如果源 row 没有 `signal_id`，继续用 legacy 32 位算法，但记录 `id_source='pm_agent_legacy'`。
- `plans.plan_id` 同理: 源头有 `plan_id` 直接用，否则 legacy 生成。
- `orders.order_id` paper CSV 继续用原 `order_id`；live order 优先 `exchange_response.place.orderID`，没有则用 `execution_id`。

注意:

- SQLite 不适合原地改 PRIMARY KEY。早期阶段建议 rebuild DB，避免把 32 位和 64 位 ID 混在同一个 run 里。
- `final_yes` 暂时保留，新增 `final_price`。PnL 计算先用:

```text
resolved_yes = int(round(float(final_price))) if final_price is not NULL else final_yes
```

验收:

```bash
sqlite3 runtime/weather.db "select length(signal_id), count(*) from signals group by 1;"
sqlite3 runtime/weather.db "select count(*) from orders where execution_id is not null;"
sqlite3 runtime/weather.db "select count(*) from settlements where final_price is not null;"
```

### Phase 3: Live Cycle Ingest

目的: 把 N100 live 的真实血缘入库。

新增模块:

```text
weather_dashboard/ingest/live_cycle_jsonl.py
```

输入:

- `runtime/weather_edge_v1/live_cycle/*.json`
- `runtime/weather_edge_v1/signals/live_*_signals.jsonl`
- `runtime/weather_edge_v1/plans/live_*_trade_plans.jsonl`
- `runtime/weather_edge_v1/live/live_*_orders.jsonl`
- `runtime/weather_edge_v1/paper/live_*_paper_orders.jsonl`
- `runtime/weather_edge_v1/remote_pm_agent/...` 同构目录

实现要点:

1. 先读 `live_cycle/*.json`，以 `paths.signal/plan/live/paper/summary` 作为 manifest。
2. 对 `paths` 中的绝对路径做本机镜像映射:
   - `/home/rui/projects/pm_agent/runtime/weather_edge_v1/...`
   - `/home/jiarui/projects/pm_agent/runtime/weather_edge_v1/...`
   - `/home/jiarui/projects/weather-predict/...`
3. `signals` JSONL:
   - `profile -> forecast_source`
   - `model_probability_yes -> model_p_yes`
   - `snapshot_fetched_at_utc -> snapshot_ts_utc`
   - `signal_side=BUY_NO/BUY_YES` 归一为 `signals.side=NO/YES`
4. `orders` JSONL:
   - `signal_side=BUY_NO/BUY_YES` 可临时推导 `orders.side`
   - `order_side=BUY` 保留到 raw 字段或忽略，不作为 canonical order side
   - `size -> shares`
   - `posted_notional/notional -> cost_usd` 按字段可用性选择，并保留原 notional
   - `exchange_response` JSON 序列化为 TEXT
5. `live_cycle` JSON:
   - `config` 存入 `strategy_config.params`
   - `contract_alerts` 进入 `run_alerts`
   - `executor.balance_preflight` 后续进入 `account_snapshots`，Phase 3 可先存入 `runs.metrics` 或 `run_metadata`

建议新增表:

```sql
CREATE TABLE IF NOT EXISTS run_alerts (
    alert_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    alert_ts_utc TEXT,
    severity TEXT,
    kind TEXT,
    payload TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    artifact_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    row_count INTEGER,
    payload TEXT,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

验收:

```bash
python -m weather_dashboard.cli.ingest_live_cycles \
  --db-path runtime/weather.db \
  --root runtime/weather_edge_v1/remote_pm_agent \
  --limit 10

sqlite3 runtime/weather.db "select count(*) from runs where execution_mode='live';"
sqlite3 runtime/weather.db "select count(*) from orders where execution_id is not null and exchange_response is not null;"
sqlite3 runtime/weather.db "select count(*) from plans where execution_policy is not null;"
```

### Phase 4: Trade 血缘钻取页

目的: 从平铺交易表升级为单条交易的纵向复盘。

API:

```text
GET /runs/{run_id}/trades/{signal_id}
```

返回结构:

```json
{
  "signal": {},
  "plan": {},
  "order": {},
  "fill": {},
  "settlement": {},
  "run": {},
  "artifacts": [],
  "alerts": []
}
```

前端:

```text
/weather/trade/:runId/:signalId
```

页面展示:

```text
[Signal]     target_date / city / bracket / model / edge / market_price / city_pool
    ↓
[Plan]       notional / sizing_mode / entry_price_window / execution_policy / skip_reason
    ↓
[Order]      venue / limit_price / posted_price / status / execution_id / exchange_response
    ↓
[Fill]       shares / filled_price / cost_usd / fees / filled_at
    ↓
[Settlement] final_price / token_id / PnL / ROI / settlement_status
```

History 表里每行 `signal_id` 加 link 到该页。没有 live execution 的 paper 行仍显示 Signal/Plan/Order/Fill/Settlement，Order 的 venue/exchange_response 为空。

验收:

```bash
curl -s "http://localhost:8000/runs/<run_id>/trades/<signal_id>" | jq .
npm --prefix frontend/strategy_dashboard run build
```

### Phase 5: L1/L2 原始数据入库

目的: 支持 entry timing 优化、特征工程和天气归因。

新增表:

- `market_snapshots`: 从 `paper_snapshots/snapshot_*.json` 提取 30 分钟盘口。
- `weather_observations`: 从 `cache/iem/*.csv` 和 `cache/wu_obs/*.csv` 提取观测。
- `weather_forecasts`: 从 GFS/ECMWF cache 或快照源提取预报。
- `signal_features`: 长表，保存模型训练/归因使用的特征。

这阶段不是当前 blocker。只有当 Phase 1-4 的血缘链跑通后再做。

## 4. N100 侧依赖

N100 应优先修正以下字段，pm_agent adapter 在一个迭代周期内保留兼容:

| 优先级 | N100 改动 | pm_agent 兼容 |
|---|---|---|
| P0 | CSV 增加 `signal_id` | 有则透传，无则 legacy 生成 |
| P0 | `event_date -> target_date` | `_get("target_date", "event_date")` |
| P0 | `model -> model_version` | `_get("model_version", "model")` |
| P0 | `model_prob/model_probability_yes -> model_p_yes` | `_get("model_p_yes", "model_prob", "model_probability_yes")` |
| P0 | `market_yes_price -> market_price` | `_get("market_price", "market_yes_price")` |
| P0 | `profile -> forecast_source` | `_get("forecast_source", "profile")` |
| P0 | live `order_side=BUY` 改为 `BUY_YES/BUY_NO` | 短期从 `signal_side` 推导 |
| P1 | paper/live 都输出 `plan_id` | 有则透传 |
| P1 | live order 输出 `posted_notional/cost_usd` 语义固定 | adapter 明确映射 |

## 5. 推荐落地顺序

1. 合并并验证 Phase 1 P0 字段入库，确保 T1/T2 和 forecast_source 切片可用。
2. 调整 contract 中 ID 策略，明确 legacy 和 source ID 共存窗口。
3. 实现 Phase 2 DB 列扩展和 adapter canonical 输出。
4. 实现 Phase 3 live cycle ingest，只跑 `--limit 10` 验证 schema。
5. 增加 `/trades/{signal_id}` API 和前端 drilldown 页面。
6. N100 完成字段改名后，移除 adapter 的旧字段别名。

## 6. 不建议现在做

- 不要现在把 `final_yes` 删除；先新增 `final_price` 并双读。
- 不要在 append-only 表上批量 UPDATE 历史 row；早期 DB 用 rebuild。
- 不要让 RunsPage 做 `city_pool` 过滤，除非新增 run-level aggregate。
- 不要先做 L2 weather 入库；当前最大收益是血缘链和 live ingest。
- 不要让 pm_agent 推断 T1/T2 城市池；以源文件 `city_pool` 为准。

## 7. 完成定义

这次重构完成时，应同时满足:

- `signals` 中 P0 字段非空率可统计，T1/T2 切片可用。
- paper/snapshot/live 都能进入同一 DB，且 live order 有 `execution_id`。
- 任意 live trade 可通过 `signal_id` 找到 signal、plan、order、fill、run cycle artifact。
- 契约文档列出的旧字段别名只存在于 adapter，不泄漏到 DB/API 新字段。
- `scripts/weather_dashboard/run_stack.sh` rebuild 后不需要手动 SQL 修补。
