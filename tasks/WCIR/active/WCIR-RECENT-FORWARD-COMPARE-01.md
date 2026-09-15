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


## 用户追加：以市场与交易收益为benchmark（2026-09-15）

Status: ACTIVE。授权补充离线实际成交终局损益复算、逐笔价格/费用/胜负/模型与市场对照，并以同一报价/数量/机会评估已拟合模型；不再训练，不修改生产/原canonical/原预测。Write scope延续owned runner/tests/task/context，加 artifact `/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_trade_benchmark`。真实fill只以已封存fact_trades为起点、调用既有builder盈亏公式；官方标签补全单列research_terminal_reconstruction，原canonical列和缺口保留，禁止声称钱包现金到账。新模型报价benchmark固定一次机会，不冒称全策略live反事实。


### 交易benchmark补充交付

- Status READY_FOR_REVIEW，非ownerACCEPTED。当前用户要求补市场与交易验收，已在隔离owned runner增加economic模式，未重训/改生产。
- Claim/evidence：真实101合约91胜10负、研究终局−$12.49665；最近34合约−$12.31266；fact_trades135行和官方标签，用既有builder公式，88parity/46补算/1pending，原canonical列保留。每合约及fill明细附出。
- 同报价483机会/17日期/41城：B+$12.888604，E+$8.665204，差−$4.2234；E新增7赢1亏少1原赢家。I+$39.503314但累计投入5.26倍、最近窗负、CI跨0；全买高概率−$43.119386。不是完整策略/实际fill反事实。
- 本轮8tests通过；集成验证共同ID/未选0/pendingNA/双ref自CI0/成本费用守恒；一轮独立review两P2修复，初稿attempt保留，0新fit。worker模型/usage unavailable；原repo docs gate既有缺口不自称通过。
- 剩余：完整原策略预算/触发/maker反事实仍不可识别；情景缺陷、观测不足与概率允许失败尚不能逐笔定性。候选升级尚未成立。
- Artifact：/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_trade_benchmark/TRADE_BENCHMARK_REVIEW.md；brief含逐笔与policy变更CSV。原run封存未改。

## 当前授权：多候选训练与防未来函数预算回测

Status: ACTIVE。用户明确批准第1+2步：多个离线候选、同条件预算回测，特别防止未来函数。Base ec930e114d6bb2a58186f986e51d8b655187b6eb；沿用隔离worktree。Write scope延续owned runner/test/task/context，加同family helper core_carry_candidate_backtest.py和artifact run=20260915_pit_candidates。训练、预处理、校准、选模均按标签已知时间与整日期边界；收益回测按已保存候选时间序列，不伪造maker或缺失触发；未授权部署、forward变更、生产参数或资金操作。验收为冻结候选、完整拟合/回测与明细、未来扰动测试、独立只读code review和交付。

## 多候选训练与严格时间边界预算回测（2026-09-15）

用户授权第1+2步已执行，状态 READY_FOR_REVIEW，非 owner ACCEPTED。10候选×4时间窗；最终32基础fit＋4校准，旧36fit保留；7,455最终混合训练行中3,394近期行按真实标签已知时间及feature record上界准入。13tests及未来扰动集成检查通过；独立review3P2修复，生产/canonical影响0。原4061历史输入的receipt PIT仍未证实，不伪称全数据严格PIT。

前段13个完整已知UTC决策日选中 N_pooled_hgb_small：验证+$27.1297 vsB+$8.4098；后段09-11—13共同$50/UTC日预算N15票+$8.4293 vsB4票+$3.0370。N单票成本ROI5.95%低于B8.22%，但分配资本收益较高。$100诊断N+$2.4764低于B；删除同秒歧义报价N负；旧金山主回测因预算耗尽漏掉，不能声称识别危险。N/O在已恢复两事故源评分仍正edge。保留N优先、O比较，未达到生产替换依据，未切换forward。

证据：/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_pit_candidates/CANDIDATE_BACKTEST_REVIEW.md。输入2436captured checkpoints/17目标日，773可执行carry报价；不是连续全策略maker反事实。精简brief含全部候选概率/预算交易与失败成功原始评分。两事故exact token/snapshot/p/clock匹配补全了此前actual-entry输入缺口的一部分；37源score→38唯一订单记录关联，67未恢复保留。

本轮精简审阅包登记：`pm-agents-core-carry-pit-candidates-20260915-r01`；总入口 `/Users/deepsleep/Documents/GPT-Pro-Reviews/README.md`，状态 prepared，未发送；artifact 内 review_packet/manifest.json 关联输入哈希、prompt、代码和未解决边界。
