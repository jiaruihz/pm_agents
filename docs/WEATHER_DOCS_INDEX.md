# Weather Docs Index

Status: current-source
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: yes
Superseded by / Used by: WEATHER_DOCS_INDEX.md; AGENTS.md / CLAUDE.md short entry when listed

更新时间：2026-06-09

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

## 2026-06-06 口径勘误

`pm_history` 已结算价格可能是 near-binary `0.9995 / 0.0005`，不是精确 `1.0 / 0.0`。2026-06-06 前旧 ingest/builder 会把这批已结算 bracket 误标为 `missing_bracket`；修复后 live fill 与 raw CLOB fills 对齐，`missing_bracket` 从 725 降到 0。

权威入口：

| 文档 | Status | 读它回答什么问题 |
|---|---|---|
| [2026-06-06-account-equity-replay.md](analysis/2026-06/2026-06-06-account-equity-replay.md) | `snapshot` | 用 Polymarket public activity + raw live order files + DB fills 对齐截图 `1周 -$305.32`，确认 DB fill recovery 少覆盖真实 BUY |
| [2026-06-06-polymarket-ui-account-loss-reconciliation.md](analysis/2026-06/2026-06-06-polymarket-ui-account-loss-reconciliation.md) | `snapshot` | Polymarket UI `1周 -$305.32` 与 fact 表已结算 PnL、公开 activity cashflow、当前 positions value 的口径差异 |
| [2026-06-06-live-account-reconcile-near-binary-fix.md](analysis/2026-06/2026-06-06-live-account-reconcile-near-binary-fix.md) | `snapshot` | 2026-06-06 fill 没漏、near-binary settlement 修复、最近一周 cashflow/realized/open 分拆 |
| [2026-06-06-live-strategy-period-slice.md](analysis/2026-06/2026-06-06-live-strategy-period-slice.md) | `snapshot` | 最近 7 天/14 天/更早按日期和 strategy_instance 拆 realized、open cost、cashflow；注意它不是 Polymarket UI 账户权益曲线 |
| [2026-06-06-three-strategy-instances-near-binary-reanalysis.md](analysis/2026-06/2026-06-06-three-strategy-instances-near-binary-reanalysis.md) | `snapshot` | near-binary 勘误后重算三个 live strategy_instance 的 recent/full realized 表现和动作建议 |
| [2026-06-06-near-binary-city-reanalysis.md](analysis/2026-06/2026-06-06-near-binary-city-reanalysis.md) | `snapshot` | near-binary 勘误后重算城市 alpha、recent live loss、city x side 处置 |

以下报告可能使用旧 settlement 口径；凡是要引用 settled PnL、ROI、win rate、city/side rank、live_filled 子集或 `missing_bracket` 数，必须先 sync + rebuild `runtime/weather.db` 后重算：

| 文档 | 过时原因 |
|---|---|
| [2026-06-03-performance-three-strategy-instances.md](analysis/2026-06/2026-06-03-performance-three-strategy-instances.md) | `missing_bracket=725`，且 side-band 漏算 YES 侧 `0.20-0.45`，三实例 realized 对比需重算 |
| [2026-06-04-performance-side-band-entry-analysis.md](analysis/2026-06/2026-06-04-performance-side-band-entry-analysis.md) | `missing_bracket=624`，side-band realized 判定需重算 |
| [2026-06-06-recent-live-loss-attribution.md](analysis/2026-06/2026-06-06-recent-live-loss-attribution.md) | `missing_bracket=28/734`，recent loss、open/cashflow 结论已被新对账快照覆盖 |
| [2026-06-06-city-alpha-framework.md](analysis/2026-06/2026-06-06-city-alpha-framework.md) | `missing_bracket=734`，live city/side rank 和 settled 兑现需重算 |
| [2026-06-06-blended-single-v0-backtest.md](analysis/2026-06/2026-06-06-blended-single-v0-backtest.md) | 自检 `missing_bracket=734`，settlement/ROI/live_filled 子集需重算 |
| [2026-06-06-blended-entry-band-backtest.md](analysis/2026-06/2026-06-06-blended-entry-band-backtest.md) | 自检 `missing_bracket=734`，settlement/ROI/live_filled 子集需重算 |
| [2026-06-06-blended-paper-fill-estimate.md](analysis/2026-06/2026-06-06-blended-paper-fill-estimate.md) | 自检 `missing_bracket=734`，settlement/ROI/live_filled 子集需重算 |

