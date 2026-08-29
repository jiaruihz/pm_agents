# Weather Intraday Decision Casebook

Status: current-reference
Updated: 2026-08-29 San Francisco near-Core maker no-fill case
Scope: 实时天气判断、用户与 Codex 的结论更新、PIT 数据快照、订单/成交血缘

## 结论与动作

2026-07-20 三例中，**2026-07-22 唯一优先复刻 Lucknow 型 no-reheat / current-exact YES**：当前档已经打印，
forecast peak 已过，温度路径长时间不创新高并回落，雨云、高湿和弱混合持续压制剩余加热。若明日没有同类
city-day，就不为了完成实验而交易。

这条最适合最终进入策略代码，但明日仍采用 **代码扫描与记录、人工复核和决定** 的 human-in-the-loop 形态，
只维持既有 tiny-probe 规模，不扩仓、不改 live policy。Busan 继续只作 fast-source feature/collector；Ankara
保留为 thesis invalidation/reversal 的人工训练例，暂不自动化反手。

三例只能证明这套工作流值得继续，不能证明 alpha confirmed。Ankara 必须按层记账：`34` condition 含自动入场后
净亏，但人工操作集合和整个 `33/34/35` event 均盈利；不得再把单一 condition 当成整场事件。目标仍是：

```text
估计 P(final exact bracket | PIT weather/path/source state)，
并检验相对同一时点 executable market probability 的 residual。
```

## 冻结证据

2026-07-20 三例已经冻结为可复跑证据包：

| artifact | rows | 内容 |
|---|---:|---|
| [interaction_timeline.jsonl](analysis/2026-07/generated/intraday_decision_casebook_v1/interaction_timeline.jsonl) | 142 | 用户与 Codex 可见消息；保留时间戳、role、原 session 路径和行号 |
| [case_state_snapshots.jsonl](analysis/2026-07/generated/intraday_decision_casebook_v1/case_state_snapshots.jsonl) | 385 | Busan AMOS/book replay、Lucknow `weather_state`、Ankara d1 与跨 runner state |
| [orders_and_fills.jsonl](analysis/2026-07/generated/intraday_decision_casebook_v1/orders_and_fills.jsonl) | 55 | Lucknow/Ankara raw order lifecycle、三城 canonical `fact_trades`、Ankara event-wide public activity、closed positions 与 final resolution |
| [ankara_event_account_activity.jsonl](analysis/2026-07/generated/intraday_decision_casebook_v1/ankara_event_account_activity.jsonl) | 31 | Poly proxy wallet 在 Ankara 7/20 event 的完整分页 activity；覆盖 33/34/35 三档、BUY/SELL/REDEEM |
| [evidence_manifest.json](analysis/2026-07/generated/intraday_decision_casebook_v1/evidence_manifest.json) | 1 | 输入文件 hash/mtime、产物 hash、canonical cutoff 和已知缺口 |

复现：

```bash
.venv/bin/python scripts/analysis/reheat_risk/build_intraday_decision_casebook_v1.py
```

归档只保留 user-visible user/assistant messages；system/developer/tool output 与 hidden reasoning 不进入 casebook。
这不是信息缺失，而是把“人与模型如何更新结论”与执行环境内部记录分开。

## Current-YES 气象语义 residual case library

临场 golden cases 之外，current-YES core 现在另有一套可重复生成的统计 case library：

- [语义 residual case library 报告](analysis/2026-07/2026-07-29-current-yes-semantic-residual-case-library-v1.md)
- [全量 checkpoint memberships](analysis/2026-07/generated/current_yes_semantic_residual_case_library_v1/checkpoint_case_library.csv)
- [首次机制 city-day 统计分母](analysis/2026-07/generated/current_yes_semantic_residual_case_library_v1/first_mechanism_city_day_cases.csv)
- [机制 proper-score scorecard](analysis/2026-07/generated/current_yes_semantic_residual_case_library_v1/mechanism_scorecard.csv)
- [高置信 tail-loss 展示集](analysis/2026-07/generated/current_yes_semantic_residual_case_library_v1/representative_tail_loss_cases.csv)

它固定保存当时 current-YES bid/ask/mid、market implied tail odds、core probability、最终 exact-bracket
结算、物理机制和 core-vs-market proper-score。统计分母只取每个
`(mechanism, city, target_date)` 的首次机制 checkpoint；按最意外时点展示的案例不能回流成统计分母。

首版覆盖 1,349 checkpoint、702 个机制首次 city-day membership、31 个 target dates。59 个高置信
tail-loss membership 中，只有 1 个是 core 比市场至少多 3pp 的明确过度自信；31 个是双方共同高置信错，
27 个是市场比 core 更过度自信或只有市场达到高置信。当前没有一个机制 family 在 target-date bootstrap
Brier 上显著差于 market；因此这些案例主要维护为 shared-tail/common-information-blind-spot 证据，
不能笼统写成“core 常识错误”。

