# Tmax Distribution Edge 策略：第一性原理审阅 v1

> 审阅对象：`WEATHER_TMAX_DISTRIBUTION_EDGE_STRATEGY.md` + `research-synthesis-v1` + P5/P6，
> 结合 P0–P5 审阅 v1（`2026-07-03-tmax-distribution-p0-p5-review-v1.md`）的复算数字。
> 立场：不看单个 bad case，不建议 hard gate；只审论点、表达映射、证据效度和下一步排序。

## 0. 先说这轮修正修对了什么

- **表达层统一**：从 "regime → 固定档位" 换成 "分布 → P(win)−ask → 最高 edge 表达"，这是我在第一性
  原理纲领里主张的架构，现在是实现而不是口号。regime 降级为特征有 P3 消融支撑。
- **selected + blocked 双记录**：这是整个 P 链里最有远见的一条工程决定。它让"没触发是不是更好"
  第一次可验证，也让未来 threshold 变更可以离线重放。
- **诚实等级**：`inconclusive_positive_signal / no live` 的自我判级与证据相称。

修正没有覆盖的部分构成本审阅的主体：**below/basis 缺口现在直接污染表达选择公式本身**（§2），
以及 mid 基线与可交易价格之间的 spread 包络还没测（§1）。

---

## 1. Alpha 论点是否成立？——在"概率技能"层成立，在"可交易 EV"层还差一个未测的包络

第一性原理重述论点：市场定价的是部分观测路径运行最大值的离散分布；报价者的信息集与我们相同
（公开预报 + 公开观测），差别只能来自**条件化技能**。所以论点成立的判据不是"我们懂天气"，而是：
(a) 市场信息含量接近公开上限但校准有系统偏差；(b) 偏差可被状态变量条件化；(c) 条件化收益 > 交易成本。

现有证据对 (a)(b) 的支持是实在的：

- `weather_physical`（完全不看盘口）forward logloss 只输 market 0.01–0.03 —— 市场信息含量确实
  接近公开上限，几乎没有私有信息残差。
- `market_recal` 单独拿 −0.019 —— 市场自身校准可被单参数温度缩放改进，偏差存在。
- fusion 增量集中在 P(current holds)（逐桶 −0.097），且 fixed/expanding、LOO 消融方向一致，
  不是 city 记忆 —— 偏差可条件化，位置与锚切换假说的预测吻合。

**但 (c) 未被现有证据覆盖，且有一个第一性原理上必须做的检验没做：spread 包络。**
`market_local_norm` 是 mid 的归一化。市场用 bid/ask 报的是一个区间，不是一个点——做市商不需要
mid 是对的，只需要真概率落在包络内。**赢 mid 4–8% logloss 可能完全被半 spread 吸收。**
P2 用真实 ask 执行部分回应了这点，但 P2 的 p_win 来自为打败 mid 而训练的模型，EV 的正负最终
仍以"结算标签是否可信"为前提（见 §3）。在 ask-side 基线（把 ask 归一化成分布再打分）被打败之前，
"概率技能 ⇒ 可交易 EV" 的推理链缺一环。这是 E-A 实验（§6），一天工作量。

判定：**论点第一性原理成立、机制证据支持，量级为 logloss 4–8% / 名义 EV 8–12%（未扣 basis 与
depth），当前应表述为"市场校准套利"而非"天气预测优势"。**

## 2. P(bucket) → 表达的映射是否合理？——框架对，公式里有一个比 edge 阈值更大的系统误差

映射框架（每 state 算四个表达的 P(win)−ask、取最大、不过阈值记 blocked）方向正确。四个问题，
按第一性原理的重要度排：

**(1) 结果空间缺一格，且缺口直接进了 p_win 公式。**
四桶假设 final ≥ current（METAR 网格下成立），但结算空间有第五格 below：复算 7.6% 的 state 行
结算低于 METAR 当前档，集中在全部 °F 城市。表达的真实胜负规则是：below 发生时 current YES 输、
current NO / d1 NO / d2 NO 全赢。于是在 °F 城市：**current YES 的 p_win 被高估最多 ~7pp、
所有 NO 被低估 ~7pp —— 这个系统误差比 edge 阈值 0.02 大三倍**，直接扭曲 YES vs NO 的排序。
这不再是评估口径问题，是选表达公式的偏差项。修复见 E-B：不需要等新数据，1003 行删失行本身
就是训练/校正材料。

**(2) 表达集不完整，模型的 d1/d2 观点只能间接表达。**
模型若认为 P(d1) 被市场低估，最直接的表达是 d1 YES；现在只能买 current NO（捆绑 d1+d2+tail），
把一个精确观点稀释成一个模糊观点。atlas 只存了 current YES / 各档 NO / lottery YES 的报价，
没有 d1/d2 YES ask —— P7 materializer 加两列报价字段就能让表达集完备，先记录不交易，零成本。

