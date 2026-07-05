# tmax 概率路径一致性问题 — Lucknow 7/05 replay 定位 v1

上游：`2026-07-05-tmax-model-improvement-plan-v1.md`（完善计划）·
`2026-07-05-tmax-first-live-lucknow-review-v1.md`（首 live 日复盘）·
`2026-07-02-tmax-distribution-first-principles-research-program-v1.md`（纲领）
证据来源：commit `527846f4`（四桶分布落盘接入 candidate→plan→order 血缘）+ Codex 对
snapshot `2026-07-05T05:33:23Z` 的模型 replay。

## 1. Replay 纠正了什么

11:03 本地（running max 35）的 blended 分布：

| 桶 | 含义 | 概率 |
|---|---|---:|
| current | 停在 35 | 14.5% |
| d1 | 正好 36 | 30.8% |
| d2 | 正好 37 | **49.5%** |
| tail | 38+ | 5.3% |

**第一笔 36-NO 是信念一致的表达**：模型主信念就是 37（49.5%），36-NO 押"不正好停在 36"
（胜率 69.2%，与旧日志 68.4% 吻合），赢在 37 正是它的主路径。此前复盘里"信念可能是停 35 /
错错得对"的猜测被证伪——这正是分布落盘的价值：猜测变成可审计事实。d1_no 是 exact bracket
市场里做空精确档位的合法补集表达，不是脏表达（对齐完善计划 §0a 修订版）。

## 2. 真正的病灶：跨小时概率路径不自洽（可量化）

把 11:03 的分布按贝叶斯条件化在"已到达 36"上（在 {36, 37, 38+} 归一）：

```text
P(停36 | 已到36) = 30.8 / (30.8+49.5+5.3) = 36.0%
P(37   | 已到36) = 57.8%
P(38+  | 已到36) = 6.2%
```

而 12:09 重新拟合的输出：停 36 = **57.7%**，37 = 34.5%，38+ ≈ 7.8%。

即：在"提前一小时到达 36 + trend_3h +3.6°F + 未过峰"这些**利好 overshoot** 的证据下，
模型把 P(≥37) 从 ~55% 砍到 ~42%，P(停) 比自身先验的贝叶斯后验高出 ~22 个百分点。
时间流逝（剩余加热窗口变短）可以解释其中一部分上移，但当时距 forecast peak 仍有 ~50 分钟、
路径在加速——定性方向也不该反转。13:03 更是升到 70.9% 还想加仓。

**机制诊断**（与代码一致）：每小时独立重拟合 + current/d1/d2 桶随 running max 重贴标签 +
p_current 训练基率被 E2 删失抬高 + C=0.03 强正则压扁反向特征 → 重锚瞬间，
"新高会 hold"的基率直接覆盖模型自己一小时前的信念。没有任何时序状态把上午的信念带下来。
副证：12:17 市场给 36-YES 定价 0.48，raw model 给 0.72——**模型比市场锚得更狠**，
而纲领的 alpha 论点本来是做空市场的锚启发式。

## 3. "该不该以最新为准" 的判据：martingale 性质

- 校准的信念过程是 martingale：概率变化必须**事前不可预测**。这样的变化再频繁也不是模型缺陷，
  该不该跟着换仓完全交给执行层的成本门槛（增量 EV > 平仓点差 + 双边 fee + 逆选择缓冲）。
- 反之，"每打新高 → P(停在新高) 跳向基率、无视自身先验"是**可预测的系统性模式**——
  这是模型缺陷，市场甚至可以反向收割它。Lucknow 的 22 个百分点就是一次实测。
- 所以"方向频繁变 = 模型不好"只在变化可预测时成立；判据可以在全量 shadow 行上直接测量（§4 P0e）。

两层修复解耦：**模型层修一致性**（P0e 审计 → P1 hazard/时序状态），
**执行层修磨损**（target-book reconciliation 只在 replay 中测试，first-lock 仍是 live 主口径）。

