# 韩国城市模型 8/4–8/8 forward 绩效

> Busan 与 Seoul 分开；research replay、WCIR coverage runtime 与 CrossNO 实盘互不混算。

## 2026-08-12 追加：新增日期确实提供了正证据，但尚未确认 alpha

本次从 current canonical raw 全量重放 Busan，模型仍固定为 `2026-08-03` 以前选出的
`p_factorized_random_forest_full_weather`，没有用 8/4 以后日期重选模型或更新参数。当前共有
`255` 个 pending states / `20` 个日期；锁模 forward 为 `106` states / `8` 个已结算日期，其中
`65` states / `8` 日有同刻 PIT market 可比。

- 全部 8 日：model logloss `0.2913`，market `0.2902`，model−market `+0.0011`，
  95% CI `[-0.2181,+0.2618]`。两者基本打平，仍未战胜 market。
- 同一固定模型在新增的 8/8–8/11 四日：35 个同盘口 states，model logloss `0.1125`，
  market `0.3354`；delta `-0.2229`，95% CI `[-0.3933,-0.0775]`。四日逐日均胜 market。
- 全 8 日正 edge replay：24 单 / 8 日、19 胜，PnL `+$8.8436`，ROI `+10.26%`，
  95% CI `[-8.32%,+30.81%]`；新增四日单独为 11 单 / 4 日、9 胜，PnL `+$11.6267`，
  ROI `+34.84%`，95% CI `[+16.76%,+54.55%]`。

这批新增日期非常有帮助：它把 8/4–8/7 的明显负面点估拉回到“全窗与 market 打平”，并形成一个连续
四日的正 cluster。但前四日与后四日方向相反、总窗 CI 仍跨 0，因此当前 gate 仍是
`significance=FAIL; baseline=FAIL; forward=FAIL; conclusion=inconclusive`；不部署 Busan probability adapter。
当前 raw 重建也把早期四日的同盘口 coverage 从旧快照的 26 行补到 30 行，因此旧四日 headline 作为历史
快照保留，当前决策使用上述 8 日固定分母。

Seoul 目前有 `36,152` 条 AMOS raw rows / 21 个 target dates，WCIR 当前累计 `6,828` 个 coverage blocker rows，
但仍没有 Seoul 专属冻结 probability artifact；这些数据是可训练覆盖，不是模型成绩。

### WS 对 Busan 的实际贡献边界

Busan WS 自 `2026-08-09T07:59:19Z` 起覆盖 8/9–8/12 四个 target dates、40 个唯一 token、20 个
`date×bracket`；Seoul 当前没有 WS subscription epoch。Busan 当前概率模型的固定 feature set 仍只含天气、
AMOS/routine path、forecast ceiling/peak、云雨风湿度、physical prior 与 checkpoint REST quote，**没有 WS dynamics，
也没有 Busan `feature_book_snapshot_id`**。所以新增四日的好结果不能归因给 WS。

WS 已经对执行研究有价值：8/10 的 generic hot-strip `bid+1 native tick` negative control 中，Busan 有
627 个反事实 posts、13 个 queue-conservative exit-scoreable fills，60 秒 fee-adjusted PnL `-$0.6111`、
ROI `-1.24%`。这说明更细盘口能识别 passive fill 后的 adverse selection；但该分母不是天气模型信号，
不能当作 Busan 概率 alpha。下一步应在同一 AMOS first-seen checkpoint 上固定比较
`weather+level` 与 `weather+level+WS dynamics`，而不是把 raw frame/epoch 数当训练样本。

## 数据快照

| 项目 | 值 |
|---|---|
| 观测截止 | `2026-08-08T06:10:41Z`；Busan 8/4–8/7 已结算，8/8 未结算 |
| canonical identity | `/Volumes/jrs/pm_agents/runtime/weather.db`，device `16777247` / inode `54444`；manifest `healthy` |
| settlement 补全 | 8/4–8/7 Busan/Seoul 8 city-days、88 bracket outcomes；本轮补 7 个缺失文件、77 条 outcomes |
| Busan 模型分母 | 67 个 15-minute pending states / 5 target dates；已结算 56 / 4；同刻 PIT quote 26 / 4 |
| Seoul 概率分母 | 0；WCIR adapter 仍是 `city_probability_model_not_deployed` coverage-only |
| DB builds | settlement producer `d1cf1ade3c0187a81a605f01d1c67ab67e4df08f`；`fact_trades` `2026-08-08T06:09:06Z` |
| machine artifact | `manifests/busan_model_forward_20260808.json`，12 files / 1,493,366 bytes，JRS archive content-addressed |
| CLOB gate | NA：本报告没有发布 `live_real` PnL，模型 actual fills 为 0 |

