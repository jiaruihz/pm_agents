# tmax_distribution_edge 首个 live 日复盘 — Lucknow 2026-07-05

目标切片：`tmax_distribution_edge_live_candidate_v1`（strategy_id `tmax_dist_clean_edge02_tiny_live_v1`）
2026-07-05 首个 live 日，Lucknow bracket 36 全部 4 笔真实成交的信号→订单→成交→敞口复盘。
target metric：这 4 笔的表达正确性 + 执行政策符合性 + 对"模型是否有大问题"的证据强度。

## 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | live_orders.jsonl（策略实例 runtime）+ Mac data-feed snapshot/observations + docs 回测报告；**fact_trades 无覆盖（见异常）** |
| market 同步 | `sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only` @ 2026-07-05 16:29 CST |
| 最新 snapshot | 2026-07-05T08:19:30Z（13:49 IST） |
| 最新观测 | 2026-07-05T08:31Z：VILK current_temp 37.0°C，d_tmpf_1h +1.8°F（仍在升温） |
| 记录行数 | live_orders 4 笔（全 Lucknow bracket 36，place status 全部 matched） |
| unsettled 占比 | 4/4（今日未结算） |
| missing_bracket | n/a（未入 fact_trades） |

## 数据完整性异常（必须先修）

1. **4 笔真实 CLOB fill 不在 `clob_fills.jsonl`，`fact_trades` 中 tmax 策略 0 行**。
   该实例未接入 clob fill sync。修复前禁止发布该策略任何 live_real PnL；coverage gate 现在根本看不见它。
2. `latest_blocked.json`（13:03 IST 轮）state 行 `current_native=35.0`，落后实况约 1 个 METAR（36 已打印）。
   VILK 30 分钟 cadence + snapshot 约 17 分钟节奏，决策输入天然滞后 30-50 分钟，临峰窗口内影响不可忽略。

## 事实时间线（IST）

市场结构：Lucknow 是**整数度 exact bracket**（…35 / 36 / 37 / … / 42+）。"36 NO" = 赌最终 Tmax ≠ 36
（低于 **或高于** 都赢），不是"不会升到 36"。

| 时间 | 事件 | 模型 | 动作 |
|---|---|---|---|
| 早间 | GFS forecast_max 41.3°C，peak 13:00；实况 34→35 | | |
| 11:18 | running max 35 | P(36-NO)=0.684 | BUY_NO 36 @0.65 ×5 |
| 11:28 | 同上 | P(36-NO)=0.701 | BUY_NO 36 @0.66 ×5（**重复**） |
| 11:30 | 同上 | P(36-NO)=0.701 | BUY_NO 36 @0.65 ×5（**重复**） |
| ~12:00 | 36 打印（fresh high） | | |
| 12:17 | trend_3h +3.6°F，距 peak −45min | P(36-YES)=0.586 | BUY_YES 36 @0.48 ×5（**同 bracket 反向**） |
| 13:03 | 36→35(-DZ)→36 | P(36-YES)=0.709，想 @0.61 加仓 | 仅被 fresh-ask drift guard 挡下 |
| ~13:30 | **37 打印**，此后仍 +1.8°F/h | | |
| 13:49 | 盘口：37-YES 0.56/0.61，38-YES 0.39/0.47 | | |

## 结论先行：交易动作

1. **仓位不用动，拿到结算。** 37 已打印 → 36-YES（5 股，−$2.40）已死；36-NO（15 股）按 METAR 口径已锁赢。
   若持有到结算且结算印 ≥37：净 **约 +$2.6（含费）**——今天这个 city-day 是**净赚的**，不是全错。
   若已按午间"留 YES 砍 NO"建议手动卖 NO，请把实际成交补记进来重算（那样今天才是真亏，亏损来源是手动干预）。
2. **tiny-live 暂停，修完两个执行 bug 再开**（修根因，不是加 gate）：
   a. 实现文档已写明的 `dedupe = first accepted per city + target_date`（runner 完全没实现，导致 3 倍 size + 反向单）；
   b. 下单前查本实例 journal 的既有持仓，同 bracket 已持反向时不下单（YES 0.48 + NO 0.65 = 1.13 > 1，锁定亏损）。
3. **`current_yes` 摘出 live expression set，回 shadow**，直到 E2 settlement-basis 层判定其符号
   （7/3 review 已书面判定"current_yes 符号不可判定，任何把它当真的动作都建立在删失伪影上"）。d1_no / d2_no 可留。
