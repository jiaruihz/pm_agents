# Weather Strategy — 系统接口契约

Status: current-source
Updated: 2026-08-03 WCIR decision contract and canonical build identity overlay
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed

> 版本: v1 · 2026-05-17  
> 范围: weather data producer ⟷ strategy executor ⟷ pm_agent canonical analysis 之间的数据接口规范
> 规则: 两侧任何 agent 修改字段名、枚举值、ID 格式前必须先更新本文档并对齐

---

## 0. 两侧职责边界

详细 repo/runtime 边界见 [`WEATHER_REPO_BOUNDARY.md`](WEATHER_REPO_BOUNDARY.md)。

| 系统 | 职责 | 不负责 |
|---|---|---|
| **Mac `weather_data_feed_service_runtime`** | 当前 market/orderbook snapshot、forecast curve、observation/source-event producer | 策略 selection、sizing、钱包、下单 |
| **Mac `pm_agents` strategy runners** | 当前 signal/plan/order/fill、live probe/paper/shadow runtime | canonical weather source 定义 |
| **Mac `pm_agents` canonical/dashboard** | `runtime/weather.db`、facts、看板、回测、研究、部署 staging | 替代 current raw/exchange evidence |
| **N100 `weather-predict` / `pm_agent`** | 事故前历史、备份恢复目标；恢复验证后才可重新承接生产 | 恢复前充当 present-state truth |

N100 两个 repo 产出的文件格式 = 本文档约定的契约。pm_agent dashboard
消费这些文件，**不应** 静默地把字段改名再存 DB（会造成双方字段漂移）。
如果 DB 字段名与 CSV/JSONL 不同，必须在本文档里显式标注「别名」和迁移时间。

### 0.1 WCIR 决策契约覆盖层

本文后续 `Signal` 字段仍是 legacy/canonical compatibility contract，不是新城市 runtime 的完整内部边界。
WCIR 的权威链路为：

```text
EventEnvelope -> DecisionContext -> ModelOutput -> SignalCandidate -> TradeIntent
-> shared execution handoff -> plan -> order -> fill -> settlement
```

具体 typed contract 以 `WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md` 与 `weather_city_runtime/` 为准。
跨层投影必须保留四时钟、revision parent、model/artifact/config/schema/runtime hash、
`candidate_grain_version`、source/official/expression anchor、`feature_book_snapshot_id`、
`execution_book_snapshot_id` 和 intent/handoff blocker。legacy adapter 不得伪造无法证明的 intent/token/outcome。

canonical 物化与报告还必须保存 DB identity、materialization `build_id`/build time 与
`observed_at_utc`。同一次分析不得静默混合不同 build。

---

## 1. 规范字段名（canonical field names）

以下是每个概念的**唯一正确字段名**，两侧都必须用。

### 1.1 Signal / 信号

