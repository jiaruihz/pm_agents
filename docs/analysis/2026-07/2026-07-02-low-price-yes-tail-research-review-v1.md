# Low-Price YES Tail / Reversal 研究审阅 v1

Generated: 2026-07-02
Reviewer: 独立复核（不依赖原研究结论，全部数字用冻结 artifacts 重算）
Scope: `2026-07-02-low-price-yes-tail-strategy-thesis-v1` / `source-aware-tail-v3` / `lottery-selector-refinement-v1` / `lottery-metar-regime-v2`

## 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | 冻结研究 artifacts `docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/details.csv`（v3 的唯一输入）+ `docs/analysis/2026-06/generated/city_strategy_fit_by_forecast_bias_v1/city_strategy_fit_by_forecast_bias.csv` + `runtime/weather.db` 只读（survivorship / executability 复核） |
| 数据快照时间 | fact built `2026-07-01T16:27:01Z`（v1 报告头）；本审阅未重新 sync N100——审阅对象是原研究用的冻结分母，重 sync 会改变分母导致无法对齐复现 |
| 记录行数 | v1 分母 476 行（hist 457 + fwd 19）；v3 champion 289 行（hist 278 + fwd 11）；train-only（≤6/20）诊断 383 行 / 44 天 |
| unsettled 占比 | base universe（edge≥0.20, ask 0.05..0.20, <6/27）463/475 settled = 97.5%；12 行被 drop，集中在 5/17（4/9 缺）、5/18（6/6 全缺）、5/19（2/2 全缺） |
| missing_bracket 数 | 未单独计数；上述 12 行即 settled 覆盖缺口，5/18、5/19 两个整日不在分母里 |

复算 sanity：v3 hist 278 rows / 50 dates / 47 cities / win 16.9% / ROI +46.1%，fwd 11 rows +186.6%——与原报告完全一致，原报告数字本身无造假/算错，问题出在归因和推断。

---

## 0. 一句话结论

**v3 的"source-aware"机制故事被推翻——48/48 城市 forecast source 固定，band 实际是城市分组，excess vs v1 的 CI 跨 0，且 holdout/forward 都被选择污染——但分母级信号是真的。用 train-only（≤6/20）数据独立诊断后，更可信的核心论题是：结算站热基差没有被 tail YES 定价吸收，错价在"历史上系统性打穿预报"的城市最大，且日内不自我修正。**

```text
对 source_aware_v3 作为 live selector 候选：
significance(超额 vs v1)=FAIL   (paired date-block excess CI [-3.8%, +47.4%] 跨 0)
baseline(零模型)=PASS(点估)     (同行 blind BUY_NO -6.3%，random side +25%，v3 +46.1%)
forward=NA                      (band 在看过 6/21-6/26 holdout 和 6/27-6/30 forward 后选出，两段均被选择污染)
conclusion=inconclusive          (不可作为改 live selector 的依据；分母级信号值得继续研究，见 §1、§5)
```

交易动作（先于证据给出）：**维持 $1 v1 tiny live 不变；不切 v3 selector；不 size-up；下一版按 §5 的校准选择器和预注册流程重做，不是继续调 ask band。**

---

## 1. 核心 Alpha 论题

### 1.1 论题一句话

**结算站热基差（station hot basis）+ 湿热城市 tail 方差没有被 next-day tail YES 的定价吸收：给 bracket 报价的边际参与者按共识预报数字定价，不带站点级误差分布；错价在"站点历史上系统性打穿预报"的城市最大，且日内不自我修正（直到 METAR 打印才 reprice）。** ask band、forecast source 都只是这个结构的粗代理。

> 本节切片只用 train 窗（target_date ≤ 2026-06-20，383 rows / 44 dates），6/21+ 保持未触碰。这些仍是事后切片，只用于**定假说**，不用于定规则；规则需在 §5 预注册后由 fresh forward 裁决。

### 1.2 原始信号确实存在

v3 的 278 个 historical 行上：realized win 16.9% vs avg ask 11.5%。同一批行 blind BUY_NO 是 -6.3%，随机选边 ~+25%（一半 YES 收益）。即：**市场对这批"被模型标记过的" tail YES 的定价系统性低于 realized 频率**。这不是分母幻觉——v1 全分母（457 行）也有 win 13.6% vs ask 10.5%。所以"cheap tail YES + model edge 过滤"这个 universe 里确实有东西，问题只在于把它归因给了错误的机制（forecast source）。

