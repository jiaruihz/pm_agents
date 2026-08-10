# Weather Tmax Distribution Edge Strategy

Status: current-reference
Updated: 2026-08-09 convective tail distribution independent sleeve review
Source of truth: yes for this strategy family
Superseded by / Used by: WEATHER_DOCS_INDEX.md; WEATHER_STRATEGY_REGISTRY.md

## 当前结论

这条研究家族的稳定名字是：

```text
tmax_distribution_edge
```

当前研究状态：

```text
confirmed_alpha = none
historical_shadow_identity = tmax_distribution_edge_shadow_v1
promotion = no live
```

人话：这一家族保留了完整的概率、表达和执行血缘，但截至当前没有一版在固定 PIT
分母上稳定打败 market 并通过 frozen forward。`tmax_distribution_edge_shadow_v1`
是历史 shadow identity，不证明此刻有对应进程；实际 runner、notional 和订单状态只认
production manifest、raw runtime 与 exchange evidence。

45 份 Tmax 报告整合后的耐久结论：

- P0–P6 与 target-book v2 证明了“coherent exact/full-ladder distribution →
  fee-adjusted expression residual”是正确问题形式，但早期正 ROI 的日期 CI 较宽。
- 7/10 lineage repair 修掉 live/replay feature、NaN ask、sibling 和完整 ladder
  口径分叉；同时证明所谓 below-ladder 新容量主要是 collector 丢 near-binary sibling，
  不是新 alpha。
- v3 在固定 8,094 clean states 上复核后，weather-only 明显输 market；
  `market_path`、source、strict PIT 和 archive upper-bound 均未稳定改善 market proper
  score。first-lock execution 的大多数 route ROI 也跨零或为负。
- 因此旧 `inconclusive_positive_signal` 只能描述对应历史窗口，不能作为当前晋级依据。
  当前可复用的是分布表示、PIT contract、full-ladder accounting 和 blocked telemetry，
  不是旧 selector、阈值或 live 配置。

当前收口证据：
[lineage repair](analysis/2026-07/2026-07-10-tmax-lineage-repair-replay-v1.md) ·
[clean feature restoration](analysis/2026-07/2026-07-12-tmax-clean-feature-restoration-v1.md) ·
[v3 fixed-denominator result](analysis/2026-07/2026-07-13-tmax-distribution-v3.md)。

### Convective / Tail Distribution：独立可运行 zero-notional sleeve（不并入 Core Carry）

稳定 identity：`weather.convective_tail_distribution`。它与高价、高命中的 Current-YES Core
Carry 分账；即使未来成立，也只作为低命中、多腿、凸性 terminal-distribution sleeve。

2026-08-09 May-long retrain 以 2026-05-06–07-28 的 3,378 张原始 HeadA 5–20¢ candidates
为 signal denominator（1,224 selected + 2,154 unselected）。PIT probability denominator 从
5/19 immutable snapshots 接到 7/15 `tmax_v2`，共 17,339 states / 63 dates；expanding OOF 为
15,874 states / 58 dates。5/06–5/18、7/07–7/14 是显式 coverage gap。旧/新 adapter 在 14 个
重叠日的 330 个 states 上 normalized market L1、forecast peak 差和修正后的 decision-clock 差
中位数均为 0。

结论比 July-only 结果更弱且取代它：raw market Brier/logloss/RPS 为
`0.572449/1.113613/0.061441`；weather-only、固定 30% offset、C1–C5 全部未胜 market。
最接近且 9/9 folds 收敛的是 C2 adjacent diffusion，三项 delta 仍为
`+0.000376/+0.001470/+0.000077`；C3 0/9、directional split-tail C4 5/9 folds 收敛，均不能冻结。
更低维的 directional neighbor transport C5 9/9 收敛，但 delta 为
`+0.001385/+0.004217/+0.000310`，logloss/RPS date-CI 全劣于 market。物理约束解决了方向语义和
数值收敛，但没有替代稀缺的完整 PIT convective supervision。C2 几乎总 widening（99.79%，平均
variance ratio 1.149），但 outcomes 不支持这种宽尾。C2 full weather 相对同族 intercept-only
有极小点估改善 `-0.000177/-0.000627/-0.000037`，三项 target-date CI 全跨 0；因此目前既不能
确认概率 edge，也不能确认天气时钟 edge。fresh full90 与 partial books 都输 market，盘口 age
中位约 0.03 min，不支持旧/薄盘口归因。

