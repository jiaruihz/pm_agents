# Amsterdam CrossNO / V7 shadow scorecard v1

Status: `2026-08-11 frozen-forward update / market baseline fail / CrossNO positive low-sample / no-live-change`

## 数据快照

- observed at：`2026-08-11T13:42:32Z`；production manifest `status=healthy`，canonical DB route healthy，
  `runtime/weather.db` 与 `/Volumes/jrs/pm_agents/runtime/weather.db` 为同一 device/inode。本轮未同步、未重建。
- WCIR raw：`decision_bundles.jsonl`、`trade_intents.jsonl`、`next_metar_outcomes.jsonl`；概率主分母按
  `KNMI initial observation × target` 去重，不把同一 provider item 的重复迁移行重复计分。
- clean EOD probability：V7 `144 rows / 4 settled target dates`，market-prior A `143 / 4`；unsettled 0，
  missing settlement bracket 0。
- deployed intent：V7 `9 settled / 7 dates`；market-prior A `4 settled + 1 open / 5 dates`。clean-forward
  intent 分别为 `3 settled / 3 dates`、`3 settled + 1 open / 4 dates`；全部是 zero-notional displayed-book
  counterfactual，actual fill=0。
- next-routine head：`240 resolved predictions / 3 target dates`，unsettled 0。
- KNMI ladder：`3,530` 个 first-seen events、`21,180` 个 event-offset captures，`20,894` complete，
  complete rate `98.65%`；其中 `1,239 new_content + 2,293 revision`。微观结构主表只保留 new-content，
  不用 revision 重加权。
- 已知污染剔除：2026-08-10 `00:00:00Z..08:27:54Z` 的 previous-local-day running-max anchor；该段原始
  append-only rows 保留，但不进入 clean-forward score。污染影响为 62 checkpoints / 184 model-expression rows / 2
  zero-notional candidates / 0 WCIR orders。
- 可复跑：`scripts/analysis/forecast_quality/research_amsterdam_knmi_shadow_scorecard.py`；结果
  `generated/amsterdam_knmi_shadow_forward_scorecard_v2/{summary.json,selected_intents.csv,cross07_executable.csv}`。

## 2026-08-11 结论与动作

真正的发现有三条：

1. **V7 weather-only 不能作为交易 fair price，已经是明确的 baseline fail。** clean same-row 上 V7
   Brier/logloss=`0.21860/0.63510`，market=`0.05427/0.18935`；按 target-date 等权的
   model-market ΔBrier=`+0.16021`，95% CI `[+0.12020,+0.20023]`，Δlogloss=`+0.44834`，
   CI `[+0.35757,+0.51690]`。正值代表模型更差。
2. **market-prior A 把概率拉回了市场附近，但当前 frozen correction 仍在 4/4 clean settled dates 上恶化。**
   model Brier/logloss=`0.05863/0.20119`，market=`0.05640/0.19276`；date-equal
   ΔBrier=`+0.001905`，CI `[+0.000090,+0.003721]`，Δlogloss=`+0.007375`，
   CI `[+0.002051,+0.012700]`。因此不能把“更接近市场”误写成“打败市场”。
3. **first-seen 后并非完全没有盘口窗口，但窗口集中在少数事件和 30–60 秒，而不是每条 KNMI 都能交易。**
   12–15 当地时间的 140 个 complete new-content events 中，t0 后 15/30/60 秒至少一个 rung 移动 ≥1c 的比例为
   `22.1%/40.0%/54.3%`，p90 最大 rung move 为 `1.5c/3.0c/5.0c`。这是 repricing 证据，尚不是
   executable alpha。

当前动作：V7 只保留 weather feature/head；market-prior A 保留为 frozen negative control，不再靠阈值微调救它。
主研究改为同一个 first-seen panel 上的双头：`+30/+60/next-official` coherent ladder markout head，以及以 pre-event
market 为 prior 的 EOD settlement correction。`:20/:50` 和当地 12–15 点只作为预先列出的评测 strata，不作为新 hard gate。

## Probability 与执行分开看

| 模型/表达 | clean probability rows / dates | model vs market | clean selected intents | 5-share fee-adjusted replay |
|---|---:|---|---:|---:|
| V7 weather-only | 144 / 4 | 明显更差；ΔBrier CI 全正 | 3笔/3日，1胜2负 | cost `$4.21603`，PnL `+$0.78397`，ROI `+18.59%` |
| market-prior A | 143 / 4 | 小幅但一致更差；4/4日 delta>0 | 3 settled + 1 open，settled 0胜3负 | settled cost `$2.75687`，PnL `-$2.75687`，ROI `-100%` |

