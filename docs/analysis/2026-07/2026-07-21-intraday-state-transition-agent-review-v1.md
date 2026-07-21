# 日内天气状态机与 LLM 前置节点审阅 v1

Status: `current-reference / design-review`

Scope: 只做研究设计和文档收敛；不改 live、runner、selector、city pool 或 sizing。

## 数据快照

- 数据源：用户提供的 ChatGPT Pro 方案全文；本仓 `WEATHER_TEMPERATURE_CONTEXT_FEATURE_LAYER.md`、
  `WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md`、`WEATHER_STRATEGY_REGISTRY.md`、intraday regime atlas、
  weather-climate feature review，以及当前 `weather_feature_layer/regimes.py` / `weather_data_feed/weather_context.py`。
- 审阅时间：2026-07-21；本轮没有刷新 DB、行情、forecast 或 settlement，也没有发布 live_real PnL。
- 记录行数：本轮为设计审阅，`N/A`；引用的最近 atlas 快照为 14,368 个 city-date-hour states、36 城、
  `2026-05-19..2026-07-08`。
- unsettled 占比：`N/A`；missing_bracket：`N/A`。
- 外部材料里的 ICAO/WMO/TAF/Polymarket 说明本轮没有逐条做来源审计；这里吸收的是研究结构，
  不是把外部文字当成已验证 alpha 或生产规范。

## 结论与动作

这套框架与我们当前 regime **高度同源，但不是同一个坐标系**：

- 我们已有的是多轴 PIT state：站点/城市底座、forecast runway、peak clock、温度路径、云湿、风、
  solar、source freshness 与 settlement lattice。
- 外部十类更像面向人解释的 **dynamic process taxonomy**：日照混合、低云、清云、持续降雨、
  对流冷池、海风、锋面、焚风、逆温、下垫面。
- 它真正补上的维度不是“再加十个 regime label”，而是
  `next_state`、`P(transition in 1-3h)` 和 `transition_time_distribution`。

因此推荐吸收为共享 `intraday_state_transition_card_v0`，放在 strategy probability head 之前，
但保持 `research/design`：先做全分母 zero-notional 日报/事件卡和 collector，不改 live，不把类型变成 hard route。

LLM 节点应当这样放：

```text
immutable PIT sources
  -> deterministic canonical state/features
  -> transition scorer / scenario builder
  -> versioned LLM state-card synthesis
  -> calibrated Tmax distribution + market residual
  -> expression EV / target book
  -> plan -> order -> fill -> settlement
```

LLM 负责归纳冲突、给多情景解释和指出缺证据；确定性 parser、native-lattice 映射、概率校准、
可执行价格与下单仍由代码负责。LLM 不直接发单，也不把自然语言 confidence 当 `p_win`。

## Target

```text
估计 P(Tmax exact outcome, next intraday state, transition time | PIT weather/source state)，
并在固定 rows/labels/quotes 上检验 state-transition card 及 LLM synthesis
是否相对同一时点 market 和当前 Tmax model 提供增量 residual。
```

- physical target：最终 exact bracket 分布、未来 1–3 小时状态转变及其时间。
- grain：`city + target_date + decision_ts`；同一 city-day 允许多次状态更新，不允许一天一个永久标签。
- decision timestamp：每个输入必须 `available_at_utc <= decision_ts`。
- label：官方 settlement exact bracket；天气过程标签只能由冻结规则或版本化标注生成。
- executable expression：full-ladder direct bid/ask、官方 fee、depth/slippage/adverse-selection 后的 net edge。
- primary metric：exact/bucket logloss、Brier、transition-time calibration；交易层才看 fee-adjusted residual/ROI。

## 与现有 regime 的关系

