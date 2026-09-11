# Core Carry 研究主上下文

本文件是后续 Core Carry 任务的优先入口，详细数字回溯前三阶段及下表原报告。状态：Phase5 独立官方标签/日报于2026-09-11T16:49:10Z启动；原Phase4采集器及模型不变，转折仅抽样协议修订为v2、仍未拟合；A/B/C/D 冻结，未批准生产替换。生产真实状态仍须读当前 manifest，不用本文替代进程证据。

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