**(3) max-edge 选择有 winner's curse，且数据已经显形。**
四个相关表达的估计概率取最大，被选中者的 edge 条件性高估——这是选择噪声的期望性质，不是 bug。
证据：verified 的 edge 0.02–0.05 边际带 EV **−6.9%**（CI [−20.3, +4.6]），edge≥0.05 才为正；
extension 里高 edge 带也变薄。**正解不是把阈值调到 0.05（那是又一次 in-sample 选择），而是把
选择标准从点估 edge 换成不确定度惩罚后的 edge**（p_win 向 market 收缩或减一个与 p(1−p) 成比例
的项），让边际带自然出清。见 E-D。

**(4) 风险聚合的单位错了。**
dedupe 是每 city-date-hour 一个表达，但同一 city-day 的多个小时事件共享同一结算结果，
且不同小时可能选出方向相反的表达。PnL 表按行独立统计低估了日内相关性风险。shadow 账本应
补一个 city-day 净敞口视图（不改选择逻辑，只改风险报告）。

## 3. positive signal 的效度风险清单

按"能翻转结论的概率 × 影响"排序：

1. **删失/标签双向偏差（未解决，最高优先）**：verified 删掉 1003 行 basis 行单边抬高 current YES
   （+17.7% 是上界）；extension 的 observed 标签因 METAR 系统性高于结算而偏向高桶，单边压低
   current YES（−40.3% 是下界）。真值不明。注意 `settlement_outcomes` 已有 6/27–28 部分官方结算
   （max_date=2026-06-28），**extension 的一部分现在就可以转 verified 并直接测量标签分歧率**（E-C）。
2. **spread 包络未测**（§1）：技能可能在 bid/ask 区间内。
3. **ask replay 的成交假设**：整量按快照 ask 成交、无 depth/size/滑点（文档已声明）。快照 anchor
   小时的 ask 新鲜度未审计——若快照滞后，replay 会吃到已消失的价格。P7 记录 ask size + 快照时延
   即可量化。
4. **跨 scope 赢家漂移**：verified 最优 clean、extension 最优 city_source，两个 config 各拿一个
   scope 的第一。当前并行三 config 是合理的过渡，但 promotion gate 评审时只能预注册一个 primary
   （建议 clean_edge02），其余是 sensitivity——否则 10 个 forward dates 攒齐那天又要面对选择偏差。
5. **单季节样本**：39 天全在 5–6 月。regime/边界特征的分布会随季节漂移；solar-clock 特征是唯一
   防御，7–8 月 forward 本身就是季节外推压力测试，无需额外动作但要在 promotion 时明示。
6. **`loo_no_city_source` ≡ `mkt_regime`**（同一模型两个名字）已在上轮指出，synthesis 已合并
   表述，保持即可。
7. 一个反向 sanity 其实支持你们：dev_cv 里 `market_local_norm` 自身只选出 50 行、verified ROI
   −8.5% —— 局部盘口自不一致不是免费钱，说明模型 edge 不是在捡报价噪声。这条值得写进 synthesis。

## 4. 真机制 vs 样本噪声

**真机制（物理先验 + 跨口径稳定）：**
- P(current holds) 的校准优势——锚切换盲区 + 度内小数位置盲区 + hazard 时间衰减三者的交集，
  这是整条线的 alpha 本体。
- boundary/fractional position 特征族（P3 fixed forward 去掉变差）。
- regime 作为特征的增量（LOO +0.0073）。
- market 自身可被温度缩放改进的校准偏差。
- d2/高 ask 表达 EV ≈ 0 —— 这不是噪声而是结构：ask 0.8–0.95 处半 spread 就吞掉 0.02 级 edge，
  说明 edge 的可采集性随 ask 上升而消失，与 §1 的包络论一致。

**疑似噪声/伪影（不应写进机制叙事）：**
- city/source 增量（P3 已判：去掉更好）。
- 高 edge (≥0.10) 的超额——dev-CV 与 verified 强、extension 薄，winner's curse 的典型形状，
  维持 pressure-test 定位。
- extension 的 current YES 崩溃——首要假设是标签向上偏置的伪影而非机制失效（E-C 直接可判）。
- 6/28 extension 坏日——当天只有 30 states/12 城，是采集断流的伪影，不是天气。

## 5. 优先级排序（问题 5 的直接回答)

