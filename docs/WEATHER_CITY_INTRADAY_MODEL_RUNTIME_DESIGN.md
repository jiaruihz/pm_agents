# 跨城市日内温度模型 Runtime：总体设计与迁移方案

Status: approved migration roadmap / partial implementation; model-performance baseline remains rolling
Updated: 2026-08-02
Scope: 城市级分钟/小时间隔观测模型从采集、PIT checkpoint、replay 到统一候选与下单执行的目标架构
Source of truth: 目标模块边界与接口是；当前生产进程、实例和迁移状态不是
Used by: `AGENTS.md`、`CLAUDE.md`、`weather-strategy-research`、各城市模型研究与接入任务
Reviewed against: [东京/赫尔辛基/阿姆斯特丹 Runtime Contract 实数审计 v1](analysis/2026-08/2026-08-01-three-city-runtime-contract-audit-v1.md)

## 0. 结论

跨城市研究应收敛到“**共享 runtime + 城市策略插件**”，但不强迫城市模型共用算法、特征或是否使用盘口：

```text
共享采集配置 / immutable raw / four clocks
  -> 共享 PIT checkpoint 与 deterministic replay
  -> DecisionContext
  -> 城市策略插件
       ├─ 纯天气模型
       ├─ market-offset / market-prior 模型
       ├─ 天气 + 盘口联合模型
       └─ 城市特有 expression / selection policy
  -> 标准 SignalCandidate
  -> 标准 TradeIntent
  -> 共享 plan -> order -> fill -> settlement
```

盘口和模型**不是必须独立的两层**。盘口可以是模型的 prior、offset、联合特征或微观结构特征；真正稳定、必须统一的边界是 `TradeIntent` 之后的计划、风控、下单、成交与结算链。模型使用的盘口证据与执行报价必须分别留痕，避免未来信息、重放漂移和成交价格冒充模型输入。

本文件只定义目标设计、迁移顺序和验收标准，不授权修改生产 runner、采集进程、live/shadow switch 或真实下单参数。

三城实数审计确认总体边界成立，同时把四项提升为实现前 P0：typed observation/revision、业务日期与物理分区解耦、source/official/market 多 anchor、repo 与 deployed runtime contract handshake。原计划不能跳过这些项直接抽象现有 adapter。

## 1. 为什么这样切

赫尔辛基、东京和阿姆斯特丹已经证明两件事同时成立：

- 采集 cadence、source 能力、温度 lattice、特征和模型形式确实因城市而异；强行共用一个训练模块会扭曲物理机制。
- raw 保存、first-seen/available 时钟、PIT checkpoint、回放、候选血缘、盘口证据和执行链高度重复；继续各写一套会产生难以排查的时钟、漏采、重放和下单差异。

因此，框架统一“系统如何运行和留证”，城市插件决定“在这个时点知道什么、怎么算概率、要不要形成交易意图”。

## 2. 设计原则与非目标

### 2.1 必须统一

- source/profile 配置结构与调度引擎；具体 interval 值仍按 source/city 配置。
- typed immutable raw、point/interval/revision 事件 identity、`event_ts_utc / first_seen_ts_utc / available_at_utc / ingested_at_utc` 四时钟。
- 业务 `target_date` 与物理 shard 解耦的 `InputCatalog/DataLocator`；策略代码不得拼路径猜输入。
- PIT `DecisionContext` 构建、checkpoint identity、deterministic replay 和 live/replay parity。
- source、official/settlement、market expression 三套 lattice anchor 与 expression-union/full-ladder capture。
- `SignalCandidate`、`TradeIntent`、candidate→plan→order→fill→settlement 血缘。
- 评测 grain、同分母 market baseline、signal/evidence 双漏斗和 fee-adjusted 执行评测。
- 统一错误分类、freshness/coverage telemetry 和可定位的 blocker。
- producer/consumer schema fingerprint、checkout/module/config/artifact hash 与启动 handshake。
- canonical/materialization `build_id`、DB identity/mtime 与审计 `observed_at`，避免 refresh 中途改变分母却沿用同一报告身份。

### 2.2 保留城市自由度