| 概念 | 规范字段名 | 类型 | 说明 |
|---|---|---|---|
| 信号唯一ID | `signal_id` | TEXT (64位hex) | 见 §2 |
| 信号生成时间 | `snapshot_ts_utc` | TEXT ISO-8601 | |
| 信号来源文件 | `snapshot_file` | TEXT | |
| 结算日期 | `target_date` | TEXT YYYY-MM-DD | ~~`event_date`~~ 废弃 |
| 城市 | `city` | TEXT | |
| 温度区间 | `bracket` | TEXT | |
| 信号方向 | `signal_side` | TEXT YES/NO | 见 §3 |
| 天气模型 | `model_version` | TEXT | ~~`model`~~ 废弃 |
| 模型P(YES) | `model_p_yes` | REAL 0-1 | ~~`model_prob`~~ ~~`model_probability_yes`~~ 废弃 |
| YES盘口价 | `market_price` | REAL 0-1 | ~~`market_yes_price`~~ 废弃 |
| 预报源 | `forecast_source` | TEXT | ~~`profile`~~ 废弃；见 §4 |
| 预报最高温 | `forecast_max_f` | REAL °F | Open-Meteo hourly `temperature_2m` 目标日最大值 |
| 预报最高温（市场单位） | `forecast_max_native` | REAL °C/°F | 若该城市合约单位为 C 则由 `forecast_max_f` 转 C；F 城市同 `forecast_max_f` |
| 预报最高温小时（本地） | `forecast_peak_hour_local` | INTEGER 0-23 | 目标日 hourly forecast 首个最高温小时，按 Open-Meteo `timezone=auto` 的本地时间 |
| 预报最高温时间（本地） | `forecast_peak_time_local` | TEXT ISO-like | 例如 `2026-06-16T14:00` |
| 预报最高温小时（UTC） | `forecast_peak_hour_utc` | INTEGER 0-23 | 用 forecast response `utc_offset_seconds` 从本地峰值时间换算 |
| 预报最高温时间（UTC） | `forecast_peak_time_utc` | TEXT ISO-8601 | 例如 `2026-06-16T06:00:00Z` |
| 预报小时数 | `forecast_hourly_count` | INTEGER | 该 target_date 可用 hourly 温度点数量 |
| 预报序列hash | `forecast_values_hash` | TEXT | 对目标日 hourly `(time, temperature_2m)` 序列做 SHA256 前16位，用于判断 forecast 是否换版 |
| 预报曲线归档路径 | `forecast_hourly_curve_path` | TEXT | snapshot producer 写出的相对路径；真正曲线在 `forecast_hourly_curves/*.jsonl`，不要在每个 bracket 行重复存整条曲线 |
| 预报峰值源 | `forecast_peak_source` | TEXT | 通常同 `forecast_source`，例如 `open_meteo_live_gfs` |
| 预报源时区 | `forecast_timezone` | TEXT | Open-Meteo response timezone，用于审计本地峰值小时 |
| 预报UTC偏移秒 | `forecast_utc_offset_seconds` | INTEGER | Open-Meteo response `utc_offset_seconds` |
| 决策相对峰值小时 | `forecast_peak_delta_hours_local` | REAL | `decision_local_hour - forecast_peak_hour_local`；正数表示已过预报峰值小时 |
| 决策时刻预报温度 | `forecast_temperature_at_decision_f` | REAL °F | 从当时 PIT hourly curve 线性插值；不访问事后 API |
| 决策后的预报最高温 | `forecast_remaining_max_f` | REAL °F | 只使用决策时刻及之后的同版 hourly curve；不得复用已经过去的全天峰值 |
| 决策后预报再升温空间 | `forecast_reheat_after_now_f` | REAL °F | `max(0, forecast_remaining_max_f - forecast_temperature_at_decision_f)` |
| 剩余预报高出观测最高温 | `forecast_remaining_gap_to_running_native` | REAL °C/°F | future-only forecast max 减 running max；no-reheat/remaining-heat 使用此字段，不用全天 `forecast_gap_to_running_native` |
| 预报最高温是否落入本 bracket | `forecast_max_in_bracket` | INTEGER 0/1 | 按当前 record 的 bracket 边界判断 |
| 预报最高温高出本 bracket | `forecast_max_above_bracket_f` | REAL °F | 若未高出则 0；top bracket 可能为空 |
| 预报最高温低于本 bracket | `forecast_max_below_bracket_f` | REAL °F | 若未低于则 0 |
| 预报最高温高出已观测最高温 | `forecast_max_above_metar_max_f` | REAL °F | `forecast_max_f - metar_current_max_f`，METAR 缺失时为空 |
| 边际优势 | `edge` | REAL | |
| 绝对边际 | `abs_edge` | REAL | |
| 城市池 | `city_pool` | TEXT | t1_trading / t2_research |
| ICAO代码 | `icao` | TEXT | ZSPD / LFPG 等 |
| 市场condition | `condition_id` | TEXT | 0x... |
| 市场ID | `market_id` | TEXT | |
| 距结算小时数 | `hours_to_settle` | REAL | |