复现：

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_physical_semantic_audit_v2.py
.venv/bin/python scripts/analysis/reheat_risk/build_current_yes_semantic_residual_case_library_v1.py
```

## Case 1 — Busan 31 NO：快源短刺与最终结算不是同一个 label

### 全过程

- 12:49 Busan local，AMOS 连续越过 31 档后，`31 NO` 成交 `10 @0.85 taker + 5 @0.84 maker`。
- 用户在 14:23 local 看到浮亏后开始实时复盘；我们逐轮检查 AMOS、routine METAR、WU、forecast peak 与盘口。
- 上午 12:50 AMOS 短暂到 32.0，但报文前回到 31.0；13:00 routine METAR 仍报 31，属于
  `next-report transient false`。
- 下午 15:45 后 AMOS 跨档并持续保留；16:00 routine METAR 报 32，WU running max 随后也到 32，
  `31 NO` 的最终天气方向获胜。

完整单日血缘见 [Busan 31 NO lineage](analysis/2026-07/2026-07-20-busan-31-no-live-lineage-v1.md)。

### 沉淀

必须分别估计：

```text
P(next routine METAR cross | source path, report clock, market path)
P(final WU leaves bracket | official max, remaining heat, forecast/path state)
```

terminal retention、drawdown、report clock 与盘口持续收敛是概率特征，不是新的 hard gate。Busan AMOS 与 WU
已有 source-to-settlement basis 风险，且正确事件往往不可成交、错误事件反而容易成交；因此不作为明日复刻的
live 操作。

## Case 2 — Lucknow 27 YES：最值得复刻的单腿 no-reheat

### 事前状态与成交

H2 runner 在 13:13 local 的 PIT snapshot 识别到：

- 当前 26°C、当天已经打印 27°C，因此 `27 YES` 的 exact 语义是“后面不再到 28°C”。
- running max 已约 265 分钟未刷新，路径为 `mature_fade`，1h/3h 温度趋势均为 0。
- forecast peak 已过约 2.22 小时；当前档从高点回落 1°C。
- `-DZ / TSRA / RA`、RH 100%、overcast、风约 4kt；remaining-3h 降水概率最高 88%、平均云量 91.5%。
- 物理支持项为 observed precipitation、cloud/moisture suppression、未来雨云与 solar falling；同时保留
  `forecast_room_gt_1_native` 这一反证，没有把雨云解释成确定性锁定。
- fresh book 为 `27 YES 0.543 / 0.715`，ask depth 5 shares。

真实成交：`5 @0.715 taker + 5 @0.709 maker-taker fallback`，共 10 shares。此后 27 YES ask 逐步升至
约 0.97。用户在 15:23 和 16:04 local 两次要求刷新判断；新的报文仍为 26°C、持续毛毛雨、低云和饱和湿度，
所以结论从“强支持但有 forecast room”更新为“没有 reheat，继续持有、不追价”。

### 为什么排第一

- thesis 单一：已经打印 current bracket 后，只评估剩余时间能否再升一整档。
- 主要输入来自 settlement-facing official path、固定模型 peak clock、雨云/湿度/太阳与 fresh full ladder，
  不依赖 Busan 式未校准快源。
- 入场、复核和退出可以使用同一个连续 `P(final exact current)`；不需要先买一边、再用另一套叙事反手。
- 既有 H2 代码已能发现并执行这类候选；真正缺的是统一 decision journal、概率校准和 forward 分母，不是再加规则。

## Case 3 — Ankara 34 YES → thesis invalidation：可复制的是更新方法，不是追涨后自动反手

### 信号究竟怎么来的

14:28 Ankara local 的原始入场不是“露点/风速共同确认 34°C”，而是
`d1_yes_high_mid_live_v1` 的价格形态：LTAC running max 为 33°C，d1 34 YES 的 fresh book 为
`0.89 / 0.90`、mid `0.895`，达到 `market_d1_yes_mid_ge_0p80`。runner 以同一 observation epoch
`11:20Z` 下了 `5 @0.90 taker + 5 @0.89 maker`。所以这次应该拆成两个研究对象：

1. **原始 d1 high-mid 入场**：市场已高价认同 d1，不是独立物理 alpha；本例最终失败。
2. **持仓后的 thesis invalidation / reversal**：新天气与 source-to-settlement 证据逐步推翻 34 YES；这是可复制的工作流。

### 操作与现金结果

| Ankara local | 动作 / 新证据 | 已核实交易 |
|---|---|---:|
| 14:28–14:29 | d1 runner 按 high-mid 信号入场 | BUY 34 YES `5@0.90 + 5@0.89` |
| 15:06 起 | 用户开始复核 LTAC、MGM、forecast peak、邻站、露点、风和盘口 | — |
| 15:24 | 仍偏向“可升到 34”，人工加仓 | BUY 34 YES `5@0.78` |
| 16:20 | MGM 33.4、LTAC 仍 33；首次反手 | BUY 34 NO `5@0.58` |
| 16:50–16:53 | MGM 回到 33.2、LTAC `33/08 33008KT`；确认 native-F 边界错误后继续反手 | BUY 34 NO `5@0.66 + 5@0.64` |
| 17:20 / 17:50 | LTAC 仍 33，露点继续降到 6°C，未出现整档升温 | 持有至结算 |
| 结算 | CLOB final 为 `34 YES=0 / NO=1` | REDEEM 15 NO = `$15` |

公开 account activity 已按整个 event 分页拉取并冻结在
[ankara_event_account_activity.jsonl](analysis/2026-07/generated/intraday_decision_casebook_v1/ankara_event_account_activity.jsonl)，
汇总和 Poly closed positions 交叉核验保存在
[ankara_exchange_evidence.json](analysis/2026-07/generated/intraday_decision_casebook_v1/ankara_exchange_evidence.json)。
按 `activity.usdcSize` 的账户现金口径：

- 反手 15 NO 成本 `$9.5185`，兑付 `$15`，**NO 腿 PnL `+$5.4815`，ROI `+57.59%`**。
- `34` 档内的人工 overlay 是 `5 YES@0.78 + 15 NO`，净现金 **`+$1.5815`**；加上自动策略最初
  `10 YES` 的 `-$8.9725` 后，**34 condition 合计 `-$7.3910`**。
- 手动交易还包含 `33` 档（净现金 **`+$11.012959`**）和 `35 NO`（**`+$1.00105`**）。因此
  **全部手动操作合计 `+$13.595509`，整个 Ankara event 合计 `+$4.623009`**。
- Poly `closed-positions.realizedPnl` 跨五个 outcome 合计 `+$4.833405`；比 activity 现金结果高 `$0.210396`。
  这里把 activity 现金流作为含交易现金差异的最终 event 结果，把 closed positions 仅作独立交叉核验。

之前的错误不是 Poly 漏数据，而是分析只查了 `34` condition，并把该 condition 的负数误写成整个 Ankara。
canonical `fact_trades` 只覆盖自动策略的 10 YES；没有 authenticated order id 的手动交易仍不混入策略绩效分母，
但账户/事件复盘必须从 Poly 全量 activity 补齐。

### 天气证据如何更新

| 时间 | MGM 0.1°C | LTAC routine METAR | 正确解释 |
|---|---:|---|---|
| 15:20 | 33.3 | 33°C | 仍有剩余加热窗口，但没有打印 34 |
| 15:50 | 33.3 | 33°C | plateau；不能仅因 forecast peak 在 17:00 就断定还会升 |
| 16:20 | 33.4 | `33/10 VRB05KT SCT040` | `+0.1` 是弱升温证据，不是已跨结算档 |
| 16:50 | 33.2 | `33/08 33008KT SCT040` | 小数回落；干混合增强但整度温度未升 |
| 17:20 | 33.2 | `33/08 30008KT SCT040` | 历史剩余升温风险已很低，但非数学零 |
| 17:50 | 33.3 | `33/06 29007KT SCT040` | 单次 `+0.1` 仍只是弱证据；全窗口净变化为 0 |

关键不是某一个字段“发出 NO 信号”，而是联合状态：

- **settlement lattice**：WU 当天 native-F high 仍为 92°F；34 档需要 93°F。MGM 的 33.4°C 不能直接解释成
  “离结算 34 只差 0.1°C”。这是当天最大的一次语义纠错。
- **peak clock / runway**：fixed ECMWF 把峰值放在 17:00，16:00/17:00 约 91.1/91.2°F，之后下行。
  峰值时钟是剩余窗口 prior，不是“17:00 前必升温”；实际已贴近模型 ceiling 且连续 plateau，剩余 heat hazard 持续下降。
- **露点**：16:20–17:50 从 10→8→8→6°C，而温度一直 33°C。它说明更干空气混合进来，却没有转化为
  新的整档升温，是联合负证据；**露点下降本身不能单独看空**，因为干混合也可能伴随升温。
- **风**：VRB 5kt 转为西北风 7–8kt，没有出现新的暖平流或静风 reheat。风只有结合上游站、距离和高程才有方向意义，
  不能把“西北风”做成 Ankara hard gate。
- **邻站**：Akıncı/LTAE、Etimesgut/LTAD、Güvercinlik/LTAB 大约在 30–37km 外且低 113–140m，曾报
  34.1–34.6°C；未做站点 bias、高程和 upwind 权重前，原始绝对温度不能替代 LTAC/WU settlement truth。
- **历史路径 prior**：LTAC 2016–2026 年 7 月的探索性统计中，“仍在当日高点且近 60 分钟平”的样本，
  后续再升至少 1°C 的比例随时间从 15:50 的 `48/95=50.5%`、16:20 的 `31/106=29.2%`、
  16:50 的 `17/118=14.4%`，降至 17:20 的 `2/114=1.8%`、17:50 的 `0/93`。这是 rounded-METAR
  路径 prior，不是可执行概率；`0/93` 也不等于真实概率为零。

### 这些数据和方法到底准不准

| 输入 | 本例可用性 | 能做什么 | 不能做什么 |
|---|---|---|---|
| Polymarket CLOB resolution / pm_history | 高，结算层 | 确认 YES/NO winner | 不提供盘中气象领先信号 |
| WU LTAC native-F daily max | 高，settlement-facing | 定义 exact bracket 与 native-unit 边界 | 有更新延迟，盘中值不是最终锁定值 |
| LTAC routine METAR | 高，路径层 | 同站、约 30 分钟 cadence，判断是否打印新整度高点 | 整数 °C 太粗，不能回答 33.3 vs 33.4 |
| MGM 0.1°C | 中，方向特征 | 补充十分钟级小数趋势 | 不能替代结算；历史 `n=10,839`、MAE `1.085°C`、bias `-0.576°C`、within-1°C `74.31%`，评级为 `direction_only_bias_risky` |
| forecast peak clock / curve | 中高，timing prior | 描述剩余加热窗口、ceiling margin 和之后斜率 | 模型有绝对温度 bias，不能把峰值时刻当硬 deadline |
| 露点趋势 | 中，机制证据 | 区分湿抑制、干混合以及“混合后仍不升温” | 正负号都不能单独映射 YES/NO |
| 风速/风向 | 中，机制证据 | 与 upwind、地形和暖/冷平流联合更新 | 不校正站位/高程时不能单独裁决 |
| 邻站温度 | 低（raw）/中（校正后） | 识别区域暖区与平流方向 | 不能拿低海拔邻站的 34.x 直接证明 LTAC 会到 34 |
| 单次 `+0.1°C` | 弱正证据 | 小幅提高 reheat likelihood | 既不能断言是噪声，也不能确认持续 reheat |
| last trade / midpoint | 低，宽 spread 时 | 仅作市场路径参考 | 不能当可成交价格；动作必须用 fresh bid/ask/depth |

### 可复制模式

可复制的不是“17 点还 33 就机械买 NO”，而是同一条连续更新链：

```text
现有持仓 thesis
-> 把每个源转换到 settlement native-unit lattice
-> 用 peak clock + remaining heat + temperature path + dewpoint/wind/sky 更新 P(exact bracket)
-> 和 fresh executable bid/ask/fee 比较
-> 分开决定 exit YES 与 open NO
-> 每次新报文重新写 posterior，而不是叠 hard gate
```

- 持有 YES 时，若卖出 bid 高于继续持有的 fee/risk-adjusted value，执行 exit；这和是否另开 NO 是两个决策。
- 只有 `P(NO)` 高于 NO ask 加 fee/risk buffer，才开反手；不要因为 thesis 被削弱就自动反手。
- MGM、露点、风、邻站都进入连续 feature / likelihood update，不设 `support>=N` 或多条件 AND。
- 每个 update 必须同时保存 `data_epoch_ref + settlement transform + feature_frame_ref + quote_frame_ref`；否则无法 PIT 重放。

### 为什么不选作明日自动复刻

Ankara 最能展示“新证据推翻原 thesis”的价值，但它同时暴露三个未闭环问题：

1. 通用 heat-death frame 与 d1 runner 同期引用了不同 observation epoch，跨 runner 状态不一致。
2. exchange maker order 明确为 `5 @0.89 matched`；旧 canonical 曾误把 requested `0.781` 当 fill。现已通过
   append-only fill-price adjustment 修复，但旧 artifact 中的 `0.781/0.8405` 仍属污染口径。
3. 人工 `5 YES + 15 NO` 已由 public activity 回补 price/size/transaction hash/cash flow，但没有 authenticated
   order id，也不在 strategy raw / canonical `fact_trades`；不能和自动订单混成同一执行分母。

因此 Ankara 适合人工做 thesis update 和反证检查；在 data epoch 统一、manual order lineage 与 forward 概率校准完成前，
不适合代码自动做两腿 reversal。

## Case 4 — San Francisco 64–65 YES：方向正确，但 0.83/0.84 没有可成交机会

### Grain 与部署边界

- grain：`current_yes_core_carry_tiny_live_v2 / SanFrancisco / 2026-08-28 / 64-65 / market 3922441`；
  condition `0x7b14c50fb42c5df54a618e7f284a5ae3bdc3fd01e1c4aa684efabe9af3a90158`。
- Core production release `8c973d32d4315c07ff7180f3b66b5b3024b4ae60` 已加载且主进程健康；常规
  `10 taker + 5 shared maker` profile 为 live。
- 同一 release 已包含 near-Core maker 观察/生命周期代码，但截至
  `2026-08-29T07:36:29Z`，runtime 明确为 `near_core_feature_enabled=false`、
  `near_core_live_authorized=false`、`near_core_entry_plans=0`、submitted/terminal order rows 均为 0。
  因此下面的 `0.84` 是按冻结 quote contract 计算的反事实挂价，不是实际订单或 fill。

### PIT candidate 与反事实 quote

`pre_live_scores.jsonl` 在 `2026-08-28T23:32:26Z` 保存：Core hold probability
`0.940732`，YES book `0.83 / 0.98`，tick `0.01`，10-share taker 的 fee/depth-adjusted edge
`-0.040248`，唯一 blocker 为 `non_positive_taker_ev`。按 near-Core maker contract：

时钟必须分层：`0.83 / 0.98` 是 pre-live direct fetch 在 `23:32:38Z` 取得、
`23:32:39Z` 写入的 BBO；同一 row 中的 `23:29:07.744Z` 是输入 paper snapshot 继承的
collector response clock，不属于这组 direct-fetch 价格，不能据此把该 BBO 判为旧了 198 秒。

```text
maker cap = floor_to_tick(0.940732 - 0.01) = 0.93
initial quote = min(best bid + 1 tick, best ask - 1 tick, maker cap)
              = min(0.84, 0.97, 0.93) = 0.84