## 结论与动作

Busan 锁模 forward **没有战胜市场，也没有形成正交易表达**。8/3 后不再选模型或更新参数，固定使用 development 冠军 `p_factorized_random_forest_full_weather`：同盘口 26 states/4天的 model logloss `0.6955`，market `0.3106`；model−market `+0.3849`，target-date bootstrap 95% CI `[-0.0893,+1.0335]`。点估明显更差，且不显著。

positive-edge fee-adjusted replay 为 13单/4天、10胜，PnL `-$3.4298`、ROI `-6.42%`，95% CI `[-23.02%,+12.85%]`。因此维持 collector/research，**不部署 Busan probability adapter，不启动 Busan probability shadow**。Seoul 目前只有 AMOS/盘口 coverage，尚没有可评价的冻结概率模型，不能把“runtime 在跑”说成“Seoul 模型有成绩”。

```text
significance=FAIL; baseline=FAIL; forward=FAIL; conclusion=inconclusive
```

## Target metric 与固定分母

- target：`P(final exact current routine rung NO | PIT source/path/weather state)`；exact bracket，不是 touch。
- grain：Busan 15-minute pending source-cross state；同一 date-rung 的交易表达只取首次正 edge。
- train / forward：模型族与冠军只用 `<=2026-08-03` development；`2026-08-04..08-07` 锁模评估，8/8 只记未结算 capture。
- features：source/routine margin、path 15m/60m、peak clock、forecast ceiling、湿度/露点差、未来3h云雨风、long physical prior。
- baseline：同一 checkpoint 的 NO market probability；成交表达用当时 NO ask、可见 ask size 与 weather fee。

## Signal 与 evidence funnel

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| pending mechanism states | 15-minute state | 67 | 5 | 含 8/8 未结算 11 states |
| settled states | 15-minute state | 56 | 4 | 8/4–8/7 |
| same-row PIT quote | 15-minute state | 26 | 4 | 其余 30 rows 是盘口 coverage gap，不是策略过滤 |
| positive-edge expression | first date-rung | 13 | 4 | ask + fee 后 edge > 0 |
| actual model fill | fill | 0 | 0 | research replay，不是 shadow/live fill |

## Frozen forward 结果

| target date | same-market states | model logloss | market logloss | ΔLL | replay orders | replay ROI |
|---|---:|---:|---:|---:|---:|---:|
| 2026-08-04 | 5 | 2.0285 | 0.6868 | +1.3417 | 3 | -26.44% |
| 2026-08-05 | 5 | 0.1966 | 0.0875 | +0.1091 | 3 | -16.50% |
| 2026-08-06 | 6 | 0.3857 | 0.0738 | +0.3119 | 3 | -10.52% |
| 2026-08-07 | 10 | 0.1713 | 0.3943 | -0.2230 | 4 | +19.88% |

四天中仅 8/7 同时在概率质量和交易 replay 上胜过市场。全 settled 56 states 的 model logloss `0.4265`、threshold accuracy `82.81%`；但限定为有同刻盘口的可比行后，model accuracy `73.33%`，market `82.50%`。这说明高总体命中率主要来自 NO label 基础率，不能当作 alpha。

## 三门与残余风险

| 门 | 结果 | 证据 |
|---|---|---|
| significance | FAIL | model−market ΔLL CI 跨 0，且点估为正 |
| same-denominator baseline | FAIL | `0.6955 > 0.3106` |
| frozen forward | FAIL | ROI 为负；4个独立日期只有1天胜 market |

主要风险是只有 4 个独立 settled target dates、PIT quote 只覆盖 26/56 states、K=18 的模型族选择仍属探索性。后续只 append 新日期到同一锁模评测；不得根据 8/7 单日正例换模型或加 threshold。