- 历史训练数据整理和城市专属 adapter。
- 特征、缺失处理、算法、校准器和预测 horizon。
- 是否使用盘口，以及使用 raw market、offset、prior、spread/depth 等哪类输入。
- source-to-settlement basis、native-unit lattice、exact-bracket 映射。
- 形成候选前的城市特有 expression 与 selection policy。

### 2.3 非目标

- 不造一个“所有城市共用模型”。
- 不要求所有 source 使用相同秒数；统一的是 scheduler/profile contract。
- 不新建平行事实表、平行 order client 或第二套 settlement/PnL 计算。
- 不因迁移而删除旧城市资产；未迁移实现保留并标记 `legacy_adapter` / `dormant`。
- 不用兼容 fallback 隐藏缺数据或 schema 漂移；无法计算时产生结构化状态。

## 3. 模块边界

| 层 | 负责 | 不负责 |
|---|---|---|
| `weather_data_feed` | 城市/source identity、解析、单位、source profile、标准 observation/forecast/book snapshot | 策略概率、selection、下单 |
| data-feed service | 按 profile 调度采集、写 immutable raw、记录 first-seen/health | 城市模型、交易决策 |
| intraday runtime harness | 事件合并、PIT checkpoint、`DecisionContext`、virtual clock replay、状态与 telemetry | 城市算法细节、CLOB 私有实现 |
| city strategy plugin | 特征、模型、盘口如何进入模型、概率输出、expression/selection | 自建 collector、自建回放时钟、自建 order/fill 账本 |
| canonical migration | 标准候选进入 `fact_signal_candidates`，补 settlement/label/coverage | 重新解释 raw 或自算另一套 PnL |
| shared execution | `TradeIntent -> plan -> order -> fill -> settlement`，dedupe、exposure、fee、审计 | 重算模型或偷偷补特征 |

现有 [运行时平台设计](WEATHER_STRATEGY_RUNTIME_PLATFORM_DESIGN.md) 仍定义 StrategyHead、BaseRunner、supervisor 和控制面；本文件是其“城市日内概率模型”专门化设计，不另造一个竞争平台。first-seen 与 replay 语义服从 [信息血缘契约](WEATHER_FIRST_SEEN_INFORMATION_LINEAGE.md)。

## 4. 最薄但足够的接口

### 4.1 `CaptureProfile`

统一结构，不统一数值：

```yaml
city: tokyo
streams:
  - stream_id: official_observation
    source: generic_observer
    payload_kind: point_observation
    revision_policy: immutable_content_revision
    schedule:
      mode: fixed
      interval_seconds: 60
  - stream_id: orderbook
    source: polymarket_clob
    schedule:
      mode: hot_cold
      hot_interval_seconds: 10
      cold_interval_seconds: 300
      hot_when: "local_active_window"
```

允许的 schedule 至少包括 `fixed`、`hot_cold`、`after_event_burst` 和 source 原生 push。调度器输出统一事件 envelope；“东京 60 秒、KNMI 热段 10 秒/冷段 300 秒”是配置差异，不是两套运行框架。

profile 还必须声明 payload kind、measurement interval、native unit/precision、revision policy、业务日期 timezone、producer owner 和 contract fingerprint。Amsterdam/KNMI 的 `ta + 10m tx + revision` 与 Tokyo/JMA、Helsinki/FMI 的 point temperature 是不同 payload type，不能靠一组 nullable columns 假装同构。

### 4.2 `EventEnvelope`

每个可回放输入必须包含：

| 字段 | 语义 |
|---|---|
| `event_id` | 基于稳定 identity 的可复现 ID |
| `event_kind` | observation / forecast / book / source_health 等 |
| `payload_kind` | point observation / interval summary / forecast curve / book 等 typed payload |
| `city`、`source` | 规范 identity |
| `event_ts_utc` | 上游事实发生或观测时间 |
| `measurement_start/end_utc` | interval payload 的测量窗口；point observation 可为空 |
| `first_seen_ts_utc` | 本系统首次见到时间 |
| `available_at_utc` | 策略允许使用该信息的最早时间 |
| `ingested_at_utc` | 写入本地 raw 时间 |
| `event_role`、`revision_of_event_id` | new content / revision 及其父事件 |
| `material_state_change` | revision 是否改变 PIT state |
| `native_unit`、`native_precision` | source 原生单位和精度 |
| `payload_hash`、`schema_version`、`schema_fingerprint` | 去重、兼容检查与可复现性 |
| `raw_ref` | immutable raw 引用 |