### 1.3 支撑 pattern（按证据强度排序）

**① station-bias class 在 train 上强判别，且剂量响应单调。** 把 `city_strategy_fit_by_forecast_bias_v1` 的城市分类 join 到 v1 分母 train 行：

| source_bias_regime | rows | dates | cities | ROI |
|---|---:|---:|---:|---:|
| hot_underforecast_clean | 129 | 41 | 16 | **+57.6%** |
| hot_underforecast_noisy | 13 | 12 | 4 | +206.3% |
| cold_overforecast_clean | 16 | 14 | 2 | +30.1% |
| balanced_tight | 31 | 24 | 3 | -57.3% |
| cold_overforecast_noisy | 27 | 23 | 4 | -30.4% |
| mild_or_mixed | 31 | 24 | 5 | -79.8% |
| 未分类（欧洲 13 城缺层） | 135 | 43 | 13 | +17.8% |

按 `hot_tail_pct` 三分位**单调**：low -30.5% / mid +44.0% / high +54.2%。单调剂量响应 + 独立数据层（站点历史误差，不是从交易结果里挖的）——这比 v3 任何 ask band 的证据形状都好。它同时解释了 v3 的假象：亚太 GFS 城市恰好多为 hot_underforecast，欧洲未分类城市落在 ECMWF 袖。

⚠️ 一个必须修的 leakage：6/30 bias 层的窗口与 train 交易期重叠（部分循环论证）。下一版必须用 **as-of 滚动 bias 特征**（每个决策日只用 T-1 之前的站点误差历史），见 §5 W0。

**② 市场不在日内修正，反而更不信。** 50 个 train 赢单里 33 个的全生命周期最低 ask ≤ 决策 ask 的 0.7 倍（均值 0.58x）。即赢单的 ask 在决策后通常还会**继续走低**，市场对 tail 越来越不信，直到打印才跳。含义：(a) 执行窗口很宽，不需要抢；maker 挂单可能把入场改善 ~30-40%；(b) "repricing lag v4" 的真实形态是**晚间打印跳变**，不是渐进 repricing。

**③ 注意力/时区缺口。** 前一晚 18:00-24:00 本地时间决策的 172 行承载了全部 PnL（+$149.7, win 20.9%）；当天 00:00-06:00 决策的 117 行合计 ≈ 0（win 12.0%）。与 ①的城市构成混杂，需城市内对照，但方向一致：给亚洲 book 报价的人在美盘深夜不在场。

**④ 模型 tail 全局过度自信、在 hot-basis 城市接近校准。** 全分母 mean model_p 0.35-0.49 vs realized win 7-35%，模型对 tail 系统性高估约 2 倍；但 Shanghai win 41.7% ≈ model_p，即对这些城市模型是对的、市场是错的。`edge>=0.20` 之所以有用，只是间接选出了 ask 远低于（哪怕打了对折的）真实概率的行——这是个粗代理，真正该做的是校准后的 `p_cal - ask`。

**⑤ 执行可行性初步为正。** train 行 median yes_spread 2c、median 5c-depth $135，仅 3/305 行 depth<$5（20% 行 depth 缺失）。$1-5/张的 taker 成交假设基本成立；$50+/张才开始碰容量。

**⑥ 组合角色（待验证假说）。** tail YES 袖与 regime-routed NO 主账本可能在同一物理事件上反号——NO 账本最差的日子（温度打穿）正是 tail YES 中奖的日子。若日度 PnL 负相关成立，这条袖子即使 standalone edge 一般，也值得作为 NO 账本的凸性对冲持有，sizing 可以挂在 NO 敞口上。

### 1.4 本轮诊断暴露的数据缺口

- `forecast_max_below_bracket_f` / `forecast_max_in_bracket`（bracket 距离，第一性机制特征）在分母行上 **98% 为空**（383 行只有 8 行有值），必须自己物化（bracket label 解析 + `forecast_max_native`）。
- 城市 bias 层缺 13 个欧洲城市（Amsterdam/Madrid/Moscow/London/Milan/Paris/Warsaw/Helsinki/Munich/Ankara/Busan/HongKong/LA），而 ECMWF 袖的利润恰好在这些城市——层必须补全才能统一假说。
- bias 特征是 6/30 静态快照，需改 as-of 滚动（见 ① 的 leakage 警告）。

