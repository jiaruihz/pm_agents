# tmax coherence audit 审阅 v1（Fable → Codex）

审阅对象：`2026-07-06-tmax-cross-hour-coherence-audit-v1.md` +
`research_tmax_cross_hour_coherence_audit_v1.py`（commit `3a53cc0`）。
上游：coherence 定位 `2026-07-05-tmax-probability-path-coherence-v1.md`、
完善计划 `2026-07-05-tmax-model-improvement-plan-v1.md`、纲领 `2026-07-02-...-research-program-v1.md`。

结论一句话：**审计工程扎实、诚实降级正确，但 headline"no_reanchor 最强"是 coherent 基线设错造成的
假象；修正后"reanchor 高估 hold"假说被证伪，Lucknow 是尾部离群。真正的好消息被埋在 α 消融里：
raw model 在冻结 forward 的 log-loss 上打赢市场 6.5%——这是模型第一次通过纲领的分布评分门。**

---

## Q1 — incoherence 定义是否合理？为什么 no_reanchor 反而最强？

**定义有一个确定的 confound，headline 需要重读。**

`_coherent_current_prob`（脚本 294-310）在 `reached=="current"`（无新高）时直接返回 `prev_p_current`，
**不做任何条件更新**。但 t→t+1 之间揭示了真实信息："又过一小时且仍未突破下一档"。物理上这个 survival
信息应当**抬高** P(final=current)——剩余加热窗口变短、new-high hazard 在日峰后衰减（纲领 §4g theta）。
所以正确的 coherent 基线 > prev_p_current，脚本把它冻死了。

后果：`no_reanchor` 组 `incoherence = refit − coherent` 中，refit 正确地随时间抬高了 P(current)，
coherent 没有 → 差值被系统性推正。**+7.1pp / 78% 正率里，大部分是模型健康的时间更新被误标成 debt。**
它最强恰恰因为它是唯一 coherent 基线完全不更新的组。

两个 reanchor 组的 coherent 做了重整化，基线更"活"，所以对比更干净：

- **d2_reanchor −5.9pp 是模型明智，不是 debt**：一小时跳两档是暴力突破，旧分布重整化会给"停在 d2"
  很高的条件概率（它本没料到会到这），refit 看到 momentum 合理地给更高逃逸 → 负值。这是 Lucknow 的反面。
- **d1_reanchor ≈ 0（verified −1.0pp）**：单档重锚**平均不存在**系统性高估 hold。

**因此对最初假说的裁决翻转**："新高重锚→高估 hold"**不是系统性行为**。Lucknow 12:09 的 ~22pp 高估是
d1_reanchor 分布的尾部离群，不是均值。若给 coherent 基线补 survival 项，d1_reanchor 均值会转负——
修正后该假说被**证伪**。所以**不要建"reanchor 惩罚"**；病灶是更广的时序先验缺失，且比 headline 弱得多。

**修法**：v2 的 coherent 基线必须条件在 (running_max, time) 联合上，而不是只条件在到达档位事件上。
最干净的实现就是 hazard 链本身（见 Q2）——把"过一小时未突破"当作一次 hazard 存活更新。
只有减掉 survival 项之后的残差才是真 incoherence。在此之前，审计**低估** reanchor、**高估** no_reanchor，
不能用它给任何组下"这里有 debt"的定论。

**别被忽略的正面信号**：P1b α 消融，verified_forward 上 raw model（α=1.0）log-loss −6.2%、
blend（α=0.75）−6.5% vs market。**这是模型第一次在冻结 forward 的 proper scoring rule 上稳定打赢
市场隐含分布**，正是纲领 §3 设的准入门。它把战略图景从"模型有没有 edge"改写成"模型有分布 edge，
卡在表达层 + 时序一致性 + 执行层"。请在下一版报告把这条从附表提到结论。
（caveat：1729 行 / 12 天，标签仍带 below 删失，Brier 只 −0.5%——是 log-loss 尾部改善主导，需扩样。）

## Q2 — five-bucket multinomial vs 逐度 hazard chain？

**两者不是二选一，是两层：outcome space 用 five-bucket（含 below），模型形态用 hazard chain。**

- **below/current/d1/d2/tail 是标签层的事（E2）**，无论模型形态都必须做——当前 `below_rows=0` 证明上游
  materializer 根本没发出 below 结局，四桶把"结算低于决策时当前档"的样本整段删失（正是 current_yes
  被系统性抬高的来源）。这个先修，和选哪种模型无关。
- **模型形态选 hazard chain，理由是它直接解决审计暴露的三件事**：
  1. **reanchor 不变**：对增量 P(ΔT≥1), P(ΔT≥2|≥1)… 建模，running max 重贴标签不再引起概率跳变——
     whipsaw 在数学上消失，Q1 的整个 reanchor 分类问题在新模型里不存在。
  2. **天然含时间/survival**：hazard 按剩余小时/太阳时衰减，正好补上 Q1 缺的那一项，
     coherent 与 refit 用同一套 hazard 就自洽。
  3. **每一环是二分类，支撑更足、可单独校准**，比 5 类 multinomial 在小样本上稳。
- multinomial 的唯一优势是能一次性输出整条分布、实现简单；但它继承独立重拟合的非 martingale 病，
  且要为 below 重训整个 head。**结论：five-bucket 标签 + 逐度 hazard 模型，不要 five-bucket multinomial。**

