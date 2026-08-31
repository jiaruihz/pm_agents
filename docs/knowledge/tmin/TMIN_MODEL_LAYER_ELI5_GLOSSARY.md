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
source_archive_entry: TMIN_MODEL_LAYER_ELI5_GLOSSARY_20260829.md
---

# Tmin 模型层 ELI5 名词解释

**版本日期：2026-08-29**  
**用途：** 给项目成员、非量化背景参与者和后续 AI reviewer 统一解释 Tmin 模型研究中的术语。  
**适用范围：** 只解释 probability/model layer；交易执行、Maker/Taker 仅作必要衔接。

---

## 0. 先记住整条链路

假设东京今天截至早上 7 点的最低温是 18°C，Polymarket 正在交易：

> “东京今天最终最低温是不是 18°C？”

项目里的完整研究链路是：

```text
当时已经知道的天气和市场信息
        ↓
市场给出 p_market
        ↓
天气模型判断市场是否偏高或偏低
        ↓
得到修正后的概率 p_model
        ↓
先考察概率是否比市场更准
        ↓
有概率 Alpha 后，才研究是否值得交易
        ↓
最后才比较 Taker / Maker / 撤单 / 实际成交
```

最重要的纪律是：

> **“模型脑子准不准”“当时能不能成交”“策略是否决定交易”“最后赚没赚钱”是四个不同问题，不能混在一起。**

---

# 一、数据与预测对象

## 1. Target date：目标日期

指市场最终结算对应的**当地日期**。

例如：

```text
Tokyo, 2026-08-20
```

即使 UTC 已经跨日，仍然按东京当地民用日判断当天最低温。

---

## 2. City-date：城市日

一座城市的一天，例如：

```text
Tokyo × 2026-08-20
Seoul × 2026-08-20
```

一个 city-date 内可能有很多次模型判断，但这些判断共享同一个天气过程，因此不能把它们当成完全独立的样本。

---

## 3. Checkpoint：判断时刻

项目预先规定的一个决策时间点。模型在这个时刻只使用当时已经公开、已经可取得的信息进行判断。

例如：

```text
东京当地 07:00 checkpoint
```

---

## 4. Row：一行预测记录

一行记录就是模型在某个 checkpoint 做的一次完整判断：

| 城市 | 日期 | 时间 | 当前最低档 | 市场概率 | 模型概率 | 最终结果 |
|---|---|---|---:|---:|---:|---:|
| Tokyo | 8月20日 | 07:00 | 18°C | 95% | 97% | YES |

它通常还包含所有 feature、来源时间、模型版本、盘口可用性和最终 label。

**Row-level probability audit** 就是把每一行预测摊开检查，而不是只看最终汇总收益。

---

## 5. Current rung / exact bracket：当前温度档

天气市场通常按离散温度档结算，例如：

```text
17°C
18°C
19°C
```

当前 running minimum 落在哪一档，就称为当前 rung 或 current bracket。

---

## 6. Next-colder rung：下一冷档

比当前最低温再低一档。

例如当前最低温档是 18°C，则 next-colder rung 通常是 17°C 档；具体必须遵守结算源的单位、取整和 native lattice 规则。

---

## 7. Label：答案标签

模型考试的最终答案，通常写成 0 或 1。

对于“当前最低温档最终是否胜出”：

```text
label = 1：之后没有跌入下一冷档，当前档最终 YES
label = 0：之后继续降温，当前档最终失败
```

---

## 8. Positive row / negative row

```text
positive row：label = 1
negative row：label = 0
```

这里的 negative 不是“坏数据”，而是指后来真的继续降温、当前档失败的案例。对于高概率策略，negative rows 很少，却往往决定长期盈亏。

---

## 9. Settlement-native target：结算原生目标

直接用交易所最终采用的结算档作为训练或评估答案。

它回答的是：

> 当前 exact bracket 最后是否按交易所规则结算为 YES？

这是最贴近真实交易问题的 label。

---

## 10. Proxy target：代理目标

用一个相近但不完全相同的答案代替真实结算答案。

V1 physical model 学的是：

> observation cache 里的最低温 proxy，从现在到日终是否不再降低？

它和交易所 settlement 高度相关，但可能因为数据源、时间边界、取整、修订或缺失而不完全一致。

---

