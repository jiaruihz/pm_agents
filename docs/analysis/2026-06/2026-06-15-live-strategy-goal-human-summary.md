# Live Strategy Goal - Human Summary

> Updated: 2026-06-15 Beijing time
> Purpose: short human-readable summary of the current live-strategy research goal.
> Scope: no live config change, no N100 deployment, no orders placed.

## 一句话结论

这轮目标已经不是空转：我们排除了几条看起来赚钱但不适合散户执行的路线，并把下一步收敛到一个更现实的方向：

```text
forecast_bounded_range_rv_shadow_v0
```

也就是：围绕天气预报最可能的温度区间，做小型 Range RV 组合，先 shadow/paper，不直接 live。

## 现在最重要的判断

`all_yes_underround_basket_v0` 的数学边是真的，但不适合作为当前散户 live 主线。

原因很简单：

- 机会少，出现时间短。
- 边很薄，常见毛利只有 2%-3%。
- 必须所有 YES 腿同时成交，否则会变成裸风险。
- 散户很难拼速度、盘口深度和部分成交处理。

所以它现在的定位是：

```text
offline confirmed
but retail-live blocked
```

不是废掉，而是放回市场结构研究和工程 sandbox，不作为当前 tiny-live 候选。

## 我们已经试过什么

### 1. BUY_NO 小仓

做过 ECMWF + BUY_NO + 价格/edge 筛选 + city-day top1。

结果：有方向感，但样本太薄。`forecast_quality_low=0` 这种硬过滤过度收缩样本；`city_model_reliable` 更合理，但 forward 还不够。

结论：只适合 shadow，不适合 live。

### 2. Forecast Quality

它不是一个策略，而是一个辅助标签层。

有用的地方：

- 判断某个城市/模型组合近期是否可靠。
- 给 Range RV、BUY_NO、side-band 做风险标记。
- 帮我们避免把低质量预报当成硬 edge。

结论：作为辅助特征保留，不单独上线。

### 3. Range RV / Adjacent / Side-band

之前试过很多表达：adjacent range、market-shape、tail fade、center/shoulder/butterfly、dual expression 等。

大多数问题都是同一个：历史点估计可以，但 holdout、top-date stress 或 orderbook 可执行性不过。

结论：不要继续 broad search；下一步只做收敛后的 forecast-bounded Range RV。

### 4. All-YES Underround

历史回放最漂亮，但散户执行最难。

结论：研究价值高，live 价值暂时低。

## 下一步真正该做什么

做一个小而具体的 shadow 策略：

```text
forecast_bounded_range_rv_shadow_v0
```

规则方向：

- 只看预测分布附近的温度区间。
- 组合腿数控制在 2-4 条。
- 可以用 inside YES，也可以用 outside NO，选更便宜的表达。
- 每条腿都必须有同一时点之前的 orderbook ask。
- 按 city-day basket 统计，不把每条腿当独立样本。
- 先 zero-notional shadow，再 paper，再考虑 tiny-live。

## 到 live 还差什么

最低还差这些证据：

| 门槛 | 要求 |
|---|---|
| forward 样本 | 至少 20 个已结算 basket |
| 活跃日期 | 至少 7 个 event dates |
| 统计 | event-date bootstrap 后超额 ROI 不跨 0 |
| 稳健性 | 去掉最大 5 个贡献后仍为正 |
| 可执行性 | 每条腿都有足够盘口、点差和深度 |
| 散户友好 | 腿数少，毛边能覆盖 tick + slippage |

没过这些之前，不应该 tiny-live。

## 当前状态

```text
live_action = none
best_current_direction = forecast-bounded Range RV shadow
all_yes_status = research only / retail-live blocked
buy_no_status = shadow only
forecast_quality_status = feature layer
```

## 数据自检

本轮整理使用 `runtime/weather.db`，事实表 build 到：

```text
2026-06-14T16:08:13Z
```

5 行 SQL 自检：

```text
fact_trades: live_real 855, live_simulated 624, paper 2285, snapshot_replay 636
settlement: settled 4250, blank 150
fact_signal_candidates: rows 29313, eligible 10031, paper_ordered 3824, live_filled 348
CLOB orders: submitted 961 / with_fill 855, error 33 / with_fill 0
CLOB coverage gate: pass
```

这份报告不发布 live_real PnL / ROI / rank / curve。
