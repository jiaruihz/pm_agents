# Weather 策略 · 交接执行总文档

Status: design-draft
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; draft/design reference, not current production fact

> **这是迁移 agent 的唯一入口。** 本轮工作把"为什么这套策略一直找不到效果"诊断清楚，并把
> 分析体系、项目结构、分析 skill 一并升级到科学态。本文串起全部产出 + 执行顺序 + 校验清单。
> 生成 2026-06-08。**未 git push**，由你迁移。代码（src/、ops 运行脚本）本轮未改。

---

## 1. 三句话背景（先对齐再动手）

1. **诊断**：现有研究 90% 是"环1 描述性 PnL 切片 + 按实现 PnL 后验砍城市"，缺统计推断/基准/前瞻/
   执行微结构，所以一直在小样本上挖噪声（过拟合跑步机）。
2. **判决**：当前实盘绩效在统计上与掷硬币不可区分（零 edge 未被拒绝）；但有理论支撑的小 edge
   （市场结构 favorite-longshot，做市收割）可能存在，从没被干净测过。模型 alpha（H_A）基本证伪。
3. **出路**：换主线——从"让模型更准"换成"市场有没有 model-free 结构错价、扣点差还活不活"，
   并把分析方法从"描述"升级为"推断"（三道门）。

---

## 2. ★ skill 改了什么（你最关心的：以后"分析 X 天 X 策略"怎么变科学）

**改的文件**：`skills/weather-strategy-performance/SKILL.md`（已改到最终态，代码未动）。

### 旧版四宗罪（已写进 skill §0）

1. 点估计当结论——ROI/win_rate 不带置信区间就给 keep/cut。
2. **in-sample 后验切割被制度化**——第 7 步要求看实现 PnL 给"保留/砍"，无前瞻 → 这是
   "Warsaw 5月+57%→6月−2.5% 被一刀切"的根源，是过拟合发动机。
3. win_rate 当 edge——不和"无脑买 NO"零模型比，分不清 skill 还是 base-rate。
4. low_sample 阈值只有 5 fill，且把相关的城市-日当独立。

### 新版的科学契约（三道门，不可绕过）

以后你说"分析最近 N 天 X 策略效果如何"，skill 会强制走：

| 门 | 通过条件 | 不过 |
|---|---|---|
| 显著性门 | 指标 bootstrap 95% CI 不跨 0 | `inconclusive`，禁 live 动作 |
| 基准门 | 超额于零模型（无脑买NO/买市场）显著>0 | 只是 base-rate，不算 alpha |
| 前瞻门 | train 选出的结论在 holdout 同号且仍超额 | 只能 `shadow_candidate`，不改 live |

**结论强制分级**：`confirmed`（三门全过，可改 live）/ `shadow_candidate`（只能 shadow）/
`inconclusive`（样本不足或不显著，禁动作）。一句话总结的形态被钉死为：

> "在[窗口]，X 策略相对[零模型]超额 +Y%（CI[a,b]），前瞻[同号/否]，结论[等级]"
> ——**不再是**"X 策略 ROI +Y%，建议保留 A 城砍 B 城"。

新增还包括：有效样本 `n_eff = n/(1+(n−1)·ρ̄)`（相关性折减，阈值抬到 ≥30）、
执行微结构升级为真实 `best_ask/spread/fill_price` 口径、8 环覆盖自检。

> 其余分析 skill（lineage/exposure/account-reconcile）本轮未改；**建议后续把"三门 + 8环 + 不前瞻不给keep/cut"
> 抽进 `WEATHER_ANALYSIS_CONTRACT.md` 作为共享硬条款，让它们都引用**（本轮未动 contract，避免盲改）。

---

## 3. 全部产出文件清单（交接包）

| 文件 | 类型 | 作用 |
|---|---|---|
| `docs/WEATHER_HANDOFF_EXECUTION.md` | 本文 | 唯一入口 |
| `docs/WEATHER_ARCHITECTURE_SPINE.md` | 骨架 | [0]–[6] 主线 + [6] 评估层逐文件重构清单 |
| `docs/analysis/_ANALYSIS_COVERAGE_MAP.md` | 方法论 | 8 环覆盖图：有哪几环、缺哪几环、怎么补、用哪个已有字段 |
| `docs/analysis/model_vs_market.md` | living doc 样板 | 5 篇模型快照→1 篇；含 6 月翻负/BUY_NO 失守真实表格 |
| `docs/analysis/2026-06/2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md` | 审计 | "猜硬币 vs 真能赚"的正式判断 + Step1/2/3 |
| `skills/weather-strategy-performance/SKILL.md` | **已改 skill** | 升级为科学推断模式（本节 §2） |
| `scripts/analysis/market_structure_edge/research_market_structural_edge.py` | 脚本 | Step1：H_B 市场结构检验（已自测） |
| `scripts/analysis/execution_quality/research_executable_edge.py` | 脚本 | Step2：扣点差执行检验（已 compile） |
| `scripts/ops/reorg_eval_layer.sh` | 脚本 | [6] 评估层重构（DRY_RUN 验证过，未真跑） |

> 本机补齐与校验状态见
> [docs/analysis/2026-06/2026-06-08-HANDOFF-LANDING-VALIDATION.md](analysis/2026-06/2026-06-08-HANDOFF-LANDING-VALIDATION.md)。

原 skill 备份在 `/tmp/weather-perf-skill.orig.md`（本机临时，迁移前自取）。

---

## 4. 执行顺序（迁移 agent 按此推进）

**A. 先固化方法（低风险，先做）**

1. 落本文 + SPINE + COVERAGE_MAP 作为骨架与标准。
2. 确认改后的 `weather-strategy-performance/SKILL.md`，并把三门/8环抽进 `WEATHER_ANALYSIS_CONTRACT.md`。

**B. 跑决定性实验（回答"是不是幻觉"）**

3. Step1 `research_market_structural_edge.py` → No-Go 则停、重想方向；Go 则继续。
4. Step2 `research_executable_edge.py`（先过 VERIFY 清单）→ 看扣点差后 edge 是否还在。
5. 先补三把低成本刀：环5 执行微结构 / 环2 显著性 / 环8 基准（字段全现成）。

**C. 整理结构（中风险，审后批量跑）**

6. `DRY_RUN=0 bash scripts/ops/reorg_eval_layer.sh` 收敛 [6]：76 文档→11 living doc、JSON 移出 git、
   5月+退役归档、copy_trade/ETL 移出主线。
7. 按 model_vs_market.md 的样板，把其余 10 篇 living doc 也提取出来。

---

## 5. 跑脚本前必须 VERIFY 的 schema（不要盲跑）

- Step1：已对齐 `calibrate_weather_probability.py` loader，无需额外校验。
- Step2-2A：`PRAGMA table_info(fact_trades)` 确认 `fill_price/model_p_yes/market_price/edge/notional` 列名。
- Step2-2B：`token_id↔(condition_id,bracket,outcome)` 映射来源，导出 `--token-map`。
- skill 第 6.5/6.8 步引用 `best_ask/spread/fill_price/counterfactual_pnl/market_price`，跑前 `PRAGMA` 确认。

---

## 6. 明确不要做（anti-goals）

- 不要再增删城市/微调 timing/加模型变体去"找更好的回测数字"（环1 加切片≠加维度）。
- 不要在 `n_eff<30` 的城市上做 keep/cut。
- 不要把任何样本内后验切割当 edge 证据；不前瞻不给 live 动作。
- 不要把 ensemble 的 0.9% Brier 改善当显著 alpha。
- 不要用 `scp/rsync` 直推 N100 改 live 配置（走 `weather-strategy-deploy`）。