## 11. Event-time truth：事件时间真值

不仅知道“后来有没有继续降温”，还知道：

> checkpoint 以后，第一次进入下一冷档是在什么时候？

V3 survival/hazard model 需要这个标签。

---

# 二、概率模型相关名词

## 12. `p_market`：市场概率

同一 PIT 时刻，市场对最终结果给出的概率判断。

ELI5：

> 市场里所有参与者综合后的当前答案。

它不是一定正确，但通常已经是非常强的 baseline。

---

## 13. `p_physical`：物理天气概率

仅根据天气观测、预报、时间状态和气象条件得出的概率。

例如市场认为 95% 不会再降温，但天气模型看到仍有强辐射降温条件，认为只有 82%。

---

## 14. Clock/window baseline：时钟或窗口基准

不看详细天气，只根据“现在处在哪个时间阶段”给出的普通基准概率。

例如：

```text
日出前仍在降温：风险较高
中午已经明显升温：继续创低温的风险较低
```

---

## 15. Residual / innovation：残差信号、额外意见

模型相对普通基准或市场多知道的那一部分。

在 V1 中可以粗略理解为：

```text
physical innovation
= 天气模型的意见 - 普通时钟窗口的意见
```

若 residual 为负，表示天气模型比普通基准更担心继续降温；为正则更乐观。

---

## 16. Market offset：以市场为底座进行修正

不是从零预测，而是先相信市场，再让天气 residual 做有限纠偏：

```text
最终模型 = 市场概率 + 天气额外意见
```

这是 V1 最值得保留的科学思想。

---

## 17. Logit：适合处理概率的赔率刻度

模型不是直接把 95% 加减 3%，而是先把概率转成一种适合相加的赔率刻度，再做修正。

ELI5：

> 50% 附近的一个百分点，与 99% 附近的一个百分点风险含义不同；logit 能更合理地表达这种差别。

无需人工手算。

---

## 18. Alpha：天气意见的音量旋钮

此处的 alpha 是 residual 权重，不是泛指投资 Alpha。

```text
alpha = 0：完全听市场
alpha = 0.10：小幅采用天气意见
alpha = 0.50：较大幅度采用天气意见
```

V1 当前存在 `.10 / .25 / .50` 等候选强度。

---

## 19. Routing：路由，只在正确阶段启用模型

不是全天都使用同一个天气 residual，而是在预先规定的物理阶段才打开。

当前主要 active windows：

```text
morning_cooling
post_sunrise_provisional_low
```

ELI5：

> 早晨仍可能继续降温时，天气细节有价值；白天已经进入明显升温阶段时，硬套同一个模型可能反而破坏市场判断。

---

## 20. Active window：激活窗口

允许天气 residual 修正市场的窗口。

当前 V1 routed challenger 只在两个 active windows 修正，其他窗口直接退回市场概率。

---

## 21. Selector：交易筛选规则

模型每个 checkpoint 都可以输出概率，但 selector 决定哪些机会最终进入 shadow trade 或交易候选。

它可能使用：

- 模型概率；
- direct ask；
- fee；
- 盘口 freshness；
- depth；
- city-day 去重规则。

模型输出概率不等于一定交易。

---

## 22. Edge threshold：入场优势门槛

常见逻辑：

```text
edge = p_model - executable ask - fee
```

只有 edge 大于预设门槛，才可能被 selector 选中。

V1 并不是简单规定“价格必须高于 95% 才买”；高价主要来自 no-further-cooling 问题本身通常发生在市场已经很有把握的阶段。

---

# 三、四层分母与链路

## 23. Denominator：统计分母、参加考试的题目集合

统计结论取决于到底把哪些 rows 放进考试。

若本来有 168 行可评分概率，却因为其中 57 行当时没有合格 ask 而只考 111 行，模型成绩可能被扭曲。

---

## 24. Denominator sensitivity：分母敏感性

比较不同合理分母下，结论是否变化：

```text
用111行时模型表现怎样？
用完整168行时模型表现怎样？
```

若变化很大，说明结论高度依赖样本筛选。

---

## 25. P0：Canonical Probability Universe

所有可以公平比较模型概率和市场概率的 rows。

当前研究中：

```text
P0 = 168 rows / 15 target dates
```

要求有 PIT 合法的市场概率、模型概率和成熟 settlement label；**不要求当时一定可交易**。

