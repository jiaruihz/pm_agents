# Tmin / Tmax 下一代 Alpha：GPT Pro 独立审阅包 v1

日期：2026-08-28  
用途：把当前 Tmin 两条策略、下一代 challenger、Tmax 跨策略族验证和已知数据问题一次性交给独立模型审阅。  
状态：研究咨询材料；不改变任何 live、shadow、frozen 或生产配置。

---

## 一、请 GPT Pro 最终回答什么

请不要泛泛建议“多收集数据、加特征、换复杂模型”。请基于本文固定证据，回答：

1. 当前关于两条 Tmin 策略的结论是否成立，哪里存在逻辑跳跃？
2. 每条策略的问题分别来自：概率模型、market baseline、候选 selector、交易表达、执行价格、覆盖率，还是样本选择？
3. `no-further-cooling` 应继续作为概率 challenger、重新设计成覆盖率更高的交易策略，还是停止投入？
4. `cross-prev-NO + 60m exit` 是 Tmin 特有的短期 repricing 机制，还是 first-city-day 选择造成的样本假象？
5. Tmax 上固定 60 分钟退出失败，对 Tmin 结论能否构成反证？如果不能，Tmin 特异性的因果解释和验证门槛是什么？
6. 最多提出 3 个下一步可证伪、可做 PIT 回放、数据基本具备的 alpha 假设，并按以下维度排序：
   - 预期信息增益；
   - 实现成本；
   - 数据就绪度；
   - 过拟合风险；
   - 即使失败仍可复用的基础设施价值。

允许且欢迎最终结论是：**当前没有可信的新 alpha，暂时不应继续做交易策略升级。**

---

## 二、先给结论摘要

### 2.1 当前两条 Tmin 策略都不支持 tiny live

- 两条 incumbent 都没有通过“模型相对同一时点 market 的概率基准 + 同分母交易基准 + prospective frozen forward + 可执行性”完整门槛。
- 两个下一代 challenger 只具备从 `target_date=2026-08-28` 开始做零资金 frozen forward 的资格；生成 artifact 时 forward 行数均为 0，尚未开始形成新样本。
- 本文涉及的 challenger 均没有真实订单或真实 fill。

### 2.2 `no-further-cooling` 的 19/19 胜率不等于模型有 alpha

Incumbent 在精选交易上：

- 19 笔 settled trade，19 胜 0 负；
- 5-share 总成本 `$89.85885325`；
- fee-adjusted PnL `+$5.14114675`；
- ROI `+5.721%`，按 target_date bootstrap CI `[+2.145%, +14.065%]`。

但在全部同分母的 111 个 settled probability rows 上：

- market logloss / Brier：`0.2636746 / 0.0845018`；
- model logloss / Brier：`0.2696043 / 0.0918782`；
- model-minus-market 日期等权 delta：
  - logloss `+0.0204649`，CI `[-0.04413, +0.10560]`；
  - Brier `+0.0127953`，CI `[-0.01051, +0.04315]`。

也就是说，模型整体概率质量没有胜过同一时点 market，点估计反而更差。19/19 很大程度是 selector 选中了 market 本身就认为胜率很高的容易样本，不能证明物理模型提供了增量信息。

此外结果集中度很高：Tokyo 2026-08-21 的 `YES@0.49` 单笔贡献总 PnL 的 48.38%；去掉最大赢家后 ROI 为 `+3.038%`。若额外加入 1 笔同成本量级 binary loss，5-share PnL 只剩约 `+$0.1411`、ROI `+0.157%`；再多 1 笔则约 `-5.41%`。

### 2.3 no-further challenger 改善了概率点估计，却砍掉了约 90% 的交易

下一代概率策略只在两个物理机制更合理的窗口加入很小的物理 residual：

```text
p = logistic(
  logit(p_market)
  + 0.10 * physical_innovation_logit
    * I(window in {morning_cooling, post_sunrise_provisional_low})
)
```

其他窗口直接返回 market probability。

开发集 111 rows / 15 dates：

- challenger logloss / Brier：`0.2600638 / 0.0840691`；
- market：`0.2636746 / 0.0845018`；
- challenger-minus-market：
  - logloss `-0.0041362`，CI `[-0.010126, +0.001412]`；
  - Brier `-0.0005059`，CI `[-0.002484, +0.001475]`。