V7 的 clean selected ROI 为正，不推翻 proper-score fail：3 笔里唯一赢家是高成本 `21 NO @0.75`，其盈利覆盖了
`@0.03/@0.05` 两个小额输家；这只是资金加权后的 3-date 路径。反过来，market-prior 的 `-100%` 也只是 3 个
settled intents，不能外推长期错误率，但与四日 proper-score 一致地说明当前 correction 没有增量。若包含 archive-known
available 与污染前原始 lineage，V7 为 9笔3胜、ROI `-9.15%`；market-prior 为4笔1胜、ROI `-14.77%`，这些不作为
clean-forward 晋级证据。

## Next-routine head：天气预测有信号，但还不是交易策略

`amsterdam_knmi_next_routine_v5` 在 8/09–8/11 的 240 个 resolved initial observations 上：

- accuracy `95.00%`，always-NO `93.33%`；Brier `0.03201`，always-NO `0.06667`；
  logloss `0.11303`，AUC `0.9682`。
- confusion：TN 217、TP 11、FP 7、FN 5；16 个正例全部出现在 8/11，故只有 3 个独立日期，不能称稳定。
- `:20/:50` checkpoint 的 Brier 分别为 `0.02154/0.01246`，从 KNMI first-seen 到本地 collector 看到下一份
  routine METAR 的中位时间均约 7 分钟。这两个 slot 是下一阶段的 primary timing strata；仍需用同事件 WS 盘口证明
  market 在这 7 分钟内尚未完成重定价。

该 head 的 target 是“下一份 routine 是否确认 D1”，不是 EOD exact bracket；当前 same-target market probability
不存在，trade_signals=0、ROI=not available。它适合做 entry timing / held-position update，不能直接替代 settlement head。

## `.7 CrossNO`：有正向小样本，但不是短线退出 alpha

在 7/30–8/11 first-seen ladder 的固定规则下：

```text
23 first .7C cross signals / 8 dates
  -> 4 有 t0 NO ask<=0.97 且至少5-share depth / 4 dates
  -> 3 settled + 1 open
  -> settled 3/3 wins，cost $21.58787，PnL +$3.41213，ROI +15.81%
```

全部 20 个已结算 signal-level `.7 cross` 都最终离开 previous bracket；但真正可执行的 3 个 settled trades 太少。
而且 4 个 executable NO 在 t0 都已经是 market favorite（ask `0.65..0.96`），所以这组正 ROI 尚未证明天气规则
打败市场，只说明 `.7 cross` 能从高概率 favorite 中筛出一个值得继续 forward 的稀疏 cohort。

把这 4 笔在 `+15/+30/+60/+120/+300s` 直接 taker 卖回 bid，4/4 各 horizon 都亏；组合 round-trip ROI
依次为 `-9.87%/-7.88%/-5.14%/-5.80%/-2.76%`。因此当前证据支持“若做则持有至 settlement 的小样本
carry”，不支持“t0 taker 买入、下一报文前后 taker 退出”。maker 是否能回收 spread 仍缺真实 queue/fill 证据。

注意：generic CrossNo 当前 production policy 是 Amsterdam `live_trial`，不属于本报告的 zero-notional replay；本报告
明确排除其 actual order/fill/PnL，不能把今天的 live journal 混进上述 4 笔 counterfactual。

## 三门与研究状态

| 机制 | significance | baseline | forward | conclusion |
|---|---|---|---|---|
| V7 standalone fair-price | FAIL | FAIL | FAIL：仅4 clean settled dates | `inconclusive / rejected_current_expression` |
| market-prior A correction | FAIL：低于样本门槛且点估为负增量 | FAIL | FAIL：仅4 dates | `inconclusive / frozen negative control` |
| next-routine head | FAIL：仅3 dates、正例集中1日 | NA：无 same-target market | FAIL | `inconclusive / retain feature head` |
| `.7 CrossNO` hold | FAIL：3 settled executable dates | NA：4/4本就是market favorite | FAIL | `inconclusive / positive low-sample forward` |

8 环：覆盖 probability、同分母 market baseline、target-date bootstrap、displayed ask/depth、taker fee、短期 bid markout
和 settlement replay；缺 30-date frozen forward、same-event pre/t0 WS dynamics A/B、真实 maker queue/fill、足量 actual fill
与组合容量。没有一项满足 live 晋级三门，本轮不改 production 行为。

## 2026-08-04 历史快照

截至 2026-08-04，CrossNO 的 clean-forward shadow 为正，但只有 2 个可执行样本；V7 只有 1 个 target date，且唯一 selected residual trade 亏损。两者都不足以升 live，维持 zero-notional 并行 shadow。