```

若只 join `0.83`，经济结论相同：两者都低于模型 cap，若最终 YES，毛收益分别为
`0.17/share` 与 `0.16/share`；但只有真实 fill 才能形成 PnL。

### 后续 tape 与 outcome

- candidate 创建后第一笔 YES 成交在 `2026-08-28T23:47:10Z`，晚 `14m31s`，价格直接为 `0.98`。
- 此后公开 tape 中 26 笔 YES 成交的最低价为 `0.98`；没有 YES seller print `<=0.84`。
- 同期 NO 侧最高 BUY 为 `0.12`，低于与 YES `0.84` 互补成交所需的 `0.16`；所以也没有
  complementary match。即使把 YES 追到冻结 cap `0.93`，该路径仍不会成交；追到 `0.98` 则超过
  Core `0.940732` value，变成负 EV。
- NOAA/NWS KSFO 在当地 `2026-08-28` 的最高观测为 `18.3°C = 64.94°F`，按市场 whole-degree
  规则属于 `65°F`，即 `64-65 YES`。截至本次维护，Polymarket 尚未 formally resolved，canonical
  也没有 settlement row；正式 settled denominator 必须等 exchange/canonical final，不能用 99.9% 盘口代替。

证据指针：

- candidate：`/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/pre_live_scores.jsonl`
- WS：`/Volumes/jrs/weather_data_feed_service_runtime/market_books/ws_incremental/2026-08-28/market_books_ws_20260828_23_1787933342225550000_78383.jsonl`
- production state：`/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/latest_summary.json`
- public tape：`https://data-api.polymarket.com/trades?market=0x7b14c50fb42c5df54a618e7f284a5ae3bdc3fd01e1c4aa684efabe9af3a90158&limit=10000`
- settlement source：`https://api.weather.gov/stations/KSFO/observations?start=2026-08-28T07:00:00Z&end=2026-08-29T07:00:00Z&limit=500`

