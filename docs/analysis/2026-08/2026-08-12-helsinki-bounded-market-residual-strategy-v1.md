# Helsinki regime-calibrated bounded market-residual exact-bracket strategy v2

## 数据快照

- 数据源：Helsinki rich-contract PIT replay、FMI/forecast/official/active-book raw，以及只读 canonical `/Volumes/jrs/pm_agents/runtime/weather.db`；仓库兼容入口为同一 device/inode `16777247/54444`。
- 观测时间：canonical `MAX(fact_built_at_utc)=2026-08-12T03:59:21Z`；本次 clean-forward raw 检查截止 `2026-08-12T05:01:25Z`。
- 研究分母：282 probability rows/8 settled target dates；9 retrospective expressions/6 active dates。unsettled `0/9`，missing bracket `0/9`，actual order/fill `0/0`。
- canonical 自检：`fact_trades=5,004`（settled 4,982、missing bracket 4、状态空18）；`fact_signal_candidates=184,465`；strict manifest、DB route 与 production controller 均 healthy。本报告收益是 research replay，不是 `fact_trades` actual fills。

## 结论

交付策略升级为 `helsinki_regime_calibrated_bounded_residual_c015_v2`。它不是天气模型单独猜最终温度，也不是跟着盘口复制：先用 2025 OOF 学到的统一 regime calibration 修正 FMI remaining-heat 概率，再把盘口作为 prior，天气只允许在 logit 上做最多 `0.15` 的连续修正；每个 `target_date × current bracket` 比较真实 5-share YES/NO ask 与官方 taker fee，只在净 EV 为正时选择较优一边，首次入场后不重复开同档。METAR 不开仓，当前版本持有到 settlement。

公式：

```text
weather_no_cal = sigmoid(beta · [weather_logit, local clock, forecast peak clock,
                                 forecast availability, path state,
                                 weather_logit × path state])
p_no = sigmoid(logit(market_no) + 0.15 * tanh((logit(weather_no_cal)-logit(market_no))/0.15))
```

`0.15` 仍沿用既有固定表达，不按交易 ROI 重选。新增 calibration 只用 2025 的 `51,451 rows / 365 target dates` OOF 训练：四个连续日期块做三次 expanding validation，五个预声明正则强度中，只有 `256/512/1024` 在三段里同时改善 Brier 与 logloss；按 mean Brier 选择 `L2=256`。2026-07 与 8 月盘口/收益只作压力测试，没有参与模型选择，也没有增加价格、小时、天气形态或坏日期 hard gate。git 内 artifact SHA-256 为 `fd09be9c39e585a8b5502452a84db517fbaa455a728427db71de92e030f614e9`；v2 clean forward 从 artifact freeze 后的 `2026-08-12T08:31:24Z` 开始，v1 当天更早记录不冒充 v2 forward。

## 完整训练与模型选择收口

这次不是围绕两三个坏 case 改规则，而是把目标函数和训练 grain 一次收口：每个 target date 等权，直接最小化 Brier，并用 identity-centered L2 约束 calibration 不远离原天气概率；logloss 作为共同晋级条件。比较结果如下：

| 候选 | rolling Brier delta | rolling logloss delta | Brier/LL 胜出折 | 结论 |
|---|---:|---:|---:|---|
| compact logistic / Platt | 既有 8-split 中仅 3/8 | 仅 4/8 | 不稳定 | 拒绝 |
| shallow HGB calibration | Brier 点估略好 | logloss 2/3 折变差 | 2/3、1/3 | 拒绝 |
| rich physical calibration | 2025 可改善 | 2026 market replay 缺逐字段同钟 parity | 不具备完整同分母 | 保留 challenger |
| compact regime `L2=256` | `-0.000317` | `-0.002260` | `3/3、3/3` | 选中 |

选中模型只读取 live 已有且已经做过 PIT parity 的字段：天气概率、Helsinki local clock、距 forecast future peak 的分钟数、forecast availability、`fresh_runway/plateau/pullback/fade` 及 path×weather-logit。这样修复的是统一的概率表达：不同峰值时钟与路径状态下，天气基座的置信度不同；没有把 8/5、8/10、8/11 写成例外。

