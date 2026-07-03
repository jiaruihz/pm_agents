# Regime-Routed NO Strategy Review v1

审稿范围：城市最高温 Polymarket 策略 —— runway/capped 表达 + soft sizing 的 regime-routed NO。
依据文档：`2026-06-25-regime-routed-no-expression-v1.md`、`2026-06-25-regime-routed-no-live-feature-parity-v1.md`、
`2026-06-26-regime-routed-no-live-case-review.md`、`2026-06-29-regime-routed-v3-frozen-forward-failure-diagnosis.md`、
`2026-06-24-intraday-weather-regime-atlas-v1.md`、`2026-06-29-regime-routed-expression-router-v3.md`、
`2026-06-26-regime-routed-no-peak-clock-clean-v2.md`、`2026-06-26-regime-routed-no-wind-context-v2.md`，
以及 `scripts/analysis/reheat_risk/research_regime_routed_no_expression_v1.py` 实现细节。

## 1. Alpha 表达 vs bracket payoff：大体一致，但有两处结构性错位

**runway → current-bracket NO 是对的表达**：running max 只会上升，所以 current-bracket NO 只在"全天封在当前档"时输。
它是一个"不在这里"的赌注，不需要预测封在哪——对一个低分辨率的升温信号，这比买 forecast-peak bracket YES 稳健。

但有两处错位：

**a) capped 用 d1 NO 表达，恰好是做空预报误差的众数档。** `day_forecast_capped` 的定义是 forecast max 贴着 running max——
那么 d1（current+1）正是预报只低估 1° 时的落点。d1 NO 不是"cap 守住"的表达，而是"最终不落在 current+1 这一格"的表达，
对预报正偏差最敏感。数据已经证实：route-leg 拆分里 `capped_d1_no` ROI **-6.2%**，而 d2 变体 +2.4%
（expression v1 route-leg 表、router v3）。d2 才是真 cap 表达（要输需要预报低估 ≥2°）。这不是调参问题，是表达语义问题——
建议 capped 路由在文档里明确"d1 NO 永久禁止"的机制理由，而不是仅凭回测选了 d2。

**b) runway 的"打穿"判定和结算口径不在同一个刻度上。** 结算是 WU/结算站整数 °F（或 0.5°C），而 running max、forecast
margin 都在 METAR native 单位。当 escape margin < 1 个结算刻度时，payoff 实际上由舍入和站点 basis 决定，不由"还能不能升温"
决定。Seattle 64-65 案（forecast 65.3F，margin 0.3F）输的不是天气，是刻度；SF 案（METAR 21.0°C→70°F vs WU 结算 68-69）
直接证明 bracket 归属本身可以判错。**当前 `day_regime` 用 `forecast_gap_to_running_native` 判 runway，正确的物理量是
`forecast_max − bracket_upper`（换算到结算单位、含结算舍入规则）**——case review 里已经写了这条 "still needed"，
但它不该是待办增强，它是 runway 路由的定义级缺陷。

还有一个值得注意的信号：atlas 无条件矩阵里 `day_open_runway` 的 current_bracket_no ROI 是 **-2.4%**，`day_marginal_runway`
才 +1.5%。也就是说"明显还有空间"的日子市场定价是充分的，所谓的 edge 全部住在 marginal 带——而 marginal 带恰恰是
basis/舍入噪声主导的区域。这要么意味着 edge 真实但极窄，要么意味着 edge 是边界噪声的镜像。区分这两者比任何新特征都重要
（见实验 E1、E2）。

## 2. PIT / parity / 口径漏洞（按危险程度排序）

1. **`best_ask` 选点是后视择时，且它抬升了所有 headline ROI。** `select_one_per_city_day` 按全天最低 ask 挑一笔——
   当时不可能知道哪个小时是全天最低价。strict 口径下 best_ask 比 near_noon 高出约 9 个点 ROI（+52.6% vs +43.8%）。
   frozen forward 诊断已承认"旧 best_ask 有后视择时"，但 v1/v2 的主候选（含那条 CI [+5.5%, +47.4%]）仍然是 best_ask
   口径。**所有还在引用 best_ask 数字的结论应标记为上界，不可用于 live 决策。**

