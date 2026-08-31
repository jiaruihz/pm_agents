# Core Carry 研究现状总结与外脑咨询提示词(2026-08-20)

> 目的:自包含的研究现状快照 + 可直接复制的高级模型咨询提示词。所有数字来自 2026-08-20 前的 canonical/raw/链上复核,证据细节见同日 [maker adverse-selection 全审计](2026-08-20-core-carry-maker-adverse-selection-review-v1.md) 与 registry。

## 一、策略机制(一分钟版)

Polymarket 城市每日最高温市场(`exact bracket`,如 "Amsterdam 最高温 = 21°C")。策略买 **current exact bracket 的 YES**:当当前温度所在档已锁定运行最大值时,赌它保持到日终(persistence)。

- **数据**:aviationweather METAR(30 分钟 cadence,obs→我们 ingest 延迟 3~15 分钟)、Open-Meteo ECMWF/GFS 小时级预报、Polymarket CLOB 盘口(REST 快照 5 分钟粒度)。
- **模型**:frozen logistic(Core v3),仅 5 特征:`market_logit`(市场中价 logit)、`decision_hour_local`(本地小时)、`dewpoint_depression_f`、`wind_speed_kt`、`obs_age_min`(+预报峰值时钟变体)。输出 p_hold(当前档保持概率)。
- **执行**:触发条件 = `p_hold ≥ 0.80 ∧ (p − ask − fee) > 0`,在**首个**满足时刻进场(first-positive)→ 10 股 taker 立即成交 + maker 5+5 股(staged 追价 / pullback 静态,双 sleeve A/B);maker 挂单到"下一份报文预期时刻 −90s"强制撤;报文 due 已过则不挂(blackout,fail closed)。
- **触发条件的已知弱点(什么时候我们不行)**:
  1. first-positive 意味着进场时刻几乎总是"市场价格刚被打到满足线"——两个已链上核实的案例(Warsaw 8/18、Amsterdam 8/20)触发诱因都是**对手第一波卖压压低了 ask**,我们实际在系统性接下跌的刀;
  2. 亏损聚集在 **p 0.87–0.92 中置信带 × 低价入场(ask 0.84–0.86)**:7 个 loss 中 5 个在此区间;另有 2 个高 p 例外(Chengdu 0.989=预报峰值时钟午夜别名错误,Miami 0.994=骤降中反转再升);
  3. 无最小 edge 要求(edge>0 即进);mid-floor forward v1 测过更高质量价格带(0.90),事后更优但 family-wise p=0.55 不显著,冻结未动;
  4. 入场无升温路径/预报分歧检查(唯一活跃假设正在补)。
- **规模**:tiny live(单 signal ≤20 股,日成本上限 $100),25 天 80 个 signal。

## 二、已证实的事实

### 表现与损失结构

| 事实 | 数字 |
|---|---|
| signal 结算胜率 | 74W/5L = 94.5%(settled;另有 2 个未结算已确认输,合计 7 loss) |
| taker 腿 PnL | +$10.73(settled) |
| maker 腿 PnL | ≈ −$12.5(含 Warsaw 单 signal −$16.75 中的 maker −8.25) |
| 全部 7 个 loss 的机制 | **均为 upward overshoot**(温度升破当前档) |
| 单个 loss 的代价 | −$8~−17,约等于 20~40 个 win 单的利润 |

### maker / 执行层(全部已定量证伪)

| 结论 | 证据 |
|---|---|
| maker fill = 反向选择 | fill 的 signal 胜率 87.5~90% vs 不成交 95.6~100%;全部亏损 maker fill 挂价 ∈[0.80,0.84] |
| 亏损 maker fill 全是"闪吃" | 链上核实:对手在 obs 前 5.3~19.2 分钟一笔卖压扫掉我们的 top bid;不存在可观察盘口前兆 |
| 撤单 buffer 无解 | obs−8min 只覆盖 1/5 亏损型;obs−15min 净效果≈0;obs−20min 砍 80% 正确成交 |
| 触发时挂 maker 替代 taker | fill 率 29~41%,EV 全部低于 taker(打平需 88%) |
| p 一算出来就提前挂 | 全量回测 EV −0.035/股 vs taker +0.026(挂价被当时 ask 锁死+胜率被稀释,价格触发线本身是有效过滤器) |
| 延迟执行等价差收窄 | 触发后可成交买价上行,5 分钟中位 +2~9c(等待成本 >> 1.5c spread) |
| 追价/改价 | 收益上界 +$1.6/6天 vs Warsaw 单次 −8.25;历史上改价全是防守性跟价(7% 成交率) |