checkpoint 和 replay 只按 `available_at_utc` 放行信息，不能用文件 mtime 或事后 archive 时间代替。
`target_date` 只作业务语义；raw 通过 catalog/index 定位，不能假设文件按 target_date 分区。

### 4.3 `DecisionContext`

runtime 在每个合法 trigger 构造不可变 context：

```text
checkpoint_id
city / target_date / decision_ts_utc / trigger_event_id
weather_state_ref / forecast_state_ref / settlement_profile_ref
feature_book_snapshot_ref? / execution_book_snapshot_ref?
source_lattice_anchor / official_lattice_anchor / expression_anchor
required_stream_watermarks / input_event_set_hash
canonical_build_id / producer_consumer_contract_manifest
coverage_status / freshness / source_health
```

`feature_book_snapshot_ref` 是模型真正读到的盘口；`execution_book_snapshot_ref` 是 selection/成本判断使用的报价。两者可能相同，也可能因时钟和刷新时点不同而不同，必须分别保存。
等待首报、source 领先 official、one-sided、目标 expression 未被采集和跨日 producer 未 ready 都是结构化 coverage 状态，不是 adapter exception。

### 4.4 `CityStrategyPlugin`

目标接口保持很薄：

```python
class CityStrategyPlugin(Protocol):
    def build_features(self, context: DecisionContext) -> FeatureVector: ...
    def predict(self, features: FeatureVector) -> ModelOutput: ...
    def select(
        self,
        context: DecisionContext,
        output: ModelOutput,
    ) -> list[SignalCandidate]: ...
```

训练可以留在城市自己的 research package；runtime 只加载带版本、schema 和 artifact hash 的冻结模型。插件不得主动联网、自己轮询 source、直接调用 order client 或写私有 fill/PnL。

### 4.5 `ModelSpec` 与 `ModelOutput`

`ModelSpec` 至少声明：

- `model_id`、artifact/feature schema hash、训练 cutoff、target/horizon；
- `target_kind`: `physical_path | settlement_outcome | market_expression`；
- `market_feature_role`: `none | prior_offset | joint_feature | microstructure_feature`；
- `market_feature_clock`: `pre_event | first_post_event | decision_current | none`；
- 必需输入、缺失策略和支持的 one-sided book 形态；
- 预测 outcome 与 settlement/native lattice 的精确定义。

`ModelOutput` 至少含 `p_model`、target、model/feature lineage、实际使用的输入 refs、scorable 状态和 blocker。没有可用 midpoint 时：

- 依赖 midpoint 的模型返回 `not_scorable_missing_midpoint`；
- 纯天气或明确支持 ask-only/one-sided 的模型仍可计算；
- 原 checkpoint 保留在 coverage 分母中，不能静默丢行或形成重试风暴。

`PredictionTarget` 必须同时写 source/settlement truth、horizon、native lattice 和 event 语义。`P(new source strict high in 60m)` 只能作为 `physical_path` output；只有已映射为精确 settlement expression probability 的 output 才能产生 `SignalCandidate/TradeIntent`。

### 4.6 `SignalCandidate`

统一候选是研究与执行之间的事实边界，至少包含：

```text
candidate_id / checkpoint_id / city / target_date / decision_ts_utc
target_id / expression_id / side
p_model / market_p? / executable_cost?
model_id / feature_set_id / feature_book_snapshot_id?
execution_book_snapshot_id? / policy_id
candidate_status / blocker_reason / selected
```

它应进入 canonical `fact_signal_candidates`，包括未选中、缺盘口、不可执行和未结算行；不能只记录最后下单的赢家。

### 4.7 `TradeIntent`

`TradeIntent` 是城市插件之后真正稳定的执行边界：

```text
intent_id / candidate_id / condition_id / token_id / side
requested_size / sizing_profile / execution_profile
max_cost / ttl / dedupe_key / exposure_bucket
mode = research | shadow | zero_notional | live
```