1. **current-day materializer（P7）+ 采集恢复** ——绝对第一。当前 shadow 消费的是 P5 backfill，
   不产生任何新 forward 证据；promotion gate 要 10 个新 settled dates + 80 events/config，
   发生器不存在则 gate 永远不可能过。orderbook 快照从 48/天衰减到 1/天是同一问题的一部分。
   materializer 顺带补三个字段：d1/d2 YES ask、ask size、快照时延。
2. **settlement 补齐 + below/basis 修正（E-B/E-C）** ——解决唯一能翻转结论的未知数
   （current YES 真实符号），且 6/27–28 官方结算已在库，今天就能做一半。
3. **calibration 层（E-A 包络 + E-D 不确定度惩罚）** ——纯离线、模型侧，直接改善表达选择质量。
4. **entry timing** ——排在 materializer 之后：有了逐小时 selected/blocked 账本，小时 × 表达的
   EV 剖面是免费副产品（E-E），不需要单独立项。
5. **city/source bias** ——降级：P3 已回答（排除出 primary），只在 basis 修正后作为残差诊断回看。
6. **新 alpha 模式** ——不做。这条线第一次同时具备论点、机制、账本和晋级门槛，最大风险是
   在证据攒齐前分心。

## 6. 可落地实验

**E-A — Spread 包络评分（1 天，纯离线）**
用 atlas 现有 bid/ask 列构造 `market_ask_norm` 和 `market_bid_norm` 两个包络基线分布，与
`market_local_norm`(mid) 一起进 P1/P3 评分表，分母不变（6519 行 + forward）。
判据（预注册）：模型 logloss < ask-side 基线才能声称"技能超出 spread"；若只赢 mid 不赢 ask-side，
alpha 表述降级为"做市商区间内的校准差"，EV 预期相应下调。同时输出每桶平均 spread，给 edge
阈值一个物理参照（阈值应 ≳ 半 spread）。

**E-B — below 桶 + basis 修正重放（2–3 天，不需要新数据）**
把 1003 行删失行以 `actual_bucket=below` 回灌评分分母；表达胜负规则改五桶（below: current YES
输、全部 NO 赢）；市场侧 below 质量并入残差项。分 °F/°C 重跑 P1 评分与 P5 EV。
输出：per-city P(below) 表；current YES / current NO 修正前后 EV 对照。
判据（预注册）：修正后 clean_edge02 的 dedupe EV CI 下界仍 >0 才维持 positive signal 等级；
current YES 若转负，表达集权重自然重排（这是公式修正，不是 gate）。

**E-C — extension 标签分歧测量（半天，数据已在库）**
`settlement_outcomes`（max_date 6/28）与 observed_max_derived 标签逐行 join 6/27–28，
输出：标签分歧率（预期 ≈ per-city basis 率）、分歧行的表达 PnL 翻转表、
extension current YES −40.3% 在官方标签下的修正值。
这直接检验 P9 计划里"current YES 为什么在 extension 差"的首要假设，先于任何模型归因。

**E-D — 不确定度惩罚的表达选择（2 天，离线重放）**
选择标准从 `argmax(p_win − ask)` 换成 `argmax(p̃_win − ask)`，p̃ 为向 market 概率收缩
（λ 在 dev-CV 里选，冻结后不动）。用 P6 的 selected+blocked 全量账本离线重放——这正是双记录
架构的第一个回报：不用重跑模型就能重放任何选择政策。
判据（预注册）：0.02–0.05 边际带 EV 从 −6.9% 收敛向 0，且 edge≥0.05 带 EV 不显著恶化。

**E-E — 小时 × 表达 EV 剖面 + hazard 形状检验（P7 上线后的免费副产品）**
对每个 city-day 画模型 P(current) 随 decision hour 的轨迹，与物理预期形状（太阳峰前缓升、
峰后加速趋 1）对照；输出小时 × 表达的 EV 矩阵。
判据：若模型 hold 概率的时间形状违反 hazard 单调性（峰后仍大幅下调 hold），说明时间衰减
没学对，优先补 solar-clock 特征而不是加数据。同时回答"每天该在哪个小时决策、一天几次"——
用机制回答 entry timing，而不是用 ROI 搜索。

---

## 总评

这条线现在的形态，第一次同时满足：可证伪的论点（校准套利）、机制上可解释的增量来源
（hold 概率 + 边界位置）、能重放任何政策的账本（selected+blocked）、和写死的晋级门槛。
架构债已基本还清，剩下的是**证据债**：一个没接上的 forward 发生器、一个没测的 spread 包络、
一个没修的 below 缺口。三者都不是研究难题，是工程排期。在它们完成前，最理性的姿态就是
文档里已经写的那句——zero-notional shadow，攒天数，别分心。