### 概率层(7+ 轮 challenger 全部未胜)

- 语义特征、transition confirmation、overshoot survival、full-ladder prior、actual-transport tail、market spline/interaction、price-conditioned full-support:全部在冻结 forward 上 CI 跨 0 或更差。
- **入场时升温斜率判别力弱**(1.2~1.3x lift,两轮独立数据确认);校准整体偏乐观 4~6pp(live-selected 分母,含选择效应)。
- sizing overlay 被拒(误伤 >94% winner shares);exit/减仓 overlay 被拒(25 次退出全部落在最终 winner,CI 全负)。
- **唯一活跃假设**:`预报峰值已过(peak_delta<0) ∧ 实际再创新高`——8 个 signal、loss rate 25%(2.8x lift),覆盖最近两个 loss(Warsaw、Amsterdam),n 太小未验证。

### KNMI 快源与 nowcasting 建模(仅 Amsterdam)

- 覆盖:仅阿姆斯特丹(7 个 loss 里 1 个);市场结算源是 Wunderground EHAM **METAR**,不是 KNMI(source-basis 差 0.1~0.5°C)。
- 模型质量:V8 frozen 显著输 market;V9 ΔBrier +0.010 CI 跨 0(dormant);`cross_survival` head AUC 0.945(0.7°C 跨档存活)是唯一显著优于基线的 head,但 live 零 forward。
- **nowcasting 思路(高频源预测低频结算源)**:结算报文(METAR,:25/:55)发布前,用更高频的 KNMI 10/20 分钟读数 **预测下一份 METAR 的温度/是否创新高**——不是拼获取速度(我们管道比对手慢),而是把"快"变成"预测":P(下一份 METAR 创新高 | KNMI 最近 2~3 个 10 分钟读数 + 预报 + 小时/季节)。已有正面证据:clock-parity 443 rows 里 19 次 model-vs-market 方向分歧,KNMI 增强模型判对 14/19(vs 市场 5/19)——高频信息在同源分歧时确实比市场准;负面折扣:KNMI provider-created 后我们 checkpoint 典型延迟 11.3 分钟、source-basis 差未系统量化。
- 可推广性硬约束:其他城市**没有** 10 分钟级公开源(只有 30 分钟 METAR),nowcasting 路线只在阿姆斯特丹直接可行;全城推广需评估非官方快源(airport sensors/WindSup 类,合规与稳定性未评估)。
- 10 分钟观测的另一用途:Amsterdam 8/20 型**入场后升温**的持仓期预警(KNMI 比 METAR 早 25~45 分钟可见),不是入场概率替换。

### 执行层新观察:上一档价格 = 免费 overshoot 读数

Warsaw 链上核实:20°C 档被砸(12:42)的**同一时刻**,上一档 21°C 从 0.08 升至 0.14(13:01 报文确认后飙至 0.9)。上一档价格即市场对 overshoot 的实时定价:(a) 是 p_hold 的天然交叉校验(当时我们 p=0.879 ↔ 上一档隐含 8~14%,基本一致);(b) 保险腿(买上一档)成本约主腿 7%;(c) 其短时变动可能早于任何天气特征提示 overshoot。全城可用、尚未利用。

### 数据与部署现状

- 已部署止血:maker 挂价 <0.84 fail closed(shadow 继续记录);已识别但未部署:无。
- 数据缺口:token 级成交流(tape)未采集;挂单时盘口结构(下方支撑深度)未记录;KNMI-vs-METAR 分歧未量化;对手信息源未知(比 obs 快 5~19 分钟)。

## 三、当前卡点(开放问题)

1. **5% overshoot loss 不可预测**:94.5% 之后的所有特征方向已试尽(见上),单个 loss 抵 20~40 个 win。是继续攻预测,还是转向尾部管理(仓位/组合/对冲)?
2. **触发条件本身的改进**:first-positive 的"接刀"结构、p 0.87~0.92 × 低价入场的亏损聚集、edge>0 无最小要求——触发端有没有既不稀释机会、又能避开聚集区的重构?
3. **forecast-actual divergence 假设**(2.8x lift,n=8)如何高效验证:tiny live ~3 signal/天,统计功效永远不够,shadow/回放的正确实验设计是什么?
4. **高频 nowcasting 的定位**:KNMI→METAR 下一份报文预测(14/19 分歧判对的正面证据)在阿姆斯特丹值多少、能否推广到无快源城市、与结算 source-basis 差如何共存?
5. **模型/参数天花板**:5 特征 logistic 之后,下一档改进在哪——新特征(需新采集)、模型族(GBM/NN/生存模型)、分层多模型、还是 market-anchor 微调?在 base rate 94.5%、正样本 70 个的约束下,什么方法不会过拟合?
6. **执行层是否彻底关闭**:三层证伪后,小账户在对手信息优势(快 5~19 分钟)下的执行最优解是否就是纯 taker?maker 是否只剩结构化用法(高置信高价带、盘口健康条件、跨档保险)?
7. **数据采集优先级**:tape / 挂单结构 / KNMI 分歧 / first-seen transition 事件,先采哪个?

