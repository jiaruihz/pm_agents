# 观测更新后 Ladder Repricing 新策略研究提示词 v2

生成日期：2026-07-18

状态：本文件取代 v1 作为“新策略”研究目标；v1 的 cross-NO 结果保留为事件时钟与基准腿审计，不是目标策略。

## 大白话目标

当一份新的、事前可见的观测把 running max 从 `T-1` 推到 `T` 时：

- `T-1 NO` 只是最容易判定的基准腿，用来确认信息何时到达、盘口何时开始重定价；
- 真正要研究的是更新以后，`T YES/NO`、`T+1 YES/NO`、`T+2 YES` 等其他腿有没有反应过度、反应不足或更新不同步；
- 结合剩余加热窗口、路径状态、天气机制、source 质量和盘口结构，找一个能在少数城市先冻结验证、之后再扩城的概率 residual。

不要把任务再次做成“cross 后买旧档 NO”。

## 可直接交给独立任务执行的提示词

你在 `/Users/deepsleep/projects/pm_agents` 中执行一项 city-first、event-time-first 的天气策略研究：

```text
以 running-max 更新事件为时钟，研究更新后 exact-bracket ladder 的概率重新分配和可执行表达。
```

必须使用 `weather-strategy-research` skill，并读取：

- 项目 `AGENTS.md`
- `docs/WEATHER_ANALYSIS_CONTRACT.md`
- `docs/WEATHER_STRATEGY_QUANT_DESIGN.md`
- `docs/WEATHER_STRATEGY_REGISTRY.md`
- `docs/analysis/post_cross_repricing.md`
- `docs/analysis/2026-07/2026-07-14-source-event-denominator-audit-v2.md`
- `docs/analysis/2026-07/2026-07-14-source-event-expression-denominator-v3.md`
- `docs/analysis/2026-07/2026-07-18-cross-no-city-mechanism-event-time-repricing-v1.md`

实际执行研究、运行脚本并交付结果，不要只写计划。

## 1. 事件与目标表达

事件定义：一份 official/live-eligible 或已校准 proxy source 的新观测，第一次在 PIT first-seen 口径下把当日 running max 从 `T-1` 推到 `T`。

事件 grain：

```text
(city, target_date_local, source_profile, source_observation_ts_utc,
 source_first_seen_ts_utc, previous_bracket=T-1, current_bracket=T)
```

每个事件建立同一个 sibling ladder 面板：

| 表达 | 正确语义 | 研究角色 |
|---|---|---|
| `T-1 NO` | 旧档已失效或高概率失效 | 事件时钟、信息吸收基准；不是目标策略 |
| `T YES` | 最终最高温正好停在 T | stop/exhaustion 表达 |
| `T NO` | 已到 T 后最终继续到至少 T+1 | overshoot/continuation 表达 |
| `T+1 YES` | 最终正好停在 T+1 | one-step continuation 后停止 |
| `T+1 NO` | 最终不是 T+1，混合 stop-at-T 与 overshoot-above | 只作补充，不默认当干净方向表达 |
| `T+2 YES` | 最终正好停在 T+2 | two-step tail 表达 |

先研究三个连续概率目标：

```text
P(final = T | reached T, PIT state)
P(reach >= T+1 | reached T, PIT state)
P(final = T+1 | reached T, PIT state)
```

再与同一事件、同一时刻的真实 market probabilities 比较 residual。不要先按 ROI 选择某条腿。

## 2. 已知失败，禁止重复包装

已有 generic-cross 宽分母结果：current YES、current NO、d1 YES、d1 NO 均未显示可扩 alpha；current YES 约 -8.9%、current NO 约 -3.5%、d1 YES 约 -23.2%、d1 NO 约 -2.3%。这说明“出现 cross 就固定买某条腿”不成立。

本任务要检验的是：

```text
城市/来源机制
+ 剩余加热与路径状态
+ sibling ladder 更新不同步
=> 相对同一时点 market 的连续概率 residual
```

不能再做全城市 pooled 固定表达，也不能通过追加价格带和多个 AND filters 把 generic 失败切成几个赢家。

## 3. 首轮只做少数城市

先用已有 v1 source scorecard 和最新 raw 重新核验，最多冻结两个主城市加一个负面对照。选择不能看目标表达的 ROI。

城市评分至少包括：

1. source→official/settlement 准确度及 single/persistent 差异；
2. observation→first-seen→collector detect 延迟；
3. direct `T/T+1/T+2` sibling book 覆盖、cadence、spread、depth；
4. exact bracket、单位、取整、local date 是否干净；
5. PIT forecast/path/weather 特征覆盖；
6. 独立 active dates 和 event 数。

当前 Helsinki/FMI 可作为首个候选，因为 first-seen 和 source calibration 相对干净；Tokyo/JMA、Busan/AMOS 可进入对照评分。最终仍以最新 scorecard 冻结，不因历史 fills 或 ROI 优先。

