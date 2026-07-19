# 绩效分析：d1_yes_high_mid_live_v1 live forward

> 窗口：2026-07-16 — 2026-07-19 11:11 北京时间（7 月 19 日为未完成日）  
> 策略身份：instance `d1_yes_high_mid_live_v1` / canonical strategy `live_weather_edge_v1_df8eb1679a26` / run `pm_agent_local_strategy_runtime_d1_yes_high_mid_live_v1_live` / current execution `5 taker + 5 post-only maker`  
> evidence layer：canonical `runtime/weather.db` + current Mac raw runtime

## 结论与动作

live forward 的描述性结果很好：4 个独立 city-day 全胜，5 个真实 fills、25 shares，fee-adjusted realized PnL `+$3.1449`，canonical ROI `+14.45%`。但分母只有 3 个独立 target dates；target-date bootstrap 的表面 95% CI 为 `[+8.06%, +21.33%]`，因为三个 date block 全为正，这个 CI 在 3 clusters 下不具备 promotion 可信度。任何一个 5-share city-day 从赢变输，累计 PnL 会变成 `-$1.8552`、ROI `-8.53%`；Madrid 这类 10-share paired position 若从赢变输，则为 `-$6.8552`、ROI `-31.50%`。

maker/taker 只有 Madrid 一次真正的同信号 A/B：taker `5 @ 0.81`，maker `5 @ 0.80`，maker 等待 8 分 19 秒后成交，省下 `$0.05` 价格加 `$0.038475` taker fee，合计改善 `$0.088475`（`1.7695c/share`）。这是正向执行证据，但 `n=1`，不能推出 maker-only 或扩大 maker 比例。

动作：不 size-up、不把 `ask<=0.95` 或天气切片改成 live hard gate；继续把当前 `5 taker + 5 maker` 当 paired execution probe。优先修 maker 成交后的 lifecycle terminal reconciliation，并补 d1 opportunity → `fact_signal_candidates` 血缘；同时记录 observation first-seen latency 和 `ask>0.95` 的独立 shadow 标签。

```text
significance=FAIL_LOW_SAMPLE; baseline=NA_LIVE_PAIRED_BASELINE; forward=FAIL_TOO_SHORT; conclusion=inconclusive
```

## 数据快照

| 项目 | 值 |
|---|---|
| raw/canonical 覆盖截止 | raw runner `2026-07-19T03:11:47Z`；fill fact build `2026-07-19T03:11:20.883431Z` |
| DB `fact_built_at_utc` | `2026-07-19T03:11:20.883431Z` |
| opportunity / fill rows | raw trigger telemetry 82；首个 city-day signal 4；canonical live fills 5 |
| 独立 target dates | settled 3；7 月 19 日截至快照无首个 signal |
| unsettled / missing settlement | d1 `0 / 0`；5 fills 全部 settled |
| CLOB coverage gate | `gate_pass=true`；DB/cache 1078/1078；missing order、over-order、fill cost delta 均 0 |
| fee evidence classes | d1 taker 4 fills `exact`；maker 1 fill `exact/maker_zero` |

本次先用 `refresh_weather_analysis_incremental.sh --start-date 2026-07-18` 补 47 个交易城市的 settlement，再重物化 `fact_trades`；Madrid 从 `missing_bracket` 修复为 settled win。全账户 CLOB refresh 因会逐查 948 个历史 submitted orders而中止在只读状态核验阶段；随后使用已有权威 fill cache 重跑 coverage gate并通过。`fact_signal_candidates` 最新 decision snapshot 仍只到 `2026-07-18T16:58:20Z`，机会层约滞后 10 小时，作为 coverage gap 保留，不影响本文 fill PnL。

## Target metric 与固定分母

- unit/grain：绩效为每 fill；策略触发为首个 `(city,target_date)`；bootstrap block 为 `target_date`。
- PIT decision timestamp：raw `shadow_events.cycle_ts_utc`，entry book 为下单前 fresh CLOB revalidation。
- universe / denominator：2026-07-16 live 首单以后，runner 首个合格 city-day signal；Taipei 保持 shadow，但本窗口没有 Taipei trigger。
- label / settlement source：canonical `settlements` / `settlement_outcomes`，exact bracket 语义。
- price / executable cost / fee：真实 `fill_price`；PnL 直接取 `fact_trades.pnl_usd_at_fill`；Weather taker fee canonical evidence，maker fee 0。
- 主指标：settled fee-adjusted PnL / `SUM(fill_price*fill_qty)`。
- same-denominator baseline：live alpha 没有独立 paired no-signal baseline；maker/taker 只在 Madrid 同信号 paired。
- train / forward split：历史研究为 train/holdout；本文只看 7 月 16 日起用户授权 tiny-live forward，不调阈值。

## Signal funnel