方向上比 market 好，但两个总体 CI 都跨 0，尚未证明稳定增量。相对 incumbent：

- logloss delta `-0.0246012`；
- Brier delta `-0.0133012`；

二者 CI 也跨 0。

交易 replay 只有 2 笔 / 2 dates：

- 成本 `$9.380375`；
- PnL `+$0.619625`；
- ROI `+6.61%`。

因此它可能是更合理的**概率校准 challenger**，但不是已证明更好的**交易策略**：交易数从 19 降至 2（`-89.5%`），绝对 PnL 从 `+$5.14` 降至 `+$0.62`（`-87.9%`）。不能因为 ROI 较高就称其升级。

### 2.4 cross 的 +60m 结果只在 first-city-day 子集为正

`cross-prev-NO` incumbent 的严格可执行分母：

- 21 rows / 10 dates；
- 18 胜 3 负；
- 成本 `$94.8244525`；
- PnL `-$4.8244525`；
- ROI `-5.0878%`，CI `[-15.967%, +7.336%]`。

下一代 `first-city-day + exit60m` challenger：

- 15 entries / 11 dates；
- 12 positive / 3 negative；
- 成本 `$71.09455225`；
- PnL `+$3.80045575`；
- ROI `+5.3456%`，CI `[+1.2658%, +13.1996%]`；
- 15/15 有可执行 exit。

但同样的 60m 退出放到 all-entry 分母：

- 23 rows；
- 成本 `$104.6293`；
- PnL `-$8.16788`；
- ROI `-7.806%`，CI `[-17.777%, +6.381%]`。

所以正收益不能简单归因于“60 分钟退出有效”。它高度依赖 first-city-day dedupe/selection；真正待验证的机制可能是“首次 source cross 更有短期信息”，而不是固定时钟退出。

### 2.5 Tmax 跨策略族验证不支持通用 fixed-60m taker exit

我们把同一个固定策略、不重新调参地用于已有较多数据的 Tmax：

- entry：taker ask；
- exit：事件后第一个 `+60..+72m`、fresh、full-size 可成交 bid；
- entry/exit 双边使用官方 Weather fee；
- quote age `<=300s`；
- 缺失 exit 保留在分母；
- 对比同一 settled rows 的 HOLD。

结果：

| 分支 | 信号 / exit | entry cash | fixed-60 all-exit PnL / ROI | 同行 settled fixed vs HOLD |
|---|---:|---:|---:|---:|
| Tmax fast-source 全量 | 279 / 267 | `$1,256.01225` | `-$2.9916 / -0.238%` | `-$1.58875` vs `+$10.43815` |
| Tmax fast-source 8/13 后 | 86 / 82 | `$384.5072` | `-$3.5493 / -0.923%` | `-$2.14645` vs `+$6.9432` |
| Tmax normal Core 全量 | 112 / 70 | `$635.51588` | `+$0.084226 / +0.013%` | `-$0.913404` vs `+$18.171889` |
| Tmax normal Core 8/13 后 | 44 / 43 | 约 `$400` | `-$7.84858 / -1.962%` | `-$8.8462` vs `-$1.30842` |

Normal Core 早期的 fixed-60 点估计为 `+$7.9328 / +3.368%`，但只有 27/68 exit coverage，且同行 HOLD 为 `+$19.4803`，fixed-minus-HOLD 仍是 `-$11.5475`。正点估计不能越过 coverage bias 和基准劣势。

跨族结论是：**通用固定 60 分钟 taker exit 不合格。** 它有时能提前避开 settlement loser，但也会在短期回撤时卖掉最终 winner，并额外支付 spread 和第二次 fee。

这不严格证明 Tmin 特异性机制一定不存在；但若要继续主张 Tmin 有效，必须明确说明 Tmin 为何会在 cross 后产生不同于 Tmax 的短期 repricing，并在新 frozen forward 上验证。不能再仅凭开发集的正 ROI 推断。

---

## 三、不可放宽的研究合同

请按以下口径审阅，不要用 accuracy 或 selected trades ROI 替代完整证据：

