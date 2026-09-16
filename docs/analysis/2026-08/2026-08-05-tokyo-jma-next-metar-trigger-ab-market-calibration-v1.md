# Tokyo JMA next-METAR 同时点 A/B 与 market calibration

## 结论

`next routine METAR confirmation` 可以作为 Tokyo 最终结算模型的一个连续特征，但当前证据不支持把它直接做成 live hard gate，更不支持固定等待到 T-3。

- 在同一 `.7 cross`、同一 fresh ask、同一 top depth、同一结算标签上，直接 taker 为 10 笔、8 胜2负、fee-adjusted PnL `+$3.7607`、ROI `+3.61%`。
- `P(next METAR confirms) >= 0.5` 后为9笔、8胜1负、PnL `+$5.0031`、ROI `+4.86%`；只拦掉 7/29 `34 NO @0.119 ×10` 这一笔错误，PnL delta `+$1.2424`、ROI delta `+1.25pp`，date bootstrap 下界为0，未形成显著证据。
- `>=0.7/0.8` 分别漏掉3/4笔正确单，ROI退化到 `-4.56%/-7.05%`。confirmation head 的 label 不是最终 settlement label，不能把高阈值解释为更高最终胜率。
- broad market probability 不是“绝对准确”：103 日 midpoint-like proxy 中，`45–55%` 的 date-equal 实现率为 `59.5%`，但 gap CI 跨0；`75–85%` 的实现率为 `90.7%`，gap CI `[+3.6pp,+16.0pp]`。这里 market 在该 Tokyo current-exact checkpoint slice 上偏保守，而非完美校准。
- midpoint 校准不等于 taker 盈利。若 ask 恰为 `0.80`，Weather fee 后保本概率是 `0.808`；ask=`0.50` 时是 `0.5125`。若看到的是 midpoint，还要再加 half-spread、深度 VWAP 与延迟磨损。

动作：保持 zero-notional；把 scheduled-next-METAR confirmation 作为 Tokyo final-settlement / market-offset head 的输入，在触发同一时点立即算，不固定等待、不把 `0.7/0.8` 做 live gate。

## 固定口径

目标：在真实 `.7 cross` 触发的同一 execution clock 上，比较“直接买 previous exact NO”与“先经过 confirmation model 再买”的交易差异，并独立评估 market probability 对最终 exact settlement 的校准和 taker 磨损。

- signal grain：每个 `target_date × previous bracket` 的首个实际 JMA margin `>=0.7°C` event；
- phase：JMA observation `:10/:40` 为 nominal T-13，`:20/:50` 为 nominal T-3；`:00/:30` race cohort 不进入主分母；
- model：冻结的 `jma_metar_hgb_v1`，target 为下一份 routine METAR 是否确认 JMA lattice；
- execution：runner event 内 fresh top ask，`min(15, top ask size)`，至少5 shares，ask `<=0.97`；不假定未观测的 deeper VWAP；
- fee：`shares × 0.05 × ask × (1-ask)`；
- settlement：逐日 Tokyo pm_history，near-binary 归一化；
- trade class：research counterfactual / zero-notional，actual order/fill=`0/0`；
- window：trigger A/B `2026-07-09..30`；broad market proxy `2026-04-01..07-15`。

## 双漏斗

```text
signal funnel
162 Tokyo raw cross events
  -> actual source margin >=0.7: window-specific candidates
  -> scheduled T-13/T-3
  -> 44 first target-date×bracket events / 13 dates

evidence funnel
44 settled + confirmation scores
  -> 10 fresh taker-executable events / 10 dates
  -> 9 two-sided fresh mid events
  -> actual order/fill = 0/0

broad market calibration
6,658 midpoint-like PIT proxy checkpoints / 103 dates
  -> no historical ask/spread/depth/VWAP
```

盘口或两侧 quote 缺失只算 evidence gap，不算策略筛除。ask-only 行可用于执行回放，但不冒充 midpoint calibration。

## 同时点交易 A/B

| policy | trades | W-L | shares | avg ask | cost | fee PnL | ROI | ROI 95% date CI | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct taker | 10 | 8-2 | 128.04 | 0.8092 | $104.2793 | +$3.7607 | +3.61% | [-18.52%,+20.17%] | -$9.0310 |
| confirmation >=0.5 | 9 | 8-1 | 118.04 | 0.8677 | $103.0369 | +$5.0031 | +4.86% | [-17.71%,+21.59%] | -$7.7886 |

