# Weather 交接包落地与校验

> 生成于 2026-06-08。本文件只做交接包落地状态校验，不跑策略实验、不移动评估层文件、不改 N100 live 行为。

## 1. 已落地文件

| 文件 | 状态 | 校验 |
|---|---|---|
| `docs/WEATHER_HANDOFF_EXECUTION.md` | 已新增 | UTF-8，标题正常 |
| `docs/WEATHER_ARCHITECTURE_SPINE.md` | 已从附件落地 | UTF-8，标题正常 |
| `docs/analysis/_ANALYSIS_COVERAGE_MAP.md` | 已从附件落地 | UTF-8，标题正常 |
| `docs/analysis/2026-06/2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md` | 已从附件落地 | UTF-8，标题正常 |
| `docs/analysis/2026-06/2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md` | 已新增 | UTF-8，标题正常 |

文件权限已校验为普通文档权限 `rw-r--r--`。

## 2. 文件完整性快照

| 文件 | SHA256 |
|---|---|
| `docs/WEATHER_HANDOFF_EXECUTION.md` | `2c0866c2bd43be2bc4f07d0745f05066e8f2afa2c7a442cc1b728944b9f27751` |
| `docs/WEATHER_ARCHITECTURE_SPINE.md` | `bde7c689def2e6ca3186288a1ff7754e5e5eccb90d232c264b8a1c14b8480a1c` |
| `docs/analysis/_ANALYSIS_COVERAGE_MAP.md` | `92d84863a0cbf94cc958df9a685d152dbde5aee605d7e8628126a56b016549ac` |
| `docs/analysis/2026-06/2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md` | `027664bc68f06e3f33342b258084b8506991c233a7e1c9bfa5fa9ed3eb03da85` |
| `docs/analysis/2026-06/2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md` | `9e917f2ea9d0f5a4a710786fd5ab71f1327474bd8da746a9c05b01c8179b8a24` |
| `docs/analysis/model_vs_market.md` | `afe6a60a900b298d2aa700a35b6562de782a90251a72df9df2babc88b2da0a85` |
| `scripts/analysis/research_market_structural_edge.py` | `d0ac2bc2badede0f3d1970d2a44e24118d34fefde377b996d044a1f3a8f2bc03` |
| `scripts/analysis/research_executable_edge.py` | `d634bc702069967d1ed4dc547d3fb862470d7e8854b655e5d42cc75679658f8d` |
| `scripts/ops/reorg_eval_layer.sh` | `b8b476ed1b54e69901286b31690fb9ffb0bd1b99165bd5f75e9e877a724a0281` |

## 3. Manifest 产物状态

交接文档列出的以下配套产物已在本轮补齐：

| 文件 | 当前状态 | 验证 |
|---|---|---|
| `docs/analysis/model_vs_market.md` | 已补齐 | living doc 样板可读 |
| `scripts/analysis/research_market_structural_edge.py` | 已补齐 | `py_compile`、`--help`、低 bootstrap `/tmp` smoke 通过 |
| `scripts/analysis/research_executable_edge.py` | 已补齐 | `py_compile`、`--help`、低 bootstrap `/tmp` smoke 通过 |
| `scripts/ops/reorg_eval_layer.sh` | 已补齐 | dry-run 通过；默认不移动历史文件 |

结论：当前交接包已经完成“文档入口落地”和“配套产物补齐”。两个实验脚本已经做运行时 smoke，
但正式研究结果仍未发布；后续正式跑数必须按 contract 先做数据同步/重建/coverage gate，并把输出写入 `docs/analysis/2026-06/`。

## 4. Skill 与 Contract 校验

当前 `skills/weather-strategy-performance/SKILL.md` 已包含三道门：

| 条款 | 当前状态 |
|---|---|
| 显著性门 | 已存在，要求 bootstrap 95% CI |
| 基准门 | 已存在，要求相对零模型超额 |
| 前瞻门 | 已存在，未前瞻只能 `shadow_candidate` |
| 结论分级 | 已存在：`confirmed` / `shadow_candidate` / `inconclusive` |
| 样本门槛 | 已存在：城市/切片级 keep/cut 默认需要 `active_days >= 10` 且 `settled_fills >= 30` |
| `n_eff` | 已存在，作为相关性折减报告项 |

`docs/WEATHER_ANALYSIS_CONTRACT.md` 已补入：

- “绩效结论三道门（硬规定）”
- “8 环覆盖自检（报告必须声明）”
- “执行微结构三源口径（硬规定）”

`skills/weather-strategy-performance/SKILL.md` 已明确：若与 contract 冲突，以 contract 为准。