## 当前运行入口

| 文档 | Status | 读它回答什么问题 |
|---|---|---|
| [WEATHER_STRATEGY_ENTRYPOINT.md](WEATHER_STRATEGY_ENTRYPOINT.md) | `current-source` | 现在 N100 应跑哪些 live 实例、每条实例的 allowed cities、怎么检查、近期事故/决策是什么 |
| [WEATHER_CITY_POOL_DECISIONS.md](WEATHER_CITY_POOL_DECISIONS.md) | `current-source` | 当前 T1/T2 城市池、pm_agent 实例级 live allowlist、为什么升降级、回滚条件是什么 |
| [WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md) | `current-source` | weather 分析、PnL、切片、账户对账必须用什么口径 |
| [WEATHER_HANDOFF_EXECUTION.md](WEATHER_HANDOFF_EXECUTION.md) | `design-draft` | 2026-06-08 方法论/结构重整交接入口；用于迁移执行顺序，不直接定义 live 生产状态 |
| [WEATHER_ARCHITECTURE_SPINE.md](WEATHER_ARCHITECTURE_SPINE.md) | `design-draft` | 天气策略 [0]–[6] 主线骨架和评估层重构映射 |
| [OPS_RUNBOOK.md](OPS_RUNBOOK.md) | `current-reference` | 常驻进程、日志、通用运维命令在哪里 |
| [WEATHER_DASHBOARD_TROUBLESHOOTING.md](WEATHER_DASHBOARD_TROUBLESHOOTING.md) | `current-reference` | 本机 dashboard / API / FE 出问题时怎么排查 |

## 数据真相与协议

| 文档 | Status | 读它回答什么问题 |
|---|---|---|
| [WEATHER_REPO_BOUNDARY.md](WEATHER_REPO_BOUNDARY.md) | `current-source` | `weather-predict` 和 `pm_agent` 各自负责什么，生产/本机边界在哪里 |
| [WEATHER_DATA_CANONICAL_SOURCES.md](WEATHER_DATA_CANONICAL_SOURCES.md) | `current-source` | 哪些表/文件是 source、mirror、derived、legacy，分析前先查什么 |
| [WEATHER_DATA_PIPELINE.md](WEATHER_DATA_PIPELINE.md) | `current-source` | N100 -> 本机镜像 -> DB -> API 的脚本职责和数据链路 |
| [WEATHER_SYSTEM_CONTRACT.md](WEATHER_SYSTEM_CONTRACT.md) | `current-source` | 字段名、枚举、ID 算法、跨 repo contract 怎么定义 |
| [WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md](WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md) | `current-reference` | dashboard DB/API 分层缺口审计；P0 已完成，剩余项按当前 fact-table 口径复核 |
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
| [model_vs_market.md](analysis/model_vs_market.md) | `current-reference` | 模型概率相对市场是否有 alpha 的 living doc；当前结论：global probability alpha 为负，model edge rank alpha 未确认 |
| [WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md](WEATHER_EDGE_ENGINE_CURRENT_STATE_2026-06-06.md) | `current-source` | weather_edge_engine 当前接手入口：blender shadow/paper 与 city-day basket 下一步 |
| [WEATHER_PROBABILITY_MODEL_REVIEW.md](WEATHER_PROBABILITY_MODEL_REVIEW.md) | `current-reference` | 生产 baseline `model_p_yes` 的问题、条件模型缺口、季节/forecast jump 风险 |
| [WEATHER_PROBABILITY_MODEL_ROADMAP.md](WEATHER_PROBABILITY_MODEL_ROADMAP.md) | `current-reference` | 概率模型从 M0 可观测骨架到 lead-time/ensemble/ML 的路线图 |
| [WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md](WEATHER_CITY_DAY_PORTFOLIO_OPTIMIZER_DESIGN.md) | `design-draft` | 城市/日组合优化器目标、约束、分布和 tail 风险怎么设计 |
| [WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md](WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md) | `snapshot` | 2026-06-05 blend model 实施计划背景，当前结论以 edge engine current state 为准 |
| [WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md](WEATHER_STRATEGY_AND_MODEL_REVIEW_2026-06-05.md) | `snapshot` | 2026-06-05 策略/模型复盘，作为时间点证据，不定义当前生产 |
| [WEATHER_STRATEGY_DIRECTION_RECONCILIATION_2026-06-05.md](WEATHER_STRATEGY_DIRECTION_RECONCILIATION_2026-06-05.md) | `snapshot` | 2026-06-05 策略方向对齐记录，当前执行以后续入口为准 |
| [模型优化研究.md](模型优化研究.md) | `snapshot` | 中文模型优化研究笔记，保留背景，当前有效动作以 roadmap/current state 为准 |