### 案例标签与沉淀

```text
signal: direction_correct_pending_formal_resolution
execution: unfilled_price_jump / no_edge-compatible_executable_opportunity
order: counterfactual_only
pnl: 0 actual
```

该例支持“模型判断正确、拒绝追到负 EV 价格也正确”，不支持“near-Core live 已验证”或“maker 一定能成交”。
它应进入 execution no-fill/机会成本分母，用于比较 quote cap、time-to-fill 与 traded-through；不能作为
提高 maker 价格、放宽 retained edge 或开启真钱 near-Core 的单例依据。

## 2026-07-22 frozen-forward 操作卡

不是指定城市，也不是看到下雨就买。代码扫描所有 same-day city-day，出现下列连续机制状态时才提醒人工：

1. current exact bracket 已由 settlement-facing path 打印；先锁定 exact semantics。
2. forecast peak clock 已到或已过，remaining heat integral 明显下降。
3. 温度路径为 plateau/pullback/fade，strict high 已成熟，没有 fresh reheat。
4. 雨、云、高湿、弱混合、冷平流等至少存在一种可解释的压温机制；这些是 soft evidence，不做 support-count gate。
5. 同时列出反证：forecast ceiling margin、散云、暖平流、重新升温、source age/basis 问题。
6. fresh full ladder 给出 current YES、d1 NO 与相邻 exact brackets 的 bid/ask/depth/fee；只在估计概率高于
   executable cost 时形成候选。

