# Core Carry 研究现状总结与外脑咨询提示词(2026-08-20)

> 目的:自包含的研究现状快照 + 可直接复制的高级模型咨询提示词。所有数字来自 2026-08-20 前的 canonical/raw/链上复核,证据细节见同日 [maker adverse-selection 全审计](2026-08-20-core-carry-maker-adverse-selection-review-v1.md) 与 registry。

## 一、策略机制(一分钟版)

Polymarket 城市每日最高温市场(`exact bracket`,如 "Amsterdam 最高温 = 21°C")。策略买 **current exact bracket 的 YES**:当当前温度所在档已锁定运行最大值时,赌它保持到日终(persistence)。

- **数据**:aviationweather METAR(30 分钟 cadence,obs→我们 ingest 延迟 3~15 分钟)、Open-Meteo ECMWF/GFS 小时级预报、Polymarket CLOB 盘口(REST 快照 5 分钟粒度)。
- **模型**:frozen logistic(Core v3),仅 5 特征:`market_logit`(市场中价 logit)、`decision_hour_local`(本地小时)、`dewpoint_depression_f`、`wind_speed_kt`、`obs_age_min`(+预报峰值时钟变体)。输出 p_hold(当前档保持概率)。
- **执行**:p − ask − fee > 0(first-positive)且 p≥0.80 → 10 股 taker + maker 5+5 股(staged 追价 / pullback 静态,双 sleeve A/B);maker 挂单到"下一份报文预期时刻 −90s"强制撤;报文 due 已过则不挂(blackout,fail closed)。
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

### KNMI 快源(仅 Amsterdam)

- 覆盖:仅阿姆斯特丹(7 个 loss 里 1 个);市场结算源是 Wunderground EHAM **METAR**,不是 KNMI(source-basis 差 0.1~0.5°C)。
- 模型质量:V8 frozen 显著输 market;V9 ΔBrier +0.010 CI 跨 0(dormant);`cross_survival` head AUC 0.945(0.7°C 跨档存活)是唯一显著优于基线的 head,但 live 零 forward。
- 10 分钟观测的真实价值定位:**持仓期预警**(Amsterdam 8/20 型入场后升温,KNMI 比 METAR 早 25~45 分钟可见),不是入场概率替换。

### 数据与部署现状

- 已部署止血:maker 挂价 <0.84 fail closed(shadow 继续记录);已识别但未部署:无。
- 数据缺口:token 级成交流(tape)未采集;挂单时盘口结构(下方支撑深度)未记录;KNMI-vs-METAR 分歧未量化;对手信息源未知(比 obs 快 5~19 分钟)。

## 三、当前卡点(开放问题)

1. **5% overshoot loss 不可预测**:94.5% 之后的所有特征方向已试尽(见上),单个 loss 抵 20~40 个 win。是继续攻预测,还是转向尾部管理(仓位/组合/对冲)?
2. **forecast-actual divergence 假设**(2.8x lift,n=8)如何高效验证:tiny live ~3 signal/天,统计功效永远不够,shadow/回放的正确实验设计是什么?
3. **模型/参数天花板**:5 特征 logistic 之后,下一档改进在哪——新特征(需新采集)、模型族(GBM/NN/生存模型)、分层多模型、还是 market-anchor 微调?在 base rate 94.5%、正样本 70 个的约束下,什么方法不会过拟合?
4. **执行层是否彻底关闭**:三层证伪后,小账户在对手信息优势(快 5~19 分钟)下的执行最优解是否就是纯 taker?
5. **数据采集优先级**:tape / 挂单结构 / KNMI 分歧 / first-seen transition 事件,先采哪个?

## 四、咨询提示词(直接复制以下全部内容)