选模不使用 ROI。旧 299/192/123 结果来自三个不同 first-positive universe，已 superseded-for-decision-use。
统一 entry-state 后，三种表达同为 294 city-date events / 53 target dates：single/adjacent/basket fee ROI
`-3.42%/-2.64%/-6.50%`；single/strip CI 跨0，basket CI `[-12.97%,-0.68%]` 全负，标记
`rejected_for_expression`。故资金表达为 none，runner 只并行记录完整 distribution 与三种 zero-notional
expression。若以后过门，容量仍按 5–10 shares，不用 displayed depth 冒充 fill capacity。

与 frozen Core replay 的同日完整 calendar 有 29 dates；统一 entry 后 daily PnL correlation 为
single `-0.178`、strip `+0.145`、basket `+0.102`。低相关不替代 alpha 门。当前状态仍为
`runnable zero-notional candidate / not confirmed / no funded sleeve`，不并入 Core、不恢复 HeadA live。

frozen artifact 继续为 C2、training 5/19–7/28、63 dates / 17,322 states，final fit 收敛；
当前 5 个定向测试通过。更新后 current-raw zero-notional smoke 为 79 events→2 scored、77 blocked：
51 个缺 target-day observation，26 个 full-ladder quote/bracket blocker；0 common-entry tickets、0 orders。统一
selector 后 formal forward 从
`0/30 dates, 0/80 tickets` 重新累计，fresh coverage 暂不可算。POP、云、风、湿度和 warming 的历史
缺失均显式保留，不新增 city/source/price/weather hard filter。

权威报告：
[convective tail distribution v1](analysis/2026-08/2026-08-09-convective-tail-distribution-v1.md)。

下文保留策略从 P0–P6、target-book、lineage repair 到 runtime contract 的历史演进。
其中出现的 candidate config、runner 名和 live/shadow 叙述都只属于相应报告时点；与本节冲突时，
以本节研究结论和动态 production evidence 为准。

## 策略本体

旧版本是：

```text
识别 weather regime -> 固定 route 到 current NO / d2 NO / current YES
```

现在的版本是：

```text
估计 Tmax 最终分布 -> 计算每个可买表达的 P(win)-ask -> 选择最高 edge 表达
```

模型输出四个 bucket：

- `current`
- `d1`
- `d2`
- `tail`

可选表达：

- `current YES`
- `current NO`
- `d1 NO`
- `d2 NO`

核心公式：

```text
model_edge = P(expression wins | market + weather state) - ask
```

同一 `city + target_date + decision_hour_local` 先只选择一个最高 edge 表达。
执行/绩效主口径再按 live-like 风险单位收敛：同一 `shadow_config_id + scope + city + target_date`
只把第一条 edge-pass 机会标成 `selected`。

后续同一 city-day 再次触发的小时信号不会丢弃，会记录为
`blocked / city_day_after_first_selected`，用于复盘“如果重复追单会怎样”。
如果最高 edge 没过阈值，则记录为 `blocked / below_edge_threshold`。

## Exact-Book Bridge v1

Lucknow 复盘后的表达层实验不是“删掉 d1 NO”或“马上改买 d1 YES”，而是先把 exact bracket 的
YES/NO sibling 放到同一个矩阵里比较。

当前已完成的最小 bridge：

```text
fixed four-bucket probability: current / d1 / d2 / tail
legacy_4expr: current_yes / current_no / d1_no / d2_no
bridge_6expr: legacy_4expr + d1_yes + d2_yes
bridge_no_current_yes_5expr: current_no / d1_no / d2_no / d1_yes / d2_yes
selection: first eligible city-day, fee-adjusted edge >= 0.02
ask source for d1/d2 YES: 1 - sibling NO bid
```

关键结果见
[2026-07-05-tmax-exact-book-bridge-v1.md](analysis/2026-07/2026-07-05-tmax-exact-book-bridge-v1.md)：

- `ask>=0.40 + fee_edge02` verified 上，`bridge_no_current_yes_5expr` 点估 +9.7%，legacy_4expr +8.4%。
- dev/verified 的 CI 仍宽，且 `d1_yes` 单腿偏弱；所以这是 `shadow_bridge_complete_not_live`，不是 live 替代。
- `current_yes` 在 settlement-basis / below bucket 修复前仍只做 shadow；`d1_no` 保留为合法补集表达。