只有共享 execution runtime 可以把 intent 变成 plan/order。任何 `live` intent 仍须服从既有部署 skill、显式授权、资金 cap、pause 和 exchange 验证；本设计不会自动赋予 live 权限。

## 5. 盘口进入模型时的纪律

盘口可以进入模型，但必须把“预测信息”和“交易成本”分开留痕：

1. 同一 prediction row 明确记录 market feature 的 snapshot、clock 和处理方式。
2. 评测始终保留同 rows 的 raw/calibrated market baseline；不能因为模型用了 market 就不再比较 market。
3. `p_model - executable_cost` 使用 decision-time fresh executable quote，不用训练输入的旧 midpoint 冒充成本。
4. pre-event、first-post-event 和 decision-current 是不同研究问题；不得混在一个 `model_id` 中。
5. book 缺失属于 evidence coverage；模型明确不能算时返回状态，不得把缺盘口伪装成 eligibility filter。
6. market-offset/joint 模型必须与纯天气、纯 market 做固定 rows、固定 label、固定 split 的 A/B。
7. `relative_offset` 必须声明相对 source、official 或 expression 哪个 anchor；capture 取所需 anchor expressions 的 union，anchor 差距超出窗口时显式记 coverage gap，不能报成 bracket mismatch。
8. `first_post_event` 必须由 checkpoint builder 选择“首个满足预注册 quote contract 的 book”，不能由 scorer 第一次成功运行的偶然时点决定。

这允许东京的 market-offset、赫尔辛基的 residual 研究和阿姆斯特丹的 source-cross/quote policy 共用 runtime，同时保留各自机制。

## 6. Live 与 replay 必须同构

replay 不复制策略逻辑，只替换两个依赖：

- wall clock → 由 `available_at_utc` 推进的 virtual clock；
- live input stream → 按同一 `EventEnvelope` 排序的 immutable recorded stream。

同一 model artifact、profile、plugin 和 policy 在同一 raw 输入上应得到一致的 checkpoint、candidate ID、概率、blocker 和 intent。replay 禁止联网补历史数据；缺失就是缺失。

最小 parity fixture 应覆盖：

- observation 与 book 同秒但 arrival 顺序不同；
- forecast 晚到、重复 payload、进程重启后的去重；
- KNMI 同一 measurement interval 的 initial/update/revision 与 late backfill；
- one-sided book、stale book、无 midpoint；
- hot/cold cadence 切换和 after-event burst；
- 本地日期先于 UTC shard 切换、等待首报和新日文件尚未产生；
- source lattice 领先 official 一档/两档，以及 multi-anchor expression union；
- develop fixture 与 deployed-runtime sample 的 schema fingerprint handshake；
- exact-bracket overshoot 与 native-unit lattice 边界。

## 7. 统一评测与可观测性

概率与执行评测继续服从 [跨城市研究约定](WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md)：

- checkpoint / transition / state-entry 三种 grain；
- 按 `target_date` 等权和 block bootstrap；
- 纯天气、market、market-aware model 固定 rows 比较 logloss、Brier、calibration；
- signal funnel 与 evidence funnel 分开；
- 有盘口才评估 fresh executable cost、官方 fee 后 PnL/ROI；无盘口标 `not_available`。

共同 runtime 至少暴露：last success/attempt、next due、lag、raw write、dedupe、checkpoint、scorable/blocker、candidate、intent、order/fill 等计数和关联 ID。重复 loop error 必须同时有稳定 `incident_id`，区分 poll rows 与独立故障窗口；错误需带 stage、input refs、checkpoint ID、producer/consumer contract hash。排错路径固定为：

```text
source health -> raw event -> available clock -> checkpoint
-> feature/model lineage -> candidate -> intent -> plan/order/fill
```

## 8. 迁移方案（2026-08-02 rolling-baseline 版）

### 8.1 基线不再等同于“冻结模型”

当前只积累了一两个 forward city-day，模型和链路仍会暴露 bug，因此迁移不等待一个并不存在的最终绩效基线，也不趁样本少直接替换全部 runtime。基线拆成三类：

