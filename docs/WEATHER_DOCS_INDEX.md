# Weather Docs Index

更新时间：2026-06-06

这份索引是 weather 文档的入口和权威性判断。`AGENTS.md` / `CLAUDE.md`
只保留短入口；新增、归档或改变 weather 文档职责时，优先更新这里。

Status 口径：

| Status | 含义 |
|---|---|
| `current-source` | 当前事实或流程的 source of truth，冲突时优先信它 |
| `current-reference` | 当前仍常用的参考文档，但不单独定义生产事实 |
| `design-draft` | 设计或迁移方案，未必全部实现 |
| `snapshot` | 时间点分析/复盘，保留证据，不代表当前生产口径 |
| `superseded` | 已被新入口覆盖，只作为历史背景 |

## 当前运行入口

| 文档 | Status | 读它回答什么问题 |
|---|---|---|
| [WEATHER_STRATEGY_ENTRYPOINT.md](WEATHER_STRATEGY_ENTRYPOINT.md) | `current-source` | 现在 N100 应跑哪些 live 实例、怎么检查、近期事故/决策是什么 |
| [WEATHER_CITY_POOL_DECISIONS.md](WEATHER_CITY_POOL_DECISIONS.md) | `current-source` | 当前 T1/T2 城市池是谁、为什么升降级、回滚条件是什么 |
| [WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md) | `current-source` | weather 分析、PnL、切片、账户对账必须用什么口径 |
| [OPS_RUNBOOK.md](OPS_RUNBOOK.md) | `current-reference` | 常驻进程、日志、通用运维命令在哪里 |
| [WEATHER_DASHBOARD_TROUBLESHOOTING.md](WEATHER_DASHBOARD_TROUBLESHOOTING.md) | `current-reference` | 本机 dashboard / API / FE 出问题时怎么排查 |

## 数据真相与协议

| 文档 | Status | 读它回答什么问题 |
|---|---|---|
| [WEATHER_REPO_BOUNDARY.md](WEATHER_REPO_BOUNDARY.md) | `current-source` | `weather-predict` 和 `pm_agent` 各自负责什么，生产/本机边界在哪里 |
| [WEATHER_DATA_CANONICAL_SOURCES.md](WEATHER_DATA_CANONICAL_SOURCES.md) | `current-source` | 哪些表/文件是 source、mirror、derived、legacy，分析前先查什么 |
| [WEATHER_DATA_PIPELINE.md](WEATHER_DATA_PIPELINE.md) | `current-source` | N100 -> 本机镜像 -> DB -> API 的脚本职责和数据链路 |
| [WEATHER_SYSTEM_CONTRACT.md](WEATHER_SYSTEM_CONTRACT.md) | `current-source` | 字段名、枚举、ID 算法、跨 repo contract 怎么定义 |
| [WEATHER_DATA_PROTOCOL_UNIFICATION_PLAN.md](WEATHER_DATA_PROTOCOL_UNIFICATION_PLAN.md) | `design-draft` | canonical schema / paper 语义 / shadow-run cutover 怎么迁移 |
| [WEATHER_FACT_TRADES_DESIGN.md](WEATHER_FACT_TRADES_DESIGN.md) | `design-draft` | `fact_trades` 设计背景和目标形态是什么 |
| [WEATHER_SIGNAL_CANDIDATES_DESIGN.md](WEATHER_SIGNAL_CANDIDATES_DESIGN.md) | `design-draft` | `fact_signal_candidates` 设计背景和机会粒度口径是什么 |

## 策略与执行决策

