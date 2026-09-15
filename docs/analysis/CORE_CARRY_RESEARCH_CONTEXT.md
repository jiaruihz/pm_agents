# Core Carry 研究主上下文

## 2026-09-15时钟与日期口径补审（替代对strict负PnL的解释）

用户追问后，从原raw恢复request/response/parsed三钟：测试1043/1043条为direct response lineage且顺序与评分整秒区间一致，0倒序/0越区间。旧strict因微秒book与整秒score比较排除546条，并非已证坏数据；O移除18赢+$4.7213，再新增11票净−$2.6169（含Chengdu09-13−$4.7619），才产生旧−$2.4194。该数字仅保留为metadata exclusion sensitivity，撤回将其当“去掉异常/未来数据后O亏损”的负向证据。主选票和收益不变，48票独立raw算术复核、去重和BJ预算均通过；原始producer时钟18项测试通过。

O并非只在9/14盈利：7目标日6日正，9/14为9票9赢+$2.5761，去掉该日仍+$2.3427；但同期B+$3.7795，故相对B−$1.4368。9/14B唯一CapeTown票−$4.5225，当天O−B+$7.0986推动全期优势。target-date9/14跨BJ9/14和9/15预算；BJ自然日9/14单独是O5票5赢+$1.1434，不能混日期口径。NOT_READY仍由未接入的多策略执行/账户risk/logging/kill闭环决定，历史采样截断和缺深档限制保留。

[本次校正与逐票结果](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_live_readiness/clock_data_audit/CLOCK_AND_DATE_CORRECTION.md)；CHECKS、CLOCK_CHAIN和旧filter逐票差异在同目录，原封包不覆盖。

## 2026-09-15 O五股就绪研究（READY_FOR_REVIEW；live NOT_READY）

冻结O与同6157成员W核验通过，0fit。复算3013历史评分的5股盘口，验证/测试共同可行动域36个深档缺口显式保留；当前生产实为10taker+5maker及near5、预算按BJ日。主规则每策略25美元/BJ日，补22个官方condition的54条pending标签后，测试O38票37胜1负模拟+$4.9188 vs B10票−$0.743；严格时钟O−$2.4194，去掉9/14则O−B=−$1.4368，单额外5股loss使O整体−$0.0812。7日期配对CI跨0，概率验证较差/测试较好，inconclusive，不改选W。

B/O独立order/fill/state、全账户跨日风险预留、partial/restart/reconcile、O/W完整slot日志及独立kill未接入，live gate NOT_READY。联合未解本金receipt proxy94.835美元高于同机会B10 taker45.270，非实际钱包敞口或批准风险上限；方案不能仅靠单笔减半。14日期只提议执行checkpoint，原30/60效力协议保留。157相关tests和23机器表独立重放一致；单轮review三P2修复，model/usage telemetry不可得如实标注。无生产/collector/资金变更、无新真实订单，未自我接受。

[摘要](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_live_readiness/00_LIVE_READINESS_SUMMARY.md) · [提示词评估](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_live_readiness/PROMPT_ASSESSMENT.md) · [readiness gate](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_live_readiness/LIVE_TRIAL_READINESS.md)。

## 2026-09-15 详细审阅补充r03：不能把O封存当作可替换（READY_FOR_REVIEW）

用户要求完整文字报告/逐票经济/训练验证/过拟合/替换证据，并写入Apple Notes。本轮0fit、0重选；O35票预测胜率96.176%、market93.24%、含费保本94.996%，预期+$4.129859 vs 模拟+$7.513933；每天5票，无citydate/token/checkpoint重复。测试相对B优势+$8.914433，但剔除9/14变−$2.198049；完整14日验证W+$24.561169高于O+$16.060582，O三张验证亏损全在未进入正式6日排名的日期。标签延迟/完整日选择可能改变风险样本，不能据6日O第一称最优/非过拟合。