authoritative Moscow/HKO 如果仍没有 first-seen→sibling-book episode，只记录 coverage gap，不得为了语义漂亮强行进入交易分母。

## 4. 特征必须围绕更新后的概率重新分配

### 天气与路径特征

- forecast peak clock、距峰值分钟数、剩余太阳加热窗口；
- fresh runway / plateau / pullback / fade；
- 最近 1h/3h 升温趋势、minutes since previous max、cross margin；
- forecast ceiling margin、剩余加热积分；
- 云量、降雨、湿度/露点、风速风向及海风/混合机制；
- source cadence、age、single/persistent、source→official basis。

所有特征必须在 decision timestamp 前 first-seen。未来 METAR/WU/settlement 只能作 label。

### 盘口特征

- 事件前最后一份以及 first-seen 后 `0/30/60/120/300s` 的 direct YES/NO bid、ask、spread、size；
- `T-1/T/T+1/T+2` 各腿更新的先后顺序和相对幅度；
- crossed leg 已归零但 current/tail 未更新的 asynchronous state；
- ladder probability mass、overround、entropy、相邻档价格斜率；
- 当前腿与 d1 腿的 residual redistribution；
- book age、snapshot cadence、top-level 1/5/10-share capacity。

`T-1 NO` 的价格只能度量市场是否已经吸收事件，不能作为事后挑选目标腿盈利样本的 hard gate。

## 5. 固定研究问题

必须逐项回答：

1. **current underreaction**：cross 后、剩余加热低时，市场是否低估 `T YES` 的 stop probability？
2. **current overreaction**：cross 后仍有强 runway 时，市场是否高估 `T YES`、低估 `T NO` 的 overshoot probability？
3. **d1 lag**：市场先杀旧档、抬 current，但是否没有及时抬高 `T+1 YES`？
4. **asynchronous ladder**：sibling legs 的更新顺序是否提供短暂、可执行的相对价值，而不是单腿方向预测？
5. 哪些城市/来源机制下存在该现象，哪些只是 source basis 或低频盘口造成的假象？

如果数据只支持某一个问题，就明确缩小结论；不要把缺失问题写成通过。

## 6. 评估顺序

1. 固定城市、事件和 expression denominator，列 signal/evidence 双漏斗。
2. 对每条目标概率做 expanding/OOF 预测，与同 rows 的 market 做 logloss、Brier、calibration 和 residual。
3. 只在 proper score 不输 market 后，才用 fresh direct ask、官方 fee、1c buffer 和真实 size 做交易表达。
4. 同一事件比较 `T YES`、`T NO`、`T+1 YES`，不能各自挑不同的最好 rows。
5. 按 target date block bootstrap；报告 active dates、城市集中度、top-days removed。
6. retrospective 只用于冻结模型/表达；fresh forward 从冻结后开始，不能混称。

基线至少包括：

- 同城市、同 local-time、相近 market probability 的 non-cross state；
- generic cross 的同表达结果；
- market-only probability；
- 只有 event flag、没有天气/路径特征的模型。

## 7. 数据与代码资产

优先复用：

- `scripts/analysis/market_structure_edge/research_post_cross_repricing_v0.py`
- `scripts/analysis/market_structure_edge/research_source_event_hazard_router_v1.py`
- `scripts/analysis/market_structure_edge/research_source_event_expression_denominator_v3.py`
- `scripts/analysis/market_structure_edge/research_cross_no_city_event_time_repricing_v1.py`
- `/Volumes/jrs/weather_data_feed_service_runtime/output/source_event_ladder_repricing_shadow`

当前 collector 已保存 `T-1/T/T+1/T+2` YES/NO，先核验实际字段和 cadence，再决定是否需要补代码。不要另建平行事实链。

分析最新窗口前核对 Mac raw 和 canonical settlement coverage；缺数据按项目约定增量同步，不能静默带缺口下结论。全量 rebuild 仍需用户明确同意。

## 8. 必须交付

1. 冻结的首轮城市与选择 scorecard；
2. `event × expression × horizon` 面板；
3. current-stop、overshoot、d1-stop 三个概率目标的 market baseline 与 OOF 对比；
4. weather/path/source/market-geometry 的同分母 ablation；
5. 各表达真实 ask、fee、depth 下的执行结果；
6. asynchronous repricing 案例和完整反例清单；
7. signal/evidence 双漏斗与 coverage gaps；
8. 可复跑脚本、报告和明确 verdict。

报告第一段必须用大白话回答：

> 更新以后到底该交易哪条腿、在什么物理状态下、为什么市场来不及或定价错了、系统真正看到时还剩多少 edge；如果现在回答不了，缺的是样本、天气 PIT 特征，还是盘口 cadence？

最终动作只能是：`continue_collector`、`freeze_city_expression_shadow`、`reject_expression` 或 `reject_post_update_direction`。没有通过同分母 market baseline、fee-adjusted execution 和 fresh-forward，不得改 live。
