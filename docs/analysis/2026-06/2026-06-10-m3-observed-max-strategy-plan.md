# M3 Observed-Max Strategy Plan — 2026-06-10

Status: design-draft
Updated: 2026-06-10
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md

原始交接稿见：`docs/analysis/2026-06/2026-06-10-m3-observed-max-strategy-handoff.md`。

## 数据快照

本报告是策略方向和工程规划，不发布 live PnL / ROI / city rank 结论。

本次只使用本机当前 `runtime/weather.db` 做分析前自检，未执行完整
`sync_weather_remote.sh` + `run_stack.sh` 刷新；因此所有策略现状以当前
source 文档为准，不把本文写成实时生产诊断。

自检结果：

| 检查项 | 当前值 |
|---|---:|
| `MAX(fact_built_at_utc)` | `2026-06-09T17:37:40.513320+00:00` |
| `fact_trades.live_real` | 1405 |
| `fact_trades.live_simulated` | 1147 |
| `fact_trades.paper` | 2285 |
| `fact_trades.snapshot_replay` | 636 |
| `settled` | 5241 |
| unsettled / NULL status | 232 |
| `fact_signal_candidates` rows | 25117 |
| `SUM(eligible)` | 8306 |
| `SUM(paper_ordered)` | 3139 |
| `SUM(live_filled)` | 554 |
| CLOB orders `submitted` / with fill | 1635 / 1405 |
| CLOB orders `error` / with fill | 151 / 0 |

CLOB coverage gate:

```text
gate_pass=true
fact_trades_live_real.rows=1405
fact_trades_live_real.fill_ids=1405
missing_order_rows=0
over_order_keys=0
db_fill_cost_minus_fact_cost=0.0
db_vs_primary_cache.db_not_in_cache=0
db_vs_primary_cache.cache_not_in_db=0
```

## 目标指标

本轮问题不是“旧 live 是否继续跑”，而是验证一个新信息结构：

```text
m3_observed_max_lockable_city_day
= 在当地傍晚决策时点，已观测当日 running max 对最终结算 max 的残差足够小，
  且晚间二次升温风险可用事前信息过滤的 city-day。
```

第一阶段只回答物理问题：

```text
residual_c = final_daily_max_c - observed_running_max_c_at_decision_time
```

不要在这个阶段混入 market price、edge、PnL 或 live 动作。

## 当前策略现状

当前生产 live 不是 M3，也不应被 M3 直接替换。

生产默认只应运行两个 `mid_price_core_v1` 实例：

| strategy_instance | 定位 | 当前约束 |
|---|---|---|
| `mid_price_core_v1_25_75` | forecast-first raw edge live | legacy core 9 城，`0.25 <= price < 0.75`，`edge >= 0.10` |
| `mid_price_core_v1_side_band` | side-specific band live | legacy core 9 城，YES `0.20-0.45` / NO `0.35-0.65` |

当前生产风控已经做过两类收缩：

- 城市：pm_agent live allowlist 收回到 legacy core 9 城；弱 ECMWF 城市仍在采集、paper、research，但默认不 live。
- 时间：`T>28h` 已从 live 里移除，`<T-22h` 不进 live，`T-24-26h` 仍需继续复核。

当前研究线的状态：

- `mid_price_core_v2_25_75` 已停 live；不要默认重启。
- blender / probability blend 只适合 shadow/paper 和健康信号，不进 live hard gate。
- city-day basket 仍是研究候选；PR2b headline 好，但 holdout/recent/top5 stress 不稳，不能 canary。
- 2026-06-10 的多个 Range RV / side-band / forecast-regime 实验整体没有给出真钱 live 候选；较明确的动作是 shadow/paper 观察，而不是改 live。

因此，M3 的定位应是“新研究主线”，不是现有参数微调。

## M3 与现有路线的关系

M3 改的是信息时间轴。

旧 A/B/C、side-band、blender、basket 的共同基础仍是 forecast-first：在目标日前约
22-28 小时，用 forecast/model/market distribution 判断未来最高温落在哪些 bracket。
这些路线的问题已经在近期文档里暴露：

- raw model 在近期退化，global probability alpha 为负或不稳定。
- 城市/模型/side 子池有样本内亮点，但 holdout 易翻车。
- 组合/basket 能改善表达方式，但如果底层分布不稳，会把 tail risk 包装成更复杂的策略。

M3 的不同点：

- 用“当天已观测 running max”作为硬事实下界，而不是提前一天预测。
- 把问题从“精确预测最终最高温”改成“傍晚后残差是否已经塌缩”。
- 用气象条件只过滤二次升温风险，不用 forecast 去做 1°C 级别点预测。

这使它值得单独验证。但它还没有证明三件事：

1. 历史逐时观测能否稳定重建到每个城市、每个决策时点。
2. 傍晚残差分布是否真的小到足以跨过 1°C bracket 宽度。
3. 市场在这个窗口是否仍有可成交价差，且扣 best ask / spread / fill 后仍显著。

## 下一步规划

### P0：冻结边界

结论：

```text
M3 当前只能是 research/shadow 方向；不得改 N100 live 下单配置。
```

落地要求：

- 不修改 `mid_price_core_v1_*` 的 live gate。
- 不新增真钱 live instance。
- 不把 M3 的外部 deep-research 数字当成本仓库已验证结论。
- 后续如涉及 N100 代码、city pool、paper policy 或 live instance，必须走 `weather-strategy-deploy` 的 git-first 流程。