这版 bridge 还不是 full ladder target book。真正完整版本需要逐格 hazard / full-ladder 概率、
实时 sibling book、以及 target-book reconciliation 的平仓成本账本。

## External Baseline / Target-Book v2

2026-07-08 按外部 `polymarket-tmax-lab` 的可迁移概率形状做了一个同分母对照组：

```text
external_tmax_baseline_v0:
  daily-max forecast error -> Gaussian outcome probabilities
  recency-weighted residuals
  market blend alpha selected on pre-cutoff expanding CV
  no live / no executor / no external ROI claims
```

结果见
[2026-07-08-external-tmax-baseline-v0.md](analysis/2026-07/2026-07-08-external-tmax-baseline-v0.md)：

- 外部 raw Gaussian/recency 概率明显输给 market-local；pre-cutoff CV 选出的 blend alpha = `0.0`。
- 结论是 `external_baseline_shadow_only`：不能把外部模型当 alpha 替代；可借鉴的是 previous-runs/source-profile/calibration checker 的工程方法。

同日新增
[2026-07-08-tmax-target-book-v2.md](analysis/2026-07/2026-07-08-tmax-target-book-v2.md)：

```text
our_tmax_target_book_v2:
  probability_source -> expression candidates -> target_book ledger
  open: first eligible city-day target only
  hold: default after posterior update
  close/reopen: only if close_value + new_value - old_hold_value > buffer
  current_yes: not active in primary set; only for revalue/complement cost
```

关键结果：

- `our_current / v2_first_lock_no_current_yes` verified: 126 rows / 12 dates / ROI +9.7% / CI [-1.5%, +21.4%]。
- `our_current_ext_guard` verified 点估更高且 CI 为正，但只有 39 rows，dev-CV 仍不稳；作为 shadow diagnostic，不作为主策略。
- `v2_rebalance_ev02` 低于 first-lock，说明后续 posterior flip 不能直接当翻仓 alpha。
- ABCD 汇总见
  [2026-07-08-tmax-abcd-comparison-v1.md](analysis/2026-07/2026-07-08-tmax-abcd-comparison-v1.md)：6/21+ 重算后 A/B 概率分母扩到 12 dates / 1,729 rows；
  `mkt_city_source_blend` logloss 0.578 vs A 0.644，`loo_no_city_source_blend` logloss 0.579；外部 raw 仍显著输 market。
- 概率模型 review 见
  [2026-07-08-tmax-probability-model-review-v1.md](analysis/2026-07/2026-07-08-tmax-probability-model-review-v1.md)：模型本体保留；
  `mkt_city_source_blend` 是 proper-score 最优，`loo_no_city_source_blend` 是当前 target-book 执行概率源且表达 EV 更稳。下一步不是换外部模型，
  而是在 zero-notional target-book 里双写 B_score/B_exec，并推进 full-ladder / hazard-chain 概率。
- 第一性原理模型实验见
  [2026-07-08-tmax-hazard-chain-v0.md](analysis/2026-07/2026-07-08-tmax-hazard-chain-v0.md) 和
  [2026-07-08-tmax-full-ladder-v1.md](analysis/2026-07/2026-07-08-tmax-full-ladder-v1.md)：hazard overlay 在 dev-CV 退回 alpha=0；
  direct full-ladder v1 的 dev-CV 也选 alpha=0，不能升格执行。但 forward 诊断 alpha=0.25 在 exact-step / overshoot / tail 切片改善，
  说明方向有信号，下一版应做 monotone survival + 真实 sibling book/source-basis，而不是直接用多分类输出替换 B_exec。
- monotone survival 版见
  [2026-07-08-tmax-full-ladder-survival-v2.md](analysis/2026-07/2026-07-08-tmax-full-ladder-survival-v2.md)：把 full-ladder 改成逐档 conditional survival，
  接入 sibling book / source-basis / cadence 特征。dev-CV 按 bucket logloss 仍选 alpha=0，所以不能升格执行；但 verified-forward
  细 alpha sweep 后 `survival_city` alpha=0.35 同时改善 exact logloss 0.6680→0.6494 和四桶 bucket logloss 0.5791→0.5755，
  bucket delta date-CI [-0.0199,-0.0008]，overshoot/tail/reanchor 切片均同号改善。效应量是小幅校准改善：true bucket 几何均值概率约
  0.5604→0.5624，不是单笔巨大 edge。结论：v2 是当前最强 full-ladder 研究候选，
  下一步应进入 zero-notional target-book 双写验证，而不是直接替换 B_exec。