1. **概率层**：model 必须在同一 row、同一 PIT 时点上与 market probability 比较 logloss 和 Brier；优先按 `target_date` 等权并做 block bootstrap。
2. **交易层**：策略必须与同一 opportunity universe 的基准比较；报告绝对 PnL、投入成本、交易数、capital efficiency、集中度和不确定性，不只看 ROI。
3. **双漏斗**：严格区分 signal funnel 和 evidence funnel。没有盘口证据不能假设成交，也不能静默丢弃后只报告 survivors。
4. **执行层**：使用时点可得的 direct quote、深度、freshness、完整 5/10-share fillability；taker 退出计第二次 fee 和 spread。
5. **PIT**：所有特征、market、source、official 数据只使用决策时点已知信息；cross 不能被当作 settlement truth。
6. **训练/验证**：开发集只用于构建；从 `2026-08-28` 起的 prospective frozen forward 不得调阈值、改 selector 或偷看后再冻结。
7. **晋级**：开发集点估计、selected trade 胜率、少数城市或事后窗口都不能直接升级 tiny live。
8. **交易效用约束**：概率分数改善但交易从 19 降到 2，不能自动称为更好的交易策略；至少要证明在可比 universe 下的绝对期望 PnL、资本效率或风险调整结果更好。

---

## 四、数据边界与训练/验证状态

### 4.1 no-further-cooling

- checkpoints：655；
- target dates：16，`2026-08-12..2026-08-27`；
- scored rows：115；
- same-row settled probability rows：111 / 15 dates；
- incumbent selected：20；settled：19 / 12 active trade dates；
- cities：HongKong、London、Miami、NYC、Paris、Seoul、Shanghai、Tokyo。

现有 111 rows 全部属于 development/reset 证据，不是独立未来验证。下一代 challenger frozen start 是 `2026-08-28`，artifact 生成时为 0 forward rows。

### 4.2 cross-prev-NO

- strict cross candidates：40 / 14 dates，城市 Seoul、Tokyo；
- closed labels：36；
- direct asks：26；
- underlying freshness `<=300s`：39；
- ask 且 5-share 深度足够：23；
- settled + 5-share executable：21 / 10 dates。

60m challenger 的开发 replay 仍来自历史已有数据；其 frozen start 同为 `2026-08-28`，artifact 生成时 forward rows 为 0。

### 4.3 数据修复和当前真值

- 最近一次有界 refresh 补回 174 个 PM history 文件，并新增 1,914 条 `settlement_outcomes`；canonical settlement 覆盖到 `2026-08-27`。
- production manifest strict 检查退出码为 0；DB route 健康，physical DB identity 为 device/inode `16777244/54444`。
- CLOB fill coverage gate 已通过。
- 研究 challenger 没有真实 orders/fills；本文交易结果均为 PIT/replay 或 counterfactual execution。

---

## 五、策略 A：Tmin no-further-cooling

### 5.1 Incumbent 身份和直觉

- strategy：`weather.tmin.no_further_cooling`
- instance：`weather_tmin_no_further_cooling_shadow_v1`
- model：`tmin_market_plus_physical_innovation_v1`
- model artifact SHA：`969bbbf00b29f57756890ed5e2faa6e3d0ccdc070c83000b76a665d5207424dd`
- 当前为 zero-notional。

大白话直觉：夜间温度已经降到某个低点，如果剩余夜间条件看起来“不太可能继续明显变冷”，就提高当前最低温对应 bracket 的胜率并买入。

问题是：selector 很容易只在 market 已经高度确信的候选上交易。这样能产生极高 accuracy，却不能说明物理 residual 比 market 更懂结算概率。

### 5.2 Incumbent 交易证据

| 指标 | 结果 |
|---|---:|
| settled trades | 19 |
| wins / losses | 19 / 0 |
| 5-share cost | `$89.85885325` |
| fee-adjusted PnL | `+$5.14114675` |
| ROI | `+5.721%` |
| target-date bootstrap CI | `[+2.145%, +14.065%]` |
| static PIT 5-share depth | 19 / 19 |
| actual fills | 0 |

主要脆弱性：

- Tokyo 8/21 一笔贡献 48.38% 总 PnL；
- 去掉最大赢家 ROI 仍为 `+3.038%`，但利润规模更薄；
- 加一笔同量级 binary loss 后几乎抹平全部利润；
- static PIT full-book 深度不等于 post-decision 实际成交。

### 5.3 Incumbent 概率证据