结论保持inconclusive，O是已固定待审对象，尚不支持唯一首选或替换B。原artifact/selection/oldreports不改。全部O/B48张测试票含UTC/北京/当地时点、bid/ask/VWAP/fee/赔率/market/model p/预期与实际PnL/预算/clock；训练回代与验证/测试并列。24tests；review2P2修复，11审计表复跑全等。[r03全文](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_holdout_detailed_review/FULL_TEXT_REPORT.md)；review_id=pm-agents-core-carry-holdout-20260915-r03，外审总库~/Documents/GPT-Pro-Reviews/README.md，prepared_not_sent。

## 2026-09-15 单次时间留出：O研究权重封存（READY_FOR_REVIEW）

本轮按用户要求将拟合、验证选型、最终留出隔离：8/25前训练5个既有候选，8/25—9/7验证，仅6个完整已知UTC日选出O（15叶HGB/L2=10）；9/8截点重训唯一赢家6157行，固定权重测试9/8—14，不根据测试改赢家、不再吸收测试labels。

7日1043评分，主O35票34胜1负、投入$332.4861、模拟含费净收益+$7.5139/ROI2.2599%；同$50/UTC预算B13票12胜1负、投入$121.4005、−$1.4005。日均差+$1.2735的日期CI[−$2.5094,+$5.3856]跨0；O额外使用2.74倍实际投入，非同投入盈利比较。严格时钟净收益范围−$4.6693..+$5.3307、+1美分摩擦范围−$1.6694..+$18.3306，待定使稳健性仍未确认；carry Brier好于市场而全域更差。结论inconclusive，仅固定下一研究验证对象；尚无已证明可升级生产的版本。

整个历史已被研究者看过，真实O forward0日期；准备的独立协议最早9/16完整目标日起、实际接入后计日，至少30完整终局日/60日历覆盖检查，未部署/未改原C/D。封存的是实测权重SHA `5a54e8aaff31ba475431d7bc9fe5e47bd1c790198ea0bcc1e0df356f0eb9f75c`，不是重训后未测的新权重。

单轮review修正完整UTC选模日边界与配置预算：去掉8/25不完整日、32条跨模型选模模拟票，O仍首位；完整复跑确认1043预测、模型SHA、test账及训练成员全等，实盘影响0。20tests通过；全库docs7项既有失败保持不变；reviewer实际model/effort/usage unavailable。原attempt保留。[完整留出报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_frozen_holdout/HOLDOUT_REVIEW.md) · [冻结信息](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_frozen_holdout/FROZEN_CANDIDATE.json)。

## 2026-09-15 交易基准补充（当前研究结论，READY_FOR_REVIEW）

用户指出概率小数点不是策略验收，本轮已补交易账：沿用封存135fact_trades，官方标签补全46、88原PnL parity、1pending，使用同一canonical builder公式生成独立research_terminal_pnl列，不改原canonical或声称钱包到账。08-18—09-13共101合约91胜10负，终局研究净损益−$12.49665、含费ROI−1.2719%；09-08—13共34合约28胜6负，−$12.31266/−4.7851%（core−$8.21266、near−$4.10）。

2436开发预测→785carry→773可执行10股报价→483共同首次城市日机会：原B43票41胜2负、+$12.888604/3.2456%；混合E50票47胜3负、+$8.665204/1.8783%。E新增7胜1负、少1原赢家，差−$4.2234，说明上一轮“新数据有价值”只能指概率开发指标，不支持交易升级。四特征非线性I221票+$39.503314、但累计投入5.26倍且最近窗−$9.349601；路径天气J最近窗−$28.835709。全买市场carry483票93.17%正确仍−$43.119386。所有增量日期CI跨0。

该比较是固定同报价单次机会benchmark，未模拟完整原策略后续触发/预算/maker；不能拿B最近7/7替代实盘34笔。没有已确认可盈利的情景修复，也没有证明稀疏METAR导致个案不可预测，更不能逐笔判定纯运气。每个候选今后必须同时交市场基准、投入/费用/净收益、胜率、新增取消胜负明细及日期稳定性；概率分数保留为诊断。

[交易基准主报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_trade_benchmark/TRADE_BENCHMARK_REVIEW.md) · [102合约逐笔CSV](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_trade_benchmark/brief/actual_contract_detail.csv)。8tests与集成验证通过，单轮review两P2修复后经济分析完整复跑，0新fit、0生产改动，原封包保留。

