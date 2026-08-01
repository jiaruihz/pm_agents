# 跨城市日内温度模型 Runtime：总体设计与迁移方案

Status: design-draft
Updated: 2026-08-01
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

## 8. 迁移方案

### Phase 0：deployed contract census

Status: **completed 2026-08-01; Phase A implementation gate remains closed until the immediate P0 root-fixes below pass deployed-fixture replay.** 证据与机器可读 census 见 [Phase 0 census](analysis/2026-08/2026-08-01-city-intraday-phase0-census-v1.md)；9 个 golden deployed samples 位于 `tests/fixtures/weather_city_intraday_phase0/`。

- 动态盘点实际 producer/consumer checkout、loaded SHA、process args、config、schema fingerprint 和 raw ownership。
- 从东京、赫尔辛基、阿姆斯特丹当前 runtime 各冻结 point/interval/revision、cross-day、one-sided、anchor mismatch 样本。
- 对比 repo tests 所用代码与 running process 所载代码；同名 schema shape 不同必须 bump version 或提供显式 migration。

完成标准：每条运行链能回答“哪个进程、哪份代码/配置、写哪种 row、由谁消费”；develop fixture 与 deployed sample 都通过 contract validator。发现 drift 只记录并进入后续 git-first migration，本阶段不直接改生产。

Phase 0 实际结论：Helsinki/Tokyo 的 producer、book producer 与 model consumer 已定位；Amsterdam 当前无 active KNMI producer 或同级 model consumer。fixtures/validator 全部通过，所以 census 完成。one-sided runtime 行为已在 cutoff 前改为结构化 `not_scorable`，但旧 614 个 exception polls / 68 个 legacy evaluations 与 10 个 current rows 仍共用 v1 journal；因此 runtime identity、schema migration、跨日 locator 三项仍须在 Phase A 公共抽象前先修。multi-anchor 与 typed revision 是 Phase A contract 本体，FMI/JMA canonical information-event lineage 在 Phase A→B 补齐。

2026-08-01 root-fix status：consumer/runtime 的 v2 schema、显式 v1 migration、loaded module/config/artifact identity、InputCatalog cross-day locator 与 Tokyo/Helsinki one-sided/expected-blocker contract 已在 develop 实现并通过 current-raw 临时 output smoke。随后 production-only producer entrypoint/fast-lane contract 已收回 Git，producer payload/row/notification/state identity 与 consumer fail-closed handshake 已实现；Tokyo source+official ladder union 及 official-expression selection 也已完成。39 个旧 mismatch polls 中 17 可恢复 scored、13 恢复为 one-sided not_scorable、9 因历史未采到相差两档 expression 保留 coverage gap，详见 [root-fix report](analysis/2026-08/2026-08-01-city-intraday-contract-root-fix-v1.md)。生产仍跑旧 checkout/config，production cutover 仍是 gate，不能把 develop 完成写成 deployed 完成。

### Phase A：冻结 contract 与 golden fixtures

- 以东京、赫尔辛基、阿姆斯特丹各取一段现有 raw，建立 event/checkpoint/candidate golden fixture。
- 定义 typed payload/revision、InputCatalog、multi-anchor、schema fingerprint、ID 和 blocker taxonomy；先做 adapter，不重写模型。
- 为 live/replay parity、one-sided book、revision、跨日分区、anchor lead 和四时钟写 contract tests。

完成标准：三城现有逻辑可通过 adapter 在 fixture 上复现，point/interval/revision 差异逐条解释；不涉及生产切换。

### Phase B：共享 capture profile 与 checkpoint/replay harness

- 复用 `weather_data_feed` source registry 和现有 collector，只把 cadence、active window、burst 规则移入统一 profile。
- 统一事件 envelope、event-store fold、InputCatalog、checkpoint builder、virtual clock 和 telemetry；adapter 不再直接扫 mutable `latest.json` 或按 target_date 猜路径。
- 禁止启动第二套 collector 与旧链并行抢同一 raw ownership。

完成标准：相同 raw 在重复 replay 中产生完全一致的 checkpoint IDs 和 coverage 状态。

### Phase C：接入东京与赫尔辛基

- 东京作为“market-offset 可进模型”的验证城市。
- 赫尔辛基作为“纯天气 vs market-offset 稳定性比较”的验证城市。
- 东京必须先解决 source/official/expression multi-anchor 和 ladder union；赫尔辛基必须把 one-sided/等待首报变为结构化状态。
- 只包 adapter 和接口，不趁迁移重训或调阈值；先做旧/新同输入 parity。

完成标准：概率、候选、blocker 和盘口 snapshot lineage 与原实现逐行对账。

### Phase D：接入阿姆斯特丹

- 先恢复并验证 KNMI collector ownership/freshness，再把 interval summary、revision、source-cross、热/冷 cadence、ask-only/one-sided 作为第三类插件验证。
- 保留它尚未成熟为完整概率模型的事实，不为接口整齐伪造概率。

完成标准：initial/revision/late-backfill replay 确定；缺 midpoint 不丢 checkpoint；physical-path output 不越级生成 intent；quote policy 可在相同 replay 上确定性复现。

### Phase E：接 canonical 候选和 settlement

- adapter 输出写入统一 `fact_signal_candidates` 血缘。
- settlement、label、market/fill coverage 走既有 canonical migration，不建城市私表。
- 对候选数、未选中、不可执行、fill 与 settlement 做端到端对账。

完成标准：research/evidence 两个漏斗都能由 canonical rows 重建。

### Phase F：zero-notional forward

- 新旧路径并行读取同一已授权 feed，旧路径不下单，新路径只产 zero-notional intent。
- 逐日比较 freshness、checkpoint、概率、candidate、intent 和资源占用。
- 冻结一段 forward，不能在对比期边看结果边改模型。

完成标准：达到预注册的 parity/稳定性门，且无数据 ownership、重启去重或延迟回退。

### Phase G：按实例迁移执行

只有用户明确要求变更生产行为时，才按 `weather-strategy-deploy` 做 git-first、单实例迁移和 process/raw/API/exchange 验证。每次只迁一个实例，保留可审计 rollback；不得由本设计直接批量切 live。

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
- 文档、schema、contract tests 和三城 migration report 同步完成后，才可把本文件从 `design-draft` 升级。

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
