# Tmax Distribution v3 Model Work Order

## 目标

请完成一个可运行、可回放、可进入 zero-notional shadow 的 `tmax_distribution_v3`，而不是输出一组彼此独立的研究报告。

模型要在每个决策时刻，根据当时可见的观测路径、预报、天气机制和盘口，估计当日最高温落入完整 exact-bracket ladder 的概率分布，再在真实 ask、fee、容量和已有持仓成本下产生 target position。最终必须交付冻结模型 artifact、同口径回放、逐笔血缘、shadow runner 输入契约和明确 verdict。

## 已有事实

- 干净 single-snapshot 数据有 8,094 个状态，覆盖 2026-06-19..2026-07-10；评分使用 `below/current/d1/d2/tail` 五桶，执行按每 city-day first eligible、5 shares、fresh direct ask、官方 fee。
- 当前最小 clean fusion 在 2,777 个 paired rows 上明显输给 market：logloss 0.6043 vs 0.4984；执行 275 笔、ROI +3.38%，date-block CI [-4.23%, +9.39%]。它不能作为最终模型。
- 旧 `historical_full_features` 为 159 笔、ROI +8.08%；`coherent_cal_quote` 为 167 笔、ROI +11.19%，且相对旧 full-feature 的 paired delta 为 +3.11pp（date-bootstrap CI [+0.63pp, +5.73pp]，方向稳定）。但旧窗口被反复观察、部分特征血缘混杂，不能直接当 fresh-forward 结论。
- 完整逐档 quoted absolute ladder 极薄（7/11 fusion-v2 baseline 审计：complete quoted state 仅 72，带 label 47，pre-cutoff 30 行/11 天）。因此 full-ladder 是**模型输出空间**（物理 rung 由 anchor + 单位步长展开，label 来自 settlement，不依赖全档报价），但 market prior 的完整 ladder 特征在大多数 state 上不完整；主对比分母仍是五桶 / available-rung paired，全档完整报价子集只作 secondary 审计，不得当 primary 证据。
- 当前 clean 模型不是因为“限制更多”而变差，而是只保留了少量可严格证明 PIT 的路径特征，丢失了 quote geometry、天气状态、source calibration 等有效信息；同时它用弱的固定五桶独立拟合表达 remaining heat。
- strict PIT 可恢复：同一 snapshot 的 bid/ask/size/spread/5c/10c depth、saved ladder geometry、forecast source、forecast curve、running-max 路径、1h/3h trend、peak clock、freshness。
- report-time 可重建但不是严格 live-first-seen：humidity/dewpoint/cloud/sky/wind。只能作为研究上界，不能和 strict PIT 主结果混称。
- 双 GFS+ECMWF 历史同刻覆盖约 2.8%，且基本只有 6/19-6/20，无法回答 forward 增量；不得插值后发布性能。（该覆盖率与下条 16 分钟中位泄漏两个数字来自上一轮会话，仓库文档未固化；执行时先用当前数据重测并把结果写进报告，再引用。）
- 不允许按 `city/date/hour` 直接连接 atlas。必须按 feature timestamp `<= decision timestamp` as-of；直接连接存在未来泄漏（上轮测得中位约 16 分钟，需重测确认）。

## 现有资产（必须复用/对齐，不得另起平行数据链）

- 状态分母 materializer：`scripts/etl/materialize_tmax_v2_canonical_state.py`（8,094 clean states 的产地口径）。
- PIT 特征契约：`src/strategies/weather_edge_v1/tools/tmax_feature_contract_v2.py` + `weather_data_feed/city_mechanism_profiles.py`（已有单测 `tests/pmm_tests/test_tmax_feature_contract_v2.py`）。新特征先扩这份契约，不另写一套 builder。
- 同口径 replay 参照：`scripts/analysis/reheat_risk/research_tmax_single_snapshot_lineage_replay_v2.py`（8,094/2,777 分母与"已有事实"数字的产地）。
- hazard / ladder / target-book 先例：`research_tmax_hazard_chain_v0.py`、`research_tmax_full_ladder_survival_v2.py`、`research_tmax_target_book_v2.py`、`research_tmax_target_book_rebalance_v1.py`、`research_tmax_coherent_expression_calibrator_v1.py`（同目录）。v3 是把这些收敛成一个 primary，不是再加一条并行研究线。
- shadow runtime 入口：`scripts/ops/tmax_distribution_edge_shadow_v1.py` / `tmax_distribution_edge_live_candidate_v1.py`（7/11 已修成 PIT-safe）。zero-notional shadow adapter 挂这条 runtime。
- 开始前先同步数据并重建到最新已结算日期：`scripts/ops/sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only`，然后按 `weather-fact-rebuild` 口径 rebuild；分母窗口允许比 7/10 更新，冻结时写明截止日期。

## 要构建的模型

### 1. 概率主体

用逐档 survival / competing-risk hazard 表达剩余升温：

- `P(reach next bracket | current path and remaining heat)`
- `P(reach k+1 | reached k)`
- `P(stop at k | reached k)`

输出完整 ladder 分布，再聚合为 five-bucket 仅用于与旧模型对比。模型更新必须以前一时刻 posterior 和“这一小时未突破/已突破”的新证据做条件化，不能每小时独立重拟合后任意翻向。