固定 7/20–29 的 362 rows/9 dates 上，新版 Brier/logloss 为 `0.07388/0.24195`，旧版为 `0.07430/0.24309`，market 为 `0.07641/0.24874`。新版相对旧版的 target-date block bootstrap delta CI：Brier `[-0.000916,-0.000047]`、logloss `[-0.002188,-0.000292]`，九个开发日期上是稳定的小幅概率升级。

8/2–11 的 282 rows/8 dates 没有参与选择。新版 Brier/logloss `0.05234/0.17027`，旧版 `0.05227/0.17065`，market `0.05442/0.17542`：logloss 小幅改善、Brier 与旧版基本持平且 CI 跨零。5-share first-positive 仍是完全相同的 9 笔、6胜3负、cost `$23.0224`、PnL `+$6.9776`、ROI `+30.31%`，说明升级没有靠新增/删除历史交易制造收益。

runtime scorer 用 git artifact 对 362 个开发 checkpoint 逐行复算，最大概率误差 `1.11e-16`；24 个日期、3,456 个 FMI rows 的 37 项特征公式仍是 `122,950` 次比较零 mismatch。artifact、训练脚本、运行时实现与 5-share expression 已形成可部署闭环。

## Independent shadow preflight audit（已修复并通过）

独立 reviewer/LLM challenger 最初给出 **BLOCKER**：原报告证明的是 research scorer，不是 production runtime parity。四个问题现已统一修复：

1. runtime 原来只看 best ask，research 用真实 5-share depth；8/4 21-NO 在 11:21 的 best-ask 口径为正，扫完 5 shares 后已不是正 EV，正确首笔是 11:42 的 21-YES。
2. `book_fetched_at_utc` 原来记录 HTTP request start。294/294 个旧 checkpoint 都比真正 quote availability 早，p50 `0.389s`、p95 `0.955s`、最大 `6.361s`。
3. 旧 active-book 行只有 NO token。9 笔表达中 6 笔是 YES，若没有真实 YES token identity，research 可以算收益但 authoritative runtime 不能形成正确 intent。
4. weather artifact 声明 85 个特征，旧 live FMI payload 典型只覆盖 `43/85`。新 producer 已按 station `100968` 同时请求 13 个 weather 参数和 6 个 radiation 参数；runtime 改用训练同源共享 feature builder，并补 forecast transition morphology。缺失值仍按 FMI 原始缺测保留，不填造数据。

代码侧已修正 response availability clock、official PIT bracket anchor、真实 YES/NO token、5-share depth/per-level fee、严格正 EV、实际 cadence gap 和 rich feature parity；定向测试 `60 passed`。24 个跨期样本日、3,456 rows 的 37 项 FMI 公式共 `122,950` 次有限值比较为零 mismatch。冻结 2026 audit 的 29,604 rows/210 dates 同分母 A/B 中，rich FMI 将 Brier `0.08918→0.08277`、logloss `0.30757→0.28370`、AUC `0.94864→0.95610`；target-date bootstrap 的 Brier/logloss delta 95% CI 分别为 `[-0.00939,-0.00344]`、`[-0.03176,-0.01605]`。该 audit 只用于部署 parity，不重选模型、cap 或阈值。

用新共享 builder 重放 8/2–11 的相同 282 probability checkpoints 后，model Brier/logloss `0.05227/0.17065`，market `0.05442/0.17542`；9 笔6胜、PnL `+$6.9776`、ROI `+30.31%`，ROI CI `[-14.99%,+67.14%]`。相比旧稀疏 replay，8/4 21-YES 从错误变正确，交易数和日期分母不变；这不是新增 case filter。

## 固定分母与结果

### 历史 OOF（2026-07-20..29）

