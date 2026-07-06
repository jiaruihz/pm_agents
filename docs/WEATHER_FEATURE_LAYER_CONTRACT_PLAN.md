# Weather Feature Layer Contract Plan

Status: design-draft
Updated: 2026-07-06
Source of truth: no; migration contract proposal
Superseded by / Used by: WEATHER_ARCHITECTURE_SPINE.md; WEATHER_FEATURE_LAYERING_PLAN.md; WEATHER_TEMPERATURE_CONTEXT_FEATURE_LAYER.md; WEATHER_DOCS_INDEX.md

Revision note: 2026-07-06 review 修订：依赖方向 / factory 归属 / 落库契约 / 单位契约 / multiplier 归属 / 特征补充。

Implementation progress: 2026-07-06 Phase 1-3 initial primitives landed:
`weather_feature_layer/` skeleton, state re-export, market/regime/bias helpers,
and parity tests. Phase 4A minimal `build_weather_state_frame()` builder landed
for snapshot + observation-cache state frames with row-level metadata and PIT
provenance; feature store and consumer decision-input rewires are not landed.

## One-Line Decision

新增独立包 `weather_feature_layer/`。它不是新的策略，也不是新的 data-feed。它是 `weather_data_feed` 标准化数据之上的 point-in-time 机制特征层，统一产出“天气状态、forecast runway、城市/来源 bias、market geometry、regime state”这几类粗粒度特征，供 HeadA、HeadB、tmax、regime-routed NO、theta/current YES、station-basis、Range RV 等策略消费。

原则：共享的是机制状态，不共享交易结论。`edge`、selector、sizing、maker/taker lifecycle、live gate、order/fill/PnL 不能进这个包。

## Why A Separate Package

当前代码里已经有共享特征，但边界不干净：

| 当前位置 | 当前问题 | 迁移判断 |
|---|---|---|
| `weather_data_feed/weather_context.py` | 已包含温度、云、湿度、风、peak clock 机制特征；短期直接搬物理文件会让 vendored `weather_data_feed` 依赖新 L1 包，造成 L0 -> L1 依赖倒置 | Phase 1 实现物理不动；`weather_feature_layer/state.py` import 并 re-export 它作为新 canonical 入口。策略消费者逐步改 import 到 feature layer；data-feed 侧只加注释“策略代码不要直接 import 本模块”。物理搬迁等所有策略消费者切换完成后另开小 phase |
| `src/strategies/weather_edge_v1/tools/regime_routed_no_stable.py` | 已自包含，但混有通用 bracket/regime helper 和 regime-routed NO 私有 route policy | 通用 helper 上移，route/cap/hour policy 留在策略 |
| `src/strategies/weather_edge_v1/tools/low_price_yes_tail_telemetry.py` | as-of bias、bracket distance、local bucket 与 HeadA pcal/source-aware telemetry 混在一起 | 通用 bias/geometry 上移，HeadA selector/telemetry 模型留策略 |
| `scripts/ops/tmax_distribution_edge_live_candidate_v1.py` | 仍直接 import P0-P4 research 模块；P0/P3 有通用 market distribution 和 boundary 特征，P1/P2/P4 是模型/标签/回放链 | 先抽 market geometry 和 feature specs，概率模型继续策略私有 |
| `docs/analysis/**/generated/*feature*` | 同一 CSV 常混合 PIT features、future labels、payoff、ROI、diagnostic score | 迁移时强制拆 `features`、`labels`、`model_outputs`、`execution_results` |
| METAR/source-events/theta 观测链 | parser 可以共享，但 freshness/cadence/latency 分支有策略语义差异 | 观测解析和时间戳 contract 留 data-feed；是否触发交易留策略 |

## Layer Contract

目标分层：

```text
L0 weather_data_feed
  city calendar, source profile, observations, forecasts, snapshots, source_events

L1 weather_feature_layer
  point-in-time mechanism features
  no order intent, no strategy selector, no PnL, no future label in live frames

L2 probability / expression models
  HeadA pcal, tmax distribution fusion, theta current YES model, station-basis model

L3 strategy policy
  selector, edge threshold, expression choice, sizing, city pool, live/shadow mode

L4 execution
  maker/taker/FOK lifecycle, order, fill, position, settlement, PnL
```