| 策略 | 固定 grain | 覆盖 | 判断正确率 | 可执行/选中交易 | fee-adjusted cost | fee-adjusted PnL | ROI |
|---|---|---:|---:|---:|---:|---:|---:|
| CrossNO | 每个 `target_date × previous_bracket` 的首次 `.5` confirmation | 10 signals / 4 dates | 10/10 = 100% | 2/2 = 100% | $16.89424 | +$1.10576 | +6.55% |
| V7 | 10-minute checkpoint；交易绩效只算 selected candidate | 31 checkpoints / 1 date | 31/31 = 100% | 0/1 = 0% | $0.0020998 / share | -$0.0020998 / share | -100% |

这里的两种“正确率”不能互换：V7 在所有 checkpoint 都判断 `P(final cross) < 50%`，而 8 月 3 日最终确实没有跨过 31°C，所以方向判断是 31/31；但它估计的约 31% 仍远高于市场的 0.15%，因此选中了极便宜的 `31 NO` residual，这一笔最终输掉。

## 数据与固定口径

- 生产 identity：2026-08-04T01:50:52Z manifest；canonical DB 为 `/Volumes/jrs/pm_agents/runtime/weather.db`，DB route healthy。整体 warning 来自 checkout head drift，不是 DB split。
- CrossNO raw：`/Volumes/jrs/weather_data_feed_service_runtime/output/knmi_ta_prev_no_v1/events.jsonl`。
- V7 raw：`/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_runtime_v3/decision_bundles.jsonl`，模型 `amsterdam_knmi_remaining_heat_freeze_v7`。
- V7 bundle 的实际 `pit_provenance=archive_reconstruction`，information event 仍由 legacy city-probability score 迁移；因此本表是 forward-like shadow score，不是 clean live PIT confirmation。
- settlement：Polymarket Gamma closed market outcome；对应 winner 为 7/28 27°C、7/29 32°C、7/30 26°C、7/31 24°C、8/03 31°C。pm_history 已定向补抓 7/28–8/03，4 cached、3 fetched、0 errors。
- fee 使用当前官方曲线实现：`round(shares × 0.05 × price × (1-price), 5)`；ROI 分母为 fill cost 加 fee。
- CrossNO 的 `.5` 与 `.6` 在该窗口重复发出相同机会，本表只计 primary `.5`，不重复计算。
- CrossNO ask 可见 6/10；按原执行约束 `ask <= 0.97` 且 top ask size 可用，仅 2/10 可执行。
- V7 的 31 个 checkpoint 高度相关，不能当作 31 次独立交易；交易绩效只计唯一 selected candidate。

## Signal funnel 与 evidence funnel

### CrossNO

Signal funnel：10 个 first-confirm signals（4 target dates）→ 10 个方向正确。

Evidence funnel：10 signals → 6 有 NO ask → 2 满足原执行条件 → 2 settled → 2 wins。

两笔为：

- 2026-07-28，`26 NO`，8 shares @ 0.93：cost $7.46604，PnL +$0.53396，ROI +7.15%。
- 2026-07-30，`25 NO`，10 shares @ 0.94：cost $9.42820，PnL +$0.57180，ROI +6.06%。

### V7

Signal funnel：31 unique checkpoints（1 target date）→ 2 个同 checkpoint market-scorable → 1 个 selected candidate。

Evidence funnel：1 selected candidate → 1 settled → 0 wins。该候选为 2026-08-03 `31 NO`，`p_model=0.31276`、`p_market=0.0015`、每 share fee-adjusted cost $0.0020998；winner 为 31°C，因此亏损全部成本。

## 概率质量与 market baseline

- V7 全 31 checkpoint：Brier 0.11203，logloss 0.40711。
- 同分母可比的 2 个 checkpoint：V7 Brier 0.10284、logloss 0.38659；market Brier 0.00000225、logloss 0.00150。
- 这唯一 target date 上 market 明显优于 V7；样本不足以估计长期差异，但不支持当前模型已经打败 market。
- CrossNO 是 source-event threshold 策略，不产生连续概率，不能与 V7 做 proper-score A/B。

## 三门判断与动作

| 策略 | 统计显著性 | 同分母 baseline | frozen forward | 当前结论 |
|---|---|---|---|---|
| CrossNO | FAIL：仅 2 个 executable dates | N/A：无连续概率 | FAIL：窗口过短 | `inconclusive / positive small sample` |
| V7 | FAIL：仅 1 个 date | FAIL：该 date market 胜 | FAIL：窗口过短 | `inconclusive / selected trade lost` |

动作：两者继续 zero-notional shadow；CrossNO standalone 当前已停止，因此本表只覆盖 7/28–7/31 已记录的 clean-forward shadow，不能把它描述成仍在连续出新样本。V7 必须先把 legacy-migrated `archive_reconstruction` 升级为真实 first-seen PIT lineage，并改用 market-prior residual 后再评价。恢复/部署属于另一个生产动作，不能由这组小样本直接授权。
