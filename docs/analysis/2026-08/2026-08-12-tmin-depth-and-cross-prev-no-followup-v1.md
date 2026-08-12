# 绩效分析：Tmin remaining-depth 与 cross previous-NO

> 窗口：2026-07-15 — 2026-08-12  
> 策略身份：`daily_low_temperature_next_colder_no_v1`；`weather_tmin_cross_prev_no_shadow_v1`  
> evidence layer：JRS research artifact + current append-only shadow raw；无实际 fill

## 结论与动作

没有得到可升 live 的盈利策略。remaining-cooling depth 明显改善了 weather-only proxy 模型，但作为
market residual 在严格 expanding OOF 上仍输给同 rows market。cross previous-NO 的全可执行历史 edge
接近零；`NO ask <= 0.90 + 每 city-day 第一笔 eligible cross + 5 shares` 有正点估，但只有 5 个日期且
置信区间跨零。本轮把它冻结为 zero-notional challenger，继续沿现有全分母 journal 收集，不改 live。

2026-08-13 deployment follow-up：该 challenger 已接入现有
`weather_tmin_cross_prev_no_shadow_v1`，独立 release `tmin_cross_prev_no_shadow` 加载 SHA
`d450aba34a84ceb1d8344177cefad55de322d41f`。全量 candidate journal 不变，合格行另写
`shadow_decisions.jsonl`；上线验收为 1 个 pre-policy candidate、0 个 forward decision、0
intent/order/fill/venue write，后续新 target dates 才计 untouched forward。

```text
significance=FAIL; baseline=FAIL; forward=FAIL; conclusion=shadow_candidate/inconclusive
```

## 数据快照

| 项目 | 值 |
|---|---|
| raw 覆盖截止 | candidates snapshot 51 rows / 17 target dates；其中 8/12 两条未结算 |
| canonical identity | manifest `db_route.status=healthy`，repo 入口与 JRS physical 同 device/inode |
| manifest 例外 | overall critical：另一个 live process 的 loaded SHA 与 release pin 不一致；本分析未读该进程输出、未写 DB |
| direct quote | 51/51 exact checkpoint join；29 有 NO ask；27 有至少 5 shares top depth |
| settlement | 49 closed binary；只接受 `event.closed && market.closed`，open near-binary 不算 settlement |
| actual fills | 0；全部为 zero-notional shadow replay |
| fee | Weather taker `0.05 * p * (1-p)` per share |

Artifacts：

- `daily_minimum_next_colder_no/run_20260812_remaining_cooling_depth_v3/`
- `daily_minimum_no_further_cooling/run_20260812_market_history_v3_depth_followup/`
- `tmin_cross_prev_no_performance/run_20260812_cap90_v4/`

## Target metric 与固定分母

- depth unit：固定 PIT checkpoint；612 panel rows → 164 source rows → 140 proxy labels → 98 expanding OOF rows / 15 dates。
- cross unit：每个 source 首次跨过一个更冷 rung 后，对刚离开的 previous-warmer exact bracket 买 NO。
- price：同 checkpoint direct NO ask；容量要求 top ask depth `>=5 shares`。
- settlement：closed Gamma event 内 matched `condition_id` 的 closed binary market。
- 主指标：概率层 target-date equal-weight logloss/Brier；交易层 5-share fee-adjusted ROI。
- baseline：depth residual 对同 rows raw market；cross 表达对 executable cost，因尚无冻结 `P(NO|cross)`，概率 baseline gate 记 FAIL。

## Signal / evidence funnel

| 漏斗 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| fixed Tmin checkpoints | checkpoint | 612 | 32 | 三城、6 个 local clocks |
| depth PIT source | checkpoint | 164 | 25 | source coverage |
| depth OOF | checkpoint | 98 | 15 | proxy label，非 settlement |
| cross candidates | cross/rung | 51 | 17 | 全部保留，不用价格当 signal gate |
| exact quote joined | cross/rung | 51 | 17 | same checkpoint |
| raw NO ask | expression | 29 | 14 | coverage，不是策略筛除 |
| 5-share executable + settled | expression | 27 | 13 | 17 city-days |
| actual fills | fill | 0 | 0 | zero-notional |

## Probability quality

remaining-cooling depth 定义为从 checkpoint 的 source running minimum 到 EOD proxy minimum 的 native
tick 数，classes 为 `0/1/2/3+`。hurdle head 先估 `P(depth=0)`，再估 conditional severity。

| candidate | rows/dates | logloss | Brier/RPS | delta vs baseline | 95% CI |
|---|---:|---:|---:|---:|---|
| depth multinomial | 98 / 15 | 0.7061 | RPS 0.0585 | logloss -0.2651；RPS -0.0359 vs clock | logloss [-0.4121,-0.1379]；RPS [-0.0746,-0.0079] |
| structured exact-NO proxy | 98 / 15 | 0.4529 | 0.1205 | logloss -0.1446；Brier -0.0125 vs direct binary head | logloss [-0.3452,-0.0150]；Brier [-0.0271,-0.0014] |
| depth residual + market | 68 / 10 | 0.1903 | 0.0598 | logloss +0.0267；Brier +0.0137 vs market | logloss [-0.0067,+0.0739]；Brier [-0.0002,+0.0291] |
| same rows market | 68 / 10 | 0.1636 | 0.0462 | baseline | — |

