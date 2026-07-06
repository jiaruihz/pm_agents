# Weather Feature Layer Contract Plan

Status: design-draft
Updated: 2026-07-06
Source of truth: no; migration contract proposal
Superseded by / Used by: WEATHER_ARCHITECTURE_SPINE.md; WEATHER_FEATURE_LAYERING_PLAN.md; WEATHER_TEMPERATURE_CONTEXT_FEATURE_LAYER.md; WEATHER_DOCS_INDEX.md

## One-Line Decision

新增独立包 `weather_feature_layer/`。它不是新的策略，也不是新的 data-feed。它是 `weather_data_feed` 标准化数据之上的 point-in-time 机制特征层，统一产出“天气状态、forecast runway、城市/来源 bias、market geometry、regime state”这几类粗粒度特征，供 HeadA、HeadB、tmax、regime-routed NO、theta/current YES、station-basis、Range RV 等策略消费。

原则：共享的是机制状态，不共享交易结论。`edge`、selector、sizing、maker/taker lifecycle、live gate、order/fill/PnL 不能进这个包。

## Why A Separate Package

当前代码里已经有共享特征，但边界不干净：

| 当前位置 | 当前问题 | 迁移判断 |
|---|---|---|
| `weather_data_feed/weather_context.py` | 已包含温度、云、湿度、风、peak clock 机制特征，但 data-feed 应只负责 L0 数据协议和标准化输入 | 搬到 `weather_feature_layer`，`weather_data_feed` 保留兼容 re-export 一段时间 |
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

`weather_feature_layer` 的输入 grain 和输出 grain：

| Grain | 用途 | 例子 |
|---|---|---|
| `city_date_snapshot` | 一个城市、一个 target_date、一个决策时间点的天气状态 | theta/current YES、regime-routed NO、HeadB |
| `city_date_snapshot_bracket` | 同一状态下展开到 bracket/expression 的市场几何 | tmax、Range RV、HeadA sibling expression |
| `city_source_asof` | 某城市/forecast source 在 as-of 时刻可用的历史 bias 和 source reliability | HeadA、regime-routed、tmax ablation |
| `source_event_state` | source event 的观测时间、检测时间、age、lag，用于 latency telemetry | metar_cross shadow、theta telemetry；不是交易触发器 |

## Feature Families

不要把特征拆成一堆单点 flag。初版只保留六个粗粒度机制家族。

### 1. Thermal Path

回答：当前温度路径还在上行、平台、回落，还是已经过峰。

通用字段：

- `current_temp_native`
- `running_max_native`
- `decline_from_running_max_native`
- `minutes_since_running_max`
- `temp_trend_1h_native`
- `temp_trend_3h_native`
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

边界：forecast source selection 仍由 `weather_data_feed` 和 `source_policy` 定义。特征层只消费已显式标注的 source，不静默替换 GFS/ECMWF。

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
- `current_no_ask`
- `d1_yes_ask`
- `d1_no_ask`
- `d2_yes_ask`
- `d2_no_ask`
- `tail_yes_ask`
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
| sky/moisture/wind context | cloud/warming, moisture/cloud, wind/thermal interactions | `weather_data_feed/weather_context.py`, atlas |
| city/source bias | as-of forecast error, hot/cold tail rate, station/source reliability | low-price telemetry, regime-routed live, station-basis research |
| market geometry | bracket parser, current/d1/d2/tail ladder, bracket distance, book state | tmax P0/P3, HeadA overlays, Range RV research |
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
- `builder_version`
- `input_snapshot_id` or source file hash when available

## Current Asset Review

| Asset | Shared pieces | Private pieces | Plan |
|---|---|---|---|
| `weather_data_feed/weather_context.py` | weather context labels, peak clock, wind/moisture/cloud interactions | none substantial | Move to `state.py` and `regimes.py`; keep re-export wrapper |
| `weather_data_feed/observation_clock.py` | observation as-of, freshness metadata, cadence facts | strategy-specific freshness veto | Keep in data-feed; feature layer consumes its timestamps and age fields |
| `weather_data_feed/source_basis.py` | source/station basis facts | live readiness gate | Keep canonical source basis in data-feed; derived bias states in `bias.py` |
| `regime_routed_no_stable.py` | bracket parser, regime labels, route candidate raw features | ASK caps, route hours, route selection, soft weights | Split; strategy imports feature helpers, keeps policy local |
| `low_price_yes_tail_telemetry.py` | bracket bounds/distance, as-of bias fields, local time bucket | pcal score, source-aware selector tags, HeadA telemetry row contract | Split; bias/geometry up, HeadA tags remain |
| `tmax_distribution P0/P3` | market-local distribution, boundary features, numeric/context feature specs | model fitting, blend prediction, EV selector | Extract market geometry; keep P1/P2/P4/P5/P6 strategy/research |
| `reheat_feature_factory_v1` | temperature path, forecast peak, meteo state | future labels, payoff, current YES outcome | Use as offline fixture source; new builders should not depend on docs-generated CSV path |
| `intraday_weather_regime_atlas_v1` | regime taxonomy and state rows | label/outcome/payoff columns | Use as parity reference; move taxonomy to `regimes.py` |
| theta/current YES live | live observation path, running max state, obs age telemetry | current YES model, freshness gate, pre-update blackout, taker cushion | Only migrate parser/path features after replay parity |
| METAR reversal / metar_cross | source event timestamps, parsed observation state, bracket state | crossed-market trigger, FOK execution, latency assumptions | Parser/timestamp only; fast path remains frozen |
| station-basis | station/source mismatch state | live-prep gate and execution policy | Bias state can be shared; approval gate stays strategy |
| Range RV / market structure | ladder geometry, under/over book state | portfolio construction and execution | Reuse `market.py`; no strategy migration in v1 |

