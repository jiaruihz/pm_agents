# Helsinki Remaining-Heat Distribution + Expression Router 研究计划 v1

Status: `preregistered research work order / Helsinki-only / zero-notional / no live change`

## 1. 结论与动作

第一阶段只做 Helsinki/FMI，将“每个 10 分钟观测后今天还会不会升档”定义成一个连续概率问题，并由同一个最终温度分布比较四种 Polymarket 表达。

```text
FMI first-seen checkpoint
→ P(final settlement increment = 0 / 1 / 2 / 3+)
→ current X NO / upper-YES strip / current X YES / d1 YES
→ 同时点 market residual
→ 5/10-share executable replay
```

Primary expression 固定为 `confirmed-current X NO`；`upper-YES strip` 是同义执行比较，`current X YES` 是同模型的互补表达，`d1 YES` 只作 exact-landing diagnostic。当前只研究和 zero-notional forward，不修改任何 live、city pool、sizing 或 execution policy。

该计划把已有 `source-event stale-book / pre-cross hazard / full-ladder residual` 统一到一个概率底座中；旧报告与数据保留。快源跨档只是 `P_break` 急剧更新的一种状态，不再作为独立的确定性结算规则。

## 2. Target

在 Helsinki 本地日、时刻 `t`：

```text
X_t       = 截至 t 已 first-seen 的 EFHK METAR/WU-facing running-max bracket
M_final   = 当天最终 EFHK METAR/WU settlement maximum
Δ_t       = M_final - X_t
q0/q1/q2/q3+ = P(Δ_t = 0 / 1 / 2 / >=3 | PIT state at t)
P_break_eod  = 1 - q0
```

辅助 horizon：

```text
P_break_30m
P_break_60m
P_break_120m
P_break_eod
```

- physical target：剩余加热能否使 settlement-facing running max 再升至少一个 native 1°C tick。
- exact-bracket semantics：`X YES` 只在 `M_final=X` 时赢；`X NO` 在已确认打印 X 后等价于 `M_final>X`。
- grain：distinct FMI first-seen checkpoint × current official bracket × full-ladder signature。
- universe：全部 scoreable Helsinki FMI first-seen observations；不按 cross、价格、未来 persistence、输赢或 ask availability缩小概率分母。
- decision timestamp：FMI `local_detect_ts_utc`；historical pretrain 只有 observation clock，单独标 provenance。
- label：EFHK METAR local-day final max；WU/Polymarket winner作 settlement audit。
- primary metrics：同 rows Brier/logloss/calibration 相对 market；交易层用 official-fee-adjusted 5-share ROI 与 excess。

## 3. 数据和 PIT 契约

### Historical weather pretrain

- FMI 100968：`2023-07-30..2026-07-29`，1096 个完整本地日、157,781 个 10 分钟 rows。
- EFHK METAR：1096 日、52,592 条。
- WU：可用日 METAR/WU daily max `1092/1092` exact；真实市场 settlement WU/METAR 均 `79/79`。
- 因此 METAR 可作完整历史 label；FMI half-up 只作 predictor，不作 settlement latch。
- 历史 WFS/IEM 没有真实 first-seen，不能用于 latency、repricing 或 executable ROI。

### As-of official state

历史 IEM METAR 只提供 report time，不提供真实 detect time。训练时主口径只允许：

```text
report_time <= checkpoint_time - 10m
```

并预注册 `5m / 10m / 15m` 三档 availability-lag sensitivity；三档只作稳健性检验，不择优选结果。生产 forward 使用真实 `local_detect_ts_utc`。

后到的 METAR、WU 和 settlement 只能构造 label，禁止回填到此前 checkpoint。Forecast 必须带 issue/run/first-seen/hash/age；无法恢复 PIT vintage 的历史 forecast 不进入 v1 primary，只能进入 diagnostic。

## 4. 数据切分

```text
development:      2023-07-30..2024-12-31
expanding OOF:    2025-01-01..2025-12-31
final audit:      2026-01-01..2026-07-29
true forward:     artifact freeze 后首个完整 Helsinki target date 起
```

既有 FMI 30/60/120m 研究已经查看过 2026，因此 2026 只保留为新 label/spec 下的一次性 confirmatory audit，不能反复用来调特征。真正 untouched 证据从本计划 artifact freeze 后开始。

所有 split 按 `target_date`，禁止 row-level random split。每个 target date 总训练权重为 1；bootstrap 也按 target date block。

## 5. 特征 ablation

按固定次序逐组加入；每轮固定 rows、labels、imputation 和 split。某组只有在 2025 expanding OOF 的 Brier/logloss 都改善时才保留。

### A0 Boundary + clock

