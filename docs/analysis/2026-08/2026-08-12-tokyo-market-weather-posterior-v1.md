# Tokyo market-weather posterior v1

## 数据快照

结论：已经得到第一版**点估正收益**的 Tokyo 候选，但还没有得到可宣称稳定盈利的模型。
`market_weather_strong_shrinkage_logit_blend_v1` 在 3 个 strict exact forward dates 上同时改善
market Brier/logloss，按真实 5-share ask depth 和官方 Weather fee 回放为 9 笔、7 胜、
PnL `+$3.2178`、ROI `+10.12%`。但 target-date bootstrap ROI 95% CI 为
`[-100.00%, +47.97%]`，只有 3 个独立日期，所以状态是 `shadow_candidate / inconclusive`，
不是 confirmed alpha，也不改变 live。

| 项目 | 快照 |
|---|---|
| observed at | `2026-08-11T17:37:10.915992Z` |
| canonical DB | `/Volumes/jrs/pm_agents/runtime/weather.db`；compat 与 physical 均为 device `16777247` / inode `54444`；mtime `2026-08-11T17:33:16.139768Z` |
| production/storage | manifest、controller health、storage identity audit 均 healthy；critical/warning `0/0` |
| exact raw | WCIR bundles `4,325` rows；Tokyo v7 NO `500`；two-sided `173`；first-seen→book ≤30s `156` |
| exact book/depth | exact book `152/156`；5-share displayed ask depth `152/156` |
| settlement | `152` scored rows / 3 dates；condition exact `104`、canonical city-date-bracket `48`；4 个 8/7 bracket 27 rows 同时缺 exact book 与 source-grain label |
| development | 57 rows / 7 dates，2026-07-16..28；`archive_reconstructed_plus_15m`，只选 blend weight，不冒充 exact first-seen |
| weather artifact | 生产 Tokyo adapter 同款 `binary_multigrain_hgb_v5`，SHA `c329b133…b3175`，train cutoff `2026-07-15` |
| final artifact | `posterior_frozen_20260811_v7`；input SHA `e130e734…c49c9` |

Readiness：PIT state/four clocks=`READY`；canonical/build identity=`READY`；fresh quote/depth=`READY 152 rows`；
settlement=`READY for 152 / gap 4`；independent dates=`BLOCKED_LOW_SAMPLE 3`；clean frozen-forward=`READY but short`；
WS capture/reconstruction=`N/A`；sampling grain=`READY, first event×bracket and date-equal`。

## 问题、模型与防未来函数

目标不是打败旧 Tokyo 版本，而是估计
`P(current exact bracket NO wins | PIT JMA path, same-time market)`，并打败同一行的 market probability。

候选只做一个强收缩更新：

```text
logit(p_NO_post) = 0.5 * logit(p_NO_market) + 0.5 * logit(p_NO_weather)
```

`0.5` 只由 7 个 development dates 的 date-equal Brier 在固定
`{0,.1,.2,.3,.4,.5}` 网格中选择；三个 forward dates 的标签没有参与权重选择。开发与 forward
都用同一冻结 weather artifact。复杂 logistic/HGB 候选只作同分母负面对照，没有按 forward ROI 选赢家。

forward 行要求 `collector_exact`、JMA first-seen 不晚于 book、event→book ≤30 秒、双边盘口、
exact active-ladder book 和 5-share depth。后到的 settlement 只作 label；late backfill/issue time 没有冒充 first-seen。

## 双漏斗

Signal funnel：

| 层 | grain | rows | dates |
|---|---|---:|---:|
| WCIR raw journal | bundle row | 4,321 | 11个采集日范围内 |
| Tokyo v7 NO / weather head | evaluation | 500 | 3个可评分日 |
| two-sided + causal ≤30s | event×bracket | 156 | 3 |
| first date×bracket positive net edge | research trade | 9 | 3 |

Evidence funnel：

| 层 | grain | rows | dates | gap |
|---|---|---:|---:|---|
| collector-exact weather | event×bracket | 156 | 3 | 0 |
| exact book | event×bracket | 152 | 3 | 4 |
| canonical settlement | event×bracket | 152 | 3 | 4 个 bracket 27 无 source-grain row |
| 5-share executable | event×bracket | 152 | 3 | 4 |
| actual fill | fill | 0 | 0 | research replay，不是成交 |

## Probability：市场才是基准

以下均为同一 152 rows / 3 target dates，按 target date 等权。输出中的历史字段名 `raw_a8`
在本次 Tokyo 输入实际表示冻结 v5 weather-only probability，不是 Helsinki A8。