## 2026-09-15 当前推进：扩大离线模型研究（READY_FOR_REVIEW）

用户明确授权从策略出发点继续研究，原“只做单一转折/本轮不拟合”的文字是对应Phase4/5历史范围，不再限制单独的当前开发分支；原冻结A/B/C/D与transition协议仍保留，不部署替换。

已固定8表达×3日期窗、完成24组拟合：原4061行 + 近期训练优于只用近期；测试08-28—09-13共2436checkpoint/608城市日/41城/17日。混合四特征E全价Brier .082471 vs B .082885，三个窗口均小幅改善；carry .046365 vs B .046708、market .046295。路径/云雨/城市线性表达没有稳定增量，非线性carry平均改善不稳定且最新窗较差；保留开发证据，不声称新数据无用或整体feature域失效。

六近期失败及28成功的同token先前checkpoint已关联，1.2—52.2min缺口明确；SP09-11过去3h升12.6°F的风险可被路径表达压低，但SF成功对照同样有升温/边界风险，Seattle09-12在回落阴天状态仍失败。新分数不是actual-entry counterfactual。全评估692失败checkpoint及同城成功匹配另存，固定高概率降档同时报告成功代价。

审计收尾发现：秒精度score与微秒book直接比较，导致旧Phase2 FINAL资格排除474有标签行；时间区间相容但first-seen未知。若只修该项FINAL池6298→6772；原历史dailyfold新增0（label时钟未到），原模型/forward分数不改。当前研究同类缺陷修正恢复548评估行，旧attempt原样保留，无真实订单影响。其余S/连续adapter/forward合法标题缺口仍限定相关证据，不再代替建模工作。

下一项优先研究应更直接估计剩余升温分布/结束升温状态，并以旧scalar overshoot作对照；本轮尚未拟合该新目标。继续独立forward，未开始新自动化/未扩大collector/未修改生产或交易参数。6tests通过，单轮独立review P1/P2修复并复跑；worker实际模型/usage metadata unavailable。

[本轮报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_model_development/RESEARCH_REVIEW.md) · [精简附件](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_model_development/brief) · [研究记录](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_model_development/research_record.json)。

## 原Phase4/5阶段说明（保留历史范围）

本文件是后续 Core Carry 任务的优先入口，详细数字回溯前三阶段及下表原报告。当时状态：Phase5 独立官方标签/日报于2026-09-11T16:49:10Z启动；原Phase4采集器及模型不变，转折仅抽样协议修订为v2、仍未拟合；A/B/C/D 冻结，未批准生产替换。生产真实状态仍须读当前 manifest，不用本文替代进程证据。

## 策略、模型与信息边界

对象是当日**已观测运行最高温所在 current exact bracket YES**，标签是该档最终保持为结算档，不是曾经触到该温度。持有胜出支付1的YES，只有真实胜率高于含费可取得成本才有正预期；maker入场机制与taker不同，概率、资格、成交、现金到账分别评估。

所有城市共用逻辑回归：`sigmoid(intercept + Σcoef × standardized_feature)`；四特征为 `market_logit + decision_hour_local整数 + dewpoint_depression_f + wind_speed_kt`。带训练内imputer/scaler和L2，无城市固定效应，market系数可学习而非固定1的offset。原训练4,061行、1,305城市日、46日期、36城，2026-05-19至07-08；原训练是全赔率，carry的mid≥.80是使用域。部署v3在全部原训练上拟合，不能用它对此训练集评分冒充OOF。内部artifact hash `40af251f…`与JSON文件SHA是两种身份，不混用。

天气仍为整点/半点METAR。序列中保存温度、露点、风、部分云雨等，保存不等于已入模。原历史多为observation-valid-time，archive local availability只证明在当地该时刻可用，不能猜全球first-seen。训练/测试按全城市target_date阻断；同城日多个checkpoint不得分到训练和测试两边。所有fold的填补、缩放和训练label也必须满足实际available cutoff。

