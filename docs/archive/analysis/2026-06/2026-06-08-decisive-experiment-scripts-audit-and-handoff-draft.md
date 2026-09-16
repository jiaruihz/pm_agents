# 天气策略 · 决定性实验脚本审计 + 合理性交接

> **废弃提示（2026-06-08，本机复核后）**：本文是外部审计草稿，只保留作原始输入和对照材料。
> 其中 `live_real=1246~1302`、近 7 天 PnL、以及“两脚本已写已自测”等表述已经被本机审计降级或修正。
> 审计结论已吸收进 `docs/analysis/live_performance.md` 与 `docs/analysis/data_integrity.md`；
> 实验脚本状态以当前工作树 `scripts/analysis/` 为准。

> 生成于 2026-06-08。本文是一份**自包含交接件**,回答用户的原始问题:「这个项目的天气策略是否合理」。
> 它在 `2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md`(给出 Step1/Step2 两个决定性实验)的基础上,
> 多做了一件之前没人做的事:**对那两个决定性实验脚本本身做逐行代码审计 + 对抗式验证**,
> 因为如果用来"定生死"的尺子本身是歪的,跑出来的"Go"就是假的。
>
> **方法**:19 个子智能体并行(4 审计 + 15 对抗验证),约 125 万 token。每条代码发现都由一个**默认立场为"驳回"**的
> 独立怀疑者回源码逐行复核。**15 条发现全部 confirmed,0 条被驳回**,其中 **11 条会高估 edge**。
> 本文所有代码行号引用均来自验证者实读源码,非推测。
>
> **⚠ 2026-06-08 样本口径勘误**:本文初稿误用了"67 fills / 9 天 / +$31.72"这个数字作为唯一 live 证据。
> **那是 2026-06-07 CLOB partial-fill recovery 修复之前的旧窄口径**(且只覆盖 05-24→06-01)。
> 修复后真实 `live_real` 是 **1246~1302 fills / 一整月+**(会随新成交增长,以 coverage gate 为准,见
> `2026-06-07-fill-recovery-and-performance-recalc.md`)。**因此"67 笔上挖噪声 / 样本太小无法拒绝零"
> 的原始论证是错的,本文已按全量口径重写;凡涉及样本量/统计显著性的结论以本勘误后版本为准。**
>
> **状态:未 git push**(按交接惯例)。

---

## 0. 一句话结论(先读这段)

**这套策略目前不合理 —— 精确含义是「方向错(模型不是 alpha)+ 全量样本已显示主力实例近期实打实亏钱 + 用来翻案的验证工具本身不可信」。不是"样本太小说不清",恰恰相反:样本够大,且不利。**

分三层:

1. **策略层**:它赌的是 H_A(自家天气模型**全局**能识别错价档位)。**H_A 已被干净证伪**——模型 Brier 在干净样本外输市场 16–30%,edge 符号不预测盈亏。**但"全局打赢市场"本来就不该是目标(市场里全是 bot),H_A 死掉是排除一条死路,不是判全局死刑。**
2. **真 edge 层(两条并行,都没被否决)**:
   - **H_B(market-free)**:市场有结构性 favorite-longshot 偏差,系统性 fade YES / 买 NO 就能结构性收割,**不需要好模型**。与全部现象自洽,从没干净测过。
   - **H_C(条件性模型优势,即用户原始思路)**:不求全局,只求**筛出一个子池**(某些城市 × GFS × 某些条件)在那里模型确实有增量。**全局 Brier 否决不了一个子池**,所以 H_C 没被证伪,只是没被科学地证实过。
3. **工具层(本文新增)**:用来裁决 H_B/H_C 的决定性脚本,**本身有 15 条已确认的统计缺陷,其中 11 条会让实验假装有 edge**。**先修脚本,再跑实验,否则会得到一个假的"Go"。**

> 判决:全量口径(1246~1302 fills / 一整月+,fill-recovery 已修、coverage gate 通过)下,**最近 7 天(05-31→06-06)三活跃实例 settled PnL −$232.74 / ROI −16.0%**,其中主力 `mid_price_core_v1_25_75` **−$163.91 / −18.2%**。这不是小样本噪声,是**主力策略在近窗的实质亏损**。**别加注、别放大 sizing。** 该做的是修好脚本 → 在全样本上干净跑 H_B(market-free)和 H_C(GFS 子池前瞻检验)→ 用数据裁决,而不是继续在已证伪的 H_A 全局路线上调参。

---

## 1. 这套策略到底是什么(锁定对象)

| 项 | 内容 |
|---|---|
| 形态 | Polymarket 天气温度档位市场,maker-only / post-only GTC,$5 notional,25 shares 上限 |
| 池 | `t1_trading`(weather-predict v4 共 22 城),两个 live 实例各带 allowlist |
| 实例 1 | `mid_price_core_v1_25_75`:全局 `0.25≤price<0.75`,`edge=model_p_yes−market≥0.10` |
| 实例 2 | `mid_price_core_v1_side_band`:YES `0.20–0.45 edge≥0.20`;NO `0.35–0.65 edge≥0.10` |
| 入场窗 | `22h≤hours_to_settle≤26h`(2026-06-08 移除 T-26-28,T-24-26 待 forecast checkpoint 复核) |
| 模型 | ECMWF/GFS/JMA/HRRR/ICON/AROME 集合 → 模拟温度分布 → 各档概率,线上用 `0.3·model+0.7·market` |
| 隐含假设 | **H_A:自家天气模型能识别市场定错价的档位** |