## 评估层 Living Docs

这些文档是 `[6] 评估层` 的当前入口。日期快照继续保留为证据，但新分析结论应优先落到对应 living doc。

| 文档 | Status | 主线层 | 读它回答什么问题 |
|---|---|---:|---|
| [model_vs_market.md](analysis/model_vs_market.md) | `current-reference` | [1] | 模型概率相对市场是否有 alpha；模型字段是否可影响 signal / sizing |
| [market_structure_edge.md](analysis/market_structure_edge.md) | `current-reference` | [2] | 是否存在 model-free 的市场结构 edge，例如 favorite-longshot、BUY_NO base-rate、price-bucket mispricing |
| [execution_quality.md](analysis/execution_quality.md) | `current-reference` | [4] | maker-only 扣 spread、queue、逆向选择和 fill selection 后是否仍有可成交 edge |
| [entry_timing.md](analysis/entry_timing.md) | `current-reference` | [3] | target-date lead time、forecast checkpoint、decision window 对计划和成交的影响 |
| [side_alpha.md](analysis/side_alpha.md) | `current-reference` | [2] | BUY_NO / BUY_YES、side-band 是否有持久超额，而不是单纯 win-rate |
| [city_selection.md](analysis/city_selection.md) | `current-reference` | [3] | 城市池、city-day basket、城市 x model x side 选择证据；live 事实仍以 CITY_POOL_DECISIONS 为准 |
| [sizing_entry_band.md](analysis/sizing_entry_band.md) | `current-reference` | [3] | 仓位、entry price band、side-specific band 是否改善风险调整后的 executable edge |
| [blender_shadow.md](analysis/blender_shadow.md) | `current-reference` | [1] | blender / edge-engine 字段作为 shadow、paper 或 sizing signal 是否有价值 |
| [live_performance.md](analysis/live_performance.md) | `current-reference` | [5][6] | live 策略绩效曲线、strategy_instance 归因、settled/open/quasi-settled 拆分 |
| [account_reconcile.md](analysis/account_reconcile.md) | `current-reference` | [5] | 钱包余额、CLOB fill、cashflow、DB/fact 对账 |
| [data_integrity.md](analysis/data_integrity.md) | `current-reference` | [0] | snapshot 健康、side flip、candidate/fill linkage、fact-table coverage 和分析前自检 |
| [SCRIPT_MIGRATION_MANIFEST.md](analysis/SCRIPT_MIGRATION_MANIFEST.md) | `current-reference` | [0]-[6] | Phase 3A/3B 脚本迁移归属表：old path、new path、owner living doc |
| [MD_CONSOLIDATION_PLAN.md](analysis/MD_CONSOLIDATION_PLAN.md) | `current-reference` | [6] | Phase 4A/4B Markdown 审计表：口径有效性、重复、勘误、owner living doc、后续动作 |

## 历史分析快照

这些文档是时间点证据。正文默认不改；如果被当前决策引用，在“Used by current decision”列标明。