A=同次原市场midpoint；B=冻结v3；C=market-logit-only校准；D=同规格四特征更新。D只变训练数据和拟合参数，没换特征、算法或盈利机制。逐日期walk-forward与FINAL是不同产物；A/B/FINAL C/D只在新封存后完整目标日作独立验证，不被转折分支改写。

## 已确认的发现与纠正

| 证据层 | 已确认 | 不能推出 |
|---|---|---|
| Phase1代码/模型/日志审计 | 原65,698次非空评分可由原四特征和artifact复算；模型调用链已核验 | 不再凭坏例子猜“用了错模型”或maker执行错 |
| Phase2封存标签 | checkpoint有标签评分1,898→4,461；原94合格有标签，88胜6负，均值预测约93.66%、实际93.62% | 不是高概率普遍严重虚高；也不是每城/状态/near-core已校准 |
| Phase2更新 | 改善很小且未稳定超过市场/对照；早期walk-forward几乎无近期训练行 | 不能说完整吃进一个月新数据再独立验证同一个月 |
| Phase3资格 | D保留B全部95资格，仅增8行/4日、移除0，原6失败均保留 | 不是8笔独立真交易；不是尾部过滤器；不代表FINAL D的行为 |
| Phase4对既有4个分量的小额复核 | 事后报价利润+$1.9076，但当时D隐含预期合计仅+$0.0238062；每股附加0.10美分已使预期合计转负 | 四次全赢不是边际已可靠，不是本轮新真实盈利 |
| 成交经济 | 133唯一fill；127终局离线+$9.88493，6快照未终局；原93逐笔−$3.66057保留 | 离线终局不等于钱包到账/new live_real；maker与taker非随机同分母试验 |
| 采样 | B首次正EV后停止旧checkpoint；31日期均无法完整识别Phase3配对经济 | 1,447只是已记录前缀；NA不是收益0；不可用事后行情补全 |
| 城市覆盖 | 原36城、运行41城，7运行城未在原切片；圣保罗有33训练日期 | 未证明必须每城建模；不能单次失败拉黑；城市/季节迁移仍未决 |

数字都是对应封存窗口的事实，不代表今天的即时结算/运行。数万条连续重评分、独立城市日、目标日期和成交笔数必须分开。

纠正历史保留：Phase1标签不完整由Phase2替代；5个fill旧fact概率与raw Core订单不同、1个Amsterdam来源晚747秒，未改原始记录；旧price-conditioned高mid分母误称full-support已由v2更正；actual-transport仅absolute lift误报通过已撤回；旧transition把不同bracket合并的控制组已更正。Phase3 clock/成本unknown传播、B截断位置和预算reservation修正的初轮产物保留在原根，最终只读verified/。所有纠正未自动变成生产参数变化。

## 紧凑实验台账（旧结果重新整理，不算本轮新实验）