利润几乎全来自 BUY_NO。这是判断一切的起点。

---

## 2. 合理性判决:H_A 已死 / H_B 未测 / 全量近窗主力实例在亏

### 2.1 H_A(模型 alpha):已证伪 ❌

三层证据互相咬合,指向同一结论:

- **根因**:raw `model_p_yes` 在干净样本外 Brier 输市场——time-split `0.2226 vs 0.1715`(+29.8% 更差),LOO(24 城)`0.2083 vs 0.1794`(+16.1% 更差)。
- **唯一"赢"市场的 ensemble 是噪声**:`0.3·model+0.7·market` time-split `0.1700 vs 0.1715`,只赢 0.9%。test n≈315、Brier SE≈0.01–0.02,**0.0015 的差 ≈ 0.1 个标准误 = 噪声**。LOO 的 3.9% 稍大但对小样本城市加权敏感。
- **执行端**:06-04/06-05 的 `avg_fact_edge` 都是 +0.20(模型很"自信"),当天却巨亏。**edge 符号不预测盈亏。**
- **"edge"的真相**:`model_p_yes` 系统性低于 `market_price`(0.21–0.34 vs 0.37–0.57)。所谓 edge 本质是"模型比市场更悲观",不是"模型更准"。模型 ≈ 市场价的带噪声副本。
- **退化是 regime drift 不是 bug**:`code_version` 两期都是 `live-cycle-migration`,没换实现。

kill 判据「概率质量」2026-06-08 **未通过**。raw model 城市黑名单:Milan +0.120 / Lucknow +0.102 / Austin +0.099 / Beijing +0.094(LOO Brier 比市场差 ≥9%)。

### 2.2 H_B(市场结构性错价)/ H_C(GFS 子池条件优势):都没干净测过 ⚠

**H_B** = "市场有结构性 favorite-longshot 偏差,散户高估 YES longshot,系统性买 NO 可结构性收割,**不需要好模型**"。它与全部现象自洽(NO 赚 / YES 不赚 / 模型无预测力 / 利润集中 NO 侧),有理论先验(散户主导的小众市场普遍存在该偏差),但从没干净测过——整套度量/门控都为 H_A 建,ensemble 往市场收缩还在**稀释**它。

**H_C(用户原始思路,本轮补回)** = "**不求全局打赢市场**(那是 bot 战场),只求筛出一个 GFS 表现还不错的子池,在那里下注"。这是一条独立于 H_A/H_B 的路:

- H_A 证伪的是**全局平均**(LOO / time-split 全样本)。**一个全局为负的指标,内部完全可以藏着一个为正的子集。** 所以 H_A 死 ≠ H_C 死。
- 苗头已经存在:city×model 切片里 **Tokyo / Miami / Shanghai(都是 GFS)6 月仍正**(分别 +44.6% / +29.2% / +36.7%)。
- **但这正是 H_C 最危险的地方**,见下面 §2.4——按已实现 PnL 挑出来的"还不错的池子",极可能就是噪声拟合。H_C 不是"信不信 GFS 池子",是"**怎么筛才不是自欺**"。

### 2.3 全量样本下到底是什么状态(关键数字,已勘误)

**口径说明**:下面两组数字来源不同,口径必须分清,不能混读。

**(a) 模型口径月度切片**(`model_vs_market.md`,按 `target_date` 的全量 fill,用于看 model edge 与 PnL 的关系):

| 时段 | 笔数 | 成本 | PnL | ROI | 胜率 | 去掉前3大赢单 |
|---|---:|---:|---:|---:|---:|---:|
| 5月 (05-16→05-31) | 430 | $1211 | **+$99.06** | +8.2% | 58.8% | +$58.70 |
| 6月 (06-01→06-05) | 277 | $738 | **−$89.06** | −12.1% | 43.7% | **−$123.52** |

| 时段·方向 | 笔数 | PnL | ROI | 胜率 |
|---|---:|---:|---:|---:|
| 5月 BUY_NO | 306 | +$38.33 | +4.1% | 66.0% |
| 5月 BUY_YES | 124 | +$60.73 | +22.0% | 41.1% |
| **6月 BUY_NO** | 172 | **−$38.98** | **−7.4%** | 55.2% |
| **6月 BUY_YES** | 105 | **−$50.08** | **−23.5%** | 24.8% |

**(b) fill-recovery 修复后的实例级近窗口径**(`2026-06-07-fill-recovery-and-performance-recalc.md`,最近 7 天 05-31→06-06,coverage gate 通过):

| strategy_instance | settled PnL | settled cost | settled ROI |
|---|---:|---:|---:|
| `mid_price_core_v1_25_75`(主力) | **−$163.91** | $898.28 | **−18.2%** |
| `mid_price_core_v1_side_band` | +$24.76 | $241.72 | +10.2% |
| `mid_price_core_v2_25_75`(已停) | −$93.59 | $314.68 | −29.7% |
| **三活跃实例合计** | **−$232.74** | $1454.68 | **−16.0%** |