等高平台的 observation clock 必须区分：

- `running_max_obs_utc` / `minutes_since_running_max`：兼容字段，指最后一次等于 running max 的报文；重复等高会刷新。
- `first_running_max_obs_utc` / `minutes_since_first_running_max`：当前 running max 首次出现。
- `minutes_since_last_strict_new_high`：最后一次严格抬高 running max 后经过的时间；判断成熟 plateau 优先使用它。
- `same_running_max_obs_count`：当前 running max 在可见 PIT history 中出现的独立报文数。

`running_max_state_v2` / `intraday_state_v2` 使用 strict-high clock；旧
`running_max_state` / `intraday_state` 是兼容标签，仍使用 last-equal-high
clock。`solar_phase_v2` 使用真实太阳高度及未来两小时变化，不得用固定本地
hour bucket 冒充物理太阳阶段。湿度本身不得标成 convection evidence。

Observation cache 刷新失败并复用上一条事实时，`status` 必须为
`reused_after_fetch_error`，并按当前时刻重算 `age_min` 和 observation clocks；
不得保留 `status=ok` 或旧 age。任何已知 availability 晚于 feature
`as_of_ts_utc` 的 observation 都不得进入 frame。

天气转场使用连续 duration/change 字段：`clear_sky_regime_minutes`、
`precip_free_regime_minutes`、对应 `*_temp_change_f` 与 `*_left_censored`；
缺历史时保留 null/censored，不转换为“未下雨”或“刚放晴”的策略条件。

### 1.1.1 First-seen information event（target contract）

本节定义已落地并通过 archive-known raw replay 的上游契约。只有新 collector
保存的 `collector_exact` 可用于精确到达时延研究；历史 source-event
`changed_since_last`、TAF capture time 或 provider issue time 不得补造成
canonical exact first-seen。

统一上游血缘：

```text
raw capture
  -> weather_information_event
  -> weather_state_checkpoint + feature_frame_ref
  -> fact_signal_candidates
```

最小规范 ID/时间字段：

```text
information_event_id, content_key, payload_hash, revision_of_event_id,
source_event_ts_utc, issued_at_utc, detected_at_utc, first_seen_at_utc,
available_at_utc, ingested_at_utc, trigger_event_id, state_checkpoint_id,
as_of_ts_utc, candidate_grain_version
```

时钟不得混用。`source_event_ts_utc` 是天气发生/适用时间，
`issued_at_utc` 是 provider 声称的发布时间，`first_seen_at_utc` 是 collector
对同一 immutable event identity 的最早发现时间，`available_at_utc` 是下游可读
边界。PIT state 只能读取 `available_at_utc <= as_of_ts_utc` 的输入。

初始 PIT lineage 枚举：

```text
collector_exact
archive_known_available
late_backfill_first_seen_unknown
```

只有 `collector_exact` 可用于 first-seen latency 研究；后两类不得由 provider
时间戳升级。完整 grain、ID、source-specific 规则、candidate v1/v2 共存方式和
验收标准见
[WEATHER_FIRST_SEEN_INFORMATION_LINEAGE.md](WEATHER_FIRST_SEEN_INFORMATION_LINEAGE.md)。

### 1.2 Order / 订单

