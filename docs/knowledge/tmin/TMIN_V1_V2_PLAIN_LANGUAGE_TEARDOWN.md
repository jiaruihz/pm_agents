---
status: current-stage-knowledge
source_version: 2026-08-29
scope: tmin-model-layer
live_authorization: false
supersedes: null
version_boundary: "Any later forward result or model revision must create a new version."
review_sources:
  - ../../../reviews/tmin_model_layer_v2_v3_research_v1/GPT_PRO_REVIEW_PACKET.md
  - missing_from_supplied_package:TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md
  - missing_from_supplied_package:TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json
source_archive_entry: TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN_20260829.md
---

# Tmin V1 / V2 白话拆解：哪里科学、哪里不科学、下一步该继承什么

**版本日期：2026-08-29**  
**定位：** 项目知识文档，不是 live 授权。  
**一句话结论：** 沿着 V1 的科学核心继续完善，方向是正确的；但正确做法不是继续在旧 V1 上调参数，而是保留“市场为底座 + 天气 residual + 正确时段 routing”，同时重做目标、数据基础和物理模型。

---

# 一、先回答最核心的问题

是的，我们现在已经分清了 V1 哪些地方科学、哪些地方不科学，因此在它的科学核心上继续完善，是当前最合理的方向。

但“继续完善 V1”要准确理解为：

```text
继承 V1 的好问题定义和好架构
        +
重建 V1 薄弱的数据与建模层
```

而不是：

```text
继续在原来的24个训练日期和旧feature上调alpha、加变量、挑窗口
```

前者是 V2.1；后者会继续扩大过拟合和研究污染。

---

# 二、V1 到底想做什么？

假设市场正在交易：

> 东京今天当前最低温档最终是否成为结算赢家？

市场说：

```text
p_market = 95%
```

V1 不会完全无视市场、从零给一个答案。它先训练一个天气 physical model，判断从当前时刻到日终是否还会继续降温，然后计算一个 physical innovation，再用 alpha 对市场做修正：

```text
logit(p_final)
= logit(p_market)
+ alpha × physical_innovation
```

ELI5：

> 市场是主驾驶，天气模型只负责提醒：这里可能太乐观，或者这里可能太悲观。

V1 旧 incumbent 使用：

```text
alpha=.50
所有窗口都启用天气修正
```

后来的 frozen routed challenger 使用：

```text
alpha=.10
只在 morning_cooling 和 post_sunrise_provisional_low 启用
其他窗口完全退回市场
```

---

# 三、V1 科学的地方

## 1. 它问对了真正有价值的问题

不是单纯预测天气，而是预测：

> 天气信息是否能解释 market error？

如果天气模型和市场都说 95%，没有 Alpha。只有天气模型能稳定识别市场哪里偏高或偏低，才有价值。

---

## 2. 它把市场当成 prior，而不是敌人

Polymarket 价格已经融合了天气预报、实时观测、参与者经验和市场信息。一个小样本天气模型几乎不可能从零全面击败市场。

V1 的 market-offset 架构要求模型只学习“市场尚未吸收的部分”，这是比直接天气分类器更现实的目标。

---

## 3. 它开始意识到信号具有 regime specificity

天气 residual 并不是全天通用。

当前数据表明：

```text
在两个 active windows 内：
V1 alpha=.50 的点估计明显优于市场

在 active windows 之外：
同一模型明显伤害市场概率
```

这符合物理直觉：早晨是否继续降温是一个真正未决的问题；到了明显升温的白天或其他阶段，同一套 residual 可能不再表达同一机制。

---

## 4. 它最终回到了 probability-first

当前审阅已经把四层分开：

```text
P0：概率考试
P1：active-window 概率考试
E0：执行条件
T0：selected trades
```

这使我们可以先判断模型是否比市场准确，而不是拿 19/19 或一段 PnL 反推模型有 Alpha。

---

# 四、V1 不科学或不够科学的地方

