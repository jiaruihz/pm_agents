# Fast-source cross shadow live readiness v1

## 数据快照

- 目标：在当前各城市生产 policy 的 first `city-day + previous bracket` 信号上，比较 settlement 正确率与 `ask<=0.97`、当前 depth-adaptive taker（目标 15 shares，最少 5）反事实收益，判断是否存在可升 live 的 shadow 城市。
- grain：signal 为 `city + target_date + previous market bracket`；执行为 first-priced direct-book expression；不是实际 fill。
- source/event：`/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/events.jsonl`，mtime `2026-07-22 19:22 CST`。
- direct book：`/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_stale_book/quote_snapshots.jsonl`，mtime `2026-07-22 23:35 CST`。
- settlement：`runtime/weather.db/settlement_outcomes`，settled 覆盖到 `2026-07-21`；DB mtime `2026-07-22 23:33 CST`。
- 当前 shadow policy 信号 `61` 个，已结算 `58`，未结算 `3/61=4.9%`，本 replay `missing_bracket=0`。
- `weather_clob_fill_coverage_gate.py`: `gate_pass=true`，DB/cache fill id 双向差异均为 0。本报告的 shadow PnL 仍是 PIT book replay，不冒充 actual fill。

## 结论

**目前没有一个 shadow 城市够资格升 live，Seoul 明确不应作为下一座 live。**

Seoul 的 weather-direction 表面正确率很高：当前 policy 已结算 `17/18=94.4%`；但真正可执行的 current depth-adaptive 子集只有 `4` 个，只有 `3/4` 正确，55 shares 扣 taker fee 后 PnL `-$6.9879`、ROI `-14.87%`，按 target-date block bootstrap 的 ROI 95% CI 为 `[-65.49%, +8.35%]`。错误 cross 在 `0.64 x 163.3` 的深盘口上可大量成交，正确信号多数已被定价到 `0.916-0.94`，是典型 adverse execution selection。

在剩余城市里，San Francisco 出现了唯一一个便宜且可执行的 winner（`8 @0.28`，反事实 `+$5.6794`），但只有一笔；Atlanta 的同源 negative control 则把唯一可执行事件打成 `-$13.1348`。所以 San Francisco 只值得继续收集，不能升 live。

## 当前 shadow 城市同口径结果

| 城市 | 当前规则 | 信号/日期 | settled 正确 | priced / ask<=.97 | adaptive 胜/笔/股 | PnL | ROI | 判断 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Ankara | arithmetic +0.5 | 4 / 2 | 4/4 | 1 / 0 | 0/0/0 | $0 | NA | 无可执行证据 |
| Atlanta | persistent +0.5/+0.7 | 12 / 7 | 11/12 | 5 / 2 | 0/1/15 | -$13.1348 | -100.0% | terminal false negative control |
| Istanbul | arithmetic +0.5 | 4 / 2 | 3/4 | 4 / 1 | 0/1/5.47 | -$1.5867 | -100.0% | 错误事件可执行 |
| Miami | persistent +0.5/+0.7 | 7 / 6 | 7/7 | 2 / 0 | 0/0/0 | $0 | NA | 市场已定价，无执行分母 |
| San Francisco | persistent +0.5/+0.7 | 11 / 6 | 11/11 | 8 / 1 | 1/1/8 | +$5.6794 | +244.7% | 仅1笔，不能外推 |
| Seoul | persistent +0.5/+0.7 | 21 / 6 | 17/18 | 12 / 5 | 3/4/55 | -$6.9879 | -14.9% | **不升 live** |
| Tel Aviv | arithmetic +0.5 | 2 / 2 | 2/2 | 0 / 0 | 0/0/0 | $0 | NA | 无盘口证据 |

以上 `adaptive` 按当前执行语义：top ask `<=0.97` 且可见量至少 5 shares，吃 `min(可见量,15)`；fee 为 Weather taker `shares*0.05*price*(1-price)`。maker 不计入 live-readiness 主结果。

## Seoul 深挖

### Source basis

- 2026-07-08..21 共 `14` 个 observation dates，AMOS first-seen lag p50/p90 为 `0.448/0.626 min`，速度没有问题。
- 覆盖完整 peak 的 `13` 个 city-days 中，AMOS rounded daily max 只有 `9/13` 落在最终 WU winning bracket，出现 `4` 个 terminal false-cross days。
- persistent previous-NO 事件为 `35` 个，settlement 方向正确 `33/35`；但两个错误事件都在 first read 可执行，正确事件只有 `9/33` first read 可执行。
- 因此 `94%` 方向正确率不能直接当交易胜率；错误更便宜、更深，反而更容易被策略买到。

### 可执行四笔