| 概念 | 规范字段名 | 类型 | 说明 |
|---|---|---|---|
| 订单ID | `order_id` | TEXT | venue 原始订单ID；paper 可以由 `execution_id` 确定性派生 |
| 执行ID | `execution_id` | TEXT (64位hex) | canonical order-attempt ID；保留 raw 值，缺失时按 §2.2 补全 |
| 计划ID | `plan_id` | TEXT (64位hex) | canonical decision ID；由 §2.2 的当前 helper 确定性生成 |
| 订单方向 | `order_side` | TEXT BUY_YES/BUY_NO/SELL_YES/SELL_NO | ~~BUY~~ 废弃；见 §3 |
| 成交价 | `entry_price` | REAL 0-1 | |
| 成交份额 | `shares` | REAL | |
| 成本USD | `cost_usd` | REAL | |
| 名义价值 | `notional` | REAL | |
| 限价 | `limit_price` | REAL 0-1 | |
| 执行档案 | `execution_profile` | TEXT | plan-owned；策略选择的稳定、版本化 taker/maker 组合档案，order 通过 `plan_id` 继承 |
| 执行策略 | `execution_policy` | TEXT | mid_price_core_v1 等 |
| token ID | `token_id` | TEXT | Polymarket CLOB token |
| 交易所 | `venue` | TEXT | polymarket_clob |
| 交易所响应 | `exchange_response` | JSON TEXT | 完整venue响应，包含error |
| 状态 | `status` | TEXT | filled/error/cancelled 等 |

### 1.3 Settlement / 结算

| 概念 | 规范字段名 | 类型 | 说明 |
|---|---|---|---|
| 结算结果 | `final_price` | REAL 0.0-1.0 | ~~`final_yes` INTEGER~~ 待迁移；保留精度 |
| 结算状态 | `settlement_status` | TEXT | settled/missing_event/missing_bracket |
| PnL | `pnl_usd` | REAL | |
| 胜 | `won` | BOOLEAN | |

### 1.4 Run / 实验

| 概念 | 规范字段名 | 类型 | 说明 |
|---|---|---|---|
| 运行ID | `run_id` | TEXT (UUID) | pm_agent在ingest时生成 |
| 配置ID | `config_id` | TEXT | SHA256(策略参数JSON) |
| 执行模式 | `execution_mode` | TEXT | snapshot_replay/paper/live |

### 1.5 Strategy / Signal / Policy 分层

`signal` 是机会事实，不属于某个下单模块：同一个 `signal_id` 可以被多个
`strategy_config` 消费。`strategy_config` 是策略身份，包含 signal filter、
sizing 和 execution policy。`plan` 是某个 `run/config` 对某个 `signal`
的决策，因此 A/B 执行策略对比应表现为:

```text
same signal_id
  -> config/run A -> plan(execution_profile=taker_now_v1)          -> order/fill
  -> config/run B -> plan(execution_profile=single_side_maker_v1)  -> order/fill
```

参数分层:

| 层级 | 例子 | 进入策略身份 |
|---|---|---|
| signal/model | `model_version`, `forecast_source`, `edge`, `city_pool` | 间接进入；signal 本身可共享 |
| filter/sizing | `entry_price_window`, `min_edge`, `max_order_notional`, `sizing_mode` | 是 |
| execution profile | `execution_profile`（策略选择 taker/maker） | 是 |
| execution policy | `execution_policy`, `min_quote_edge`, `max_quote_spread`, `max_mid_drift` | profile 展开后的执行参数 |
| order lifecycle | `order_lifecycle_policy`, `cancel_buffer_sec`, `data_update_source` | profile 展开后的执行参数 |

---

## 2. ID 生成算法（必须两侧一致）

### 2.1 signal_id

```
signal_id = SHA256(target_date + "|" + city + "|" + bracket + "|" + signal_side + "|" + model_version + "|" + snapshot_ts_utc)
输出: 64位lowercase hex
```

**N100 侧**: `weather_snapshot_signal_builder.py` 用此算法生成 signal_id 并写入 signals JSONL 和 paper CSV。  
**pm_agent 侧**: ingest 时直接使用 CSV/JSONL 里的 signal_id，不要重新计算。  
> 当前 pm_agent `ledger_csv.py` 用 32位hash自行生成——这是待修正的技术债（§6 迁移计划）。

### 2.2 plan_id / execution_id

当前权威实现是
[`src/strategies/weather_edge_v1/ids.py`](../src/strategies/weather_edge_v1/ids.py)，
并由 canonical migration 复用；不是 N100 单独生成的 ID 口径。

