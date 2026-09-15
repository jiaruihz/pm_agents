# WCIR-RECENT-FORWARD-COMPARE-01 handoff

Status: READY_FOR_REVIEW。完成用户要求的近期比较，不接受模型替换、不启动下一阶段。

| Claim | Evidence / limitation |
|---|---|
| 同批真实记录，未事后重构预测 | 不可变报告 snapshot `40dca1c5ec74c6cc4b4e`；截止 2026-09-14T16:14:22.933318Z；`INPUT_MANIFEST.json`、原 slot gzip 与三模型 SHA 验证 |
| 当前 D 未优于 B | `analysis/metrics.parquet` / `daily.parquet`；完整日只有 09-13，D−B Brier +.000082，不能认定稳定劣化 |
| 不能声称收益提升 | B 2 资格均 pending；D 6 资格1胜5pending；0真实订单；日期经济不可识别，不能将 NA 写成0 |
| 没有漏报坏样本或采样失败 | 32失败checkpoint/17城市日及高概率成功对照、132标题错误原文；其中旧金山15槽位全部无分数 |
| 复算与单轮 review 完成 | `analysis/VALIDATION.json`；246分数最大误差2.22e-16、163官方标签通过；独立review无阻断项，重新运行通过；实际reviewer模型/effort/usage不可得，元数据交付不完整 |

主报告、脚本和约1.46MB冻结输入根目录：`/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_recent_forward_compare`。

复现：`/Users/deepsleep/projects/pm_agents/.venv/bin/python /Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_recent_forward_compare/compare.py`。

无生产源码/配置修改，无重训，无采集器修复或启停。当前数据已被查看用于监测，不得日后整体声称未经使用盲测。沿用原冻结协议；S及转折状态保持。

## 追加原版实际正确率交付

详见 [原版复盘](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_original_recent_performance/ORIGINAL_CORE_CARRY_RECENT_ACCURACY_REVIEW.md)。09-08—13真实成交合约34/28胜6负，82.35%，原p均值89.03%；core17/20，near11/14；4个失败日期，最近3日连续失败。0.90—0.97九信号6胜3负，低于.90二十信号17胜3负，禁止把小样本切片直接当新gate。

证据：fresh manifest DB healthy、fill coverage PASS；135canonical fills与raw订单全匹配；官方补46fill标签，余1pending；先前canonical漏36合约label，raw两条概率差异单列，均未写回生产。近期expected losses3.73077，actual6；独立参考尾概率.159366，不证明仅运气。六日期bootstrap校准差CI跨0。独立review一处checkpoint命名P2已修复，全部离线验证复跑PASS；模型/effort/usage metadata仍不可得。

覆盖边界：4038个已保存checkpoint概率全部复算一致，但完整continuous rescore机会分母未物化，34live信号中32明确continuous触发、2unknown；不称完整连续策略因果归因。该批数据已用于诊断。无重训、参数、生产、canonical或调度变更。

仓库文档hygiene检查FAIL（10项）：未改文件的未跟踪链接、主工作区existing dated/runner ceilings。详见原版artifact `docs_check.log`；未在本轮制造仓库dated报告/runner或抬高ceiling。数据/模型验证、fill gate与owned diff检查PASS；不要将此交付写成全仓检查通过。


## 2026-09-15 扩大离线研究交付

- Owner scope：当前用户明确继续研究，离线拟合已授权；生产/参数/collector/原forward不变。
- Repo/worktree：`/Users/deepsleep/projects/pm_agents-worktrees/core-carry-model-development-20260915`，base90ec7290e9ac7bbf6ed5acbe5dcb37027534a5b0。此隔离分支可审阅；没有production promotion。
- Claim：新数据不是无用；旧+新四特征三个窗口小幅提升B，但未稳定胜市场。Evidence：metrics/paired_intervals/fits/predictions，2436rows/608citydays/17dates。
- Claim：单纯加静态路径、云雨、城市或小HGB尚未解决事故。Evidence：固定24模型组合、recent_trade_context34rows/6失败、成功代价及692失败checkpoint明细；不把priorcheckpoint当actual-entry。
- Audit fix：601同秒评分兼容，恢复548eval；影响清单和前后指标保留。旧Phase2 FINAL474额外资格，历史dailyfold0；不改原FINAL模型或forward。研究runner本身没有交易能力，错误下单/漏单为0。
- Validation：6针对性tests通过，原B/mid复算、所有model roundtrip、日期分组断言通过。唯一独立review P1/P2均处理，51实际fit调用=3失败partial+24旧口径+24最终；24唯一配置，未调参。
- 未完成/不适用：真实入场经济反事实、fullS、continuous canonical adapter、forward parser部署、剩余升温分布新目标、季节适配；这些不是本阶段验收动作。worker实际模型/effort/usage不可得，review证据元数据缺口保留；docs gate见交付checks。
- 状态READY_FOR_REVIEW，非ACCEPTED。主报告/全部表/代码与原输入指针：`/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_model_development/`。精简附件在brief，不复制93MB旧包。

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