| target date | previous NO | winner | ask x size | shares | PnL |
|---|---:|---:|---:|---:|---:|
| 2026-07-16 | 28 | 28 | 0.64 x 163.30 | 15 | -$9.7728 |
| 2026-07-17 | 29 | 31 | 0.94 x 95.77 | 15 | +$0.8577 |
| 2026-07-18 | 25 | 26 | 0.916 x 22.94 | 15 | +$1.2023 |
| 2026-07-20 | 24 | 27 | 0.924 x 10.00 | 10 | +$0.7249 |

这四笔合计成本 `$46.9879`，一个错误事件吃掉三笔 winner 的全部利润后仍倒亏 `$6.9879`。maker 220 秒 queue replay 同样为负（`-$4.93`），不能靠 maker 修复。

### 今天 7/22

- Seoul `26 NO` 在北京时间 `13:47` 左右首读约 `0.946 x 3.51`，低于当前 minimum taker 5 shares，因此不会下单。
- 当 source 到 `26.8C`、信号更强时，盘口已到 `0.997 x 240.48`；随后基本 `0.999`。
- 所以今天不是 runner 慢导致漏单：新链路 source→runner 已是秒级，真正限制是首个便宜盘口深度不足，随后市场立即完成定价。

### Seoul METAR clock 与严格 T-20 反事实（2026-07-23 补充）

- RKSI routine METAR 基本为半小时一份，常规 report timestamp 在每小时 `:00/:30`。当前样本的 distinct reports 中，minute 为 `:00` 221 份、`:30` 225 份；相邻可观察 report gap 为 30 分钟的有 422 段，少量 60 分钟以上 gap 属缺报/采集覆盖，不应把 Seoul 配成 hourly。
- 当前生产并非严格 pre-report：`next_metar_window_status()` 使用 `abs(minutes_to_next)<=20`，所以 scheduled report timestamp 已过去 0--20 分钟仍 eligible。7/22 的 `06:10Z` Seoul event 就被记为相对 `06:00Z` 的 `-10.363m` 且 eligible。
- 改成严格 `0<=minutes_to_next<=20` 后，Seoul 当前 policy first signals 从 `21` 降到 `19`，settled 从 `18` 降到 `16`，正确从 `17/18` 变为 `15/16`；两条被删的是正确但不可执行信号。
- 更重要的是，adaptive 可执行集合完全不变：仍为 `4` 笔、`3` 胜、55 shares、PnL `-$6.9879`、ROI `-14.87%`、95% CI `[-65.49%,+8.35%]`。7/16 的错误 `28 NO @0.64` 发生在 T-15.472，仍会被严格 T-20 放进来。
- 所以 strict pre-20 是正确的 clock/label 语义修复，但不是 Seoul alpha 修复。因为 METAR 每30分钟一次，20分钟窗口覆盖每个周期的三分之二，本身并不窄。
- exploratory 的 T-10 会同时剔除 T-15.472 的错误单和两笔 T-19.x winner；当前 first-signal replay 只剩 1 个 adaptive settled fill（winner），样本不足且属于本轮看结果后切窗，不能据此上线。report clock 应保留为连续特征，主确认仍应依赖 source path retention / terminal-basis probability。

## 双漏斗

```text
signal funnel（current-policy first signal）:
61 signals / 7 shadow cities -> 58 settled -> 55 weather-direction correct

evidence funnel:
61 signals -> 32 first-priced -> 9 ask<=0.97 -> 7 adaptive executable
-> 4 winners / 7 -> 0 actual fills（全部为 shadow）
```

盘口缺失和深度不足是 evidence gap，不算策略筛除。`55/58` 不能替代 `4/7` executable outcome。

## 下一步动作

1. Seoul 保持 shadow；不 tiny-live、不加 size。下一版应输出校准后的 `P(final WU leaves bracket)`，并把 AMOS terminal overshoot、remaining heat/path retention 和 market ask 作为连续特征，而不是继续堆一个更高 hard threshold。
2. San Francisco 保持 zero-notional shadow，等至少 10 active days、30 settled executable expressions，并必须继续用 Atlanta terminal false case 作 negative control。
3. Ankara/Tel Aviv/Miami 继续 collector；当前没有可执行分母，不能按 100% weather-direction 正确率升 live。
4. 当前不改生产 city pool。

三门：`significance=FAIL`（Seoul CI 跨0，其余 executable n<=1）；`baseline=FAIL`（无城市证明 fee-adjusted residual 稳定胜过同刻 ask）；`forward=FAIL`；`conclusion=inconclusive / keep-shadow`。

## 产物

- `generated/cross_no_shadow_city_replay_v1/current_policy_city_replay.csv`
- `generated/cross_no_shadow_city_replay_v1/current_policy_city_summary.csv`
- `generated/cross_no_shadow_city_replay_v1/summary.json`
- `generated/active_realtime_source_alignment_v1/summary_by_city_source.csv`
- `generated/high_frequency_strategy_eligibility_v2_20260722/summary_by_city_source.csv`