当前动作：

```text
live_action = none
promotion = no live
next = zero-notional target-book shadow runner / position_book telemetry
```

## 2026-07-10 Lineage Repair

[2026-07-10-tmax-lineage-repair-replay-v1.md](analysis/2026-07/2026-07-10-tmax-lineage-repair-replay-v1.md)
完成 live/replay 同口径修复：共享 tail-aware bracket parser、GFS/ECMWF enrichment、RH/sky/wind observation context、
sibling complement snapshot estimate、direct fresh executable ask、NaN ask 拦截和 blocked telemetry。`1-NO bid`
只作估价/telemetry，不作为当前 BUY YES executor 的可成交 ask。旧 live 缺字段会让 verified-forward proper score 变差：
logloss `0.5887 -> 0.5926`，因此 selected-trade ROI 偶然更高不能作为保留缺字段的理由。

8 笔首批 live order 已按原 PIT snapshot 重放：修复后原表达在原 fill price 上有 6/8 仍过 edge 门；Busan 7/09
`d1_no` 变成负 edge，北京 7/09 `d1_no` 在原 fill 上只差约 0.1c 未过门。live 继续暂停，先积累 repaired forward shadow。

审阅里“below-ladder 是新容量”的结论已纠正。6/21..7/07 的 357 个 repaired below-ladder city-hour 全部能在
canonical event inventory 中找到更低 sibling；另有 157/188 个 top-two 行缺 upper sibling。根因是旧 collector 在
写事实 snapshot 前删除 `outcomePrices <=0.001 / >=0.999` 的 near-binary markets，不是市场本身没有这些档，也不是
survival v2 已经覆盖的新 alpha。collector 已改为保留完整梯子；D1 real-tail/full-ladder A/B 必须等待完整 fresh
snapshot 后重做，旧 701 行匹配样本受缺档污染，不能用于升格。

## 2026-07-11 Coherent Expression Calibrator

[2026-07-11-tmax-coherent-expression-calibrator-v1.md](analysis/2026-07/2026-07-11-tmax-coherent-expression-calibrator-v1.md)
在完整四桶模型之后新增 coherent second-stage calibration，专门修正“全分布更准，但 max-edge 选中子集不准”的目标错位。
YES/NO 仍由同一 `current/d1/d2/tail` 分布推导，保证互补；没有独立训练互相矛盾的 YES/NO 概率。

三个候选只在 6/21 前按 date-equal logloss 选择：纯 global calibration、紧凑 weather context、exact-book quote geometry。
预选 primary 是 `coherent_cal_quote(C=0.3, alpha=0.25)`，输入完整模型四桶概率和 PIT 可执行 ask/bid/spread，不含 city/source
自由参数。6/21..7/08 同分母上：四桶 logloss `0.5887 -> 0.5875`，date-block delta CI `[-0.0045,-0.0003]`；
fee-adjusted first-lock 为 167 笔、ROI `+11.2%`，相对完整模型 `+3.1pp`，paired CI `[+0.6pp,+5.7pp]`。
前后半窗 ROI 均为正且均高于 baseline，16 天有 10 天 PnL delta 为正。

重要边界：它只恢复了上一轮 36 个 cancelled profitable `d1_no` 中的 2 个，说明改善不是针对坏案例硬拟合；但 selected
`d1_yes` 仍为负，winner-selection 条件校准尚未完成。由于模型架构是在看过 6/21+ 研究结果后提出，这段不能再算真正未见
forward，当前等级只能是 `shadow_candidate`，不得恢复 live。

```text
candidate = coherent_cal_quote
execution = zero-notional shadow only
live_action = none
next_evidence = fresh forward exact-book quotes + selected/blocked expression ledger
```

## 为什么不是继续用原来的 live 版本

原来的 `regime_routed_no` 更像 rule-based route：

- `day_open_runway / day_marginal_runway -> current NO`
- `day_forecast_capped -> d2 NO`

它的问题不是“没成交”这么简单，而是表达层不够统一：
天气形态只能说明状态，不应该直接决定买哪一档。

新体系把它拆开：

```text
weather/regime/context = 特征
bucket probability = 模型输出
ask/expression = 执行选择
selected/blocked = shadow 记录
```

这样不会因为某天错了就继续加 gate，也不会因为当前 runner 没成交就误判没有机会。

## State-Transition Card / LLM 前置节点（research design）