| Metric | Market | Model | Model - Market date-equal delta |
|---|---:|---:|---:|
| logloss | `0.2636746` | `0.2696043` | `+0.0204649`, CI `[-0.04413,+0.10560]` |
| Brier | `0.0845018` | `0.0918782` | `+0.0127953`, CI `[-0.01051,+0.04315]` |

结论：baseline FAIL。19/19 不能覆盖同分母概率表现更差这一事实。

### 5.4 下一代 challenger

名称：`tmin_no_further_cooling_window_routed_alpha010_v1`

关键改动：

- 不再在所有时段都相信 physical innovation；
- 只在 `morning_cooling` 和 `post_sunrise_provisional_low` 两个更有物理含义的窗口加小权重 `0.10`；
- 其他时段直接回到 market；
- 目标是先证明概率 residual，而不是靠高阈值筛少量交易。

开发集表现：

| Metric | Challenger | Market | Challenger - Market |
|---|---:|---:|---:|
| logloss | `0.2600638` | `0.2636746` | `-0.0041362`, CI `[-0.010126,+0.001412]` |
| Brier | `0.0840691` | `0.0845018` | `-0.0005059`, CI `[-0.002484,+0.001475]` |

相对 incumbent：logloss `-0.0246012`、Brier `-0.0133012`，但 CI 仍跨 0。early 和 late split 都是改善点估计；只有 early logloss CI 严格低于 0，不能据此宣称总体成功。

交易结果只有 2 笔、`+$0.619625`，所以它目前应被视为**概率 challenger**，不是 incumbent 交易策略替代品。

### 5.5 搜索和 forward 约束

- routed alpha 比较过 `{0.10, 0.25, 0.50}`；
- incumbent 历史上还使用过 5-alpha grid；
- 当前未做 multiplicity-adjusted significance claim；
- prospective frozen forward 从 `2026-08-28` 起；
- 建议最低观察门槛：至少 30 个新 settled target dates；
- 概率晋级要求 challenger-minus-market 的 logloss、Brier CI upper bound 都 `<0`；
- 交易晋级另需 fee、深度、覆盖率、实际或 post-decision execution evidence。

### 5.6 请重点审阅

1. window routing 是否有真正的物理先验，还是对现有 15 天的事后切片？
2. alpha=0.10 是否只是收缩到 market 后自然改善，而非新增信息？
3. 应如何在保持连续概率评分的同时，设计不把 19 笔砍成 2 笔的交易表达？
4. 是否应将概率研究和 trade selector 完全拆开，先证明 residual，再优化同分母效用？

---

## 六、策略 B：Tmin source cross previous-bracket NO

### 6.1 Incumbent 机制

- strategy：`weather.tmin.cross_prev_no`
- 触发：alternate/fast source 首次跨过档位边界；
- 表达：买前一个更暖 exact bracket 的 NO；
- 正确语义：source cross 是事件，不是 settlement truth。

大白话直觉：一个更快的温度源先显示“已经跌穿上一档”，市场可能还没来得及给上一档 NO 充分涨价，于是吃短期 repricing。

主要风险：不同 source 与最终 settlement native source 存在城市和时段相关 basis。比如 Seoul alternate source 经常比 settlement/METAR 低约 `0.6°C`；“快源跨档”并不代表官方结算一定跨档。

### 6.2 Incumbent 结果

全量严格可执行分母：

| 指标 | 结果 |
|---|---:|
| rows / dates | 21 / 10 |
| wins / losses | 18 / 3 |
| cost | `$94.8244525` |
| PnL | `-$4.8244525` |
| ROI | `-5.0878%` |
| CI | `[-15.967%, +7.336%]` |

`cap90 + first eligible city-day` incumbent：

- 4 rows / 4 dates；
- 3 胜 1 负；
- cost `$14.77235`；
- PnL `+$0.22765`；
- ROI `+1.541%`；
- CI `[-69.868%, +72.907%]`。

历史 METAR/basis settlement probability challenger 在 36 labels 上：

- model logloss / Brier：`0.38239 / 0.11846`；
- market：`0.27349 / 0.09247`；
- 未冻结。

### 6.3 已修正的历史问题