## 5. Schema 假设校验

本地 `runtime/weather.db` 当前 schema 与交接文档的部分字段假设不完全一致：

| 字段/口径 | `fact_trades` | `fact_signal_candidates` | 校验结论 |
|---|---:|---:|---|
| `fill_price` | 有 | 无直接同名主字段；有 `live_fill_price` | 已成交绩效读 `fact_trades` |
| `model_p_yes` | 有 | 有 | 两表均可用于各自 grain |
| `market_price` | 有 | 未检出同名 | 已成交侧可用 |
| `edge` / `abs_edge` | 有 | 有 | 两表均可用于各自 grain |
| `notional` | 有 | 未检出同名 | 已成交侧可用 |
| `best_ask` | 未检出 | 未检出 | 不应声称在 fact 表直接可用 |
| `spread` | 未检出同名 | 有 `yes_spread` / `no_spread` | 机会粒度可用，已成交侧需 join 或另取源 |
| `counterfactual_pnl` | 未检出 | 有 | 反事实分析读 `fact_signal_candidates` |
| `decision_entry_price` / `best_entry_price` | 未检出 | 有 | 执行微结构分析应读候选表和 orderbook 源 |

修正建议：把交接文档里“`best_ask/spread/fill_price/counterfactual_pnl` 都在 `fact_trades`”改成“三源口径”：

1. `fact_trades`：真实成交 fill、已成交 realized PnL、fill price、模型/市场基础字段。
2. `fact_signal_candidates`：机会粒度 spread、decision/best entry、反事实 PnL。
3. orderbook snapshot + token map：决策时 best ask / depth / 可成交 edge。

## 6. 文档索引状态

`docs/WEATHER_DOCS_INDEX.md` 已补充以下入口：

- `WEATHER_HANDOFF_EXECUTION.md`
- `WEATHER_ARCHITECTURE_SPINE.md`
- `docs/analysis/model_vs_market.md`
- `2026-06-08-market-structural-edge.md`
- `2026-06-08-executable-edge.md`
- `2026-06-08-HANDOFF-LANDING-VALIDATION.md`
- `2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md`
- `2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md`

`AGENTS.md` / `CLAUDE.md` 暂未同步扩展长入口；它们仍只保留高频入口，长表以 `WEATHER_DOCS_INDEX.md` 为准。

## 7. Git 状态备注

本次交接包相关新增文件为：

- `docs/WEATHER_HANDOFF_EXECUTION.md`
- `docs/WEATHER_ARCHITECTURE_SPINE.md`
- `docs/analysis/model_vs_market.md`
- `docs/analysis/_ANALYSIS_COVERAGE_MAP.md`
- `docs/analysis/2026-06/2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md`
- `docs/analysis/2026-06/2026-06-08-HANDOFF-REVIEW-AND-IMPROVEMENT-PLAN.md`
- `docs/analysis/2026-06/2026-06-08-HANDOFF-LANDING-VALIDATION.md`
- `docs/analysis/2026-06/2026-06-08-market-structural-edge.md`
- `docs/analysis/2026-06/2026-06-08-executable-edge.md`
- `scripts/analysis/research_market_structural_edge.py`
- `scripts/analysis/research_executable_edge.py`
- `scripts/ops/reorg_eval_layer.sh`

另外当前工作区已有未跟踪文件，不属于本次交接包：

- `tmp_check_n100_city_pool.sh`
- `tmp_paper_live_gap_new_cities.py`
- `docs/analysis/2026-06/2026-06-08-decisive-experiment-scripts-audit-and-handoff-draft.md`

这些文件未在本次校验中改动。

## 8. 当前结论

交接包的“入口文档落地”“配套产物补齐”“contract 口径固化”已完成。Step1/Step2 已使用当前本机 DB 快照生成离线诊断报告：

- `docs/analysis/2026-06/2026-06-08-market-structural-edge.md`
- `docs/analysis/2026-06/2026-06-08-executable-edge.md`

两份报告的三道门 verdict 都是 `inconclusive`，不能据此改 live。后续已转入模型排序能力 IC/rank 补环，并新增：

- `scripts/analysis/research_model_rank_ic.py`
- `docs/analysis/2026-06/2026-06-09-model-rank-ic.md`

Ring3 初筛结论同样是 `inconclusive`：`model_edge_at_decision` 的 IC 置信区间跨 0，train 选出的 top-rank 规则在 holdout 上未显著成立，不能转成 live gate。

下一步若继续，应先决定是否允许执行 N100 sync + rebuild 刷新数据后重跑三组实验，或进入只降风险/停 live gate 的策略收缩路线。