最后一行 residual 的 alpha 每个 test date 只用此前 common dates 在
`[0,.1,.25,.5,1]` 中选择。depth 修复了旧 innovation 的大部分误差，但没有打败 market，因此 proper-score
门失败；任何 selected trade ROI 仍只可作探索。

## Fee-adjusted trade performance

| slice | expressions | dates | wins-losses | 5-share cost | PnL | ROI | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| 全部 settled + depth>=5 asks | 27 | 13 | 25-2 | $124.8831 | +$0.1169 | +0.09% | [-15.74%,+11.32%] |
| frozen `cap90 + first eligible/city-day` | 5 | 5 | 4-1 | $17.6031 | +$2.3969 | +13.62% | [-49.24%,+88.32%] |

`cap90` 是开发窗口看过后的 challenger，不是 untouched forward。探索性 price-cap grid 共 6 个版本、
未做多重检验校正；因此不能用它的正点估升级 live。完整明细与 input journal SHA 在 performance artifact。

### 结构篮子与 repricing exit

另检验了三种同刻、每腿至少 5 shares 的结构篮子。`previous NO + current YES + next NO` 在 26 条
三腿覆盖 rows 上没有一条 fee-adjusted bundle cost < 1，故没有真套利。`current YES + next NO`
有 15 条/8 dates cost < 1，但它并不覆盖所有终局：settlement fee-adjusted ROI 仅 `+3.06%`，CI
`[-37.80%,+64.78%]`。`previous NO + current YES` 只有 1 条 under-1，不能成为策略证据。

source-cross 后按真实后续 NO best bid 做 taker exit 也失败。journal cadence 约 10 分钟，所以固定
10/30/60/120/240m horizon，目标后最多容忍 12 分钟采样延迟，并要求 entry/exit 均有 5-share depth：

| exit horizon | rows/dates | fee-adjusted ROI | 95% CI |
|---|---:|---:|---|
| 10m | 27 / 13 | -6.55% | [-12.35%,-1.49%] |
| 30m | 26 / 13 | -4.33% | [-10.54%,+0.75%] |
| 60m | 26 / 13 | -5.36% | [-12.86%,+0.68%] |
| 120m | 19 / 13 | -6.91% | [-12.05%,-1.78%] |
| 240m | 19 / 12 | -9.89% | [-17.88%,-2.77%] |

因此 first-cross 后的 taker repricing 不是退出型 alpha；10/120/240m 已有显著负证据。该结论不否定
未来有真实 queue/fill 证据的 passive maker 机制，但当前 journal 不能用 future touch 推 maker fill。

## 数据问题与影响

market-history helper 原逻辑只看 near-binary `outcomePrices`，未强制 event/market closed，因而可能把
open market 的 `0.999/0.001` 当 settlement。8/12 follow-up 中受影响的是当前两条 open candidates；
本报告 evaluator 已改为 fail closed 并将其列为 unsettled。8/11 结束的既有 frozen model training window
不含这两条，未受影响。该 helper 尚在独立 production release 分支，不能在控制分支无审计改写 release
checkout；后续合并时必须带 closed-state regression test。

## 三门与下一动作

| 门 | 结果 | 证据 |
|---|---|---|
| significance | FAIL | cap90 ROI CI 跨 0；只有 5 dates |
| same-denominator baseline | FAIL | depth residual 未胜 market；cross 尚无冻结概率模型 |
| forward | FAIL | cap90 本轮才冻结，clean forward 为 0 |
| execution | PARTIAL | 有 direct ask/top depth，无 fills/queue/adverse-selection 证据 |
| taker repricing | FAIL | 10m ROI -6.55%，CI 上界 -1.49%；更长 horizon 亦无正证据 |

动作固定为：保持全 candidate denominator 的 zero-notional collector；从下一新 target_date 起按
`tmin_cross_prev_no_cap90_first_cityday_v1` 写 production zero-notional frozen-forward decision，不删除或过滤未选 rows；累计至少
30 个新 settled target dates 后一次性复核 proper score、fee-adjusted ROI 与 source→settlement basis。

在 2026-07-15—2026-08-11 的 27 个 settled、5-share 可执行 expressions 上，全量 previous-NO 的
fee-adjusted ROI 为 +0.09%（95% CI [-15.74%,+11.32%）；cap90 challenger 为 +13.62%
（[-49.24%,+88.32%]），forward FAIL，结论 `inconclusive/shadow_candidate`，不改 live。