```text
plan_id = SHA256(run_id | signal_id | canonical_order_side | execution_policy
                 [| execution_profile | child_order_role])
execution_id = raw execution_id（若 runtime journal 已提供）
             否则 make_execution_id(run_id, canonical_plan_id, venue,
                                  attempt_index=stable_attempt_key)
```

`execution_profile` / `child_order_role` 都为空时，`plan_id` 保持 legacy
single-leg 算法；任一存在时二者进入 identity，避免一个 signal 的多个 child
折叠。`make_execution_id` 的正式参数名是 `attempt_index`；runtime migration
把按已知原始 order/attempt 字段选择的 `stable_attempt_key` 作为该参数传入，
缺失时才使用整行的稳定 hash。旧 raw `plan_id` 仅作为 source mapping 使用，
canonical `plan_id` 由上述算法生成。

### 2.3 run_id

pm_agent 在 ingest 时按文件来源确定性生成或直接使用 N100 source_run_id。paper/live run 的 run_id 与 N100 的 `source_run_id` 字段对齐。

---

## 3. 枚举值（两侧共用同一组值）

### signal_side (信号方向)
```
YES    — 押注该区间结算为 YES（价格→1）
NO     — 押注该区间结算为 NO（价格→0）
```

### order_side (订单方向)
```
BUY_YES   — 买入 YES token
BUY_NO    — 买入 NO token  (= 卖出 YES 仓位)
SELL_YES  — 卖出 YES token
SELL_NO   — 卖出 NO token
```

canonical schema 当前只持久化 `order_side`；后续 execution module 所说的
`venue_side` / `outcome_side` 是它的无损内部投影，尚不是额外的 canonical
字段：

| legacy raw `order_side` | signal outcome | canonical `order_side` | `venue_side` | `outcome_side` |
|---|---|---|---|---|
| `BUY` / 空 | YES | `BUY_YES` | BUY | YES |
| `BUY` / 空 | NO | `BUY_NO` | BUY | NO |
| `SELL` | YES | `SELL_YES` | SELL | YES |
| `SELL` | NO | `SELL_NO` | SELL | NO |

已是四值 canonical 形式的 raw 值原样通过。权威映射在
[`weather_dashboard/legacy_migration/live_cycle.py`](../weather_dashboard/legacy_migration/live_cycle.py)
和 [`weather_dashboard/legacy_migration/strategy_runtime_orders.py`](../weather_dashboard/legacy_migration/strategy_runtime_orders.py)；
不得把裸 `BUY` 直接写进 canonical plans/orders。

### city_pool
```
t1_trading    — T1 生产交易池
t2_research   — T2 研究池（paper only）
```

### execution_mode
```
snapshot_replay   — 历史 snapshot 回放（无真实下单）
paper             — paper 账本（策略决策但不实际交易）
live              — 真实 CLOB 下单
```

### forecast_source（完整枚举）
```
open_meteo_live_gfs      — Open-Meteo API + GFS 模型
open_meteo_live_ecmwf    — Open-Meteo API + ECMWF 模型
```
> N100 live JSONL 目前用 `profile` 字段存同值——待改名为 `forecast_source`（§6）。

---

## 4. 文件交换格式（N100 → pm_agent）

### 4.0 Shared data-feed snapshot fields

`weather_data_feed.snapshot_protocol` defines the minimum cross-repo snapshot
date fields:

```
city
target_date
market_local_date
city_local_date_at_snapshot
snapshot_ts_utc
```

`target_date` is the market settlement date. `city_local_date_at_snapshot` is
the city-local calendar date at collection time. They are allowed to differ,
especially for American cities while the machine/UTC/Asia date has already
rolled forward. Strategy code must not require `target_date ==
city_local_date_at_snapshot` as a generic market-selection rule.

### 4.1 t24_paper_ledger_trades.csv（主要分析源）

必含字段（pm_agent ingest 依赖）：