2. **valid-time join ≠ arrival-time join。** as-of 观测 join 用 90 分钟容忍度按观测时间对齐，隐含假设"观测发生即可见"。
   METAR 有发布延迟，IEM cache 更晚；live runner 在 decision snapshot 时刻看到的观测集合是 replay 无法用 valid-time
   重建的。同类问题在 forecast 侧更重：6/17–6/23 的 forecast peak clock 是**事后 backfill** 的（atlas 口径限制自己写了
   6/21–23 只有部分 native 覆盖），peak-clock multiplier 在这些天的 replay 值 live 拿不到。这直接污染 peak-clock clean
   v2 的对照。

3. **候选选择本身是多重比较，CI 没有为它付费。** v1 一次跑 14 个 variant × 2 selector，再叠 3 档 soft policy，然后挑
   `routed_capped_d2_no_relaxed70_best_ask + soft_balanced` 报 CI。soft weight 的系数（route 1.00/0.70/0.45、
   humid −0.12、peak-clock 0.20…）是看过同一份 38 天样本后手写的——这是绕后门的 in-sample 拟合。date-block bootstrap
   只处理了日间相关，没处理"从几十个组合里挑了最好看的一个"。frozen forward 转负（train +7.6% → forward −13%）与其说
   是 regime 失效，不如说是这个上偏被扣掉了。

4. **soft sizing 在执行层退化成 hard filter。** wind context v2 里 `soft_balanced` 的 exec_rows 是 77/271：
   base $5 × 0.2 权重 < 最小下单单位，小权重仓位直接归零。于是"不筛样本、只降权"在真实执行里等价于砍掉 72% 的样本，
   而且 `soft_balanced_base5_min5` 变体出现 10–11 个 -100% 日——这正是要避免的样本切碎。要么提高 base notional
   让权重可实现，要么承认它是 filter 并按 filter 的标准审它。

5. **live 血缘不完整，parity 无法逐单验证。** SF 真实成交不在 `fact_trades`（靠公开 activity 才发现）；pre-fix 的
   live 单没有落 accepted-candidate 完整特征 payload，复盘只能重建。加上一个标着 `shadow` 的进程实际带
   `--live --confirm-live` 在跑——这三件事叠加意味着"live 与 replay 一致"目前是不可证伪的声明。case review 里两条
   "still needed"（特征 payload 落日志、账户 activity 对账视图）应该在任何新实验之前完成。

另外两处小但真实：`apply_liquidity` 的 `require_payoff=True` 把无结算 payoff 的行静默剔除（若缺结算与市场异常相关，
是幸存者偏差）；`day_space_unknown` 1318 行来自 forecast clock 缺失，但 unknown 在 wind doc 里自带 -22.4% ROI——
**缺失本身携带信号，说明缺失不是随机的，是数据覆盖的代理变量**，任何把 unknown 当一档处理的 slice 都在拟合数据管道
而非天气。

## 3. 哪些机制是物理，哪些是噪声

**站得住的（有物理机制 + 样本量尚可 + 方向可事先写出）：**
- **peak clock**（决策在预报峰前/峰后）：日循环是最强的物理先验，current NO 在峰后失去机制支撑。v2 的 peak_future
  拆分方向正确。
- **escape margin**（预报峰 vs bracket 上沿）：直接就是 payoff 的物理自变量，见第 1 节。
- **1h/3h trend + running max 新鲜度**：真实，但两者与 `intraday_state` 高度共线，不要当三个独立证据数。
- **湿度/低纬 humid cap**：湿静力能约束午后升温上限，机制真实；humid_low_latitude 降权方向合理。
- **onshore marine flow 压制沿海升温**：机制真实（海风到达即封顶）。

**大概率是样本噪声或数据伪影：**
- **wind_sector 表**：W +46.9%（57 行）vs NE −16.7%（22 行）——8 个 sector 切 243 行，这是教科书式的切片噪声。
  风向对最高温的作用方向本身依城市地理反号（沿海 onshore 封顶 vs 干燥大陆下混增温，Karachi 案就是后者），全局
  sector 表没有物理解释力。
- **coastal_flow "flow_unknown" −22.4%**：见上，缺失即信号 = 管道伪影。
- **cheap_stale_tail_current_no +70.5%（9 行）**、**pullback YES（11 行，还缺 ask size）**、**plateau_near_high
  （atlas 里只有 12 个 state）**：全部不足以支撑任何结论，只能算待验证假设。router v3 把它们标成 research_shadow
  是对的，但注意 v3 的 +13.0% 总 ROI 里有 +$31.74 来自那 9 行 stale tail——**头寸表的总数被微型 sleeve 的运气抬着**。