## 1. 模型学的题不是最终结算题

V1 physical model 训练的 target 是 observation-cache proxy：

> 当前最低温 proxy 到日终是否不再下降？

真正交易的问题是：

> 当前 exact bracket 是否按交易所 settlement source 和规则结算为 YES？

两者之间存在：

- source basis；
- rounding/lattice；
- local-day boundary；
- revision；
- missing observation；
- cache latency。

因此 V1 physical probability 不能直接被当成严格校准的 settlement probability。

---

## 2. 独立天气日期太少，feature 相对过多

V1 final refit 使用：

```text
534 checkpoint rows
24 target dates
约35个 transformed coefficients
```

534 行看起来不少，但同一天的 rows 高度相关。真正独立的信息更接近 24 条天气路径，而不是 534 次独立试验。

因此 V1 可以作为 regularized baseline，却不足以证明每个 coefficient 稳定表达气象规律。

---

## 3. 存在重复或共线 feature

部分 feature 只是常数变换或确定性关系，标准化后可能完全相同或相反。

这会带来两个问题：

1. 单个 coefficient 没有可靠可解释性；
2. 重复方向会改变实际 L2 正则化强度。

V1 frozen artifact 不应追溯修改，但下一代模型必须 algebraic deduplication。

---

## 4. 它把天然的 survival 问题压成普通分类

真正的问题是：

> 从 checkpoint 到当地日终，下一次进入更冷档的风险如何随时间变化？

刚创最低温、还剩 12 小时，与已经反弹 3°C、只剩 2 小时，风险结构完全不同。

V1 靠 `time since low`、`hours remaining` 等 feature 让普通 logistic 自己学习这条规律，能够近似，但不是最自然的模型表达。V3 会直接建 hazard。

---

## 5. 旧 incumbent 最大的问题是全天乱用 residual

修复后的数据表明：

```text
P1 active windows 内：
ΔLogLoss = -0.026328
ΔBrier   = -0.001392

P1 之外：
ΔLogLoss = +0.020430
ΔBrier   = +0.010017
```

所以旧 V1 全局失败的最合理解释不是“天气模型完全没用”，而是：

> 一个只在特定降温 regime 有信息的 signal，被错误地应用到所有时段。

---

## 6. Alpha 和窗口都经过开发期选择

`.10 / .25 / .50` 以及 active windows 都已经看过历史结果。

所以历史点估计可以帮助提出假设，但不能再被包装成未见数据上的确认性证据。必须另开 frozen-forward 边界。

---

# 五、V1 当前证据到底说明了什么？

修复后的主要概率分母：

```text
P0 = 168 rows / 15 target dates
P1 = 52 rows / 14 target dates
P0 negative rows = 12
P1 negative rows = 10
```

在 P1 的 52 行中：

```text
10/10 negative rows 的 physical innovation < 0
33/42 positive rows 的 physical innovation > 0
```

平均 residual：

```text
后来失败的 rows：-1.3226 logits
后来成功的 rows：+1.3121 logits
```

而 V1 physical model 的 final training dates 是 2026-07-18 至 2026-08-11；本轮评价 dates 是 2026-08-12 至 2026-08-26，没有同日训练重叠。

这是一条相当有价值的开发期信号：

> 在两个正确窗口内，天气模型可能确实提前识别了市场高概率判断中的失败风险。

但它仍不是正式证明，因为：

- 只有 14 个 target dates；
- 只有 10 个 negative rows；
- active windows 和 alpha 已经经过开发观察；
- 置信区间仍跨 0；
- 城市集中度较高。

因此准确状态是：

```text
PROMISING, NOT PROVEN
值得冻结观察，但不能 live
```

---

# 六、为什么 19/19 和“一次亏损吃掉很多收益”不矛盾？

当前交易通常发生在高价格区间。假设 95¢ 买入：

```text
赢：赚约5¢
输：亏约95¢
```