编号说明：本计划的 L0-L4 是 feature-layer 迁移视角，不等同于
`WEATHER_ARCHITECTURE_SPINE.md` 的 [0]-[6] 或旧 L0-L2 编号。大致映射是：
本计划 L0 = SPINE 数据/source 协议层；本计划 L1 = SPINE 中 factory/context feature
事实层的目标抽象；本计划 L2-L4 分别对应 probability/expression、strategy policy、
execution/order/fill 血缘。Phase 7 必须统一 SPINE、LAYERING_PLAN 和本计划的层编号。

`weather_feature_layer` 的输入 grain 和输出 grain：

| Grain | 用途 | 例子 |
|---|---|---|
| `city_date_snapshot` | 一个城市、一个 target_date、一个决策时间点的天气状态 | theta/current YES、regime-routed NO、HeadB |
| `city_date_snapshot_bracket` | 同一状态下展开到 bracket/expression 的市场几何 | tmax、Range RV、HeadA sibling expression |
| `city_source_asof` | 某城市/forecast source 在 as-of 时刻可用的历史 bias 和 source reliability | HeadA、regime-routed、tmax ablation |
| `source_event_state` | source event 的观测时间、检测时间、age、lag，用于 latency telemetry | metar_cross shadow、theta telemetry；不是交易触发器 |

## Feature Families

不要把特征拆成一堆单点 flag。初版只保留六个粗粒度机制家族。

设计原则：连续量是一等接口，枚举 state 是其版本化派生视图。策略想换切法应基于连续字段自建 selector，不 fork 共享层。

### 1. Thermal Path

回答：当前温度路径还在上行、平台、回落，还是已经过峰。

通用字段：

- `current_temp_native`
- `running_max_native`
- `decline_from_running_max_native`
- `minutes_since_running_max`
- `temp_trend_1h_native`
- `temp_trend_3h_native`
- `obs_age_minutes`
- `expected_report_cadence`
- `station_gap_state`
- `thermal_path_state`
- `warming_state`
- `remaining_heat_native`

可共享模式：

- `fresh_runway`
- `sustained_warming`
- `one_hour_warm_without_3h`
- `plateau_near_high`
- `pullback_from_high`
- `late_reheat_after_dip`
- `post_peak_fade`

不进初版的细碎项：单独的 `trend3h_positive`、`trend1h_positive` 作为策略 gate。它们可以作为 `thermal_path_state` 的组成部分，但不作为第一等共享接口。

观测新鲜度是 Thermal Path 的一等机制字段：50 分钟前 METAR 撑起的
`running_max_native` 和 3 分钟前的不是同一个天气状态。它不同于
`source_event_state` 的 latency telemetry；这里表达的是天气真值可信度，
不是“我们比市场早看到几分钟”。

### 2. Forecast Runway

回答：forecast 最高温相对当前最高温还有多少空间，峰值时间是否已过，模型间是否一致。

通用字段：

- `forecast_source`
- `forecast_max_native`
- `forecast_gap_to_running_native`
- `forecast_peak_hour_local`
- `forecast_peak_delta_hours_local`
- `forecast_peak_hour_spread`
- `model_disagreement_native`
- `hours_to_sunset`
- `hours_past_solar_noon`
- `solar_elevation_state`
- `day_length_minutes`
- `season_state`
- `forecast_clock_state`
- `runway_state`

可共享模式：

- `peak_ahead_2h_plus`
- `peak_ahead_0_to_2h`
- `peak_recently_passed`
- `peak_stale_passed`
- `forecast_bust_vs_running`
- `forecast_room_one_bracket`
- `forecast_room_multi_bracket`
- `solar_runway_available`
- `late_day_solar_exhaustion`

边界：forecast source selection 仍由 `weather_data_feed` 和 `source_policy` 定义。特征层只消费已显式标注的 source，不静默替换 GFS/ECMWF。

太阳几何 / 日历钟归入 Forecast Runway 家族。它是纯物理、PIT 天然安全、
不依赖 forecast source 的第二意见，特别用于 forecast bust 或 source mismatch 时解释剩余升温空间。

### 3. Weather Suppression / Acceleration

回答：云、湿度、风、雨是否支持继续升温，还是压制升温。

通用字段：

- `sky_cover_code`
- `sky_state`
- `relative_humidity_pct`
- `dewpoint_depression_native`
- `wind_speed_kt`
- `wind_direction_deg`
- `precip_state` if available
- `cloud_warming_interaction`
- `moisture_cloud_interaction`
- `wind_thermal_interaction`
- `suppression_state`

可共享模式：

- `clear_solar_warming`
- `cloud_limited_flat_or_cooling`
- `humid_cloud_suppression`
- `dry_heat_inertia`
- `onshore_marine_cooling_risk`
- `offshore_or_parallel_warming_risk`
- `wind_mixing_risk`

