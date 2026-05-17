# Weather Dashboard Canonical 最终态与迁移方案

> 日期: 2026-05-17  
> **实施状态: 字段契约已提取为 [WEATHER_SYSTEM_CONTRACT.md](WEATHER_SYSTEM_CONTRACT.md)（更权威）。本文的 P0 迁移（6列补全）已完成；P1 部分（signal_id 统一算法、live ingest 管道、plans/orders 补字段）仍在待办。**  
> 目标: 直接重建 dashboard DB 到完整血缘模型，同时允许 N100 和本机 pm_agent 都能生产同一套规范信号。  
> 原则: 新链路干净、旧数据可迁移、兼容逻辑集中在一次性迁移层，不进入长期业务代码。

## 1. 核心判断

我们不应该把 N100 作为唯一 ID 源头。更合理的最终态是:

```text
N100 weather-predict      -> canonical signal/plan/order files
本机 pm_agent             -> canonical signal/plan/order files
                    两边共用同一份字段契约和 ID 算法
                    ↓
              weather_dashboard canonical DB
```

原因:

- N100 是生产数据真相，但它不是 99.9% 可靠的唯一计算环境。
- 本机 pm_agent 也需要能复现同一批 signals，用于回测、debug、灾备和策略开发。
- 真正要统一的是 **契约 + deterministic ID 算法**，不是“只能由某台机器生成”。

因此 canonical 最终态的核心不是 N100-only，而是 **producer-neutral**:

```text
producer_system = n100 | pm_agent_local
producer_run_id = 源系统本轮运行 ID
signal_id       = 两边用同一算法算出的稳定 ID
```

## 2. 最终态字段契约

新代码只认 canonical 字段。旧字段只允许出现在 `legacy_migration` 里。

### Signal

| 字段 | 说明 |
|---|---|
| `signal_id` | deterministic 64 hex |
| `producer_system` | `n100` / `pm_agent_local` |
| `producer_run_id` | 源系统 run/cycle/snapshot ID |
| `snapshot_ts_utc` | 信号使用的盘口 snapshot 时间 |
| `snapshot_file` | 源 snapshot 文件 |
| `target_date` | 结算日期 |
| `city` | 城市 |
| `city_pool` | `t1_trading` / `t2_research` |
| `icao` | 天气站 |
| `bracket` | 温度区间 |
| `unit` | `F` / `C` |
| `signal_side` | `YES` / `NO` |
| `model_version` | `gfs` / `ecmwf` |
| `model_p_yes` | 模型 P(YES) |
| `forecast_source` | `open_meteo_live_gfs` / `open_meteo_live_ecmwf` |
| `market_price` | YES price |
| `edge` / `abs_edge` | edge |
| `condition_id` / `market_id` / `token_id` | Polymarket identity |
| `hours_to_settle` | TTM feature |

### Plan

| 字段 | 说明 |
|---|---|
| `plan_id` | deterministic 64 hex |
| `run_id` | dashboard run |
| `signal_id` | FK |
| `config_id` | strategy config |
| `order_side` | `BUY_YES` / `BUY_NO` |
| `notional` | 目标下单金额 |
| `desired_shares` | 目标份额 |
| `sizing_mode` | `notional` / `fixed_shares` |
| `entry_price_window` | 例如 `0.25-0.75` |
| `execution_policy` | 例如 `mid_price_core_v1` |
| `limit_price` | 计划限价 |
| `skip_reason` | NULL 表示计划下单 |

### Order / Fill

| 字段 | 说明 |
|---|---|
| `execution_id` | 每次执行尝试的稳定 ID |
| `order_id` | 真实 CLOB orderID；paper 可用 deterministic ID |
| `venue` | `polymarket_clob` / `paper` / `snapshot_replay` |
| `order_side` | `BUY_YES` / `BUY_NO` |
| `limit_price` | 请求限价 |
| `entry_price` | 实际/模拟成交价 |
| `shares` | 成交份额 |
| `cost_usd` | 成本 |
| `status` | `submitted` / `filled` / `error` / `skipped` |
| `exchange_response` | JSON TEXT，live 才有 |

### Settlement

| 字段 | 说明 |
|---|---|
| `settlement_id` | deterministic 64 hex |
| `target_date` / `condition_id` / `market_id` / `bracket` | 结算定位 |
| `token_id` | 可选 |
| `final_price` | 0.0-1.0，不再用 `final_yes` 作为主字段 |
| `settlement_status` | `settled` / `missing_event` / `missing_bracket` |

## 3. ID 算法