## 四、咨询提示词(直接复制以下全部内容)

```text
我在运营一个小规模的量化天气预测市场策略(Polymarket 城市每日最高温市场),需要你作为资深量化研究员帮我分析研究路线。请先读完背景和"已证伪清单"再回答——很重要:我已经用数据排除了大量常见建议,如果你建议的方向在已证伪清单里,请说明你的版本与已证伪版本的关键差异,否则不要重复建议。

【策略机制】
- 市场:Polymarket 每城市每日最高温 exact-bracket 二元市场(如 "华沙今天最高温 = 20°C" YES/NO),官方结算源为 Wunderground 机场 METAR 序列的日最高值。
- 策略(current exact persistence):当日温度运行最大值进入某档后,买该档 YES,赌温度不再升破/跌破该档直到日终。本质是 "P(当前档保持到日终)" 的定价。
- 数据:机场 METAR(30 分钟一条,我们看到数据平均延迟 3~15 分钟)、ECMWF/GFS 小时级预报(一天更新几次)、CLOB 盘口(5 分钟快照)。部分城市(仅阿姆斯特丹)有气象局 10 分钟级观测,但结算仍以 METAR 为准,两者有 0.1~0.5°C 系统差。
- 模型:冻结 logistic 回归,只有 5 个特征:市场中价 logit、本地小时、露点差、风速、观测年龄。输出 p_hold。
- 触发条件:p_hold ≥ 0.80 且 (p − 卖一价 − 手续费) > 0,在首个满足时刻进场(first-positive)。已知弱点:(a) first-positive 使进场几乎总在"价格刚被打到满足线"的时刻——两个已核实的案例(Warsaw、Amsterdam)触发诱因都是对手卖压压低卖一价,我们在系统性接下跌的刀;(b) 7 个亏损中 5 个聚集在 p 0.87~0.92 中置信带 × 低价入场(卖一 0.84~0.86),2 个例外分别是预报峰值时钟错误和骤降中反转;(c) 无最小 edge 要求(edge>0 即进)。
- 执行:触发后 10 股 taker 立即成交 + 10 股 maker 限价(staged 追价/静态两个 sleeve);maker 挂到"下一份报文预期发布前 90 秒"强制撤单。
- 规模:微型实盘,25 天 80 个信号,单信号上限 20 股(约 $17 名义)。

【已证实的结果】
- 信号胜率 74胜5负 = 94.5%,taker 腿累计 +$10.7;但全部 7 个亏损(含 2 个未结算已确认)都是同一机制:温度升破当前档(upward overshoot),单个亏损 -$8~-$17,约等于 20~40 个赢单的利润。策略整体接近盈亏平衡,亏损尾部是唯一的结构性问题。
- 模型校准:整体偏乐观 4~6 个百分点(注意:样本是价格触发筛选后的子集,有选择效应)。

【已证伪清单(全部有定量证据)】
概率/特征层:
1. 加入 METAR 升温斜率(1/2/3小时温度与运行最大值增量):判别力仅 1.2~1.3x(亏损组与赢组分布重叠),两轮独立数据确认。
2. 语义/转变类特征(forecast 跨档、fresh-high、dewpoint 趋势、风向、云雨转变、剩余热量等,多个 challenger 版本):冻结 forward 上全部 CI 跨 0。
3. overshoot 生存模型(market cumulative hazard × 剩余热量/路径暴露):Brier/logloss 更差。
4. 完整 ladder 先验、forecast revision 重定价、market spline/交互:全部未胜当前 5 特征 logistic。
5. 仓位分层(按 overshoot 风险降仓):误伤 >94% 的赢单份额;出场/减仓 overlay:历史上 25 次退出全部退在最终赢单上,CI 全负。
6. 提高入场价格带阈值(0.80→0.90):事后窗口更优但多重检验校正后不显著(family-wise p=0.55),已冻结不动;0.80~0.92 各价格带 proper-score CI 全部跨 0。
执行层:
7. maker 替代/部分替代 taker(三种方式全部证伪):(a) 触发时挂 maker:窗口内成交率只有 29~41%,期望低于 taker(打平需 88%);(b) 模型 p 一出就提前挂(不等价格触线):全量回测 EV -0.035/股 vs taker +0.026/股——挂价被当时卖一价锁死,且绕过价格触发线把胜率从 94.5% 稀释到 90.1%,价格触发线本身被证明是有效过滤器;(c) 延迟执行等价差收窄:信号触发后可成交买价系统性上行(5 分钟中位 +2~9 美分),等待成本远大于价差。
8. maker 的反向选择已定量:成交的 maker 单胜率 87~90% vs 未成交的 95.6~100%;全部亏损 maker 成交挂价在 0.80~0.84;链上核实对手在数据发布前 5~19 分钟主动扫掉我们的买单(我们的数据管道延迟 3~15 分钟,信息上系统性落后)。
9. 提前撤单(各种 buffer):对手提前量分布 5~19 分钟太宽,固定 buffer 没有无损设置点(提前 8 分钟只覆盖 1/5 的历史亏损形态;提前 15 分钟净效果归零;提前 20 分钟砍掉 80% 的正确成交)。
10. 追价/爬价机制:收益上界(6 天完美情形)约 +$1.6,对比单次亏损 -$8~17,不对称。

【唯一活跃的正面假设】
1. "预报峰值已过 ∧ 实际观测再创新高"(forecast-actual divergence):8 个信号样本里亏损率 25%(基准 9%,lift 2.8x),恰好覆盖最近两个最大亏损。但 n=8、其中 2 亏损,CI 必然跨 0,只是假设生成。
2. 高频 nowcasting(仅阿姆斯特丹):结算源 METAR 为 30 分钟节奏,我们另有一条 10 分钟节奏的气象局源(KNMI),但结算不看它(两源有 0.1~0.5°C 系统差)。思路是"不拼获取速度、拼预测":用最近 2~3 个 10 分钟读数 nowcast 下一份 30 分钟 METAR 报文的温度/是否创新高,从而在结算报文发布前 10~20 分钟更新 p。正面证据:历史 443 个对齐样本中 19 次模型与市场方向分歧,KNMI 增强模型判对 14 次、市场仅 5 次。负面折扣:高频源我们拿到手平均晚 11.3 分钟;两源系统差未量化;且其他城市没有等价高频源,该路线目前只覆盖 1/7 的亏损城市。
3. 上一档价格 = 免费的 overshoot 概率读数(全城可用):买当前档(如 20°C @0.85)时,上一档(21°C)同时在场内交易,Warsaw 案例中其价格在第一波卖压的同刻从 0.08 升到 0.14(报文确认后飙至 0.9)。这意味着:(a) 市场一直在为 overshoot 定价,上一档价格是我们模型 p_hold 的天然交叉校验(我们的 p=0.879 隐含 12% 破档概率,与上一档 0.08~0.14 的读数接近);(b) 买上一档作保险腿的成本量级约为主腿的 7%(0.10~0.15 × 5 股 vs 0.85 × 10 股);(c) 上一档价格的短时变动(几分钟内 +6c)可能比任何天气特征都更早提示 overshoot。我们尚未系统利用过这个信号。

【我的约束】
- 微型规模,~3 个信号/天,统计功效天然不足;不能大规模加仓做实验。
- 数据管道可以增加采集(成交流/盘口结构/更快观测),但不知道哪个最值。
- 市场流动性有限(天气 niche 市场,单档深度几十股)。
- 对手中有明显的信息优势者(比官方数据发布早 5~19 分钟行动,可能用机场实时源)。
- 成本结构与绝对量级:taker 手续费 ≈ 名义本金的 0.4%(约 0.4 美分/股),maker 零手续费;策略 25 天累计接近盈亏平衡(taker 腿约 +$11、maker 腿约 −$12.5),单 signal 名义 ≤$17。任何建议请在这个绝对量级下评估值不值得做。

【我的问题(按重要性排序)】
1. 概率层:在 base rate 94.5%、约 70 个正样本/25 天的约束下,预测剩余 5% upward-overshoot 亏损的最优路径是什么?请具体评估:(a) 还有哪些我们没试过的特征族(预报路径分歧、跨市场信息、太阳辐射/云量、气候学先验等)在 0.5~1pp 的概率增量尺度上值得试;(b) 换模型族(GBM/生存分析/贝叶斯层次)在 n=79 下是真提升还是过拟合陷阱;(c) 如果预测不可行,尾部管理(对冲买上一档、动态仓位、组合层止损)在我们的仓位规模下是否更实际。
2. 触发条件重构:first-positive 的"接刀"结构(进场时刻由对手卖压定义)、亏损聚集在 p 0.87~0.92 × 低价带、edge>0 无最小要求——请设计一个不稀释总机会(94.5% 胜率的分母不能明显缩水)但能避开聚集区的触发结构。注意单纯提高价格/概率阈值已被证伪(见已证伪清单第 6 条),请给结构性方案(如二次确认、上一档价格条件、时间窗约束、分批进场等),并给出用什么历史数据能离线验证(我们有全部 4700 个逐报文 checkpoint 评分 + 当时盘口 + 链上成交)。
3. 高频 nowcasting:用 10 分钟源预测下一份 30 分钟结算报文(14/19 分歧判对的正面证据、11.3 分钟获取延迟、0.1~0.5°C 源差)——(a) 请给出 nowcast 下一份 METAR 读数的具体建模方案(回归/分类/两步:先预测高频读数轨迹再映射结算源);(b) 源差(高频源≠结算源)应该怎么显式建模;(c) 在只有阿姆斯特丹有高频源的约束下,这条路线的期望价值上限,以及是否有办法把"预测下一份报文"泛化到无高频源城市(例如用上一份报文后的预报偏差统计)。
4. maker 的结构化用法:在"亏损成交全部是对手主动吃、撤单类反应机制逻辑上无效、但高价带(0.95+)成交样本胜率 100%(n=9)且 EV≈0"的事实下:(a) maker 是否应该只保留在高置信×高价带做边际改善,量化标准怎么定;(b) 用"买入上一档 YES 作保险"(升破当前档时上一档赢,类似 ladder spread)对冲 overshoot 的结构在我们的仓位规模下是否正 EV,保险腿的最优比例/价格上限怎么算(参考量级:上一档典型 0.05~0.15,保险成本约主腿 7%);上一档价格本身作为 overshoot 概率的市场读数,应该进特征、进触发条件还是只进对冲,给出你的判断;(c) 小额账户在信息劣势下是否还有其他 maker 存活结构(例如只在盘口健康——挂价下方有支撑、近期无卖压流——时挂单)。
5. 校准:整体偏乐观 4~6pp 但样本是价格触发筛选后的子集。这种选择效应下的校准修正应该怎么做?值不值得修(我们的入场阈值本身就是 p-价差>0,校准偏移直接影响阈值)?
6. 数据投资优先级:token 级成交流(tape)、挂单时盘口结构快照、阿姆斯特丹 10 分钟源与结算源的分歧配对、逐 checkpoint 评分历史——按"每 MB 数据的期望研究价值"排序,并说明每项能解锁什么分析。
7. 盲区检查:基于以上全部信息,你认为我们最可能忽略的一个系统性问题是什么?

【输出要求】
- 每个问题给具体可执行的方案(公式/伪代码/实验设计),不要泛泛建议。
- 明确区分"立即值得做"、"先采数据再决定"、"不建议"。
- 如果某个方向你认为在我们的样本量和市场规模下没有前途,直接说。
- 篇幅有限时优先回答问题 1、2、4。
```