P0 回答：

> 模型脑子准不准？

---

## 26. P1：Active-window Probability Universe

P0 中属于两个 active windows 的 rows。

当前：

```text
P1 = 52 rows / 14 target dates
```

P1 回答：

> 在我们真正主张天气信号有效的阶段里，模型准不准？

---

## 27. E0：Execution-clean Universe

在 P0 基础上，还要求当时存在合格的 direct quote、freshness、fee 和足够 depth。

当前：

```text
E0 = 111 rows
```

E0 回答：

> 当时理论上是否存在可执行盘口？

---

## 28. T0：Selected-trade Universe

E0 中又通过 frozen selector 的交易候选。

当前旧策略：

```text
T0 = 19 selected trades
```

T0 回答：

> 策略规则最终决定做哪些笔？

---

## 29. Selected trade：被策略选中的交易

不是所有模型预测，也不是所有可执行盘口，只是 selector 最终选中的那部分。

因此：

```text
概率模型好不好
≠
selected trades 是否赚钱
```

后者还混入了 selector、价格、费用和样本选择。

---

## 30. Execution-clean：执行条件合格

指该时刻的交易证据满足：

- direct token side；
- quote 不陈旧；
- size/depth 足够；
- fee 可正确计算；
- token/condition identity 正确。

它只应该影响执行研究，不能删除本来可用于概率评分的 rows。

---

# 四、时间完整性与验证方法

## 31. PIT：Point-in-Time，当时可知

07:00 做判断时，只能使用 07:00 前已经发布且可取得的信息。

```text
可以用：06:50发布的观测
不能用：07:30才发布的观测
不能用：当天最终最低温
```

没有 PIT 的回测等于提前看答案。

---

## 32. `observed_at` 与 `available_at`

```text
observed_at：天气现象实际发生的时间
available_at：系统在什么时候真正能取得这条信息
```

物理 truth 常使用 observed time；可交易决策必须使用 available time。两者不能混为一谈。

---

## 33. OOF：Out-of-Fold 样本外预测

预测 8 月 20 日时，只允许用 8 月 19 日及更早日期训练。

它模拟：

> 当时真的不知道未来答案时，模型会给什么概率？

天气项目必须按 target date 切分，同一天的 rows 不能一部分训练、一部分测试。

---

## 34. Prior-date chronological OOF

按时间向前滚动的 OOF：

```text
训练过去日期 → 预测下一日期
再加入已成熟过去日期 → 预测更后日期
```

禁止 random split。

---

## 35. Fail closed：证据不足时退回安全基准

若训练日期或负例不足，不强行拟合：

```text
p_model = p_market
```

这是科学的保守做法，但若大量 rows 都 fail closed，说明新模型事实上没有接受充分检验。

---

## 36. Frozen forward：冻结后的未来验证

先锁死：

- feature；
- alpha；
- routing；
- evaluator；
- 数据边界；
- model hash。

然后只用未来新成熟的数据评分，期间不调参。

---

## 37. Diagnostic arm：平行观察臂

同一未来日期上同时记录多个固定版本，例如：

```text
市场
alpha=.10 routed
alpha=.25 routed
alpha=.50 routed
```

全部 zero-notional，只比较预测，不下单。

---

## 38. Evidence seal：证据封条

记录并锁定：

- 代码 SHA；
- 数据 hash；
- 参数；
- feature 定义；
- forward start；
- evaluator 版本。

以后不能改完模型仍冒充原 frozen experiment。

---

# 五、评价指标与统计证据

## 39. Accuracy / hit rate：猜对率

例如 19 笔全部猜对，就是 19/19。

但对于平均价格 94%～96% 的高概率交易，连胜本来就不罕见，因此 accuracy 不能独立证明 Alpha。

---

## 40. Calibration：概率校准

模型说 95% 的事情，长期是否大约 95% 发生？

高概率策略最需要校准，而不是只要方向猜对。

---

## 41. Brier score

预测概率与最终 0/1 答案之间的平方误差，越低越好。

模型越接近真实答案，分数越低。

---

## 42. LogLoss

同样衡量概率，但对“极度自信却猜错”惩罚特别重。

对于经常预测 95%～99% 的策略，LogLoss 是关键指标。

---