```
target_date, city, bracket, signal_side, model_version, model_p_yes,
market_price, edge, abs_edge, forecast_source, city_pool, icao,
condition_id, market_id, hours_to_settle, snapshot_ts_utc, snapshot_file,
order_id, shares, cost_usd, entry_price, settlement_status, final_price, pnl_usd
```

> 当前 CSV 还用旧字段名（`model`、`event_date`、`model_prob`、`market_yes_price`）——adapter 做临时兼容。待 N100 侧改名后删 adapter（§6）。

### 4.1.1 paper_snapshots/*.json forecast peak fields + curve archive

snapshot record 必须保留 forecast peak clock 字段。它们是
`forecast_source` 同一次 Open-Meteo hourly response 的派生值，不允许用实际观测最高温反推。

```
forecast_max_f, forecast_max_native,
forecast_peak_hour_local, forecast_peak_time_local,
forecast_peak_hour_utc, forecast_peak_time_utc,
forecast_hourly_count, forecast_values_hash, forecast_peak_source,
forecast_timezone, forecast_utc_offset_seconds,
forecast_peak_delta_hours_local,
forecast_max_in_bracket,
forecast_max_above_bracket_f, forecast_max_below_bracket_f,
forecast_max_above_metar_max_f
```

`forecast_values_hash` 是 forecast 元数据和曲线归档的连接键。full snapshot producer 同时写：

```
targeted_output/forecast_hourly_curves/YYYY-MM-DD/forecast_hourly_curves_*.jsonl
```

该 JSONL grain = `(city, target_date, snapshot_ts_utc, forecast_values_hash)`，字段包括
`forecast_source` / `forecast_model` / `forecast_peak_*` / `forecast_hourly_count` /
`forecast_timezone` / `hourly_curve`。`pm_agent` rebuild 时写入
`runtime/weather.db.fact_forecast_hourly_curves`。研究剩余加热积分、ceiling margin、
curve slope 等 PIT 特征时从该表按 `forecast_values_hash` 取曲线，不再事后访问第三方 API 补同日曲线。

`fact_signal_candidates` 的价格、entry、spread 仍保存**决策窗代表 snapshot**值；forecast peak/hash
字段则优先用决策窗记录，决策窗记录缺字段时用同一机会最接近目标 HTS 的 snapshot forecast 元数据补齐。
这只补同一 PIT snapshot universe 中已经存在的 forecast 派生字段，不用实际观测或未来 API 反推。

### 4.2 live_*_signals.jsonl

必含字段：

```
signal_id, target_date, city, bracket, signal_side, model_version, model_p_yes,
market_price, edge, abs_edge, forecast_source, city_pool, icao,
condition_id, market_id, hours_to_settle, snapshot_ts_utc, snapshot_file
```

> 当前用 `profile`（非 `forecast_source`）和 `model_probability_yes`（非 `model_p_yes`）——待修正。

### 4.3 live_*_orders.jsonl

必含字段：

```
signal_id, plan_id, execution_id, order_id, order_side,
entry_price, shares, cost_usd, limit_price, notional,
execution_policy, token_id, venue, exchange_response, status, created_at_utc
```

> 当前 `order_side` = "BUY"（非 "BUY_YES"/"BUY_NO"）——待修正。

### 4.4 theta_current_yes forward_telemetry.jsonl

current-YES no-reheat 分支的分钟级 would-order 证据层；它不是订单源，
不代表已成交，也不进入 live PnL。用于之后按真实前瞻样本评估 hit rate、
taker ROI、METAR 更新窗口和 fresh-book 滑点。

路径：

```text
runtime/weather_edge_v1/theta_current_yes_fade_confirmed_tiny_live_v1/forward_telemetry.jsonl
runtime/weather_edge_v1/theta_current_yes_peak_forming_micro_tiny_live_v1/forward_telemetry.jsonl
```

核心字段：

