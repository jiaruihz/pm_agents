---
name: weather-strategy-research
description: 设计和验证新的 weather 策略机制、物理特征、概率/分布模型、source-event/快源、market residual、exact-bracket 表达和 PIT 回放。用于“这个思路能不能做”、新策略、模型改进、特征研究、快源领先、Tmax/remaining-heat/reheat/overshoot、shadow 方案。要求连续信号、固定分母、signal/evidence 双漏斗、同分母 market baseline、官方 fee 与 frozen forward；禁止从稀疏事件或事后切片直接造 live gate。
---

# Weather strategy research

研究目标是找到 fee-adjusted、PIT、可执行、能在 frozen forward 重复的 market residual。

## 先定位血缘

读 `AGENTS.md`、`WEATHER_ANALYSIS_CONTRACT.md`、`WEATHER_STRATEGY_QUANT_DESIGN.md`、`WEATHER_STRATEGY_REGISTRY.md` 和目标 family living doc。

跨城市分钟/小时间隔温度模型还必须读 `WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md` 与
`WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md`，先声明目标、PIT 时钟、source/cadence、settlement lattice 和盘口在模型中的角色。

新产物挂到：

```text
source/raw -> EventEnvelope -> DecisionContext -> ModelOutput
           -> SignalCandidate -> TradeIntent -> shared execution handoff
           -> plan -> order -> fill -> settlement
```

共享数据逻辑进 `weather_data_feed/`；共享机制特征按 feature-layer contract；策略私有 selector 不塞回数据层。

## 跨城市日内模型的 runtime 边界

- 城市 feature/model/policy 可独立；盘口可以不使用，也可以作为 prior、offset、联合或 microstructure 特征。
- 采集 profile、事件/four clocks、PIT checkpoint、deterministic replay、`SignalCandidate`、`TradeIntent` 与
  `plan -> order -> fill -> settlement` 必须复用公共框架。
- 模型盘口输入与执行报价分别记录 `feature_book_snapshot_id`、`execution_book_snapshot_id` 和对应 clock。
- candidate 明确记录 `candidate_grain_version`；replay/report 固定 canonical DB identity、`build_id`
  与 `observed_at_utc`，运行中 refresh 不得静默改变同一次评测分母。
- 正式接入前先对 deployed producer/consumer 做 contract census；repo fixture 与 running sample 必须同时通过 schema fingerprint/parity。
- source payload 必须区分 point、measurement interval、revision/late-backfill；`target_date` 不得用于猜物理 shard。
- source、official/settlement、market expression anchor 分开留痕，book `relative_offset` 必须声明相对哪个 anchor。
- 新城市不得另建 collector、回放时钟、order client、fill/PnL 或 settlement 链；公共能力不足时扩展共享 contract，
  并为既有东京、赫尔辛基、阿姆斯特丹 fixtures 补 parity regression。
- 缺失、stale、one-sided book 返回结构化 scorable/blocker 状态并保留 coverage 分母，禁止静默丢行或 fallback。

研究若读取 canonical facts/features，先运行 `.venv/bin/python scripts/ops/weather_production_ctl.py health` 与
`.venv/bin/python scripts/ops/weather_production_manifest.py --strict`；
DB split 或存在非 canonical consumer 时只允许继续 raw coverage/机制诊断，不得产出 canonical 同分母结论。
开始读取时保存 build manifest；若 build 变化，重启该次查询或按 build 分层。

## 研究问题模板

先写一句 target：

```text
估计 P(outcome | PIT weather/path/source state)，并检验它相对同一时点 market probability 的 residual，
而不是预测天气事件本身或寻找最高 ROI 切片。
```

锁定：物理目标、state grain、label、decision timestamp、universe、market expression、executable cost、主 metric、forward 窗。

## Exact bracket 语义

- `X YES` 只在最终最高温正好为 X 时赢。
- 已打印 X 不代表 X YES 安全；overshoot 到 X+1 会使 X YES 输。
- 研究 current/previous/d1/d2 时同时写清 touch、break、stop-exact、final-exact，不能混 label。

## 连续信号优先

先构造可校准概率或 residual，再用切片解释：

- forecast peak clock / remaining heating window
- fresh runway / plateau / pullback / fade
- forecast ceiling margin / overshoot hazard
- source age / cadence / source-to-settlement basis
- cloud/rain/wind/humidity 等机制变量