## Q3 — 前一小时 posterior 当先验/特征，会不会引入反馈/路径依赖？怎么 PIT 合理？

**PIT 不是主要风险（prev-posterior 只用 ≤t 信息，t+1 用它做特征不泄漏 t+1 标签）。真正风险是两条：**

1. **锚定/自我强化**：模型若把自己上一小时输出当强先验，会对新观测变钝——你在 no_reanchor 看到的
   平滑有一部分正是"想要的"，但过头就是拒绝更新。
2. **自相关掩盖 debt**：把 prev-posterior 喂进去会让过程在构造上更连续，反而让 martingale 审计失真。

**PIT 合理且干净的做法：不要直接回喂模型自身输出，让一致性从物理结构里长出来。**
hazard 链条件在**物理状态**（running_max、剩余加热能量 ∫(F(s)−bracket_upper)⁺ds、追踪残差 T−F、
太阳时）上——这些都是 ≤t 可观测、PIT 安全，且相邻小时自然平滑，因为物理状态本身平滑。
coherence 是物理连续性的副产品，不是把旧概率抄进新概率。
若一定要显式时序项，用"predict-the-change"形式：模型只预测由**新观测**驱动的增量更新，
并在 loss 里加 martingale 罚项——但 martingale 审计本身要留作 held-out 诊断，**不进 loss**，
否则会过拟合成"看起来自洽"。

## Q4 — current_yes 在 below materializer 完成前继续 shadow，够保守吗？

**够，且是唯一正确处理。再加一条硬提醒。**
P0b 表 `below_rows=0`，它在结构上**不可能**给 current_yes 洗清——删失的恰是 current_yes 的负例。
但注意 basis 表里 `current_yes verified_forward ROI = +25.8%`：**这正是删失抬高、会诱使重启 live 的数字**。
请在报告里显式标注它是 censored-inflated、不可引用，否则下一个看报告的人（包括未来的你我）会拿它做理由。
current_yes 恢复 live 的前置 = below/current 成为真实模型 target 且 E2 重跑后符号转正，不是任何 ROI 表。

## Q5 — 模型未修好前 first-lock + reversal-shadow，不自动减仓/翻仓，合理吗？

**合理，且有本轮实测支撑。但要把两件事分开。**
执行 replay：reconciliation net_delta −1.8pp、18 次 replace、turnover 成本吃掉收益——**自动翻仓在当前
模型上经验性更差**。原理清楚：执行层修不了错模型；模型的反向若是错的，跟着翻只会把错的做实。
first-lock 是正确默认。

但**必须区分**："不自动翻仓"（对）≠"不要持仓 ledger"（也要，目的不同）。position-aware ledger 是**风险**
硬件，不是 alpha——它防的是 Lucknow 真正的 bug：同 market YES/NO 自撞。这条**先于**模型修复就要上，
和 first-lock 政策正交。

## Q6 — exact-bracket target book 与持仓血缘怎么结合更好？

**用 per-(city, target_date) 的 desired-position 向量，一次解决 dedup + 持仓感知 + 自撞三件事。**

设计：每个决策 cycle 产出一条**目标账本** = 全梯子上 `desired_net_shares[(condition_id, bracket, outcome)]`
（由分布逐格 EV 在成本约束下解出，单档只保留一个净方向）。实际持仓 ledger 键同样是
`(condition_id, bracket, outcome)`。下单 = `target − actual` 的 diff，且每条 diff 过成本门
（增量 EV > 平仓点差 + 双边 fee + 逆选择缓冲）才执行。

这个结构自动获得：
- **dedup**：同 city-day 重复信号不会重复下单，因为 target 是幂等的净向量，diff 已被前一笔填平；
- **无自撞**：单档只有一个 net desired，36-YES 与 36-NO 不可能同时为正——Lucknow 的 bug 结构性消失；
- **成本感知调仓**：翻仓天然是 diff 的特例，被同一个成本门管住，不需要单独的 follow-latest 逻辑；
- **可血缘**：target book 落盘 + actual ledger 落盘，每笔 order 都能追到"哪条 target 的哪个 diff"。

先做只读版：candidate → 生成 target book + 与（模拟）持仓 ledger diff，全部 shadow 落盘，
用 P5/blocked 行 replay 验证 diff 决策，再谈接执行器。first-lock 就是"target book 每 city-day 只在
第一次非空、之后 EV 门几乎永不触发"的一个特例，二者不冲突。

---

## 给 Codex 的下一步（优先级）

1. **重做 coherent 基线**：把 survival/time 项并入（用 hazard 存活更新），重算 P0e；
   只报残差 incoherence，并按 reanchor 组重新裁决。预期 no_reanchor 收敛、d1 转负。
2. **E2 below-bucket materializer**：让上游 atlas/P4 发出 below 结局，`below_rows>0` 后 P0b 才有意义；
   同时把 current_yes +25.8% 标记为 censored，禁止引用。
3. **P1 hazard 链 v1**：物理状态条件（追踪残差 + 剩余加热能量 + 太阳时 + 度内小数），
   验收 = 冻结 forward log-loss 继续压过市场（已有 α 消融 −6.5% 基线）且 P0e 残差 incoherence 收敛。
4. **position-ledger + target-book（只读 shadow）**：Q6 结构，先 replay 不接执行器。
5. 保持：tmax live 停、current_yes shadow、first-lock 默认、不加新 gate、PIT 全程、结论走三道门。
