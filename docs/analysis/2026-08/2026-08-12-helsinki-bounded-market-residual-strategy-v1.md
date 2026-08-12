# Helsinki bounded market-residual exact-bracket strategy v1

## 结论

交付策略为 `helsinki_bounded_market_residual_c015_symmetric_v1`。它不是天气模型单独猜最终温度，也不是跟着盘口复制：盘口是 prior，FMI remaining-heat 概率只允许在 logit 上有限修正；每个 `target_date × current bracket` 比较真实 5-share YES/NO ask 与官方 taker fee，只在净 EV 为正时选择较优一边，首次入场后不重复开同档。METAR 不开仓，当前版本持有到 settlement。

公式：

```text
p_no = sigmoid(logit(market_no) + 0.15 * tanh((logit(weather_no)-logit(market_no))/0.15))
```

`0.15` 不是按交易 ROI 挑选。固定旧 OOF 的 bounded family 中，它最小化 worst-target-date checkpoint logloss；没有增加价格、小时、天气形态或事后坏日期阈值。研究 joblib SHA-256 为 `3d57f1ce9eadeb11d3699df9617d44c0d2b55814fb0250847708f4bfb2ec2cd0`。它不是可直接部署的 artifact；git 内准备的 JSON artifact 使用唯一 `model_id`，并明确标为 `blocked_preflight_audit`。

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

这 8 天已经在模型设计过程中被查看，因此是 retrospective PIT replay，不冒充下一版 untouched forward。下一份结算日开始才是 artifact freeze 后的真正 forward。

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
- 当前资格：`zero-notional shadow eligible / not live-eligible`。feature parity blocker 已消除；proper-score 与 ROI CI 仍跨零，所以只能积累 clean forward，不能真实下单。
- production preflight 已重新检查；部署只允许在 strict manifest 无 critical、controller health 健康时执行。METAR 仍不开仓，只可作为持仓退出 A/B。

## 执行证据

- OOF artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_first_principles_v6/oof_model/`
- frozen replay：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_first_principles_v6/frozen_forward/`
- strategy score：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_first_principles_v6/strategy_score/`
- corrected preflight replay：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_preflight_audit_v1/`
- rich feature parity：`docs/analysis/2026-08/generated/helsinki_bounded_market_residual_v1/runtime_feature_parity_v1.json`
- rich-contract replay：`/Volumes/jrs-archive/pm_agents/research/artifact_store/helsinki_bounded_market_residual/run=20260812_rich_contract_replay_v1/`