- **geo_context 细分**（inland_alpine_edge n=10 之类）：同上。

一个结构性建议：atlas 本身说得很对——"label 是天气结构，不等于买/不买"。但 router v3 实际上又把 label 当成了 route
的硬条件（`intraday_state in active_warming/fresh_high` 才进 fresh_runway）。更干净的做法是把这些机制特征喂进一个
校准的 P(escape) / P(cap) 连续模型，和市场 ask 比较出 EV，route 只决定表达式；这也符合"先构造连续信号，切片只用来
解释"的原则。现在的架构介于两者之间，每加一个 regime 就多切一刀样本。

## 4. 最近 live/frozen 错单归因

逐单归因（依据 `2026-06-26-regime-routed-no-live-case-review.md`）：

| 案例 | 归因 | 类别 |
| --- | --- | --- |
| NYC ×2 重复单 | 重复下单 gate 缺失，赢了但流程错 | 纯执行 bug（已修） |
| Seattle 64-65 | forecast 65.3F 只高出 bracket 0.3F 就进场 | **表达定义缺陷**（escape margin 不在结算刻度上），不是价格也不是运气 |
| Chongqing | 旧 live 没有 peak-clock，峰后 +0.6C margin 进场 | **live/replay 特征不对齐**（parity bug） |
| Karachi | peak 在前、通过所有 gate，输在预报正偏差 + 大风下混 | **真实机制失效**，属于模型风险，不该用城市黑名单补 |
| Manila | pullback 状态被路由成 runway | label 语义 bug（v3 已改路由，待验证） |
| SF 68-69 | METAR °C→°F vs WU 整数 °F 结算 + `shadow` 进程实际 live | **source basis bug + 治理事故**，最严重的一单 |

6 单里没有一单是"正常波动"——每单都指向一个可命名的缺陷，这其实是好消息（说明还没到纯运气层面），但也说明系统还在
还债阶段。

**frozen forward（6/21+ 转负）的归因**：不是单一 bug。三个成分：(a) train 基线被 best_ask 后视 + 变体选择抬高，
forward 是第一个无偏估计——train +7.6% 在扣掉选择效应后可能本来就接近 0；(b) forward 窗口 ask 均值 +5 个点
（0.534→0.585），入场价格实打实变差；(c) `forecast_error_native` 从 0.09 跳到 0.92——6 月下旬进入预报系统性高估的
天气段，fresh_runway 和 capped_d2 同时转负与此一致。28 行 / 6 天的 forward 无法在统计上区分"机制失效"和"从来没有
那么大的 edge"，所以正确动作是 frozen 诊断文档里那句：不许挑 6/23+ 当新切分，继续冻结积累 forward 样本。

## 5. 五个最值得做的增强实验

**E1 — Escape margin 校准曲线（最优先）**
- 假设：P(打穿当前档) 对 `forecast_max(结算单位) − bracket_upper` 单调，runway NO 的亏损集中在 margin < 1 个
  结算刻度的区间。
- PIT 字段：decision snapshot 时刻的 run-stamped forecast max、bracket 定义、per-city 单位与结算舍入规则。
- 分母：全部 runway 路由候选 city-day（不是 selected trades——用候选层避免选样偏差）。
- 指标：margin 分桶的 escape 率校准曲线 + 各桶 `escape_rate − (1 − ask)` 的 EV。
- 防过拟合：事先注册"单调"这一个假设，不搜阈值；6/21 前拟合、6/21 后验证。
- 纯离线可做，不需要 shadow。

**E2 — 结算 basis 归一层**
- 假设：X%（估计 5–15%）的 "current bracket" 判定在决策时刻已经和结算站口径差一格，这部分单 EV 显著为负
  （SF 是存在性证明）。
- PIT 字段：结算站（WU）整点观测序列、METAR native 序列、per-city 舍入规则。
- 分母：全部 selected trades + SF 类账户 activity 单。
- 指标：off-by-one 率；basis 一致 vs 不一致子样本的 ROI 差。
- 防过拟合：这是测量实验，无参数可调。已有 `2026-06-30-forecast-station-error-distribution-v1.md` 底子可复用。
  离线。

