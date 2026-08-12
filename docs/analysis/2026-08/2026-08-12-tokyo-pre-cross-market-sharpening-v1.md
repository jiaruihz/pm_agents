# Tokyo first pre-cross market-anchored strategy（2026-08-12，v2）

Status: `inconclusive / reused-audit profitable / clean-forward accumulating / zero-notional only`

## 结论与动作

Tokyo 当前最值得继续积累的表达不是“天气模型取代盘口”，而是：每档只在 JMA 首次进入
`current +0.3/+0.4°C`、但 JMA native rounding 仍未跨到下一档时判断一次；盘口若仍偏 YES，保持原概率，
盘口已偏 NO 时才把同刻 market odds 有界强化。v2 不增加高价 hard filter，而是为同刻盘口的不确定性预留
`max(1 tick, half spread)`：

```text
P_market_NO < 0.5: P_post = P_market_NO
P_market_NO >= 0.5: logit(P_post) = 2 × logit(P_market_NO)
reserve = max(0.001, 0.5 × (NO ask - NO bid))
BUY 5 NO iff P_post > five-share ask VWAP + official Weather fee + reserve
```

8/1–8/11 raw-exact reused audit 从 v1 的 29 笔降为 12 笔，12 胜，投入 `$53.4381`，fee 后
PnL `+$6.5619`、ROI `+12.28%`，target-date bootstrap 95% CI `[+7.23%, +17.69%]`。但该窗口已经被用于
发现执行问题，不能冒充 clean forward；同 rows Brier 相对 market 的 CI 也仍跨 0。v2 已冻结从 8/12 起离线
zero-notional 评分，不接 intent/order/fill，不改 live。

## v2 做了什么、没做什么

天气路径 challenger 用长历史构造
`logit(P_full path) - logit(P_clock + margin base)`，只在 7 月 development 的 9 个候选/6 日选择
`alpha ∈ {0, 0.25, 0.5}`。三者 Brier 分别为 `0.01643 / 0.01725 / 0.01809`，因此冻结 `alpha=0`：
当前 exact-clock 可用的 slope、pullback、remaining heat 没有提供盘口之外的稳定概率增量，不能为了“看起来更像天气模型”
硬塞进 posterior。这个 negative result 被保留，后续只有新 clean-forward 日期能推翻。

真正进入 v2 的是执行不确定性：ask 已计 taker 成本和官方 fee，但宽 spread 仍表示 quote/latency/adverse-selection
风险。统一扣除半个 spread（最低 1 tick），不是按 98¢、90¢ 做事后价格切片。8/1–11 v2 入场 ask 均值
`88.61¢`、中位 `94.35¢`、范围 `70.0–99.8¢`；`≥98¢` 仅 2/12，而 v1 为 18/29。高价并非绝对禁止：
只有 posterior edge 足以覆盖 fee 和 spread reserve 才保留。

## 固定目标、时钟与 grain

- 目标：`P(final official exact Tmax leaves current official bracket upward)`，即 current exact bracket 的 NO。
- source：JMA AMeDAS collector exact first-seen；不使用 late backfill、issue time 或后到 settlement 信息。
- 决策时钟：source first-seen 后 180 秒内首个 causal、two-sided active-bracket book。
- grain：每个 `target_date × current exact bracket` 的首次 pre-cross proximity state；是否入场只在这个时钟判断一次，
  不能因为后续价格变便宜再入场。
- expression：只买 current bracket NO、每档最多一次、5 shares、taker ask depth 与官方 fee。

## 数据与双漏斗

输入 scope 不是项目“全部历史”，而是下列可核验切片：

| 层 | rows | target dates | 角色 |
|---|---:|---:|---|
| 历史 JMA/RJTT feature rows | 63,384 | 819 | 物理模型输入 universe，2024-04-30～2026-07-30 |
| 首次 pre-cross 历史候选 | 2,244 | 737 | train/calibration/physical audit |
| 7 月 market development expression | 124 | 12 | archive+15m 57 rows + hash-verified exact 67 rows |
| 8/1–8/11 strict raw-exact expression | 554 | 11 | reused audit |
| development 首次 pre-cross 候选 | 9 | 6 | exponent 选择 |
| reused-audit 首次 pre-cross 候选 | 34 | 11 | probability 与 execution 复核 |
| reused-audit v1 fee-only trades | 29 | 11 | 真实 5-share depth + fee，28 胜 1 负 |
| reused-audit v2 reserve trades | 12 | 7 | 真实 5-share depth + fee + half-spread reserve，12 胜 |