这就是高胜率、负偏度策略。它本身并不不合理，保险、信用 carry 和卖波动策略都可能如此。

真正的问题不是“一笔为什么亏很多”，而是：

> 真实胜率是否真的高于含费用后的 break-even probability？

旧 19 笔平均成本约 94.6¢。即使真实概率就等于市场粗略概率，19 次全胜的概率量级仍约为：

```text
0.946^19 ≈ 35%
```

因此 19/19 并不是罕见到足以证明模型 Alpha 的事件。高概率策略必须依赖长期 calibration、足够多独立日期和真实 loss tail，而不是只看连胜。

---

# 七、V2 做了什么？

当前 V2 试图修复 V1 的一些问题：

1. 直接采用 settlement-native label；
2. 保持 market offset；
3. 把 feature 清理到 8 个低维机制变量；
4. 使用 prior-date chronological OOF；
5. 数据不足时 fail closed 到 market；
6. 简单模型未过关后，没有上复杂模型追结果。

它的基本形式仍是：

```text
logit(p_v2)
= logit(p_market) + f(weather features)
```

这套实验纪律比 V1 的原始训练更规范。

---

# 八、为什么当前 V2 略输市场，却不能证伪天气 Alpha？

V2 的 headline：

```text
ΔLogLoss = +0.000475
ΔBrier   = +0.000129
```

正数表示略差于市场。但它并没有形成一次有统计力度的天气 residual 测试。

## 1. 独立样本和失败案例太少

```text
15 target dates
168 rows
12 negative checkpoint rows
negative 只出现在6个日期
```

---

## 2. 只有 96/168 行真正使用了拟合后的 V2

前 72 行因历史训练日期或负例不足，直接 fail closed：

```text
p_v2 = p_market
```

所以并非 168 行都检验了新模型。

---

## 3. V2 几乎没有修改市场

```text
全P0平均绝对概率移动：0.000369
= 0.0369个百分点
```

市场 95.00%，V2 平均大致只改到 95.04%。它更接近“市场本身”而不是有力度的 challenger。

---

## 4. 强 L2 可能把 residual 压没

极少负例 + 强正则 + 小样本同时存在，无法区分：

```text
确实没有天气信号
样本不足
正则过强
非活跃窗口稀释信号
```

---

## 5. V2 删除了当前最有希望的 routing 结构

V1 的证据指向 active-window-specific residual，但 V2 改成全局 residual head，没有保留 window routing。

所以它不是沿 V1 最有希望的机制做科学升级，而是把这一机制删除后，在更小的 settlement sample 上重新学习。

因此当前 V2 的正确分类是：

```text
UNDERPOWERED_NEAR_IDENTITY_NULL
```

白话：

> 这次模型几乎等于市场，考试规模也太小；它没赢，但也没有能力证明天气信号不存在。

---

# 九、V1、V2 当前的准确定位

| 模型 | 好的地方 | 主要问题 | 当前状态 |
|---|---|---|---|
| V1 all-window α=.50 | market prior + physical residual | 全天乱用、proxy target、样本小 | 停止作为主模型 |
| V1 routed α=.10 | 保留正确窗口、历史点估计改善 | alpha 可能太弱、仍未经独立 forward 证明 | 保持原 frozen forward |
| V1 routed α=.25 | 历史点估计优于 .10 | 已看过历史结果 | 新开 diagnostic forward |
| V1 routed α=.50 | 历史点估计最好 | CI 跨0、已看过历史结果 | 新开 diagnostic forward |
| 当前 V2 | settlement-native、OOF 规范 | 近似市场、无 routing、样本不足 | 不冻结，不作证伪 |
| V2.1 | 大天气历史 + 低维市场适配 | 尚未实现 | 下一阶段主路线 |
| V3 | 直接建继续降温的时间风险 | 事件时间真值尚未建 | 数据 gate 后实现 |

---

# 十、为什么继续沿 V1 完善是正确方向？

