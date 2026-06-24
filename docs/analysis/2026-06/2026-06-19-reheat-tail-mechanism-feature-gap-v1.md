# Reheat 尾部机制 ↔ 特征覆盖缺口 v1

Status: design-draft
Date: 2026-06-19
血缘层: [1] 概率/特征层（reheat_risk 分支）。不碰 [3]–[6] 执行/评估链。

## 一句话

current-YES（fade_confirmed / peak_forming）在高 ask 区赢不动，不是阈值没调好，而是
**决定尾部（高 ask 打穿）的物理机制没进模型**。方向：从"在现有特征上调存活分类器"
转向"用驱动尾部的物理机制补全特征集"。

## 思路方向：机制覆盖审计 → 尾部特征补全

两步纪律：

1. **机制覆盖审计**：列全"决定 target metric 的因果/物理机制" → 逐条对照模型**实际吃进的
   特征** → 标 `在模型 / 只代理 / 缺失`。对抗的是 omitted-variable / 特征不完备；纪律是
   对照**领域机制清单**，不是对照手头现成的列。
2. **尾部 hazard / 二阶 edge**：高 ask 时 EV 全在分布尾巴 → alpha 不在更准的中位数分类器，
   而在难特征化、连市场隐含模型也欠定价的残余机制。核心命题：**edge 存在于"我的特征集抓到
   某个尾部机制、而市场也漏了它"的交集**（differential information）。

为什么有效：把问题从"在已校准市场（市场价当概率 holdout AUC ≈ 0.946）上调阈值"（零和）
换成"找市场和我都还没建模的那块物理"。

## 机制覆盖审计快照（2026-06-19）

对照 `train_theta_current_yes_fade_confirmed_model_v1.py` 与
`train_current_yes_peak_forming_hazard_v1.py` 的真实 `NUMERIC_FEATURES`：

| 物理机制 | fade_confirmed（真钱默认 base） | peak_forming_hazard（research） | 原始数据 |
|---|---|---|---|
| 露点天花板 | 水平+depression+**趋势**(d_dwpf/d_relh) | 水平+depression，**无趋势** | 有 |
| 温度 tendency | d_tmpf_1h/3h | temp_trend_1h/3h | 有 |
| 日照过峰时机 | **裸 hour，线性塞进 logistic** | GFS/ECMWF forecast peak clock + gap | 有 |
| 云量水平 | sky_now | sky_cover_code | 有 |
| **云的导数（转晴/堆积）** | 缺失 | 缺失 | 需工程化 |
| **暖平流（风向×上游温度）** | 缺失（只有风速） | 缺失（只有风速） | **风向 drct 现成、未用** |
| 边界层混合/塌缩 | 缺失 | 缺失 | 无（需气压/层结） |
| 结算站 basis | 缺失（在 station-basis 另一条线） | 缺失 | — |

## 缺口结论

- 最该决定高 ask 单子死活的两个机制——**暖平流、云导数**——两个模型都没有。它们正是把
  95% 胜率打穿成赔钱日的尾部事件。
- 风只进了**速度**没进**方向**：暖平流是矢量(V·∇T)，光知道风多大没用。
- fade（下真钱那个）的时间处理反而比 peak_forming 粗：裸 hour 线性塞 logistic 表达不出
  日内驼峰；peak_forming 用 forecast peak clock 更对。
- 模型是线性 LogisticRegression：即使字段在表里也是各自线性相加，表达不了"晴 **且** 已过峰
  **且** 低露点 → 锁死"这种**合取**尾部条件。"字段在"≠"机制建模了"。

## 补全成本排序

- **几乎免费**：`d_sky_3h`（云导数，复用现有 3h 差分机器）、`drct` 风向 sin/cos（原料现成）。
- **中等**：露点趋势补进 peak_forming；fade 用 forecast peak clock / minutes-since-solar-noon
  替掉裸 hour；加机制交互项。
- **贵**：真·暖平流（GFS 850hPa 温度梯度）、边界层（气压 tendency / 层结）——数据管道活。
- **坑**：peak_forming 的 forecast peak clock 字段虽定义，但 hazard doc 自报
  `forecast_peak_hour_local` 生产 0% 覆盖、待 backfill——先核实覆盖再谈别的。

## 触发这种分析的提示词

- 机制对照：「列出决定 `<target metric>` 的物理/因果机制，逐条对照模型特征表，标 *在模型/
  只代理/缺失*；缺失里哪些驱动尾部（高 ask 打穿）。」
- EV 落点：「这个策略 EV 集中在分布哪一段？那段由哪些机制决定？建模了吗？」
- 市场盲点：「市场对这个标的哪些维度已校准、哪些可能系统性定错？我的特征覆盖后者吗？」
- 先验证：「把还没进模型的候选特征，在 holdout 上跟 `survive=0` 样本对区分度，证明能分再加。」

口诀：**先问"机制列全没"，别先问"阈值调对没"。**

## 结论（2026-06-20）

1. **不是气象没用，而是 residual edge 变薄**：METAR core（温度、露点、湿度、风速、云、温湿趋势）
   对 survive 有真实判别力；但市场价/base v9 已吸收大部分一阶信息。
