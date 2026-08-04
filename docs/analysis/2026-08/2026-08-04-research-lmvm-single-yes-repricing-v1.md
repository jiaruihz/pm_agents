# LMVM-style D-2/D-1 单档 YES repricing v1

> 2026-08-04 纠偏：zero-notional collector 已停止并移出 production desired state。
> 本策略必须先完成历史 `ΔPmodel−ΔPmarket` 同分母回测和 frozen forward；下文保留
> 先前 collector 启动记录仅作审计，不代表当前运行状态或研究升级。

## 数据快照

| 字段 | 值 |
|---|---|
| 数据源 | immutable paper snapshots `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/paper_snapshots` + canonical settlement `/Volumes/jrs/pm_agents/runtime/weather.db` |
| 数据快照时间 | 2026-08-04T14:07:41.984084+00:00 |
| DB identity | device=16777239, inode=1636907, mtime=2026-08-04T13:48:20.427355+00:00 |
| snapshot files | 6,357（invalid=1） |
| D-2/D-1 complete ladders | 79,656 |
| forecast update events | 12,413（left-censored initial state 已剔除主分母） |
| single-rung candidates | 37,239 across 3 same-row arms；primary eligible=12,363 |
| unsettled | 396 / 12,413 update states |
| missing_bracket | 1 |

## 冻结问题与动作

目标是在所有 D-2/D-1 forecast first-seen update 的固定 city-day ladder 分母上，检验
`P(final exact bracket)-market` residual 是否能预测未来固定时限的可执行 repricing。
当前动作固定为 **research / zero-notional**；本脚本不改 live、不会产生 order。

```text
signal funnel:
snapshot files -> D-2/D-1 complete ladders -> non-left-censored forecast update
-> one residual-argmax YES per city-day/update -> positive after-entry-fee residual

evidence funnel:
PIT model probability -> same-snapshot complete direct book -> executable taker ask/depth
-> 5/15/30/60/120m first observed bid -> double-sided Weather fee -> settlement label
```

## 概率层：同 rows market baseline

| metric | model | market | model-market delta | target-date 95% CI |
|---|---:|---:|---:|---:|
| Brier | 0.085398 | 0.068673 | +0.016725 | [+0.015093, +0.018369] |
| logloss | 2.701873 | 1.414771 | +1.287102 | [+1.091192, +1.504523] |

settled update states=12,017，independent target dates=64。

## 固定 horizon 可执行 markout

Weather taker fee 在 entry ask 与 exit bid 两边都计入；每笔最多 5 shares，并受 entry ask size / exit bid size 限制。
未来 snapshot 必须在预注册 tolerance 内到达，否则保留为 coverage gap。

| policy | lead | horizon | covered/signals | turnover ROI | target-date 95% CI |
|---|---:|---:|---:|---:|---:|
| forecast_mode | D-1 | 5m | 4/12112 | -13.60% | [NA, NA] |
| forecast_mode | D-1 | 15m | 543/12112 | -14.06% | [-14.90%, -13.49%] |
| forecast_mode | D-1 | 30m | 8966/12112 | -13.32% | [-14.05%, -12.70%] |
| forecast_mode | D-1 | 60m | 10017/12112 | -13.01% | [-13.66%, -12.45%] |
| forecast_mode | D-1 | 120m | 8789/12112 | -12.81% | [-13.48%, -12.22%] |
| forecast_mode | D-2 | 5m | 0/301 | NA | [NA, NA] |
| forecast_mode | D-2 | 15m | 0/301 | NA | [NA, NA] |
| forecast_mode | D-2 | 30m | 240/301 | -13.88% | [-16.62%, -11.74%] |
| forecast_mode | D-2 | 60m | 284/301 | -12.80% | [-14.87%, -10.99%] |
| forecast_mode | D-2 | 120m | 271/301 | -11.66% | [-13.79%, -10.04%] |
| market_favorite | D-1 | 5m | 4/12112 | -6.94% | [NA, NA] |
| market_favorite | D-1 | 15m | 543/12112 | -10.53% | [-10.84%, -9.80%] |
| market_favorite | D-1 | 30m | 8966/12112 | -10.89% | [-11.58%, -10.33%] |
| market_favorite | D-1 | 60m | 10017/12112 | -10.82% | [-11.42%, -10.30%] |
| market_favorite | D-1 | 120m | 8789/12112 | -10.85% | [-11.52%, -10.27%] |
| market_favorite | D-2 | 5m | 0/301 | NA | [NA, NA] |
| market_favorite | D-2 | 15m | 0/301 | NA | [NA, NA] |
| market_favorite | D-2 | 30m | 240/301 | -12.13% | [-14.36%, -10.16%] |
| market_favorite | D-2 | 60m | 284/301 | -11.71% | [-13.64%, -9.97%] |
| market_favorite | D-2 | 120m | 271/301 | -11.12% | [-12.69%, -9.69%] |
| residual_argmax | D-1 | 5m | 4/12066 | -9.00% | [NA, NA] |
| residual_argmax | D-1 | 15m | 543/12066 | -18.36% | [-22.20%, -17.17%] |
| residual_argmax | D-1 | 30m | 8928/12066 | -17.22% | [-17.89%, -16.62%] |
| residual_argmax | D-1 | 60m | 9975/12066 | -16.66% | [-17.27%, -16.08%] |
| residual_argmax | D-1 | 120m | 8752/12066 | -16.01% | [-16.62%, -15.44%] |
| residual_argmax | D-2 | 5m | 0/297 | NA | [NA, NA] |
| residual_argmax | D-2 | 15m | 0/297 | NA | [NA, NA] |
| residual_argmax | D-2 | 30m | 237/297 | -17.49% | [-20.00%, -15.13%] |
| residual_argmax | D-2 | 60m | 280/297 | -15.13% | [-17.42%, -13.03%] |
| residual_argmax | D-2 | 120m | 268/297 | -12.88% | [-15.51%, -10.69%] |