2026-07-21 对外部“站点底座＋十类动态天气过程＋日内状态机”的审阅结论是：它与当前
distribution-first 主线高度一致，但新增的是 **状态转变时钟**，不是另一套固定 regime route。

当前 `day_regime`、`intraday_state`、peak clock、remaining heat、cloud/moisture、wind/solar 和
source reliability 描述的是决策时刻的多轴 state；下一版研究应再估计：

```text
P(next_state | PIT state)
P(transition occurs within 1h / 2h / 3h)
transition_time_quantiles
```

日照混合、低云、清云反弹、持续降雨、雷暴冷池、海风、锋面、焚风、逆温和下垫面只作
multi-label diagnostic processes。一天可以经历 `low_cloud -> clearing -> sea_breeze_cap`；process label
不直接决定买 current YES、current NO 或 d1/d2 表达。

推荐的策略前置链路：

```text
PIT raw/source snapshots
  -> deterministic weather_state + transition features
  -> versioned LLM state-card synthesis
  -> calibrated exact-bracket distribution
  -> P(outcome)-market residual
  -> fresh executable expression EV / target book
```

LLM 只负责结构化综合 source conflict、三情景和 invalidation signals；native-unit settlement lattice、
source-to-settlement basis、概率校准、fresh ask/depth、fee 和执行仍由确定性代码负责。LLM 不直接给 live
selector 发单，也不能把自然语言 confidence 当 `p_win`。最终日报里的 exact-bracket probability 和
expression edge 分别由 downstream calibrated model 与 execution evaluator 注入，不由 LLM 填写。

每天不是只生成一篇静态报告。每个 active city-day 应冻结 `morning_map`、`state_confirmed`、
`pre_transition_or_key_report`、`post_update/end_of_day` 四类 checkpoint，并在 TAF amend、METAR/SPECI、
云雨/风转、source conflict 或盘口跳变后追加 event-driven card。全 universe 的 observed/selected/blocked
都要保留，避免 LLM selection bias。

验证必须同 rows A/B：

```text
raw market
vs current Tmax probability model
vs + deterministic transition features
vs + LLM state card
```

先比较 exact/bucket logloss、Brier、calibration 与 transition-time coverage，再比较 fee-adjusted executable
residual。现有 weather-climate forward ablation 没有打败 raw market，因此“日报更完整”不等于 alpha 更强。
当前动作是补 PIT collector 与 zero-notional card；`live_action=none`。

完整映射、card schema 和 acceptance：
[2026-07-21-intraday-state-transition-agent-review-v1.md](analysis/2026-07/2026-07-21-intraday-state-transition-agent-review-v1.md)。

## 当前候选配置

| config | method | edge threshold | 角色 |
|---|---|---:|---|
| `tmax_dist_clean_edge02` | `loo_no_city_source_blend` | 0.02 | 主候选：机制更干净，少依赖 city/source 记忆 |
| `tmax_coherent_cal_quote_v1` | `coherent_cal_quote` | 0.02 | 新模型 shadow candidate：完整四桶概率 + exact-book quote calibration；未接 live |
| `tmax_dist_city_source_edge02` | `mkt_city_source_blend` | 0.02 | 容量/城市源偏移对照：点估更强、交易更多 |
| `tmax_dist_clean_edge10` | `loo_no_city_source_blend` | 0.10 | 高 edge 压力测试：旧样本和 verified 很强，但 recent 变薄 |

## Evidence Summary

### Verified Settlement: settlement-backed forward rows after 2026-07-05 settlement rejoin

这组只统计 `label_source=settlement_outcomes` 的 forward rows。2026-07-04 重新把 atlas 老 shard
接到 Single Runs PIT backfill，2026-07-05 又补齐 7/03-7/04 pm_history settlement 并重跑
atlas/P5/P6 后，点估仍为正；clean primary 的 verified CI 下界刚转正，但这仍是 backfill/rejoin
证据，不是真 P7 fresh-forward。

| config | rows | ROI | CI | 备注 |
|---|---:|---:|---:|---|
| `tmax_dist_clean_edge02` | 226 | +10.5% | [+0.8%, +20.0%] | 主机制候选，唯一 tiny-live 候选，但仍需 P7 fresh-forward |
| `tmax_dist_city_source_edge02` | 236 | +11.3% | [-1.7%, +24.3%] | 点估正，但 city/source 风险更高 |
| `tmax_dist_clean_edge10` | 33 | +34.3% | [-18.7%, +92.6%] | 样本太薄，只作压力测试 |