> 全量 `live_real` = **1246~1302 fills / 一整月+**(非 67),`fact_built_at` 2026-06-07,coverage gate `PASS`。

合理性判断(基于全量,不再是"样本太小"):

1. **样本足够,且方向不利**:这不是 67 笔说不清,是一个月+、1246 笔下,**主力实例近 7 天 −18.2%、三实例合计 −16.0%**。
2. **跨月翻转坐实退化**:5 月 +8.2% → 6 月 −12.1%;5 月最赚的 Warsaw/London/Seattle 6 月集体归零/转负。**按上月成绩选城市必被下月打脸。**
3. **6 月高胜率照样亏**:BUY_NO 6 月胜率 55.2% 但 ROI −7.4% —— **胜率≠edge** 在实盘兑现。
4. **机制已独立证伪**:H_A 死了(§2.1),没有因果故事能把近窗这点结构性亏损解释成"运气差"。
5. **唯一还活着的是 side_band(+10.2%)**:样本短(48 fills 量级),需前瞻验证,不能当已证赢家。

> 修正后的精确判断:**不是"猜硬币、样本不足",而是"全量样本已足够,且显示主力实例近期在亏 + 模型方向已证伪"。** 这比"猜硬币"更尖锐——它意味着继续在 H_A 路线上调参是负期望的,真正的出路是干净地裁决 H_B/H_C(脚本审计 §4 是这件事的前提)。
>
> **遗留口径风险**:近 7 天 −16% 本身也是一个"近窗切片",同样不能反过来用作"永久砍某城/某实例"的后验依据(否则又是过拟合跑步机)。它能支撑的结论是"别加注、停 H_A 调参",不是"按这 7 天再砍一轮"。

### 2.4 ⭐ 核心:什么是"噪声拟合",什么是"真正能判断的优势"

> 这是整份文档最该被记住的一节。前面所有数字都会过时,但这套判别标准不会。
> 它直接回答两个问题:**为什么"5 月最赚的城市"是噪声,而不是优势?到底什么样的"优势"才信得过?**

#### A. 噪声拟合长什么样(以下任何一条中招,就是在挖噪声)

1. **同窗选 + 同窗证**:用 5 月数据挑出"5 月最赚的城市",再用 5 月数据证明"这些城市赚" —— 这是循环论证,不是验证。**"剔 6 城 + ban T28 → +$39.53"就是这个**:在产生亏损的同一窗口上做切割,必然能拼出一个好看的数。
2. **跨期不稳**:Warsaw 5 月 +56.8% → 6 月 −2.5%;Seattle +84% → −18%。**一个真优势不会下个月就翻脸。** 翻脸的那个,从来就不是优势,是上个月的运气被你误读成了规律。
3. **样本不够支撑那个结论的粒度**:全局 1246 笔很多,但**切到"单个城市 × 单个方向"可能只剩 1–4 笔**。在 4 笔上排 ROI,等于看 4 次硬币就宣布这枚硬币的脾气。
4. **靠少数大赢单撑场**:去掉 top-3 赢单后转负 → 利润是集中度,不是分布性。一两单的运气不能复制。
5. **多重比较没校正**:扫 22 城 × 2 方向 × 6 个时段 = 几百个格子,**总有几个偶然显著**。不校正就挑出"最显著的那个",几乎一定是假阳性。

> 共同点:**"优势"是在你已经看过答案的数据上被挑出来的。** 看过答案再下注,当然百发百中——但那不是预测,是背答案。

#### B. 真正能判断的优势长什么样(必须同时满足三条,缺一不可)

| 门 | 通过条件 | 它挡掉的噪声 |
|---|---|---|
| **① 前瞻门(最重要)** | 在 train 窗**选**池子 → **冻结** → 在它**没见过**的 holdout 窗上同号、仍正 | 挡掉"同窗选+同窗证"和"跨期不稳"(A1/A2)——这是 Warsaw 的唯一解药 |
| **② 基准门** | 超额于**零模型**(同价位无脑买 NO),且超额显著 > 0 | 挡掉"把 base-rate 当 skill"(NO 天然高胜率,73% 可能全是 base-rate) |
| **③ 显著性门** | 指标的 **cluster bootstrap 95% CI 不跨 0**;有效样本 `n_eff ≥ 30`(同日多城相关要折减) | 挡掉"样本不够 / 靠大赢单 / 没校正多重比较"(A3/A4/A5) |

**三门全过 → `confirmed`,可以下注。** 只过①②不过前瞻 → `shadow_candidate`,只能纸面观察。任何一门不过 → `inconclusive`,**禁止当优势用**。

#### C. 把它套到你的"GFS 子池"思路上(H_C 怎么走才不是自欺)

你的直觉(筛 GFS 好的子池,不求全局)**方向是对的**。它和"5 月明星城"的唯一区别,就在于**有没有过前瞻门**:

```
错的做法(= Warsaw 第二):
  看 6 月哪些 GFS 城市赚 → Tokyo/Miami/Shanghai → 就投它们
  (这就是按本月成绩选,下个月大概率翻车)

对的做法(= H_C 的科学版):
  1. 按 城市×模型 分层算 Brier(不是全局平均)——找 GFS 在哪些城市条件性赢市场
  2. 在 train 窗选出候选池 → 冻结名单 → 在 holdout 窗复核:还赢吗?同号吗?
  3. 选出的池子上算 executable edge(真实 best_ask):扣点差/扣 bot 抢单后还剩吗?
  4. 三门全过的城市才进池;过不了的标 inconclusive,继续 shadow,不上 live
```

**判据一句话**:Tokyo/Miami/Shanghai 现在是 `inconclusive`(只有本月成绩,没过前瞻),**不是** `confirmed`。它们值得**进 holdout 检验**,不值得**直接加注**。这个区别就是"有纪律的筛池子"和"过拟合跑步机"的全部差距。

---

## 3. 对积累成果的盘点(分析维度 + 资产分级 + 结构债)

> 本节回答用户问的"分析维度模块"和"对积累成果有没有意见"。
> 方法:4 个独立评估员(8环覆盖 / 资产vs废气 / 被埋的金子 / 结构债)+ 19 条对抗复核,~179 万 token。
> 复核纠正了多条初判(标注在下),所以这里写的是**复核后**的结论。规模事实:46 篇顶层 md / 80 篇 analysis md / 52 个 JSON(~29.7 万行,单文件 7.4 万行)/ 61 个分析脚本 / 13 skill / docs 22MB。

### 3.1 分析维度模块:不是"只建了1环",是"1环过建,2/5/8环写好没跑,3/6/7环真空"

`_ANALYSIS_COVERAGE_MAP.md` 把策略验证拆成 8 环。**对抗复核给出了比原图更准的诊断——原图"Ring2/5/8 = 0"的结论本身已过时**:

| 环 | 回答什么 | 复核后真实状态 |
|---|---|---|
| 1 描述切片 | 谁赚谁亏 | 🔴 **过建**(~50/61 脚本 + ~90% 输出都在这,且在小样本上反复重切 = 多重检验面) |
| 2 统计推断 | 是真是噪声 | 🟡 **机器已存在但只接了 3 个脚本**(`weather_sizing_band_study.py` 等有 bootstrap CI / sharpe-like),缺多重检验校正、缺聚类有效N,没接进头部报告 |
| 3 信号判别(IC) | 模型能不能排序 | 🔴 **真空**(全仓 0 个 spearman/rank-IC)。这是 H_A 的 plan B:**只算了校准没算排序就宣判模型死刑,逻辑上不完整** |
| 4 分布(CRPS) | 分布准不准 | 🟢 可暂缓(H_A 已基本死,边际价值低) |
| 5 执行微结构 | 扣点差后还赚吗 | 🟡 **脚本写好了没跑**(`research_executable_edge.py`,untracked,且有 §4.3 的 bug 要先修)——maker 策略命门 |
| 6 容量 | 能做多大 | 🔴 真空(但要先有 edge 才谈容量,优先级低) |
| 7 相关性 | 同日多城是不是一个赌注 | 🔴 真空。东亚多城天气高度相关,**你的"分散"是假分散,组合方差被低估** |
| 8 基准/反事实 | 比无脑买NO有超额吗 | 🟡 **零模型只在 untracked 草稿里**(`research_market_structural_edge.py`,没跑);`counterfactual_pnl` 被 19 个脚本用了但只当求和列,没当逆向选择 delta |

**最关键的修正**:我上一版(和原图)说"缺整个右半边、要从零建",**错了**。真实情况是:**右半边最该建的 3 环(2/5/8)代码已经写好了,只是 untracked + 没跑 + 有 bug 没修**。所以最高性价比动作不是"从零建 Ring5",而是 **把那两个草稿脚本修好(§4.3 清单)、跑出来、提交**,再把已有的 bootstrap CI 接进头部 period-slice 报告。**近零新代码,就能把 3 个半环变成落地覆盖。** 之后 Ring 3(IC)是唯一真正要从零写、且能救活模型当 sizing 信号的环。

### 3.2 积累成果分级:净资产为正,但有"过拟合跑步机废气"

| 判级 | 内容 | 怎么处理 |
|---|---|---|
| **真资产(keep)** | 数据管道 / `fact_trades` 契约 / CLOB fill coverage gate / 8环覆盖图 / H_A 证伪链 / 两个决定性脚本(待修)/ ensemble(已上线) | 核心家底,别动 |
| **有价值的负结果** | blender / basket PR2/PR2b / optimizer 的几十次 sweep | **配置是废气,但"证明了它们过拟合"这个结论本身有价值**——保留结论 md,sweep JSON 可移出 git |
| **被埋的金子(buried gold,见 3.3)** | 季节条件化 / IC 测试 / C2 口径核对 / orderbook 可成交 edge | 真金,且便宜,正在被 30 万行 JSON 淹没 |
| **过建(overproduction)** | Ring1 的 ~50 个描述切片脚本 + 它们每天重跑生成的 JSON | 别再加切片维度;复核提醒:对单人 repo **不必"冻结脚本"**,那是过度流程 |

### 3.3 被埋的金子(复核确认便宜且可能 edge 相关,按性价比排)