边界：风向缺失要显式输出 `direction_unknown`，不能用城市常识补一个假风向。

### 4. Climate / Source Bias

回答：该城市、source、station 历史上是否系统性偏热/偏冷，当前 forecast/source 是否可靠。

通用字段：

- `city_family`
- `forecast_source`
- `station_basis_state`
- `bias_n`
- `bias_mean_native`
- `bias_p50_native`
- `bias_p90_native`
- `hot_tail_pct`
- `cold_tail_pct`
- `bias_mae_native`
- `source_fit_state`
- `source_reliability_state`

可共享模式：

- `hot_underforecast_bias`
- `cold_overforecast_bias`
- `source_hot_clean`
- `source_noisy`
- `station_basis_aligned`
- `station_basis_mismatch`

边界：城市池、城市白名单、`city_source_bias_multiplier`、raw city filter 不进 feature layer。特征层只输出 bias state，策略自己决定怎么用。

### 5. Market Geometry

回答：当前 market ladder 如何把天气状态映射成 exact bracket 表达，以及盘口是否可作为概率/成本输入。

通用字段：

- `current_bracket`
- `current_bracket_low_native`
- `current_bracket_high_native`
- `current_yes_ask`
- `current_yes_bid`
- `current_no_ask`
- `current_no_bid`
- `d1_yes_ask`
- `d1_yes_bid`
- `d1_no_ask`
- `d1_no_bid`
- `d2_yes_ask`
- `d2_yes_bid`
- `d2_no_ask`
- `d2_no_bid`
- `tail_yes_ask`
- `tail_yes_bid`
- `bracket_distance_native`
- `bracket_distance_bucket`
- `market_local_distribution`
- `book_spread`
- `book_depth_ask_5c`
- `book_depth_bid_5c`
- `book_state`

可共享模式：

- `at_current_bracket`
- `one_bracket_above`
- `two_brackets_above`
- `hot_tail`
- `thin_or_missing_hot`
- `tight_liquid_book`
- `stale_or_dust_book`

边界：`P(win) - ask`、edge threshold、which expression to buy、same-market close/reverse 都不进 feature layer。市场几何是输入，不是策略选择器。

bid 侧字段必须与 ask 侧对称进入 v1。MTM、TP/stop exit、underround/Range RV
都依赖 bid；只记录 ask 会把 entry 策略偏见写进共享层。

### 6. Regime State

回答：把上面几类机制压成少数可解释状态，用于分层、ablation、telemetry 和概率模型输入。

通用字段：

- `day_regime`
- `intraday_state`
- `running_max_state`
- `solar_window`
- `moisture_cloud_regime`
- `wind_regime`
- `thermal_forecast_regime`
- `composite_regime`

可共享模式：

- `day_open_runway`
- `late_morning_runway`
- `afternoon_peak_forming`
- `post_peak_exhaustion`
- `false_fade_reheat_conflict`
- `rich_current_collapse_candidate`

边界：regime label 可以共享；`route_name`、`ASK_CAPS`、`DECISION_HOURS`、`soft_weight`、`selected_expression` 是策略私有。

## What Goes In

这些应该进入 `weather_feature_layer/`：

| Category | Examples | Current sources |
|---|---|---|
| temperature path state | running max, trend, minutes since max, decline, remaining heat | `reheat_feature_factory_v1`, `intraday_weather_regime_atlas_v1`, theta runners |
| forecast runway | forecast max gap, forecast peak delta, model disagreement | atlas, reheat factory, tmax P3, theta reports |
| solar calendar state | hours to sunset, hours past solar noon, solar elevation/day length state | temperature context docs, future builder |
| sky/moisture/wind context | cloud/warming, moisture/cloud, wind/thermal interactions | `weather_data_feed/weather_context.py`, atlas |
| city/source bias | as-of forecast error, hot/cold tail rate, station/source reliability | low-price telemetry, regime-routed live, station-basis research |
| market geometry | bracket parser, current/d1/d2/tail ladder, bid/ask book state, bracket distance | tmax P0/P3, HeadA overlays, Range RV research |
| regime labels | day/intraday/running max/moisture/wind/composite regime | `regime_routed_no_stable.py`, atlas |
| source-event timing features | source report time, detect time, fetch age, lag buckets | observation parity harness, source-events reports |

## What Stays Strategy-Specific

这些不应该上移，最多只消费 feature layer：