| 文档 | Status | Used by current decision | 读它回答什么问题 |
|---|---|---|---|
| [2026-06-03-performance-three-strategy-instances.md](analysis/2026-06/2026-06-03-performance-three-strategy-instances.md) | `snapshot` | yes | 三策略实例 live_real 表现、V2 停 live、Amsterdam/BuenosAires 降 T2 的证据入口 |
| [2026-06-06-near-binary-city-reanalysis.md](analysis/2026-06/2026-06-06-near-binary-city-reanalysis.md) | `snapshot` | yes | near-binary 勘误后城市 alpha、recent live loss 和 city x side 处置重算 |
| [2026-06-07-mid-price-core-v1-raw-degradation.md](analysis/2026-06/2026-06-07-mid-price-core-v1-raw-degradation.md) | `snapshot` | yes | raw / mid_price_core_v1 为什么 2026-06-01 后退化：side、city、edge、market divergence、tail 事件归因 |
| [2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md](analysis/2026-06/2026-06-07-mid-price-core-v1-forecast-timing-degradation-lineage.md) | `snapshot` | yes | v1_25_75 forecast timing 退化：`>T-28`、`T-26-28`、forecast run age、side flip、market adverse move 和 timing policy overlay |
| [2026-06-07-mid-price-core-v1-city-model-downgrade.md](analysis/2026-06/2026-06-07-mid-price-core-v1-city-model-downgrade.md) | `snapshot` | yes | v1_25_75 city×model 降级依据，识别 Ankara/Jeddah/Karachi/Moscow/Munich 等弱近期 ECMWF 城市 |
| [2026-06-07-v1-ecmwf-blocked-side-band-overlay.md](analysis/2026-06/2026-06-07-v1-ecmwf-blocked-side-band-overlay.md) | `snapshot` | yes | 被移出 v1_25_75 live allowlist 的弱城市若换 side-band 是否改善；结论是明显减亏但仍只适合 shadow |
| [2026-06-08-blender-research-state-and-next-plan.md](analysis/2026-06/2026-06-08-blender-research-state-and-next-plan.md) | `snapshot` | yes | blender 全部研究和工程改动交接：结论是只做 shadow/paper 与 size/risk signal，不进 live hard gate |
| [2026-06-08-blender-signal-value-research.md](analysis/2026-06/2026-06-08-blender-signal-value-research.md) | `snapshot` | yes | strict `22<=T<=28` 新 base 下研究 blender 本体：hard gate、size curve、alpha grid、walk-forward 均不支持 live gate |
| [2026-06-08-v1-removed-ecmwf-t28-blender-overlay.md](analysis/2026-06/2026-06-08-v1-removed-ecmwf-t28-blender-overlay.md) | `snapshot` | yes | 剔除 6 个弱 ECMWF 城市并 ban T>28 后，blender 相对新 base 的边际收益为负 |
| [2026-06-08-decisive-experiment-scripts-audit-and-handoff.md](analysis/2026-06/2026-06-08-decisive-experiment-scripts-audit-and-handoff.md) | `audit` | yes | 对外部草稿做本机 DB/gate/source 复核：标出哪些结论可用、哪些数字漂移、哪些脚本审计当前不可复核 |
| [2026-06-08-market-structural-edge.md](analysis/2026-06/2026-06-08-market-structural-edge.md) | `snapshot` | yes | H_B model-free 市场结构检验第一版：按日期前瞻、cluster bootstrap、Bonferroni 风险；当前 verdict=inconclusive |
| [2026-06-08-executable-edge.md](analysis/2026-06/2026-06-08-executable-edge.md) | `snapshot` | yes | Step2 执行现实检验：live_real fill 审计 + decision-entry proxy + time-aligned raw orderbook 2B；当前 verdict=inconclusive |
| [2026-06-09-weather-strategy-research-window-handoff.md](analysis/2026-06/2026-06-09-weather-strategy-research-window-handoff.md) | `handoff` | yes | 本窗口策略研究收口：H_A/H_B/H_C/Step2B verdict、Range RV Scanner v0 下一窗口提示词 |
| [2026-06-09-range-rv-scanner-v0.md](analysis/2026-06/2026-06-09-range-rv-scanner-v0.md) | `snapshot` | yes | Range RV Scanner v0：city-day 区间/相邻 bracket relative value，train 显著和基准过但 holdout 前瞻失败，当前 verdict=inconclusive |
| [2026-06-09-range-rv-scanner-v0-1.md](analysis/2026-06/2026-06-09-range-rv-scanner-v0-1.md) | `snapshot` | yes | Range RV Scanner v0.1：单腿/true-range、seen-complete/eligible-only 分家复核；所有 family 前瞻门仍失败，当前 verdict=inconclusive |
| [2026-06-09-range-rv-positive-v0-2.md](analysis/2026-06/2026-06-09-range-rv-positive-v0-2.md) | `snapshot` | yes | Range RV v0.2 正实验：预注册 eligible adjacent_3 long profiles；decision proxy 点估计正但 excess CI / orderbook forward 不过，当前 verdict=inconclusive |
| [2026-06-09-range-rv-variant-lab-v0-3.md](analysis/2026-06/2026-06-09-range-rv-variant-lab-v0-3.md) | `snapshot` | yes | Range RV v0.3 正实验：9 个预注册区间/相邻/尾部/单腿允许表达，proxy/orderbook 三门均未同时通过，当前 verdict=inconclusive |
| [2026-06-09-range-rv-walkforward-v0-4.md](analysis/2026-06/2026-06-09-range-rv-walkforward-v0-4.md) | `snapshot` | yes | Range RV v0.4 正实验：expanding-window 只用历史日期选择算法，点估计好但 excess CI/top5 stress/orderbook 活跃日期不过，当前 verdict=inconclusive |
| [2026-06-09-range-rv-market-shape-v0-5.md](analysis/2026-06/2026-06-09-range-rv-market-shape-v0-5.md) | `snapshot` | yes | Range RV v0.5 正实验：盘口 implied distribution shape anomaly first，eligible-only 口径下 holdout/top5/orderbook 不稳，当前 verdict=inconclusive |
| [2026-06-09-range-rv-temporal-reversion-v0-6.md](analysis/2026-06/2026-06-09-range-rv-temporal-reversion-v0-6.md) | `snapshot` | yes | Range RV v0.6 正实验：同 city-day 前后 snapshot 市场过冲/反转表达，可形成样本仅 20 行，当前 verdict=inconclusive |
| [2026-06-09-range-rv-market-shape-fullop-v0-7.md](analysis/2026-06/2026-06-09-range-rv-market-shape-fullop-v0-7.md) | `snapshot` | yes | Range RV v0.7 正实验：移除旧 eligible 硬门、使用 full fact opportunity + spread/orderbook 约束；样本扩大但三门仍不过，当前 verdict=inconclusive |
| [2026-06-09-range-rv-regime-v0-8.md](analysis/2026-06/2026-06-09-range-rv-regime-v0-8.md) | `snapshot` | yes | Range RV v0.8 正实验：按模型/市场分布 regime 固定表达，full opportunity 口径下仍因 holdout/top5/orderbook 不稳，当前 verdict=inconclusive |
| [2026-06-09-range-rv-noarb-v0-9.md](analysis/2026-06/2026-06-09-range-rv-noarb-v0-9.md) | `snapshot` | yes | Range RV v0.9 正实验：互斥 bracket no-arb；all-YES underround proxy confirmed，orderbook 接近但初版阈值下 forward 门贴边不过 |
| [2026-06-09-range-rv-underround-robust-v1-0.md](analysis/2026-06/2026-06-09-range-rv-underround-robust-v1-0.md) | `snapshot` | yes | Range RV v1.0 confirmed：model-free all-YES underround，proxy 0.01-0.05 与 executable 0.005-0.05 阈值均通过三门；下一步仅做 shadow/paper 工程化，不直接改 live |
| [2026-06-09-forecast-quality-regime-signal-value.md](analysis/2026-06/2026-06-09-forecast-quality-regime-signal-value.md) | `snapshot` | yes | forecast quality regime 研究：用 fact_signal_candidates 构造 entropy/mode/adjacent/tail/calibration features，给 Range RV planner 提供 low/medium/high/tail-overpriced regime；不输出 live action |
| [2026-06-09-forecast-quality-range-rv-overlay.md](analysis/2026-06/2026-06-09-forecast-quality-range-rv-overlay.md) | `snapshot` | yes | forecast quality filter overlay：对 forecast-first adjacent2/3 Range RV 做不筛/宽松/中等/严格过滤对比；proxy 为正但严格过滤偏死，未做 executable 三门，不输出 live action |
| [2026-06-10-forecast-quality-range-rv-city-model.md](analysis/2026-06/2026-06-10-forecast-quality-range-rv-city-model.md) | `snapshot` | yes | forecast quality Range RV 城市/模型/数据积累分层：ECMWF 候选强于 GFS，city+model 样本薄；给出 `range_rv_forecast_quality_probe_v0` 小额试探候选，不改 live |
| [2026-06-09-forecast-first-adjacent-range-rv-v0-1.md](analysis/2026-06/2026-06-09-forecast-first-adjacent-range-rv-v0-1.md) | `snapshot` | yes | Forecast-first adjacent2/3 Range RV：full opportunity 口径、按 event_date train/holdout、orderbook all-leg matched；significance/forward 不过，当前 verdict=inconclusive |
| [2026-06-09-range-rv-tail-fade-uncertainty-v1-1.md](analysis/2026-06/2026-06-09-range-rv-tail-fade-uncertainty-v1-1.md) | `snapshot` | yes | Tail fade / uncertainty Range RV：BUY_NO tail + BUY_YES adjacent inner/center，full opportunity 口径、event_date split、orderbook executable 复核；holdout 样本/top5 stress 不足，当前 verdict=inconclusive |
| [2026-06-09-center-shoulders-butterfly-range-rv.md](analysis/2026-06/2026-06-09-center-shoulders-butterfly-range-rv.md) | `snapshot` | yes | Range RV 专项：forecast-first center vs shoulders / butterfly 表达；center/band/tail proxy 超额不支持，shoulders 便宜样本太少且 orderbook train 覆盖不足，当前 verdict=inconclusive |
| [2026-06-10-side-band-forecast-regime-v0.md](analysis/2026-06/2026-06-10-side-band-forecast-regime-v0.md) | `snapshot` | yes | Side Band + Forecast Regime Clean Test v0：full opportunity 复现/推广 side-band 并叠加 forecast regime；旧形态历史赚过但 top5 stress/holdout 失败，三门不过，当前 verdict=inconclusive |
| [2026-06-10-weather-strategy-live-test-selection.md](analysis/2026-06/2026-06-10-weather-strategy-live-test-selection.md) | `snapshot` | yes | 本轮子 agent 策略研究总控选择：没有 real live 候选；只建议 `forecast quality soft gate + adjacent3 range` 进入 shadow/paper 观测 |
| [2026-06-10-adjacent3-quality-shadow-journal-v0.md](analysis/2026-06/2026-06-10-adjacent3-quality-shadow-journal-v0.md) | `snapshot` | yes | Adjacent3 + forecast quality 固定 shadow journal：只记录 would-trade/orderbook 覆盖，不改 live；当前样本薄且三门不过 |
| [2026-06-10-hybrid-adjacent3-single-v0.md](analysis/2026-06/2026-06-10-hybrid-adjacent3-single-v0.md) | `snapshot` | yes | Hybrid adjacent3 + single / outside NO v0：验证单腿多为 adjacent3 内重复加注，outside NO 固定口径未触发；当前三门不过 |
| [2026-06-10-live-test-readiness-scoreboard-v0.md](analysis/2026-06/2026-06-10-live-test-readiness-scoreboard-v0.md) | `snapshot` | yes | Live-test readiness 总表：当前无真钱 live 候选；冻结 `forecast_quality_medium_adjacent3_shadow_v0` 为主 shadow 规则和硬门 |
| [2026-06-09-decision-window-backfill.md](analysis/2026-06/2026-06-09-decision-window-backfill.md) | `snapshot` | yes | `fact_signal_candidates` decision window 回填：用 city/date anchor + raw orderbook + 0.005 磨损补 2186 行；只改本机分析 DB |
| [2026-06-09-model-rank-ic.md](analysis/2026-06/2026-06-09-model-rank-ic.md) | `snapshot` | yes | Ring3 模型排序/IC 检验：`model_edge_at_decision` 显著性、基准、前瞻均 FAIL；当前 verdict=inconclusive |
| [2026-06-10-side-band-forecast-regime-clean-test-v0.md](analysis/2026-06/2026-06-10-side-band-forecast-regime-clean-test-v0.md) | `superseded` | no | Side Band + Forecast Regime Clean Test 早期重复版本；当前决策改读 `2026-06-10-side-band-forecast-regime-v0.md` 的 cost-proxy 修正版 |
| [2026-06-08-city-model-conditional-edge.md](analysis/2026-06/2026-06-08-city-model-conditional-edge.md) | `snapshot` | yes | H_C city×model×side 条件优势检验：train 选池、holdout 复核、matched side+price baseline；当前 verdict=inconclusive |
| [2026-06-08-HANDOFF-LANDING-VALIDATION.md](analysis/2026-06/2026-06-08-HANDOFF-LANDING-VALIDATION.md) | `snapshot` | yes | 交接包文件落地、manifest 产物补齐、skill/contract/schema 假设和 git 状态校验 |
| [2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md](analysis/2026-06/2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md) | `design-draft` | yes | 交接包审阅和 P0-P3 改进计划 |
| [2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md](analysis/2026-06/2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md) | `design-draft` | yes | 策略可靠性审计和 Step1/Step2/Step3 迁移执行顺序 |
| [2026-06-08-city-day-basket-vs-legacy-baselines.md](analysis/2026-06/2026-06-08-city-day-basket-vs-legacy-baselines.md) | `snapshot` | yes | 最新 fact 重建后 basket vs legacy baseline refresh；recent slice 仍不支持上线 |
| [2026-06-08-city-day-basket-walkforward.md](analysis/2026-06/2026-06-08-city-day-basket-walkforward.md) | `snapshot` | yes | 最新 fact 重建后 city-day basket walk-forward refresh；用于拒绝过拟合候选 |
| [2026-06-08-city-day-distribution-quality.md](analysis/2026-06/2026-06-08-city-day-distribution-quality.md) | `snapshot` | yes | 最新 fact 重建后 city-day 分布质量 refresh；market-normalized distribution 仍优先 |
| [2026-06-08-side-band-entry-timing-impact.md](analysis/2026-06/2026-06-08-side-band-entry-timing-impact.md) | `snapshot` | yes | side-band live_real 的 entry timing 与日度 PnL 关系；`>T-28` 不适合对 side-band 简单套用 v1 规则 |
| [2026-06-08-entry-timing-rigorous-research-plan.md](analysis/2026-06/2026-06-08-entry-timing-rigorous-research-plan.md) | `design-draft` | yes | 针对 `<T-22`、`T-24-26`、`>T-28` 与天气预报更新卡点的完整 timing 研究计划：分母、matched、机制、city-day、shadow/live gate |
| [2026-06-08-entry-timing-effect-baseline.md](analysis/2026-06/2026-06-08-entry-timing-effect-baseline.md) | `snapshot` | yes | 最新 sync + fact rebuild + CLOB coverage gate 后的 entry timing 第一轮基线：L0 signals、L1 decision-window candidates、L2 submitted、L3 live_real、L4 city-day；forecast checkpoint 仍是 data gap |
| [2026-06-08-city-x-entry-timing-research.md](analysis/2026-06/2026-06-08-city-x-entry-timing-research.md) | `snapshot` | yes | 最新 sync + fact rebuild + CLOB coverage gate 后的城市 x timing 切片：确认 `T-26-28` 先从 live 移除，`T-24-26` 需要城市和 forecast checkpoint 复核 |
| [2026-06-07-blended-live-instance-overlay.md](analysis/2026-06/2026-06-07-blended-live-instance-overlay.md) | `snapshot` | yes | blender 叠加到真实 live instance fills 的控制变量 overlay；6 月后改善但 6 月前误杀盈利 |
| [2026-06-07-v1-raw-regime-filter-walkforward.md](analysis/2026-06/2026-06-07-v1-raw-regime-filter-walkforward.md) | `snapshot` | yes | v1 raw 退化后的 gate walk-forward：城市层风控强于纯 blended edge gate |
| [2026-06-06-city-alpha-framework.md](analysis/2026-06/2026-06-06-city-alpha-framework.md) | `snapshot` | yes | 城市 alpha 评价体系、paper->live 扩池反转、city x side gate |
| [2026-06-06-city-day-distribution-quality.md](analysis/2026-06/2026-06-06-city-day-distribution-quality.md) | `snapshot` | yes | raw/market/blend_norm 分布质量和 holdout 退化问题 |
| [2026-06-06-blended-single-v0-backtest.md](analysis/2026-06/2026-06-06-blended-single-v0-backtest.md) | `snapshot` | yes | blended single v0 shadow 策略回测、策略身份和 opportunity 对比；settled/live 结论以 2026-06-07/08 重算为准 |
| [2026-06-06-blended-entry-band-backtest.md](analysis/2026-06/2026-06-06-blended-entry-band-backtest.md) | `snapshot` | yes | 保留 live 入场区间后的 blend gate 公平对比；settled/live 结论以 2026-06-07/08 重算为准 |
| [2026-06-06-city-day-basket-vs-legacy-baselines.md](analysis/2026-06/2026-06-06-city-day-basket-vs-legacy-baselines.md) | `snapshot` | yes | basket vs 旧 per-bucket raw 策略的同窗、同 entry-band baseline 对比 |
| [2026-06-06-city-day-basket-walkforward.md](analysis/2026-06/2026-06-06-city-day-basket-walkforward.md) | `snapshot` | yes | city-day basket 目标选择 walk-forward：用于拒绝过拟合候选，不批准上线 |
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