---

## 2. source-aware v3 的漏洞清单

按严重度排序：

### 2.1 选择污染：holdout 和 forward 都不是干净的（最致命）

`research_low_price_yes_source_aware_tail_v3.py` 在**同一张含 recent/holdout/forward 列的表上**比较了 10 个 band 变体（gfs_05_15 / gfs_05_20 / ecmwf_10_20 / ecmwf_08_20 / source_aware_wide / …），然后宣布 source_aware_v3 是 champion。K=10 未报告、无任何多重检验处理（合同 §0 样本门槛明确要求）。6/21-6/26 "holdout +55.8%" 和 6/27-6/30 "forward +186.6%" 都在选择集里——**它们是被选出来的，不是被验证的**。原报告标 `forward=PARTIAL` 太宽容，应为 `forward=NA`。这些窗口已经烧掉，下一版不能再用它们选任何规则。

### 2.2 超额不显著

v3 相对 v1 的 paired date-block bootstrap excess：点估 +21.9%，**CI [-3.8%, +47.4%] 跨 0**。原报告的 `baseline=PASS versus unified v1` 只比了点估，没做同分母配对检验，按合同显著性门应 FAIL。v3 自身 CI [+3.0%, +95.7%] 也只是勉强不跨 0。

### 2.3 贡献集中度：~10 笔交易承载全部 edge

- top 3 城市（Shanghai/Amsterdam/Manila）占总 PnL 的 **69%**；Shanghai 一城占 36%。
- historical top-N trades removed：top1 +40.6%（原报告只报了这个）→ top3 +30.5% → top5 +20.4% → **top10 -1.1%**。278 行里去掉 10 笔，edge 归零。
- top-N dates removed：去掉 5/14 一天 +35.4% → 去掉 3 天 +13.1% → **去掉 5 天 -0.8%**。50 个交易日里 5 天承载全部。
- 凸性袖子本来就该长这样，但这意味着有效样本 n_eff 是"个位数中奖事件"，任何 CI 都要打折读。

### 2.4 forward +186.6% ≈ Shanghai 又中了两次

11 行 forward 里，3 笔赢单中 2 笔是 Shanghai（+$17.18、+$6.63），fwd top1 removed = +33.5%。Shanghai 本来就是历史最大贡献城市——forward 不是对 source-band 机制的独立确认，是**同一个城市集中度风险在 forward 里重演**。

### 2.5 月度衰减

v3 historical：May +74.5% → June(1-26) +25.5%。被剔除的互补带两个月都稳定小亏（-7.5% / -11.0%），说明"剔除的确实差"，但"留下的在变弱"。

### 2.6 口径尾巴（不致命，但要修）

- **Survivorship**：5/18、5/19 两个整日因结算缺失整体不在分母（12 行 drop）。占比小（2.5%），但"缺的日子恰好是什么行情"没人查过。
- **forward payoff 口径混用**：historical 用 `final_yes`/`settlement_outcomes`，forward 用 CLOB `tokens[].price`。fresh forward 固化前应统一走 `settlement_outcomes`。
- **v3 与 v1 的 source 判定不一致**：v1 champion 的 gfs tag 用 `forecast_source OR forecast_peak_source`，v3 只用 `forecast_source`。这次没造成行数差异，但同族脚本口径应统一。
- **无 lookahead 硬伤**（这点是好消息）：decision 快照都在前一晚 18:00 至当天 06:00 本地时间（升温开始前）；`decision_entry_price` 是决策窗快照的 PIT entry；`edge = model_p - entry_price`（`market_yes_price` 与 entry 完全相等，不是独立 mid）；every-city-date dedup 规则（最早快照、低 ask tie-break）可执行。结算 join 按 city+date+bracket，抽查 top winners 无 bracket 错配迹象。

### 2.7 执行现实性完全未验证，且现成字段没用

决策快照里**已经带了 `dec_yes_spread` 和 `dec_yes_depth_ask_5c`**（builder 物化了），但整个 v1/v3 研究链没有用它们。5-10c 档（恰好是 GFS 袖利润所在）是薄 book 高 spread 区，backtest 假设 ask 全额可成交。$1/单下问题不大，但这同时锁死了容量：v3 约 5.8 行/活跃日，$1 规模日均 PnL ~$2.6，size-up 前必须先看 depth 重放。

---

## 3. 被忽略的模式