- official running max / current exact bracket；
- FMI current / FMI running max；
- `pullback_depth = fmi_running_max - fmi_current`；
- current temperature 到下一 settlement boundary 的距离；
- FMI implied lattice 与 official lattice 的 basis；
- local hour、day-of-year、solar elevation、remaining daylight。

不同时保留高度共线的 `temp_c + running_max_c` 来间接表达 pullback；直接使用 pullback 与 boundary distance。

### A1 Path

- 10/20/30/60m delta、robust slope、acceleration；
- warming-run length、strict-high age、plateau duration；
- recent high count、path volatility；
- fresh-runway / plateau / pullback / fade 只作解释切片，不作 hard gate。

### A2 Radiation

- global/diffuse/longwave/reflected radiation；
- 30/60m mean、slope、radiation integral；
- diffuse fraction、sunshine duration、radiation balance proxy。

### A3 Air mass / suppression

- dewpoint depression、RH及趋势；
- wind u/v、speed、gust及趋势；
- cloud cover、precipitation、present weather、visibility；
- pressure tendency。

### A4 Forecast remaining heat

只在 PIT forecast lineage 完整时加入：

- assigned `CITY_MODEL` future maximum 相对下一 boundary 的 margin；
- forecast peak clock；
- future 1/2/3h heating integral；
- future local heat lobe、晚峰/第二峰 margin。

### A5 Source reliability

- FMI first-seen lag、cadence gap、revision；
- source-to-latest-official level/basis；
- single print / repeated distinct observations；
- persistence 作为连续特征，不设“两次确认”资格门；
- terminal-false risk state。

## 6. 模型打擂

### Weather-only pretrain

1. `W0 season×clock empirical prior`；
2. `W1 compact regularized logistic`；
3. `W2 shallow HistGradientBoosting`；
4. `W3 multinomial Δ={0,1,2,3+}`。

1096 个独立日期不足以支持 LSTM/Transformer。序列信息先通过预注册 path/radiation features表达。

### Market residual

在有 PIT book 的同 rows 上比较：

1. `M0 raw market`；
2. `M1 date-OOF calibrated market`；
3. `M2 market logit + compact weather score`；
4. `M3 market + compact PIT feature frame`；
5. `M4 shallow nonlinear challenger`。

概率校准只用 prior-date OOF predictions；默认 Platt/logit calibration。四个 horizon 必须满足：

```text
P30 <= P60 <= P120 <= PEOD
```

主输出同时满足 `q0+q1+q2+q3+=1`。模型/参数只在 development+2025 OOF 选择；2026 不再调参。

## 7. Expression router

同一 checkpoint 保存全部 sibling expression，不只保存 selected：

| Arm | Model probability | Primary role |
|---|---:|---|
| E1 `BUY X NO` | `1-q0` | primary remaining-heat expression |
| E2 `BUY upper YES strip` | `q1+q2+q3+` | 与 E1 同义的执行成本比较 |
| E3 `BUY X YES` | `q0` | peak/fade 后的互补表达 |
| E4 `BUY X+1 YES` | `q1` | exact-one-step diagnostic |

每个 arm 使用真实 5-share、10-share full-ladder ask VWAP 和官方 Weather fee：

```text
edge = p_win - effective_executable_cost
```

Primary policy：

- 每个 `(target_date, confirmed X)` 只保留首次 `fee-adjusted edge > 0` 的 E1/E2 机会；
- E1 与 E2 选较低 executable cost，不按历史赢家选择；
- E3/E4 平行记录，不与 E1/E2 共用 selected ROI 分母；
- `+1c/+2c/+3c` friction 只作预注册 sensitivity，不反向选择最佳 margin；
- v1 只做 taker replay；maker/queue 留到 taker alpha 与 repricing 过门后。

所有 checkpoint 继续记录后续 score；首次入场以后只计算 `would_adjust_target`，不把每个 10 分钟 tick 当新独立交易。

## 8. 实验阶段

### Phase 0 — Label/clock parity

- 生成每个 10 分钟 FMI checkpoint 的 as-of official state；
- 构造 `Δ`、30/60/120/EOD labels；
- 验证 local-day、DST、tail bracket、report-lag sensitivity；
- 输出 source→next routine→final METAR→WU/market lineage。

通过条件：1096 日 label coverage 完整；所有 feature timestamp `<=decision_ts`；METAR/WU audit 维持当前一致性。

### Phase 1 — Historical probability head

- 在 2023–2025 完成 A0→A5 ablation；
- logistic 与 HGB 同 rows打擂；
- 先评全部 checkpoint，再按 hour、season、boundary distance、path state解释；
- 一次性打开 2026 final audit。

通过条件：相对 `W0` 的 Brier/logloss date-block CI 上界均 `<0`；2026 同号；calibration 不出现系统性极端过置信。

这只证明天气预测有效，不证明有交易 alpha。

### Phase 2 — Market residual + expression replay