| Strategy family | Private logic |
|---|---|
| HeadA low-price YES | cheap YES denominator, `dist>0` selector, pcal score, score-tier sizing, maker-first entry, recover stake, TP/stop overlays, source-aware v3 tag as selector |
| HeadB METAR reversal | `rich_current_collapse_d1_yes` trigger thresholds, d1 YES expression choice, taker/hold assumption, false-fade selector |
| tmax distribution | P1 fusion model, P2 EV selector, P5/P6 walk-forward execution replay, clean_edge configs, selected expression |
| regime-routed NO | route table, `ASK_CAPS`, `DECISION_HOURS`, route-specific soft weights, city/source multipliers, tiny-live order policy |
| theta/current YES | v9/v11/v12 model heads, peak-forming hazard thresholds, freshness gate, pre-update blackout, taker cushion |
| metar_cross / latency arb | FOK fast path, crossed market trigger, source race ordering, latency-specific fallback, live notional gates |
| station-basis | live-prep gate, maker/taker execution design, official bucket entry rule, station-specific approval state |
| Range RV / market-structure | portfolio construction, underround/overround execution, basket constraints, arbitrage thresholds |

## What Must Not Go In

Hard exclusions:

- Future labels: `final_max`, `final_winning_bracket`, `future_break_any`, `payoff`, `roi`, `pnl`, `settled_win` in live feature frames.
- Execution facts: order id, fill id, maker/taker lifecycle, position, open cost, realized PnL, cancel/repost state.
- Strategy policy: city allowlists, sizing, notional caps, edge thresholds, selected expression, route decisions.
- Hidden fallback: silently replacing missing ECMWF with GFS, silently filling missing observation from future archive, or defaulting unknown station basis to clean.
- Review-only diagnostics as live features: top-trade-removed, bootstrap CI, post-hoc score buckets, future max bid, fill-after-the-fact labels.
- Direct CLOB behavior: private key paths, CLOB client creation, FOK latency path, kill switches.

Training/evaluation builders may produce labels, but those labels must live under a separate namespace such as `weather_feature_layer.labels` and must never be returned by default live builders.
CI 必须断言 live/shadow runner 的 import 闭包不包含 `weather_feature_layer.labels`，不能只靠人工约定。

## Proposed Package Shape

Initial package should be small:

```text
weather_feature_layer/
  __init__.py
  contracts.py          # schema versions, grains, enums, column groups
  frame.py              # FeatureFrame wrapper and validation helpers
  state.py              # Thermal Path + Forecast Runway + Weather Suppression
  regimes.py            # coarse regime labels
  bias.py               # city/source/station as-of bias states
  market.py             # bracket parser, expression ladder, market geometry
  builders.py           # PIT builders from data-feed snapshots/cache/fact rows
  labels.py             # offline labels only; never imported by live runners by default
  parity/               # shared fixtures and old-vs-new parity harnesses
```

Avoid creating one file per feature. The package should be organized by mechanism family, not by column name.

Phase 1 dependency rule: `weather_feature_layer` may import `weather_data_feed`;
`weather_data_feed` must not import `weather_feature_layer`. During the first
phase `weather_feature_layer/state.py` should re-export existing
`weather_data_feed.weather_context` helpers instead of moving them. Vendoring
must be explicit: `weather_data_feed` remains the N100 `weather-predict` vendor
package; `weather_feature_layer` is a separate package and is vendored/deployed
only where strategy/research consumers need L1 features.

Public API sketch:

```python
from weather_feature_layer import (
    build_weather_state_frame,
    add_bias_features,
    add_market_geometry_features,
    add_regime_features,
)

state = build_weather_state_frame(snapshot_rows, observation_rows, as_of_ts_utc=ts)
state = add_regime_features(state)
brackets = add_market_geometry_features(state, orderbook_rows)
features = add_bias_features(brackets, bias_reference, as_of_ts_utc=ts)
```

Versioning:

```text
weather_state_v1
weather_regime_v1
weather_bias_v1
market_geometry_v1
feature_frame_v1
```

Every feature frame must carry:

- `feature_schema_version`
- `feature_grain`
- `as_of_ts_utc`
- `source_profile_id`
- `feature_version_manifest`, for example `{weather_state: v1, weather_bias: v2, market_geometry: v1}`
- `pit_provenance`
- `builder_version` only as a diagnostic implementation id, not as the schema pin
- `input_snapshot_id` or source file hash when available

Downstream probability models must pin the exact per-family version manifest, not
just a single aggregate builder version.

## Feature Storage And Main-Lineage Join Contract

