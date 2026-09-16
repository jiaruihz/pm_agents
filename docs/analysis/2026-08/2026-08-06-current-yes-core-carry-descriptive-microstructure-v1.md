# Core Carry 触发、价格、天气模式与微观盘口审计 v1

Status: `descriptive / probability baseline mixed / latest canonical blocked / no-live-change`

## 动作

保持 fixed 10 taker；不启用 continuous net-EV sizing。maker 的 queue-preserving + max 2 reprices 是执行层实验，不改变信号、模型概率或 taker sizing。

## 触发频率

- 冻结 PIT 评测：136 signals / 30 target dates，平均 4.53/日，中位 4.0，P90 7.2，最高 10。
- 全部 raw live：40 signals / 13 target dates，平均 3.08/日。
- 当前 10+5 窗口（2026-08-03 起）：9 signals / 4 target dates，平均 2.25/日；逐日 2026-08-03 2、2026-08-04 2、2026-08-05 4、2026-08-06 1。

## 入场价格与点差

| scope | avg mid | avg ask | avg actual/effective cost | avg spread | median spread | 1c spread rate | avg model p | p-mid | p-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen 136 | 0.8926 | 0.9093 | 0.9133 | 0.0333 | 0.0200 | 28.7% | 0.9358 | +0.0431 | +0.0225 |
| live current | 0.9311 | 0.9389 | 0.9396 | 0.0156 | 0.0100 | 55.6% | 0.9572 | +0.0261 | +0.0176 |

当前窗 9 个 maker intents、4 个已确认 maker fills。点差小不等于 maker 易成交：高概率 carry 的 bid queue 较厚，且 maker cap 经常先于 ask 限制追价。

## 城市分布

- 冻结评测覆盖 29 城；Top 10：Istanbul 15、Wellington 11、Karachi 10、SanFrancisco 10、Amsterdam 9、Tokyo 8、LA 7、Manila 7、Houston 6、Shanghai 5。最大单城 Istanbul 占 11.0%，整体城市集中度不高，但 Istanbul/Karachi/LA 等城市准确率差异仍因样本不足不能转成 city gate。
- 当前 10+5：Manila 2、Amsterdam 1、CapeTown 1、Miami 1、NYC 1、SanFrancisco 1、Shanghai 1、Wellington 1。只有 4 个日期，城市切片全部 low-sample，不能做城市 keep/cut。

## 天气模式与模型表达

当前 10+5 的 raw PIT regime：

- temperature context：forecast_peak_passed_2h_plus | cooling | clear_but_not_warming | mixed_moisture_cloud | onshore_marine_cooling_risk 2、forecast_peak_passed_0_to_1h | cooling | cloud_limited_flat_or_cooling | cloud_suppression | onshore_marine_cooling_risk 1、forecast_peak_passed_1_to_2h | flat | cloud_limited_flat_or_cooling | cloud_suppression | offshore_or_parallel_warming_risk 1、forecast_peak_passed_1_to_2h | cooling | cloud_limited_flat_or_cooling | humid_cloud_suppression | onshore_marine_cooling_risk 1、forecast_peak_passed_0_to_1h | cooling | cloud_limited_flat_or_cooling | humid_cloud_suppression | onshore_marine_cooling_risk 1、forecast_peak_passed_2h_plus | flat | cloud_limited_flat_or_cooling | humid_cloud_suppression | onshore_marine_cooling_risk 1、forecast_peak_0_to_2h_ahead | warming | warming_through_cloud | cloud_suppression | offshore_or_parallel_warming_risk 1、forecast_peak_passed_1_to_2h | flat | mixed_sky_flat_or_cooling | mixed_moisture_cloud | onshore_marine_cooling_risk 1
- intraday state：mature_fade 5、plateau_near_high 2、pullback_uncertain 1、active_warming 1
- heating done：heating_done_confirmed 7、heating_done_probable 1、runway_still_open 1
- wind：windy_mixing_noise 5、moderate_wind 4
- moisture/cloud：cloud_suppression 3、humid_overcast_suppression 3、mixed_moisture 3
- precipitation：none_observed 5、thunderstorm 2、rain_or_drizzle 2

冻结模型直接使用的只有 `market_logit + local hour + forecast peak delta + dewpoint depression + wind speed`。rain/cloud、gust、dewpoint trend、warm advection 和完整 path regime 虽已进入 telemetry，但不直接进入 frozen probability，因此模型对这些语义仍是部分表达，不是完整物理模型。

## 模型相对市场

- selected 136：平均 model p 93.58%，market mid 89.26%，差 +4.31%；扣 5-share ladder+fee 后平均净 edge +2.25%。这是 selector 后均值，不能单独证明 alpha。
- 同分母 1350 个 0.80+ OOF PIT states：core−market Brier Δ -0.001030，date-block 95% CI [-0.002859, +0.001089]；logloss Δ -0.006090，CI [-0.014310, +0.002158]。负值才代表模型优于市场。
- full core−no-clock：Brier Δ -0.000489，CI [-0.001496, +0.000656]；full core−no-wind：-0.000048，CI [-0.001038, +0.000910]。这回答 peak-clock/wind 是否提供稳定增量。

## 证据边界

- signal funnel：OOF 0.80+ PIT state → positive full-ladder net EV → first city-day signal。
- evidence funnel：frozen rows 有 PIT quote/settlement；latest live 使用 Mac raw bid/ask/matched taker evidence。
- 当前 production manifest 仍 critical，latest canonical settlement/PnL 不在本报告更新；旧的 8/03 cutoff 后 PnL 结论不外推到 8/06 新信号。
- `significance` 见同分母 CI；`baseline` 为同 row market mid；`forward=low-sample`；`conclusion=inconclusive / no-live-change`。