## 4. 新增实验 P0e — 跨小时一致性审计（并入完善计划 §1，排在 P0c 旁）

对每个 city-day 的相邻 state 行对 (t, t+1)：

1. 把 t 时刻分布按 t→t+1 之间揭示的信息（新高与否、到达哪档）做贝叶斯条件化，得 coherent 后验；
2. 与 t+1 的重拟合输出比较，记录 `incoherence = P_refit(current) − P_coherent(current)`；
3. 按"是否发生重锚（新高打印）"分组，报告 incoherence 的均值/分位数与符号。

预期若假说成立：重锚组的 incoherence 显著为正（重拟合系统性高估新档 hold），
非重锚组接近 0。全部用现有 atlas/shadow 行，零新数据。这个审计同时给 P1 的时序修复
（前小时条件化后验作为先验/特征，或 hazard 增量化）提供靶子和验收基线。

## 5. 给 Codex 的接续提示词（存档版）

见本文档同日 chat 记录；正文如下——

> 背景：tmax_distribution_edge 首 live 日（Lucknow 2026-07-05）复盘已定位模型病灶为
> "跨小时概率路径不自洽"：11:03 blended 分布 35/36/37/38+ = 14.5/30.8/49.5/5.3%，
> 贝叶斯条件化"已到 36"后应为停36≈36%/37≈58%，但 12:09 重拟合给出停36=57.7%/37=34.5%，
> 在利好 overshoot 证据下反向移动 ~22pts。机制：小时级独立重拟合 + 桶随 running max 重锚 +
> p_current 基率被 settlement-basis 删失抬高（E2）+ C=0.03 强正则。参考文档：
> docs/analysis/2026-07/2026-07-05-tmax-probability-path-coherence-v1.md、
> 2026-07-05-tmax-model-improvement-plan-v1.md、
> 2026-07-02-tmax-distribution-first-principles-research-program-v1.md。
>
> 请按以下顺序推进（验收标准先冻结再开工，全部零 live 变更）：
>
> 1. **P0e 跨小时一致性审计**：在全量 atlas/shadow state 行上，对相邻小时对计算
>    "贝叶斯条件化先验 vs 重拟合后验"的 incoherence（定义见文档 §4），按重锚事件分组报告。
>    输出 docs/analysis/2026-07/2026-07-06-tmax-cross-hour-coherence-audit-v1.md。
> 2. **P0c p_current 校准审计**：reliability curve，按 hours_to_forecast_peak（前/后）×
>    temp_trend_3h（升/平/降）× forecast_ceiling_margin（≥+2°C / <+2°C）切片，
>    重点验证"未过峰×升温×大 ceiling margin"切片是否系统性高估。
> 3. **P0b E2 settlement-basis 层**：current bracket 判定换到结算单位，重跑 P1 评分与 P2/P5 EV，
>    重估 current_yes 符号。
> 4. **P1 时序一致性修复**（P0e 证实后）：两个候选方案同台——(i) 逐度 competing-risk hazard
>    对增量建模；(ii) 现架构 + 前小时条件化后验作为先验/特征。验收 = 冻结 forward
>    （6/21 线滚动）上 log-loss/Brier 稳定优于市场隐含分布，且 P0e 的 incoherence 显著收敛。
>    同场跑 P1b α∈{0,0.25,0.5,0.75,1} 消融。
> 5. **执行层 replay（不改 live）**：用 shadow blocked 行（city_day_after_first_selected）做
>    first-lock vs target-book reconciliation 同分母对比，换仓按 bid 成交并扣双边 taker fee 与点差，
>    报告换仓触发次数、磨损、净差。
>
> 硬约束：不重启/恢复 tmax live runner；current_yes 保持 shadow；不加新 gate/filter，
> 修根因；概率与标签全程 PIT；结论按三道门分级（confirmed / shadow_candidate / inconclusive），
> 报告落 docs/analysis/YYYY-MM/ 并 git commit。执行层 dedupe + 持仓感知 + clob fill sync
> 是独立并行任务，如未完成请先做。