2. **"先验证再加"纪律生效**：拦下两个听起来合理、实测为空的特征。负结果本身是产出。
3. **P1/A-B 后续已补测**：`d_sky_3h×solar_altitude` near-zero，`wind×city` holdout logloss 稳定退化；后续本地 research-only IEM cache 可拉到 `alti`，`d_alti_3h` 有小的条件信号，但完整候选模型仍不过 raw market/base promotion gate。

## 未来方向（两条并行，主线仍是完善模型）

P0 只否定了**廉价形态**；P1 已补测本地可做的正确形态。合并结论见
[2026-06-20-current-yes-proper-form-tail-features-v1.md](2026-06-20-current-yes-proper-form-tail-features-v1.md)：
不要把结果读成"气象没用"，应读成"常规气象有判别力，但新增粗尾部形态没有稳定 residual edge"。

- **主线 A → 存活模型残差校准**（继续完善模型，但换焦点）：
  本地正确形态里 `d_sky×solar` 和 `wind×city` 已不过关，不加；`d_alti_3h` 已用本地 IEM alti cache 补测，有弱条件信号但不够升级模型。若继续完善模型，优先扩样验证 `market price + existing METAR core/alti` 的正则残差校准，而不是继续堆粗风/云尾部特征。
- **并行 B → reheat-reversal 反买头**（同一气象信号的另一种表达，非替代）：
  用 P0 **唯一验证有效**的温湿趋势当 reheat 信号，低 ask 买更高档 YES 凸性。赔率结构比 fade 头好。
  与主线共享信号，不抢资源；可并行或在 A 之后做。
- 任一方向真要进模型/live：走 STRATEGY_REVIEW_PIPELINE 复盘 + deploy 的 git-first 流程，不直推。

## 阶段路线（成本 vs edge 切片）

| 阶段 | 内容 | 成本 | 状态 |
|---|---|---|---|
| **P0** | 风向 drct→sin/cos、云导数 d_sky_3h（+气压倾向待 N100） | 几乎免费（本地 ext 缓存有原料） | **已做，廉价形态负结果，见下** |
| **P1** | 云导数×太阳高度交互、风向×city、本地正确形态 residual 测试 | 便宜~中等（部分需经纬度） | **已补测**：`d_sky×solar` near-zero，`wind×city` 退化；`d_alti_3h` 弱正向但不够过模型门 |
| **P2** | NWP 逐时 2m 温度趋势、修 forecast peak clock 0% 覆盖、换非线性模型(GBDT) | 中等（接管道/改模型类） | 暂缓 |
| **P3** | 真·温度平流 −V·∇T(格点)、边界层混合(探空)、HRRR/RAP 短临 | 贵 | 基本放弃，除非 P1 证明平流是主因 |

> AUC 读法：随机取一对(打穿日/守住日)，模型把打穿日打更高分的概率。0.5=抛硬币(无信息)，0.7–0.8=不错，1.0=完美。

## P0 验证结果（2026-06-20，本机真实数据）

脚本 `scripts/analysis/reheat_risk/validate_reheat_tail_feature_discrimination_v1.py`：
把本地 IEM ext 缓存（`theta_no_iem_ext_patch_v6`，含 drct/skyc1）按 snapshot 时刻 asof-join
回 full_replay_v8 holdout，造 `wind_dir_sin/cos`、`d_sky_3h`，测对 `survive=0`（打穿）的区分度
与"在 base v9 之上的增量 logloss"。join 自检 `corr(sky_now 原表, ext 还原)=1.000`。

| 候选（原始形态） | 单独 AUC | Δlogloss vs 现模型 | 结论 |
|---|---|---|---|
| wind_dir_sin / cos | 0.53 / 0.53 | +0.0008 / −0.0003 | 噪声，无信号 |
| d_sky_3h | 0.54 | −0.00005 | 中性 |
| 参照 d_tmpf_3h（已在模型） | **0.78** | — | 真正区分打穿日的是温湿**趋势** |

**结论：风向/云导数的廉价原始形态对打穿日几乎零区分力，也不在现模型之上加信息——不加。**
真正的判别力来自温度/湿度趋势；这说明气象机制存在，但新增廉价形态没有 residual edge。

P1 后续补测：
- 风向 × city 交互：feature-only AUC 0.607，但加到 base v9 后 logloss +0.04971，稳定退化；
- d_sky × 太阳高度交互：AUC 0.530，delta logloss -0.00008，near-zero；
- 气压倾向：后续本地 research-only IEM cache 已拉到 `alti`；`d_alti_3h` 有小的条件信号，但完整候选模型不过 raw market/base promotion gate。

下一步取舍：若继续模型主线，扩样验证 `market + existing METAR core/alti` 正则残差校准；若看交易表达，
继续执行层（maker / fresh-ask）与 reheat-reversal 反买头（同一信号喂相反策略）。

## 关联

- 策略状态/血缘归属：[WEATHER_STRATEGY_REGISTRY.md](../../WEATHER_STRATEGY_REGISTRY.md) 分支二 reheat_risk。
- 复盘流程：[WEATHER_STRATEGY_REVIEW_PIPELINE.md](../../WEATHER_STRATEGY_REVIEW_PIPELINE.md)。
- 当前 live 过滤现状：[2026-06-19-theta-current-yes-filter-simplification-v22.md](2026-06-19-theta-current-yes-filter-simplification-v22.md)。