每一份新 official observation、forecast run 或 material book change 都追加一条 update：

```text
as_of/data_epoch -> feature_frame + quote_frame
-> prior probability / new evidence / posterior probability
-> thesis supported | weakened | invalidated | reversed
-> suggested action / actual human action / order-fill link
```

明日执行边界：

- 若没有符合机制的候选，结果是 `no_signal`，不是漏掉一次实验。
- 若出现候选，只沿用既有 tiny-probe 上限，不 size-up；本 casebook 不授权新 live policy。
- 人工必须在动作前核对 source-to-settlement basis、最新报文、exact bracket 和 fresh executable price。
- 代码负责全分母扫描、时间戳、特征/盘口快照、概率与 journal；人工负责识别机制错配、审查反证并做最终决定。

## 晋升到全自动代码的条件

立即应该代码化的是记录与评分层，不是自动反手：

- 统一所有 runner 的 `data_epoch_ref`、`feature_frame_ref` 与 `quote_frame_ref`。
- append-only 保存每次 probability/thesis/action update，并把人工动作回连 canonical order/fill。
- 记录全城市全候选分母，不只保存成交和盈利例。
- 在 frozen forward 上比较 calibrated probability 与同 rows market 的 logloss/Brier，再评估 fee-adjusted expression。
- 修复 Ankara canonical fill price 与人工交易遗漏后，才允许用它做 reversal PnL 训练标签。