决策时特征快照优先。live/shadow runner 在决策时必须把 feature frame 原样落盘；
训练、复盘、PnL 归因优先消费决策时记录。事后重算的 feature frame 只能作为补充
或缺口修复，不能覆盖 live/shadow 当时所见。这是 forecast peak clock backfill
PIT 污染事故的结构性防线。

`fact_signal_candidates` join 契约：

- 机会行通过 `feature_frame_ref` 引用 feature frame。
- `feature_frame_ref` 至少包含 `feature_schema_version`、`feature_grain`、
  `feature_version_manifest`、`as_of_ts_utc`、`input_snapshot_id` 或等价可复现键。
- feature frame 本身按 grain 存储；初始落点暂定 `runtime/weather_feature_store/`，
  与 `WEATHER_FEATURE_LAYERING_PLAN.md` Phase D-8 对齐。
- 策略私有 telemetry 可以额外记录，但不能替代 opportunity -> feature frame 的主血缘引用。

`pit_provenance` 是一等字段：

| value | meaning |
|---|---|
| `live_capture` | 决策时 live/shadow runner 实际看见并落盘的 feature frame |
| `archive_reconstruction` | 事后从 mirror、patch、官方历史或 archive 重新构造的 frame |

同一观测在 live 所见和事后 mirror patch 后可能不同。用清理后的历史重建训练帧时，
必须标注 `archive_reconstruction`，不得声称等价于 live capture。

Bias 层结算延迟规则：

- `city_source_asof` 的 `bias_mean`、`bias_p50`、`bias_p90`、`hot_tail_pct`、
  `cold_tail_pct` 等依赖官方最终最高温。
- as-of 时刻只允许使用当时已结算可得的 target_date。可得性以 settlement source
  已落入 canonical settlement/final-max 记录、且记录时间不晚于 `as_of_ts_utc` 为准。
- `bias_reference` 输入必须版本化且可复现：记录构建时间窗、settlement source、
  source policy、生成时间、输入 hash 或 snapshot id。
- Phase 3 验收必须检查 bias reference 未使用 as-of 之后才结算的日期。

## Unit And Threshold Space Contract

`contracts.py` 必须逐字段钉死单位，不能只靠 `_native` 后缀猜。每个分类 state
必须声明阈值定义在哪个单位空间：

| Field/state family | Canonical threshold space | Rule |
|---|---|---|
| temperature trend labels inherited from `weather_context.py` | F-space | F 阈值先在华氏空间判断；C 城市必须先换算或使用等价 F 阈值 |
| `dewpoint_depression` humidity/moisture labels | F-space where inherited | 不得把 `dew_dep >= 25F` 直接套到 C native |
| bracket geometry / exact-market step | native market unit | 使用 `unit_step()` 规则：F=1.0, C=0.5 |
| forecast gap to bracket | native market unit plus explicit converted columns when needed | schema 同时说明 native 与 F-space 派生字段 |

Phase 1/2 parity fixture 必须同时覆盖 F 城市和 C 城市；验收标准包含 “C 城市 label 无漂移”。只在 F 城市通过 parity 不算完成。

## Temperature State Factory Ownership

`build_weather_state_frame()` 是温度路径计算的唯一未来实现。它负责
`running_max_native`、`temp_trend_1h/3h`、`minutes_since_running_max`、
`remaining_heat_native` 等 Thermal Path 核心字段。

`reheat_feature_factory_v1` 在后续 Phase D/factory 迁移时改为调用该 builder
做物化，退化为 materialization wrapper。过渡期现有 factory 实现只作为 parity
基准和历史 fixture source。禁止出现第三份独立温度路径计算。

## Current Asset Review

Moisture 双口径决策：`weather_data_feed.weather_context.moisture_cloud_interaction`
与 `regime_routed_no_stable.label_moisture_cloud` 是近似但不同的枚举体系，
不能在纯搬家中合并。仿照 CITY_FAMILY 的双 taxonomy 处理，先版本化共存：

| Versioned view | Current source | Example labels | Decision |
|---|---|---|---|
| `moisture_cloud_context_v1` | `weather_context.moisture_cloud_interaction` | `humid_cloud_suppression`, `humid_convective_risk`, `dry_heat_inertia` | 作为通用 context feature 保留 |
| `moisture_cloud_regime_v1` | `regime_routed_no_stable.label_moisture_cloud` | `humid_overcast_suppression` and route-oriented labels | 作为 regime-routed parity view 保留 |

