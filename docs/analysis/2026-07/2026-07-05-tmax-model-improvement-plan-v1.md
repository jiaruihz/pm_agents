# tmax 模型完善计划 v1 — 由 Lucknow 7/05 首个 live 日触发

上游：`2026-07-02-tmax-distribution-first-principles-research-program-v1.md`（纲领不变，本文是其执行细化）
触发：`2026-07-05-tmax-first-live-lucknow-review-v1.md`（首 live 日复盘）

## 0. Lucknow 案例给纲领补充的三个具体证据

**(a) "补集赢了"= 固定表达集不够完整。** 11:00 的 d1_no(36) 同时覆盖"停在 35"和"冲过 36"
两类路径；这不是脏表达，而是 exact bracket 市场里做空某个精确档位的合法表达。
如果市场把 36 这个精确档位定贵了，36-NO 本来就应该赢。真正的问题是固定表达集
{current_yes, current_no, d1_no, d2_no} 没有全 ladder 的 exact YES/NO sibling matrix：
当模型认为最终大概率落在 37 时，系统没有自然地表达 37-YES，只能间接买 36-NO，
或在 36 打印后被重锚诱导去买 36-YES。纲领 §5 的"逐格分歧交易"应理解为
**整条 exact-bracket target book**，不是简单把 d1_yes 加进 combo。

**(b) 锚点重定位 = whipsaw 发生器。** current/d1/d2 桶随 running max 重新贴标签，每打一个新高，
模型就在新档位重新报"停留"。纲领 §5 的逐度 hazard 链（P(ΔT≥1), P(ΔT≥2|≥1), …）对增量建模，
可以减少重贴标签带来的概率跳变；但 whipsaw 不会只靠模型数学消失，必须由 target-book
reconciliation 负责：后续新表达只有在增量 EV 覆盖平仓点差、双边 fee 和逆选择缓冲后才允许换仓。
Lucknow 是这条重构的第一号动机案例。

**(c) 市场混合把对手的锚偏差进口进来。** p_used = 0.5×model + 0.5×market。今天市场自己就在
追 print 重锚（36-YES 随打印 0.35→0.48→0.63），α=0.5 等于把我们要做空的锚启发式掺进自己
的估计里。α 需要在冻结 forward 评分上做消融（P1b）。

## 1. 实验清单（按优先级，验收标准开工前冻结）

### P0a — 决策时四桶分布落盘（~半天，先于一切）
live/paper/candidate 每次决策把 raw 四桶分布 + blended 四桶分布 + 每个表达的 p_win/ask/edge
全部写进 order/blocked 记录。今天连"11:00 的 d1_no 信念是停 35 还是冲 37"都无法审计。
不落盘，后面所有校准审计都是盲的。

### P0b — E2 settlement-basis 层（已是文档钉死的唯一修复路径）
current bracket 判定换到结算单位，重跑 P1 评分与 P2/P5 EV。这是 current_yes 符号判定的前置。

### P0c — p_current 校准审计（现有 ~13k atlas rows，零新数据）
Reliability curve，按三个机制轴切：`hours_to_forecast_peak`（前/后）× `temp_trend_3h`（升/平/降）
× `forecast_ceiling_margin`（预报 max − 当前档上沿）。
目标假设：**"未过峰 × 在升温 × ceiling margin ≥ +2°C" 切片里 p_current 系统性高估**
（Lucknow 12:17 就在这个切片：p̂=0.59-0.71，事后 0）。若证实，这是模型层排第一的可修偏差；
若证伪，今天就归为单次抽样，模型层不动。

### P0d — 锚切换检验 + 三基准评分台（纲领 §6 P0 原样执行，1-2 天）
市场隐含分布 vs forecast 锚 vs running-max 锚同台 log-loss/Brier。产出决定后续投入先验。
今天市场追 print 的行为是锚切换假说的一个正面样本，但要全量数据说话。

