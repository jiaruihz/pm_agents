# Weather 交接包审阅与改进计划

> 生成于 2026-06-08。本文件审阅 `WEATHER_HANDOFF_EXECUTION.md` 及其三份附件落地后的本地事实，
> 目标是区分“方法论是否正确”和“当前交接包是否已经可执行”。

## 1. 本地落盘状态

已落盘：

| 文件 | 状态 |
|---|---|
| `docs/WEATHER_HANDOFF_EXECUTION.md` | 已新增 |
| `docs/WEATHER_ARCHITECTURE_SPINE.md` | 已从附件落盘 |
| `docs/analysis/_ANALYSIS_COVERAGE_MAP.md` | 已从附件落盘 |
| `docs/analysis/2026-06/2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md` | 已从附件落盘 |

本地未发现，但 manifest 声称存在或应随包迁移：

| 文件 | 问题 |
|---|---|
| `docs/analysis/model_vs_market.md` | 交接文档列为 living doc 样板，但本地未提供 |
| `scripts/analysis/research_market_structural_edge.py` | Step1 决定性实验脚本缺失 |
| `scripts/analysis/research_executable_edge.py` | Step2 决定性实验脚本缺失 |
| `scripts/ops/reorg_eval_layer.sh` | 重构执行脚本缺失 |

## 2. 审阅结论

整体方向有道理：这批文档准确抓住了当前 weather 研究链路的主要风险：小样本描述性切片过多、缺置信区间和零模型、后验筛城市污染 live 配置、执行微结构没有被单独验证。把结论分成 `confirmed` / `shadow_candidate` / `inconclusive`，并强制通过显著性、基准、前瞻三道门，是正确的工程约束。

但当前交接包还不是可执行态，不能直接按文档跑：

1. `weather-strategy-performance` skill 当前已经包含三道门、结论分级、bootstrap 95% CI、样本门槛和 `n_eff` 条款；这一点已补齐。
2. `WEATHER_ANALYSIS_CONTRACT.md` 还没有吸收三道门和 8 环覆盖自检。当前真正的硬约束仍然是同步、fact 表、PnL 公式、CLOB 对账和报告头规则。下一步应把 contract 补成硬来源，再让 skill 引用它。
3. 文档里“`best_ask/spread/fill_price/counterfactual_pnl` 都在 `fact_trades`”这一句不准确。本地 DB schema 显示 `fact_trades` 有 `fill_price/model_p_yes/market_price/edge/notional`，但没有 `best_ask/spread/counterfactual_pnl`；`fact_signal_candidates` 有 `yes_spread/no_spread/decision_entry_price/best_entry_price/live_fill_price/counterfactual_pnl/counterfactual_pnl_best`。执行微结构分析需要改成 fact_trades + fact_signal_candidates + orderbook snapshot/token map 三源合并。
4. “H_A 基本证伪 / H_B 可能存在”的表述可以作为研究假设，不宜作为 live 结论。因为配套 Step1/Step2 脚本缺失，且审计文档自己也承认未在真实 DB 上跑过。
5. `[6]` 评估层重构方向正确，但 `DRY_RUN=0` 批量移动 76 文档、JSON 和脚本属于中风险操作。必须先做 manifest 审核、git mv、索引更新和 smoke，而不是直接跑未提供的脚本。

## 3. 改进计划

### P0：先把交接包从“声明态”修成“可执行态”

1. `model_vs_market.md`、`research_market_structural_edge.py`、`research_executable_edge.py`、`reorg_eval_layer.sh` 已补齐，并已通过 `py_compile` / `--help` / dry-run 或 `/tmp` smoke。正式实验结果仍需按 contract 数据门禁重新生成。
2. 修改 `docs/WEATHER_HANDOFF_EXECUTION.md` 和审计文档里的 manifest 状态：把“已改 / 已写 / 已自测”限定为“本地已补齐并 smoke”，不要暗示已经完成正式研究结论。
3. 把 `best_ask/spread` 字段口径修正为：
   - `fact_trades`：已成交真实 fill 和模型/市场基础字段。
   - `fact_signal_candidates`：机会粒度 spread、decision/best entry、反事实 PnL。
   - orderbook snapshot + token map：Step2B 的决策时 best ask / depth。
4. 将三道门写入 `WEATHER_ANALYSIS_CONTRACT.md`，并让 `skills/weather-strategy-performance/SKILL.md` 明确引用 contract。contract 是硬来源，skill 只引用并执行，不单独发明口径。

### P1：跑决定性实验，但必须先做数据门禁

1. 按 contract 先跑 `scripts/ops/sync_weather_remote.sh` 和 `scripts/weather_dashboard/run_stack.sh`，并记录 `MAX(fact_built_at_utc)`、`trade_class`、`settlement_status`、`fact_signal_candidates` 覆盖和 CLOB coverage gate。
2. Step1 只回答 model-free 市场结构问题：market implied probability vs realized frequency、同价位 dumb BUY_NO/BUY_YES baseline、train 选价桶后 holdout 是否同号。
3. Step2 拆成 2A/2B：
   - 2A 用 `fact_trades` 真实成交，检验 edge 是否预测 realized PnL、是否由 top city/side 少数样本驱动。
   - 2B 用 `fact_signal_candidates` + orderbook token map，检验点差、best ask、fill/unfill 选择偏差是否吃掉 edge。
4. 输出必须只给三类结论：`confirmed`、`shadow_candidate`、`inconclusive`。未过前瞻门时禁止改 live city pool、entry band 或 size。

### P2：重构评估层，先小批量再全量

1. 先只建立 11 篇 living doc 空壳和索引，不移动旧文件。
2. 选 `model_vs_market.md` 和 `execution_quality.md` 两篇做试点：从旧快照摘录结论、标注样本窗口、数据版本、kill 判据状态。
3. 试点通过后再移动 `_archive/`，同时更新 `docs/WEATHER_DOCS_INDEX.md`，避免新旧口径并列造成 agent 误读。
4. JSON 移出 git 前先生成清单：路径、大小、是否被当前文档引用、目标 runtime 位置、是否需要 `.gitignore`。

### P3：把 live 配置回写制度化

1. 新建一个“预注册配置”模板：假设、窗口、selector、样本门槛、holdout 起止、允许动作、回滚条件。
2. 所有 city pool / T28 / entry band / size 变更必须从 confirmed 结论生成，并走 `weather-strategy-deploy` 的 git-first 流程。
3. 对 shadow_candidate 只允许 shadow/paper 实验，不允许直接影响 N100 live。

## 4. 下一步建议

先做 P0。P0 完成前，这批文档只能作为方法论和交接说明，不能作为“脚本已验证、可直接部署或重构”的依据。