Signal funnel：`678 expression rows → 68 mechanism rows → 43 first date-bracket candidates → 15 v2 selected
development/audit expressions`。Evidence funnel：开发 3 笔只有 top-of-book proxy、无 5-share depth；严格审计
12 笔全部有 exact 5-share ask depth、binary settlement 和 fee。

## 结构性修复

本轮先修了三个会制造假结论的问题：

1. development adapter 过去只收 `archive_reconstructed_plus_15m`，漏掉 7/22–7/29 已存在的
   `collector_exact_hash_verified`。现在可显式组合 clock classes；同一物理 checkpoint 重复时优先 exact。
2. runtime feature frame 中 `jma_current_minus_current_bracket` 是相对模型 checkpoint 的旧 anchor；直接把它套到
   另一条 market expression 会在 8/8 产生 `1.3～6.3°C` 假 margin。现在 source bracket 和 margin 都按目标
   expression 现场重算。
3. strict exact 窗内 JMA wind/precip feature 覆盖为 0；prior-METAR age 也超出历史训练支持。它们不进入当前候选，
   不能用 imputation 把“字段不存在”伪装成多气象特征模型。

## 概率层

历史 source-only logistic 使用 temperature path、remaining heat、slope、pullback、solar clock 等 13 个有 PIT
parity 的特征。固定 train `≤2025-06-30`（1,179 rows/380 日），2025H2 calibration（479/166），
2026-01-01～07-15 physical audit（529/176）；calibration 选 `C=0.03`。它在 physical audit 上
Brier/logloss=`0.08036/0.26939`，说明路径特征能预测天气，但在 market overlap 上仍明显输 market：

| evaluation | model | Brier | logloss |
|---|---|---:|---:|
| development 9 rows/6 日 | market | 0.01854 | 0.07563 |
| development | source-only physical | 0.07364 | 0.25407 |
| development | bounded posterior | **0.01643** | **0.05555** |
| reused audit 34 rows/11 日 | market | 0.05307 | 0.17065 |
| reused audit | source-only physical | 0.08204 | 0.26161 |
| reused audit | bounded posterior | **0.04961** | **0.14541** |

开发窗在预注册四个 exponent `1/1.25/1.5/2` 中选 2（K=4）。reused audit 相对 market 的
Brier delta `-0.00346`，95% CI `[-0.00792,+0.00081]`；logloss delta `-0.02524`，
CI `[-0.03821,-0.01351]`。主指标 Brier 仍未显著通过，因此 baseline gate 记 FAIL。

## 交易层与错误

v1 29 笔中唯一失败是 8/11 12:26:55 JST 的 `32 NO`：JMA `32.3°C`，market NO `0.6295`，
posterior `0.7427`，5-share fee 后成本 `$3.63085`，终局停在 32，亏 `$3.63085`。同日其余五档 NO
虽胜，整日仍净亏 `$1.31435`。这正说明“升温过程中多个低档 NO 可以同时获胜”的现金流结构成立，
但每日最后 terminal bracket 的一次错误足以吃掉许多 99c 尘埃利润；不能用 28/29 胜率替代日期级风险。

该笔 NO bid/ask 为约 `0.543/0.716`，raw posterior edge 只有 `1.65¢`，而 half-spread reserve 为 `8.65¢`，
因此 v2 会在事前阻断；这不是知道最终停在 32 后追加 terminal-hour 或价格 gate。后续 clean forward 必须完整记录
全部 selected/unselected candidate，不能只报成交子集。

## 三门与产物

- reused-audit execution ROI：点估和 CI PASS，但窗口已用于改模，不能充当 clean-forward significance。
- same-denominator market baseline：Brier 点估 PASS、CI FAIL；主门 FAIL。
- clean frozen forward：已开始；8/12 有 27 个 exact expressions、1 个 pre-cross candidate、0 signal，尚未结算。
  该候选为 13:17 JST 的 `28 NO`：JMA `28.3°C`，bid/ask `0.44/0.60`，posterior `0.53994`，fee 后成本
  `0.612`，未扣 reserve 前 edge 已是 `-7.21¢`，所以 0 signal 是无正 edge，不是市场已经 99¢ 定死。
- live：不具备资格。

可重复入口仍是 `weather_model_evaluation.tokyo_market_prior_adapter`，没有新增平行 runner。主要产物：

- research artifact：`tokyo_pre_cross_market_sharpening/run_20260812_v2/pre_cross_research`
- frozen spec：`frozen_candidate_spec.json`
- 8/12 离线 forward：`tokyo_pre_cross_market_sharpening/forward_20260812_v2`；27 个 exact expression、
  1 pre-cross candidate、0 signal；未使用 settlement，notional=0。