## 多候选训练与严格时间边界预算回测（2026-09-15）

用户授权第1+2步已执行，状态 READY_FOR_REVIEW，非 owner ACCEPTED。10候选×4时间窗；最终32基础fit＋4校准，旧36fit保留；7,455最终混合训练行中3,394近期行按真实标签已知时间及feature record上界准入。13tests及未来扰动集成检查通过；独立review3P2修复，生产/canonical影响0。原4061历史输入的receipt PIT仍未证实，不伪称全数据严格PIT。

前段13个完整已知UTC决策日选中 N_pooled_hgb_small：验证+$27.1297 vsB+$8.4098；后段09-11—13共同$50/UTC日预算N15票+$8.4293 vsB4票+$3.0370。N单票成本ROI5.95%低于B8.22%，但分配资本收益较高。$100诊断N+$2.4764低于B；删除同秒歧义报价N负；旧金山主回测因预算耗尽漏掉，不能声称识别危险。N/O在已恢复两事故源评分仍正edge。保留N优先、O比较，未达到生产替换依据，未切换forward。

证据：/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_pit_candidates/CANDIDATE_BACKTEST_REVIEW.md。输入2436captured checkpoints/17目标日，773可执行carry报价；不是连续全策略maker反事实。精简brief含全部候选概率/预算交易与失败成功原始评分。两事故exact token/snapshot/p/clock匹配补全了此前actual-entry输入缺口的一部分；37源score→38唯一订单记录关联，67未恢复保留。

本轮精简审阅包登记：`pm-agents-core-carry-pit-candidates-20260915-r01`；总入口 `/Users/deepsleep/Documents/GPT-Pro-Reviews/README.md`，状态 prepared，未发送；artifact 内 review_packet/manifest.json 关联输入哈希、prompt、代码和未解决边界。


## 算法与价格域扩展至09-14（2026-09-15，READY_FOR_REVIEW）

本次用户要求已执行：18候选（含6组混合HGB复杂度/正则配置、4小MLP配置、线性/样条/市场残差/近期/校准比较）、8时间窗、134实际fit；离线去掉market_p≤.9895上限，保留.9895与50/100/10000预算完整对照，未改生产或forward。输入07-28—09-14共6809评分/41城/49日期；09-14为165评分111终局54pending，不能写成全日已结算。

前段29完整已知UTC日选W（7叶/L2=30），不是上轮N（7叶/L2=10）；最终训练8720行=4061旧+4659近期。$50/日无上限全窗口B+$5.9806，V+$66.2639，NN8alpha1+$63.3136，W已终局+$91.0539另1pending、最终范围+$81.5301..+$91.5301。最近09-11—14 B−$5.9320、V+$8.3144、NN8+$5.7109，W范围−$5.6748..+$4.3252；提高预算V可转负、W时钟敏感，不能升级生产。概率基准前段市场仍强；上限取消并不普遍改善。开普敦09-14失败checkpoint多候选因概率不足不买，成都09-13 N因扩展训练p上移买入失败，不能只展示成功避亏。

状态：离线研究交付READY_FOR_REVIEW，非ownerACCEPTED；W为冻结规则主候选，V/NN8/O保留比较，N保留降为对照（不是disproven）。134fit有1个NN16→8/alpha1优化上限warning。16tests及封存输入/3058标签身份/未来扰动/预算费用/PIT成员检查PASS；唯一只读code review2P2（概率域、rawresponse seal）已修复，无重fit/选票变化；reviewer模型/usage不可得。原4061历史receipt-PIT仍未证实，本批历史研究者已见，不是cleanOOS。全部原run/模型/日志保留。

[报告与精简数据](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_extended_candidates/EXTENDED_MODEL_AND_BACKTEST_REVIEW.md)，对应research:weather:core_carry:20260915_extended_candidates；PLAN、REPRODUCE、训练成员、cap_and_training_ticket_changes、全预算ledger、DELIVERY_SEAL可追溯。本次不新增外部reviewer请求或发送行为。详细handoff与本次交付seal相连。