| 模型 | Brier | logloss | accuracy | AUC | ΔBrier vs market (95% CI) | ΔLL vs market (95% CI) |
|---|---:|---:|---:|---:|---:|---:|
| market | 0.11434 | 0.34671 | 83.98% | 0.9266 | — | — |
| weather-only | 0.07433 | 0.24123 | 89.86% | 0.9660 | -0.04001 `[-0.07327,-0.00316]` | -0.10549 `[-0.15492,-0.02969]` |
| **market-weather 50/50** | **0.08589** | **0.27245** | **89.18%** | **0.9552** | **-0.02845 `[-0.05006,-0.00602]`** | **-0.07426 `[-0.09948,-0.02642]`** |

这说明当前三天里，JMA/path 确实包含 market 尚未完全吸收的信息；但 152 个重复 checkpoint
不能代替 152 个独立样本，统计独立单位仍只有 3 天。accuracy 只作辅助，不能拿 89.18% 当盈利证明。

## 5-share fee 后回放

固定表达：每个 `target_date × bracket` 首次 `posterior > 5-share ask VWAP + fee` 时 BUY NO，
不加事后价格带、时段或最小 edge 门槛。多个 NO 不冲突：只有最终 winning bracket 的 NO 会输，
其余 exact-bracket NO 可同时赢；这与同时买多档 YES 的互斥问题不同。

| target date | JST | bracket NO | market | posterior | cost/share | result | PnL, 5 shares |
|---|---:|---:|---:|---:|---:|---:|---:|
| 8/7 | 08:47 | 30 | .970 | .984 | .981 | win | +$0.0951 |
| 8/7 | 09:17 | 31 | .895 | .965 | .904 | win | +$0.4775 |
| 8/7 | 10:07 | 32 | .650 | .860 | .671 | win | +$1.6439 |
| 8/7 | 12:35 | 33 | .240 | .491 | .259 | loss | -$1.2969 |
| 8/10 | 10:17 | 30 | .805 | .938 | .837 | loss | -$4.1853 |
| 8/11 | 08:57 | 27 | .925 | .963 | .952 | win | +$0.2381 |
| 8/11 | 10:17 | 28 | .810 | .887 | .827 | win | +$0.8631 |
| 8/11 | 10:47 | 29 | .550 | .637 | .572 | win | +$2.1384 |
| 8/11 | 11:46 | 31 | .305 | .623 | .351 | win | +$3.2439 |

合计 cost `$31.7822`，7/9 胜，PnL `+$3.2178`，ROI `+10.1245%`。分日为
8/7 `+$0.9196`、8/10 `-$4.1853`、8/11 `+$6.4835`；单个坏日能吞掉多个赢家，
因此 ROI CI 仍跨 0。

两个错误都是 source→settlement/remaining-rise 过度自信，而不是“市场已经定死”：8/7 12:35 JST
JMA 33.3°C 时 posterior 把 33 NO 从 market 24% 抬到49.1%，最终却停在33；8/10 10:17 JST
JMA 30.1°C 时把 30 NO 从80.5%抬到93.8%，最终仍停在30。它们说明下一轮应继续积累
source-to-official basis、peak clock、remaining-heat 与 plateau/reversal 的 strict exact 负例，不能从这两个错例追加 hard filter。

## 三门与动作

- `significance=FAIL`：fee-adjusted ROI 95% CI `[-100.00%,+47.97%]`。
- `baseline=PASS_LOW_SAMPLE`：同分母 Brier/logloss delta 的 3-date bootstrap CI 均小于0。
- `forward=FAIL_LOW_SAMPLE`：权重冻结后确有 3 个 exact forward dates，但低于 30 日期门槛。
- `conclusion=shadow_candidate / inconclusive`。
- 动作：冻结 `weather_weight=0.5`，继续 zero-notional 全分母记录；再积累至少 27 个新 settled exact dates，
  满 30 日后只复核，不调权重/阈值。届时同时要求 proper-score CI 全负、fee PnL/ROI CI 下界大于0，
  再讨论策略执行；当前 plan/order/fill/live behavior 均不变。

## 产物与血缘

- materializer：`weather_model_evaluation/tokyo_market_prior_adapter.py`
- shared evaluator：`weather_model_evaluation/market_prior_posterior.py`
- input artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/tokyo_market_posterior/wcir_plus_archive_20260811_v5/`
- result artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/tokyo_market_posterior/posterior_frozen_20260811_v7/`
- frozen candidate spec：`strong_shrinkage_candidate_spec.json`，`live_eligible=false`
- lineage placement：WCIR `ModelOutput` weather head + exact book → research `SignalCandidate` candidate；
  本轮没有生成 `TradeIntent`、plan、order 或 fill。