| 基线 | 现在是否冻结 | 用途 |
|---|---|---|
| 结构基线 | 立即冻结 | code/config/schema/hash、raw sample、四时钟、当前错误和数量；用于判断 contract 是否漂移 |
| 行为基线 | 滚动积累 | 同一 checkpoint 下 legacy/vNext 的概率、candidate、blocker、intent 差异；由 dual-run 每日保存 |
| 模型绩效基线 | 暂不冻结 | Brier/logloss/calibration/market baseline/ROI；按 model artifact 版本和 frozen-forward window 分层 |

迁移期间 raw collector 持续运行，immutable journal 不重置、不改写；模型修复必须产生新的 `model_id/artifact_hash/config_hash/effective_from`，不能覆盖旧版本。框架 parity 与模型好坏分别验收：模型尚未盈利不阻止 contract 迁移，模型概率变化也不能被误报为 runtime parity bug。

### 8.2 阶段总览

| 阶段 | 主交付 | 风险 | 是否改变生产行为 |
|---|---|---:|---|
| Phase 0 | 结构快照 + rolling baseline ledger | 低 | 否 |
| Phase 1 | replay + 通用评测 + 固定事后报告 | 低 | 否，只写 research/temp DB |
| Phase 2 | `SignalCandidate/TradeIntent` + canonical bridge | 中 | 否，先临时 DB/dual-write |
| Phase 3 | Helsinki → Tokyo → Amsterdam 逐城 dual-run/cutover | 中高 | 只切 zero-notional shadow，逐城授权 |
| Phase 4 | 共享执行 runtime 的 non-live 迁移 | 中 | 否，legacy 仍是 live authority |
| Phase 5 | active execution 单实例 canary | 高 | 是，每次单独显式确认 |
| Phase 6 | canonical/report 正式切换 | 中 | 只做批准的增量 materialization |
| Phase 7 | 关闭 active 旁路、保留 legacy rollback | 中 | 分实例验证后进行 |

每个 phase 是独立、可 review 的 change set；不在一次变更中同时迁城市、改模型和切资金行为。Phase 1 可在 rolling baseline 积累期间立即推进；Phase 3 的三个城市分别验收，不组成一次批量切换。

### Phase 0：结构快照与 rolling baseline ledger

Status: **deployed census 与首轮 root-fix 已完成；rolling ledger 持续追加，不作为 Phase 1 的等待门。** 证据见 [Phase 0 census](analysis/2026-08/2026-08-01-city-intraday-phase0-census-v1.md) 和 [root-fix report](analysis/2026-08/2026-08-01-city-intraday-contract-root-fix-v1.md)。Helsinki/Tokyo producer、multi-anchor ladder 和 city shadow v2 已部署；Amsterdam 仍无同级 active producer/model consumer。

要做：

- 保存当前 production manifest、checkout/loaded SHA、config/schema/artifact hash 和 raw ownership。
- 冻结 point、interval/revision、cross-day、one-sided、anchor mismatch、off-hours stale 等 fixtures。
- 每个 city-day 追加 rolling manifest：raw/event/checkpoint/evaluation/error/scored/not-scorable/candidate/intent 数，以及各自 build/runtime identity。
- 错误修复记录影响窗口、错误 poll rows 与独立事件数，并给 order/fill/notional/fee/PnL delta。

完成标准：任意一天都能复原“哪份代码/配置/模型处理了哪些 raw”；结构快照不可变，行为和绩效按版本追加而不覆盖。

### Phase 1：低风险证据层整体迁移

Status: **2026-08-02 已完成公共证据层实现与三城 fixture 验收；未改变生产行为。** 公共包为
`weather_model_evaluation/`，统一入口为
`scripts/analysis/market_structure_edge/replay_city_intraday_evidence_v1.py`。入口只允许写 macOS/POSIX 临时目录、
`runtime/research/` 或 `research_outputs/`，拒绝 production/canonical runtime 路径。

已落地的契约：

- `EventEnvelope` 显式保存 event/available/first-seen/observation clocks、revision parent、state key、物理输入引用；
  revision 在父事件可见前会显式失败。
- `ReplayRunner` 只依赖注入的 input provider、clock 与 checkpoint builder；checkpoint/output identity 使用 canonical JSON hash，
  相同 event stream 不依赖输入文件枚举顺序。
