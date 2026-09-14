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