1. 旧 51-row journal 混入 legacy 语义，不是 strict-cross denominator，已 reset。
2. Seoul 8/19 的 `NO@0.53` 使用了内部 stale book，quote stale `13,559s`，约 3 小时 46 分；这笔曾虚增 `+$2.2877`。
3. 修正后：
   - cap90 first-city-day 从 5 rows 降至 4，ROI 从 `14.39%` 降至 `1.54%`；
   - all executable ROI 从 `-2.60%` 降至 `-5.09%`；
   - 0 real fills 受影响，因为这些均为研究 replay。
4. 当前 exact quote 重复键已 fail closed；candidate schema 和价格边界有显式校验。

### 6.4 下一代 +60m repricing challenger

名称：`tmin_cross_prev_no_first_cityday_exit60m_v1`

固定规则：

- 每个 city,target_date 只取第一个 strict executable cross；
- entry：fresh direct NO ask，taker，5 shares；
- exit：`+60..+72m` 内第一个 fresh、full 5-share bid，taker；
- entry 和 exit 都计官方 Weather fee；
- quote age `<=300s`；
- 不使用 price cap。

开发集结果：

| 指标 | First-city-day +60m | All-entry +60m |
|---|---:|---:|
| rows | 15 | 23 |
| dates | 11 | — |
| positive / negative | 12 / 3 | — |
| cost | `$71.09455225` | `$104.6293` |
| PnL | `+$3.80045575` | `-$8.16788` |
| ROI | `+5.3456%` | `-7.806%` |
| CI | `[+1.2658%, +13.1996%]` | `[-17.777%, +6.381%]` |
| exit coverage | 15 / 15 | — |

城市切片：

- Seoul：10 rows，ROI `+1.849%`；
- Tokyo：5 rows，ROI `+13.11%`。

### 6.5 搜索和 selection 风险

开发中比较了 5 个 horizon × 2 个 cohort，共 10 个组合：

- horizon：10、30、60、120、240 分钟；
- cohort：all-entry、first-city-day。

30/60/120/240 的 first-city-day CI lower bound 为正，但 30m 少 1 个 exit；60m 被选为最早完整 horizon。120m 与 240m 的 first-city-day 结果都约 `+5.35%`，出现平台，说明精确 60m 未必是机制本身。

当前没有 multiplicity-adjusted 显著性声明。开发窗口不能验证开发中选出的 horizon/cohort。

### 6.6 请重点审阅

1. 正收益究竟来自首次 cross 的信息含量、事件后 repricing，还是事后选 cohort/horizon？
2. all-entry 为负，是否说明固定退出不是核心机制，而 repeated crosses 本身质量低？
3. first-city-day 是否是合理的独立事件 grain，还是人为删掉坏交易？
4. 是否应该建两个 head：
   - `P(short-term repricing | source event, market state)`；
   - `P(final settlement | source basis, official evidence)`；
   分别服务 exit 和 hold，而不是用一个概率混在一起？

---

## 七、Tmax 固定 60 分钟退出验证

### 7.1 为什么做

用户提出了一个合理质疑：如果“积累事件后固定 60 分钟退出”真是普遍有效的交易机制，那么在已有大量 real-time、fast-source、normal/Core 数据的 Tmax 上也应该有迹象。于是执行了不重新调参的跨策略族验证。

科学上需要区分：

- Tmax 失败足以否定“fixed-60 是通用 alpha”；
- Tmax 失败不足以单独否定“它是 Tmin source-cross 特有 alpha”；
- 若主张后者，必须提出可检验的 Tmin 特异性因果机制，而不是把失败解释成“两个策略不同”。

### 7.2 固定政策

Fast-source：每个 city-day 第一个有效 v2/v3 previous-bracket NO，5 shares。  
Normal Core：使用 frozen current-YES selections，保留原 5/10 shares。

两边统一：

- entry taker ask；
- exit 使用 `+60..+72m` 第一个 fresh、full-size bid；
- 两腿官方 fee；
- quote age `<=300s`；
- missing exit 留在 denominator；
- actual orders/fills = 0。

### 7.3 数据规模和结果

Fast-source 全量：

- 279 signals，267 exits（95.7%），266 settled；
- entry cash `$1,256.01225`；gross two-leg quote notional `$2,507.81`；
- fixed all-exit PnL `-$2.9916`，ROI `-0.238%`；
- same-settled fixed `-$1.58875`，HOLD `+$10.43815`；delta `-$12.0269`。

Fast-source 8/13 后：