H2 仍服从既有预注册门：至少 25 settled rows、10 个独立 target dates、8 个城市，并通过概率、显著性、
repricing 和执行覆盖门。达到这些条件前，结论保持 `inconclusive / human-in-the-loop forward`。

## 双漏斗与边界

```text
signal funnel:
全城市 PIT state -> no-reheat mechanism candidate -> 首个 city-day candidate -> 人工复核/既有 probe

evidence funnel:
PIT feature/source -> fresh full ladder -> suggested/actual action -> order -> fill -> settlement
```

前述 7/20 三例是 ex-post 选出的案例，不是 signal denominator。Case 4 是生产 candidate collector 留下的
no-fill forward case，但 formal settlement 仍 pending；两类材料都不能替代统计确认。后续 forward collector
必须同时保存没有交易、判断错误和反向后仍亏损的样本。

## 2026-07-21 全城市 forward scan

Evidence cutoff: forecast/paper snapshot `2026-07-21 09:36 BJ`；observation cache `09:47 BJ`；
direct AWC METAR recheck `09:55 BJ`；orderbook freshness `09:52 BJ`。

本轮扫描 target-date=2026-07-21 的 52 个 forecast city-day。forecast/model 102 个 city-target 全覆盖、无
model fallback；40 城 observation cache 正常，但同本地日 snapshot 只有 32 城，6 城构建缺 observation，
HongKong/Singapore/TelAviv 缺 snapshot 必需 METAR 字段。缺失只记 coverage gap。

当前没有城市达到 confirmed no-reheat/action-ready：亚洲候选仍在 forecast peak 前或仍创新高；欧洲候选尚在
凌晨；美洲的 7/21 本地日尚未开始。下表的“置信度”是**匹配 Lucknow 机制的主观 PIT 置信度，不是 exact-bracket
胜率，也不是下单授权**。

| 优先级 | 城市 | 当前本地状态 | 原 forecast peak（本地 / BJ） | 模式置信度 | 关键盯盘窗口（本地 / BJ） | 当前动作与升级条件 |
|---|---|---|---|---:|---|---|
| A | Guangzhou | 09:37 official 31°C；09:40–09:55 `+TSRA/SQ` 冷池降到 25°C，forecast max 约31.1°C | 14:00 / 14:00 | 78% | 10:00–15:30 / 同 BJ | **全城第一观察位**；先看雷暴后能否恢复。11:30后仍<=29、雨云持续、WU/settlement-facing path已确认日高31且未重新升温，才升级 current 31 exact 候选；一旦打印32，31 YES直接失败 |
| A- | Shanghai | 09:00、09:30连续32°C，1h仍+1°C；当前无雨，forecast max约32.1°C、11点后降温且云量100% | 11:00 / 11:00 | 62% | 10:00–13:30 / 同 BJ | 仍在 active warming，暂不操作；11:30–12:00仍停32、雨云兑现且WU已确认日高32，才评估32 exact；若打印33立即排除 |
| B+ | Lucknow | 07:00–07:30雨中26°C、RH100%、OVC；当前高点26但峰值尚远，forecast max约30.3°C | 14:00 / 16:30 | 68% | 13:00–17:00 / 15:30–19:30 BJ | 最像昨日机制，但不能复制昨日结论；13点后看实况是否继续落后forecast、high age是否成熟、雨云是否未断，再选择当时已打印的current exact档 |
| B | Warsaw | 03:30 local 小雨14°C、日高15°C；低云、雷阵雨，forecast peak附近雨100%/云约100%，但仍预报升到约18.3°C | 13:00 / 19:00 | 63% | 12:00–16:00 / 18:00–22:00 BJ | 机制环境强、时钟太早；只在午前后实际温度持续大幅落后18°C路径时升级 |
| B- | Helsinki | 05:00 local 约11°C、夜间高14°C后回落；全天高云，但完整白天仍在前方 | 00:00 warm-night max / 05:00 | 52% | 12:00–15:00 / 17:00–20:00 BJ | 不把午夜forecast peak当heating done；中午以后仍未回到14且云雨压制持续，才进入current 14 exact复核 |
| C+ | KualaLumpur | 08:00–09:30约26→28→30°C，当前仍快速升温；15点后雨云明显增强 | 13:00 / 13:00 | 38% | 12:30–16:00 / 同 BJ | 当前不符合；只有13点前后停止创新高、随后降雨形成持续pullback才升级 |
| C | Wuhan | 09:00 local 31°C、仍在日高；当前FEW/无雨，forecast peak约32.7°C | 15:00 / 15:00 | 32% | 14:00–17:30 / 同 BJ | forecast有降雨但实况未兑现；需先出现雨云、回落和峰值后不reheat |
| C / data gap | Singapore | 09:30 local 29°C、无观测降雨，forecast peak约30.9°C；snapshot 必需 METAR 字段缺失 | 16:00 / 16:00 | 28% | 15:00–18:30 / 同 BJ | 先修复/补齐同epoch state；即使后续降雨，也不能用当前不完整 frame 下判断 |

### Forecast-only 高潜力但不可操作