- 362 checkpoint、9 target dates；其中 active post-source 231 rows/4 dates。
- checkpoint date-equal：模型 Brier/logloss `0.07430/0.24309`，market `0.07641/0.24874`。
- active checkpoint：模型 `0.05392/0.19199`，market `0.05855/0.20394`。
- date-X：模型 `0.07984/0.25590`，market `0.08180/0.26162`。
- NO-only 旧路由：10笔6胜，5-share PnL `+$5.49`、ROI `+22.41%`，target-date bootstrap CI `[-29.65%, +67.00%]`。
- 最终双边表达：13笔6胜，PnL `+$2.08`、ROI `+7.44%`；active only 8笔5胜、ROI `+3.97%`；target-date bootstrap CI `[-33.39%, +64.81%]`。

### 8/2..11 retrospective PIT replay

覆盖已结算的 8 个日期：`08-02/04/05/06/07/09/10/11`。8/3 无 scorable book，8/8 缺 official observation journal，不进入 evidence denominator。

- 同行概率：282 NO rows/8 dates。模型 Brier/logloss/AUC `0.05227/0.17065/0.99281`；market `0.05442/0.17542/0.99111`。
- model-market delta：Brier `-0.00215`、logloss `-0.00477`；date bootstrap CI 分别 `[-0.00698,+0.00332]`、`[-0.01542,+0.00691]`。
- 交易：9笔、6胜3负、6个日期，cash cost `$23.0224`、fee-adjusted PnL `+$6.9776`、ROI `+30.31%`。
- target-date bootstrap ROI 95% CI `[-14.99%,+67.14%]`，中位数 `+31.68%`。
- YES：5笔4胜，PnL `+$6.92`、ROI `+52.93%`；NO：4笔2胜，PnL `+$0.06`、ROI `+0.56%`。两边仍属于一个预注册的竞争表达，不据此关闭 NO。
- 价格档：1–20% 为2笔0胜；40–60% 为3笔2胜、ROI `+22.91%`；60–80% 为3笔3胜、ROI `+53.50%`；80–99% 为1笔1胜。主结果没有过滤任何价格档，且没有 ≤1% / ≥99% 成交。

这 8 天已经在模型设计过程中被查看，因此是 retrospective PIT replay。v1 曾从 `03:30 UTC` 开始记录；v2 在完成选择与 artifact freeze 后把 clean-forward 起点重新锁为 `2026-08-12 08:31:24 UTC`，尚无已结算 v2 forward 日。

## 漏斗

Signal funnel（单位是 FMI event / expression intent，不混入盘口缺失）：

1. 8 个纳入日期共有 713 个 FMI first-seen events；
2. 同一连续机制在每个 event 重估 official exact bracket；
3. 首个严格正 EV + 同档双边竞争后 9 个 expression intents。

Evidence funnel（盘口和标签覆盖）：

1. 713 个 FMI events 中 576 个有任意 active-book 行，137 个是 book coverage gap；
2. 576 个 book-covered checkpoint 中，133 个没有满足 response clock + 120 秒 source window 的 PIT official-anchor book，6 个 source history 不足；437 个完成 adapter evaluation；
3. 437 个中 282 个有双边中点并形成 564 个 YES/NO 5-share executable side rows；
4. 8 个 replay dates 全部有 settlement，6 个日期出现 intent；
5. 0 actual fills、0 orders，全部为 zero-notional retrospective replay。

上述缺口全是 evidence coverage，不是策略过滤。8/3 无 scorable book、8/8 缺 official journal，仍不进入这 8 天 evidence denominator。

## 8/11 失败与结构修复

旧 `c=0.25` 在 8/11 的 18-NO 上由 market `0.545`、weather `0.930` 推到 `0.606`，按有效成本 `0.56238` 入场并亏 `$2.8119`，令旧版累计 ROI 从正转负。新版本没有为 8/11 加规则，而是把训练 loss 改为关注 worst target date，并统一收缩整套天气修正；`c=0.15` 在历史 OOF 的 worst-date logloss 最低。新模型仍会做这笔亏损，但此前较少的错误与更好的 YES 表达使完整窗口保持正 ROI。

## 执行状态与资格