## 用户口径命名（2026-08-12）

以后 Tokyo 这两条方向固定用下面的短名，避免再把脚本内部历史版本号混进讨论：

- **Tokyo V2 — cross 前升温/离档模型**：只在每个 exact bracket 首次出现 JMA `current+0.3/+0.4°C`
  时判断一次“当前档最终会不会被向上离开”，当前只表达 current-NO。它比 `.7 cross` 规则早，但不是纯天气模型；
  同刻 market 是 prior，宽 spread 作为连续 execution reserve。
- **Tokyo V3 — 连续全概率模型**：每个 causal JMA 10-minute checkpoint 都输出
  `P(stay), P(+1), P(+2), P(+3+)`，因此既能表达“还会升”也能表达“不会再升”，未来可在完整 ladder 上同时评估
  YES/NO。V3 是研究方向，不替代 V2，也未接 runtime。

稳定 model ID 分别为
`weather.city_intraday_probability.tokyo_pre_cross_market_sharpening` 与
`weather.city_intraday_probability.tokyo_continuous_full_probability`。数字只作为人类短名；血缘仍使用稳定语义 ID。

## V2 与 `.7 cross` 规则的同机会对照

不能把 V2 的 8/1–11 结果和规则的 7/9–8/9 结果直接横比后就称 V2 胜出。本轮锁成双方都实际覆盖的
`target_date × exact bracket`：8/1–9 的 V2 有 26 个候选/8 个信号，规则有 4 笔；真正重合只有 3 档。

| target date / bracket | V2 时刻 | `.7` 规则时刻 | V2 提前 | V2 动作 | V2 PnL | 规则 PnL |
|---|---|---|---:|---|---:|---:|
| 8/04 / 28 | 13:00 JST | 13:10 | 10m | 入场 | +$1.1057 | +$0.3816 |
| 8/06 / 29 | 11:00 JST | 14:20 | 200m | ask=.997，reserve 后无 edge，不入 | $0 | +$0.2811 |
| 8/07 / 32 | 10:50 JST | 11:50 | 60m | 入场 | +$0.1427 | +$0.2381 |

V2 平均提前 90m、中位 60m。双方都入场的 2 档，V2 合计 `+$1.2484`、规则 `+$0.6197`，V2 多
`$0.6287`；三档按各自实际动作，V2 `+$1.2484`、规则 `+$0.9008`。这是“更早且点估更好”的直接证据，
但只有 3 个 shared opportunities，不能称统计确认。正确动作是规则保留 baseline，V2 跑 clean zero-notional forward。

paired artifact：`tokyo_v2_v3_model_map/run_20260812_v1/v2_vs_cross07_rule`。

## V3 第一版结果

V3 没有重新发明 collector/clock/订单链，直接复用已冻结于 2026-07-15 的 Tokyo continuous distribution head；
天气特征包括 JMA 温度路径、斜率、running max、solar/clock，以及 PIT RJTT METAR 的露点、湿度、风、气压、云、雨、
能见度。需要特别说明：当前 exact runtime 的 **JMA 自身 wind/precip 覆盖仍为 0**，这些字段不能冒充已在实时起作用；
METAR 天气特征有约 89.4% checkpoint 覆盖。

开发窗 124 rows/12 日只用来从固定 alpha `0/.05/.1/.25/.5/1` 选择 market/weather leave-logit blend，选中
`alpha=.5`。随后在已经看过、因此只能叫 reused audit 的 8/1–11（554 checkpoints/11 日）评测：

| same checkpoint leave-current target | accuracy | Brier | logloss |
|---|---:|---:|---:|
| Tokyo V3 | 87.84% | 0.08657 | 0.27014 |
| market | **90.99%** | **0.07473** | **0.24058** |

所以 V3 的**问题定义与结构是对的**：它确实能在没有 `.7` cross 时连续判断升/不升，并给完整上升档位分布；但当前
第一版 posterior 在同分母上输 market，不能接 shadow intent，更不能替换 V2。下一轮 V3 只沿连续全概率方向改进
feature parity / conditional tail calibration，不用这 11 日继续挑 alpha。新 clean forward 必须从下一次冻结后开始。

V3 spec：`configs/weather/tokyo_continuous_full_probability_v3.json`；audit artifact：
`tokyo_v2_v3_model_map/run_20260812_v1/tokyo_v3_full_probability_audit`。