## 四(附)、本地数据包(建议与提示词一并复制给外部模型)

### 1. 亏损 signal 的"入场→温度破档"时间(全部 7 个)

```text
Singapore 07-27: 入场06:44 -> 破档obs 07:30 = 45 分钟
Chengdu    07-27: 入场09:33 -> 破档obs 10:00 = 27 分钟
Lucknow    07-31: 入场09:06 -> 破档obs 11:30 = 144 分钟
Manila     08-07: 入场05:40 -> 破档obs 06:00 = 19 分钟
Busan      08-17: 入场08:46 -> 破档obs 09:49 = 63 分钟
Warsaw     08-18: 入场12:46 -> 破档obs 13:00 = 13 分钟
Amsterdam  08-20: 入场12:44 -> 破档obs 14:25 = 101 分钟
```

解读要点:**没有任何亏损是"秒破档"**——最短 13 分钟、中位 45 分钟。maker 被闪吃(几秒内)≠ 破档(几十分钟),说明保险腿/退出/对冲机制有真实的时间窗口可用。注意这是"温度首次越过档上沿的报文时刻",若用更密观测(如阿姆斯特丹的 10 分钟源)窗口还会更早。

### 2. 触发时刻的本地小时分布(win vs loss)

```text
本地小时   win  loss
13:00     21    0
14:00     14    5
15:00     12    0
16:00      9    0
17:00      5    2
```