| 层 | grain | rows | dates | 说明 |
|---|---|---:|---:|---|
| raw universe | runner cycle | 3,266 | 4 calendar days | 每 cycle 平均 24.3 城同时有 observation+book，13.1 城有 usable d1 quote |
| mechanism candidate | trigger telemetry | 78 | 3 settled target dates | 同一信号会按分钟重复记录；另有启动边界前 4 rows，raw 文件总计 82 |
| first city-day signal | city-day | 4 | 3 settled dates | Ankara、Warsaw、Wellington、Madrid |
| policy selected | city-day | 4 | 3 settled dates | 4/4 live；0 blockers；7 月 19 日截至快照为 0 |

后续覆盖复核（截至 `2026-07-19T03:59Z`）显示 3,266 个 live-window cycles 中有 493 个 cycle（15.1%）完全没有可用 book，集中为三个连续窗口：`2026-07-16T19:45Z..2026-07-17T00:10Z`、`2026-07-17T18:21Z..21:19Z`、`2026-07-18T17:48Z..18:36Z`，合计约 8.2 小时。数据可用 cycle 的中位 funnel 是 47 个 book cities → 28 个 observation/book 同日交集 → 15 个可构造相邻 bounded d1 且有双边报价的城市。78 个 trigger cycles 最终折叠为 4 个首个 city-day signal，且 4/4 均进入 live；因此没有证据表明 daily cap/depth/parity 在触发后压缩了 live 数，主要收缩来自 `mid>=0.80` 之前的语义/报价可用性和阈值本身。但由于 15.1% book blind cycles，4 个只能称 observed first signals，不能称完整自然机会总数。

## Evidence funnel

| 层 | grain | rows | dates | coverage gap |
|---|---|---:|---:|---|
| PIT observation | city-day | 4 | 3 | Wellington 存在约 26 分钟旧 observation 的 blind-window 风险；缺 first-seen latency 证据 |
| PIT quote | city-day | 4 | 3 | 4/4 下单前 fresh CLOB revalidated |
| settlement | fill | 5 | 3 | 5/5 settled，0 missing |
| executable expression | city-day | 4 | 3 | taker 4/4 matched；maker 1/1 matched |
| actual fill | fill | 5 | 3 | 25 shares；canonical/raw fill gate 完整 |

注意：d1 的 strategy-specific opportunity 尚未独立进入 `fact_signal_candidates`；该表也没有 strategy identity 列，不能用全局 eligible 分母冒充 d1 分母。本文的 signal funnel 因此来自当前 raw journal，PnL 仍严格来自 canonical `fact_trades`。

## Probability / ranking quality

该策略不输出独立 `p_win`，只使用 market calibration cell `d1_yes_mid>=0.80`，因此本 live 窗口没有可与 market 同 rows 比较的 logloss/Brier candidate。历史同规则诚实分母 218 rows 的 fee ROI 为 `+2.24%`、CI `[-0.64%,+5.19%]`；forward 15 dates 为 `+6.62%`，但不是本次 4 个 live city-day 的 paired baseline。

## Fee-adjusted trade performance

| target date / slice | city / fills | shares | cash cost | fees | PnL | ROI |
|---|---|---:|---:|---:|---:|---:|
| 2026-07-16 | Ankara `5@0.84`、Warsaw `5@0.972` | 10 | `$9.06` | `$0.0404` | `+$0.8996` | `+9.93%` |
| 2026-07-17 | Wellington `5@0.93` | 5 | `$4.65` | `$0.016275` | `+$0.333725` | `+7.18%` |
| 2026-07-18 | Madrid taker `5@0.81` + maker `5@0.80` | 10 | `$8.05` | `$0.038475` | `+$1.911525` | `+23.75%` |
| 2026-07-19（未完成） | 无首个 signal | 0 | `$0` | `$0` | `$0` | NA |
| **合计** | **4 city-days / 5 fills** | **25** | **`$21.76`** | **`$0.095155`** | **`+$3.14485`** | **`+14.45%`** |

全部为 BUY_YES；by-count win rate `5/5=100%`，by-notional win rate `100%`。加 fee 后的 weighted break-even win probability 为 `87.42%`。

## Maker 与 taker

| style | fills | shares | cost | fees | PnL | ROI | 解释 |
|---|---:|---:|---:|---:|---:|---:|---|
| taker | 4 | 20 | `$17.76` | `$0.095155` | `+$2.14485` | `+12.08%` | 保证 capture；包含旧版 3 个单腿和 Madrid paired taker |
| maker | 1 | 5 | `$4.00` | `$0` | `+$1.00` | `+25.00%` | Madrid 唯一样本；8m19s 后 fill，不能外推 100% fill rate |

Madrid 同信号 paired delta（maker minus taker）为 `+$0.088475/5 shares`。其中 `$0.05` 来自低 1c 的 fill price，`$0.038475` 来自 maker 不收 taker fee。旧版三笔 taker 的 mid-relative spread+fee drag 已在 Wellington audit 算为 `$0.436679`、占 notional `3.185%`；Madrid 的 fresh execution 已明显更好，但样本仍不足。

## Execution / selection quality

