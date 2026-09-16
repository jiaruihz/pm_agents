# Helsinki Remaining-Heat Distribution + Expression Router 研究计划 v1

Status: `active end-to-end qualification plan / A8 weather base frozen / Helsinki-only / zero-notional / no live change`
Updated: 2026-08-09（从单层 remaining-heat 研究收敛为“天气基座 → first-seen posterior → repricing → executable profit”终局验收）

## 0. 终局目标与交付纪律（2026-08-09 更新）

本计划不再以“发现某个新特征有效”或“某个小切片 ROI 为正”为交付。唯一要回答的问题是：

> 在全部 Helsinki FMI first-seen event × full-ladder rung 的固定 PIT 分母上，pre-event market prior 加上
> A8 天气分布变化、FMI→METAR/WU basis 和路径状态后，能否同时改善最终 settlement probability、预测尚未被盘口
> 吸收的短期 repricing，并在真实 ask/depth/官方 fee 下形成可重复正收益。

最终模型使用一个稳定 family identity，以 run/artifact hash 区分实验，不继续增加 `v8/v9/v10` 平行“当前模型”：

```text
A8 weather base: P(final exact Tmax | forecast + PIT FMI/METAR path)
  -> FMI entry head: P(settlement lattice | latest FMI before next METAR, prior official state)
  -> market-prior posterior: logit(p_post)=logit(p_market_pre)+g(weather innovation, basis, path)
  -> METAR correction head: update held-position P(win); HOLD/EXIT only, never new entry
  -> repricing head: P(full-ladder move at +30/+120/+300s/next official)
  -> execution head: fresh ask/VWAP + depth + fee + fill/adverse-selection
  -> SignalCandidate -> TradeIntent -> shared execution chain
```

其中天气 head、settlement posterior、short-horizon repricing 和 execution 是四个分别验收的 head；后一个 head
不能掩盖前一个 head 失败。A8 只是 frozen reference base，不因本轮某一天输赢重训；新 challenger 只有在固定三 grain
和 clean forward 上整体通过后才能替换它。

### 当前事实基线

- A8=`A2 forecast logistic + A3 METAR logistic + A4 all-feature shallow HGB + A5 enriched-physics HGB + A7 ordinal HGB`
  的等权 coherent ensemble。2025 Q3–Q4 confirmation 相对 A4 的 checkpoint RPS/logloss delta 为
  `-0.000892/-0.022146`，target-date 95% CI 均低于 0；天气基座历史晋级通过。
- 2026-08-01..08 的已查看 replay 是 `783 event rows / 7 settled dates / 1,312 two-sided expression rows`。
  A8 expression Brier/logloss=`0.10483/0.34250`，明显差于同 rows market `0.07009/0.21915`；
  34 个 first date-bracket trades 为 27 胜、fee-adjusted ROI `-2.07%`，非尘埃切片 `-4.07%`。
  因此“天气基座可用”成立，但“可交易 market residual”当前失败。
- 当前 collector-exact Helsinki/FMI 有 `1,586 distinct observations / 20 dates`，但既有通用 repricing book
  仍主要是 `46 cross events / 12 dates` 的事后 cross 子集；不能用它训练 all-event first-seen 策略。
- A8 clean forward 从 `2026-08-10` 首个完整 target date 开始；当前为 `0/30`，历史及 8/1–8 已查看窗口不再调参。
- 当前 WS journal 仍是 raw frame transport evidence；在 deterministic reconstructed-book materializer 和 immutable
  `feature_book_snapshot_id` 完成前，WS dynamics 不进入模型。

### 只保留四个晋级结论

内部 ablation、坏例子与价格档明细继续完整落盘，但不再逐个当阶段成果向上汇报。只在以下情况交付结论：

1. `BASE_PASS/FAIL`：天气基座是否继续合格；
2. `RESIDUAL_PASS/FAIL`：market-prior posterior 是否同分母胜 market；
3. `EXECUTION_PASS/FAIL`：repricing 是否覆盖 spread/fee 且可成交；
4. `FINAL_CONFIRMED/REJECTED/KEEP_COLLECTING`：30 日 frozen forward 后能否进入 tiny-live 评审。

若出现 PIT、book、settlement 或 artifact identity blocker，会直接报告 blocker；除此之外不以“小发现”中断执行路线。

## 1. 结论与动作

整体只做 Helsinki/FMI，将“每个 material first-seen 观测后最终 exact ladder 概率改变多少、市场是否已经吸收”
定义成连续概率与 repricing 问题。每个 10 分钟 FMI 更新都重估，但重复轮询不增加训练权重，也不自动产生新交易。