## 43. Delta LogLoss / Delta Brier

通常定义为：

```text
模型分数 - 市场分数
```

所以：

```text
负数：模型优于市场
正数：模型差于市场
```

---

## 44. Row-weighted / date-equal / city-date-equal

```text
row-weighted：每一行一票
date-equal：每个 target date 一票
city-date-equal：每个城市日一票
```

天气研究主指标通常应优先 date-equal 或 city-date-equal，避免某个行数很多的日期支配结果。

---

## 45. Bootstrap：重采样稳定性检验

把 target dates 整块反复重抽，观察模型改善是否在不同日期组合下仍然成立。

不能把同一天的 checkpoint 当成互相独立随机抽取。

---

## 46. Confidence interval crosses zero：置信区间跨 0

若模型相对市场的 delta 区间既包含负数又包含正数，表示：

> 当前数据无法排除模型其实没有改善，甚至略差。

这不是证明无效，而是证据仍不充分。

---

## 47. One-sided gate：单边晋级门

研究问题通常是“是否优于市场”，所以只关心改善方向。预注册单边 gate 可以避免事后改变检验方式。

---

## 48. Max-T / multiplicity：多重比较修正

同时比较多个 alpha、窗口或模型时，不能最后只挑表现最好的一个而忽略尝试次数。

Max-T 是一种联合门槛，用来控制“试得多总会碰巧选中赢家”的风险。

---

## 49. Generic market calibration baseline

不使用天气，只修正市场整体是否过度自信或过度保守：

```text
市场95%可能统一校准成94%
市场70%可能统一校准成72%
```

天气模型只有胜过 raw market 和这个简单校准 baseline，才能更有资格称为天气 residual Alpha。

---

## 50. Score gradient

先不争论 alpha 应该是 `.10` 还是 `.50`，只问：

> 天气 residual 指示“应该上调”的地方，后来市场是否真的偏低？指示“应该下调”的地方，后来市场是否真的偏高？

方向长期正确，则 gradient 应为正。

当前正确 routed 定义必须乘 active-window indicator。

---

## 51. Leave-one-date-out / leave-one-city-out

每次拿掉一个日期或一座城市，重新看结论。

若拿掉东京结果就反转，说明模型可能只在东京有效，或结论被东京支配。

---

## 52. Ablation：消融实验

一次只拿掉或替换一个组件，判断改善来自哪里。

例如：

```text
只改 target
只加 routing
只加 forecast uncertainty
只换 physical foundation
```

不能一次改所有东西后声称知道是哪一项带来改善。

---

# 六、模型复杂度与小样本

## 53. L2 regularization：L2 正则化

给模型参数踩刹车，避免小样本下参数过大。

好处：防过拟合。  
风险：数据本来就少时，真实 residual 也可能被压得几乎为零。

---

## 54. Shrinkage：收缩

没有足够证据时，把模型推回简单基准，例如：

```text
回到 raw market
回到 global city average
回到 coefficient = 0
```

---

## 55. Underpowered：统计功效不足

不是模型一定没信号，而是当前独立日期、负例或事件数量不足，无法可靠区分：

```text
真无信号
vs
有信号但样本太少
```

---

## 56. Near-identity null：几乎等于市场的近似空模型

当前 V2 平均只把市场概率改动约 0.0369 个百分点，大量 rows 直接等于市场。

因此它更像：

```text
市场 + 几乎为零的修正
```

它没有跑赢，不能有力证伪天气信息。

---

## 57. Co-linearity / duplicate features：共线或重复特征

两个 feature 实际表达同一方向，例如只相差常数，标准化后可能完全相同。

这会让 coefficient 不可解释，并改变实际正则化强度。V2.1 必须先做 algebraic deduplication。

---

## 58. Foundation model：基础天气模型

先用大量非 Polymarket 天气历史学习纯气象规律：

> 当前状态以后是否还会进入下一冷档？

然后只用少量市场数据学习“怎样把这个天气意见接到市场概率上”。

---

## 59. Market residual adaptor：市场残差适配器

小型模型，只学习：

> physical foundation 和 forecast uncertainty 应该以多大权重修正市场？

V2.1 的 settlement-level 自由参数应尽量只有 1～3 个。

---

# 七、V3 与预报不确定性

## 60. Forecast point estimate：预报点值