- prediction table 固定保存 model/feature/market/label/coverage/runtime lineage；不可评分或缺 evidence 的 row 不删除，
  使用结构化 `not_available/coverage_gap`。
- 固定报告同时输出 prediction quality、同 rows market baseline、signal/evidence 双漏斗、plan/order/fill、maker/taker、
  fee-adjusted settled PnL 和 raw/canonical delta；无对应证据时报告 `not_available`，不把 0 伪装成已验证结果。
- 同一命令已回放 Helsinki、Tokyo、Amsterdam 的 9 个去重 event / 9 个 checkpoint，覆盖 point、interval、revision、
  cross-day、one-sided book 和 multi-anchor mismatch。两次独立运行目录逐文件一致，fixture output hash 为
  `d1ee35bfc0378944ed0e804bd9f626165cb9e156a4b472f559080ee6bf7d06f2`。fixture 本身没有模型概率和 settlement label，
  因而质量、PnL 正确显示为 `not_available`；这不是用伪标签补齐的模型绩效证据。

本阶段把 replay、评测和事后报告作为一个独立模块完成，不触碰生产 runner 或真实 DB。

要做：

- 正式提交并稳定 `weather_model_evaluation/`：checkpoint/transition/state-entry grain、Brier/logloss/RPS/ECE、target-date block bootstrap、simplex calibration 和 artifact lineage。
- 建立公共 `ReplayRunner`：`InputCatalog + EventEnvelope + virtual clock + checkpoint builder`；live/replay 只替换 clock/input provider，不复制城市模型逻辑。
- 定义统一 prediction table，保存 model/feature/market snapshot/label/coverage/runtime lineage。
- 建立固定报告入口：prediction quality、同分母 market baseline、signal/evidence 双漏斗、executable coverage、plan/order/fill、maker/taker、fee-adjusted PnL 与 raw/canonical reconciliation。
- Helsinki、Tokyo、Amsterdam fixture 都走同一命令；缺数据写 `not_available/coverage_gap`，不联网补历史。

完成标准：相同 raw 重复 replay 得到相同 checkpoint/output hash；三类 payload 均可回放；报告只写 research output 或临时 DB；既有城市结果在锁定 rows/labels 后可解释性复现。

### Phase 2：统一决策事实边界与 canonical bridge

Status: **2026-08-02 已完成 offline/temp-canonical 实现与 deployed-journal 验收；未切换生产 runner。** 证据和污染窗口见
[Phase 2 decision bridge report](analysis/2026-08/2026-08-02-city-intraday-phase2-decision-bridge-v1.md)。公共
`weather_city_runtime/` 已定义版本化 `ModelOutput`、`SignalCandidate`、`TradeIntent`；legacy
`CityScore/evaluation/paper_intent` 通过显式 adapter 转换，原 journal 保留不删。统一 bridge 复用
`fact_signal_candidates(candidate_grain_version=v2_event_checkpoint)`，只允许写 temp/research DB，并验证 legacy-only、
vNext-only 和 mixed journal。

实际 deployed raw snapshot 的 358 行决策记录收敛为 346 个唯一 candidate，临时 canonical 为 346，delta=0；
6 个可证明 token/outcome 的 zero-notional intent 成功转换，4 个 Tokyo YES 历史 paper intent 因 legacy NO-token
payload 未保存 `outcome` 而显式阻断，历史 notional/shares/orders/fills/PnL 影响均为 0。新 Tokyo telemetry 已补
`outcome`；YES expression token 的真实映射留给 Phase 3B，不从 NO token 猜测。

要做：

- 定义版本化 `ModelOutput`、`SignalCandidate`、`TradeIntent`；当前 `CityScore/evaluation/paper_intent` 先通过显式兼容转换，不删除。
- selected、unselected、one-sided、缺 book、不可执行 rows 全部保留；execution profile A/B 共享同一 candidate，不复制 signal。
- candidate 增量写入临时 canonical `fact_signal_candidates`；settlement/label/coverage 复用既有 migration。
- `TradeIntent` 只声明 token/side/size/profile/cap/TTL/dedupe/exposure；metadata、模型或城市插件不能授予/升级 live mode。
- 验证 legacy-only、vNext-only、mixed journal，禁止直接重建 production DB。