4. **把该实例接入 clob fill sync**，重建 fact_trades，coverage gate 过了才允许报 live PnL。

## 复盘：到底错在哪

**"表达全错"不成立。** 36-NO 是双边赌注：升到 37 恰好是 NO 赢的路径之一。今天模型 2 注 1 胜 1 负：
d1_no（p≈0.70）赢，current_yes（p=0.586）输。p=0.59 的单次落空毫无统计意义，**单日不能证伪模型**。

真正确认的问题按层级排：

**(1) 执行层 bug（确认，今天全部实际损失的来源）**
- runner 每轮独立重建候选并执行，无跨轮 city-day 去重 → 11:18/11:28/11:30 三笔重复 d1_no，size 3 倍于 policy；
- 无持仓感知 → 12:17 在持有 15 股 NO 的同一 bracket 买入 YES。若 dedupe 按文档实现，
  **今天的亏损腿（current_yes）根本不会存在**。

**(2) 部署纪律（确认）**
策略文档 7/5 更新明确写着 `conclusion = inconclusive_positive_signal / promotion = no live`、
promotion gates（10 个 settled forward dates、80 settled selected events 等）一项未过。
回测 +10.5%（CI [+0.8, +20.0]）本身被 review 标记为 backfill/rejoin 证据 + current_yes 删失伪影。
所以今天不是"回测正的策略 live 翻车"，是**未过 gate 的策略提前上 live，第一天就撞上已知的最大未决问题**。

**(3) 模型嫌疑（方向明确，但今天只算一个数据点）**
current_yes 在临峰前 45 分钟、fresh high 刚打印、trend_3h +3.6°F、GFS ceiling 高出当前档 5°C 的状态下
给出 58.6%→70.9% 的"停留当前档"概率，与 P1 分解（模型全部增量来自 actual=current 桶）和 extension 口径
（current_yes −40.3%，CI 上界 −35.2%）指向一致：**模型对"停在当前档"系统性过度自信，
剩余加热窗口 / forecast ceiling margin 特征没有起到应有的压制作用**。修法不是 hard filter，
是 E2 basis 层 + 检验 `forecast_peak_delta_hours_local`、remaining-heat 特征在模型里的实际权重。

**(4) 临场人工判断纪律（午间分析的教训）**
午间"留 36-YES、砍净 36-NO"把 exact bracket 当成 threshold 结构推理，且只信 GFS 的 peak 时间
却不信 GFS 的幅度（41.3）。在"还在升温 + 未过峰 + 预报天花板远高于当前档"时，净 NO 才是顺风腿。
CLAUDE.md §4 的临场三问（peak clock / 剩余加热窗口 / 路径状态）如实回答就能拦住这个结论。

## v2 补充：12:17 current_yes 信号的模型机制复盘

模型链路（`tmax_distribution_edge_live_candidate_v1.py` + P1/P3/P4 研究模块）：

```text
state 行以 running max 所在 bracket 为锚（current / d1 / d2 / tail 四桶）
→ 多项 LR（spec=loo_no_city_source：market probs + path + boundary + meteo + regime，
   无 city / forecast_source / city_family 特征；selected_c=0.03 强正则）
→ p_used = 0.5 × model + 0.5 × market（selected_alpha=0.5）
→ 四个表达取 max(p_win − fresh_ask)，过 0.02 edge 门就下单
```

12:17 的 flip 由四个机制叠加产生，**不是随机抽风**：

1. **锚点重定位**：36 打印后，bracket 36 从 d1 桶变成 current 桶。11:00 的 d1_no（P(final=36)≈0.30）
   和 12:17 的 current_yes（P(final=36)=0.59）是同一台机器在两个不同锚点状态下的输出。
   问题不在"模型自相矛盾"，在于策略层没有记忆：它不知道自己上一笔就在同一个物理 bracket 上。
2. **训练基率偏高 + 标签删失**：fit 集（8028 行）里"final = running max 桶"的基率本来就高，
   且 E2 删失问题（结算低于 METAR 的 1003 行被删，恰是 current_yes 必输行）把 p_current 进一步抬高。
3. **强正则把反向证据压扁**：C=0.03 的 LR 系数被强收缩，预测大头来自基率。
   当时两个最强的反向信号——peak_delta=−0.75h（未到峰）和 forecast gap=+5.3°C（GFS 41.3 天花板）——
   在模型里只有很小的权重。且 clean spec 刻意去掉了 source 特征，模型不知道这个 5.3°C 来自
   惯性热偏的 GFS 还是可信的 ECMWF，只能按训练集里"大 gap 的平均可信度"打折。
