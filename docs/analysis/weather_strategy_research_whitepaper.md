# Weather Strategy Research Whitepaper

Status: current-reference
Created: 2026-06-16

这份白皮书只回答一个问题：天气策略研究现在应该怎么拆分，哪些模块共享事实层，哪些方向适合派给不同模型并行推进。

## 总结

当前天气策略不要再按旧代号堆报告。最清楚的结构是两条主研究分支：

```text
pre_predict
  赛前/早盘预测：还没充分看到当天路径时，预测最终最高温分布。

reheat_risk
  日内路径预测：已经看到 running max 后，判断后面会不会再升温。
```

两条分支共享底座，但问题不同。`pre_predict` 给的是全天最终分布的 prior；`reheat_risk` 给的是观察到日内状态后的 conditional update。

## 分支一：pre_predict

核心问题：

```text
在没有完整日内路径信息时，最终最高温会落在哪一档？
```

主要输入：

- GFS/ECMWF/Open-Meteo forecast max。
- 历史 forecast error distribution。
- city/model/source reliability。
- market implied probability。
- forecast peak clock 作为辅助字段，而不是 observed-state 条件。

典型表达：

- 普通单腿 YES/NO。
- forecast-bounded Range RV。
- adjacent/range basket。
- 低价 YES prior sleeve。
- forecast quality / model-vs-market overlay。

当前项目入口：

- [pre_predict.md](pre_predict.md)
- [model_vs_market.md](model_vs_market.md)
- [forecast quality snapshots in WEATHER_DOCS_INDEX](../WEATHER_DOCS_INDEX.md)

## 分支二：reheat_risk

核心问题：

```text
今天已经到过当前最高温后，后面还会不会再升温、升多少、升到哪一档？
```

主要输入：

- METAR/current observation。
- running max / minutes since max / decline from max。
- forecast peak hour / forecast peak delta。
- dew point, RH, wind, sky cover, solar/local time。
- source-aligned settlement truth。
- current YES / d1 NO / d2 NO / target YES orderbook quotes。

典型表达：

- `current_yes_peak_forming`: 当前仍在高位时买 current YES。
- `current_yes_fade_confirmed`: 已经回落后更稳健地买 current YES。
- `higher_no_carry`: 买更高温档 NO。
- `low_price_yes_reheat_reversal`: 买需要二次升温才能命中的低价 YES。

当前项目入口：

- [reheat_risk.md](reheat_risk.md)
- [2026-06-16-current-yes-peak-vs-fade-v1.md](2026-06/2026-06-16-current-yes-peak-vs-fade-v1.md)
- [2026-06-16-higher-no-carry-expression-selector-v1.md](2026-06/2026-06-16-higher-no-carry-expression-selector-v1.md)
- [2026-06-16-theta-yes-current-live-gate-v9.md](2026-06/2026-06-16-theta-yes-current-live-gate-v9.md)
- [2026-06-16-theta-current-yes-forecast-peak-clock-v2.md](2026-06/2026-06-16-theta-current-yes-forecast-peak-clock-v2.md)

## 共享事实层

不要让每个策略各自 materialize 一套事实。共享层应该统一这些口径：

```text
city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket/outcome
```

必备字段：

- source: settlement source class, official station/feed, unit, timezone。
- observed path: current temp, running max, decline, minutes since max。
- forecast context: forecast max, forecast peak hour, forecast values hash。
- weather state: dew point/RH/wind/sky/temp trend。
- market: YES/NO best bid/ask, spread, depth, snapshot age。
- truth: official winning bracket, current bracket held, target bracket hit。

这层应该服务两个分支，而不是被某个策略私有化。

当前实现：

- `reheat_feature_factory_v1` 已落地，入口为
  [2026-06-16-reheat-feature-factory-v1.md](2026-06/2026-06-16-reheat-feature-factory-v1.md)。
- 这版支持 observed path、current YES、d1/d2 NO、target YES sibling quote 和
  source-grain settlement label 的共享消费。
