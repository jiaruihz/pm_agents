# Cross-NO 城市机制与 Event-Time Repricing 研究提示词 v1

生成日期：2026-07-18
用途：可直接复制给一个独立 Codex 任务执行
边界：研究 / collector / zero-notional shadow；不改 live、不部署、不真实下单

## 一句话目标

不要再从“哪座城市历史 ROI 好看”出发。先找出少数**观测来源、结算规则、更新时间和盘口时间戳都足够干净**的城市，研究一条观测首次出现后，旧温度档位的 `NO` 在盘口上用了多久才完成重定价，以及这个时间差在真实 ask、fee、深度和检测延迟后是否还能执行。

## 可直接执行的任务提示词

你现在在仓库 `/Users/deepsleep/projects/pm_agents` 中，负责深入研究：

```text
cross-NO 的城市机制选择 + 观测链 event-time repricing
```

这不是一次“全城市扫阈值找高 ROI”的回测，也不是重新研究天气预测模型。你要完成一项 **city-first、mechanism-first、event-time-first** 的研究，先把一个或两个城市的机制做干净，再决定是否值得横向扩展。

已有研究给出的起点（执行时仍需从产地文件复核）：

- generic bracket advance 不是 fast-source 事件。旧审计中 518 个 generic crosses、499 个可执行 current-NO 的整体 ROI 为负，不能再包装成广谱策略。
- generic previous-NO 在已覆盖样本中的平均 ask 约为 `0.9989`，基本已经重定价；买这种票不是本研究目标。
- 早期 42 行正收益结果只是稀疏、模型筛出的子集，不能作为城市机制已成立的证据。
- profile-matched fast-source first-seen 的持续采集起步较晚，已结算重叠日期很少；样本覆盖缩小时应继续补 collector，不能把少量剩余事件称为“精筛策略”。

开始前必须读取并遵守：

- `AGENTS.md`
- `skills/weather-strategy-research/SKILL.md`
- `docs/WEATHER_ANALYSIS_CONTRACT.md`
- `docs/WEATHER_STRATEGY_QUANT_DESIGN.md`
- `docs/WEATHER_DATA_CANONICAL_SOURCES.md`
- `docs/analysis/post_cross_repricing.md`
- `docs/analysis/2026-07/2026-07-14-source-event-denominator-audit-v2.md`
- `docs/analysis/2026-07/2026-07-15-high-frequency-strategy-eligibility-v2.md`
- `docs/analysis/2026-07/2026-07-15-airport-observation-source-integration-v1.md`

不要只写研究计划。实际检查当前数据、完成可复跑分析、生成中间表和报告，并给出明确 verdict。除非遇到真实的不可逆操作或方向岔路，不要在中途停下来问。

## 1. 先把“cross-NO”分成两种机制

两类机制必须分开建分母、打标签、汇报，禁止混池：

### A. `authoritative_invalidated`

观测来自该 market 规则认可的官方结算源，且在正确的单位、取整和 exact-bracket 规则下，新出现的当日最高温已经不可逆地越过旧档上界。

例子：旧档最终最高温必须正好是 `T`，官方累计最高温已经打印到 `T+1`，那么旧档 `T YES` 已输、`T NO` 已赢。这里不需要再预测当天最终最高温，也不需要天气模型估算胜率。

但“结算已经确定”不等于“交易一定赚钱”。仍必须计算真实可执行收益：

```text
net_edge_per_share = 1 - direct_NO_ask - official_fee - execution_buffer
```

如果首次检测时 `NO ask` 已经接近 1，或者盘口深度不足，就没有可执行 edge。

### B. `proxy_confirmed_cross`

观测来自更快但不是结算权威的来源，例如邻近站、机场同场的另一套系统或参考观测。它只是在提前预告官方结算源之后可能跨档。

这类不能把 `p_win` 当成 1，必须测：

```text
p_confirm = P(官方/结算口径随后确认跨档 | 当时可见的快源信息)
net_edge_per_share = p_confirm - direct_NO_ask - official_fee - execution_buffer
```

必须同时报告 single-print cross 与 persistent cross 的准确度、置信区间和所有 false-cross。不能用未来 METAR、未来 WU 或最终 settlement 当作事前特征。

## 2. 首批城市不能按 ROI 挑

先给所有有数据的 `city × source × settlement rule` 做机制 scorecard，再冻结首批研究对象。scorecard 至少包含：