| 假设 / 改动 | 样本、时间可用性、基线 | 原结果 | 能/不能推出、状态 | 重新开启需要的新信息 / 原证据 |
|---|---|---|---|---|
| peak clock：strict argmax→有容差的下午峰值→去掉peak特征 | 原v3报告，原4061训练行；carry OOF1350/31日/773城市日；历史曲线可用时间边界 | strict/corrected/no-clock 136/134/104 entries；ROI4.67%/2.89%/4.38%，选择no-clock v3 | 更合理定义未增加该表达收益；不是所有热窗口无效；旧表达superseded | 需可识别boundary-relative真实增量。[原训练报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260910_training_forward_review/brief/evidence/original_training_report.md)及[07-29 v3](/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/2026-07-29-current-yes-core-carry-v3-upgrade-and-performance-v1.md) |
| actual-transport：露点趋势、风向/暖输送、云雨、气压等entry-time monotone residual | 08-07；parent1349/31日/36城，historical末段138state/8日；仅obs≤decision；3h覆盖99.85%，云77.24%，气压71.83% | 最高风险32/138抓7/14，与Core完全相同；Brier .075878 vs .075182；降仓同6笔+$0.69也完全由Core解释 | 无相对Core新增tail value；首轮absolute lift PASS误报仅研究；旧entry expression dormant | 需**后续新报文**的顺序/收到时钟，不能把趋势列换名再训。[原文](2026-08/2026-08-07-current-yes-core-carry-actual-transport-tail-v1.md) |
| transition confirmation：10个连续摘要proxy、Core-logit offset、L2=.5 | 08-07；1349/801 city-date-bracket/36城/31日；15 OOF日+8历史末段；date→state→checkpoint权重 | Brier .075179 vs .075182，Δ−.000003 CI跨0；开发亦未改善 | 限制这10项摘要，不否定完整顺序/first-seen cloud-rain；inconclusive for sequence | 需真正新报文状态编码和版本可用时间；[原文](2026-08/2026-08-07-current-yes-core-carry-transition-confirmation-challenger-v1.md) |
| overshoot survival：标量暴露量current-status survival | 08-06；1349/36城/31日/95 upward；末段138state，8历史日期；缺逐事件完整时钟 | .078556 vs core .077255；Δ+.001301 CI跨0 | tested简化表达未胜，不是完整hazard已证伪；dormant | 需真实逐事件风险集和到达时钟；[原文](2026-08/2026-08-06-current-yes-core-carry-overshoot-survival-v1.md) |
| 静态full-ladder prior | 08-08；1349母表中898/29日可恢复，451scope缺口；119末段state，非clean forward | 两ladder系数全0，微小改善仅intercept+.088288；开发更差 | 静态实现无增量，不等于动态订单流无效；rejected_for_expression | 需新增动态信息及独立采集，不补事后盘口；[原文](2026-08/2026-08-08-current-yes-core-carry-full-ladder-prior-v1.md) |
| full-support校准结构 | 08-09v2；4061/1305城市日/46日/36城，25日2495OOF，最后7日263，历史valid-time | challenger/Core/market Brier .182963/.173113/.140487 | 此结构未优于市场；全域、carry、收益不可互换；superseded v1高mid分母保留 | 需独立相关域/经济增量；[v2原文](2026-08/2026-08-09-current-yes-core-carry-price-conditioned-full-support-v2.md) |
| 同规格C/D更新（Phase2/3） | 2026-08-11—09-10已用于development；FINAL6298行封存；同期逐日模型不同 | D记录资格95→103，6失败全保留；完整经济增量NA | 放宽少量边缘资格；暂未证明新alpha；frozen research | 独立持续checkpoint+费用/时钟+预算；[Phase3](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260911_phase3/brief/CORE_CARRY_PHASE3_DECISION_AND_ECONOMIC_REVIEW.md) |

共同原则：解释坏例子≠预测增量；抓风险≠比Core多抓；平均校准≠风险排序；小数点改善≠交易改善。负结果只约束实际测试的表达、信息、分母和时钟。保留dormant资产，不删除或写“全部证伪”。

## 本轮新增与尚未回答

新增的是独立slot采集、动态token/condition/fee身份、四模型原子虚拟状态，以及**一个**receive-PIT顺序编码。没有改生产特征、参数、算法、城市、资金；没有拟合新候选。新研究继承actual-transport已提出的post-entry invalidation方向，绝不冒称原创，也不能救下下一份报文尚未到来前已成交的那一单。

新编码只加一个signed transition：同exact bracket内，冷却且云雨已确认后，新报文显示回暖或明确转晴/无雨记+1；已知未确认后首次冷却+云雨确认记−1；60分钟内无新转折记0；缺少3份合法报文/收到时间或状态不明留NA。3h窗口、改报reset、重复不算新证据、跨档reset均固定。它与旧十列静态proxy不同，但**有差异不等于有增益**。

城市/季节适配、概率局部排序及是否还有被遗漏的序列价值仍未决。本轮不并行做城市模型、GBM/神经网络、全量特征或另一套温度分布。

下一步按[Phase4主报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260911_phase4/brief/CORE_CARRY_PHASE4_REVIEW.md)及已封存协议执行：先3个完整日工程验收，A/B/C/D 30终局日/60日覆盖审阅；转折分支用固定日期split及独立失败/转折事件信息量门，未达门不训练。只有owner可批准production promotion，代码跑通、采集更多、略准、模拟更好、真实盈利和接受是不同状态。