```text
我在运营一个小规模的量化天气预测市场策略(Polymarket 城市每日最高温市场),需要你作为资深量化研究员帮我分析研究路线。请先读完背景和"已证伪清单"再回答——很重要:我已经用数据排除了大量常见建议,如果你建议的方向在已证伪清单里,请说明你的版本与已证伪版本的关键差异,否则不要重复建议。

【策略机制】
- 市场:Polymarket 每城市每日最高温 exact-bracket 二元市场(如 "华沙今天最高温 = 20°C" YES/NO),官方结算源为 Wunderground 机场 METAR 序列的日最高值。
- 策略(current exact persistence):当日温度运行最大值进入某档后,买该档 YES,赌温度不再升破/跌破该档直到日终。本质是 "P(当前档保持到日终)" 的定价。
- 数据:机场 METAR(30 分钟一条,我们看到数据平均延迟 3~15 分钟)、ECMWF/GFS 小时级预报(一天更新几次)、CLOB 盘口(5 分钟快照)。部分城市(仅阿姆斯特丹)有气象局 10 分钟级观测,但结算仍以 METAR 为准,两者有 0.1~0.5°C 系统差。
- 模型:冻结 logistic 回归,只有 5 个特征:市场中价 logit、本地小时、露点差、风速、观测年龄。输出 p_hold。
- 执行:当 p − 卖一价 − 手续费 > 0 且 p≥0.80 时触发,10 股 taker 立即成交 + 10 股 maker 限价(staged 追价/静态两个 sleeve);maker 挂到"下一份报文预期发布前 90 秒"强制撤单。
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
执行层:
6. maker 替代/部分替代 taker(三种方式全部证伪):(a) 触发时挂 maker:窗口内成交率只有 29~41%,期望低于 taker(打平需 88%);(b) 模型 p 一出就提前挂(不等价格触线):全量回测 EV -0.035/股 vs taker +0.026/股——挂价被当时卖一价锁死,且绕过价格触发线把胜率从 94.5% 稀释到 90.1%,价格触发线本身被证明是有效过滤器;(c) 延迟执行等价差收窄:信号触发后可成交买价系统性上行(5 分钟中位 +2~9 美分),等待成本远大于价差。
7. maker 的反向选择已定量:成交的 maker 单胜率 87~90% vs 未成交的 95.6~100%;全部亏损 maker 成交挂价在 0.80~0.84;链上核实对手在数据发布前 5~19 分钟主动扫掉我们的买单(我们的数据管道延迟 3~15 分钟,信息上系统性落后)。
8. 提前撤单(各种 buffer):对手提前量分布 5~19 分钟太宽,固定 buffer 没有无损设置点(提前 8 分钟只覆盖 1/5 的历史亏损形态;提前 15 分钟净效果归零;提前 20 分钟砍掉 80% 的正确成交)。
9. 追价/爬价机制:收益上界(6 天完美情形)约 +$1.6,对比单次亏损 -$8~17,不对称。

【唯一活跃的正面假设】
"预报峰值已过 ∧ 实际观测再创新高"(forecast-actual divergence):8 个信号样本里亏损率 25%(基准 9%,lift 2.8x),恰好覆盖最近两个最大亏损。但 n=8、其中 2 亏损,CI 必然跨 0,只是假设生成。

【我的约束】
- 微型规模,~3 个信号/天,统计功效天然不足;不能大规模加仓做实验。
- 数据管道可以增加采集(成交流/盘口结构/更快观测),但不知道哪个最值。
- 市场流动性有限(天气 niche 市场,单档深度几十股)。
- 对手中有明显的信息优势者(比官方数据发布早 5~19 分钟行动,可能用机场实时源)。

【我的问题(按重要性排序)】
1. 概率层:在 base rate 94.5%、约 70 个正样本/25 天的约束下,预测剩余 5% upward-overshoot 亏损的最优路径是什么?请具体评估:(a) 还有哪些我们没试过的特征族(预报路径分歧、跨市场信息、太阳辐射/云量、气候学先验等)在 0.5~1pp 的概率增量尺度上值得试;(b) 换模型族(GBM/生存分析/贝叶斯层次)在 n=79 下是真提升还是过拟合陷阱;(c) 如果预测不可行,尾部管理(对冲买上一档 NO、动态仓位、组合层止损)在我们的仓位规模下是否更实际。
2. forecast-actual divergence 假设:设计一个能在每天 ~3 个信号下、最多 4~8 周内给出可信结论的验证实验(shadow 采集什么、预注册什么决策规则、多少样本、用什么统计检验处理 n<30 的稀有事件)。
3. 校准:整体偏乐观 4~6pp 但样本是价格触发筛选后的子集。这种选择效应下的校准修正应该怎么做?值不值得修(我们的入场阈值本身就是 p-价差>0,校准偏移直接影响阈值)?
4. 执行层:在"对手信息快 5~19 分钟、我们 30 分钟数据 cadence"的结构下,微型账户的执行是否应该彻底放弃 maker、接受 taker 成本?有没有我们没有想到的第三条路(例如只在特定盘口结构下被动、跨档做市、或用上一档 NO 的买入替代部分 YES 敞口)?
5. 数据投资优先级:token 级成交流(tape)、挂单时盘口结构快照、阿姆斯特丹 10 分钟源与结算源的分歧配对、逐 checkpoint 评分历史——按"每 MB 数据的期望研究价值"排序,并说明每项能解锁什么分析。
6. 盲区检查:基于以上全部信息,你认为我们最可能忽略的一个系统性问题是什么?

【输出要求】
- 每个问题给具体可执行的方案(公式/伪代码/实验设计),不要泛泛建议。
- 明确区分"立即值得做"、"先采数据再决定"、"不建议"。
- 如果某个方向你认为在我们的样本量和市场规模下没有前途,直接说。
```

## 五、使用说明

- 复制第四节代码块全部内容到 GPT Pro(或等价高级模型);建议开新对话避免上下文污染。
- 拿回建议后先对照本文第二节"已证伪清单"过滤重复方向,再回到本仓库讨论落地(涉及 live 变更的走 `weather-strategy-deploy` 流程)。
- 本文档是快照,后续新证据(止血 forward、divergence 影子、tape 采集)出来后以 registry 为准。
