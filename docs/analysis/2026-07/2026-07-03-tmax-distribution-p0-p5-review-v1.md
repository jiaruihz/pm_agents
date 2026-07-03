# Tmax Distribution P0–P5 审阅 v1

> 审阅范围：`p0-anchor-scorecard` → `p1-fusion-scorecard` → `p2-ev-shadow` → `p3-feature-ablation` →
> `p4-observed-label-extension` → `p5-walk-forward-execution-replay`，含
> `research_tmax_distribution_p0_anchor_scorecard_v1.py` 代码级审查与独立复算。
> 复算脚本口径：与 P0 `_label`/`_interval` 完全一致，对 13173 行 atlas 重分解。

## 一句话结论

链条纪律是这个 repo 迄今最好的一轮（train-only 选择、fixed+expanding 双 forward、逐桶分解、诚实 verdict），
方向值得继续；但**有一个未解决的结构性问题贯穿 P0–P5：分母对"结算低于 METAR 当前档"的行做了结果依赖的删失
（1003 行 / 7.6%，几乎全部是美国 °F 城市），它单边美化 current_yes，而 P4 的 observed 标签又反向单边惩罚
current_yes——当前证据无法判定链条最大 EV 来源 current_yes 的真实符号**。这把 settlement basis 层从
"待办测量实验"升级成 P 链的阻塞依赖。

---

## 1. P0-local 三问审阅

### 1.1 market_local_norm 是否合理？——基本合理，三处具体缺陷

构造（midpoint 经 NO 簿交叉推 YES，残差归 tail）作为"市场技能基准"是合理的，但：

- **残差 tail 在 53.7% 的行上塌缩到 ≈0**（复算：`market_raw_local_mass` p50=1.002，>1 占 53.7%）。
  局部三档 mid 之和略超 1 时 tail 被压到 EPS。market 在 actual=tail 行的 logloss 0.843（vs 全体 0.579），
  没有灾难性失真，P0 结论不翻，但 v2 应改用 `lottery_yes_ask`（最便宜更高档 YES）作 tail 质量下界，
  atlas 里现成有这列。
- **mid 打分 ≠ 可交易口径**：用 midpoint 做技能对比正确，但它对 P2 的含义是"打败 mid 的技能要再扣半个
  spread 才是 EV"；P2 用真实 ask 执行，这一点处理对了，保持即可。
- **必须分 unit 报告**：market 在 °F 城市 logloss 0.2718 vs °C 城市 0.6310。结合 §2 的删失发现，
  "°F 市场极准"有幸存者成分——被删的 1003 行 basis 行几乎全是 °F 城市。

### 1.2 current/d1/d2/tail 四桶是否足够？——对当前表达集足够，但桶体系缺了一格

四桶覆盖 METAR 网格下的全部可达结果（running max 只升不降）。问题在于**真实结算的结果空间是五格**：
复算发现 1003 行（7.6%）`final_winning_bracket` 低于 METAR 当前档——结算源（WU 整数 °F）打印得比
METAR running max 低。分布按城市：Atlanta 130 / Austin 121 / Miami 120 / NYC 114 / Houston 103 /
Seattle 102 / Dallas 95 / LA 74——**美国 °F 城市全员上榜，这是 METAR≠WU 结算 basis 的指纹**（SF 案的
批量版）。P0 的 `_label` 对这些行直接 return None 删掉。

四桶本身可以保留（表达只到 d2/tail），但需要二选一修复：
(a) 加第五桶 `below`，市场侧 below 质量并入残差处理；或
(b) 先做 basis 归一（current bracket 用结算单位判定），使 below 物理上不可能，然后四桶重新完备。
(b) 是正解，(a) 是过渡。

另：13173 行只评 6519，另一大删失是 `grid_nan` 3674 行（27.9%，三档报价不齐）——评分样本条件在
"市场关注度高、报价齐"的状态上，模型优势在报价稀疏处可能更大也可能不存在，这部分是盲区，说明容量
估计不能从 6519 行外推。429 行 final_nan 与 6/27–29 未结算完全对应，无异常。

### 1.3 "forecast/running anchor 输给 market"是否有口径漏洞？——结论成立但被高估

- 复算桶基率：current 59.3% / d1 18.2% / d2 12.1% / tail 10.4%。**常数气候基线（按边际频率报概率）
  logloss ≈ 1.111**，而两个 anchor 是 1.63/1.65——anchor 连一个"永远报基率"的零信息基线都打不过，
  甚至比 uniform(1.386) 还差。它们是 hour-blind、σ=0.75 定宽的稻草人（且 σ 在 °C/°F 城市物理宽度差
  一倍）。"输 1.05 nats"里大部分是"没用小时信息"，不是"市场懂天气"。
- P0 缺的"锚切换/小时加权 blend"基线，P1 的 `weather_physical` 实际上补上了，而且结果重要却被 P1
  文档轻描淡写：**纯天气模型（完全不看盘口）forward logloss 0.574–0.598 vs market 0.5654**——
  公开物理特征几乎复现了市场的全部信息量。这是对"市场是浅的、锚启发式的"假说的最强支持，
  应该写进结论而不是当作 blend 失败案例。

---

## 2. P1–P5 链条审阅

### 2.1 值得肯定（保持）

- 选择纪律：C/alpha 只在 6/21 前 date walk-forward CV 里选；fixed + expanding 双口径；date-block CI。
- P3 的 LOO 消融回答了"是不是 city id 记忆"：去掉 city_source 反而略好（-0.0037），regime 族有真增量
  （去掉变差 +0.0073）——"regime 从方向盘降级为传感器"的迁移已经完成且有数据支撑。