### P1：补“已观测 running max”事实层

先补数据，不先回测。

责任边界：

| repo | 责任 |
|---|---|
| `weather-predict` | 生产采集和 cache：逐时/亚小时 METAR、WU、IEM 或等价观测源；按 city/date/station 保留原始观测 |
| `pm_agent` | 镜像、DB ingest、fact 表、分析脚本、dashboard/research 报告 |

建议新增分析 grain：

```text
city
station_id
target_date
decision_time_local
decision_hour_local
observed_running_max_c
current_temp_c
final_daily_max_c
residual_c
residual_bucket_delta
cooling_slope_1h_c
cooling_slope_2h_c
wind_dir
wind_speed
cloud_cover_or_condition
source_file
source_ts_utc
```

第一版决策时点固定为当地时间：

```text
18:00, 19:00, 20:00, 21:00
```

先不要让脚本自动扫很多阈值；避免一开始就过拟合。

### P2：只做物理残差实验

目标：

```text
按 city x month x decision_hour 统计 residual_c 分布。
```

必须输出：

- `n_city_days`
- `P50/P75/P90/P95/P99 residual_c`
- `residual_c >= 0.5C / 1.0C / 1.5C` 的比例
- `residual_bucket_delta >= 1` 的比例
- bad case 明细：哪些 city-day 在傍晚后继续刷新最高温

候选池门槛建议：

| 门 | 通过条件 |
|---|---|
| 样本门 | city x decision_hour 至少 30 个有效 city-day |
| 塌缩门 | P95 residual_c 明显小于 1°C，且 `residual_bucket_delta >= 1` 低 |
| 前瞻门 | train 选出的 city/hour，在 holdout 中仍同向成立 |

这一阶段结论等级只能是：

```text
physical_candidate / physical_reject / inconclusive
```

### P3：验证二次升温过滤

只在 P2 有候选城市后做。

目标：

```text
bad_case_filter_recall
= 在 residual_bucket_delta >= 1 的坏 case 中，下注当时可见特征能提前过滤掉多少。
```

候选事前特征：

- 最近 1-2 小时温度斜率。
- 当前温度距 running max 的回落幅度。
- 风向/风速变化。
- 云量或天气现象变化。
- 暖平流、暖锋、焚风等可由 forecast/观测粗粒度 proxy 表达的风险标记。

关键纪律：

- 特征必须是 `decision_time_local` 当刻可见。
- 不允许用最终最高温、最终 settlement 或事后完整天气路径做过滤。
- 输出 recall 的同时要输出 false positive：过滤掉多少本来可交易的干净 case。

### P4：再做市场可成交回测

只有 P2/P3 都过，才引入价格。

基准必须至少包括：

1. 傍晚无脑买 observed max 所在 bracket。
2. 傍晚无脑买已不可能更高/更低 bracket 的 NO。
3. 同 city/date/hour/price band 的随机或 matched baseline。

价格口径：

- 用 decision-time orderbook 的 best ask / executable proxy，不用 mid 直接算收益。
- 必须记录 orderbook coverage；缺 coverage 的 city-day 不可偷换成 market midpoint。
- 过 CLOB fill coverage gate 后才能引用 live-real 子集。

统计门：

- 按 city-day cluster bootstrap。
- train 规则冻结后做 holdout。
- top-N stress：去掉最大盈利的 5 个 city-day 后仍不能反号。

结论等级沿用 `WEATHER_ANALYSIS_CONTRACT.md`：

```text
confirmed / shadow_candidate / inconclusive
```

未过三门时，只允许进入 shadow/paper。

### P5：shadow / paper 小步接入

若 P4 至少达到 `shadow_candidate`：

```text
strategy_id = weather_m3_observed_max_v0
execution_mode = shadow
decision_mode = observed_max_lock
```

shadow 产物必须写清：

```text
observed_running_max_c
decision_time_local
residual_model_bucket
secondary_warmup_filter_pass
selected_bracket
selected_side
market_best_ask
would_trade_notional
reason_codes
```

初期只记录 would-trade，不下真钱；是否 paper 下单另行决策。

## Kill 条件

任一条件成立，M3 应干净降级为 null 或只保留为研究记录：

- 主要城市在 19:00/20:00 的 P95 residual_c 接近或超过 1°C。
- `residual_bucket_delta >= 1` 的 case 多且没有事前可见过滤特征。
- 市场在傍晚窗口已经充分反应，best ask 后没有正 excess。
- 盈利高度依赖少数 tail city-day，top-N stress 后反号。
- 历史观测源覆盖不足，无法稳定重建 decision-time running max。

## 推荐最近执行顺序

1. 存档原始 M3 交接稿，并在 docs index 里标明它是 snapshot，不是生产口径。
2. 做 `observed_running_max` 数据覆盖审计：按城市列出可重建的逐时观测天数。
3. 写 P2 物理残差脚本，只输出残差分布，不读市场价格。
4. 如果 P2 看到候选城市，再写二次升温 bad case filter 审计。
5. 只有残差和过滤都过，才接入 orderbook 做 P4 市场回测。

当前不建议做：

- 直接把 M3 上真钱 live。
- 用现有 forecast-first `fact_signal_candidates` 硬拼一个 M3 ROI。
- 先调 city_pool 或 live allowlist。
- 先做复杂 basket/optimizer；M3 的第一性问题是物理残差，不是组合优化。