合并两个 moisture 枚举属于行为变更，明确排除在本计划的纯搬家迁移之外。

| Asset | Shared pieces | Private pieces | Plan |
|---|---|---|---|
| `weather_data_feed/weather_context.py` | weather context labels, peak clock, wind/moisture/cloud interactions | `temperature_context_multiplier()` 是 regime-routed sizing overlay，按 `route_leg` 输出 multiplier，属于策略私有 | Phase 1 物理实现留在 data-feed，feature layer 从 data-feed import/re-export；`temperature_context_multiplier()` 不进 feature layer，Phase 2 随 regime-routed 拆分迁到策略空间 |
| `weather_data_feed/observation_clock.py` | observation as-of, freshness metadata, cadence facts | strategy-specific freshness veto | Keep in data-feed; feature layer consumes its timestamps and age fields |
| `weather_data_feed/source_basis.py` | source/station basis facts | live readiness gate | Keep canonical source basis in data-feed; derived bias states in `bias.py` |
| `regime_routed_no_stable.py` | bracket parser, regime labels, route candidate raw features | ASK caps, route hours, route selection, soft weights | Split; strategy imports feature helpers, keeps policy local |
| `low_price_yes_tail_telemetry.py` | bracket bounds/distance, as-of bias fields, local time bucket | pcal score, source-aware selector tags, HeadA telemetry row contract | Split; bias/geometry up, HeadA tags remain |
| `tmax_distribution P0/P3` | market-local distribution, boundary features, numeric/context feature specs | model fitting, blend prediction, EV selector | Extract market geometry; keep P1/P2/P4/P5/P6 strategy/research |
| `reheat_feature_factory_v1` | temperature path, forecast peak, meteo state | future labels, payoff, current YES outcome | Use as parity baseline during transition; after Phase D/factory migration it calls `build_weather_state_frame()` as materialization wrapper |
| `intraday_weather_regime_atlas_v1` | regime taxonomy and state rows | label/outcome/payoff columns | Use as parity reference; move taxonomy to `regimes.py` |
| theta/current YES live | live observation path, running max state, obs age telemetry | current YES model, freshness gate, pre-update blackout, taker cushion | Only migrate parser/path features after replay parity |
| METAR reversal / metar_cross | source event timestamps, parsed observation state, bracket state | crossed-market trigger, FOK execution, latency assumptions | Parser/timestamp only; fast path remains frozen |
| station-basis | station/source mismatch state | live-prep gate and execution policy | Bias state can be shared; approval gate stays strategy |
| Range RV / market structure | ladder geometry, under/over book state | portfolio construction and execution | Reuse `market.py`; no strategy migration in v1 |

## Migration Plan

### Phase 0: Contract Only

Deliverables:

- Add and review this contract plan.
- Do not create package behavior yet.
- Do not change live/shadow runners.

Acceptance:

- Review agrees on inclusions/exclusions.
- `WEATHER_DOCS_INDEX.md` points to this plan as `design-draft`.

### Phase 1: Package Skeleton And Pure Re-exports

Deliverables:

- Create `weather_feature_layer/` skeleton.
- Implement `weather_feature_layer/state.py` as import/re-export wrapper over existing `weather_data_feed.weather_context`.
- Do not move or copy `weather_data_feed/weather_context.py` implementation in this phase.
- Add a comment in `weather_data_feed/weather_context.py`: strategy code should import via `weather_feature_layer.state` after the cutover.
- Document whether and where `weather_feature_layer` is vendored; keep dependency direction L1 -> L0 only.
- Add parity tests for context labels using existing fixture rows.

Acceptance:

- Existing `weather_data_feed.weather_context` tests and importers pass unchanged.
- Static import check confirms `weather_data_feed` does not import `weather_feature_layer`.
- F-city and C-city fixtures both match old labels; C-city label drift is a blocker.
- No runner output changes.
- No live process restart required.

### Phase 2: Regime Labels And Bracket Primitives

Implementation progress: 2026-07-06 `temperature_context_multiplier()` was
removed from `weather_data_feed.weather_context` and moved to
`src/strategies/weather_edge_v1/tools/regime_routed_temperature_context.py`.
It remains a regime-routed sizing overlay, not a shared feature-layer primitive.

Deliverables:

- Extract `parse_bracket`, `bracket_contains`, bracket low/high/mid helpers to `market.py`.
- Extract generic day/intraday/running max/moisture/wind regime labels to `regimes.py`.
- Update `regime_routed_no_stable.py` to import generic helpers while retaining policy constants locally.
- Keep `moisture_cloud_context_v1` and `moisture_cloud_regime_v1` as separate versioned views.
- Move or isolate `temperature_context_multiplier()` into regime-routed strategy space; it is not a shared feature.

