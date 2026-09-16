# Pre-Cross Path Hazard + Source-Event Full-Ladder Residual 研究计划 v1

Status: `preregistered two-head research work order / Helsinki-FMI phase-1 / all-observation denominator / zero-notional / no live change`

## 1. 先回答：上一轮对 cross-NO 有什么帮助

上一轮不是一个新策略，但它给 `cross-NO` 留下了六个可直接复用的结论。

1. **first-seen clock 已经钉死。** 决策时间必须是系统第一次看到 source observation 的
   `source_detect_ts_utc`，不是 observation timestamp、collector episode 时间或后来 METAR 时间。
2. **source→routine METAR→WU settlement 三个时钟已经分开。** 下一份 settlement-facing METAR 只能作为
   post-event validation label；它不是本研究的入场条件。等它出现后 old bracket 通常已在物理上失效，也往往已经
   没有可买价格。真正要研究的是 routine METAR 之前，市场在哪一段快源升温路径上先完成重定价。
3. **market 必须是基准而不是障碍。** Helsinki 2026-07-17 的 old `24°C NO`：expanding source P0 为
   `0.9655`，NO market mid 只有 `0.295`、ask `0.44`，最终 old bracket 没有失效。市场低价不是自动的便宜货，
   可能是在表达 source-basis / terminal reversal 风险。
4. **上一轮只从现有 trigger 已成立的时点开始看，不能回答提前入场问题。** Helsinki 当前
   `persistent_candidate_margin_v5` 先要求至少两次 distinct observation `>= old bracket + 0.5`，且最新一份
   `>= old bracket + 0.7`，才买 previous-NO。上一轮 16 个同刻 rows 的 P0 logloss `0.2552`、market
   `0.1194`，只说明在这个确认点上 source-only 概率没有胜过市场；它没有观察 `x.5 → x.7` 甚至更早的路径，
   因而不能否定 CrossNO，也不能解释盘口何时已无价。
5. **现有 x.5/x.7 策略是正式 benchmark。** 新研究不再把 next METAR 当成替代策略，而是检验：
   市场是否在 x.7 前已经根据 slope、cadence、remaining heat 和连续新高概率定价；如果是，能否在不使用未来
   x.7 信息的情况下更早得到校准后的正净 EV。
6. **collector 缺口已定位。** 旧 observer 只保存 cross episode 和 top-of-book；39 个 Helsinki events 只有
   16 个 t0 direct quotes，没有完整 10-share VWAP。下一轮必须收集所有报文和 full depth。

因此 cross-NO 的后续定位是：

- 保留现有 source profile、first-seen、settlement、Atlanta negative-control 和 order/fill lineage；
- 固定现有 `两次 >=x.5 且最新 >=x.7 → BUY previous NO` 为 benchmark arm，不修改其 trigger；
- Head A 在 x.7 之前预测 old bracket 的失效概率与未来 5/10/20 分钟达到 x.7 的 hazard，研究更早的
  previous-NO entry；
- Head B 在每次 observation 后更新完整 Tmax distribution，previous-NO 只是全 ladder expression 之一；
- 不由本计划修改或停止任何现有 runner。当前 live/probe 状态另走 deploy/lineage 流程动态核对。

## 2. 两个并行研究目标

### Head A：Pre-cross path hazard / 盘口何时先定价

> 在现有 x.7 trigger 之前的每一份 FMI first-seen 上，估计
> `P(final leaves old bracket)` 与 `P(reach x.7 within 5/10/20m)`，并定位 old-NO 从可买到无价的实际时点；
> 比较“首次校准后净 EV>0 的提前入场”与现有 x.5/x.7 benchmark。

未来是否真的到 x.7 只能作为 hazard label，绝不能回填筛选早期 entry。Head A 的核心不是“放宽成 x.5 下单”，
而是从连续路径估计 old bracket invalidation probability，再与同刻真实可执行盘口比较。

### Head B：每次实时变化后的完整概率分布

> 对 Helsinki 每一份 FMI first-seen，估计最终最高温 exact-bracket 的完整 PIT 概率分布，检验
> `P(final bracket | market + source update + path state)` 是否在同 rows proper score 上胜过报文到达前的
> market distribution；若胜，再从整条 YES/NO ladder 中选择净 EV 最大的可执行表达。