证据入口：Phase1 `run=20260910_training_forward_review/brief`；Phase2 `run=20260911_phase2/brief`；Phase3 `run=20260911_phase3/verified`；本轮 `run=20260911_phase4`，均在 `production://research_artifact_root/core_carry/`。另命名的Phase3独立审阅和旧Phase4提示词未在可定位附件/仓库中找到；本轮以用户完整整合请求为准，未把未读文件当证据。


## Phase5：自动终局接入与抽样一致性（2026-09-12交付，READY_FOR_REVIEW）

官方label/report worker已单独注册并启动，源码 `5552790c1f54aff6488c99194a4421a4c687c58b`，main registration `ed8d0e3a`；原collector `c6a3de0c…`、v2 epoch和15:26:36实际起点不变。首轮16:49:14Z：10个官方identity pending、0terminal；11实际尝试/10评分/9城市日/40模型概率/5共同carry，完整新日0，B锁后真实样例0。UTC时间不同的后续累积不能套用此快照。概率、隐含期望、理论fee quote收益及真实成交收益分开，unknown不充0。原始预测不写入label。

无label/收益输入的旧窗口核查确认：v1文字最早city/date/exact-token样本没有event子键，270个曾实际可见事件只保留113、丢157；不是已运行拟合器删数据。一次修订为原13:30—17:30固定landmark的第一实际尝试，再筛合法序列；旧数据1078合法样本/193可见事件、carry72/73（旧40）。新协议 `configs/weather/core_carry_transition_protocol_v2.json`；v1保留。全记录可用1959/6438，carry384/1447、B资格23/95、旧逐日D新增3/8，不能把全域信息量外推可交易域。

v2只澄清抽样、主指标全赔率母体/固定carry次级行动域、入场前信息和完整日期。原3h/3report/60min、单次fit、数值信息门、开发09-13..10-22、训练label cutoff10-23 00:00UTC、test10-23..11-11不变；A/B/C/D不重置。入场后新METAR不得倒算成避免原买入。完整采集处置日不要求每slot转折可用；研究合法样本和按cutoff可得官方labels仍需单列。下一节点3完整日工程/30完整终局日/60日历覆盖；未达信息门不训练、无promotion。

[Phase5主报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260912_phase5/brief/CORE_CARRY_PHASE5_REVIEW.md)、[增量数据](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260912_phase5/delivery)、[handoff](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260912_phase5/brief/HANDOFF.md)。40相关tests通过；单轮review两个P2已修复。没有新模型效力结论，未把READY_FOR_REVIEW写成owner接受。

补充实测（2026-09-11T23:46Z，非原封包快照）：用户要求复盘09-11圣保罗/旧金山跨档及新模型是否避开。相同live评分输入下B/D两案均正quote EV，C均非正；D概率92.27%/88.15%，不是避亏证据。实际forward圣保罗对应半点mid不在carry域，旧金山4槽位因合法标题`be between …°F`被当前正则误拒，没有评分，不能计为成功跳过。全epoch该错误35槽位/8城，逐条原文已导出；collector未修订/重启，历史unknown未改写，需版本化修订及独立重放后才能评价影响。两案还确认live评分mid与提交盘口mid不同，固定B按后者机械重算后两taker不再正EV；宽盘口mid可靠性及全窗影响尚未验证，不作生产修复或maker故障结论。冻结转折编码两案均因同档报文不足而NA。详见[个案及forward补充](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260912_phase5/case_review_20260911_sp_sf/CASE_REVIEW.md)，原始预测、原Phase5包与模型全部保留；两案从此标为已用于诊断。

同案进一步盘口/成交补证：276个事故token报价采样点和4个authenticated trade事件已保留在上述报告目录。圣保罗触发时买一87分50股、次档45分；旧金山买一81分10.18股、次档63分，后者入场报价优势仅0.33965美分/股。高概率跳变由市场输入驱动，不能当成独立天气确认或确定操纵。旧金山名义10股taker实际为交易所MAKER，在21:44:11/12UTC各成交5股；另5股maker为21:52:19UTC，更新先前只到canonical/终态发现时钟的精度。市场21:58:58已无bid、ask1分，早于source fetched22:01:22和sidecar22:01:33；对手信息来源仍未知。该补证是机制诊断，不是模型总体失效、序列必有增益或已完成全窗执行反事实的证明。