1. **结算一致性**：实际结算权威源 / 同站同规则 / 同机场代理 / 邻近参考源。
2. **档位语义**：`exact_1c`、`range_2f` 等规则是否已正确实现；单位、取整、日界线和 bracket 上下界是否无歧义。
3. **源更新质量**：理论 cadence、实际 cadence、observation time 到 first-seen 的检测延迟、revision/late report 比例。
4. **跨档准确度**：single-print 与 persistent cross 对下一份官方观测、最终 settlement 的 precision 及区间。
5. **盘口覆盖**：event 前后是否有足够密的 direct YES/NO book；盘口采样 cadence 是否小于潜在领先窗口。
6. **可执行性**：检测时 direct NO ask、spread、ask size、5/10-share depth、book age 和 fee 是否可得。
7. **独立样本**：active dates、city-days、distinct source events，而不是重复 poll 行数。
8. **时间戳可信度**：`observation_ts`、`source_first_seen_ts`、`collector_detected_ts`、`book_snapshot_ts` 能否严格排序；是否存在 fallback、回填或事后重建。

评分完成前不得查看分城市 ROI 来决定谁进入主样本。历史 PnL 只允许在城市和机制冻结后作为结果指标。

### 当前候选池与启动建议

以下只是截至 2026-07-15 的先验，执行本任务时必须用最新 Mac raw 重新核对，不能把旧数字当最终结论：

- `Moscow / WRH / UUWW`：历史 settlement replay 对规则源高度一致，适合审计 `authoritative_invalidated`，但 source→book 的实时领先与 first-seen 质量尚未证明。
- `Hong Kong / HKO`：官方独立结算体系，语义可能很干净，但不能套通用 METAR cross，必须单独实现/验证规则映射。
- `Helsinki / FMI / EFHK`：当前 exact-1C、同机场代理候选中，更新和持久跨档证据相对最完整，适合 `proxy_confirmed_cross` 主样本；样本仍小。
- `Tokyo / JMA / RJTT`、`Busan / AMOS / RKPK`：可进入 scorecard，但既有 false-cross 与 source-basis 问题必须逐条复核，不能因为曾产生交易就优先。
- `Singapore / MSS / WSSS` 或 `Istanbul / MGM`：可作为负面对照，前者更新快但 source basis 较弱，后者检测延迟和确认率较差。
- 美国城市的 `range_2f` 必须走正确的 2°F range handler；在此之前不能与 exact-1C 城市混做主研究。

首轮最多冻结：

- 1 个 `authoritative_invalidated` 城市：从 Moscow/Hong Kong 中按时间戳和盘口覆盖选择；若两者都不合格，明确判定该轨道当前数据不足。
- 1 个 `proxy_confirmed_cross` 城市：优先检验 Helsinki，但最终仍以 scorecard 决定。
- 0–1 个负面对照：仅在现有数据足够时加入 Singapore 或 Istanbul，不为凑对照补一条平行数据链。

禁止首轮把所有城市一次性打包。两个机制轨道也不得合并算一个总胜率或总 ROI。

## 3. 锁死 event grain 和 PIT 时间

主事件粒度为：

```text
(
  city,
  target_date_local,
  source_profile,
  source_observation_ts_utc,
  source_first_seen_ts_utc,
  previous_official_running_bracket,
  previous_market_condition_id
)
```

要求：

- 同一 source observation 被 collector 重复轮询只能算一个事件。
- `source_observation_ts` 是观测发生/报告时间；`source_first_seen_ts` 是本系统首次真正看到它的时间；两者不能互换。
- 决策时钟从 `source_first_seen_ts` 或更保守的 `collector_detected_ts` 开始，绝不能从事后知道的 report time 假装成交。
- 对事件前取最后一份 book，对事件后取第一份真实可见 book；不得按未来价格反推触发时间。
- 如果 book cadence 只有 30 秒，就只报告其实际支持的 horizon，不得伪造 5 秒或 15 秒反应。
- 同一 city-day 的多次阶梯推进要保留为不同 source event，但还要另报每 city-day first event，避免高频城市在统计上过度加权。

event ledger 至少要有：

- 城市、站点、source profile、source/settlement 关系、机制类别。
- target date、本地日界线、原始温度、标准化温度、单位和取整规则。
- 旧 running max、旧 bracket 上下界、新观测、cross margin。
- single/persistent 标记；persistent 的定义必须在看结果前冻结，例如连续两份独立观测或持续超过一个源 cadence。
- observation、first-seen、detect、book 时间及各段 latency。
- source status、fallback/revision/stale 标记。
- 下一份官方观测是否确认、最终 settlement 是否确认、false-cross 原因。
- event 前 book、检测时第一份可见 book，以及实际可覆盖的 `30/60/120/300s` book。
- 旧档 direct NO bid/ask、spread、ask size、5/10-share VWAP、book age、官方 fee。
- sibling ladder 的可见报价几何，只作盘口状态解释，不能替代 direct NO ask。