- 86 signals，82 exits，81 settled；
- entry cash `$384.5072`；gross notional `$765.06`；
- fixed all-exit `-$3.5493`，ROI `-0.923%`，CI `[-3.612%,+1.455%]`；
- same-settled fixed `-$2.14645`，HOLD `+$6.9432`；delta `-$9.08965`。

Normal Core 全量：

- 112 signals，70 exits（62.5%），69 settled；
- entry cash `$635.51588`；gross notional `$1,270.3524`；
- fixed all-exit `+$0.084226`，ROI `+0.013%`；
- same-settled fixed `-$0.913404`，HOLD `+$18.171889`；delta `-$19.085293`。

Normal Core 8/13 后：

- 44 signals，43 exits，42 settled；
- entry cash 约 `$400`；gross notional `$791.78127`；
- fixed all-exit `-$7.84858`，ROI `-1.962%`，CI `[-7.248%,+2.577%]`；
- same-settled fixed `-$8.8462`，HOLD `-$1.30842`；delta `-$7.53778`。

### 7.4 判定

- significance：FAIL；
- same-row baseline：FAIL；
- forward：`FAIL_not_pristine_forward`；
- status：`inconclusive_do_not_promote_general_fixed_taker_exit_keep_existing_hold_semantics`；
- live change：none。

更精确的表述是：fixed-60 尚不能被证明在所有环境永远有害，因为 CI 跨 0 且少数大亏损会影响点估计；但它已明确不具备作为通用退出规则晋级的资格。

---

## 八、请审阅的候选研究方向

下面只是待审假设，不是预设答案。请最多保留 3 个；可以全部否决。

### A. 覆盖率约束下的连续 no-further probability residual

目标：仍对全部 checkpoints 连续评分，先证明 physical residual 在 market 之外有信息；交易层另设明确的 trade-count、absolute PnL、capital-use 约束，避免“分数略好但交易从 19 变 2”。

关键问题：能否不用事后窗口硬 gate，而用连续的 remaining-cooling / reheat / official-observation 状态与 market-offset shrinkage，保持覆盖率并改善 proper score？

### B. Cross 双头模型

分别估计：

1. `P(未来 30-120m 出现可执行 repricing | source event, market microstate)`；
2. `P(final settlement | alternate-to-official basis, physical state, market)`。

第一个决定短期退出/跳过，第二个决定是否持有到 settlement；避免把 alternate-source cross 当成结算真相。

### C. 状态依赖的 optimal stopping，而不是 clock-only exit

退出由事件状态决定，而非到 60 分钟一刀切。候选状态包括：

- cross 是否被 official/normal source 确认；
- remaining cooling/heating 和 reheat；
- alternate-official basis；
- market residual；
- current/adjacent bracket 的 bid/ask、spread、depth 和 support survival。

必须在完全相同 rows 上同时比较：HOLD、fixed-60、state-dependent exit；不能让缺失 exit 或 selector 改变分母。

### D. 城市/来源 basis 的 hierarchical model

用 prior-date OOF 学 city × source × native-lattice 的 basis，并加入 local time、cross rank、cooling window；模型以 market probability 为 offset 做强收缩。目标不是直接猜结果，而是校正“快源跨档并不等于官方跨档”。

### E. 真正的执行/微观结构 alpha

研究 current 与 adjacent bracket 的订单流、spread/depth、support survival、maker/taker/skip；但原始 CLOB WS/tape 必须先完成 deterministic reconstruction、snapshot parity 和 order lifecycle 对账，否则不能作为模型证据。

### F. 从单一 ROI 改为效用 frontier

联合报告并优化：

- proper-score gain；
- 每 100 city-days 的绝对 PnL；
- trade count；
- capital deployed / turnover；
- concentration / drawdown；
- fee 和 missing-execution stress。

目的是防止“高胜率、极少交易、很小绝对利润”或“ROI 高但机会被砍光”被误判为升级。

---

## 九、GPT Pro 必须给出的输出格式

请按下面格式作答，先结论，后证据：

### 1. 总 verdict

- 当前两条策略各自是：有弱证据 / inconclusive / 应停止 / 数据不足；
- 是否存在任何已经达到 tiny live 的候选；
- 当前研究团队最重要的一个逻辑错误或盲区。

### 2. 分层诊断表

对 `no-further-cooling`、`cross-prev-NO`、`cross +60m` 分别标注：