Acceptance:

- `regime_routed_no_stable` old-vs-new parity on historical shadow journal rows is byte-identical for route inputs and selected rows.
- `tmax_distribution_edge_shadow_v1` state-feature columns match before/after where it consumes regime labels.
- F-city and C-city bracket/regime fixtures both pass.

### Phase 3: Bias Layer

Implementation progress: 2026-07-06 initial `bias_reference_v1` landed in
`weather_feature_layer.bias`: city/source bias lookup and daily as-of error
index can now be loaded with build window, settlement source, source policy,
generated timestamp, input hash, row counts, and snapshot id. The old lookup
API remains compatible; regime-routed tiny-live records the reference metadata
in its summary without changing candidate behavior.

Deliverables:

- Move as-of bias lookup and classification primitives into `bias.py`.
- Define `source_fit_state` and `source_reliability_state` as mechanism labels.
- Define a versioned `bias_reference` contract with build window, settlement source, source policy, generated timestamp, and input hash/snapshot id.
- Keep HeadA pcal/source-aware tags and regime-routed multipliers private.

Acceptance:

- HeadA `low_price_yes_integrated_tail_shadow_v2` telemetry diff shows identical non-migrated strategy tags and identical shared bias columns.
- Regime-routed live/shadow route decisions are identical on replay rows.
- Bias parity verifies no as-of frame uses target_dates whose official final max was unavailable at `as_of_ts_utc`.

### Phase 4: Weather State Builder

Implementation progress: 2026-07-06 Phase 4A landed the minimal
`weather_feature_layer.builders.build_weather_state_frame()` path. It consumes
data-feed snapshot rows plus observation cache, emits `city_date_snapshot`
frames with `source_report_ts_utc` and `detect_ts_utc` / `fetched_at_utc`
separated, and attaches required frame metadata including `pit_provenance` on
every row. It does not write a feature store or change any runner decision path.
Phase 4B offline parity against the tmax live-candidate state builder matched
828/851 shared-field comparisons on the 2026-07-01 N100 recovery sample; the
only remaining diff is `decision_hour_local` precision (feature layer decimal
hour vs legacy tmax integer hour bucket). See
`docs/analysis/2026-07/2026-07-06-weather-state-frame-parity-v1.md`.
Regime-routed live feature replay was rerun on 2026-07-06; historical parity
gate coverage was 275/279 rows, but the NYC as-of replay returned
`no_asof_records` from the current live fetch path, so that acceptance item is
recorded as partially checked, not fully closed.
Phase 5A adds shared exact-bracket settlement geometry and symmetric bid/ask
book-state fields with tmax P0/P3 fixture parity. It does not move tmax
market-local probability distribution, anchor distributions, selectors, or any
live consumer decision input.

Deliverables:

- Implement `build_weather_state_frame()` for `city_date_snapshot` from data-feed snapshots and observation cache.
- Make it the single future implementation for Thermal Path; `reheat_feature_factory_v1` becomes a materialization wrapper in its later factory migration.
- Include timestamp contract: `source_report_ts_utc` for observation time and `detect_ts_utc` / `fetched_at_utc` for detection/fetch time.
- Keep `source_events` and `observations` semantics separate.
- Write decision-time feature frames to the agreed feature store in live/shadow dry-run mode before any strategy consumes them for decisions.

Acceptance:

- Theta/current YES observation-path replay matches current runner fields on the approved parity window.
- METAR/LDM parser parity remains at the prior accepted level.
- No theta freshness gate or metar_cross trigger logic is changed.
- `feature_frame_ref` can be joined from a sample opportunity row back to a stored feature frame.

### Phase 5: Market Geometry Builder

Deliverables:

- [done 2026-07-06 Phase 5A] Implement `add_market_geometry_features()` for exact-bracket settlement geometry on `city_date_snapshot_bracket`-style rows.
- [done 2026-07-06 Phase 5A] Extract tmax P0 interval/hour/relative-position geometry and P3 boundary features that are not model-specific.
- [already present] Extract HeadA bracket-distance helpers into shared geometry.
- [done 2026-07-06 Phase 5A] Include bid-side sibling fields alongside ask-side fields.
- [deferred] Keep tmax market-local probability distribution, soft-anchor distributions, blend model, EV selector, and runner rewiring strategy-private until separate parity/replay approval.