```text
record_type, telemetry_version, telemetry_run_id, strategy_instance,
decision_status, city, target_date, current_bracket, decision_local_time,
decision_timezone, snapshot_ts_utc, snapshot_age_min,
obs_source, obs_age_min, obs_cadence_min, minutes_to_next_obs,
obs_age_limit_relaxed, cadence_source,
last_obs_utc, running_max_obs_utc, minutes_since_running_max,
decline_c, gap_running_to_d1_low_c,
yes_current_ask, available_notional_at_ask, fresh_best_ask,
fresh_ask_size, taker_limit_price, edge_at_fresh_ask, edge_at_limit,
p_yes_win, p_yes_win_base_current_yes_model,
p_yes_win_fade_confirmed_specialist, fade_confirmed_specialist_delta,
probability_source, probability_branch, model_version,
forecast_peak_hour_local, forecast_peak_time_local,
forecast_peak_source, forecast_values_hash, forecast_peak_delta_hours_local,
source_profile_class, source_profile_primary_source,
source_profile_station_or_feed, source_profile_live_eligible
```

For `theta_current_yes_peak_forming_micro_tiny_live_v1`,
`minutes_since_running_max` is a required live freshness input. Missing or
non-finite values must reject the candidate with
`snapshot_rule_peak_forming_missing_running_max_age`; they are not equivalent
to passing the veto.

---

## 5. 什么不应该是契约的一部分

| 内容 | 谁管 | 说明 |
|---|---|---|
| T1城市列表 | N100 `FULL_CITY_CONFIGS` / `city_pool` 字段 | pm_agent 不维护城市白名单，读 `city_pool` 字段 |
| 策略参数 | N100 config + pm_agent `strategy_config` 表 | config_id 是双方共用的锚点 |
| 结算逻辑 | N100 `settle_t24_paper.py` | pm_agent 只消费结算结果，不重算 |
| DB rebuild时机 | pm_agent 决定 | N100 不感知 pm_agent 的DB |

---

## 6. 待修正的技术债（按优先级）

### 立刻可改（不影响生产，只改字段名）

| # | 位置 | 当前 | 改为 | 影响 |
|---|---|---|---|---|
| A | N100 CSV 生成 | `model` | `model_version` | CSV + adapter |
| B | N100 CSV 生成 | `event_date` | `target_date` | CSV + adapter |
| C | N100 CSV 生成 | `model_prob` | `model_p_yes` | CSV + adapter |
| D | N100 CSV 生成 | `market_yes_price` | `market_price` | CSV + adapter |
| E | N100 live signal JSONL | `profile` | `forecast_source` | live ingest |
| F | N100 live signal JSONL | `model_probability_yes` | `model_p_yes` | live ingest |
| G | N100 live order JSONL | `order_side`=`BUY` | `order_side`=`BUY_YES`/`BUY_NO` | live ingest |

当 N100 改完后，pm_agent 删除 `real_ledger_adapter.py` 里对应的字段别名处理，adapter 变成纯透传。

### 需要设计的（影响ID格式）

| # | 内容 | 当前状态 | 目标 |
|---|---|---|---|
| H | signal_id 统一算法 | pm_agent 32位 vs N100 64位 | N100 生成并写入 CSV；pm_agent 直接用 |
| I | CSV 含 `signal_id` 字段 | 无 | N100 settle 脚本在 CSV 里加 signal_id 列 |
| J | `final_yes` INTEGER → `final_price` REAL | DB 当前存0/1 | 保留精度；向后兼容 |

---

## 7. 变更流程

1. 修改本文档（明确说明字段名/枚举值变化）。
2. 在 N100 改字段名（weather-predict 代码）。
3. 在 pm_agent adapter 里保留旧字段名的兼容读取（`_get("new_name", "old_name")`）。
4. 验证 rebuild 后数据完整。
5. 一个迭代周期后删除 adapter 里的旧字段名兼容。
6. 更新本文档标注「已完成」。

任何 agent 改字段前检查：N100 和 pm_agent 是否都已改？adapter 是否还在做兼容翻译？