例如预报说今天剩余最低温为 18.6°C。

单独一个点值没有表达历史误差范围。

---

## 61. Forecast uncertainty：预报不确定性

需要知道：

> 在这个城市、这个提前量下，18.6°C 的预报历史上通常误差多大？

同样的 18.6°C，误差标准很小则很安全，误差很大则仍可能跌档。

---

## 62. Forecast-error archive

把每个历史 PIT forecast vintage 与之后真实官方温度路径配对：

```text
当时预报了什么
之后实际发生了什么
误差是多少
是否跨入下一冷档
```

它不要求当天有 Polymarket 市场，因此可以比 settlement rows 多很多。

---

## 63. Threshold-crossing probability

不是只看预报最低温点值，而是直接估计：

> 最终温度跨入下一冷档的概率是多少？

这是 V2.1 最有价值的候选输入之一。

---

## 64. Survival model：生存模型

研究“直到日终仍未跌入下一冷档”的概率。

```text
P(no further cooling before day end)
```

---

## 65. Hazard：危险率

在已经坚持到当前时刻、尚未跌档的条件下，未来某个时间段第一次跌档的概率。

例如：

```text
07:00–08:00 首次跌档风险 5%
08:00–09:00 在尚未跌档条件下风险 3%
```

---

## 66. Right censoring：右删失

到当地日终都没有发生下一冷档事件，只知道“事件在观察结束前未发生”。

这不是缺失数据，而是 survival 模型中的正常标签。

---

# 八、执行层术语

## 67. Taker

直接吃掉当前盘口，通常成交确定性高，但支付 spread/fee，价格可能较差。

---

## 68. Maker

挂限价单等待别人来成交，可能价格更好，但不一定成交，而且更容易发生逆向选择。

---

## 69. Adverse selection：逆向选择

你的 Maker 单之所以突然成交，可能不是运气好，而是新信息正在证明你的方向变差。

例如你认为真实概率 98%、挂 93¢，但成交时真实概率已经因新天气数据跌到 90%。

---

## 70. Markout

成交后若干秒或分钟，观察市场价格往哪边走。

若 Maker fill 后价格经常立刻对你不利，说明存在 adverse selection。

---

## 71. Negative skew：负偏度

经常小赚、偶尔大亏。

买 95¢ 合约：

```text
赢：约赚5¢
输：约亏95¢
```

这不自动等于坏策略，但要求极其可靠的高概率校准。

---

# 九、常见结论的白话翻译

| 研究表述 | 白话意思 |
|---|---|
| `ΔLogLoss < 0` | 模型概率比市场更准一点 |
| CI 跨 0 | 看起来更好，但样本不足，仍可能只是运气 |
| fail closed to market | 数据不够时不瞎猜，直接采用市场答案 |
| current V2 is underpowered | 这次考试太小，不能据此判定天气信号不存在 |
| routed residual | 只在物理上合理的时段让天气模型发言 |
| settlement-native | 直接按交易所最终结算题训练和评分 |
| proxy target mismatch | 模型学的近似题与最终交易题不完全一致 |
| forward frozen | 模型锁死后只看未来，不再边看答案边改 |
| no tiny live | 目前只够继续无资金观察，不够下真实小单 |
| defer Maker/Taker | 先证明预测优势，再研究如何成交 |

---

# 十、最简记忆版

```text
P0：模型脑子准不准
P1：在正确天气阶段准不准
E0：当时能不能成交
T0：策略最终选了哪些交易

Market offset：先信市场，再用天气纠偏
Routing：只在适合的时段启用天气信号
Alpha：天气信号音量
OOF：只能用过去训练未来
Bootstrap：换一批日期后结论稳不稳
Calibration：说95%的事情是否真的约95%发生
V2.1：大天气数据学气象，小市场数据学修正权重
V3：逐小时估计第一次继续降温的风险
Maker：价格可能更好，但要防只在坏消息时成交
```

---

## 证据来源与版本边界

本解释对应以下已审阅阶段：

- `TMIN_NO_FURTHER_FORENSICS_EXTERNAL_REVIEW_20260828.md`
- `TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md`
- `TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json`

本文是知识解释，不是模型晋级授权；后续数字若因新 frozen-forward 数据更新，应在新的版本文件中增补，不要静默覆盖本版。