不要从坏例子出发连续追加 hard filters。hard filter 只用于机制边界、PIT/数据有效性、执行质量或资金安全。

## 双漏斗

必须分别列：

```text
signal funnel: raw universe -> mechanism candidate -> first event/city-day signal -> policy selection
evidence funnel: PIT source -> PIT book -> settlement -> executable expression -> actual fill
```

每层标 unit 和独立日期。archive 晚起、盘口缺失、结算缺失只算 coverage gap；不能包装成精筛策略。

## PIT 与 source 研究

- forecast 用 issue/run/first-seen/hash/age lineage，不用粗 local-hour 标签冒充信息状态。
- source event grain 默认 first-seen `(city, local_date, source, observation_ts, prior official state)`。
- 快源温度不是 settlement truth；校准 source→official/settlement 的 basis、boundary、age、path state。
- 机场快源必须用 Atlanta `2026-07-17` terminal false cross 作 negative control：OMO/MADISHF `91.4F`、
  direct MADIS `temperatureQCR=0`，但 routine METAR/WU final `89F`、旧 `88-89` bracket 未离开。
  相关报告必须单列 terminal false、同 timestamp source→routine→WU basis，以及 correct/false 各自的
  fresh executable/fill 分母；persistent 命中率或 QC pass 不能替代这三项。
- 后到的 METAR/WU 只能作 label，不能回填成事前特征。
- 多源 fallback 必须显式；模型/source 缺失不得静默替换。

## Baseline 与指标

概率层先比较同 rows 的 market：logloss、Brier、calibration、AUC/rank。交易层再用 fresh executable side price、depth、官方 Weather fee 与声明的 friction：

```text
edge = p_win - executable_cost
```

maker 假设必须建 fill/queue/adverse selection，不能把 future touch 当成交。gross ROI 不作主结论。

## 验证顺序

1. 宽分母 sanity check，防止事件故事掩盖 base rate。
2. 同分母 A/B：固定 rows、labels、quotes，只改一个因素。
3. expanding/OOF probability calibration。
4. train 内选模型/参数；frozen holdout/forward 只复核。
5. target-date block bootstrap、日期数和多重检验。
6. shadow/collector 记录完整 score，而不是只记录 selected winners。
7. 有真实 fills 后转 `weather-strategy-performance` 与 execution audit。

当前项目不把“collector 在跑”“某 4–7 个日期 ROI 正”“物理逻辑合理”当 live 证据。至少满足 contract 的日期/样本、absolute 与 baseline CI、PIT/fill 可执行性后，才讨论 tiny-live；扩大 size 需要更强 forward。

## 产物

- 可复跑脚本放对应 `scripts/analysis/<family>/`，使用 `.venv/bin/python`。
- 同一机制的 city/date/window/parameter 变化必须走现有 runner 的 config/run manifest；禁止每天、每城复制一个
  `research_*_vN.py`。新增入口前先运行 `check_weather_docs.py` 的 research debt audit；只有算法或输入合同真正变化
  才允许新增，并把跨城市/跨版本重复函数提到共享 adapter、`weather_model_evaluation` 或 family common module。
- 原始机会/特征若会复用，进入 canonical opportunity/feature layer，不另建平行事实表。
- 大型 CSV/JSONL/model/image 写 `production.yaml.research_artifact_root`；历史脚本需要已归档输入时，先用
  `weather_research_artifact_ctl.py dependencies` 定位，再用 `restore-dependencies --script ... --apply`
  按脚本最小恢复，不把整库复制回 `docs/`。
- 日期报告写 `docs/analysis/YYYY-MM/`，只作 snapshot。实验结束时必须同时回写
  `WEATHER_DOCS_INDEX.md` 的所属家族 living doc 与 `WEATHER_STRATEGY_REGISTRY.md`：写清耐久结论、
  被取代的旧判断、证据边界和当前动作。只新增日期报告、不更新家族入口，任务不算完成。
- 新版本若只是同一模型的 feature/参数/训练窗 experiment，保留稳定 model identity，用 `run_id/artifact_id`
  区分；不要继续制造 `v9/v10/v11` 平行“当前模型”。
- 结论保留 `inconclusive` / `shadow_candidate` / `rejected_for_expression` 边界；方向暂停不删除资产。

最终先给动作：继续 collector、启动 zero-notional shadow、保持 research、停止某 expression、或不改 live；再给证据和 blocker。