- P2/P5 用真实 ask、一状态一表达 dedupe、逐桶 PnL 分解、edge 阈值不冻结进 live。
- P4 把 observed-derived 标签明确隔离为压力测试，不混入正式口径。

### 2.2 关键问题（按严重度）

**(1) current_yes 的符号在当前证据下不可判定——链条最大的未决问题。**
P1 逐桶分解显示模型全部增量来自 actual=current（delta -0.097），d1/d2 略变差；P2/P5 里 current_yes
是最大持仓（dedupe mix 里 YES 占 ~40%）。但两个口径对 current_yes 的偏差方向相反：

- verified（settlement 标签）：删失掉的 1003 行 basis 行恰好是 current_yes 必输的行
  （结算印得比 METAR 低 → METAR 当前档 YES 落空）→ **verified 的 current_yes +17.7% 被删失单边抬高**；
- extension（observed 标签）：METAR final max 相对结算系统性偏高（7.6% 行高出 ≥1 档）→ observed
  标签系统性偏向更高桶 → **extension 的 current_yes -40.3%（CI 上界 -35.2%）被单边压低**。

真值在两者之间，且无法从现有表推出。d1_no/d2_no 对 ±1 档 basis 扰动同样敏感（结果在
current/d1/d2 间移动）。**修复路径唯一：settlement-basis 层（E2），把 current bracket 判定换到结算
单位，重跑 P1 评分与 P2/P5 EV。** 在此之前，任何把 current_yes EV 当真的动作（包括 shadow sizing
倾斜）都建立在删失伪影上。低成本先行版：对 P2/P5 全部 selected rows 做"结算 = METAR 网格下移一档，
概率 = per-city basis 率"的扰动重算，报告 EV 对 basis 的敏感度带。

**(2) `loo_no_city_source` ≡ `mkt_regime` 是同一个模型。**
full 减 city_source 恰好等于 mkt_regime 特征栈，P3/P4/P5 三份表里两者数字完全相同。文档把它们并列
报告，读起来像两个独立方法互相印证——应合并为一行，避免把同一模型数成两票稳健性。

**(3) 数据采集在 forward 最需要的时刻衰减。**
P3 库存表：orderbook snapshot 文件 6/27=48、6/28=23、6/29=3、6/30=1；settlement_outcomes 止于
6/28；6/27+ state rows 无正式 label。**forward 证据管道正在断流**——这比任何建模改进都优先。
链条现在最缺的就是 forward 天数（verified 只有 6 天，extension 3 天），采集每断一天，
`inconclusive_positive_signal` 就多维持一天。

**(4) 跨 scope 的赢家漂移仍是多重比较。**
verified 最优 = loo_no_city_source(+11.5%)，extension 最优 = mkt_city_source(+8.4%)，两个 scope 各挑
各的赢家；edge≥0.10 在 dev-CV 最优但 extension 变薄。P5 文档对此是诚实的，但下一轮必须事先冻结
唯一 primary（建议：mkt_regime blend + edge≥0.02 + dedupe，其余全部降级为 sensitivity），否则
forward 天数攒够后又会面对"哪个才算数"的选择偏差。

### 2.3 次要改进

- 评分基准表加两个廉价基线：常数气候（logloss≈1.111，我算的，进表校验）和 hour-bucket 条件气候——
  给"市场浅度"一个连续标尺。
- market_local_norm v2：tail 用 lottery_yes_ask 下界；所有对比表加 unit 分栏。
- P2/P5 未模拟 depth/fill（文档已声明）；zero-notional shadow telemetry（P5 verdict 建议）落地时
  顺带记录 ask size，容量问题第一次有真数。

---

## 3. 下一步（按序，前两项阻塞后面所有）

1. **修采集**：恢复 orderbook snapshot 到 48 文件/天基线，补 settlement_outcomes 6/29+ ingest；
   同时把 P5 输出接成 zero-notional shadow ledger（每 city-date-hour 记录模型分布、市场分布、
   selected expression、ask/size），forward 从此按天自动积累。
2. **basis 层（E2 落地版）**：用 1003 行做 per-city `P(settlement 低于 METAR 档)` 表；
   P2/P5 EV 全量做 basis 扰动敏感度；然后把网格判定换结算单位重跑 P1/P3 评分。
   验收问题只有一个：**current_yes 的 EV 在 basis 修正后还剩多少**。
3. **冻结 primary**：mkt_regime blend（=loo_no_city_source）+ edge≥0.02 + dedupe 作为唯一
   pre-registered 主策略；成功标准写死：verified forward 累计 ≥12 天时，logloss delta date-CI 上界
   <0 且 basis 修正后的 dedupe EV CI 下界 >0。达标才谈 tiny live，不达标继续 shadow。
4. P0 v2（tail 下界 + unit 分栏 + below 桶）与 6/27–7/2 settlement 补录后的 verified 重跑，
   作为 2、3 的自然副产品，不单独立项。

## 4. 对"市场是浅的"假说的更新

P1 的 weather_physical 近平局 + P3 的 market_recal 单独就能拿 -0.019（市场自身校准可被温度缩放改进）
+ 模型增量集中在"当前档守住"概率——三件事拼起来的图景：**这个市场对"会不会继续升温"的定价接近公开
信息的上限，但对"守住"的置信度校准系统性偏差，且该偏差可被物理状态变量条件化**。这正是第一性原理
文档预测的锚切换盲区。alpha 不大（logloss 差 4–5%，EV 8–12% 未扣 basis），但它的存在形式与理论预测
一致——这比数字本身更支持继续投入。