完成标准：同 checkpoint 的 candidate identity 在 replay/shadow 一致；raw candidate 与临时 canonical 数完全对账；research/evidence 两个漏斗能由 canonical rows 重建；没有城市私有 PnL/settlement 表。

### Phase 3：逐城市 dual-run 与 zero-notional cutover

共同方式：同一份已授权 raw 同时进入 legacy 和 vNext，输出不同目录；vNext 只产 zero-notional candidate/intent。模型调参和 runtime 迁移分开提交，parity 报告按 model artifact 版本比较。

#### Phase 3A：Helsinki

- 作为 point-observation、纯天气/market-offset 双表达参考实现。
- 对齐 forecast、official、FMI、book、one-sided、等待首报与状态变化去重。
- 至少覆盖 3 个完整本地 active window 和一次 UTC/业务日期边界。

完成标准：checkpoint/input refs/candidate/blocker 逐行 parity；无 exception storm；差异全部归因于明确 model version 或修复项。

#### Phase 3B：Tokyo

- 验证 cross-day physical shard、source/official/expression multi-anchor、ladder union、native lattice、off-hours、previous same-bracket probability。
- 至少覆盖 3 个完整本地 active window、一次跨日，以及一次 source/official anchor 分离或等价 golden fixture。

完成标准：同 capture cycle 选中正确 official expression；窗外不制造 stale error；缺 expression 保留 coverage gap；replay/shadow candidate parity。

#### Phase 3C：Amsterdam

- 先恢复并验证 KNMI producer ownership/freshness，再接 `ta` point、10-minute `tx` interval、initial/revision、measurement window、source-cross 和热/冷 cadence。
- 不为接口整齐伪造概率；physical-path output 未映射 settlement expression 前不得生成 intent。

完成标准：initial/revision/late-backfill replay 确定；只有 material state change 产生新 checkpoint；缺 midpoint 不丢分母；至少完成 3 个完整 active window 或覆盖预注册 revision fixtures。

每城通过后只把 vNext 升为正式 zero-notional shadow；不自动删除 legacy，不自动赋予 live。

### Phase 4：共享执行 runtime 的 non-live 迁移

要做：

- 补齐 `execution_config_id`、resolved profile、root/source/replacement action lineage、execution journal、risk/exposure/dedupe 和 Polymarket capability/fee identity。
- 依次迁 dormant runner、zero-notional shadow、paper runner；真实 side effect 仍由 legacy authority 执行。
- active runner 只增加 pure `shadow_execution_engine_compare`，比较 child role、shares、price、cap、TTL、reprice/cancel、remaining shares 和 blocker；新路径不得 claim key、reserve exposure 或调用 venue。
- canonical mixed-schema 只在临时 DB 验证。

完成标准：shadow/paper 单 token runner 不再私自重实现执行生命周期；legacy/new fixture parity 无未解释差异；partial fill、cancel/fill race、unknown submit、restart dedupe 均通过；无新增 live path。

### Phase 5：active execution 单实例 canary

顺序固定为：一个低风险 GTC tiny canary → 其余 GTC → fast-source GTD → basket/FOK、SELL、stop-loss 各自独立验收。fast-source 最后，因为它包含 latency、GTD、即时重试、maker remainder 和 signed share cap。

每次只切一个实例：记录 pre-state → legacy live/new comparator → parity → 新 runtime tiny canary → process/raw/exchange/canonical 对账 → 观察窗口通过后再扩大。每次都需用户对真实生产行为单独确认并调用 `weather-strategy-deploy`；本路线图不构成 live 授权。

完成标准：无重复 opportunity/plan/order；shares/notional/cap 不扩大；open order、fill、fee 与 canonical 逐笔一致；旧路径保留可审计 rollback，直到观察窗口完成。

### Phase 6：canonical 与统一报告正式切换

要做：

- 新 execution profile/config/root/action 字段进入 canonical plan/order，并在 fill grain 允许时投影到 `fact_trades`。
- unfilled plan/order 保留在 evidence denominator；fill/fee/PnL 只来自 canonical fill/settlement。
- 公共报告默认读取 prediction table、`fact_signal_candidates`、canonical plans/orders 和 `fact_trades`。
- 先临时 DB，再经批准执行 production 增量 materialization；不因迁移无条件全量重建。