paired delta：PnL `+$1.2424`，bootstrap CI `[0,+$3.7273]`；ROI `+1.25pp`，CI `[0,+5.06pp]`。改善全部来自一笔被拒绝的低价错误单，不能解释成稳定 alpha。

| confirmation threshold | trades | correct missed | wrong avoided | fee PnL | ROI |
|---:|---:|---:|---:|---:|---:|
| 0.5 | 9 | 0 | 1 | +$5.0031 | +4.86% |
| 0.6 | 9 | 0 | 1 | +$5.0031 | +4.86% |
| 0.7 | 6 | 3 | 1 | -$3.1567 | -4.56% |
| 0.8 | 5 | 4 | 1 | -$3.8710 | -7.05% |

模型拦掉了 7/29 `34 NO`：`p_confirm=0.461`、next METAR 未确认、最终该 NO 输。但 7/26 `32 NO @0.77 ×10` 同样最终输，模型却给 `p_confirm=0.820` 并放行。它能识别部分即时 false cross，不能覆盖 final exact outcome。

10 笔基准交易中只有5笔可在 top ask 直接拿满15 shares，另外5笔按真实 top depth 部分成交；因此 headline 使用128.04 shares，不使用虚构的150 shares。

## T-13 与 T-3

| phase | events / dates | next-METAR confirm rate | model Brier | accuracy@0.5 | final NO win rate |
|---|---:|---:|---:|---:|---:|
| T-13 | 20 / 13 | 70.0% | 0.2135 | 70.0% | 95.0% |
| T-3 | 24 / 13 | 79.2% | 0.1329 | 83.3% | 95.8% |

T-3 对即时 confirmation 更容易预测，但这不自动支持等待。相同 scheduled report 的9个 T-13/T-3 quote pair 中，NO ask 变化均值 `+1.82c`、中位 `0c`、范围 `-1.7c..+16c`；只有1对两端都满足当前执行条件，另有1对等待后获得可执行性、1对等待后失去可执行性。样本不足以量化统一 waiting rule，且固定等待会引入价格与容量选择偏差。

## Market calibration 与 taker 磨损

价格历史宽分母使用 current-exact YES midpoint-like proxy，label 是最终是否停在 current exact bracket。它适合 probability calibration，不是 orderbook 或 executable price。

| market band | rows / dates | mean market P | date-equal realized | gap | date bootstrap CI |
|---|---:|---:|---:|---:|---:|
| 45–55% | 213 / 51 | 49.61% | 59.48% | +9.87pp | [-3.26pp,+23.21pp] |
| 75–85% | 271 / 75 | 80.21% | 90.67% | +10.46pp | [+3.56pp,+16.02pp] |

row-weighted realized 分别为 `48.83%` 与 `86.72%`。同一 target date 内有多个10分钟 checkpoint，故主要口径使用 target-date equal / block bootstrap；row-weighted 只作敏感性展示。

真实 `.7 cross` fresh book 只有9个 two-sided mid，不能单独回答全市场 calibration。其 spread 均值 `1.46c`、中位 `1.0c`，即 ask 相对 mid 平均约 `0.73c`。以这个小切片作执行量级诊断：

```text
mid = 0.80
approx ask = 0.8073
fee = 0.05 × 0.8073 × 0.1927 ≈ 0.0078
taker breakeven ≈ 0.8151
```

所以即使 market midpoint 真正校准到80%，在该 spread 量级下直接 taker 仍约 `-1.51c/share` EV；若屏幕上的80%本身已经是 ask，则仅 fee 就把保本点推到80.8%。50% ask 的 fee-only 保本点为51.25%。实际15-share成本还要看完整 ladder VWAP；本回放只有 top depth，未观测的更深档不作乐观假设。

## Gate 与复现

```text
significance=FAIL
same-denominator market baseline=PARTIAL
frozen forward=NA
conclusion=inconclusive_keep_zero_notional
live behavior changed=false
```

复现：

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_tokyo_jma_multivariate_market_v1.py \
  --trigger-events /Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/events.jsonl \
  --start-date 2026-07-09 --end-date 2026-07-30 \
  --out /Volumes/jrs/pm_agents/research/artifact_store/tokyo_jma_trigger_ab_20260805
```

大明细位于 `/Volumes/jrs/pm_agents/research/artifact_store/tokyo_jma_trigger_ab_20260805`；仓库只保留本结论与可复跑 evaluator。