| 外部层/过程 | 现有对应 | 结论 |
|---|---|---|
| 站点长期底座 | `city_family`、`geo_context`、coastal flow、timezone/solar、source profile | 已有骨架；应继续拆成独立静态轴，不做永久单标签 |
| 日照混合/剩余加热 | `day_regime`、peak clock、forecast runway、solar、warming/path state | 高重合 |
| 低云/雾/持续雨 | cloud/ceiling/visibility、precip、RH/dewpoint、regime duration | 新 live feature contract 已部分覆盖；历史 PIT 覆盖仍不齐 |
| 清云反弹 | cloud change、clear-sky duration、warming acceleration | 特征部分已有；显式清云时刻概率缺失 |
| 雷暴/冷池 | thunder/precip/wind change、forecast precip | METAR 侧部分已有；雷达、闪电、gust-front path 的 PIT 证据缺失 |
| 海风 | coastal geometry、wind direction/change、marine thermal state | 高重合；当前 onshore sector 仍是先验，未校准到到站时间 |
| 锋面/暖冷平流 | wind/dewpoint/pressure trend、forecast curve | 当前不完整；上游站网络与 front arrival time 缺失 |
| 焚风/下坡风 | wind/terrain interaction | 主要缺口；需客观地形、pressure gradient 和 vertical profile |
| 盆地逆温/冷池消散 | basin context、visibility/wind/solar | 部分 proxy；边界层/探空和 break time 缺失 |
| 下垫面修正 | 近期降水、soil moisture、snow、植被/海温 | 当前共享 intraday primary 基本缺失 |

外部十类混合了 forcing、边界层状态、过程结果和慢变量修正，彼此不互斥。实现时应输出多轴概率，
例如 `low_cloud -> clearing -> sea_breeze_cap`，而不是强迫一天只能属于 `type=3`。

## 为什么“每天扫一遍＋LLM”可能有用、但还不能叫 alpha

它可能先改善三件事：

1. 把散落在 METAR、TAF、forecast curve、云雨风和 source freshness 里的证据汇成同一张可审计卡。
2. 对转变型天气显式预测“过程几点到”，避免只按 local hour 或当前温度机械外推。
3. 在下一次关键观测前固定有利/中性/轻微不利情景，区分应该提前报价还是等待。

但现有证据同时要求保守：2026-06-21+ 的 weather-climate ablation 中，raw market date-equal
logloss 为 `0.1974`；`market75_all_regimes25` 为 `0.1992`，没有打败 market。说明 regime 解释力不自动
等于 market residual。新节点必须做 `market`、`current Tmax model`、`+deterministic transition features`、
`+LLM card` 四组同分母 A/B；如果 LLM 组 proper score/交易 residual 没有增量，就只保留日报解释价值。

## 每日扫描不是“一天一篇静态报告”

推荐每个 active city-day 至少保留四类 checkpoint：

1. `morning_map`：站点底座、场景先验、forecast peak clock、关键转变和观测计划。
2. `state_confirmed`：连续观测已确认主导过程，但决定性转变尚未公开。
3. `pre_transition_or_key_report`：关键过程/下一报文前，冻结三情景和价格条件。
4. `post_update/end_of_day`：更新概率、记录为何 hold/skip，并在结算后回连 label。

除固定 checkpoint 外，TAF amend、METAR/SPECI、新雨区/雷电、上游风转、source conflict、盘口跳变应触发
event-driven card。全 universe 的 `observed/selected/blocked` 都要写，不能只保存 LLM 觉得值得交易的城市。

## State Card v0

```yaml
identity:
  city: ...
  target_date: ...
  decision_ts_utc: ...
  checkpoint_type: morning_map|state_confirmed|pre_transition|post_update
  input_snapshot_hashes: {...}

site_baseline_axes:
  geography: [...]
  terrain: [...]
  surface: [...]
  source_topology: [...]

current_state:
  thermal_stage: ...
  remaining_energy: ...
  radiation_moisture: ...
  flow_geography: ...
  information_reliability: ...
  diagnostic_process_probs: {...}   # 十类可多标签，不要求和为 1
  stable_or_transition: ...

transition_outlook:
  next_state_probs: {...}
  transition_window_local: [start, end]
  transition_time_quantiles: {p10: ..., p50: ..., p90: ...}
  supporting_sources: [...]
  conflicting_sources: [...]
  missing_evidence: [...]

tmax:
  settlement_native_lattice: ...
  exact_bracket_probs: {...}
  peak_time_window_local: ...
  overshoot_hazard: ...

decision:
  next_observation_scenarios: {favorable: ..., neutral: ..., mildly_adverse: ...}
  invalidation_signals: [...]
  expression_edges_lcb: {...}
  action: observe|shadow_candidate|no_trade
  reason_codes: [...]

lineage:
  feature_versions: {...}
  prompt_version: ...
  llm_model_version: ...
  card_schema_version: intraday_state_transition_card_v0
```