```text
pre-event full ladder market prior
→ FMI first-seen + A8 weather distribution innovation + source basis
→ posterior P(final exact ladder)
→ short-horizon full-ladder repricing
→ direct NO / upper-YES strip / exact YES 的 executable cost router
→ 5/10-share fee/depth replay与 frozen forward
```

旧 `confirmed-current X NO` 保留为固定规则 baseline，不再预设为最终 primary。最终 router 在同一个 coherent posterior 下
比较 direct NO、upper-YES strip 与 exact YES 的真实成本；表达只能由事前概率与可执行成本决定，不能按历史赢家挑选。
当前只研究和 zero-notional forward，不修改任何 live、city pool、sizing 或 execution policy。

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

Router policy：

- 每个 material first-seen event 都计算 E1–E4，但每个 `(target_date, position state)` 只允许最高事前
  fee-adjusted EV 的 arm 形成一个新 action；价格档不是 eligibility 条件；
- E1 与 E2 在 payoff 同义时只选较低 executable cost，不按历史赢家选择；
- E3/E4 与 E1/E2 先分别保留 expression 账，再报告统一 router 的 selected 账，不能用一侧赢家掩盖另一侧；
- `+1c/+2c/+3c` friction 只作预注册 sensitivity，不反向选择最佳 margin；
- v1 只做 taker replay；maker/queue 留到 taker alpha 与 repricing 过门后。

所有 checkpoint 继续记录后续 score；首次入场以后只计算 `would_adjust_target`，不把每个 10 分钟 tick 当新独立交易。

## 8. 实验阶段

2026-08-09 起按下面的终局顺序执行；后文原 Phase 0–4 的 label、分母和门槛继续有效，但不再把每个 phase
单独包装成可用策略：

| 工作包 | 必须完成的动作 | 晋级门 | 失败后的动作 |
|---|---|---|---|
| A. Base lock | 把 A8 训练/评分整理为可复跑 runner + ModelSpec，冻结 input manifest、feature schema、artifact SHA、train cutoff；同 raw 重放 bitwise/parity | A8 在 checkpoint/transition/state-entry 上保持历史晋级，clean forward 不出现相对 A4 的系统性退化 | 只回到天气 head 重新训练；不进入 residual，不拿盘口层补救 |
| B. All-event panel | 通过共享 WCIR materialize `first_seen_event_ladder_panel_v1`；全部 FMI events（含 non-cross）保存 pre/t0/+30/+120/+300s/next-official full ladder、missingness、四时钟、settlement | all-event、book、settlement逐层对账；cross-only 不再充当训练 universe；REST/book state parity 通过 | 只补采集/重建；不训练 market 模型 |
| C. Two-head training | 固定比较 market-only、A8-only、market+A8 innovation+basis、再加静态 book level；WS materializer ready 后才加 WS dynamics。settlement posterior 与 repricing head 分开训练 | settlement Brier/logloss/RPS 相对 market 的 date-block CI 上界均 `<0`；至少一个预注册 repricing horizon 相对 level-only 的 primary loss CI 上界 `<0` | weather 可预测但不可交易，保留 A8；停止该 residual，不追加阈值 |
| D. Executable forward | 冻结模型、event grain、expression router 和 fee；每个 material event 重估，但 position state 只允许首个新信息/目标变化产生 action。5/10-share taker 与真实 maker lifecycle 分账 | 至少30个新 settled dates、80个 active `(date,X)` opportunities、50个 fresh executable first-positive；5-share fee-adjusted ROI/excess date-CI 下界`>0`，10-share同号；coverage≥90% | repricing 被 spread/fee 吞掉则 `rejected_for_expression`；继续积累不等于继续调参 |

最终策略分母包含全部价格档。`effective_cost>=0.98` 尘埃单单独列交易数、胜率、成本、PnL、ROI，但不删除；
同时报告 `<0.98` 经济切片，防止用大量几乎无利润的高胜率单包装模型。任何价格带只作解释，不成为事后 eligibility gate。