| 指标 | 值 |
|---|---:|
| first signal -> live city-day | `4/4 = 100%` |
| live city-day -> taker fill | `4/4 = 100%` |
| submitted maker -> maker fill | `1/1 = 100%`，等待 8m19s |
| blocked/error/size anomaly | entry 0；canonical 5 fills、25 shares，与 order cap 一致 |
| maker terminal reconciliation | Madrid maker 已在 13:39Z matched，但 14:00Z 新 observation 后 runner 仍连续写 39 个 cancel-only blocked attempts；无重复资金订单，但属于 lifecycle state bug |
| risk config parity | 当前进程与 summary 为 `10 city-days / $100`，strategy living report 仍写 `10 / $50`；本窗口实际最多 2 city-days/日，未触发影响 |
| opportunity selection bias | strategy-specific canonical opportunity denominator 缺失，不能计算 missed winners / avoided losers |
| runner book coverage | 493/3,266 cycles（15.1%）无可用 book，三段连续 blind window 共约 8.2h；可能漏掉短暂 threshold cross |

maker cancel 重试的根因不是交易所重复成交，而是原始 maker place response 仍为 `live`；后续 `order_after_cancel.status=MATCHED` 已证明终态，但 `maker_lifecycle_plans()` 只检查初始 place status 和成功 handled action，没有把该 authenticated matched state当 terminal。结果没有重复 fill，但制造了 39 条无效 cancel attempts，并可能掩盖真正的 open-order 状态。

## Forward 与稳健性

| 检查 | 结果 |
|---|---|
| frozen forward same sign | PASS_DESCRIPTIVE：3/3 target dates 为正，但太短 |
| target-date block bootstrap | 表面 95% CI `[+8.06%,+21.33%]`；只有 3 blocks，不能过 significance 门 |
| leave-one-date-out | 去掉 7/16 `+17.68%`；去掉 7/17 `+16.43%`；去掉 7/18 `+9.00%` |
| one-loss sensitivity | 任一 5-share city-day 从赢变输：累计 ROI `-8.53%`；Madrid 10-share 变输：`-31.50%` |
| high-price sensitivity | Warsaw `0.972` 一次赢只赚 `$0.133195`；需要约 36.5 次同等 win 才覆盖一次同价 loss |
| multiple testing | live 未新增调参；历史 `ask<=0.95` / remaining-heat 是预注册 shadow challenger，不用于回改本窗口 |

## 优化空间

1. **先修 execution state，不先改 alpha 阈值。** maker matched 后应立即 terminal reconcile，停止 cancel spam；同时把 matched time、wait time、price improvement、adverse selection 写成 canonical execution telemetry。
2. **继续 paired `5 taker + 5 maker`，不改 maker-only。** 当前唯一 paired 样本改善 1.7695c/share，但 maker 是否在坏单上更容易成交、好单上更容易 miss 还完全未知。
3. **高价腿只做 shadow 分层。** `ask>0.95` 的 payoff 太薄，Warsaw 证明它可以赢，但没有证明长期 EV；先累计同分母 live/shadow 数据，再决定 cap 或缩 taker。
4. **补 observation first-seen。** Wellington 的旧 observation 没造成亏损，但 exact-bracket 身份会随新报文改变；需要区分“报文尚未被源发布”和“系统已可见但 ingest 落后”，不能只用 `obs_age<120m`。
5. **补 canonical opportunity lineage。** 将 d1 首次 trigger、eligible、Taipei shadow、execution blocker 和 live selection 挂入 `fact_signal_candidates` 或策略可识别的 canonical opportunity layer，才能量化漏信号、maker miss 与全机会基准。
6. **对齐 risk 配置文档。** 当前部署上限是每天 10 city-days / `$100`，living report 仍为 `$50`；应统一成实际授权值，避免后续巡检按错误边界判断。
7. **暂不放大仓位。** 当前累计利润小于任何一个 5-share exact-bracket loss 的 `$5` payout swing；至少等到预注册的 10 个 fresh-forward dates，且最好达到 contract 的 30 settled fills 后再评 size。

## 三门与残余风险

| 门 | PASS/FAIL/NA | 证据 |
|---|---|---|
| significance | FAIL_LOW_SAMPLE | 3 dates / 5 fills；数值 CI 虽为正，但 cluster 数不足 |
| same-denominator baseline | NA | live alpha paired baseline 缺失；maker/taker paired 仅 n=1 |
| forward | FAIL_TOO_SHORT | 当前 3 settled dates，预注册至少 10 fresh-forward dates |

8 环覆盖：描述性绩效、fill fee、短样本 block sensitivity、单次 maker/taker A/B 已覆盖；概率 proper score、全机会 selection、容量、组合相关性和 live same-denominator baseline 未覆盖。结论不能升级为 confirmed，也不能据此 size-up。

最终：在 `2026-07-16..18` 的 4 个 live city-day / 5 fills 分母上，`d1_yes_high_mid_live_v1` fee-adjusted ROI 为 `+14.45%`（3-date block bootstrap 表面 95% CI `[+8.06%,+21.33%]`，但低样本失效），forward `FAIL_TOO_SHORT`，结论 `inconclusive`，动作是维持 tiny paired evidence、先修 execution/canonical lineage、不放大仓位。