完成标准：raw/canonical order 数一致、无缺失 execution ID、无重复 fill、coverage gate 通过；signal/plan/order/fill/PnL 与 Phase 0 rolling ledger 的差异有逐条清单。

### Phase 7：关闭 active 旁路并保留 legacy 资产

最终扫描与验收：

- active weather runner 不直接 import `ClobClient`，只有共享 venue adapter 可接触它。
- active runner 不直接调用旧 `weather_order_executor`；basket/FOK/true-MM 可保留独立 orchestration，但共用 venue/risk/journal/canonical contract。
- 新城市不自建 collector、replay clock、order/fill/PnL 链。
- 所有城市输出标准 candidate/intent；replay/shadow/paper/live 共用 model/plugin/policy contract。
- 旧 runtime、raw、fixture 和兼容 reader 标为 `legacy_adapter` / `dormant` / read-only rollback，保留不删；不再是 active authority。

完成标准：仓库和 production manifest 的 active-path 扫描均无未登记旁路；三城 migration report、schema、contract tests、rolling-baseline impact report 同步完成，之后才把本文件状态升级为 `implemented`。

## 9. 全框架验收标准

- 至少三类城市插件通过：纯天气或 weather-first、market-aware、source-cross/one-sided。
- producer/consumer 的 checkout/module/config/artifact hash 与 schema fingerprint 可在每个 checkpoint/evaluation 追溯；不兼容时启动失败为 `contract_incompatible`。
- canonical build/DB identity 与 observed-at 固定在 replay/report manifest；运行中 refresh 不得静默改变同一次评测分母。
- 每个采集 interval 都来自 profile；仓库扫描无城市脚本私自硬编码第二套调度主循环。
- point、interval summary、revision/late-backfill 和 native unit/precision 都有 typed contract；同名 schema 不允许 shape 漂移。
- `target_date` 不用于猜物理 shard；本地日/UTC 日切换和等待首报 fixture 不产生 error storm。
- 同一 raw + artifact + config 的重复 replay 输出一致。
- live harness 与 replay 使用同一 plugin/model/policy 代码，只有 clock/input provider 不同。
- `feature_book_snapshot_id` 与 `execution_book_snapshot_id` 可独立追溯。
- source/official/expression anchor 与各自 evidence time 可追溯，所需 expression capture 无隐式窗口缺口。
- 缺失、stale、one-sided 状态保留在固定 coverage 分母并有结构化 blocker。
- 所有候选进入 `fact_signal_candidates`；执行只接受 `TradeIntent`。
- 城市插件无网络轮询、order client、私有 fill/PnL 或 settlement 实现。
- 新城市只需新增 profile、adapter/plugin、model artifact 和 fixtures，不复制 runtime。
- 文档、schema、contract tests 和三城 migration report 同步完成后，才可把本文件从 `approved migration roadmap / partial implementation` 升级为 `implemented`。

## 10. 新城市接入工作单

以后任何对话新增城市或重构城市模型，默认按以下顺序：

1. 读本文件、`WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md` 和目标 source/settlement contract。
2. 声明 target ontology、PIT 时钟、point/interval/revision payload、source profile、cadence、native lattice、三类 bracket anchor 和盘口在模型中的角色。
3. 先接共享 capture/checkpoint/replay；发现共性缺口时扩展公共 contract 并补三城 regression，不另写私有链。
4. 城市内部自由实现 feature/model/policy，但输出标准 `ModelOutput` 与完整 `SignalCandidate`。
5. 用 repo fixture + deployed sample 做 schema handshake 和 golden replay parity，再跑 OOF/frozen-forward 与同分母 market baseline。
6. 需要执行时只输出 `TradeIntent`；是否 shadow/live 另走部署流程。

对未来 Codex 对话的最短约束是：

> 城市模型内部可独立，盘口也可进入模型；但采集 profile、事件/四时钟、PIT checkpoint、replay、候选血缘、`TradeIntent` 和执行链必须复用 `WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md`。不要为新城市另建 collector、回放时钟、order/fill/PnL 链；共性缺口扩展公共框架并补跨城 parity tests。