## 4. 天气特征在这里怎么用

本研究不是拿天气特征继续堆 hard filter。天气特征只承担三个明确角色：

1. **判断 source 是否可信**：站点距离、海陆/城市微气候、海拔、机场同场关系、传感器单位和取整方式。
2. **解释 proxy false-cross**：云量、降雨、风向/风速、湿度、海风切换、剩余峰值时钟、观测是否短暂 spike 等是否导致快源与官方源分叉。
3. **解释 market reaction 异质性**：事件发生时是 fresh runway、plateau、pullback 还是 fade；事件是否接近本地峰值时刻；市场是否可能把一份短暂观测当成噪声。

对于 `authoritative_invalidated`，这些天气特征不能反过来决定“旧档 NO 是否已经赢”：一旦官方结算规则下旧档已被排除，天气预测不再改变标签。它们只能解释事件到达频率、市场为何反应快慢，或帮助提前部署 collector。

对于 `proxy_confirmed_cross`，优先构造连续的 source-confidence / confirmation score；不要通过不断叠加城市、价格、湿度、风速等 AND 条件，把样本切成几个漂亮赢家。

## 5. 盘口研究必须以事件时间为主轴

核心不是“买某个固定价格带的 NO”，而是测一条 repricing curve：

```text
source first seen
→ collector detects
→ first book visible
→ NO ask/mid 开始上移
→ 盘口达到 0.90 / 0.95 / 0.97 / 0.99
→ 可执行 edge 消失
```

逐城市、逐机制报告：

- event 前、检测时、30/60/120/300 秒的 `NO ask`、mid、spread、depth 变化。
- `time_to_90/95/97/99`；这些阈值只用于描述市场反应曲线，不能事后变成 eligibility gate。
- 市场在 source report time 是否已重定价，以及在本系统 first-seen/detect time 是否仍有 edge。
- 在 1、5、10 shares 下的真实可执行成本与容量。
- orderbook age、snapshot cadence、源 cadence 和检测延迟的相对大小。
- 事件发生后仍满足正费后 edge 的比例，即 latency-survival curve。

`authoritative_invalidated` 的可执行 edge 直接用 `1 - ask - fee - buffer`。`proxy_confirmed_cross` 用冻结、out-of-sample 或 walk-forward 的 `p_confirm - ask - fee - buffer`，禁止用同一事件的最终标签回填概率。

## 6. 固定评估与对照

### 机制指标

- distinct events、active dates、city-days。
- single/persistent cross 对下一份官方观测与最终 settlement 的 precision。
- Wilson 或 Jeffreys 区间；小样本不只报点估。
- source first-seen 到 official first-seen 的领先时间分布。
- 所有 false-cross 的逐条清单与原因分类，不只报汇总率。

### 市场指标

- 检测时有 fresh direct book 的事件比例。
- 检测时仍有正费后 edge 的事件比例和可执行 shares。
- repricing curve、time-to-threshold、latency-survival。
- event-time book coverage 缺口；缺盘口是 evidence gap，不是策略过滤。
- 按日期等权和按事件等权都报告，主结论以日期 block 为准。

### 对照

- 同城市、同时间窗的 generic bracket advance。
- 同城市、相近本地时刻但没有 cross 的 source update。
- 若能严格匹配，再做相近 pre-event NO ask / liquidity 的 non-cross 对照。
- 不得拿不同城市、不同 source basis、不同 book cadence 的均值直接宣称 alpha。

### 两条漏斗分开写

`signal funnel`：

```text
全部 source updates
→ 正确 source profile
→ 首次可见的新观测
→ 合法 bracket cross
→ authoritative 或 proxy 机制事件
→ 每 city-day first event
```

`evidence funnel`：

```text
机制事件
→ 有可信 first-seen 时间
→ 有 event-time direct book
→ 有 fee/depth
→ 有官方确认/settlement label
→ 可计算的执行表达
```

盘口、settlement 或时间戳缺失只能写 coverage gap，不能包装成“精筛后剩余策略样本”。

## 7. 数据与代码血缘

先动态盘点当前 Mac collector、进程和 raw 文件覆盖，不能根据旧文档假定它仍在运行。当前生产 truth 在 Mac；N100 只作历史镜像，不作为最新事实源。

优先复用、扩展这些资产：