### Observed-Max Pressure Test: remaining non-settled / non-four-bucket rows

当前 P6 feature/state layer 只覆盖到 7/03，且 7/03 已经由 pm_history settlement 验证；
因此本轮 `extension_forward = 0`。7/04 已有 settlement_outcomes，但还没有进入 atlas state rows，
不能算作 P6 分母。

| config | rows | ROI | CI | 备注 |
|---|---:|---:|---:|---|
| `tmax_dist_city_source_edge02` | 0 | n/a | n/a | 无 extension rows |
| `tmax_dist_clean_edge02` | 0 | n/a | n/a | 无 extension rows |
| `tmax_dist_clean_edge10` | 0 | n/a | n/a | 无 extension rows |

结论：

```text
conclusion = inconclusive_positive_signal
promotion = no live
next_action = zero-notional shadow
```

## Runtime

当前 shadow runner：

```bash
.venv/bin/python scripts/ops/tmax_distribution_edge_shadow_v1.py run
```

循环运行由 production contract/controller 管理，历史直启 wrapper 已删除：

```bash
.venv/bin/python scripts/ops/weather_production_ctl.py health
```

当前本机 shadow loop：

```text
tmux session = tmax_distribution_edge_shadow_v1
mode = zero-notional shadow
orders = none
```

默认 runtime 目录：

```text
runtime/weather_edge_v1/tmax_distribution_edge_shadow_v1/
```

关键文件：

| 文件 | 含义 |
|---|---|
| `shadow_events.jsonl` | append-only shadow event journal |
| `latest_events.json` | 最近一轮完整 selected/blocked events |
| `latest_summary.json` | dashboard / registry 可读摘要 |
| `summary_history.jsonl` | 每轮 summary 历史 |
| `shadow_loop.log` | start script 循环日志 |

当前 materialized source refresh：

```text
source = docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv
source_rows = 16416
source_date_range = 2026-06-02..2026-07-03
selected_rows = 1562
blocked_rows = 14854
```

这证明 P6 source 已经刷新到当前可评分分母；runtime loop 是否已消费这批 source 需要看
`runtime/weather_edge_v1/tmax_distribution_edge_shadow_v1/latest_summary.json`，不能用旧 journal 数字替代。

Runtime registry 已接入：

```text
strategy_instance = tmax_distribution_edge_shadow_v1
lifecycle_status = shadow
execution_mode = zero_notional_shadow
health_status = healthy
shadow_rows = 90
telemetry_rows = 90
blocker_count = 0
```

重要边界：`tmax_distribution_edge_shadow_v1` 这个旧 shadow loop 消费的是 P6 materialized source：

```text
docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv
```

这已经足够验证 runtime journal / dashboard registry / selected+blocked event contract，
但它不是完整的 current-day live snapshot selector。current-day 入口现在由
`tmax_distribution_edge_live_candidate_v1` 负责：

```text
current snapshot + observation cache -> tmax distribution candidates -> latest_candidates/latest_blocked/trade_plans -> weather_order_executor
```

## Tiny-Live Candidate Policy v1

2026-07-05 收敛出的执行候选：

```text
strategy_id = tmax_dist_clean_edge02_tiny_live_v1
runtime_state = zero_notional_shadow_only
model_config = tmax_dist_clean_edge02 / loo_no_city_source_blend
expression_set = current_yes / current_no / d1_no / d2_no
selector = max(p_win - ask)
edge_threshold = 0.02
ask_floor = 0.20
shares = fixed 5 shares
dedupe = first accepted per city + target_date
daily_time_order_cap = none
mechanism_filter = no_trend3h_flat
trend3h_flat = -0.5F <= temp_trend_3h_f < +0.5F
missing_trend3h = block for live candidate; record in blocked telemetry
```

`no_trend3h_flat` 是 row-level mechanism filter，不是整天 veto：如果上午 3h flat 被 block，
后面同 city-day 重新升温并再次出现合格信号，可以重新评估。这个避免把“早盘蓄势 flat”和
“峰值附近熄火 flat”混成一个永久 city-day 禁止项。

当前 candidate runner：

```bash
scripts/ops/start_tmax_distribution_edge_candidate_shadow_v1.sh
```

默认输出：

```text
runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1/latest_summary.json
runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1/latest_events.json
runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1/latest_blocked.json
runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1/summary_history.jsonl
```