最终报告必须同时给：全机会概率、同分母 market baseline、每个价格档、每个 relative rung、每天触发数、
首次入场时间、taker/maker fill 假设、最大单日/Top-5 PnL 集中度、去最佳日结果和逐笔错误清单。

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
| 任一预注册 expression 的 probability、market、repricing、fee-forward 全过 | `shadow_candidate` | 另行申请 tiny-live，不自动部署 |

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
weather_base=PASS_historical_A8_vs_A4_and_prior
market_baseline=FAIL_2026-08-01..08_same_rows
all_event_repricing_panel=BLOCKED_cross_only_book_evidence
execution=FAIL_seen_replay_fee_ROI_negative
forward=NA_A8_clean_forward_starts_2026-08-10_0_of_30_dates
conclusion=inconclusive / keep zero-notional / no live change
```

Readiness（2026-08-09）：production manifest 无 critical、canonical DB route healthy；唯一 manifest warning 为
market-books production checkout 的 loaded SHA 与 checkout HEAD drift。controller health 为 WARNING，但 JRS context、FMI/source、
market books、WCIR runtime 均健康；warning 是 non-trading weather state coverage，不污染 Helsinki 本计划分母。

**下一唯一动作**：先用现有 collector-exact raw 离线实现并验证共享 `first_seen_event_ladder_panel_v1`，以 Amsterdam/KNMI
作 golden fixture、Helsinki/FMI 作第二 adapter；不改变 collector scope、不部署生产、不生成真实订单。panel 通过
event/book/settlement count、四时钟、REST/book-state parity 后，才在固定 rows 上训练 C 工作包的 market-prior posterior
与 repricing head。A8 在此期间保持 frozen。

8 环：当前计划覆盖 signal discrimination、probability distribution、market baseline、execution microstructure、capacity 与统计推断设计；真实 fill、组合相关性和 live drift 尚未覆盖，因此不允许 live 动作。

## 14. 2026-08-09 执行结果：A8 exact-PIT 重放、all-event panel 与 market-prior A/B

### 14.1 Identity、数据补齐与结构修复

- production manifest 先于分析执行：`db_route.status=healthy`、无 critical，repo compatibility DB 与
  `/Volumes/jrs/pm_agents/runtime/weather.db` 为同一 canonical identity。唯一 manifest warning 是 market-books
  loaded SHA 与 checkout HEAD drift；本轮只读研究未改变该进程。
- Helsinki `2026-08-08` pm_history 已精确补取，随后按 canonical incremental refresh 合并；当前
  `settlement_outcomes` 覆盖至 `2026-08-08`、共 `45,928` rows，Helsinki 8/1–8 每日均为 11 个 bracket、1 个 winner。
  标准 refresh 同时补了其余城市：46 个 city-day 文件 fetched、5 个 known-not-found，新增/重放 517 settlement rows；
  这是 coverage 修复，不是策略筛选。
- 旧 A8 forward runner 读取的 observation cache 停在 8/7，但 canonical `source_events` 已有 8/8 的 EFHK METAR。
  共享 loader 现只接受 `collector_exact` 且 `original_first_seen_unknown != true` 的 source event，并按 report clock
  去 revision 重复；8/1–8 从 378 个候选 METAR rows 中移除 40 个 late-backfill unknown，保留 338 个 exact rows。
- frozen A8 artifact SHA-256 为
  `bed86855c44ae5fe876506908170091ae85d47ec4abdb538102e051ea2e0d27a`；已补齐 artifact 所需的 ordinal/prior
  class 与 SHA-pinned generic scorer，成员仍为 A2/A3/A4/A5/A7 各 20%，没有重权重或重训。

两次直接重跑 FMI WFS 时遇到 `SSL UNEXPECTED_EOF`；因此 exact-PIT 结果由已成功保存且 prediction/market rows 完全相同的
v2 replay 做严格 lineage post-filter，不以失败网络请求生成新预测。完整 A8 producer 当前仍以 git commit `fc56d1f3`
作为训练代码 identity；当前分支已能稳定加载/评分 frozen artifact，但尚未把该大 runner 合并成公共训练入口。

### 14.2 A8 seen-window 结果：天气基座尚可，交易表达失败

本段是 2026-08-01..08 的 **seen-before-freeze research replay**，不是 8/10 起 clean forward。

Signal funnel：

```text
976 input weather events / 8 dates
→ remove 40 non-exact late-backfill METAR
→ 936 exact-PIT events / 8 dates
→ 598 FMI + 338 METAR
```

天气最终 exact distribution：exact accuracy `70.01%`、相差不超过一档 `85.23%`、multiclass Brier
`0.03340`、logloss `0.74415`、RPS `0.02778`。这说明基座能约束最终档位，但绝不等于可按每个 10 分钟 tick 下单。

Evidence funnel：

```text
5,464 expression rows
→ 5,453 exact-event-linked
→ 2,482 two-sided same-row market scores
→ 943 positive raw A8 edges
→ 40 first (target_date, bracket) BUY-NO trades / 8 dates
```

在 2,482 same rows 上，A8 Brier/logloss=`0.10213/0.32541`，market=`0.07648/0.23329`；market 明显更好。
40 笔 5-share 含费 replay 为 32 胜 8 负、成本 `$162.91`、PnL `-$2.91`、ROI `-1.79%`，target-date
bootstrap CI `[-3.00%,-0.72%]`。`effective_cost<0.98` 只作解释的非尘埃切片为 25 笔 17 胜、ROI `-3.89%`，
CI `[-6.40%,-1.89%]`；未将价格档改成 gate。

按 first-entry attribution，A8 触发的 FMI slice 为 9/9、ROI `+10.25%`，METAR slice 为31笔23胜、ROI `-5.81%`，
但这**不能解释成 FMI 比 METAR 准**。逐笔 lineage 显示8个loss的首次动作全部发生在 Helsinki local `00:21–04:51`：
当天最早的 routine METAR event 先占用了 `(target_date, bracket)` 的 first-positive 名额，FMI 白天后来更新的同一机会不再计作新交易。
对应 book 均在 event 后1.0–19.6分钟，市场已经有时间吸收报文；这些不是 daytime METAR cross 或 stale-book repricing，
而是模型在当前温度仅12–18°C时，对最终20–25°C exact winner过度自信地买NO。同分母 proper score 中，FMI 的
model-market Brier 差仅 `+0.00406`，METAR 为 `+0.03813`，但 source 与 clock/entry-order共线；耐久结论应写为
`overnight first-entry posterior overconfidence`。该切片只定位 market-prior/source×clock calibration 与 entry grain 问题，
不新增 METAR exclusion 或时段 gate。

### 14.3 共享 all-event panel：代码通过，Helsinki 训练覆盖不通过

已实现共享 `first_seen_event_ladder_panel_v1` 与 CLI，固定保存 pre/t0/+30/+120/+300/next-official 的全 ladder、
missingness、request/response/parse/available clocks、event identity 与 settlement；8 个定向测试通过。

Helsinki 2026-08-07..09 signal funnel 为 255 个 material FMI events / 3 dates、255 个 panel events、1,815 event-rungs。
Evidence funnel 为：pre `157/255`、t0 `10/255`、+30 `19/255`、+120 `25/255`、+300 `106/255`、next-official
`10/255`，五个 book slot 全有为 `0/255`。canonical 周期 full-ladder 约 300 秒一次，所以能覆盖部分 +300，不能替代
event-driven t0/+30/+120 burst。结论是 collection coverage blocker，不是模型筛除，也不得在该分母训练 repricing head。

Amsterdam golden fixture 有 2,756 events / 10 dates，slot shape coverage 为 96.95%–99.06%，其中 2,615 个 event 五档齐全；
但旧 dedicated capture 没保存严格 request/response/parse clocks，post slots exact-clock=`0`。它只证明 materializer shape，
不构成正式 PIT clock parity。

### 14.4 Market-prior posterior seen-OOF A/B

在 5,453 exact expression rows 上固定为“每个 `(event_id, bracket)` 第一份双边 PIT quote”，得到 1,602 rows / 8 dates；
前三日只训练，后五日 expanding OOF 测试共 1,317 rows。训练按 target_date 等权；没有 source、时段、价格或额外 edge gate。

| 模型 | Brier | Logloss | AUC | ECE10 | 5-share trades | ROI |
|---|---:|---:|---:|---:|---:|---:|
| raw market | 0.09409 | 0.27228 | 0.8989 | 0.07525 | 0（基准，不是交易信号） | NA |
| raw A8 | 0.09697 | 0.31584 | 0.8564 | 0.03026 | 27 | -1.34% |
| fixed-market-logit offset logistic | **0.09361** | **0.27195** | 0.8976 | 0.06943 | 11 | -5.75% |
| compact model-only logistic | 0.10149 | 0.33021 | 0.8375 | 0.03306 | 23 | -3.21% |
| free market+A8 logistic | 0.10680 | 0.31261 | 0.8850 | 0.06380 | 40 | -0.12% |
| shallow HGB market-prior | 0.13052 | 0.44714 | 0.8250 | 0.09998 | 26 | -2.03% |

fixed-offset 是唯一在 Brier/logloss 点估同时略胜 raw market 的 challenger，但 paired target-date delta CI 分别为
`[-0.00094,+0.00002]`、`[-0.00229,+0.00193]`，没有过门，且表达 ROI 更差。free logistic 的 ROI 接近 0，
但 proper score 明显退化，不能拿交易噪声掩盖概率失败。HGB 在小日期分母明显过拟合。

分情况结果保持同一 OOF rows：

- FMI：raw A8 Brier `0.09200` 优于 market `0.09652`，但 logloss `0.29154` 仍差 market `0.27988`；
- METAR：raw A8 Brier/logloss `0.10726/0.35423`，明显差 market `0.09500/0.27310`；
- morning A8 Brier略优但logloss仍差；midday、overnight均差；free market+A8 logistic 只在 afternoon 点估改善，
  不能变成事后时段规则；
- free market+A8 logistic 的 40 笔由 FMI 6笔/5胜/ROI `+11.21%` 与 METAR 34笔/31胜/ROI `-1.74%` 组成；
  仍只用于 source-basis 定位，不作 eligibility。

### 14.5 当前资格与下一动作

```text
weather_base=PASS_historical_reference
A8_seen_exact_PIT=PASS_path_accuracy_but_FAIL_same_row_market_and_fee
all_event_panel_contract=PASS_offline
Helsinki_event_book_coverage=FAIL_t0_10_of_255_and_complete_0_of_255
market_offset_posterior=POINT_IMPROVES_BUT_CI_FAIL_AND_FEE_FAIL
repricing_head=BLOCKED_by_event_burst_coverage
formal_forward=0_of_30_starts_2026-08-10
live_eligible=false
```

因此现在**不冻结新的交易模型，也不换 shadow artifact**。A8 weather reference 保持 frozen；唯一合理的下一阶段是经独立部署授权，
让共享 collector 在 Helsinki 白天每个 FMI material first-seen 附近抓 full-ladder t0/+30/+120/+300 burst，并保留原有低频分层采样；
先把 exact-clock coverage 补到可训练，再冻结 market-offset/source-basis posterior 与独立 repricing head。10-share depth/VWAP 当前输入未覆盖，
正式执行验收尚不具备资格。本轮未改 live、未下真实订单。

可复跑 artifacts：

- A8 exact PIT：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_event_driven_a8_seen_replay_through_20260808_v3_exact_pit`
- Helsinki all-event panel：`/Volumes/jrs-archive/pm_agents/research/artifact_store/first_seen_event_ladder_panel_v1_helsinki_20260809`
- Amsterdam golden：`/Volumes/jrs-archive/pm_agents/research/artifact_store/first_seen_event_ladder_panel_v1_amsterdam_20260809`
- market-prior OOF：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_market_prior_posterior_v1_seen_oof_20260809`

### 14.6 Source role correction：FMI entry，METAR 只纠错/退出

逐笔 review 后确认 14.2 的 mixed-source first-entry policy 混淆了 source 职责。耐久因果合同改为：

```text
latest FMI first-seen before next routine METAR
→ 估计 final exact-bracket distribution并允许新entry
→ next/later METAR到达后只更新已持仓概率
→ HOLD或按可执行NO bid退出
→ METAR永远不创建新仓
```

这不是按坏案例新增时段/source gate，而是恢复策略原本的信息领先关系；METAR 后的市场已经获得官方更新，不能再冒充
FMI first-seen alpha。此前40笔mixed-source replay保留作模型/盘口诊断，但从主策略交易证据中 supersede。

用同一 exact-PIT rows 重放 FMI-only entry：1,453个FMI executable rows → 460 positive rows → 35个
first `(target_date, bracket)` entries / 8 dates；27胜8负，成本`$138.84`、hold PnL `-$3.84`、ROI `-2.77%`，
date CI `[-6.82%,+0.71%]`。因此只改 source role 仍不足以证明 entry alpha。

METAR correction diagnostic 使用每次后续 METAR 的 updated A8 `P(NO)`；若5-share taker-sell NO top bid扣官方fee后
高于继续持有价值，则首次退出。35个持仓中触发20次退出，救到8个loss中的4个，同时提前退出16个winner；overlay PnL
`+$4.91`、ROI `+3.53%`、CI `[-3.57%,+8.27%]`，相对hold uplift `+6.30pp`、paired date CI
`[+2.10pp,+9.47pp]`。但当前只有top bid、20次exit的5-share depth验证为0，故只是有结构的退出方向，不能作为
可执行ROI或frozen state machine。

在干净FMI-only probability grain上，前3日训练/后5日OOF共808 event-rungs：raw market Brier/logloss
`0.09652/0.27988`，raw A8 `0.09200/0.29154`；fixed-market-logit offset为`0.09362/0.27526`，Brier delta CI
`[-0.00515,-0.00096]`通过，但logloss delta CI `[-0.01027,+0.00102]`未过。14笔5-share entry ROI `+3.25%`，
CI `[-8.78%,+23.29%]`。这比mixed-source口径干净且有点估空间，仍不足以冻结。

更新后的唯一动作：继续A8 frozen weather reference；entry posterior只训练/评分FMI first-seen，METAR rows进入独立
correction/exit state，不再参与新entry。补齐每个FMI event的pre/t0/+30/+120盘口与METAR exit时的5-share bid depth后，
再冻结entry与exit两个head并启动30日forward；本轮不改live。

因果口径 artifact：

- `/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_fmi_entry_market_prior_posterior_v1_seen_oof_20260809`

### 14.7 FMI-entry / METAR-exit 八日 casebook

35笔是8个target dates上的多档NO组合，不是35个独立city-day判断；每日若组合包含最终winner bracket，该档NO必输，
其余NO多半赢。因此27/35胜率不能作为主指标，必须按每日组合成本/PnL与相关敞口解释。

| target date | FMI entry local time | BUY NO brackets | final winner | hold PnL | METAR exits | overlay PnL |
|---|---|---|---|---:|---:|---:|
| 08-01 | 14:02–18:41 | 22,23,24,25,26+ | 23 | +$1.11 | 0 | +$1.11 |
| 08-02 | 10:08–12:11 | 22,23,24,25 | 22 | -$1.76 | 2（未救22） | -$1.81 |
| 08-03 | 06:32 | 21,22 | 21 | -$0.71 | 0 | -$0.71 |
| 08-04 | 06:00–08:01 | 21,22,23,24,25 | 21 | -$0.18 | 4（救21） | +$2.31 |
| 08-05 | 06:12 | 21,22,23,24,25,26 | 21 | -$0.81 | 5（救21） | +$1.49 |
| 08-06 | 07:32–11:01 | 24,25,26 | 25 | -$1.36 | 1（未救25） | -$1.36 |
| 08-07 | 06:01–14:01 | 18,20,21,22,23 | 22 | +$0.37 | 4（救22） | +$2.17 |
| 08-08 | 06:02–12:31 | 19,20,21,23,24 | 20 | -$0.51 | 4（救20） | +$1.71 |

错误分为三类：8/1 是18:41买入23 NO、成本仅0.1c的late overshoot dust loss；8/2、8/3、8/6是早间
FMI对最终winner NO过置信且后续METAR未给出可退出信号；8/4、8/5、8/7、8/8同类错误被METAR value-exit救回大部分成本。
20次exit中只有4次对应最终loser、16次对应最终winner；ex post看似“错退赢家”，但这16次共少赚`$3.32`，换来4个loser
少亏`$12.07`，净uplift仍为`+$8.75`。这验证的是持仓纠错机制，而不是METAR预测最终winner更准。

价格结构也说明胜率会误导：`effective_cost>=0.98`有14笔、14胜，但投入`$69.48`只赚`$0.52`；其余21笔仅13胜，
hold PnL `-$4.37`、ROI约`-6.3%`。价格档继续只作解释，不成为 eligibility gate。

FMI entry quote 相对event的age为0.59–8.65分钟、p50 4.49分钟，仅3/35在1分钟内；所以当前回放可以评
settlement posterior与状态机方向，不能证明first-seen盘口反应速度。FMI-only market-offset的14笔OOF challenger集中在
8/4–8/8：前3日均亏、后2日盈利，总ROI `+3.25%`且CI宽，尚无稳定性。

设计判断：source role与HOLD/EXIT价值比较是合理的；仍需修的不是新增阈值，而是把多档NO作为单个city-day portfolio
统一计算预算/相关敞口，补exact-clock FMI entry quote与METAR exit 5-share depth，并让entry posterior只在FMI grain训练。

### 14.8 1c–99c research slice 与 METAR exit clock audit

按 review 要求只作解释性过滤：在已选35笔中剔除 fee-adjusted 5-share entry cost `<=0.01` 或 `>=0.99`，不以
后来价格替补，也不把该价格带写成最终 eligibility。保留24笔/8 dates，17胜7负、投入`$88.98`、hold PnL
`-$3.98`、ROI `-4.47%`，date CI `[-10.00%,+1.06%]`。

| target date | kept NO brackets | final winner | hold PnL | delayed-METAR exits | delayed overlay PnL |
|---|---|---:|---:|---:|---:|
| 08-01 | 24 | 23 | +$1.06 | 0 | +$1.06 |
| 08-02 | 22,23,24 | 22 | -$1.80 | 2 | -$1.84 |
| 08-03 | 21,22 | 21 | -$0.71 | 0 | -$0.71 |
| 08-04 | 21,22,23 | 21 | -$0.19 | 2 | +$2.33 |
| 08-05 | 21,22,23 | 21 | -$0.83 | 3 | +$1.50 |
| 08-06 | 24,25,26 | 25 | -$1.36 | 1 | -$1.36 |
| 08-07 | 18,20,21,22,23 | 22 | +$0.37 | 4 | +$2.17 |
| 08-08 | 19,20,21,23 | 20 | -$0.52 | 3 | +$1.73 |

退出规则本身是连续价值判断，不是“METAR与FMI不一致就退”：每份later METAR到达后，用新METAR重跑held bracket的
`P(NO)`；计算当刻可卖NO的 `net_bid = best_bid - official_weather_fee`；仅当 `net_bid > updated P(NO)` 时全退5 shares。
METAR不能创建新entry。该规则的feature weather event与execution book必须分别保存identity/clock。

clock audit 推翻了此前对overlay执行性的暗示：`event_obs_ts`是METAR report clock，`decision_ts`是collector实际first-seen/
可用时钟；20个exit的report→decision p50=`210s`（约3.5分钟，属预期上游发布/检测延迟）。当前问题是decision/model-output之后
又等到周期canonical snapshot，额外quote lag p50=`216s`、p90=`319s`；仅1/20在collector decision后30秒内、3/20在60秒内、
4/20在120秒内，且exit
5-share depth验证仍为0。1c–99c slice的15个delayed exits给出PnL`+$4.86`、ROI`+5.47%`，但只是延迟盘口机制诊断；
若只承认30秒内唯一一笔、其余缺失按hold，PnL仍为`-$3.99`、ROI`-4.48%`。因此METAR exit结果正式标为
`BLOCKED_METAR_T0_QUOTE_AND_DEPTH`，不能发布`+5.47%`或旧`+3.53%`为可执行策略效果。

### 14.9 FMI/METAR ↔ WS 首日因果关联与运行链审计

2026-08-09 已把 raw WS subscription epoch、完整 `book` baseline、`price_change` delta、FMI/METAR
`information_event_id/first_seen_at_utc` 串成可复跑的 event-bracket ledger。重建器每次 reconnect/selector epoch
切换都清空旧状态，必须重新拿到 baseline；5-share buy cost / sell proceeds 按逐档深度计算，不能用 midpoint 代替。
对 CLOB 的空侧 `best_bid=0` / `best_ask=1` sentinel 已按 non-executable empty side 处理。5 个定向测试通过；选取一个
真实 Helsinki 6-token epoch 回放为 `519 frames / 6 baselines / 0 parity error / 0 blocked token`。

当日最终重放输入为77个material FMI、37个material EFHK METAR；其中40/12个落在active Helsinki WS epoch。
本轮读取111,623个去重WS frames、210个subscription epochs。
严格重建发现105个delta/book parity error，涉及缺失或乱序的增量段；这些token从错误点起fail closed，直到新baseline，
没有被下游当成“缺数据=不交易规则”。最终可关联27个FMI events / 69 bracket rows与11个METAR events / 29 rows；
其中FMI/METAR分别56/24 rows具备双边5-share depth。coverage减少属于evidence funnel，不是signal funnel。

| source | linked events | bracket rows | 5-share双边depth | NO mid 10s mean abs | 30s | 60s |
|---|---:|---:|---:|---:|---:|---:|
| FMI | 27 | 69 | 56 | 0.127c | 0.275c | 0.470c |
| METAR | 11 | 29 | 24 | 0.025c | 0.115c | 0.083c |

这只是一日微观结构证据，但方向清楚：FMI 后一分钟的平均绝对重定价约为 METAR 的5.6倍；10组同report-time
FMI→METAR配对有26个同档rows，其中21个在METAR first-seen前已不再变化，5个已移动，最大3c。故当前最合理的
因果角色仍是“FMI负责entry posterior，METAR负责held-position correction”，不是等METAR后再开仓。

盘口本身不能忽略：FMI linked rows 的spread中位1c、p90 11c；34/69在2c内、14/69为2–10c、8/69超过10c、
13/69为单边。最大FMI反应集中在09:12–09:21 UTC：21 NO 60秒mid下降8.5c，同时23 NO上升5c；这是整条
概率质量在ladder间搬移，不支持只盯单档价格。METAR最大60秒变化仅1c。

同时审出 production WCIR 尚未完成这条因果链：Helsinki adapter仍消费周期REST active-bracket book；登记的生产checkout
还保留旧forecast路径，8/8–8/9没有新的正常 Helsinki model decision，8/9只见book-fetch blocker。控制仓配置已指向唯一
生产forecast owner `/Volumes/jrs/weather_data_feed_service_runtime/forecast/forecast_hourly_curves`，并新增共享WS deterministic
reconstructor；但当前登记的 `pm_agents_city_runtime_v2_prod` checkout 含另一条Amsterdam未提交改动，按git-first边界不能把
本轮修复混入并重启。因此本轮没有声称生产shadow已切换、没有订单、没有live改动。待该dirty production checkout独立收口后，
把reconstructed `feature_book_snapshot_id/execution_book_snapshot_id` 接入WCIR，并实现FMI OPEN与METAR HOLD/SELL
zero-notional state transition；这一步完成前，状态为
`capture+offline-linkage PASS / runtime state-machine NOT DEPLOYED`。

可复跑 artifact：

- `/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_fmi_metar_ws_linkage_v1_20260809/summary.json`
- `/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_fmi_metar_ws_linkage_v1_20260809/event_bracket_linkage.csv`

### 14.10 Ladder microstructure 增量模型 A/B

按 `market_structure_edge.md` 的 City Reuse Handoff，将可跨城市复用的
`rung-relative markout`、signed mode distance、左右邻档比例/曲率、neighbor propagation/lead-lag 与
`weather shock × mode distance × neighbor propagation` 做成共享 feature contract
`weather_ladder_microstructure_v1`。没有复制已失败的 max/min selector，也没有按 Helsinki、价格或历史盈利日加 allowlist。
特征缺失不缩分母：每天首个 checkpoint 和 ladder 边缘档保留，由每个训练 fold 内 imputer 处理。

本轮先在已结算、历史周期盘口上检验 settlement posterior 的增量价值；其 feature clock 是
`decision_current_periodic_checkpoint`，不是 exact first-seen WS。8/9 的 WS 只有一天且尚未结算，只用于证明未来能物化
10/30/60s dynamics，未进入本轮训练或选模。

#### Readiness 与双漏斗

| 项目 | 状态 | 证据 / 缺口 |
|---|---|---|
| PIT source/book/label | READY for historical diagnostic | 8/1–8 FMI，quote不早于event decision，settlement binary |
| canonical/build identity | READY | production manifest `db_route=healthy`、无critical；输入SHA `4c9e3775…3f82`、run build `3b024f1f…7291` |
| market quote/depth | PARTIAL | historical模型行是双边5-share周期checkpoint；不是event-exact WS |
| independent dates | LOW | 8 dates；前3日训练、后5日expanding OOF |
| WS reconstruction | READY for offline feature materialization | deterministic baseline+delta parity已通过；8/9仅一天，不能训练/晋升 |
| frozen forward | UNTOUCHED | 8/9 WS未用settlement label；正式30日forward仍未开始 |

```text
signal funnel（event/rung）:
165 FMI events / 8 dates
→ 899 unique event-bracket checkpoints
→ 808 OOF rows / 5 dates
→ no hard selector, no runtime candidate, no order