## 报文窗口与盘口质量系统研究（2026-09-12，READY_FOR_REVIEW）

按目标日07-25..09-11封存231 fill/226订单/164城市日/45日期；官方标签补齐226单，21失败单来自12城市日，canonical净PnL仍仅覆盖176单，不能作为全体PnL。提交点差>10美分9单4负5胜；任一固定宽差/断层/15股深度诊断16单5负11胜。44单可严格恢复评分时深度，缺失不作正常。223单原模型复算及订单使用概率一致，未发现调用错模型。

300秒相比90秒额外标记5笔历史提交，均胜；涉及4个首次入场城市日，另1个后续子单。SF09-11最后5股为窗口前挂单、窗口内认证成交，需单独验证撤单确认。SP09-11已过当时预计报文时间，90/300均覆盖。两种阈值和>10美分仅诊断，未批准ban或推广。优先下一假设是提交时同一盘口重算market输入及计划数量含费成本，次为固定90/300新单和存量单完整生命周期；均须独立验证。

[系统报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260912_execution_quality/CORE_CARRY_EXECUTION_QUALITY_REVIEW.md)、[完整失败与差盘口成功对照](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260912_execution_quality/delivery/CASEBOOK.md)。已有Phase5/A/B/C/D/转折状态保持；本历史窗口已用于development，不是clean OOS，无重训/生产/订单变更。


## 时钟与完整尝试分母补审（2026-09-13，READY_FOR_REVIEW）

追加纠正上节证据用途，原包/原评级保留：34笔score−book>60秒均为旧collector钟与新REST的元数据引用混用，不能认定实际旧book；archive12降为上下文，严格评分深度44→32，差盘口union16→13（5负8胜）。12笔score→order>60秒中9笔在请求前下界仍>60秒；4笔match早1秒与ack后recorded时间相容，非已证泄漏。

共同cutoff2026-09-12T14:56:22.964848Z：442提交/207城市日、全候选attempt订单1826城市日、17个eligible但无submit；226有fill、216无canonical fill，其中110终态unknown。原extra300五笔均胜；原4仍为全442中的最早recorded submit，但Chicago实际请求窗口未知。另3城市日有更早未成交提交，2个同秒并列，不能按ID打破真实先后。

S与同覆盖W90/W300仅注册；δ=.25美元/目标日、真实配对sd未知、有限pilot/固定样本公式/停止与未来未见holdout见[冻结方案](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260913_clock_lifecycle_audit/portable/reports/MINIMAL_EXPERIMENT_SPEC.md)。未启动新试验/采集，无生产/模型/参数变更。27相关测试通过，单轮review四P2修复；reviewer实际模型/用量metadata缺口仍记录，不自认完整验收。[补审主入口](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260913_clock_lifecycle_audit/portable/START_HERE.md)。收件r01、交付r02：`pm-agents-core-carry-execution-quality-20260913-r01/r02`，归档总入口 `~/Documents/GPT-Pro-Reviews/README.md`；reviewer未提供输入SHA及其所述CSV/code附件，关联仅内容核对。


## S离线适配与观测缺口（2026-09-14，READY_FOR_REVIEW）

冻结442提交/207城市日的S工程适配已完成：436有原score、434可按submit top重算p、356有prepared journal数量/限价/类型；95个提交关联评分梯度（含未成交），成交子集仍32。最终execution全梯度及原子weather/budget/full-path闭合均不足，完整历史S评价0、UNKNOWN442。7笔p<ask诊断复算一致，不能当实际负EV或可保证拦截亏单。

代码扩展已有core_carry_execution_protocol，复用56ef exact P0纯函数及原near filled+pending预算；core新入场、near sole non-positive taker EV与maker replacement分别处理。PASS_CONTRACT_ONLY依赖可信规范化packet/producer attestations，不等于完整执行器适配或真实forward。52相关测试及独立目录重放通过，review4个误PASS边界修复；实际reviewer模型/用量元数据仍缺。未启动新采集/forward/生产，无重训或参数调整。