- 已实现：bounded JSON artifact、YES/NO token identity、同档 best-net-edge 去重、5-share depth/fee scorer、response-clock PIT replay、FMI weather+radiation producer、共享训练/runtime feature builder、逐笔 CSV 和 target-date bootstrap。
- 当前资格：`deployable zero-notional shadow candidate / not live-eligible`。feature/runtime parity blocker 已消除；新模型相对旧模型在开发日期上稳定改善，但相对 market 的 proper-score CI 与 8 月 ROI CI 仍跨零，所以不能真实下单。
- production preflight 已重新检查；部署只允许在 strict manifest 无 critical、controller health 健康时执行。METAR 仍不开仓，只可作为持仓退出 A/B。
- v1 已于 `2026-08-12 02:36 UTC` 完成 git-first zero-notional 部署，当时的 forward 起点为 `03:30 UTC`。FMI producer release `65fc4d8f`、city runtime `ddecbbd6`、forecast collector `03691ebb`；Helsinki ladder 使用隔离 release `f6472819`，没有扰动共享 `strategy_runtime`。这些是 v1 的生产证据，不延伸为 v2 已加载证明。
- v2 最终于 `2026-08-12T08:46:03Z` 按 controller/release-pin 合同重载到 `weather_city_probability_runtime_v3`。PID `28932`，production checkout/loaded repo SHA=`62c7f0b9b90b…`，artifact SHA=`fd09be9c39e5…`，config SHA=`1a54abf18539…`；runtime summary 为 `status=ok`、`execution_mode=zero_notional_shadow`、`errors=0`、`orders_submitted=0`。第一轮 Helsinki 只产生 `source_book_clock_gap` blocker：08:31 FMI first-seen 对 08:36–08:40 books 已超过120秒，不强行用陈旧盘口评分；等待下一份新FMI事件属于正确时钟行为。
- 首次重载错误地直接用了研究分支 `7517d52f…`；审计发现共享 runtime 因此夹带了不属于本次部署的 Amsterdam/Tokyo/runtime 变更，随即改为从原生产基线 `70aa30f7…` 只加入 Helsinki v2 的干净 release `62c7f0b9…`。错误窗口为 `08:38:36Z–08:43:11Z`，共5轮summary、12个shadow evaluation、0 paper intent、0 order、0 error；没有资金或成交影响。最终 pre/post manifest 无 session 缺失、findings为空，Amsterdam/Tokyo保持原生产实现。
- pre/post session compare 唯一差异是部署前已在运行的 bounded `weather_canonical_refresh` one-shot 在窗口内自然结束；其 `last_exit_status=0`、日志 `gate_pass=true`。当前 strict manifest 为 healthy、findings=0、DB route healthy，目标 city runtime healthy。全局 controller 仍因无关 `polymarket_weather_proposal_reward_shadow_v1` 缺 tmux/stale health 报 CRITICAL；未在本次 Helsinki 部署中启动或修改它。

## Live-readiness 与完整持仓时间线审计

当前用于决策的唯一 retrospective 结果是 `run=20260812_rich_contract_replay_v1/replay` 和从它确定性生成的 `run=20260812_live_readiness_case_audit_v1/evaluation`。早期 `first_principles_v4/v5`、旧 preflight 分数均标为 superseded-for-decision-use；中断且 artifact hash 错误的 8/7 partial run 只保留在 `quarantine/`，没有进入 runner 输入、282-row 概率分母或9笔交易。原始证据不删除。

8/12 在 v1 窗口收到10个 rich FMI new-content observations（当地06:32–08:01 first seen，`9.5→11.6°C`，全部 `fmi_rich_feature_status=complete`）。当天市场最低可表达档是14，official current maximum仍低于14，因此0 active-book、0 model decision 是 `outside current exact-bracket expression`，不是 source/book 缺失，也不是漏单；这些早于 `08:31:24Z` 的行不计入 v2 forward。这个策略本身不覆盖清晨基于 forecast path 提前买低档 NO。

全部9笔的 entry edge 只有 `0.052c–1.950c/share`，中位 `0.467c/share`；FMI first-seen→可用5-share book lag 中位 `27.8s`、p95 `53.3s`。现有 replay 已用 response clock 和真实深度，但还没有 signal 后的真实 order ack/fill/slippage，所以历史正 ROI 对正式实盘最薄弱的环节不是手续费，而是这些很小的 edge 能否存活到真正成交。

