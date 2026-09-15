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

## 当前追加：算法/参数对照、取消价格域上限、扩展历史至9/14

Status ACTIVE；base ac878bb0f4e51fe18296415606d2a1319f2b953a。用户授权离线去除market_p≤.9895上限并保留对照，扩大历史和9/14标签；解释原版Logistic与非线性，并进行有限HGB参数/小MLP对照。沿用owned runner/helper/tests/task/context，artifact run=20260915_extended_candidates（输入capture、标签补全、拟合、评估、报告）。仅raw/canonical只读快照和公开官方标签查询，不写canonical、不改live/forward。用完整既有可恢复窗口07-28..09-14；标签和feature cutoff不变，不在训练内回测05—07原始训练集。


## 算法与价格域扩展至09-14（2026-09-15，READY_FOR_REVIEW）

本次用户要求已执行：18候选（含6组混合HGB复杂度/正则配置、4小MLP配置、线性/样条/市场残差/近期/校准比较）、8时间窗、134实际fit；离线去掉market_p≤.9895上限，保留.9895与50/100/10000预算完整对照，未改生产或forward。输入07-28—09-14共6809评分/41城/49日期；09-14为165评分111终局54pending，不能写成全日已结算。

前段29完整已知UTC日选W（7叶/L2=30），不是上轮N（7叶/L2=10）；最终训练8720行=4061旧+4659近期。$50/日无上限全窗口B+$5.9806，V+$66.2639，NN8alpha1+$63.3136，W已终局+$91.0539另1pending、最终范围+$81.5301..+$91.5301。最近09-11—14 B−$5.9320、V+$8.3144、NN8+$5.7109，W范围−$5.6748..+$4.3252；提高预算V可转负、W时钟敏感，不能升级生产。概率基准前段市场仍强；上限取消并不普遍改善。开普敦09-14失败checkpoint多候选因概率不足不买，成都09-13 N因扩展训练p上移买入失败，不能只展示成功避亏。

状态：离线研究交付READY_FOR_REVIEW，非ownerACCEPTED；W为冻结规则主候选，V/NN8/O保留比较，N保留降为对照（不是disproven）。134fit有1个NN16→8/alpha1优化上限warning。16tests及封存输入/3058标签身份/未来扰动/预算费用/PIT成员检查PASS；唯一只读code review2P2（概率域、rawresponse seal）已修复，无重fit/选票变化；reviewer模型/usage不可得。原4061历史receipt-PIT仍未证实，本批历史研究者已见，不是cleanOOS。全部原run/模型/日志保留。

[报告与精简数据](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_extended_candidates/EXTENDED_MODEL_AND_BACKTEST_REVIEW.md)，对应research:weather:core_carry:20260915_extended_candidates；PLAN、REPRODUCE、训练成员、cap_and_training_ticket_changes、全预算ledger、DELIVERY_SEAL可追溯。本次不新增外部reviewer请求或发送行为。详细handoff与本次交付seal相连。

## 当前追加：完整实验报告与GPT Pro审阅包

Status ACTIVE；base1cc4773e1d838f8bc712cd7ecd102c7c8fb94e9c。用户要求确认新旧taker同分母、辨别09-11—14是否真实forward，并提供全部模型选型/训练实验给GPT Pro。只复用封存输入进行账本与来源核验、汇总旧实验、导出精简可携带证据及外审归档；不新增拟合、搜参、数据刷新、生产/forward变更或上传。Write scope沿用task/handoff/living/registry/docs index，新增artifact run=20260915_experiment_review及其review_packet；原sealed run只读保留。验收：统一报价数量费用时钟逐模型核验、实际拟合与历史cutoff分开、完整实验ID/候选/参数/负结果/时间切分、可读报告+附件+独立提示词、包内SHA和归档验证；owner未accept。


## GPT Pro完整实验审阅包（2026-09-15，READY_FOR_REVIEW）

用户追加执行同基座与forward分类核对已完成：22策略逐一共享6809候选/3468条10股报价，原始market/B概率与quantity/principal/fee/cost逐条一致。B同样是报价模拟，不能与原策略真实成交混比；统一policy未照搬production eligible，B选中141票中17原eligible=false。7月398条5股报价没有放大成10股，排除在共同交易分母。

明确修正读法：最新树/MLP在09-15实际拟合，W最终fit完成04:13:34.727193UTC、历史cutoff09-11；09-11—14是回溯时间留出，不是真实forward，方案在已看事故后制定。真正当时记录的FINAL C/D独立forward是另一套Logistic，采样/费用/预算也不同。所有旧产物保留；本次0fit/0调参/0生产写入，仅核验、报告、组包。

完整版本台账/各算法参数/8fold训练时间与样本/18候选含负结果/执行与未来函数缺口见[完整实验报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_experiment_review/CORE_CARRY_MODEL_SELECTION_TRAINING_EXPERIMENT_REPORT.md)。review_id `pm-agents-core-carry-pit-candidates-20260915-r02`，关联r01输入包SHA f7be896adbd3a25d83bc64580ecb72861cea8f7e0ba799aba9fc15cadff65082（精确SHA以manifest为准）；本轮review_packet/manifest提供准确前序SHA、上传包与prompt身份。总库 `~/Documents/GPT-Pro-Reviews/README.md`，prepared_not_sent，用户自行发送，非ownerACCEPTED。