[主报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260914_s_offline/reports/CORE_CARRY_S_OFFLINE_REVIEW.md)和[最小接入清单](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260914_s_offline/reports/OBSERVATION_GAPS_AND_INTEGRATION.md)明确下一项为已有book/prepare、report epoch、原资格/预算、journal lifecycle四处追加观测，复用请求；真实接入需另行授权并核对当时loaded SHA。S/W、A/B/C/D和转折协议不覆盖，3个未见完整日尚未开表。

## 近期 A/B/C/D 配对监测（2026-09-15，READY_FOR_REVIEW）

追加原版实际成交复盘（标签截止09-15 00:42北京时间）：09-08—13按合约/城市日去重34信号28胜6负，82.35%，原始提交p均值89.03%；4/6日有失败，09-11—13连续。core17/20、near11/14；整个九月core35/38仍92.11%，不能把合并分支与近期集中失利概括成全月模型崩坏。0.90—0.97九信号6胜3负是条件校准疑点；仅六日期，bootstrap实际−预测CI跨0，inconclusive/no-live-change。135fill的研究标签补46、余1pending；2条canonical概率与raw提交不一致仅导出，原数据不改。完整continuous机会分母未物化，878近期checkpoint不代替34成交。以[原版准确率报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_original_recent_performance/ORIGINAL_CORE_CARRY_RECENT_ACCURACY_REVIEW.md)作为当前实际成交正确率入口；下段独立forward比较是不同分母，继续保留。

用户要求比较近几天新旧模型；截至09-14T16:14:22Z，独立forward246评分中163有官方标签，55城市日/28城/4目标日，83pending。全标签日期/城市日等权Brier B=.077220、D=.080755；唯一完整采集处置/评分终局日09-13的108条为B=.048904、D=.048986，暂无D改善证据，不能认定长期无效。C完整日优于B但逊于market，跨日不稳定。D资格6 vs B2，仅1条D-only已胜，其他pending且完整经济全NA；零资金forward不能当成交收益。旧金山15槽位标题解析失败、无分数，不算避亏。样本已用于本次监测，不能整体标作未见盲测；无拟合/参数/生产变更，S与转折协议不变。[报告与精简数据](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_recent_forward_compare/RECENT_FORWARD_MODEL_COMPARISON.md)；[handoff](/Users/deepsleep/projects/pm_agents/tasks/WCIR/handoffs/WCIR-RECENT-FORWARD-COMPARE-01.md)。

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


## GPT Pro完整实验审阅包（2026-09-15，READY_FOR_REVIEW）

用户追加执行同基座与forward分类核对已完成：22策略逐一共享6809候选/3468条10股报价，原始market/B概率与quantity/principal/fee/cost逐条一致。B同样是报价模拟，不能与原策略真实成交混比；统一policy未照搬production eligible，B选中141票中17原eligible=false。7月398条5股报价没有放大成10股，排除在共同交易分母。

明确修正读法：最新树/MLP在09-15实际拟合，W最终fit完成04:13:34.727193UTC、历史cutoff09-11；09-11—14是回溯时间留出，不是真实forward，方案在已看事故后制定。真正当时记录的FINAL C/D独立forward是另一套Logistic，采样/费用/预算也不同。所有旧产物保留；本次0fit/0调参/0生产写入，仅核验、报告、组包。

完整版本台账/各算法参数/8fold训练时间与样本/18候选含负结果/执行与未来函数缺口见[完整实验报告](/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry/run=20260915_experiment_review/CORE_CARRY_MODEL_SELECTION_TRAINING_EXPERIMENT_REPORT.md)。review_id `pm-agents-core-carry-pit-candidates-20260915-r02`，关联r01输入包SHA f7be896adbd3a25d83bc64580ecb72861cea8f7e0ba799aba9fc15cadff65082（精确SHA以manifest为准）；本轮review_packet/manifest提供准确前序SHA、上传包与prompt身份。总库 `~/Documents/GPT-Pro-Reviews/README.md`，prepared_not_sent，用户自行发送，非ownerACCEPTED。