| 文档 | Status | 读它回答什么问题 |
|---|---|---|
| [WEATHER_STRATEGY_QUANT_DESIGN.md](WEATHER_STRATEGY_QUANT_DESIGN.md) | `current-reference` | 血缘链、策略身份、Run Registry、DB/API/前端整体设计 |
| [WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md](WEATHER_LIVE_RUN_HISTORY_AND_DATA_GOVERNANCE.md) | `current-reference` | 早期实盘事故、重复下单、回填治理、历史 live 数据如何解释 |
| [WEATHER_EXECUTION_ARCHITECTURE.md](WEATHER_EXECUTION_ARCHITECTURE.md) | `design-draft` | paper -> live 执行边界、资金安全、暂停开关、notional 上限 |
| [WEATHER_MID_PRICE_CORE_V2_DESIGN.md](WEATHER_MID_PRICE_CORE_V2_DESIGN.md) | `superseded` | V2 原设计是什么；注意 V2 已于 2026-06-06 停 live，不再默认生产 |
| [WEATHER_ENTRY_BAND_AND_SIZING_DESIGN.md](WEATHER_ENTRY_BAND_AND_SIZING_DESIGN.md) | `design-draft` | 入场区间和 sizing 设计如何替代统一 0.25-0.75 |
| [WEATHER_CLOB_ORDERBOOK_CAPTURE.md](WEATHER_CLOB_ORDERBOOK_CAPTURE.md) | `design-draft` | CLOB 快照捕获如何服务更准确回测 |
| [WEATHER_SHADOW_PORTFOLIO_TRACKING.md](WEATHER_SHADOW_PORTFOLIO_TRACKING.md) | `design-draft` | live 策略的影子持仓怎么追踪 |
| [WEATHER_LEDGER_POSITION_ANALYSIS.md](WEATHER_LEDGER_POSITION_ANALYSIS.md) | `design-draft` | dashboard 持仓拆解页面和 position 分析怎么做 |

## 模型与 Edge Engine

| 文档 | Status | 读它回答什么问题 |
|---|---|---|
| [WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md](WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md) | `current-source` | weather_edge_engine 当前接手入口：blender shadow/paper 与 city-day basket 下一步 |
| [WEATHER_PROBABILITY_MODEL_REVIEW.md](WEATHER_PROBABILITY_MODEL_REVIEW.md) | `current-reference` | 生产 baseline `model_p_yes` 的问题、条件模型缺口、季节/forecast jump 风险 |
| [WEATHER_PROBABILITY_MODEL_ROADMAP.md](WEATHER_PROBABILITY_MODEL_ROADMAP.md) | `current-reference` | 概率模型从 M0 可观测骨架到 lead-time/ensemble/ML 的路线图 |
| [WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md](WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md) | `design-draft` | 城市/日组合优化器目标、约束、分布和 tail 风险怎么设计 |
| [WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md](WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md) | `snapshot` | 2026-06-05 blend model 实施计划背景，当前结论以 edge engine current state 为准 |
| [WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md](WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md) | `snapshot` | 2026-06-05 策略/模型复盘，作为时间点证据，不定义当前生产 |
| [WEATHER_STRATEGY_DIRECTION_RECONCILIATION_2026-06-05.md](WEATHER_STRATEGY_DIRECTION_RECONCILIATION_2026-06-05.md) | `snapshot` | 2026-06-05 策略方向对齐记录，当前执行以后续入口为准 |
| [模型优化研究.md](模型优化研究.md) | `snapshot` | 中文模型优化研究笔记，保留背景，当前有效动作以 roadmap/current state 为准 |

## 历史分析快照

这些文档是时间点证据。正文默认不改；如果被当前决策引用，在“Used by current decision”列标明。