- 仍缺 forecast peak context：`forecast_peak_hour_local` /
  `forecast_peak_delta_hours_local` / `forecast_values_hash` 在当前 DB 快照中
  为 0% 覆盖。后续 forecast-peak-clock 研究应先修 upstream fact population
  或显式 backfill，不能让各策略私自重建一套 forecast cache。

## 研究任务包

下面几个任务适合拆给不同模型并行做。每个任务都必须先写清楚 target metric 和 row grain，再开始跑数。

Reusable prompts for these tasks live in
[reheat_risk_delegation_prompts.md](reheat_risk_delegation_prompts.md).

### A. Reheat Feature Factory

目标：把 `reheat_risk` 共用特征层稳定物化出来。

交付物：

- 一个 city-hour/bracket grain 的 dataset builder。
- 字段包括 `forecast_peak_delta_hours_local`、`minutes_since_running_max`、dewpoint/RH/wind/sky、current YES/d1 NO sibling quotes。
- 覆盖率报告：按 city/date/hour 说明哪些字段缺失。

适合模型：擅长数据工程和口径审计的模型。

### B. Peak-Forming vs Fade-Confirmed

目标：比较“当前还在高温时买 YES”和“回落后买 YES”两个 timing head。

交付物：

- 两个 target metric：
  - `current_yes_peak_forming_ev`
  - `current_yes_fade_confirmed_ev`
- 同一 city-hour 状态下的价格、成交、ROI、prefix walk-forward。
- 结论必须回答：早买省下的价格是否大于二次升温风险。

适合模型：擅长统计检验、walk-forward、可读结论的模型。

### C. Higher NO Carry Expression Selector

目标：判断 higher NO carry 是否只是 current YES 的差表达，还是有稳定 payoff 优势。

交付物：

- current YES、d1 NO、d2 NO 同窗 paired rows。
- 分解 current-hit、d1-hit、skip-over 三种 settlement 状态。
- 输出 expression selector：什么时候 current YES 更好，什么时候 NO ladder 更好。

适合模型：擅长交易 payoff 拆解和基准设计的模型。

### D. Low-Price YES Reheat Reversal

目标：把低价 YES 从裸 `model_p_yes - price` 升级成 `forecast prior * reheat condition`。

交付物：

- `p_reheat_to_target` 或 `p_target_yes_wins` 模型头。
- 对比 raw model、blended p、reheat-adjusted p 三种 edge。
- 小仓 shadow 规则候选，但不能直接 live。

适合模型：擅长非线性分类、低胜率高凸性策略评估的模型。

### E. Execution Freshness Gate

目标：解决 current YES tiny-live 的核心执行问题：snapshot ask 到 CLOB fresh ask 之间是否已经重定价。

交付物：

- fresh-book guarded taker 规则回放。
- `p_win - fresh_ask`、snapshot age、spread/depth 的执行生存模型。
- 明确 maker/passive/taker/skip 的切换条件。

适合模型：擅长微结构、订单簿和执行质量的模型。

## 当前优先级

1. A 已完成 v1：后续策略头默认消费共享 reheat feature factory，不再各自
   materialize observed max / orderbook / settlement。
2. B 已完成 factory-backed v1：current YES timing 后续默认 fade-confirmed
   为主、peak-forming 只保留 narrow early shadow sleeve；不做 live change。
3. 下一步做 E：current YES 已经最接近 tiny-live，但卡在 execution freshness
   和 fresh ask slippage。
4. C 已完成 factory-backed v1：NO carry/ladder 没有证明能稳定打赢同窗
   current YES，只保留 shadow-only expression telemetry。
5. D 单独做凸性研究，不要和 no-reheat 策略混成一个 PnL。

## 命名规则

- 新研究统一使用 `reheat_risk`，旧 observed-max 代号只保留在历史文件路径里。
- 新模块使用 `reheat_risk`。
- 当前 YES 分成 `peak_forming` 和 `fade_confirmed`。
- NO 方向叫 `higher_no_carry`。
- 低价 YES 反转叫 `low_price_yes_reheat_reversal`。