Acceptance:

- tmax P0/P3 feature columns match old scripts for the same fixture rows. Done for interval/hour/position/boundary geometry in `tests/pmm_tests/test_weather_feature_layer_contract.py`; market probability columns remain private and are not claimed as shared parity.
- HeadA mechanism overlay candidate rows match old bracket-distance and book-state fields.
- Bid/ask ladder fields round-trip on Range RV and TP/stop fixture rows.
- No `selected_expression` migration yet.

### Phase 6: Consumer Rewire By Risk

Order:

1. Research scripts and generated reports.
2. Zero-notional shadows.
3. Tiny-live telemetry-only fields.
4. Tiny-live decision inputs only after replay parity.
5. No metar_cross FOK fast path migration in this plan.

Acceptance per consumer:

- Old implementation vs new implementation run on the same historical input.
- Diff report is checked into `docs/analysis/2026-07/` or later month.
- Any semantic difference is classified as `bug fix`, `intentional strategy-private difference`, or `do not migrate`.
- Live behavior change requires separate approval and git-first deploy.

### Phase 7: Docs And Ownership Cleanup

Deliverables:

- Update `WEATHER_ARCHITECTURE_SPINE.md` to show actual implemented package status.
- Downgrade `WEATHER_TEMPERATURE_CONTEXT_FEATURE_LAYER.md` to a temperature-family sub-reference once v1 is landed.
- Update `WEATHER_FEATURE_LAYERING_PLAN.md` to point to this contract for feature-layer scope.
- Update strategy registry entries to say which strategies consume shared feature families.
- Align layer numbering and naming across SPINE, LAYERING_PLAN, and this plan.
- If the `weather_context.py` implementation is physically moved, do it only after all strategy consumers import through `weather_feature_layer`; treat it as a separate post-consumer mini-phase with vendoring verification.

Acceptance:

- Docs distinguish current implementation from target state.
- Deprecated import wrappers have removal dates.
- No dormant assets are deleted.

## Parity Harness Requirements

Every migration PR/commit needs at least one of these:

| Harness | Required for |
|---|---|
| function-level fixture parity | pure helpers, bracket parsing, regime labels |
| state-row CSV diff | reheat/atlas/tmax/HeadA generated features |
| shadow journal replay | regime-routed, tmax, HeadB, theta shadow |
| live runner dry import plus telemetry replay | tiny-live code paths |
| latency measurement | any future metar_cross fast-path consolidation |
| import-closure guard | every live/shadow runner after feature-layer import migration |

Diff standards:

- Exact string match for enum labels and selected strategy-private fields.
- Numeric tolerance only for floats with documented precision, normally `1e-9`.
- Missing values must match semantically; `unknown`, `None`, and empty string are not interchangeable unless the old contract already treated them as equivalent.
- CI must fail if a live/shadow runner imports `weather_feature_layer.labels` directly or through a transitive import.
- If a diff is real, stop and classify it before migration.

## Source Events vs Observations

`observations`:

- Standardized weather facts by city/station/as-of.
- Used to answer “当前温度、running max、云、风、湿度是什么”.
- Appropriate input for `weather_state_v1`.
- Carries observation freshness as weather-truth credibility fields such as
  `obs_age_minutes`, `expected_report_cadence`, and `station_gap_state`.
  `station_gap_state` currently means: non-ok observations become
  `observation_not_ok`; missing age becomes `age_unknown`; missing cadence uses
  `fresh_unknown_cadence` up to 90 minutes and `stale_unknown_cadence` above 90
  minutes; known cadence uses `within_expected_cadence` until
  `age_minutes > cadence + 10`, then `beyond_expected_cadence`.

`source_events`:

- Event stream about when a source reported or was detected.
- Used to answer “这条观测什么时候由源头发布、我们什么时候看到、延迟是多少”.
- Appropriate input for latency telemetry and freshness diagnostics.
- Carries event timing fields such as `source_report_ts_utc`, `detect_ts_utc`,
  `fetched_at_utc`, lag buckets, and source race diagnostics.

Feature layer can consume both, but must not collapse them into one field. A caller that needs weather truth uses observation time. A caller that needs latency/freshness uses detect/fetch time.

## Immediate Recommendation

Start with Phase 1 to Phase 3. They are low-risk and mostly pure feature semantics. Delay Phase 4/5 consumer rewiring until the package has fixtures and parity harnesses. Do not start order_events, factory migration, or metar_cross fast-path consolidation as part of this feature-layer work.