| 文档 | Status | Used by current decision | 读它回答什么问题 |
|---|---|---|---|
| [2026-06-03-performance-three-strategy-instances.md](analysis/2026-06/2026-06-03-performance-three-strategy-instances.md) | `snapshot` | yes | 三策略实例 live_real 表现、V2 停 live、Amsterdam/BuenosAires 降 T2 的证据入口 |
| [2026-06-06-city-alpha-framework.md](analysis/2026-06/2026-06-06-city-alpha-framework.md) | `snapshot` | yes | 城市 alpha 评价体系、paper->live 扩池反转、city x side gate |
| [2026-06-06-city-day-distribution-quality.md](analysis/2026-06/2026-06-06-city-day-distribution-quality.md) | `snapshot` | yes | raw/market/blend_norm 分布质量和 holdout 退化问题 |
| [2026-06-06-blended-single-v0-backtest.md](analysis/2026-06/2026-06-06-blended-single-v0-backtest.md) | `snapshot` | yes | blended single v0 shadow 策略回测、策略身份和 opportunity 对比 |
| [2026-06-06-blended-entry-band-backtest.md](analysis/2026-06/2026-06-06-blended-entry-band-backtest.md) | `snapshot` | yes | 保留 live 入场区间后的 blend gate 公平对比 |
| [2026-06-06-city-day-basket-optimizer-research.md](analysis/2026-06/2026-06-06-city-day-basket-optimizer-research.md) | `snapshot` | yes | city-day basket optimizer 的 headline ROI 与 tail 风险 |
| [2026-06-06-city-day-basket-pr2b-robustness.md](analysis/2026-06/2026-06-06-city-day-basket-pr2b-robustness.md) | `snapshot` | yes | PR2b 全样本通过但 tail/overfit 风险的复核 |
| [2026-06-06-city-day-basket-pr2b-sweep.md](analysis/2026-06/2026-06-06-city-day-basket-pr2b-sweep.md) | `snapshot` | yes | PR2b basket 参数 sweep 和候选 profile |
| [2026-06-05-city-day-basket-eval.md](analysis/2026-06/2026-06-05-city-day-basket-eval.md) | `snapshot` | yes | PR2 离线 replay：raw vs blended-single vs basket，对应 Step 2->3 gate 不通过 |
| [2026-06-05-probability-calibration.md](analysis/2026-06/2026-06-05-probability-calibration.md) | `snapshot` | yes | 概率校准实验和 blend/recalibration 背景 |
| [2026-06-04-performance-side-band-entry-analysis.md](analysis/2026-06/2026-06-04-performance-side-band-entry-analysis.md) | `snapshot` | yes | side-band 入场表现复盘 |
| [2026-06-03-signal-side-flip-check.md](analysis/2026-06/2026-06-03-signal-side-flip-check.md) | `snapshot` | yes | 信号 side flip / snapshot bracket 演化排查 |
| [2026-05-29-performance-city-pool-side-strategy.md](analysis/2026-05/2026-05-29-performance-city-pool-side-strategy.md) | `snapshot` | yes | city x side 白名单、Paris 降级、Madrid/Shanghai NO-only 的证据 |
| [2026-05-29-strategy-entry-band-and-execution-quality.md](analysis/2026-05/2026-05-29-strategy-entry-band-and-execution-quality.md) | `snapshot` | yes | 25-75 入场价调参、maker_queue vs mid_price 成交质量 |
| [2026-05-30-performance-entry-band-research.md](analysis/2026-05/2026-05-30-performance-entry-band-research.md) | `snapshot` | yes | 入场价带 0.25-0.75 的 side x price bucket EV |
| [2026-05-30-performance-sizing-and-band-distribution.md](analysis/2026-05/2026-05-30-performance-sizing-and-band-distribution.md) | `snapshot` | yes | sizing x 入场区间收益分布和反过拟合检验 |
| [2026-05-27-performance-live-full-research.md](analysis/2026-05/2026-05-27-performance-live-full-research.md) | `snapshot` | yes | 早期 live 全量绩效归因 |
| [WEATHER_LIVE_STRATEGY_ANALYSIS_2026-05-23.md](WEATHER_LIVE_STRATEGY_ANALYSIS_2026-05-23.md) | `snapshot` | no | 2026-05-23 早期实盘血缘分析 |
| [WEATHER_LOW_PRICE_LOTTERY_RESEARCH_2026-05-19.md](WEATHER_LOW_PRICE_LOTTERY_RESEARCH_2026-05-19.md) | `snapshot` | no | 低价 YES 彩票仓研究 |

## 开发日志

| 文档 | Status | 读它回答什么问题 |
|---|---|---|
| [2026-06-05-weather-edge-engine-pr1.md](dev_logs/2026-06-05-weather-edge-engine-pr1.md) | `snapshot` | PR1 纯函数骨架、blender + city_day_basket + tests 开发记录 |
| [2026-06-05-weather-edge-engine-pr2.md](dev_logs/2026-06-05-weather-edge-engine-pr2.md) | `snapshot` | PR2 离线 replay + sklearn recalibration 开发记录 |
| [2026-06-06-weather-edge-engine-pr2b.md](dev_logs/2026-06-06-weather-edge-engine-pr2b.md) | `snapshot` | PR2b 参数 sweep + robustness，结论是 shadow 候选不能直接 canary |

## 维护规则

- `current-source` 文档之间冲突时，先修冲突，再跑 docs check；不要靠口头解释绕过去。
- 时间点报告保持正文不改；如结论不再适用，在本索引或当前入口标注，不回写历史正文。
- 任何改变 live 策略、城市池、账户对账或数据源真相的关键发现，至少要落到对应的 `current-source` 文档和本索引。
- 每次新增或删除 `docs/` 下 weather 文档，更新本索引；`AGENTS.md` / `CLAUDE.md` 不再维护长表。