代表性完整时间线：

| 日期/表达 | 入场（Helsinki local） | 当时依据 | 后续路径 | 结算与判断 |
|---|---|---|---|---|
| 8/4 `21 YES` | 14:42，成本49.25%，model 49.71%，edge 0.47c | fade、已回落2°C、forecast future peak低于running max 1°C | 10分钟后盘口YES升到62%，整日未越过21 | 赢 `+$2.54`；方向和时机符合“峰值已成形”直觉 |
| 8/5 `20 YES` | 12:51，成本6.28%，model 6.33%，edge仅0.052c | fresh runway；weather把YES由market 5.5%只上修到6.33% | 11分钟后edge转负，30分钟后official maximum进入21，20 YES不可逆失败 | 输`-$0.31`；不是大概率误判，而是极薄尾部edge被路径延续击穿 |
| 8/7 `21 NO` | 13:01，成本70.07%，model 70.70%，edge 0.63c | pullback/plateau，但forecast仍留约0.22°C overshoot margin | 140分钟后进入22，21 NO锁定胜利 | 赢`+$1.50`；remaining-heat/overshoot机制合理 |
| 8/10 `23 NO` | 13:32，成本9.41%，model 9.74%，edge 0.33c | fade且forecast peak已低于running max，但模型仍给小概率“不停23” | 9分钟后edge转负，最终一直停23 | 输`-$0.47`；典型低价、小优势估计误差，不应被高ROI摘要掩盖 |
| 8/11 `18 NO` | 11:02，成本56.24%，model 58.19%，edge 1.95c | fresh runway、forecast尚有0.5°C margin且距future peak约178分钟 | 10分钟后已失去新增买入edge；之后模型与市场共同持续下修，最终仍停18 | 输`-$2.81`；这是主要结构性坏例：模型高估了上午剩余热量转化为跨档的概率 |

统一的持仓反事实不是“edge一转负就卖”。按每次后续 FMI checkpoint 的更新模型价值，与同刻真实5-share bid减官方fee比较，只有 `net bid > updated hold value` 才退出；9笔中该条件触发 `0/9`。因此当前数据不支持声称 FMI 反转退出能救亏损：模型下修时市场通常已同步或更早下修，可卖价格不足。METAR exit 仍是独立 held-position A/B，不属于本策略已验证能力。

正式实盘三门：

- `significance=FAIL`：9笔/6个交易日，ROI CI `[-14.99%,+67.14%]` 跨0。
- `baseline=FAIL`：model 对 market 的 Brier/logloss 点估更好，但 paired target-date CI 均跨0。
- `forward=FAIL`：已有 clean rich-feature raw，但已结算 forward target dates、forward intents、orders、fills 均为0。
- execution/capacity 也未闭环：没有 signal→execution quote survival、authenticated fill、slippage 或真实5-share成交证据。

所以当前可以继续做生产级 zero-notional shadow 和 live execution wiring 的准备，但**不能切正式盈利实盘**。晋级前至少要让冻结 artifact 在新日期上形成足够的已结算、可执行表达，重新通过同 rows market proper-score、fee-adjusted ROI block-bootstrap 与真实执行价存活三项；现有 family 口径继续以至少30个新 settled target dates 为评审窗，期间不调 `c=0.15`、不追加价格/小时/坏案例阈值。

## 模型稳健性、过拟合与校准审计

本轮不以8月9笔ROI选模型。天气层固定比较2025 expanding OOF `51,451 checkpoints/365 dates`与冻结2026 rich-contract audit `29,604/210`；盘口层在7/20–29的`362 checkpoints/9 dates`上做8种训练/验证互换，并把8/2–11仅作已看过的外部压力测试。

天气基座的结论是“有真实预测能力，但不同信息状态不能混着校准”：