字段有明确 owner：LLM 只生成 process/scenario/conflict/invalidation 的结构化 synthesis；
`exact_bracket_probs` 由校准后的 probability model 注入，`expression_edges_lcb` 由 downstream
execution evaluator 注入。最终人类日报可以展示三者，但不能让 LLM 自己填写后两项。

必须保留 raw source value/unit 与 settlement-native lattice。Atlanta terminal false cross 和 Ankara
native-unit lattice 是强制 negative controls：快源跨档、QC pass 或小数温度接近下一整数，都不能自动变成
settlement state 或交易确认。

## Data integrity / PIT

| 项目 | 当前判断 |
|---|---|
| raw source and coverage | METAR/forecast/current feature frame 已有；雷达、卫星、上游网络、surface/soil 不完整 |
| forecast issue/run/hash/age | 新 card 必须引用现有 immutable forecast lineage，不能只写 latest value |
| source first-seen/cadence | 使用 canonical first-seen/available-at；source event 与 routine/settlement 分开 |
| source-to-settlement basis | 必须显式；快源只作概率特征 |
| book freshness / archive bias | direct fresh executable quote；晚起 archive 只算 evidence gap |
| label availability | exact bracket settlement；过程 transition label 需冻结定义/版本 |
| TAF/radar/satellite history | 当前不足以做诚实长历史回放；先 forward collector，不用事后网页回填 |

## Signal funnel

| 层 | grain | rows | dates |
|---|---|---:|---:|
| active city-day checkpoints | city-date-decision | 待 collector | 待 collector |
| state/transition card complete | city-date-decision | 待 collector | 待 collector |
| calibrated positive residual | expression-decision | 待模型 | 待模型 |
| selected | first expression/city-day or target-book delta | 待 shadow | 待 shadow |

## Evidence funnel

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| PIT feature/source | city-date-decision | 待 collector | 待 collector | TAF/radar/satellite/upstream 不完整 |
| PIT quote | expression-decision | 待 collector | 待 collector | 必须 direct ask/depth |
| settlement | expression | 待 forward | 待 forward | official label 延迟 |
| executable expression | expression-decision | 待 shadow | 待 shadow | fee/slippage/AS |
| fill | fill | 0 | 0 | 本设计不下单 |

## Frozen forward / acceptance

- 先冻结 card schema、process axes、checkpoint cadence、prompt/model version和 missing-data 行为。
- 同 rows 比较：`market`、`current Tmax model`、`+transition features`、`+LLM card`。
- 概率层先看 exact/bucket logloss、Brier、calibration 和 transition-time coverage。
- 交易层再看 fresh ask/depth、official fee、selected/blocked、target-date block CI。
- 至少积累 10 个新 settled forward dates 后才讨论 shadow verdict；这不是 live promotion 许可。
- LLM 输出若不能在 prompt/model/input hash 固定后稳定重放，只能作为人类摘要，不能进入模型特征。

## 研究判定与 8 环覆盖

```text
significance=NA
baseline=NA
forward=NA
conclusion=inconclusive (design accepted; empirical increment untested)
live_action=none
```

本轮只覆盖问题定义、PIT/lineage 边界、概率/market baseline 设计、execution ownership 和 forward
验收设计。8 环中的描述性绩效、统计推断、信号判别、概率评分、执行微结构、容量、组合相关性和
基准反事实都尚未产生该 card 的实证结果；不能据此给 keep/cut/size/live 动作。

## Bloodline placement

- 共享 source/parser：`weather_data_feed/`；TAF/radar/satellite/upstream collector 先补 immutable raw 与 first-seen。
- deterministic state/transition features：`weather_feature_layer`，不另建策略私有事实真相。
- card/opportunity：挂到 `fact_signal_candidates` 对应 city-date-decision 机会血缘，保留 observed/selected/blocked。
- LLM card：版本化派生层，引用 `feature_frame_ref` 和输入 hash；不覆盖 canonical facts。
- strategy：由 Tmax probability/residual head 消费，不直接 route 到 YES/NO。
- runtime：第一阶段 zero-notional report/collector；`live_action=none`。