7/7 亏损集中在 14:48–17:45 本地;但 14–18 时段也承载 40/61 的赢单(该带 loss rate ≈15%,13 时带 0/21)。本地小时已在模型特征里(线性项);该表显示风险集中在午后晚段的非线性区。

### 3. 全部 signal 明细(86 行;字段:城市|月日|档|本地小时|p|入场ask|taker成交价|结果|PnL$)

```text
Wellington|07-25|12|13.6|1.0|0.999|0.999|W|0.0
NYC|07-25|78-79||0.843|0.82|0.82|W|0.86
PanamaCity|07-25|33||0.942|0.939|0.939|W|0.29
Chicago|07-25|80-81||0.961|0.94|0.94|W|0.29
SanFrancisco|07-25|70-71||0.971|0.95|0.95|W|0.24
Guangzhou|07-26|29|13.62|0.997|0.99|0.99|W|0.15
SanFrancisco|07-26|68-69||0.899|0.86|0.86|W|0.67
Wellington|07-27|11|15.52|0.982|0.98|0.98|W|0.25
Taipei|07-27|35|13.7|0.925|0.92|0.92|W|0.38
Singapore|07-27|31|14.68|0.876|0.85|0.85|L|-12.28
Beijing|07-27|34|15.7|0.906|0.9|0.9|W|1.03
Wuhan|07-27|32||0.95|0.94|0.93|W|0.73
Chengdu|07-27|29|17.5|0.989|0.92|0.93|L|-13.88
CapeTown|07-27|17|13.55|0.905|0.81|0.82|W|1.34
Amsterdam|07-27|21|17.5|0.964|0.94|0.94|W|0.64
Chicago|07-27|84-85|14.52|0.903|0.883|0.88|W|0.57
SanFrancisco|07-27|68-69||0.919|0.9|0.9|W|1.23
Wuhan|07-28|33||0.983|0.98|0.98|W|0.2
Karachi|07-28|33|15.68|0.982|0.98|0.98|W|0.1
NYC|07-28|80-81|13.78|0.934|0.92|0.92|W|0.83
SanFrancisco|07-28|70-71||0.965|0.95|None|W|0.3
Wellington|07-29|12|16.65|0.97|0.942|0.942|W|0.28
Guangzhou|07-29|31|14.53|0.901|0.89|0.89|W|0.53
Wellington|07-30|14|14.629999999999999|0.98|0.96|0.96|W|0.19
BuenosAires|07-30|17|16.47|0.967|0.96|0.96|W|0.19
LA|07-30|76-77||0.861|0.85|0.85|W|0.72
Lucknow|07-31|32|14.52|0.871|0.85|0.85|L|-8.56
Ankara|07-31|26|17.48|0.864|0.85|0.85|W|1.44
Chengdu|08-01|27|16.47|0.901|0.89|0.89|W|1.05
Miami|08-03|90-91|13.55|0.978|0.97|0.97|W|0.49
NYC|08-03|84-85|17.58|0.974|0.97|0.973|W|0.46
Manila|08-04|30|13.620000000000001|0.992|0.99|0.99|W|0.1
SanFrancisco|08-04|72-73||0.986|0.98|0.98|W|0.19
Wellington|08-05|9|13.52|0.963|0.91|0.91|W|0.86
Manila|08-05|28||0.974|0.96|0.96|W|0.38
CapeTown|08-05|16||0.947|0.91|0.91|W|0.86
Amsterdam|08-05|25|16.48|0.972|0.96|0.954|W|0.69
Shanghai|08-06|33|13.6|0.829|0.81|0.81|W|2.82
Munich|08-06|24|17.6|0.964|0.96|0.96|W|0.38
Wellington|08-07|12|14.65|0.942|0.92|0.92|W|1.21
Tokyo|08-07|33|13.57|0.856|0.82|0.832|W|1.61
Manila|08-07|28||0.919|0.9|None|L|-4.15
Shanghai|08-07|34|13.62|0.949|0.92|0.92|W|1.21
Chongqing|08-07|30||0.922|0.91|0.915|W|1.31
Karachi|08-07|33|13.6|0.953|0.94|0.94|W|0.57
Helsinki|08-07|22|15.57|0.874|0.85|0.85|W|2.24
Helsinki|08-08|20||0.895|0.86|0.86|W|2.09
NYC|08-08|90-91||0.938|0.93|0.93|W|1.07
Wellington|08-09|14||0.981|0.98|0.98|W|0.34
Manila|08-09|31||0.988|0.98|0.98|W|0.19
Shanghai|08-09|28||0.986|0.95|0.94|W|0.57
Taipei|08-09|29||0.925|0.91|0.91|W|0.86
Helsinki|08-09|22||0.916|0.89|0.898|W|0.97
Chicago|08-09|84-85|13.530000000000001|0.976|0.97|0.962|W|0.57
Manila|08-10|32|13.58|0.972|0.959|0.959|W|0.39
Tokyo|08-10|30|15.65|0.981|0.98|0.98|W|0.19
Shanghai|08-10|28|16.52|0.974|0.94|0.94|W|0.57
Amsterdam|08-10|23|13.52|0.878|0.87|0.87|W|1.24
CapeTown|08-10|15|15.620000000000001|0.987|0.98|0.935|W|0.62
PanamaCity|08-10|33|16.62|0.913|0.89|0.89|W|1.05
Karachi|08-11|32|15.620000000000001|0.935|0.9|0.9|W|1.6
London|08-11|24|15.55|0.88|0.87|0.87|W|1.24
BuenosAires|08-11|12|14.58|0.829|0.82|0.82|W|1.73
Shanghai|08-12|27|14.85|0.923|0.885|0.885|W|1.84
Jeddah|08-12|39|17.6|0.898|0.86|0.862|W|1.32
Karachi|08-13|33|15.57|0.992|0.99|0.99|W|0.1
Manila|08-15|31|14.5|0.945|0.94|0.94|W|0.92
LA|08-15|76-77||0.982|0.98|0.98|W|0.19
Wellington|08-16|10|13.58|0.996|0.99|0.99|W|0.1
Manila|08-16|32|13.62|0.909|0.84|0.85|W|3.19
Helsinki|08-16|21|14.67|0.94|0.91|0.92|W|0.76
Wellington|08-17|10|14.6|0.974|0.97|0.97|W|0.36
Busan|08-17|25|17.75|0.917|0.897|0.906|L|-9.1
Karachi|08-17|31|15.52|0.963|0.95|0.95|W|0.48
CapeTown|08-17|17|16.62|0.991|0.99|0.99|W|0.1
Wellington|08-18|12|13.58|0.988|0.98|0.98|W|None
Taipei|08-18|34|14.58|0.906|0.9|0.9|W|None
Karachi|08-18|32|13.52|0.91|0.88|0.887|W|None
Warsaw|08-18|20|14.77|0.879|0.85|0.85|L|None
Amsterdam|08-18|21|16.52|0.985|0.98|0.98|W|None
Shanghai|08-20|32|13.65|0.992|0.99|0.99|W|None
Manila|08-20|32||0.958|0.95|0.95|?|None
Jeddah|08-20|39|15.55|0.928|0.91|0.915|W|None
Amsterdam|08-20|21|14.72|0.892|0.86|0.86|L|None
```