- 2025全OOF Brier/logloss/AUC为`0.04937/0.16654/0.98454`。冻结2026 no-forecast parity arm为`0.08277/0.28370/0.95610`；该arm故意把forecast字段保持缺失，只能检验rich FMI physical fallback，不能冒充完整live forecast路径。
- 2026条件准确率并不均匀：`14–18`当地时间accuracy/Brier为`82.54%/0.12107`，plateau为`75.89%/0.14021`；fade为`92.14%/0.07092`。这确认主要完善对象是peak transition/plateau与下午剩余热量，不是晚间大量简单no-break rows。
- 概率不是全局过度自信，而是呈压缩型S形：预测`10–20%`的rows实际发生率约`4.0%`，预测`70–80%`的rows实际约`83.0%`。2026中`>=95%/<=5%`仍有19个高置信错误rows、5个日期；`>=99%/<=1%`为0错，但2025同档有57错/6日，不能把99%当绝对确定。
- 8种2025时间切分中，Platt calibration只在Brier `3/8`、logloss `4/8`胜raw；slope范围`1.118–1.304`。更重要的是，必须用matching no-forecast rows训练校准：该干净calibrator应用到2026反而把Brier/logloss变为`0.08586/0.28871`，delta CI跨0。因此不部署校准wrapper。此前把forecast-available与missing rows混训会得到虚假的显著改善，已从结论中剔除。

盘口residual层存在更明显的小样本不稳定：

- 9种日期互换里，训练折选出的cap分别为`0.05×2、0.10×1、0.50×4、1.50×2`，没有稳定落在当前`0.15`；当前`c=0.15`只在`5/9` validation splits胜market。
- 固定cap的重复验证均值/minimax会选`c=1.0`，但这个更激进版本在未参与选择的8月回放为24笔10胜、ROI `-1.73%`；说明不能用开发折分数直接扩大天气修正。
- 更复杂的`c015 + Platt market calibration`在开发日期互换中`8/9`胜raw和market，看似很强；但8月回放扩大到27笔23胜仍亏`-$5.32`、ROI `-4.42%`。原因是它把大量接近结算价的高成本赢家加入分母，胜率高却没有正EV，这是明确的过拟合反例。
- 当前`c=0.15`在同一8月压力测试仍为9笔6胜、ROI `+30.31%`；但它的cap本身不稳定、proper-score与ROI CI跨0，所以保留incumbent只代表“小修正比复杂重训更稳”，不代表已证明盈利。

因此本轮选择 v2 替换 zero-notional shadow 的概率 artifact，但不改 live。它已经把 `forecast availability × peak clock/path transition` 作为连续 calibration 纳入统一表达，同时保留强收缩的 `c=0.15` market residual；不按价格、小时或8/11坏例增加 hard gate。下一步不再继续用已看日期调模型，而是让 v2 在 untouched forward 上积累同分母 proper score 与5-share taker；maker 只在 taker forward 至少小正、并有真实 queue/adverse-selection 证据后作为收益放大器。

## 执行证据

- OOF artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_first_principles_v6/oof_model/`
- frozen replay：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_first_principles_v6/frozen_forward/`
- strategy score：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_first_principles_v6/strategy_score/`
- corrected preflight replay：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_preflight_audit_v1/`
- rich feature parity：`docs/analysis/2026-08/generated/helsinki_bounded_market_residual_v1/runtime_feature_parity_v1.json`
- rich-contract replay：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_rich_contract_replay_v1/`
- live-readiness/case timeline：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_live_readiness_case_audit_v1/evaluation/`
- model robustness：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_model_robustness_v1/`
- complete v2 training/freeze：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_complete_model_v1/`
- deployable v2 artifact：`docs/analysis/2026-08/generated/helsinki_bounded_market_residual_v2/helsinki_regime_calibrated_bounded_residual_c015_v2.json`（SHA `fd09be9c…14e9`）
- production loaded identity：FMI `65fc4d8f933f…`、city runtime `ddecbbd6a26a…`、forecast `03691ebb3657…`、Helsinki ladder `f64728197135…`；post-deploy manifest exit `0`、controller `HEALTHY`、0 order。