### P1 — hazard 链重构 + 融合三件套（纲领 §4/§5）
逐度 competing-risk hazard，特征进：追踪残差 `T(t)−F(t)` 及其日内趋势（今天上午 resid≈−4.5°C，
正确读法是"GFS 环境热偏，ceiling 打折到 ~37-38"而不是"信 41.3"也不是"完全不信"）、
度内小数位置、太阳时标、**校准 ceiling**（city×source 预报误差分布做层级收缩，替代被 clean spec
扔掉的 source 记忆——建模误差分布是 PIT 安全的，不是 city 自由偏置）。
验收：冻结 forward（6/21 线滚动）上 scoring rule 稳定打败市场隐含分布。不看 ROI。

### P1b — α 消融
α ∈ {0, 0.25, 0.5, 0.75, 1} 在同一评分台跑。回答"混市场到底是在校准还是在进口锚偏差"。

### P2 — 约束层（湿上限 / ensemble 宽度）
按纲领执行，逐特征消融，不显著就扔。

### P3 — 表达层：从固定 combo 到 exact-bracket target book + 成本感知调仓
- 表达 = 对全梯子每个 exact bracket 的 YES/NO 都计算 `(model_p − ask − fee − spread/slippage buffer)`。
  YES 表达用于模型质量集中在某个精确档位；NO 表达用于市场高估某个精确档位、而模型认为低/高两侧
  都有足够逃逸概率。Lucknow 这种状态可能选择 36-NO，也可能选择 37-YES，取决于整条分布和盘口，
  不能事后规定某一个表达一定更"干净"。
- 执行政策不再是 first-lock vs follow-latest 二选一，而是 **target-book reconciliation**：
  只有当新表达的 EV 增量 > 旧腿平仓点差 + 双边 fee + 逆选择缓冲时才允许换仓；否则持有。
  廉价前置实验：用 shadow `city_day_after_first_selected` blocked 行做
  first-lock vs follow-latest 同分母 replay（含卖出点差与费），先量化今天那种换仓的历史期望。

### 并行执行层修复（与建模无关，先行）
runner city-day dedupe、持仓感知、clob fill sync 接入（见首 live 日复盘报告）。
修完前 tiny-live 保持暂停；current_yes 摘出 live 表达集直到 P0b+P0c 出结果。

## 2. 目标系统下今天会怎么走（示意，非回测）

11:00：校准 ceiling ≈ 37.5±1.2（GFS 41.3 经 city×source 偏差 + 追踪残差 −4.5 折算），
距峰 2h，梯子分布大致 35:15% / 36:35% / 37:35% / 38:12%。逐格对市场：36-YES（市价 ~0.32）
无分歧不交易；**37-YES（早盘 ~0.15-0.20）是最大正分歧格**——"还在加热但被湿上限压在 37 附近"
的干净表达。12:00 36 打印后分布右移一格，已持 37-YES 无需动作，reconciliation 检查 EV 增量
不足以支付换仓成本 → 不换仓。全天一笔，信念与赢的路径一致。

## 3. 纪律重申（纲领 §3/§6 原文有效）

模型在冻结 forward 的 proper scoring rule 上打不败市场隐含分布之前，
任何表达优化、route、sizing 都是在噪声上雕花。分布评分每个 city-day-hour 都结算，
不用等 60-90 天 ROI——每周看一次评分曲线即可判断这条线活没活着。

## 4. 审阅后的最小可执行版本

短期不要把本文理解成"马上换成 d1_yes"。更合理的执行顺序是：

1. **先补日志**：每次决策落盘四桶/逐格概率、每个 sibling YES/NO 的 ask/bid/size、旧仓位、候选 target book。
2. **先回放 first-lock vs target-book reconciliation**：同一 city-day 第一腿仍是主口径；后续信号只在 replay
   中测试是否值得平仓/翻仓，不能直接变 live。
3. **current_yes 继续 shadow**：它不是永久否定，但 settlement-basis / below bucket 修复前，不能作为 live
   primary 表达。现有候选报告里 current_yes 结果对口径很敏感，不能用 Lucknow 单例定生死。
4. **d1_no 保留为合法表达**：它不是"错错得对"，而是做空精确档位的补集表达；是否优于 d1/d2 YES
   要由全 ladder 同分母 EV、spread、depth、调仓成本一起判。