两个 head 共用同一个 all-observation parent denominator。cross、no-cross、flat repeat、near-boundary 和 source
reversal 全部进入；Head A 研究 old bracket 的提前失效与 book-lock，Head B 研究整条分布如何移动。

## 3. Frozen state grain 与分母

### State grain

```text
(city, target_date, source,
 source_observation_ts_utc,
 source_first_seen_ts_utc,
 prior_source_observation_hash,
 prior_official_state_hash,
 pre_event_full_ladder_snapshot_id)
```

同一 source observation 的重复 polling 只保留 first-seen。一份报文对应一条 probability state，但在 expression
层展开为全部可交易 bracket×side rows。

### Probability universe

- Helsinki 当地日所有 distinct FMI first-seen observations；
- 不按 cross/no-cross、价格、未来结算、未来 persistence、是否被策略选中或是否成交筛分；
- market 尚未关闭，first-seen 时 final settlement 未知；
- source、book、path 或 settlement 缺失分别进入 evidence coverage gap，不属于策略 filter；
- cross episode 只是预注册解释切片之一，不是父分母。

### Expression universe

Head A 对 x.7 尚未触发时的 old bracket 计算：

```text
old-NO edge = P(final leaves old bracket) - old-NO ask_VWAP - taker_fee
```

Head B 对当时存在的每个 exact/range bracket `k` 同时计算：

```text
YES edge(k) = P(final=k)     - YES_ask_VWAP(k) - taker_fee
NO  edge(k) = 1-P(final=k)   - NO_ask_VWAP(k)  - taker_fee
```

两个 head 分别打分，但组合回放每个 source event 最多选择一个 expression；primary portfolio policy 每个
city-date 最多新增一个持仓，防止同一路径的多次报文被当作独立资金机会。所有未选 expression 仍保存 score。

## 4. Labels：最终概率与盘口反应分开

### Primary probability label

```text
y_exact = final Polymarket/WU winning bracket
```

由此派生每个 bracket 的 `YES/NO` binary payout。已打印某档只表示 touch，不表示该 exact YES 最终获胜。

### Repricing labels

对每个 bracket×side 保存 source first-seen 后：

```text
delta_mid_15s / 30s / 60s / 120s / 300s
delta_executable_ask_15s / 30s / 60s / 120s / 300s
```

probability head 预测最终 settlement；repricing head 预测短期 market update。两者分别打分，不能用随后价格替代
最终 label，也不能用最终结果证明存在低延迟交易窗。

Head A 额外保存以下 book-lock endpoint；它们只用于回答“何时被定价”，不是 eligibility gate：

```text
first old-NO ask >= 0.90 / 0.95 / 0.99
first old-NO 5-share VWAP unavailable
first old-NO 10-share VWAP unavailable
first old-NO ask unavailable
minutes/source-observations from each endpoint to x.5 and x.7 trigger
```

### Source-basis diagnostics

- next routine official 是否确认；
- final source-to-WU lattice basis；
- terminal false / source reversal；
- Atlanta 2026-07-17 固定 negative control；
- correct、false 各自的 t0 executable 和真实 fill 分母。

## 5. Collector 与数据契约

第一阶段必须先证明当前 raw 能覆盖所有 FMI observations；现有 cross-only observer 不能作为完整父分母。

每个 distinct source first-seen 保存：

```text
source raw value + native unit + observation_ts + first_seen_ts + payload hash
prior FMI state + prior routine official state
distance to old-bracket +0.5 and +0.7 thresholds
whether current production x.5/x.7 trigger is already satisfied
t-60s last known full ladder
t0 first fresh full ladder after first_seen
t+15s / +30s / +60s / +120s / +300s full ladder
final settlement
```

每个 full-ladder checkpoint 必须保存真实 YES/NO bid、ask、至少覆盖 10 shares 的 depth、fetch timestamp、HTTP/fetch
status、condition/token mapping 和 sibling ladder completeness。禁止 `NO=1-YES ask`，禁止用 later quote 冒充 t0。

数据落点：source observation/state 进 canonical source-event/feature layer；展开的 score/selected/blocked opportunity
挂入 `fact_signal_candidates` grain，不创建只存 selected winners 的平行事实表。

## 6. 连续特征

### Market baseline

- 报文前完整 YES ladder mid，约束投影到概率 simplex；
- raw normalized-mid 作为 sensitivity；
- spread、5/10-share VWAP、depth imbalance、quote age；
- source 前 60 秒的价格路径，防止把已经发生的重定价归因给 source。