1. **季节条件化误差分布(+9.02% Brier,44/52 城改善,零数据成本)** —— ⚠ **全仓 0 个 Python footprint**,驱动脚本只在 N100 `/tmp/season_brier.py`(文档自己标"未入库"),**一次重启/清理就永久丢失**。最该立刻做的:把那个 /tmp 脚本提交进 `scripts/analysis/`。
2. **IC / Spearman 排序检验(H_A plan B)** —— 全仓 0 个。一句 `scipy.stats.spearmanr` over 现有 settled fills 就能跑。**是最可能把"模型无残值"结论翻盘的一刀**:若 IC>0,模型虽校准差但能当 sizing 信号。
3. **C2:Polymarket 结算口径 vs WU 训练口径对齐核对** —— 一次性数据定义核对(站点/时窗/max 定义),0 个可调参数、不可能 p-hack。点预报 corr=0.997 已近完美,**剩下能漏的钱几乎全在这个锚对不对**;若锚错,它会静默污染整条误差分布→所有下游概率。复核校正:这是"便宜的地基保险,若失败会回溯性推翻一切",**不是**"能找回大部分钱"(锚可能本来就对)。
4. **658MB orderbook 镜像 + `research_executable_edge.py`** —— 复核校正为 `consolidate`:harness 写好了,但 (a) **本机当前没有这 658MB 镜像**(要先 N100 rsync);(b) 解锁需 3 件事(schema 自检 + token-map 导出 + 决策时刻对齐),不是"一个 JSON";(c) 裸跑会因未来盘口污染**误答** Ring5 问题。

### 3.4 结构债(复核后,已按单人 repo 比例校准)

| 债 | 严重度 | 复核后的比例化处置 |
|---|---|---|
| **没有"当前数字"唯一锚**(67-vs-1246 事件的根因) | 高 | 别再把冻结整数内联进叙事 md;coverage-gate JSON 已经是 live_real N 的唯一源,旧文档加指针即可。**复核否决了"建一个机器生成 CURRENT_NUMBERS 管道"——对单人 repo 过度工程** |
| **后验切城写进 live config**(2026-06-08 砍 5 城进了运行中的 loop) | 高 | 真正缺的窄东西:给这次切割打 forward-only 标记 + 预设再入场阈值。**复核否决了"config-hash + 工单制"全套变更管理——过度** |
| **52 个 JSON / 29.7万行进 git** | 中(非高) | `gitignore docs/analysis/**/*.json` + 保留 7 个审计用 PnL/fill JSON。**复核否决 DVC/对象存储——几 MB 文本不值得建基础设施** |
| **没有 thesis living doc** | 中 | 值得建 `WEATHER_STRATEGY_THESIS.md`(edge 假设 + 当前 N + 5 条 kill 判据状态),但它该 **link** 到 canonical sample-N 查询,不要把 N 写成会漂移的散文 |
| **off-thesis 文档污染 docs/ 根**(PMM/whale/lottery,且不在 DOCS_INDEX) | 中 | 它们有真代码支撑,**parking 到带标签的非-weather 子树**(别删),并补进索引 |
| living-doc drift 正在发生(13 篇 untracked 顶层 md 不在索引) | 中 | 一次性 triage:归类/gitignore,把幸存者加进索引 |
| 遗留空目录 `pmm/` `agents/` | 低 | 复核校正:其实只含 `.DS_Store`(已 gitignore),`rm -rf` 即可,不是 source-of-truth 问题 |

---

## 4. ⭐ 决定性实验脚本的代码审计(逐行 + 对抗验证)

这是之前的交接件没做的关键一步。`2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md` 说"跑这两个脚本就能定生死",但**没人验证过这两把尺子准不准**。逐行审计 + 对抗验证结果:

> **15 条发现全部 confirmed(对抗验证 0 驳回),11 条会高估 edge(让实验假装有 edge)。**
> 结论:**这两个脚本当前都不能直接信。Step 1 需修统计口径,Step 2B 当前甚至不该作为证据(有未来盘口泄漏 + join 扇出)。**

### 4.1 Step 1 — `research_market_structural_edge.py`(model-free 市场结构检验)

**做对的地方(已逐行确认正确,不要重写)**:
- Wilson CI 公式正确(L97–105,标准 score interval,处理 n=0)。
- 零模型 EV 数学正确:买 NO `cost=1−p, EV/$1=(p−realized)/(1−p)`;买 YES `(realized−p)/p`;符号对(买 NO +EV ⟺ `realized<implied`)。
- near-binary 归一化靠上游 ingest 正确处理,脚本只消费 `final_price` 作 YES 指示,内部自洽。
- 预注册前瞻**确实只在 train 上选桶、不偷看 test 结果**(选择逻辑干净)。

**已确认的缺陷(全部会让结论偏乐观)**:

| 严重度 | id | 问题 | 是否高估edge |
|---|---|---|:--:|
| medium(原 high) | `row-position-split-allows-same-day-leakage` | train/test 按**行位置**切(L202–205),不是按日期。同一 `target_date` 会同时落进 train 和 test → 同日天气/regime 共享信息泄漏,污染"前瞻"ROI | ✅ |
| medium(原 high) | `no-significance-or-ci-on-forward-roi` | 前瞻 ROI 和 baseline ROI 都是**裸点估计**(L226–245),无 bootstrap/CI/检验。Go 判据"forward ROI 明显 > baseline"是无误差棒的点对点比较 | ✅ |
| medium(原 high) | `no-multiple-testing-correction` | 15 桶 × (全表 + 交易带 + 6 个 lead-time 切片 + 模型增量表)≈ **100+ 次 `gap_significant` 检验**,固定 α=0.05,**无任何 family-wise/FDR 校正**;前瞻桶又是在 train 上挑"显著且 +EV"的(选噪声) | ✅ |
| medium(原 high) | `correlated-observations-counted-as-independent` | 同 `(target_date,city)` 的多个 bracket 结果高度相关,却在 Wilson CI 和 ROI 均值里**当独立样本**计 n → CI 过窄、显著性虚高 | ✅ |
| medium | `mid-price-overstates-buy-no-edge` | EV/ROI 用 snapshot **mid/last YES 价**,NO 成本 `=1−market_price`,**忽略点差**。真实可成交 NO 成本 ≥ `1−YES_bid` 更高 → 每个 +EV 都正偏。**脚本自己诚实标注了这点并 defer 到 Step 2**,故 medium 非 high | ✅ |
| medium | `settled-and-snapshot-present-survivorship` | inner JOIN 要求 settled + 有决策 snapshot,**~44% `decision_window_missing` 候选被静默剔除**,只报 n 不刻画被丢样本是否系统性更优质/更流动 | ✅ |
| medium | `min-hts-snapshot-not-decision-time` | dedup 取 `hours_to_settle` **最小**(最接近结算)的 snapshot,不是真实入场时刻的 snapshot;不泄漏结算 label,但 lead-time 切片(决定"T>28 该不该 ban")被扭曲 | 中性 |
| low | `gap-significance-ignores-price-sampling-variance` | `gap_significant` 把桶内 mean 隐含价当**已知常数**和 realized Wilson CI 比,忽略价格自身抽样方差 → 略反保守 | ✅ |

### 4.2 Step 2 — `research_executable_edge.py`(执行现实检验:2A 已成交审计 / 2B 盘口可成交 edge)

**2A(只用 fact_trades 真实成交,最可信的一半)做对的地方**:settled-only 口径正确;集中度 drop-top1/top5 逻辑在;runtime 列自省 `pick()` 在已验证 schema 上会选中真实 signed edge 列。

**已确认的缺陷**:

| 严重度 | id | 问题 | 是否高估edge |
|---|---|---|:--:|
| **high(原 critical)** | `2b-uses-future-orderbook-lookahead` | **2B 用"未来盘口"**:`load_latest_orderbook_prices` 取每 token 跨**全部文件**的最新 snapshot(L59–61),无 `ts≤decision_ts` 约束。临近结算 best_ask 收敛到已知结果 → NO ask 看起来更便宜 / edge 看起来更大。脚本自己的 VERIFY-3 注释(L255–257)承认未修。**任何"扣点差后 edge 仍活"的 2B 结论都被未来信息污染** | ✅ |
| **high** | `2b-signals-settlements-join-fanout` | `signals`(每 snapshot 一行)JOIN `settlements`(每市场≈一行)沿 `(target_date,condition_id,bracket)` **扇出**:一个结算行被乘以当天 snapshot 数。`n_matched` 虚高,`mean_exec_edge_no` 等是**逐 snapshot 行**的无权重均值,采样越密的市场越主导,`edge_destroyed_by_spread` 布尔在重复行上判定 | ✅ |
| medium(原 high) | `2a-unclustered-per-fill-ttest` | 零-edge t 检验 `t=mu/(sd/√n)` 把**相关 fill 当 i.i.d.**(L124–134)。天气 fill 按城/日/condition 聚簇(一次结算决定多笔),有效 N ≪ n → SE 低估、|t| 虚高 → `reject_zero_at_95pct` 可能在"其实是猜硬币"的数据上**误判有 edge** | ✅ |
| medium | `2b-maker-priced-as-taker` | maker-only 策略在 2B 按 **taker best_ask** 定价(L202–210),假设满档无排队,且没建模它自己 L8–10 提到的逆向选择 → 执行模型错配,结论被误标 | 中性 |
| medium(原 low) | `2a-unsettled-not-surfaced` | 只给 settled 裁决,**未报未结算/未平仓 live 敞口的笔数/notional** → 读者看不到被排除多少、survivorship 不可见 | ✅ |
| low(原 medium) | `2a-unsigned-edge-fallback` | 无 edge 列时 fallback 用 `abs(model−market)`(L142),非负幅值无法方向性预测 signed PnL;注释说"BUY_NO 取反"但代码**没做** side 翻转 → 自相矛盾。已验证 schema 上此分支休眠,但是 latent trap | 中性 |
| low(原 medium) | `2a-corr-fillna-zero` | `df['_edge'].fillna(0)` 把缺失 edge 当真·0-edge 点喂进相关系数(L143)→ 把 edge-预测-PnL 信号往 0 衰减(偏保守方向) | 中性 |

### 4.3 审计的元结论

讽刺但重要:**用来判断"是不是猜硬币"的两把尺子,本身在统计上偏向"假装有 edge"。** 11/15 条缺陷的方向都是高估。如果不修就跑,极可能得到一个**假阳性的"Go"**,然后据此放大 sizing —— 这正是项目想避免的过拟合跑步机的终极形态。