1. **city+source 已如 §1.3**：source 是城市分组的代理。下一版直接用城市/区域/station-bias 建模，把 source 从"机制"降级为"城市属性之一"。
2. **决策时刻 / 候选新鲜度**：前一晚决策 vs 当天凌晨决策的 PnL 差异巨大（§1.3③）。值得做成连续特征：候选 first_seen 距 target day 的小时数、决策快照本地时刻、book 上一次变动距今时长。它与区域混杂，需要在城市内做对照。
3. **bracket 距离连续特征缺失**：`forecast_max_above_bracket_f` / `forecast_max_in_bracket` 已被 load 进 DataFrame 但从未进入任何 selector 或切片。"tail 是 next-bracket-up 还是 far tail"是第一性机制变量（合同 §4 明确要求连续信号优先），现在完全靠 ask 间接代理。
4. **station-bias prior 未接入**：repo 已有 42,705 行 city-date-model 历史误差层（`2026-06-30-historical-forecast-station-bias-v1`），这是解释"为什么 Shanghai 的 model_p 是校准的而市场不信"的现成数据，比 ask band 直接得多。
5. **市场 repricing 路径未看过**：有 orderbook capture 的日子，可以看被选中的 5-10c ask 在决策后 6-12 小时的路径——如果多数赢单的 ask 在 METAR 出来前就爬到 20-30c，说明存在可加仓的 repricing lag（真正的 v4 方向）；如果 ask 一直躺到结算，那利润只是 settle 兑现，无中途表达。
6. **表达选择未比较**：同一 city-date 触发时，买 next-bracket YES vs 更远一格 vs 买两格 ladder vs 等半天再买——一次都没有同分母比过。
7. **组合相关性**：同日多城 tail 同涨同跌（大范围热浪），date-block bootstrap 只处理了日内相关，区域块（asia/europe/americas per date）更诚实。

---

## 4. v2 (METAR/regime) 审阅意见

v2 报告本身的自我否定是对的（宽口径 -43.9%、score 递进无宽正带、窄 tag holdout 挂），`inconclusive` 判级恰当，additive score 检查是好实践。两点补充：

- v2 与 v1/v3 **分母不同源**（intraday atlas rows vs fact_signal_candidates decision rows），"等 METAR 再买更差"这个结论严格说是"晚入场的 atlas 分母更差"，不完全等价于"同一批 v1 票等确认后再买更差"。若要真正回答 timing 问题，应固定 v1 的 city-date 集合，对每张票比"决策窗价 vs 晚 N 小时的价"。
- v2 里 forecast_source 以 `gfs_seamless` 为主而 v1/v3 全是 `open_meteo_live_*`，坐实了 source 标签随管道/时期漂移，进一步削弱把 source 当机制的做法。

---

## 5. 下一版详细执行计划

原则：**固定分母 + 预注册规则 + 连续机制特征 + 分层归因**，不再"调 band 看总 ROI"。规则全部在 train（≤6/20）冻结并预注册；6/21 起的历史一律不再参与选择；评估只认 **7/03 起的 fresh forward**。

### W0 — 数据缺口修补（前置，~0.5 天）

1. 物化 `bracket_distance_f`（bracket 下沿 − 决策时 `forecast_max_native`，按城市 unit 换算）：进 research feature 层或 candidates builder；98% null 的现字段废弃不用。
2. 站点 bias 层补 13 个欧洲城市 + 改造成 **as-of 滚动版**：`station_bias_asof(city, date)` 只用 date 之前的误差历史（expanding window，最少 60 天起报）。产出一张 city×date 的 PIT prior 表。
3. forward payoff 口径统一走 `settlement_outcomes`（弃 CLOB token price）；5/17-5/19 结算缺口能补则补。

### W1 — 冻结校准选择器 `low_price_yes_tail_pcal_v1`（~1-2 天）

- Universe：冻结的 v1 分母（BUY_YES, ask 0.05..0.20, earliest PIT per city-date；**edge≥0.20 从分母移除**，改为下面的 EV 规则，避免双重阈值）。
- 特征（全 PIT）：`hot_tail_pct_asof`、`station_bias_p50/p90_asof`、`bracket_distance_f`、`model_p_yes`、`ask`、`decision_local_bucket`、region。
- 模型：带城市先验 offset 的 logistic（或 model_p 上的 isotonic + prior shift），train ≤6/20 拟合，系数冻结进 JSON。
- 决策规则：`p_cal - ask ≥ θ`，θ 在 train 上按"日均 3-6 张票"定一次，不扫格。
- 预注册：文档里声明尝试过的全部变体数 K 与选择理由；train 上验收 = date-block CI>0 且 p_cal 十分位 lift 单调且 paired excess vs 冻结 v1 CI>0。不过验收就承认失败，不换 6/21+ 的数据续命。