### Source innovation

- 新报文相对上一份 FMI 的温度 delta、cadence、revision；
- 相对 old bracket 的连续距离，以及距 x.5/x.7 的距离；x.5/x.7 只作为已存在的 benchmark landmarks；
- 相对 routine official running max 的 delta；
- native settlement lattice distance，不把小数摄氏温度直接当 exact bracket；
- strict-new-high amount、flat repeat count、near-boundary margin；
- source observation age、first-seen lag、source-to-settlement historical basis uncertainty。

### Path / physical context

- forecast peak clock、remaining heating window；
- strict-new-high age、1h/3h slope、acceleration、plateau/pullback/fade；
- forecast ceiling margin、overshoot hazard；
- dewpoint depression、wind/mixing、cloud/rain transition。

所有机制字段先作为连续特征与 ablation；不从单个失败案例生成 AND gate。

## 7. 两个 head 的模型打擂

### Head A：old-bracket invalidation 与 x.7 arrival hazard

所有 observation 都参与 `P(final leaves old bracket)`；只有 x.7 尚未触发的 risk set 参与离散时间
`P(reach x.7 within 5/10/20m)`：

1. `A0 market_old_no`：同刻 old-NO market-implied probability；
2. `A1 market_calibrated`：expanding market calibration；
3. `A2 market + source_path`：连续 distance、slope、cadence、new-high age；
4. `A3 market + source_path + remaining_heat`：加入 peak clock、云雨风湿度与 ceiling margin。

Primary challenger 是 A2。x.7 hazard 用于解释市场为什么提前锁价，也可作为 A2 的 PIT 连续特征；不能用“后来
确实到 x.7”选择早期 rows。

### Head B：完整 exact-bracket distribution

按固定顺序，所有模型使用相同 rows、labels 和 market snapshot：

1. `M0 market_raw`：报文前 market-implied full distribution；
2. `M1 market_calibrated`：只做 expanding market calibration；
3. `M2 market + source_innovation`：检验报文本身的增量信息；
4. `M3 market + source + compact_path`：加入剩余加热与路径；
5. `M4 shallow nonlinear challenger`：只在 train dates 内选择正则/深度。

Primary challenger 是 `M2`。如果 source innovation 连 M1 都不能稳定打败，不允许靠 M3/M4 的复杂度宣称 source
alpha。两个 head 都按 target_date expanding；同日所有 observations 整块进入 test，防止同日路径泄漏。

## 8. 入场与动态 expression policy

### Head A 提前入场

1. 只在现有 x.7 trigger 尚未成立的 observation 上评估；
2. A2 输出 `P(final leaves old bracket)`，计算 old-NO 5-share taker net edge；
3. primary entry 是该 city-date/old-bracket 首个 `edge>0` 的 first-seen state，stress 为 `edge>1c`；
4. 与现有 x.5/x.7 trigger 在相同 city-date、相同 old bracket、相同 depth/fee 口径做 paired replay；
5. 单独报告提前了多久、买价改善多少、增加了多少 terminal-false/未 cross 损失；
6. 不能用未来 x.7、下一份 METAR 或最终 settlement 决定是否入场。

### Head B 全 ladder expression

模型先输出完整 `P(final=k)`，再单独应用执行层：

1. 计算所有 bracket×YES/NO 的 5-share taker net edge；
2. 选最大 edge 的一个 expression；
3. primary research threshold 为 `edge>0`，预注册 stress 为 `edge>1c`；价格不作 eligibility hard gate；
4. top depth 不足、book stale 或 token 缺失只记 execution blocked；
5. 同 city-date 已有 primary position 时，后续 source events只继续 score/markout，不新增仓；
6. hold-to-settlement 为 primary；15–300s markout 只衡量信息速度，不事后挑 exit。

可能的结果包括 current YES、current NO、next YES/NO、previous YES/NO 或 no-trade。`previous NO` 必须与所有
expression 竞争，因此不会再退化为 cross-NO。

## 9. Baselines 与负面对照

