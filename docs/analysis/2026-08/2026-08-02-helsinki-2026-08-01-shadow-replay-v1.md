# Helsinki 2026-08-01 probability shadow replay v1

## 结论

8 月 1 日 Helsinki 最终 exact bracket 为 **23°C**。当前两套 shadow 模型当天都产生了三笔首次正 edge paper intent：
`21 NO`、`22 NO`、`23 NO`。前两笔赢，第三笔输；按每笔 5 shares、官方 taker fee 计，两套模型都是
`2/3`、PnL `-$1.6903`、ROI `-14.46%`。没有真实订单和 fill。

这一天不能算“模型稳定盈利”的正证据。核心错误不是天气路径完全看错，而是模型在 23°C 刚出现时高估了继续升到
24°C 的概率，并由 first-positive position state 固化成一笔持有到结算的 `23 NO`。到 11:30 UTC 后，模型已经把
`23 NO` 概率降到 25.4%，但 paper state 没有退出或重估，因此早先错误仍完整落到结算。

## label 与时间

- EFHK METAR 当天最终最高 23°C，最后一次新高在 `2026-08-01 13:20 UTC`（Helsinki 16:20）。
- Polymarket market `3216813` 已 resolved：23°C `YES=1`、`NO=0`。
- 8 月 2 日在本次审计时 Helsinki 仅为当地凌晨 03:20：当前 14°C、running max 15°C，目标日未结束，不能当最终结果。

## 固定分母概率质量

盘口为双边、模型与 market 可在同 rows 比较的分母为每套模型 45 个 expression checkpoint。

| 模型 | accuracy | Brier | logloss | AUC | 同 rows market accuracy / Brier / logloss / AUC |
|---|---:|---:|---:|---:|---:|
| `helsinki_market_expression_v2_research_challenger` | 93.33% | 0.03962 | 0.14577 | 1.000 | 97.78% / 0.02593 / 0.12685 / 1.000 |
| `helsinki_market_offset_fade_v1` | 91.11% | 0.04502 | 0.14222 | 1.000 | 97.78% / 0.02593 / 0.12685 / 1.000 |

当天 challenger 的 Brier 和 accuracy 优于 incumbent，但 logloss 略差；两者整体仍未胜过 market。AUC 都为 1 只说明
当日排序正确，不能掩盖概率校准和交易表达错误。只有一个 target_date，target-date block bootstrap 不适用。

错误集中在最终赢家 23 档：challenger 在 23 档的 Brier 为 `0.0650`，market 为 `0.0397`；其中 fresh-runway slice
为 `0.0696` 对 `0.0391`。相比之下，21、22 档模型判断正确，并在 22 档优于 market。

## 三笔 paper intent

两个模型触发了相同的三个 expression，所以交易结果完全相同，只是估计 edge 不同。

| 决策 UTC | 路径 | 表达 | challenger P(NO) | incumbent P(NO) | market midpoint | ask + fee | 结果 | 5-share PnL |
|---|---|---|---:|---:|---:|---:|---|---:|
| 07:53 | fresh runway | 21 NO | 99.49% | 99.83% | 98.65% | 99.3348¢ | 赢 | +$0.0333 |
| 10:44 | pullback | 22 NO | 95.82% | 97.52% | 92.50% | 94.2820¢ | 赢 | +$0.2859 |
| 10:53 | fresh runway | 23 NO | 57.27% | 62.00% | 36.00% | 40.1895¢ | 输 | -$2.0095 |

最后一笔是决定性错误：天气 forecast ceiling 当时已经低于 24°C boundary，但当前创新为正，旧模型把这种
“刚打印 23°C + fresh runway”解释成较强 overshoot。随后 11:30 UTC 进入 fade 时 challenger 已降至 25.4%，
13:20 UTC 降至 3.8%，说明路径更新本身会纠错；缺的是持仓层对概率反转的统一处理。

## 双漏斗与链路

Signal funnel：85 个 expression checkpoint → 45 个双边可评分 checkpoint + 40 个单边盘口 checkpoint →
两模型合计 46 个逐 checkpoint 正 edge 状态 → 6 个首次正 edge intent（3 个经济表达 × 2 模型）。逐 checkpoint
正 edge 不是重复下单；position key 只保留每个 bracket/model 的首次 intent。

Evidence funnel：settlement 已确认 → 45 个同 rows market baseline → 6 个 paper intent → 0 order → 0 fill。
决策窗口内 0 error。最后一条 evaluation 为 18:52 UTC；之后到 Helsinki 本地午夜出现 94 条 stale-book error，属于
收盘后 collector 不再更新而 runner 仍轮询的链路尾部问题，不污染上述 85 个 checkpoint，但应改为正常的 market-ended
状态而不是重复异常。

## 判定

- 保持 zero-notional shadow，不升 live。
- challenger 保留 shadow：它当天改善了 incumbent 的 Brier/accuracy，但没有改变 expression，也没有战胜 market。
- 下一项统一修复应是 position state：概率反转后支持退出/减仓/重新选择 exact bracket，并用历史全分母 PIT 重放，
  不能根据这一个坏例子追加事后阈值。
- 结构 v3 artifact 在 8 月 1 日盘中以后才 freeze，本日不能作为 clean forward；本报告不拿它包装 forward 结果。

## 证据

- 机器可读审计：`generated/helsinki_20260801_shadow_replay_v1/audit.json`
- 原始 forward journals：`/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_shadow_v2/`
- EFHK observation journal：`/Volumes/jrs/weather_data_feed_service_runtime/output/observations/2026-08-01/observations.jsonl`
- 可复跑脚本：`scripts/analysis/reheat_risk/audit_helsinki_probability_shadow_day_v1.py`