| 城市 | forecast peak（本地 / BJ） | 天气形态 | coverage blocker |
|---|---|---|---|
| Shenzhen | 14:00 / 14:00 | peak窗口雨100%、云约76% | 无当前同站 observation state |
| HongKong | 12:00 / 12:00 | peak窗口雨99%、云约61% | snapshot 缺 METAR required fields；HKO/Co-WIN 不能静默替代 |
| Seoul | 01:00 / 00:00 | forecast把日高放在夜间，雨100%、云约94% | 无当前 observation；异常 peak clock 需人工复核 |
| Lagos | 14:00 / 21:00 | 雨97%、云100% | 无当前 observation state |
| Moscow | 15:00 / 20:00 | 雨76%、云约88% | 无当前 observation state |

美洲 target-date=7/21 在扫描时仍处于当地7/20晚间；不拿前一日 observation 评估当日 no-reheat。最早的
forecast-only 观察窗口从北京7/22凌晨开始，届时用新的同本地日 observation epoch 重新扫描。

### 今日盯盘顺序

```text
10:00–13:30 BJ  Guangzhou + Shanghai
12:30–16:00 BJ  Guangzhou + KualaLumpur（后者仅条件候选）
15:30–19:30 BJ  Lucknow
17:00–22:00 BJ  Helsinki + Warsaw
```

每个窗口必须在新 official report 到来后重新判断 current exact bracket、fresh high age、forecast residual、
remaining heat 与 full-ladder executable cost；本表不能跨时点直接转成订单。

### 10:10 BJ 补扫：HongKong / Seoul coverage 根因与其他模式

前表把 HongKong、Seoul 简写成“无当前 observation”不够准确；实际是**通用 observation cache 无可用于
settlement-facing 策略的合格行**，不是气象观测不存在：

- HongKong 的 source profile 是 `special_source_confirmed`，`live_eligible=false`；结算基准是 HKO Daily
  Extract，明确禁止用 VHHH/IEM/WU 静默替代。专用 `hko_obs` 1-minute collector 正常，10:00 local 为
  30.3°C；但它使用 `Hong Kong` key，canonical market calendar 使用 `HongKong`，跨层 join 仍需显式 alias。
- Seoul 的 source profile 是 `blocked_unresolved_settlement_basis`，`live_eligible=false`；所以 RKSI METAR
  与 AMOS 即使正常，也按设计不进入通用 cache。10:10 local 时 RKSI METAR 为25°C、AMOS两条跑道约
  23.7–24.1°C。它们能描述物理路径，但在 Weather.com/RKSI 最终结算基准没有 forward 对齐前，不能产生
  settlement-facing action。
- 09:52 paper snapshot 之所以又能看到两城的 VHHH/RKSI METAR，是 legacy city-pool 的 aviationweather
  路径；这补的是机场天气上下文，不会解除上述 source-profile blocker。

Evidence cutoff: forecast enrichment / TAF `09:58–10:00 BJ`；direct AWC METAR 与专用 HKO/AMOS
`10:00–10:10 BJ`。下表“模式置信度”只是当前 forecast/TAF/路径对**物理模式**的主观一致性，不是
exact-bracket `P(win)`；没有 fresh ladder/price，不构成下单授权。