因为 V1 已经给出了一个比“直接预测 Tmin”更有希望的研究假设：

```text
市场是强 baseline
天气信息只负责纠正市场误差
该纠偏只在特定冷却 regime 有效
```

当前数据并没有否定这个假设，反而发现：

- active windows 内 residual 方向明显更合理；
- active windows 外使用 residual 明显有害；
- 普通全局 V2 没有保留 routing，因此没有真正挑战这个假设。

所以应当保留 V1 的科学核心，重建三块不科学的部分：

```text
proxy target → canonical settlement-source truth
小市场样本学全部天气 → 大天气历史学 foundation
静态全局分类 → routed adaptor + survival challenger
```

---

# 十一、什么不叫“继续完善 V1”？

以下行为不属于科学完善：

- 在已看过结果的日期上继续挑 alpha；
- 增加更多窗口再挑赢家；
- 因历史 PnL 好看而改 selector；
- 在同一 forward 上边看结果边改 feature；
- 继续用 15～24 个日期拟合几十个参数；
- 简单模型不赢就上 XGBoost/深度学习救结果；
- 把 Maker 更好的价格当作模型 Alpha。

这些会让研究看起来越来越好，却越来越无法相信。

---

# 十二、正确的后续结构

## V2.1：V1 科学核心的重做版

```text
大量官方天气历史
→ 学 physical no-further probability

大量PIT预报历史
→ 学 forecast-error / next-rung crossing probability

少量Polymarket settlement历史
→ 只学1～3个 residual adaptor 参数

最终：
market + active-window × physical/forecast residual
```

它解决 V1 的 target、样本和 feature 问题，同时保留 market prior 与 routing。

---

## V3：真正结构不同的 challenger

```text
每个未来1小时
估计第一次跌入下一冷档的 hazard
        ↓
得到直到日终不再降温的 survival probability
        ↓
以 market residual 形式修正市场
```

它直接表达 no-further-cooling 的时间过程。

---

# 十三、现阶段模型与执行的边界

当前正确顺序：

```text
1. 证明天气 probability residual
2. 冻结 trade selector
3. 研究 Taker 可执行收益
4. 研究 Maker fill / adverse selection
5. 最后才考虑 tiny live
```

Maker 能改善价格，但不能证明天气预测正确。若没有 probability Alpha，Maker 只是换一种方式承担相同方向风险。

---

# 十四、最终结论

对 V1 的最终评价：

```text
核心问题定义：科学
market-offset 架构：科学
active-window routing：很有希望
原 physical target：不够严格
原训练样本与feature比例：偏弱
全窗口应用：错误
当前证据：值得继续，但尚未证明
```

对当前 V2 的最终评价：

```text
研究纪律更规范
settlement target 更科学
但实现几乎退回市场
样本不足且删除了关键routing
因此不能晋级，也不能证伪天气Alpha
```

主建议：

> **保留 V1 的科学骨架，以 V2.1 重建物理与预报层；同时建设 V3 event-time/hazard challenger。现有 V1 α=.10 继续 frozen forward，α=.25/.50 另开只读诊断臂。模型层证明以前，继续延后 Maker/Taker。**

---

## 当前置信度

- V1 的 `market + weather residual` 总体思想正确：约 85%；
- active-window routing 是关键结构：约 90%；
- active-window residual 最终能在独立 forward 稳定胜过市场：约 60%～65%；
- 当前 V2 能有效证伪天气 Alpha：低于 20%；
- V2.1 比当前 V2 更合理：约 90%；
- 当前已适合进入 Maker/Taker：低于 10%。

---

## 证据来源与版本边界

本拆解对应：

- `TMIN_NO_FURTHER_FORENSICS_EXTERNAL_REVIEW_20260828.md`
- `TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md`
- `TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json`

本文是当前阶段的项目知识快照。新 forward 结果成熟后应创建新版本并说明差异，不得静默改写本版结论。