**E3 — arrival-time 双时钟 replay**
- 假设：valid-time as-of join 在美化回测；换成 ingestion 时间后一部分单的 label/入场会变，ROI 下降。
- PIT 字段：snapshot/observation 的**入库时间戳**（不是观测时间戳）、forecast 发布时间。
- 分母：现有 selected trades 全集。
- 指标：决策改变的行占比；两套时钟的 ROI 差。这个差值就是当前回测的"PIT 税"，以后所有报告都应扣它。
- 防过拟合：测量实验。若入库时间戳历史上没存，只能从现在开始双写，前段承认不可考——那本身就是结论。

**E4 — route-specific 入场时点的 nested walk-forward（已在 `2026-06-29-regime-routed-v3-route-specific-timing-wf-v1.md`
起头，做完它）**
- 假设：去掉 best_ask 后存在一个事先可执行的入场规则（如"local 11 点后首个通过 gate 的小时"），forward ROI 仍 > 0。
- 分母：固定 v2 denominator，路由规则冻结。
- 指标：仅 forward 段的 date-block bootstrap ROI；成功标准 = CI 下界 > 0，事先写死。
- 防过拟合：候选菜单事先列全（≤5 个规则），nested 选择，不看 forward 挑。
- 只能 frozen/shadow。

**E5 — 预报修订动量（forecast revision delta）**
- 假设：当天内 forecast max 的上/下修比静态早晨预报更能预测 escape，且市场对修订的吸收有 ~1h 滞后
  （`2026-06-26-forecast-update-time-repricing-v0.md` 已有雏形）。
- PIT 字段：run-stamped 的逐次 forecast 发布（含发布时刻）、修订前后 orderbook。
- 分母：当天有 ≥2 次修订的 city-day。
- 指标：修订方向对 escape 率的 lift；修订后 ask 的调整速度。
- 防过拟合：只用"修订差的符号"一个特征，不设阈值。
- 需要 shadow 验证修订在 live feed 的可见延迟，历史部分离线。

## 总评

这套研究的自我纠错能力是真实的优点：PIT audit 表、peak-clock bug 的承认、frozen forward 主动报负、router v3 不当作
forward 修复来卖——这些习惯比大多数回测报告诚实。核心问题不在方向，在于**回测的地板还没打平就开始往上垒机制**。

**最可能的 5 个系统/口径漏洞**
1. 结算 basis：METAR native vs WU 整数结算刻度，污染 bracket 归属和 escape 判定（SF、Seattle 已付学费）。
2. best_ask 后视择时仍嵌在主候选和引用最多的 CI 里。
3. valid-time join + backfill 的 forecast peak clock，replay 看到 live 看不到的数据。
4. 一份 38 天样本上做变体选择 + 手写 soft 系数，CI 未为多重比较付费——frozen forward 转负的第一嫌疑人。
5. live 血缘缺口（fact_trades 漏真实成交、特征 payload 不落日志、shadow 进程实跑 live），parity 声明当前不可证伪。

**最可能的 5 个策略机制改进**
1. runway 判定改用结算单位的 `forecast_max − bracket_upper` escape margin，margin < 1 刻度直接是机制边界
   （这属于允许的 hard gate：已知无效区域）。
2. 把 peak clock 从 sizing multiplier 升级为预报小时曲线的"bracket 之上剩余度-小时"连续量。
3. 风的作用按地理物理定向（onshore 封顶 / 干燥下混增温），砍掉 fitted wind sector 表；missing 一律不给权重优惠。
4. per-city/source 预报偏差先验（6/30 的 bias 工作）对 forecast max 做 shrinkage，直接回应 Karachi 型失败。
5. regime 从 route 硬条件退回特征层，做一个校准 P(escape) 连续模型对市场 ask 算 EV——停止每加一个 state 就切一刀
   样本。

**最优先做的 3 个实验**：E2（basis 归一）→ E1（escape margin 校准）→ E3（arrival-time 双时钟）。共同点：都是测量
而非调参，都在给回测地板定水平，而且任何一个的结果都会改写其余所有报告的可信区间。在这三个落地前，任何 route 层
的新增强（包括 router v3 的 YES sleeve）的回测数字都应该按"上界"读。