注:p 为模型概率、ask 为触发时卖一、takerPx 为实际成交均价、W/L 为结算结果(?=未结算);PnL 为该 signal 全部腿合计(settled 口径)。供自行切片的两个基本事实(未做显著性声明):7/7 亏损的入场 ask ≤ 0.92,ask > 0.92 的 59 个 signal 零亏损;7/7 已成交亏损的 p ∈ [0.871, 0.989],其中 6/7 在 0.87–0.92(唯一例外 Chengdu p=0.989,其亏损归因于预报峰值时钟午夜别名错误;另有两个高 p 评分触发未成交,不在表内亏损中)。

### 4. 对手结构观察(未系统统计,定性)

链上可见吃掉我们 maker 单/制造亏损成交的 pseudonym 在多个城市的亏损窗口重复出现(如 Cloudy-Injusti 同时出现在 Singapore 与 Chengdu 窗口),提示存在跨城市活跃的知情参与者群体,而非随机散户。

### 5. 数据获取延迟的构成(工程事实)

我们的观测管道每 ~5 分钟轮询一次源站(单次抓取 2.5 秒),延迟主体 = 轮询周期(最长 5 分钟,可压缩到 ~1 分钟)+ 源站发布延迟(obs 后 0~10 分钟,不可控)。对手比 obs 时刻早 5~19 分钟行动,说明其信息源不是我们的管道能追的(机场实时/私有源),提速轮询只能缩小不追平。