- probability/model；
- market baseline；
- selector；
- trade expression；
- execution/fees；
- coverage/missingness；
- sample size/multiple testing；
- frozen-forward readiness。

每一项给 PASS / FAIL / UNKNOWN 和一句理由。

### 3. 对关键争议的直接裁决

- 为什么 19/19 胜率不能单独晋级？是否有任何条件下可以把它视为有效 alpha？
- 19→2 笔的 challenger 到底是模型升级还是交易退化？
- first-city-day +60m 的正收益更可能来自什么？
- Tmax 失败对 Tmin 命题提供了多强的反证？

### 4. 最多 3 个下一步假设

每个假设必须完整给出：

1. 一句话因果机制；
2. target / grain / label / PIT clocks；
3. 精确 feature set，哪些允许、哪些禁止；
4. model form，以及为何不只是更复杂；
5. baseline arms；
6. development、prior-date OOF、prospective frozen 划分；
7. selector 与 execution policy；
8. fee、spread、depth、missing-exit 假设；
9. 最低样本门槛和 target-date bootstrap 方法；
10. promotion gate；
11. falsification / stop condition；
12. 如何证明不是 threshold/window/horizon mining；
13. 若假设失败，哪些数据和代码仍值得保留。

### 5. 最终推荐

- 给一个主推荐和一个 fallback；
- 明确说先做什么、不做什么；
- 明确是否应保持现有 HOLD semantics；
- 不建议 live 部署，不在 forward 上调参。

---

## 十、可复核 artifact 与代码

### 10.1 Formal artifacts

- no-further challenger：  
  `/Volumes/jrs-archive/pm_agents/research/artifact_store/tmin_no_further_cooling_shadow_performance_v1/2026-08-27-challenger-freeze/summary.json`
- cross challenger：  
  `/Volumes/jrs-archive/pm_agents/research/artifact_store/tmin_cross_prev_no_performance/2026-08-27-challenger-freeze/summary.json`
- Tmax fixed-60 validation：  
  `/Volumes/jrs-archive/pm_agents/research/artifact_store/active/tmax_fixed_60m_taker_exit_v1/tmax_fixed60_20260828_v3/result.json`

### 10.2 关键输入与可复现 hash

Tmax：

- fast events：972，SHA `5ee48e1ab08af026a88d5f9a7c0e6a34ec722b4976ede97ecb1ad4b507a5445b`
- fast quotes：18,836，SHA `f3dbd9586a415765af44ab8c7ec95637aa2cf8215057616a2378f0ebf11d4df0`
- Core entries：6,256，SHA `a0ca244173e73ef6553b3935b2cea3ccb7c5009c8eb08447cd7025c7759c61a4`
- market book files：5,846；token coverage 112/112；matched book rows 36,649。
- evaluator SHA：`94fdc6130888d887c72ed872d6527fc9e8e9eeb1f8314336b38762172feb48ff`
- fast positions CSV SHA：`9602eea487a1ac2770ea9c534b6fd152fbd4fc92dfe64c9cdd541e539590a02c`
- normal Core CSV SHA：`d3172cf70f98cfa192fac318d4ccd9c98da10125b454ccdf79644dc283268065`

### 10.3 当前实现已修的审阅问题

Tmin evaluator：

- exact quote 重复 key 不再静默覆盖，改为 fail closed；
- no-further forward selection 已绑定 exact PIT execution book evidence；
- 要求 book available、ask match、5-share fillable；
- candidate schema 和 price boundary 显式校验。

Tmax evaluator：

- nonbinary settlement fail closed；
- Core quantity 限制为 5/10；
- artifact 记录 evaluator/helper/git/CSV hashes；
- denominator 展示问题已修正。

---

## 十一、给 GPT Pro 的最后约束

请把这视为一次独立反方审阅，不要替现有研究辩护。重点寻找：

- denominator 是否偷偷改变；
- market baseline 是否同 row、同时间；
- selector 是否制造高胜率；
- missing quote/exit 是否产生 survivor bias；
- first-city-day 和 60m 是否来自 multiple testing；
- 物理机制是否真的能在 settlement-native lattice 上成立；
- 改善 probability 是否能转化为足够规模的交易效用；
- 哪个新实验最可能快速证伪错误方向。

如果不能构造一个在新 frozen data 上可清晰失败的假设，就不要推荐它。