**跑实验前的最小修复清单(按优先级)**:
1. **Step 2B 暂不作为证据**,直到:(a) `ts≤decision_ts` 时间门(修未来盘口);(b) 先按 `(target_date,condition_id,bracket)` 去重再聚合(修扇出);(c) 决定用 maker(bid 侧)还是 taker(ask 侧)口径并说明。
2. **Step 1 前瞻改为按日期切**(distinct `target_date`,cutoff 前 train、后 test),消除同日泄漏。
3. **Step 1 + 2A 全部上 cluster bootstrap**(按 `target_date` 聚簇重抽),前瞻 ROI / 零-edge 检验都要 CI,别用裸 |t| 和点估计。
4. **Step 1 加多重检验校正**(Bonferroni 或 Deflated Sharpe)并在报告写明扫了 K 个桶/切片。
5. **Step 1 + 2A 报告 survivorship**:被剔除的 ~44% `decision_window_missing` 和未结算敞口的规模与画像。
6. mid-price → 在 Step 2 用真实点差口径确认(Step 1 的 mid EV 只能当"必要非充分"上界,脚本已自述)。

---

## 5. 修正后的执行路线(给下一个 agent / 下一台机器)

这台 Mac **没有 `runtime/weather.db`**(数据真相在 WSL `/home/rui/projects/pm_agent` + N100)。
本文的代码审计在 Mac 上完成;数据驱动的实验必须到有 DB 的机器上跑。

```
0. 同步 + 数据自检
   scripts/ops/sync_weather_remote.sh
   python3 scripts/analysis/weather_clob_fill_coverage_gate.py   # gate_pass=true 才能碰 live_real
   先跑 §6 的 5 行 SQL 自检(新鲜度 / trade_class 分布 / settled 占比)

1. 修脚本(见 §4.3 的 6 条),再跑——不要先跑后修
   - Step 2B 在 (a)(b)(c) 修完前,结论一律标 INVALID

2. 两条 edge 假设并行测(用同一批数据、同一套三门框架,见 §2.4):

   【线 H_B — market-free 结构偏差】 research_market_structural_edge.py
     - No-Go(gap 普遍不显著 + 前瞻 ROI≈0/负):市场有效,无结构 edge → 停止加注,重想方向
     - Go(某价带 gap 显著 + 前瞻 ROI 明显 > 全带买NO baseline,且过 CI/多重检验/cluster):进 Step 2

   【线 H_C — GFS 子池条件优势(用户原始思路)】 需新写/补一个 city×model 前瞻脚本
     - 按 城市×模型 分层 Brier,找 GFS 条件性赢市场的城市(不是全局平均)
     - train 选池 → 冻结名单 → holdout 复核同号且仍正 → 三门全过才进池
     - 现状:Tokyo/Miami/Shanghai = inconclusive(只有本月成绩),进 holdout 检验,不直接加注

3. Step 2A(真实成交,扣点差):edge 是否真预测 settled PnL?集中度去 top1/top5 后是否还正?cluster 化零-edge 检验能否拒绝零?

4. 只有某条线 1+2/3 全过(三门),才:建 thesis living doc → 冻结预注册配置(价带+方向+size 上限为代码常量)→ 选一个不再回看的前瞻窗 → 小步上。**不再按实现 PnL 砍城市。**
```

**现实预期(必须降级)**:即便 H_B 成立,模型在市场之外≈0 增量,结构偏差在有流动性的天气档位上大概每笔只有几个百分点。真实形态是「**小的、容量受限、执行敏感的做市 edge**」,**不是一台印钞机**。优化对象应是执行/点差/容量,不是模型精度。

---

## 6. 数据源护栏(跑任何数字前必须遵守,来自契约 + canonical sources)

- **唯一取数源** `runtime/weather.db` 的 `fact_trades`(fill 粒度)/ `fact_signal_candidates`(机会粒度);**只能 filter+groupby,禁止从 raw signals/orders/fills/snapshots 自算 PnL**。
- **禁用 DB**:`runtime/_legacy/*.db`、`runtime/weather_v2.db`、`runtime/weather_edge_v1/weather.db`(全退役)。
- **PnL 公式(唯一授权,formula B)**:
  - BUY_YES `pnl_usd_at_fill = (final_yes − fill_price)·qty − fees`
  - **BUY_NO `= ((1 − final_yes) − fill_price)·qty − fees`**(2026-05-29 勘误:`fills.filled_price` 存的是 NO-token 价,不是 YES 等价;旧契约 §2.1 `fill−final` 是错的)。**用错 BUY_NO 公式会错排 edge。**
  - realized PnL 只在 `settlement_status='settled'` 上算;`pnl_usd_at_fill` 是唯一授权 realized 指标。