### 2. 特征分层

按以下顺序做固定增量消融；每层必须在同一 paired denominator 上比较 proper score，不按 ROI 挑特征。

1. `market_prior`：完整可见 ladder 的归一化概率、bid/ask interval、spread、depth、overround、entropy、boundary position。
2. `path_energy`：running max、current-running gap、1h/3h trend、minutes since max、forecast peak clock、hours past/to peak、forecast ceiling margin、forecast 曲线剩余加热积分、今日路径相对 forecast curve 的 tracking residual。
3. `source_calibration`：城市固定 forecast source、source 历史误差的层级收缩、unit/settlement basis；禁止 per-city 自由记忆参数。
4. `weather_mechanism`：humidity/dewpoint/cloud/wind/solar-time 及交互，只在严格可得的子集训练主候选；archive 重建版本只报 upper bound。重点检验云量对太阳加热、湿度/露点对升温效率、风向与沿海/内陆/站点地形对 mixing/sea-breeze 的影响，而不是只放一个风速字段。
5. `temporal_coherence`：前一时刻 posterior、elapsed time、new observation evidence 和 hazard survival update。

类别变量使用 climate family/source 的层级收缩；城市只作 out-of-group 诊断，不作为自由 one-hot alpha。

### 3. 市场融合和校准

- weather/path 模型先独立输出分布，再与 market prior 融合；alpha 只在训练窗口用 proper score 选择，候选预注册为 `{0, .25, .5, .75, 1}`。
- quote calibrator 只能校准概率，不得直接用已实现 ROI 选择表达。
- 同时报告 market、weather-only、market+path、market+path+source、完整 strict-PIT、archive upper-bound。
- 盘口不完整时保留 missingness flag；不得把缺失 rung 当成概率 0，也不得称 saved ladder 为完整市场梯子。

## 评估设计

- 使用 date-expanding walk-forward；每个 target date 只能用更早日期训练。再补一组 trailing-window 稳健性检查。
- 明确标注证据等级：整个 6/19 以来的窗口都已被反复观察，所有 in-window 结果一律标 `retrospective_diagnostic`；fresh-forward 证据只能来自本任务交付后 shadow runner 前向积累，verdict 里不得把 in-window walk-forward 说成 forward 已过。
- 冻结相同状态分母、相同 settlement label、相同 expression universe 和执行规则。
- 主指标：date-equal multiclass logloss、Brier、calibration curve，以及相对 market ask-side envelope 的增量。
- 交易指标：5-share、fresh taker ask、真实 size>=5、官方 fee、每 city-day target-book。报告笔数、胜率、费后 PnL/ROI、date-block CI、逐日、YES/NO、expression、unit、source、climate family、top-days removed 和最大回撤。
- first-lock 只作为旧策略可比基线。最终策略必须另做 position-aware target-book replay：同一 city-day 后续 posterior 变化先计算继续持有、减仓、平仓、翻仓的增量 EV，换仓按 bid/ask、双边 fee 和 adverse-selection buffer 计成本。
- Lucknow 2026-07-05 必须作为固定案例回放：输出每次决策的全 ladder 概率、旧/新 target position、交易成本和为何不会出现无持仓血缘的 YES/NO 自撞。

## 模型选择规则

最终只允许选择一个 primary：

1. 在 paired walk-forward 上 proper score 稳定优于 market，而非只看 selected ROI。
2. 相对上一层的增量在日期 bootstrap 下方向稳定；若 CI 跨零，可保留能改善 calibration 且机制明确的特征，但不得宣称 alpha 已证实。
3. fee-adjusted ROI 不能由单一 side、单一城市、少数日期或高 edge 尾部独占。
4. strict-PIT primary 和 archive upper-bound 必须分开；upper-bound 更好只说明值得补采，不得进入 live artifact。
5. 不加追逐坏 case 的 gate，不通过调 ask/edge 阈值挑最好 ROI；阈值和执行政策在模型比较前冻结。
6. 若没有候选同时通过 baseline、significance、fresh-forward 三道门，也必须交付表现最好的 clean artifact 为 `shadow_candidate`，并明确唯一缺口；不能再拆出新的 P1/P2/P3 研究链结束任务。

## 必须交付

- 一个版本化模型包：feature builder、fit/predict、概率 artifact schema、模型卡。
- 一个统一 replay 脚本，一次生成所有 baseline/ablation/执行/稳定性表。
- 一个 zero-notional shadow adapter，复用共享 data feed 和 strategy runtime，不创建平行数据链。
- 一份综合报告，开头直接回答：新模型是什么、比当前 clean/旧 full/coherent 各改善多少、改善来自哪些特征、哪些只是假设、能否 shadow/live。
- 测试至少覆盖 PIT as-of、单位/basis、缺失 rung、概率和为 1、跨小时条件化、target-book dedupe、Lucknow 回放。

硬边界：不恢复 tmax live、不改 live config、不真实下单；不使用未来观测、事后 forecast backfill 或事后城市表现选特征；发现数据链 bug 先修根因并量化影响窗口。