ID 必须用一个共享实现维护，不能散落在脚本里。建议新增:

```text
src/strategies/weather_edge_v1/ids.py
weather_dashboard/contract/ids.py  # 可薄封装复用上面实现
```

### signal_id

同一信号无论 N100 还是本机产生，都应一致:

```text
signal_id = sha256(
  "weather_edge_v1|"
  target_date + "|" +
  city + "|" +
  bracket + "|" +
  signal_side + "|" +
  model_version + "|" +
  forecast_source + "|" +
  snapshot_ts_utc + "|" +
  condition_id
)
```

说明:

- 加 `condition_id` 是为了避免城市/日期/区间文本相同但市场换合约时误撞。
- 不把 `producer_system` 放进 `signal_id`，否则 N100 和本机无法对齐同一信号。
- 如果本机复现时拿不到 `condition_id`，这批数据不能进入 canonical signal，只能进入 legacy migration 或 debug 输出。

### plan_id

```text
plan_id = sha256(run_id + "|" + signal_id + "|" + order_side + "|" + execution_policy)
```

说明:

- plan 属于某个 dashboard run，因此包含 `run_id`。
- 同一个 signal 在不同策略配置/不同 run 下可以有不同 plan。

### execution_id

```text
execution_id = sha256(run_id + "|" + plan_id + "|" + venue + "|" + attempt_index)
```

说明:

- live 如果 N100 已经输出 `execution_id`，要求 N100 也按这个算法或等价稳定算法输出。
- paper/snapshot_replay 由 pm_agent 按同一规则生成。

### order_id

- live: 优先用 CLOB 返回的 `exchange_response.place.orderID`。
- paper/snapshot_replay: `sha256(execution_id + "|paper_fill")`。

## 4. Canonical DB：直接重建

不在旧 schema 上做长期迁移。canonical DB 最终就使用:

```text
runtime/weather.db
```

切换前可以短暂备份旧库；切换后 `run_stack.sh` 只指向这一份 canonical DB。这样有两个好处:

- 可以用备份库短期对比旧/新指标。
- 出错时不会破坏当前看板。

canonical 表保持少而完整:

```text
runs
strategy_config
universes
code_versions
signals
plans
orders
fills
settlements
run_artifacts
run_alerts
ingestion_log
```

暂时不做:

- `weather_observations`
- `weather_forecasts`
- `signal_features`
- `account_snapshots`

这些不是解决当前血缘问题的必需项。

## 5. 新数据写入路径

新数据只走 canonical ingest:

```text
canonical files -> strict validator -> canonical ingest -> runtime/weather.db
```

长期保留的 ingest:

```text
weather_dashboard/ingest/canonical.py
weather_dashboard/ingest/live_cycle.py
weather_dashboard/ingest/settlements.py
```

这些 ingest 的规则:

- 缺 canonical 必填字段就 fail。
- 不猜旧字段名。
- 不生成缺失的 market identity。
- 不 UPDATE append-only facts。

本机 pm_agent 生产信号时，也必须先写 canonical files，再走同一 ingest。不要绕过文件契约直接写 DB。

## 6. 存量迁移路径

旧数据迁移需要，但它应该是一次性、隔离的:

```text
legacy CSV / legacy JSONL -> legacy_migration adapter -> canonical rows -> canonical ingest
```

建议目录:

```text
weather_dashboard/legacy_migration/
  paper_csv.py
  live_jsonl.py
  validators.py
```

迁移规则:

1. 可以读旧字段:
   - `event_date -> target_date`
   - `model -> model_version`
   - `model_prob/model_probability_yes -> model_p_yes`
   - `market_yes_price -> market_price`
   - `profile -> forecast_source`
   - `side=BUY_NO -> signal_side=NO, order_side=BUY_NO`
2. 迁移输出必须是 canonical row。
3. 如果缺 `condition_id`，不进 canonical `signals`，写入迁移报告。
4. 如果缺 `signal_id`，用 canonical deterministic 算法补算，并标记:
   - `producer_system = legacy_migration`
   - `producer_run_id = source file stem`
5. 如果 live `order_side=BUY`，优先从 `signal_side=BUY_NO/BUY_YES` 推导；无法推导则跳过 order，写迁移报告。
6. 不保留旧字段到 DB。

迁移报告必须输出:

```text
runtime/weather_dashboard_migration/reports/<timestamp>.json
```

包含:

- 输入文件数
- 成功 signals/plans/orders/fills/settlements 数
- skipped rows
- skipped reasons top N
- legacy 字段使用统计
- ID 补算数量

## 7. 实盘链路迁移