| 优先级 | 城市 / 模式 | 当前事实与 forecast/TAF | forecast peak（本地 / BJ） | 模式置信度 | 关键盯盘窗口（本地 / BJ） | 当前动作 |
|---|---|---|---|---:|---|---|
| A | Manila — clean runway → pre-convection peak | 10:00 31°C；fixed forecast 32.7°C，multi-model spread仅2.3°F；TAF `TX33` at 14:00，CB窗口从14:00开始 | 14:00 / 14:00 | 84% | 12:30–14:30 / 同 BJ | **今日最适合复刻“先预判模式、再用报文确认”**；重点防当前31 exact YES被32/33上穿，先观察，不脱离价格直接选档 |
| A | Ankara — CAVOK dry-heat ramp | 05:00 17°C、CAVOK；fixed 32.6°C，模型均值同为32.6°C、spread约1.7°C；TAF全天无降水 | 15:00–16:00 / 20:00–21:00 | 88% | 12:30–16:30 / 17:30–21:30 BJ | 高置信模式快照，但离峰值很远；只做人工 thesis-invalidation，不自动两腿反手 |
| A | Madrid — CAVOK late heat peak | 04:00 22°C；fixed约37°C，TAF显式 `TX38` at 18:00，模型spread约3.1°C | 17:00–18:00 / 23:00–00:00 | 86% | 14:30–18:30 / 20:30–00:30 BJ | 干热爬坡很清楚，但37/38 exact档仍有分歧；下午用路径决定相邻档，不宜现在下注 |
| A- | Karachi — haze/marine cap + slow rise | 07:00 29°C、HZ/BKN；fixed 31.6°C，multi-model spread仅1.3°C；TAF全天霾/低云、无白天降雨 | 14:00 / 17:00 | 79% | 11:30–14:30 / 14:30–17:30 BJ | 适合做“受限但继续升温”的窄分布样本；11:30前不把29当日高，临峰再判断31/32 exact |
| A- | Busan — active runway / current-bracket overshoot | 11:00 已32°C；fixed约32.8°C，南风16kt、SCT/BKN且TAF白天无雨，仍有约3小时跑道 | 14:00–15:00 / 13:00–14:00 | 74% | 12:00–15:00 / 11:00–14:00 BJ | 当前32 exact YES仍有被33上穿风险；只做人机复核与collector，不把快源事件自动转 live |
| B+ | HongKong — pre-storm spike / official cap | HKO 10:00为30.3°C，VHHH 10:00已31°C；TAF `TX31` at 14:00，11:00后TSRA、随后SHRA | 12:00–14:00 / 同 BJ | 76% | 10:30–14:00 / 同 BJ | 只盯HKO official path：关键是HKO日高能否从floor 30跨到31；VHHH 31不能替代结算事实 |
| B+ | KualaLumpur — storm-at-forecast-peak cap | 10:00 30°C；fixed约32.1°C，TAF 13:00–16:00有`-TSRA`，风暴正压在13–14点峰值附近 | 13:00–14:00 / 同 BJ | 73% | 12:30–15:30 / 同 BJ | 先看是否到31/32，再看雷暴后持续pullback；属于条件式 no-reheat，不是现在就做“不升温” |
| B | Singapore — storm interruption → reheat fork | 10:00 28°C；forecast peak约31°C在16点，TAF 12:00–15:00 TSRA，风暴后仍留约1小时加热窗 | 16:00 / 16:00 | 69% | 12:00–17:00 / 同 BJ | 最适合训练“结论更新”：若15点后清空并回升，否决no-reheat；若冷池持续到16点后，才升级cap模式 |
| observe-only | Seoul — rain interruption / weak reheat | RKSI早间已27°C后雨中回到25°C，10:10 AMOS仅23.7–24.1°C；TAF仍给15:00 `TX27`，但paper forecast把峰值放在01:00 | 01:00 vs 15:00 / 00:00 vs 14:00 | 70% physical / 0% trade | 13:00–16:00 / 12:00–15:00 BJ | 可观察雨停后的reheat是否恢复到27；settlement basis未解，禁止转为交易候选 |

本轮新增的首选复刻对象是 **Manila**：模式、TAF温度高点、对流触发时刻和多模型离散度同时可解释，且盯盘
窗口就在白天。Ankara/Madrid 是更干净的 forecast-first 样本，但峰值较晚；HongKong/Seoul 主要用于验证
source-aware 状态更新，不能把机场观测直接等同于结算事实。

## 审阅签核 (2026-07-21)

独立复核结论：**证据包可信、结论可行，按 human-in-the-loop forward 推进；但不得据此发布任何 PnL/ROI。**

复核已核对（可复跑）：

- 幂等：`build_intraday_decision_casebook_v1.py` 重跑，artifact 行数为 `142/385/25`，manifest 重新冻结输入与产物 hash。
- Canonical 对齐 raw：Busan `10@0.85 + 5@0.84`、Lucknow `10 shares @0.715/0.709`、Ankara 自动单
  `5@0.90 taker + 5@0.89 maker`。Ankara maker 的旧 `0.781` 已用 authenticated order state 的 append-only
  adjustment 修复；fact 成本为 `$4.5225 + $4.45 = $8.9725`。
- 人工单对齐 account activity：`5 YES @0.78`、`5 NO @0.58 + 5 NO @0.66 + 5 NO @0.64` 与 15 NO redeem
  已冻结；它们仍缺 authenticated order id，所以只作 account-level evidence，不进入 strategy fill denominator。
- `weather docs check ok`，casebook 已登记 `WEATHER_DOCS_INDEX`。

复核新增的一条硬提醒（发布口径边界）：

- **Ankara 34 已由 CLOB final resolution 回填 canonical**：自动 10 YES 的 `final_yes=0`，canonical PnL
  `-$8.9725`；全库 fill-coverage gate 为 `gate_pass=true`。人工腿仍单列 public activity 现金流，不混入该 PnL。
- Busan 31 与 Lucknow 27 目前仍是 canonical `settled=0/final_yes=NULL`，在 settlement 补齐前不得进入正式
  `live_real` PnL/ROI 报告或曲线。
- 仍需回连的缺口只剩：① Lucknow/Busan pm_history settlement；② Ankara 人工单 authenticated order id。
  Ankara 的 maker price 和 final winner 已闭环，不再列为待确认项。

明日交易/策略裁决（干净可行）：

1. **Lucknow 型 no-reheat** — 唯一 greenlight 的候选形态。代码全城扫描 → 命中六条机制状态才提醒人工 →
   人工核对 exact-bracket 语义 + fresh executable price → 仅 tiny-probe 上限。无同类 city-day 即 `no_signal`，不为凑实验交易。
2. **Busan** — 只做 fast-source feature/collector，**不复刻 live 快源交易**（source→settlement basis 风险 + 正确事件不可成交/错误事件反可成交的 adverse selection）。
3. **Ankara** — 仅人工 thesis-invalidation 训练，**禁止代码自动两腿反手**，解锁前置条件同上三项复核。
4. 本轮不改 live policy、不 size-up、不直推生产参数。