Realtime candidate bridge 已接入：

```text
current snapshot -> candidate event -> fresh CLOB ask -> weather_order_executor
```

默认仍是 paper executor，不会真实下单：

```bash
.venv/bin/python scripts/ops/tmax_distribution_edge_live_candidate_v1.py run --execute --max-orders 1
```

上式仅用于离线/单次研究复现；常驻或生产状态变更必须走 controller，不再提供独立启动脚本。

默认 runtime：

```text
runtime/weather_edge_v1/tmax_distribution_edge_live_candidate_v1/
```

真实 live 边界：

```text
default = paper_executor_only
live requires = --live --confirm-live
start script requires =
  TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_LIVE=1
  TMAX_DISTRIBUTION_EDGE_LIVE_CANDIDATE_CONFIRM_LIVE=1
```

执行前检查：

```text
before order:
  refresh CLOB orderbook
  use fresh best ask, not best bid
  require fresh ask size >= 5 shares
  require fresh ask <= snapshot ask + 0.02
  require p_win - fresh_ask - 0.05 * fresh_ask * (1 - fresh_ask) >= 0.02
exit = hold to settlement
```

2c 磨损测试是 taker 保守压力测试，不是 maker 预期。Maker 可能改善价格，但会引入
fill selection bias：能成交的票可能正是价格朝我们不利方向移动的票。因此 maker-first
不能直接把 2c 当收益加回去；必须单独记录 maker quote、成交率、未成交反事实和成交后 PnL。

## Event Contract

每条 shadow event 至少包含：

- `shadow_event_id`
- `shadow_schema_version`
- `strategy_instance`
- `shadow_config_id`
- `selection_policy`
- `zero_notional`
- `no_order_placed`
- `selection_status`
- `selection_reason`
- `city_day_eligible_rank`
- `scope`
- `city`
- `target_date`
- `decision_hour_local`
- `method`
- `chosen_expression`
- `ask`
- `p_win`
- `model_edge`
- `actual_bucket`
- `label_source`
- `day_regime`
- `intraday_state`
- `running_max_state`

当前 `selection_policy = first_eligible_city_day`。
`selected` 和 `blocked` 都必须保留。这是这个体系的硬要求，因为后续需要验证
“没选的机会是不是其实更好”，同时避免把 hourly 诊断行误当成实际会重复下注的回测行。

## 和当前没成交 live runner 的关系

`regime_routed_no_soft_balanced_tiny_live_v1` 是旧 rule-route 体系。
它最近没成交，可能来自：

- 盘口/最小股数/金额约束；
- fresh feature / snapshot 缺口；
- route-price discipline 后候选变少；
- 策略本身表达过窄。

但这个现象不能直接用来判断 `tmax_distribution_edge`。
新体系现在只做 zero-notional forward 取证，不依赖实际成交。

## Promotion Gates

这条线要从 shadow 升级，至少需要：

1. 真 forward shadow 连续运行。
2. official settlement 补齐，而不是 observed-derived label。
3. depth/fill feasibility replay，不能只看 ask。
4. 至少 10 个新 settled forward dates。
5. 每个主 config 至少 80 个 settled selected events。
6. 相对 market/local baseline 或同分母 no-trade baseline 有正超额。
7. 日块 bootstrap CI 不跨 0。
8. 明确最大单日亏损和容量。

没过这些之前，不改 live、不 size-up。

## 下一步

当前已经完成：

- research synthesis
- shadow event contract
- runtime shadow runner
- smoke test
- runtime registry / dashboard visibility

下一步：

1. 等 atlas / orderbook / settlement 更新后，用 `--refresh-source` 或上游定时 job 刷新 source artifact。
2. 跑连续 fresh-forward shadow。
3. 补 official settlement 后重算 selected/blocked 的真实结果。
4. 再决定是否需要做 depth/fill-aware shadow replay。

## Related Artifacts

- Report: `docs/analysis/2026-07/2026-07-03-tmax-distribution-research-synthesis-v1.md`
- Shadow telemetry report: `docs/analysis/2026-07/2026-07-03-tmax-distribution-p6-shadow-telemetry-v1.md`
- Runner: `scripts/ops/tmax_distribution_edge_shadow_v1.py`
- Runtime owner: production contract + `weather_production_ctl.py`
- Runtime journal: `runtime/weather_edge_v1/tmax_distribution_edge_shadow_v1/shadow_events.jsonl`