### W2 — Live journal 分层 tag（不改下单，~0.5 天）

给 $1 v1 live journal + shadow 脚本（`scripts/ops/low_price_yes_lottery_reversal_shadow_v1.py`）每行加：
`p_cal`、`hot_tail_pct_asof`、`station_bias_class_asof`、`bracket_distance_f`、`decision_local_bucket`、`yes_spread`、`yes_depth_ask_5c`、`source_aware_v3`（仅对照）。pcal selector 以 zero-notional shadow 并行跑。

### W3 — Fresh forward 裁决 gate（等数据，评估窗从 2026-07-03 起）

- **Promote gate**（$1 → $3-5/张，仍 shadow-tag）：≥12 个已结算活跃日 且 pcal-selected date-block ROI CI>0 且 top-trade-removed>0 且 fill-feasible 子集（depth≥$25、spread≤3c）ROI 与全样本差 <15pt。
- **Kill gate**：≥15 日且 ROI<0，或 hot-basis 层 realized win 按 CI 低于 avg ask → selector 废弃，telemetry 保留。
- 中间不看不调：评估窗内禁止改 θ / 特征 / band。

### W4 — 执行研究：maker vs taker（并行，用 live $1 的真实 fill）

问题：决策价 taker 吃 ask，还是 ask−2c 挂 maker？依据 §1.3②（赢单 ask 决策后均值还跌到 0.58x），patient entry 期望改善大。方法：orderbook capture 可覆盖的日子做 fill-prob 加权对比 + live fill 对账（实际 fill 率 / fill price vs `decision_entry_price`）。产出一条执行策略进 W3 的 promote 版本。

### W5 — 组合验证（~0.5 天）

对齐日期算 tail-YES 袖（shadow + live）与 regime-routed NO 账本的日度 PnL 相关；若 bust 日显著负相关，写进 STRATEGY_REGISTRY：这条袖子的角色是 **NO 账本的凸性对冲**，sizing 规则挂 NO 敞口比例（例如 tail 袖日成本 ≤ NO 账本日均敞口的 5-10%），而不是独立 absolute size。

### 失败路径的价值

若 W3 kill：结论不是"cheap YES 没戏"，而是"station-basis prior 不足以在票价里兑现"——此时保留的 telemetry（p_cal、bias、距离、时段）直接喂给 current-NO 侧做**反向风险特征**（哪些 NO 会被 tail 打穿），研究不清零。

---

## 6. 对现有文档结论等级的修订建议

| 文档 | 原结论 | 审阅后 |
|---|---|---|
| source-aware-tail-v3 | `shadow_candidate_keep_collecting`，baseline=PASS，forward=PARTIAL | **`inconclusive`（作为 selector）**；baseline 超额 FAIL、forward NA；分母级信号保留 shadow 采集 |
| tail-strategy-thesis-v1 | 主线 = source-aware convex sleeve | 主线改述为 **city/station-bias + 时区注意力的 tail mispricing**（§1），source 降级为城市属性；thesis 的"不是 cheap YES 本身 / 不是 METAR regime"两条否定结论维持 |
| lottery-selector-refinement-v1 | `shadow_candidate_keep_collecting` | 维持；no-dust 分母与 payout-cap sizing 是合理工程化，唯一注意 §2.6 口径尾巴 |
| lottery-metar-regime-v2 | `inconclusive` | 维持；补一条：timing 结论受分母不同源限制（§4） |

---

## 复核 artifacts

- 复核脚本（临时，未入库）：paired date-block bootstrap、区域×source 拆分、决策时刻拆分、band 内部结构、survivorship、train-only station-bias join、executability/repricing 诊断，全部基于 `generated/low_price_yes_lottery_selector_refinement_v1/details.csv` + `generated/city_strategy_fit_by_forecast_bias_v1/city_strategy_fit_by_forecast_bias.csv` + `runtime/weather.db` 只读。
- 关键数字均可用本文口径从上述冻结 CSV 一行行复算。