### 6. 上一档盘口的典型可执行性

Warsaw 案例中上一档(21°C)成交活跃(价格 0.08~0.18、单笔 5~100 股),保险腿可执行性良好;其他城市未系统核查,一般同 ladder 相邻档流动性相近。

## 五、使用说明

- 复制第四节代码块全部内容到 GPT Pro(或等价高级模型);建议开新对话避免上下文污染。
- 拿回建议后先对照本文第二节"已证伪清单"过滤重复方向,再回到本仓库讨论落地(涉及 live 变更的走 `weather-strategy-deploy` 流程)。
- 本文档是快照,后续新证据(止血 forward、divergence 影子、tape 采集)出来后以 registry 为准。

## 六、2026-08-22 外部审阅 P0 落地状态

状态：`deployed 2026-08-23 / collector healthy / awaiting first real forward demand`。部署不改变 Core probability、selector、entry sizing、maker policy 或 live 授权。

- 首次正信号新增 append-only `decision_packets.jsonl`：稳定 packet identity，保存完整 trigger、last checkpoint/negative/informative quote-usable negative，以及 observation/book/forecast/snapshot 引用；缺字段显式 `unavailable`，写失败只告警、不改变下单选择。
- strategy snapshot 的 forecast hash/archive/source/model/model-init、book snapshot 与 exchange/request/receive/parse/archive clocks 原样穿透 state decision、pre-live score 和 packet。当前 snapshot 没有 `forecast_first_seen_utc` 时不伪造，标为 `requires_forecast_archive_hash_join`。
- 首次正信号新增公共 `polymarket_capture_demand_v1`：同 city/date 全部 YES outcome tokens，`P0`、WS、30 分钟、checkpoint `0/60/300/900/1800s`，单事件与共享 active budget 均为 24。复用唯一 `weather_market_books_ws` owner；full-ladder 作为 atomic group，预算不足时整组拒绝，不采任意前缀。既有 raw WS frame、subscription epoch、public `last_trade_price` tape、deterministic reconstructed book 与 REST/WS parity 继续作为数据真相。
- 专项测试为 `93 passed, 1 deselected`；deselect 是 dirty critical source 必须 fail closed 的 clean-deployment 测试，未削弱。最新 `snapshot_20260822_1241.json` 只读→`/tmp` replay 得到 24 decisions，24/24 有 forecast hash/archive/book snapshot；梯度为 11 档（22 rows）或 17 档（2 rows），全部低于 24-token contract；24/24 first-seen 仍需 archive-hash join。
- 2026-08-23 已按 git-first 合同部署并由 production controller 重启：Core=`24099162`，WS=`6d4676d0`；唯一 WS owner 使用 selector v7、Core shared-demand path 与 24-token atomic budget。启动回填 96 个历史 packet，因旧 score rows 缺 full-ladder tokens，96/96 写入显式 `capture_demand_full_ladder_tokens_missing` 告警而没有伪造 demand。新 snapshot 35/35 具备完整 ladder lineage（33×11档、2×17档）。
- 12 分钟生产验收窗内没有新 Core first-positive，因此真实 Core `capture_demands.jsonl`/atomic subscription 样本仍为 0；这表示 forward evidence 尚未形成，不表示 collector 故障。同期 WS raw 增长 1 file / 957,947 bytes，selector/health 正常；Core live orders 与 execution journal 分别保持 393/1077，新增 entry/order/fill/lifecycle 均为 0。