4. **市场混合减半了差距**：raw model P(36)≈0.72，市场≈0.45，混完 0.586；ask 0.48 → edge 0.106，
   是四个表达里最大的，于是选 current_yes。13:03 那轮 −DZ 回落 + running_max_age 变长 + 过峰
   让 p 升到 0.709（模型读成"plateau 确认"），只被 fresh-ask drift guard 挡住没有加仓。

**三种执行政策在今天这个 city-day 的对比（毛利，5 shares/单）**：

| 政策 | 今天的动作 | 结果 |
|---|---|---:|
| 回测口径（first-lock，每 city-day 第一单） | 1× d1_no 36@0.65 | **+$1.75（赢）** |
| 实际发生（无 dedupe，全部执行） | 3× d1_no + 1× current_yes | +$2.80（碰巧赢，靠重复的赢腿） |
| "以最新表达为准"（12:17 卖 NO 换 YES） | 平 NO@≈0.52 + 持 YES | **−$3.05（三者最差）** |

follow-latest 今天最差不是偶然的机制：runway 日每打一个新高，模型就会在新档位上重新报"停留"，
follow-latest 等于沿着梯子每一档都买一次 "stay"，全部输掉直到最后一档——P2 分解里
current_yes 在 actual=d1 时 ROI −100% 就是这个形态。回测测的从来是 first-lock，
follow-latest 从未被回测过；要评估它，用 shadow 的 `city_day_after_first_selected` blocked 行
做同分母 replay（含卖出点差 + 双边 taker fee），不要直接改 live。

**回测本身错没错**：`2026-07-05-tmax-distribution-candidate-policy-breakdown-v1.md` 内部一致、
自我标注诚实（conclusion=shadow_candidate / no live，forward=FAIL/NA）。它"没错但不覆盖"：
只回放每 city-day 第一条过门候选，没有建模日内后续反向信号，也就无法回答"要不要跟最新表达"；
且其 verified 标签继承了 current_yes 删失偏差。live runner 又没实现它假设的 dedupe——
所以今天 live 跑的政策和回测的政策不是同一个东西。

## 三道门声明

significance=NA baseline=NA forward=NA conclusion=inconclusive（单 city-day，无绩效结论；
本报告只输出执行 bug 修复 + 数据链修复 + expression set 回退动作，不含 keep/cut/size 判断）。

8 环覆盖：仅环 1（描述性，单日）与环 5（执行微结构：重复下单、反向对锁、obs 滞后）。
缺 2/3/4/6/7/8 —— 故不给任何模型层 live 动作。

## v3 审阅补充：表达层不要被 Lucknow 单例带偏

这次事故确认了 live runner 没有按回测的 `first city-day` 口径执行，但它**没有**证明
`d1_no` 本身是坏表达。Exact bracket 市场里，`36 NO` 的语义就是“最终不精确停在 36”，
它同时覆盖低于 36 和高于 36 两条路径；如果市场把 36 这个精确档位定贵了，`36 NO`
就是合理表达。Lucknow 今天升到 37 后，`36 NO` 赢，恰好说明这个补集表达可以是对的。

真正不干净的是两件事：

1. 现在的表达集只覆盖 `current YES / current NO / d1 NO / d2 NO`，没有对整条 ladder 的每个
   exact bracket 做统一比较；所以当模型认为“最终大概率落在 37”时，系统没有自然地表达
   `37 YES`，只能通过 `36 NO` 或后续重锚后的 `36 YES` 间接表达。
2. runner 把每轮最新 best expression 当成独立新机会，没有“目标仓位”概念。于是同一个
   city-day 先买 NO，后买 YES，变成执行层自相矛盾。

因此，更合理的表达不是简单“删 current_yes”或“把 d1_no 换成 d1_yes”，而是：

```text
预测整条 exact-bracket 分布
→ 对每个 bracket 的 YES/NO 都计算 fee/spread 后 EV
→ 生成 city-day 级 target book
→ 只有当新 target book 的增量 EV 覆盖平仓点差、双边 fee 和逆选择缓冲时才调仓
```

在这个架构出来前，`current_yes` 只能保留在 shadow/replay 里验证 basis 和调仓成本；
若恢复任何 tiny live，也必须执行 `first city-day lock`，不能 follow latest expression。