实盘链路要分两步，不要一次切所有东西。

### Step A: 双写文件，不切看板

N100 和本机都开始写 canonical files:

```text
runtime/weather_edge_v1/canonical/signals/*.jsonl
runtime/weather_edge_v1/canonical/plans/*.jsonl
runtime/weather_edge_v1/canonical/orders/*.jsonl
runtime/weather_edge_v1/canonical/live_cycle/*.json
runtime/weather_edge_v1/canonical/settlements/*.jsonl
```

旧文件继续写，生产不受影响。

验收:

- canonical files 每轮都有。
- canonical validator 通过。
- canonical DB ingest 后 run/trade 数量和旧 summary 大体一致。

### Step B: 看板切 canonical DB

`run_stack.sh` 增加:

```bash
WEATHER_DB_PATH=runtime/weather.db
```

看板切 canonical 后，旧 DB 备份只读保留一段时间。

### Step C: 停止旧 adapter

当 N100 和本机连续数天都能产出 canonical files 后:

- dashboard 主 ingest 删除旧字段兼容。
- legacy migration 保留，但不进入正常启动路径。

## 8. 历史数据迁移批次

建议按价值迁移，不追求一次性全量完美。

### Batch 1: 研究结算 CSV

输入:

```text
runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv
runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv
```

目标:

- 保住 paper/snapshot_replay 的 PnL、T1/T2、model/source 切片。
- 用 canonical 算法补 `signal_id/plan_id/execution_id`。

### Batch 2: live cycle

输入:

```text
runtime/weather_edge_v1/live_cycle/*.json
runtime/weather_edge_v1/signals/*.jsonl
runtime/weather_edge_v1/plans/*.jsonl
runtime/weather_edge_v1/live/*.jsonl
runtime/weather_edge_v1/remote_pm_agent/**/*
```

目标:

- 恢复 live `signal -> plan -> order -> exchange_response`。
- 允许部分 settlement 缺失。

### Batch 3: paper_orders.jsonl 原始账本

只在它比研究 CSV 多出有价值信息时迁移。否则不优先。

## 9. 前端最终态

保留三个核心页面，避免过度设计:

```text
/weather/runs
/weather/history/:runId
/weather/trade/:runId/:signalId
```

### Runs

只展示 run 级别信息:

- execution_mode
- producer_system
- date range
- trades
- pnl / roi
- alerts count

### History

交易表 + 必要过滤:

- city
- city_pool
- forecast_source
- model_version
- order_side
- settlement_status

### Trade Drilldown

单条纵向血缘:

```text
Signal -> Plan -> Order -> Fill -> Settlement
```

展示 JSON 原文时只放折叠区域，不在主界面堆大块 JSON。

## 10. 验收标准

canonical 切换完成必须满足:

1. 新 DB 可从空库 rebuild:

```bash
make -f Makefile.weather db-canonical-rebuild
```

2. 本机和 N100 对同一 canonical input 生成相同 `signal_id`。

3. canonical DB 里没有旧字段名:

```bash
sqlite3 runtime/weather.db ".schema" | grep -E "event_date|model_prob|market_yes_price|profile|final_yes" && exit 1 || true
```

4. live 至少能查到:

```sql
select count(*) from orders where execution_id is not null and exchange_response is not null;
```

5. 迁移报告清楚说明跳过了哪些历史 row，不能静默吞数据。

6. 前端能从 History 点进任意有 `signal_id` 的 trade drilldown。

## 11. 具体执行顺序

1. 写共享 ID 模块和字段 validator。
2. 新建 canonical schema 和 `runtime/weather.db` rebuild target。
3. 写 canonical ingest，先支持 research CSV 迁移后的 canonical rows。
4. 写 legacy migration Batch 1，迁移两个 research CSV。
5. 写 live cycle migration/ingest，迁移 Batch 2。
6. 改 API 指向 canonical 字段。
7. 改 History 页面和新增 Trade Drilldown。
8. N100 和本机开始双写 canonical files。
9. 新旧指标对齐后，`run_stack.sh` 默认切 canonical DB。
10. 把旧 adapter 移到 `legacy_migration/`，正常 ingest 不再引用。

## 12. 不做的事

- 不做复杂 event sourcing。
- 不做多版本 schema 兼容框架。
- 不在 DB 里保存旧字段别名。
- 不为了少量缺字段历史 row 破坏 canonical schema。
- 不把天气原始观测/预报入库作为本轮目标。

这套方案的重点是: **新世界简单严格，旧世界有门可进但只能经过迁移层。**