- **live 统计只用 `trade_class='live_real'`**,绝不用 `live_simulated`/`paper` 的 PnL 替代。
- **反事实/机会 alpha 分母**只含 `final_yes IS NOT NULL AND decision_window_missing=0 AND eligible=1`(~56% 机会),报告必须点明覆盖不全。
- **near-binary**:pm_history raw 价常是 `0.9995/0.0005`,builder 已归一化为 `{0,1}`;任何 2026-06-06 前引用大 `missing_bracket`(725/734/28)的报告**已污染,必须重算**。
- **现金流口径**:`cost_usd`/open cost **不是亏损**;`order_date_bj` **禁止**用于解释钱包现金流(只是下单归属诊断),真实花钱日是 `fill_date_bj`,`target_date` 是天气目标日。
- **fact_trades 是 fill 粒度(幸存者偏差)**:回答不了无单/无成交/被过滤的反事实,那些在 `fact_signal_candidates`,不要混粒度。

### 6.1 5 行数据自检 SQL(分析前强制)
```sql
SELECT MAX(fact_built_at_utc) FROM fact_trades;
SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class;
SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status;
SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates;
SELECT o.status, COUNT(*), SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END)
  FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status;
```

---

## 7. 明确不要做(anti-goals,违反即是过拟合跑步机)

- ❌ 不要继续在 H_A 路线上增删城市池 / 微调 timing 窗 / 搜模型变体(v1/v2/maker_queue/blend)去追更好的回测数字 —— 模型方向已证伪,这是在已知负期望的路线上调参。
- ❌ 不要把"最近 N 天某城/某实例亏"直接回写成永久砍城/砍实例的 live config —— 这是同窗后验切割,正是把 5 月明星城 6 月一刀切的那台过拟合跑步机。城市级 keep/cut 必须过显著性 + 基准 + 前瞻三门。
- ❌ 不要把任何同窗后验切割("剔 6 城 + ban T28 → +$39.53/+$128")当 edge 证据 —— 那是拟合,不是验证;由全窗切割决策构建的 walkforward 已污染。
- ❌ 不要把 ensemble 的 0.9% Brier 改善当显著 alpha —— ≈0.1 个标准误,噪声。
- ❌ 不要继续优化模型精度去"修模型" —— 修不动一个本就不如市场的模型;模型只配当收缩/风控项。
- ❌ 不要把胜率当 edge(BUY_NO 73.7% / 6月 55.2% 高胜率照样亏)—— 必须和"同价位无脑买 NO"baseline 对照才能分清 skill 还是 base-rate。
- ❌ 不要按 `execution_policy` 数策略数 —— 两实例共享 `mid_price_core_v1` 但用不同 gate + 独立 live dedup。
- ❌ 不要把 Tokyo/Miami/Shanghai 的"keep small live"当已证赢家 —— 那是按 6 月 PnL 选的,需前瞻验证,否则就是下一个 Warsaw。
- ❌ **(本文新增)不要直接跑那两个脚本就采信结果** —— 先按 §4.3 修;Step 2B 在修未来盘口泄漏 + join 扇出前结论无效。

---

## 8. 交接清单 / 文件 manifest

```
deleted audited copy (recoverable from git history); durable verdicts now live in the family living docs
docs/analysis/2026-06/2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md                    ← 前置(H_A/H_B 判断 + 两脚本初版)
scripts/analysis/research_market_structural_edge.py                                 ← Step 1(待修 §4.1)
scripts/analysis/research_executable_edge.py                                        ← Step 2(待修 §4.2)
docs/analysis/model_vs_market.md                                                    ← H_A 证伪 living doc
docs/analysis/_ANALYSIS_COVERAGE_MAP.md                                             ← 8环分析维度图(§3.1 据此,但其 Ring2/5/8=0 已过时)
docs/WEATHER_PROBABILITY_MODEL_REVIEW.md                                            ← 季节条件化 +9% Brier / C2 口径核对(§3.3 buried gold)
docs/WEATHER_ANALYSIS_CONTRACT.md / WEATHER_DATA_CANONICAL_SOURCES.md               ← 口径/护栏
docs/WEATHER_FACT_TRADES_DESIGN.md / WEATHER_SIGNAL_CANDIDATES_DESIGN.md            ← 列定义
```

**审计方法溯源**:本文用了两个后台多智能体工作流。
§4 的 15 条脚本缺陷:19 子智能体(4 审计 + 15 对抗验证),~125 万 token,每条由默认立场"驳回"的怀疑者回源码逐行复核,15/15 confirmed。
§3 的成果盘点:23 子智能体(4 维度评估 + 19 对抗复核),~179 万 token;复核纠正了多条初判(如 8环图 Ring2/5/8 已非真空、对单人 repo 不必上 DVC/冻结脚本),本文采用的是复核后版本。

---

## 附:本文诚实边界

- 我**无数据**(这台 Mac 没 `runtime/weather.db`)。§2 的所有绩效数字均**引自仓库现有文档**(`model_vs_market.md`、`2026-06-08-RELIABILITY-AUDIT-AND-HANDOFF.md` 等),非本文新测。
- §4 的代码审计是**静态**的(实读源码 + 对抗验证),但缺陷的**实际数值影响**取决于运行时数据,需在有 DB 的机器上跑出来才能量化。
- 初稿误用 67-fill 旧口径(fill-recovery 修复前)做"样本太小/猜硬币"论证,已勘误为 1246~1302 全量;§2.3 改用 `2026-06-07-fill-recovery-and-performance-recalc.md` 的实例级近窗数字。零-edge 显著性的精确 t / CI 仍需在修好的 Step 2A(cluster bootstrap)上算。
- 本文**未 git push**。