## 为什么机械复制失败

| lead | signals | median ask | median spread | 60m covered | bid > entry ask | 60m net ROI |
|---|---:|---:|---:|---:|---:|---:|
| D-1 | 12,066 | 0.140 | 0.016 | 9,975 | 8.99% | -16.66% |
| D-2 | 297 | 0.210 | 0.026 | 280 | 13.57% | -15.13% |

即使事后单列 LMVM 历史上常见的 `0.20–0.40` entry ask（不把它升级为 eligibility gate），
60m 仍只有 3,126 个 covered rows、含费 turnover ROI=-14.06%。
主要损耗不是“持有时间没选准”，而是模型概率校准弱于盘口，同时 taker ask→future bid 需要先跨过 spread 和双边 fee。
如果将来研究 maker 表达，必须另有 trade-through/queue-position/fill 证据；不能把挂单价被触碰当作已成交。

## 解释边界

- `residual_argmax` 每个 update 只选一档；不使用 LMVM 历史 0.20–0.40 价格带作 gate。
- `forecast_mode` 与 `market_favorite` 是同分母对照，不是额外调参候选。
- snapshot 的 `model_prob` 是当时系统概率 telemetry；forecast state 以 hash first-seen 为主，旧行 fallback 单列。
- fixed-horizon bid markout 衡量短期 repricing，不等同 settlement PnL；没有 bid/depth 就不假装可退出。
- public LMVM selected fills 只启发生命周期，不进入我们的候选选择、模型训练或绩效分母。

## 三门

当前历史同分母结果为：

```text
significance=FAIL（primary 30/60/120m markout CI 全部低于 0）
baseline=FAIL（model proper score 显著差于 same-row market；primary markout 也差于 market-favorite）
forward=NA
conclusion=historical FAIL_CURRENT_EVIDENCE / zero-notional research / no shadow or live promotion
```

## Forward delta-innovation collector

历史失败的是 static residual + taker/taker 表达，不等于 forecast update 方向已证伪。新的
`lmvm_forecast_innovation_single_yes_v1` 固定为：

```text
forecast_values_hash first-seen change
-> complete D-2/D-1 exact-bracket distribution
-> score every YES rung by ΔPmodel - ΔPmarket
-> select exactly one argmax rung
-> zero-notional TradeIntent
-> append-only 20/60/120/180m bid/ask/depth markout
```

raw 输出位于 JRS `output/lmvm_forecast_repricing_shadow_v1/`：

- `forecast_updates.jsonl`：forecast update grain；
- `decision_bundles.jsonl`：`InformationEvent → checkpoint → ModelOutput → SignalCandidate`；
- `trade_intents.jsonl`：selected rung 的 zero-notional、record-only intent；
- `quote_markouts.jsonl`：后续盘口，不把 quote cross 冒充 maker fill；
- `state.json` / `latest.json`：去重、open candidate 与健康状态。

2026-08-04 actual-JRS smoke 读取最近 64 份 snapshot、63,855 个 record rows，得到
227 个 complete D-2/D-1 ladder states、29 个当前 baseline streams；首轮只建立
left-censored baseline，按设计不回填候选。maker queue 当前只能记录 visible top-of-book
queue ahead；没有真实 order/trade prints 时 `maker_fill_status` 固定为 not observable。

该 forward collector 不改变上面的历史三门结论。后续需要新日期累计后，固定比较 delta selector、
static residual 与 market favorite 的 proper score 和含费 markout；未过三门前保持 zero-notional。

生产接入于 2026-08-04 完成：controller-managed session
`weather_lmvm_forecast_repricing_shadow_v1`，干净 checkout SHA `e405d4a6`，execution mode
`zero_notional_shadow`。启动后 PID `49022`；后验 manifest 与 pre-state 相比没有丢失任何既有 JRS
session，canonical DB route 保持 healthy。首个持续循环 `latest.json` 为 `status=ok`、
`phase=forward`、`zero_notional=true`、`no_plan_created=true`、`no_order_placed=true`；当时最近完整
D-2/D-1 snapshot 为 `2026-08-04T13:47:42Z`，age 3,303 秒。health 上限按 full-ladder 实际抓取
预算与间隔固定为 5,400 秒，真实 age 始终原样发布，超过后 fail closed 为 `stale`。