- 同 rows `M0 market_raw` proper score；
- 固定现有 `两次 >=x.5 且最新 >=x.7 -> previous NO` arm；
- 简单 `首次 >=x.5 -> previous NO` 仅作 timing baseline，不作为候选 live rule；
- market-only 最大 EV arm；
- source timestamp 在同 city-date 内打乱的 placebo；
- source value/delta permutation；
- 报文前伪事件 checkpoint；
- Atlanta terminal-false casebook，不与 Helsinki pool。

Head A 若不能在同 rows 的概率质量上胜 market/A1，即使比 x.7 买得便宜也不算 alpha；Head B 若只能赢 CrossNO、
却不能赢 market，不算 alpha。若 settlement score 赢但随后盘口不动，记为天气信息能力，不算 stale-book 交易能力。

## 10. 双漏斗

### Signal funnel

```text
all distinct FMI first-seen observations
→ PIT state complete
→ Head A pre-x.7 risk state → old-NO invalidation score → first positive-EV early entry
→ Head B full-distribution score → all bracket×side net edges → max-EV expression
→ first city-date policy selection
```

单位分别报告 observation、city-day、expression 和 selected opportunity。

### Evidence funnel

```text
PIT source payload
→ pre-event/t0 full ladder
→ 5-share depth
→ 10-share depth
→ settlement
→ shadow plan
→ actual order/fill
```

缺失只记 coverage gap；selected、blocked、no-trade 都写入机会层。

## 11. 验证与晋升门

### Historical discovery

- 先做 all-observation/source-book parity，不沿用 cross-only 39-event 分母；
- expanding OOF，target-date block bootstrap；
- train 内完成 imputation、模型与正则选择，holdout 不调参数；
- 报告多重检验候选数和前后半窗、leave-one-date-out、去最大两日。

### Gate A：概率 residual

- multiclass logloss、multiclass Brier、ranked probability score；
- `candidate - market` 的 date-block 95% CI 上界 `<0`；
- Head A 的 A2 binary logloss/Brier 必须胜 A1/market，Head B 的 M2 必须至少在 multiclass logloss 与
  ranked probability score 同时通过；
- Head A 的 x.7 5/10/20m hazard 另报 time-dependent Brier/calibration，不替代 final invalidation gate。

### Gate B：source→book timing

- 预注册 30s、120s signed repricing；
- candidate residual 与随后 market change 的 date-block IC/均值 CI 下界 `>0`；
- Head A 的 book-lock survival curve 必须显示模型 residual 在 ask/depth 消失之前可用，而不是只在已无价后准确；
- correct/terminal-false 的 executable 分母分别报告。

### Gate C：执行

- 5-share、10-share真实 ask VWAP和官方 Weather taker fee；
- 额外 +1c latency/friction stress；
- target-date bootstrap ROI CI 下界 `>0`；
- PnL concentration、最大两日剔除、front/back 同号；
- 没有真实 fill 时只能称 counterfactual displayed-book replay。

### Frozen forward

从 artifact freeze 后首个完整 Helsinki local day 起，至少：

- 15 个独立 settled target dates；
- 300 个全分母 scoreable FMI observations；
- 30 个有 t0 full-depth 的 policy-selected opportunities；
- source/book/settlement coverage 分母完整；
- frozen M2/M3 与 expression policy 不调参。

三门全过只能升 `shadow_candidate`。复制城市前不讨论 tiny-live。

## 12. 执行顺序与交付物

### 2026-07-25 初始历史回放（Helsinki/FMI）

已完成 2026-07-15..23 的 all-observation atlas：857 个 distinct FMI first-seen / 9 个 settled target dates，
其中 445 个 state 有 first-seen 前不超过 25 分钟的 full-ladder snapshot、347 个同时有 old-NO mid。

- **Head A 的最小连续 baseline 失败。** 仅用 source 相对 old bracket 的距离 bucket 做 expanding 概率，logloss
  `0.5825`，明显差于同刻 market `0.1986`；source-minus-market `+0.3838`，date-block CI
  `[+0.1931,+0.5471]`。这不是正式 A2（未含 slope/remaining heat），但足以禁止把 x.5 放宽直接包装成策略。
- **盘口锁价现象存在、但时间分辨率仍不足。** 首个 x.5 landmark 44 个可对齐 state 中，13 个 ask 已 >=0.90、
  12 个 >=0.95、9 个 >=0.99，29 个没有 5-share VWAP；x.7 对齐 state 为 21 个，其中 5/5/3 与 16 个。
  这些都是 0--25 分钟 archive bound，不是精确的 repricing timestamp。