evidence funnel（raw expression row）:
3,354 FMI rows
→ 1,408 valid probability + causal two-sided executable rows
→ 899 first (event, bracket) rows
→ 808 settled OOF rows / 5 dates
```

feature coverage：899 rows中，569行左右邻档静态结构完整，841行动力/interaction完整；157/165 events有prior checkpoint
和weather shock。缺失行全部保留，没有把coverage gap包装成策略筛选。

#### 固定同分母结果

| arm | Brier | logloss | AUC | ECE10 | 解释 |
|---|---:|---:|---:|---:|---|
| weather-only A8 | 0.092002 | 0.291540 | 0.89514 | 0.03770 | 天气基座 |
| M0 market level | 0.096522 | 0.279880 | 0.89969 | 0.07272 | 同时点raw market |
| weather + level（incumbent fixed offset） | **0.093618** | **0.275256** | **0.90126** | 0.05977 | 当前最优compact posterior |
| weather + level + ladder core | 0.095942 | 0.278935 | 0.89927 | 0.07238 | 强收缩fixed-offset challenger |
| weather + level + ladder shallow HGB | 0.105858 | 0.377846 | 0.81483 | 0.08093 | 小日期分母明显过拟合 |

ladder core相对raw market的Brier/logloss delta为`-0.000580/-0.000944`，date-block CI分别
`[-0.001263,+0.000100]`、`[-0.002859,+0.001090]`，均未过门。更关键的是相对当前incumbent，Brier退化
`+0.002325`，CI `[+0.000798,+0.003979]`；logloss退化`+0.003680`，CI `[-0.000380,+0.007449]`。
5个OOF日期中Brier仅8/7改善，其余4日退化。

交易输出不能反过来包装概率失败：强收缩ladder offset只触发1笔且碰巧盈利，`1/1`和`ROI +107%`没有统计意义；
joint logistic ladder为34笔/29胜但ROI `-3.00%`，shallow HGB ladder为21笔/16胜、ROI `+1.48%`且CI跨0。
没有新增阈值，也没有用ROI选模型。

**结论与动作**：共享ladder feature contract保留，供Helsinki和其他城市同一runner复用；但当前周期盘口版
`weather shock × mode distance × neighbor propagation`不能替换incumbent。真正值得继续的是把同一组特征换成
exact first-seen reconstructed WS的pre/t0/10/30/60s state，再在累计足够settled dates后固定比较
weather-only / level-only / weather+level / weather+level+WS dynamics。当前不换shadow、不改live。

可复跑artifact：

- `/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_market_prior_ladder_microstructure_seen_oof_20260809_v3/summary.json`
- `/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_market_prior_ladder_microstructure_seen_oof_20260809_v3/market_paired_bootstrap.csv`
- `/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_market_prior_ladder_microstructure_seen_oof_20260809_v3/oof_event_bracket_predictions.csv.gz`