- `scripts/analysis/market_structure_edge/audit_source_event_denominator_v2.py`
- `scripts/analysis/market_structure_edge/research_source_event_expression_denominator_v3.py`
- `scripts/analysis/market_structure_edge/research_source_event_hazard_router_v1.py`
- `scripts/analysis/market_structure_edge/research_post_cross_repricing_v0.py`
- `scripts/analysis/forecast_quality/research_high_frequency_strategy_eligibility_v2.py`
- `scripts/analysis/forecast_quality/research_lead_to_next_metar_stale_book_v1.py`
- `scripts/ops/weather_fast_source_stale_book_observer.py`
- `scripts/ops/weather_metar_cross_prev_no_shadow.py`
- `scripts/ops/start_weather_source_event_ladder_repricing_shadow.sh`
- 当前 source-event ladder collector 输出目录（开始时动态核验）：`/Volumes/jrs/weather_data_feed_service_runtime/output/source_event_ladder_repricing_shadow`

不要另建一套与 `fact_signal_candidates` / canonical facts 平行的交易事实链。需要补最新 market raw 时，只做项目约定的增量同步；全量 rebuild 必须先获得明确同意。

若发现 source timestamp、station mapping、unit/rounding 或 bracket handler 有 bug，先修根因，再量化受影响日期、事件数、false cross 和反事实决策清单；不能加一个下游 gate 把问题盖掉。

## 8. 首轮完成条件与停止条件

首轮 retrospective audit 完成后，冻结城市、source profile、cross 定义、persistent 定义和 horizon，再进入 forward collector。不得一边看结果一边换城市或阈值。

满足以下任一项，就应对该 `city × source` 停止或降级，而不是继续调参：

- 无法证明 settlement source、station、local date、unit/rounding 和 bracket 的一一映射。
- 没有可信的 source first-seen 时间，只有事后 archive report time。
- source 的实际领先窗口不大于 collector + book sampling 延迟。
- 检测时盘口通常已经完成重定价，费后 edge 不再为正。
- proxy cross 的 confirmation 下界低于其真实 break-even 概率。
- direct NO book、fee 或 5/10-share depth 覆盖不足。
- 结论依赖未来官方观测、事后 settlement 或按 PnL 挑城市。

如果 retrospective 机制成立，也只能进入冻结的 collector / zero-notional shadow。至少积累 10 个新的 active dates，并优先达到 30 个 distinct credible events 后再复核；少量事件、旧 ROI 或运行中的 probe 都不能升级为 live 结论。

只有首个城市同时满足：

1. 规则和 source basis 干净；
2. first-seen 与 book 时间可信；
3. proxy 准确度区间下界高于真实经济 break-even；authoritative 标签则已经逐条验证；
4. 检测时仍存在可执行 fee-adjusted edge；
5. forward 机制与 repricing 结果没有明显退化；

才允许把同一研究模板扩到第二座同机制城市。新城市重新过 scorecard，不复制第一座城市的阈值。

## 9. 必须交付

1. 一份城市机制 scorecard，包含所有候选和明确的首批选择理由；选择理由不得引用 ROI。
2. 一份去重的 source-event ledger，能够从 source first-seen 对齐到 direct book 和 settlement label。
3. authoritative 与 proxy 两套独立的准确度、领先时间和 repricing 结果。
4. event-time repricing curve、latency-survival、5/10-share capacity 表。
5. 全部 false-cross 清单，以及时间戳/盘口/settlement coverage gap 清单。
6. signal funnel 和 evidence funnel，逐层写清单位。
7. 一个可复跑脚本或对现有脚本的干净扩展；不要只交 notebook 或手工 CSV。
8. 一份主报告，开头用大白话直接回答：
   - 哪个城市、哪个 source、哪种机制最干净；
   - 这条信息比盘口快多少；
   - 系统真正看到时还剩多少 edge 和容量；
   - 失败主要因为 source 不准、检测慢，还是盘口更快；
   - 是否值得冻结两城 shadow，还是应该拒绝该方向。

每个机制轨道的最终 verdict 只能选择一个：

```text
continue_collector
freeze_one_city_shadow
freeze_two_city_shadow
reject_city_source_pair
reject_cross_no_event_time_direction
```

若两个机制的结论不同，分别给 verdict，不得用一个综合平均掩盖差异。

硬边界：不改 live config、不部署、不恢复或扩大真实交易、不真实下单；不把 retrospective 当 fresh forward；不把买到 `0.98/0.99` 的确定性票误称为天气 alpha；不使用未来观测或事后表现挑机制。

## 这项研究真正要回答的问题

最后不要只回答“cross 后买 NO 胜率高不高”。要回答：

> 在一座规则和数据都足够干净的城市，官方或高质量快源第一次让旧档失效时，我们是否能比盘口更早知道；如果能，扣掉采集、检测、盘口刷新、fee 和深度以后，这个时间差还剩不剩一笔能重复执行的交易？