## Migration Plan

### Phase 0: Contract Only

Deliverables:

- Add this contract plan and index entry.
- Do not create package behavior yet.
- Do not change live/shadow runners.

Acceptance:

- Review agrees on inclusions/exclusions.
- `WEATHER_DOCS_INDEX.md` points to this plan as `design-draft`.

### Phase 1: Package Skeleton And Pure Re-exports

Deliverables:

- Create `weather_feature_layer/` skeleton.
- Move or copy pure helpers from `weather_data_feed/weather_context.py` into `weather_feature_layer/state.py`.
- Add compatibility wrappers so old imports still work.
- Add parity tests for context labels using existing fixture rows.

Acceptance:

- Existing `weather_data_feed.weather_context` tests and importers pass unchanged.
- No runner output changes.
- No live process restart required.

### Phase 2: Regime Labels And Bracket Primitives

Deliverables:

- Extract `parse_bracket`, `bracket_contains`, bracket low/high/mid helpers to `market.py`.
- Extract generic day/intraday/running max/moisture/wind regime labels to `regimes.py`.
- Update `regime_routed_no_stable.py` to import generic helpers while retaining policy constants locally.

Acceptance:

- `regime_routed_no_stable` old-vs-new parity on historical shadow journal rows is byte-identical for route inputs and selected rows.
- `tmax_distribution_edge_shadow_v1` state-feature columns match before/after where it consumes regime labels.

### Phase 3: Bias Layer

Deliverables:

- Move as-of bias lookup and classification primitives into `bias.py`.
- Define `source_fit_state` and `source_reliability_state` as mechanism labels.
- Keep HeadA pcal/source-aware tags and regime-routed multipliers private.

Acceptance:

- HeadA `low_price_yes_integrated_tail_shadow_v2` telemetry diff shows identical non-migrated strategy tags and identical shared bias columns.
- Regime-routed live/shadow route decisions are identical on replay rows.

### Phase 4: Weather State Builder

Deliverables:

- Implement `build_weather_state_frame()` for `city_date_snapshot` from data-feed snapshots and observation cache.
- Include timestamp contract: `source_report_ts_utc` for observation time and `detect_ts_utc` / `fetched_at_utc` for detection/fetch time.
- Keep `source_events` and `observations` semantics separate.

Acceptance:

- Theta/current YES observation-path replay matches current runner fields on the approved parity window.
- METAR/LDM parser parity remains at the prior accepted level.
- No theta freshness gate or metar_cross trigger logic is changed.

### Phase 5: Market Geometry Builder

Deliverables:

- Implement `add_market_geometry_features()` for `city_date_snapshot_bracket`.
- Extract tmax P0 market-local distribution and P3 boundary features that are not model-specific.
- Extract HeadA bracket-distance helpers into shared geometry.

Acceptance:

- tmax P0/P3 feature columns match old scripts for the same fixture rows.
- HeadA mechanism overlay candidate rows match old bracket-distance and book-state fields.
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

Diff standards:

- Exact string match for enum labels and selected strategy-private fields.
- Numeric tolerance only for floats with documented precision, normally `1e-9`.
- Missing values must match semantically; `unknown`, `None`, and empty string are not interchangeable unless the old contract already treated them as equivalent.
- If a diff is real, stop and classify it before migration.

## Source Events vs Observations

`observations`:

- Standardized weather facts by city/station/as-of.
- Used to answer “当前温度、running max、云、风、湿度是什么”.
- Appropriate input for `weather_state_v1`.

`source_events`:

- Event stream about when a source reported or was detected.
- Used to answer “这条观测什么时候由源头发布、我们什么时候看到、延迟是多少”.
- Appropriate input for latency telemetry and freshness diagnostics.

Feature layer can consume both, but must not collapse them into one field. A caller that needs weather truth uses observation time. A caller that needs latency/freshness uses detect/fetch time.

## Immediate Recommendation

Start with Phase 1 to Phase 3. They are low-risk and mostly pure feature semantics. Delay Phase 4/5 consumer rewiring until the package has fixtures and parity harnesses. Do not start order_events, factory migration, or metar_cross fast-path consolidation as part of this feature-layer work.