## 七、2026-08-22 P1 研究收口

状态：`P1 complete / mechanism inconclusive / no-live-change`。详见
[first-positive + ladder dynamics P1](2026-08-22-core-carry-first-positive-ladder-dynamics-p1-v1.md)。

- 固定本文 92 行 signal ledger，92/92 精确回连 trigger；61 条有可评分 prior，但 prior 距 trigger 最短 42.35 分钟、中位 59.27 分钟。因此严格 `quote_driven` 只有 4 条且 0 loss 只能算粗诊断，不能推翻 Warsaw/Amsterdam 的链上逐笔证据，也不能完成触发前卖压的因果归因。
- REST full ladder 双端 7 分钟 freshness 覆盖 31 条/12 target dates；loss−win `Δq_up=+0.0490`，target-date CI `[-0.1726,+0.3332]`。`q_up/q1/alpha1` 当前没有可用于 confirm/hedge 的稳定方向。
- 历史 WS 在 trigger 时 current token 覆盖 2/92、完整 YES ladder 0/92；P0 collector 已于 2026-08-23 启用，但验收窗没有真实 Core forward demand，故 clean frozen-forward 仍未形成。下一研究动作是等待并冻结新的 target-date slice，积累 sub-minute pre-trigger current-token tape 与 post-trigger full-ladder tape，再做 P2 policy replay；不增加 gate、模型头或 live threshold。