- 将 market-era PIT full ladder join 到相同 checkpoint；
- 比较 M0–M4 proper score；
- 同时跑 E1–E4 的5/10-share taker replay；
- direct `X NO` 与 upper strip 做逐 checkpoint cost parity；
- selected、blocked、book-missing 全部保留。

通过条件：candidate 相对同 rows market 的 Brier/logloss delta CI 上界均 `<0`；5-share fee ROI 与 excess 的 date-block CI 下界均 `>0`；10-share同号。

### Phase 3 — First-seen repricing

每个 FMI semantic first-seen 保存：

```text
t-60s / t0 / +15s / +30s / +60s / +120s / +300s full ladder
next routine official first-seen
final settlement
```

检验 `signed_reprice_30s/120s`，并分别报告：

- pre-cross probability update；
- source crossed but routine not confirmed；
- routine-confirmed；
- terminal false；
- correct/false 各自 fresh executable/fill coverage。

至少一个预注册 repricing horizon 的 date-block CI 下界 `>0`，否则结论是天气信息可能有效但盘口没有可交易延迟。

### Phase 4 — Frozen zero-notional forward

artifact freeze 后，不改 label、features、model、edge、expression routing：

- 至少 30 个独立 settled target dates；
- 至少 80 个 scoreable distinct `(date,X)` opportunities；
- 至少 50 个 t0 fresh、5-share executable first-positive signals；
- full-ladder/book/settlement coverage ≥90%；
- 所有 scoreable checkpoint 均写 journal/fact candidate。

未达到这些分母只继续 collector，不发布 promotion 结论。

## 9. Signal funnel

```text
all distinct FMI first-seen observations
→ as-of official bracket 可定义
→ full probability frame 可计算
→ q0/q1/q2/q3+ score
→ expression executable
→ first positive-EV (date,X) signal
→ router selected arm
```

分别报告 observation rows、distinct semantic events、`(date,X)` opportunities、target dates。

## 10. Evidence funnel

```text
historical observation-clock weather rows
→ collector-exact first-seen rows
→ PIT fresh full ladder
→ 5/10-share executable expression
→ next routine official
→ WU/Polymarket settlement
→ shadow / order / fill
```

source、book、settlement、depth、token map 缺失只算 coverage gap，不作为模型过滤。

## 11. Negative controls 和停止条件

Negative controls：

- raw market、season×clock prior；
- FMI 路径延迟一格的 stale-source model；
- 同月 target-date label permutation；
- Helsinki terminal-false/single-print false-cross 独立切片；
- Atlanta 2026-07-17 terminal false canonical case：单列 source→routine→WU basis 和 correct/false executable/fill分母。

停止/分流：

| 结果 | 结论 | 动作 |
|---|---|---|
| weather head 不胜 W0 | `rejected_weather_head` | 停止该 feature/model，不加 gate |
| weather head 胜，但不胜 market | `weather_predictive_not_residual` | 保留概率特征，不交易 |
| 胜 market，但无正 repricing | `information_not_timeable` | 保留模型，不做 stale-book expression |
| repricing 有但 fee ROI 不过 | `edge_consumed_by_execution` | 研究成本/深度，不上 live |
| 只有 E3 current YES 过门 | `late_persistence_only` | 停止 E1/E2，保留同模型互补表达 |
| E1/E2 历史、final audit、forward 全过 | `shadow_candidate` | 另行申请 tiny-live，不自动部署 |

任何 tiny-live 都需要用户再次明确授权并走 `weather-strategy-deploy`。本计划不设置自动晋升，不扩大城市和仓位。

## 12. 血缘与产物

```text
FMI/METAR/WU/full-ladder raw
→ weather_information_event
→ weather_state_checkpoint + feature frame
→ fact_signal_candidates
→ future plan/order/fill/settlement
```

- shared data logic：FMI/METAR/WU解析、local-day、native lattice、first-seen identity进入 `weather_data_feed/`。
- shared features：remaining heat/path/radiation/source basis挂既有 temperature context/feature layer。
- strategy-private：概率模型、calibrator、expression router config。
- opportunity：全部 scored/selected/blocked E1–E4写 `fact_signal_candidates`，不建平行 fact。

预期产物：

- checkpoint/label parity audit；
- historical feature frame；
- expanding-OOF predictions；
- frozen model/calibrator artifacts；
- expression cost parity与5/10-share replay；
- repricing timeline；
- zero-notional forward journal；
- final research report、registry与docs index。

## 13. 当前门状态

```text
significance=NA
baseline=NA
forward=FAIL(not started under this frozen spec)
conclusion=inconclusive / preregistered zero-notional research
```

8 环：当前计划覆盖 signal discrimination、probability distribution、market baseline、execution microstructure、capacity 与统计推断设计；真实 fill、组合相关性和 live drift 尚未覆盖，因此不允许 live 动作。
