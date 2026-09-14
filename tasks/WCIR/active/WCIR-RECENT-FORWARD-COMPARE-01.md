# WCIR-RECENT-FORWARD-COMPARE-01

- Status: READY_FOR_REVIEW（非 owner ACCEPTED）
- Repo: `/Users/deepsleep/projects/pm_agents`
- Base commit: `146a5100118a29802b60fcb6713bdc820e362f34`
- User request: 用最近几天已保存的新旧模型 forward 做同样本比较，判断是否改善。
- Scope: 独立 A/B/C/D 的只读事实分析；冻结不可变日报、必要原始输入/标签和模型；计算全标签与完整日期的 proper scores、校准、城市/失败/资格明细。
- Write scope: 本 task、对应 handoff、`docs/analysis/CORE_CARRY_RESEARCH_CONTEXT.md` 的本轮追加；`/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_recent_forward_compare/`。
- Non-goals: 无重训、生产参数、collector 修复/重启、实际下单、S 接入或 promotion；原始来源只读。
- Acceptance: 相同 slot/token/label 分母，缺样/未结算显式；分离资格和成交；原分数/标签/哈希验证，单轮独立脚本 review，复现结果和精简证据交付。
- Result: 246 scored，163 terminal/55 城市日/28 城/4 目标日；唯一完整日 09-13（108）。全标签日期/城市日等权 Brier B .077220、D .080755；完整日 B .048904、D .048986。暂无 D 升级依据；C 完整日优于 B 但仍逊于市场。
- Open limits: 83 条评分 pending；旧金山 15 槽位无分数；全日期配对经济不可识别；reviewer实际模型/effort/usage metadata 不可得。
- Evidence: [报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_recent_forward_compare/RECENT_FORWARD_MODEL_COMPARISON.md)，[handoff](../handoffs/WCIR-RECENT-FORWARD-COMPARE-01.md)。

## 用户追加：原版近期实际正确率（2026-09-15）

- 沿用当前研究任务。目标：核对用户感受到的每日错误，分离实际成交、core/near-core和checkpoint；不修改生产/模型/交易参数。
- 追加write scope：`/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_original_recent_performance/`、本task/handoff、已有family living doc `docs/analysis/live_performance.md`、Core研究上下文及strategy registry/index紧凑指针。
- 授权动作：只读当前canonical与原日志、公开官方label缺口查询、研究导出和验证；无canonical写入/重建/部署。
- 验收完成：135fills→102合约，101终局；补回46条fill标签，2条canonical概率与raw提交差异单列。09-08—13实际34信号28胜6负82.35%，原p均值89.03%；4/6日失败，core85%、near78.57%。348缺值输入按冻结imputer复算4038分数通过；单轮review一处命名P2已修复，离线复跑通过。
- 结论：INCONCLUSIVE，近期走弱属实，未证明长期失准或仅运气；完整连续机会分母尚未物化。READY_FOR_REVIEW，不自我ACCEPTED。
- [追加主报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_original_recent_performance/ORIGINAL_CORE_CARRY_RECENT_ACCURACY_REVIEW.md)。

## 当前用户授权：扩大离线模型研究（2026-09-15）

- Status: ACTIVE。承接当前任务，旧只读范围保留为已完成阶段；本段授权离线拟合、同日期比较和失败/成功对照。
- Base: `90ec7290e9ac7bbf6ed5acbe5dcb37027534a5b0`；隔离worktree `/Users/deepsleep/projects/pm_agents-worktrees/core-carry-model-development-20260915`。
- Write scope: 本task/handoff；`scripts/analysis/reheat_risk/core_carry_model_development.py`及对应tests；Core研究上下文、registry/index；`/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_model_development`。
- 不做：生产、collector、交易参数、canonical、原A/B/C/D、原transition协议和原始数据变更；不自我promotion。
- 验收：固定PLAN后24个离线拟合；比较数据/特征/城市/非线性各自作用；同日期阻断、来源hash、PIT缺口、失败及成功代价；单轮只读review与必要复跑；精简报告/附件。
- 原审计缺口（完整S、连续候选canonical adapter、forward合法标题解析）登记保留；不阻塞本轮有明确边界的探索性预测评估，不伪造完整交易反事实。

### 本阶段交付

- Status: READY_FOR_REVIEW（非owner ACCEPTED）。8表达×3fold完整完成，6tests通过；独立review两项修复后全24组复跑。
- Result: 混合四特征E全价Brier .082471 vs B .082885；carry .046365 vs .046708，仍未稳定胜市场。新数据小幅价值存在；静态path/weather/city和小HGB未给稳定事故解决方案。
- Audit: 当前clock修复恢复548eval；旧Phase2漏入FINAL474有标签行、旧dailyfold0。原始/旧attempt保留，未改生产、canonical或原forward。
- Evidence: /Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_model_development/RESEARCH_REVIEW.md；轻量brief和研究记录/DELIVERY_SEAL；handoff见同task ID。
- Limits: exposed development、截断checkpoint、actual-entry输入非完整、season未验证；worker metadata不可得。下一分布/序列目标尚未拟合，不自动开启下一阶段。

文档检查：当前worktree7项既有失败（3runtime路径与4历史规模门），未调整ceiling；本次targeted tests和research record通过，详见artifact checks/verification.txt。