- **pre-x.7 replay 不成立。** 限制为已 >=x.5 但尚未现有两次+x.7 trigger 的 first positive edge 后，只有
  4 个 displayed-book counterfactual（9 日窗口、无真实 fill）；不能和 x.7 的 2 个 executable comparator
  做有效 paired conclusion，更不能据点估更改 live。
- **Head B 已形成可建模 parent state，尚未拟合。** 440 个 state 有 normalized market full distribution，
  204 个 sibling ladder 完整；但仅 9 个 settled dates，且 source first-seen 的 physical feature frame 未冻结，
  不训练 M2/M3、不报告 alpha。

产物：[all-observation atlas](2026-07-25-helsinki-pre-cross-path-atlas-v1.md) ·
[x.5/x.7 single-margin benchmark](2026-07-25-helsinki-single-margin-thresholds-v2.md)。

1. **Collector parity**：重建 Helsinki 所有 FMI first-seen 与完整 ladder，确认不再是 cross-only 分母；
2. **Head A timing atlas**：按 city-date 画 source path、x.5/x.7 landmarks、old-NO ask/depth 与 book-lock 时点。
   初版已完成；下一步必须提高 active-ladder book cadence，缩小当前 0--25 分钟 interval censoring；
3. **Head A OOF**：A0–A3、x.7 hazard、与现有 x.5/x.7 benchmark 的 paired fee/depth replay；
4. **Head B OOF**：M0–M4 full-distribution proper score 与全 ladder expression replay；
5. **Joint policy**：同一 observation/city-date 的资金冲突、one-position policy 与 concentration；
6. **Frozen forward**：冻结 feature/model/expression 后才开始累计，不用历史结果反复改 trigger。

交付必须同时包含：固定分母 row manifest、signal/evidence funnel、概率 score、book-lock timing、5/10-share
fee-adjusted replay、terminal-false 清单、frozen artifact hash。任何一项缺失都只能标 `inconclusive/coverage gap`。

## 13. 城市复制与停止条件

| 结果 | 状态 | 动作 |
|---|---|---|
| M2 不胜 market | `source_increment_not_proven` | FMI 只保留 feature/diagnostic，不加复杂 filter |
| 概率胜、repricing 不胜 | `weather_information_not_tradeable` | 保留概率更新，不做 latency strategy |
| repricing 胜、fee replay 不胜 | `edge_consumed_by_execution` | 研究 collector/execution，不下单 |
| Helsinki historical + frozen forward 全过 | `shadow_candidate` | 固定模型，只适配 lattice，复制 SanFrancisco/Chicago |
| Helsinki + 至少一个复制城市全过 | `promotion_candidate` | 另行请求 5-share tiny-live；必须走 deploy |

SanFrancisco/Chicago 复制时不得重选特征、threshold 或表达，只允许 source profile、timezone 和 native lattice adapter
变化。Atlanta 永远只作 negative control。

## 13. 执行阶段与产物

### Phase 0：数据可行性

- 审计当前 high-frequency raw 是否含全部 FMI distinct observations；
- 审计 full-ladder 5/10-share depth、t0 latency 与 sibling completeness；
- 输出 signal/evidence funnel，决定历史能走多远。

### Phase 1：历史 PIT 模型

- materialize all-observation state rows；
- 跑 M0–M4 expanding OOF、ablation、placebo；
- 输出完整概率 scorecard，不先做 selected ROI。

### Phase 2：repricing 与执行

- 跑 15/30/60/120/300s vector repricing；
- 动态全 ladder expression selection；
- 5/10-share fee replay与 capacity stress。

### Phase 3：frozen zero-notional forward

- 固化 model artifact、feature schema、policy config 和 repro key；
- 全 universe 写 score/selected/blocked/no-trade；
- 达到门槛后一次性裁决，不按单日输赢改规则。

预期 durable 产物：

- `scripts/analysis/market_structure_edge/research_source_event_full_ladder_residual_v1.py`
- all-observation PIT state/OOF predictions/repricing/expression/replay artifacts
- canonical source-event opportunity contract
- frozen zero-notional runner contract
- final report、registry 和 docs index 更新

本计划本身不启动 collector、不修改 runner、不改变 city pool 或 live 行为。任何生产 collector 变更先走
`weather-strategy-deploy` 的 git-first 与显式确认流程。
