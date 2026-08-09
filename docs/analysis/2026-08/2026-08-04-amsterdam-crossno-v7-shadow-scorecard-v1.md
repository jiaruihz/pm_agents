# Amsterdam CrossNO / V7 shadow scorecard v1

## 结论

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
