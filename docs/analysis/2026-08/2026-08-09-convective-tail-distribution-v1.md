# Convective / Tail Distribution alpha v1

Status: `runnable zero-notional strategy candidate / independent from Core / not confirmed`

Correlation correction, 2026-08-09 16:24 Asia/Shanghai: the earlier Core Carry Pearson
point estimates are withdrawn. They mixed Convective signal replay with Core `live_real`
settled fills and did not use a shared frozen strategy denominator.

## 结论与动作

**已交付可运行的独立 Convective Tail 候选，但仍不能宣称市场系统性低估 convective tail；不并入 Core Carry。**

固定 1,056 张原始 HeadA / 5–20¢ YES 候选（441 张旧 baseline 选中、615 张未选中）后，
weather-only exact-bracket distribution 明显输给 raw market，固定 30% weather offset 也没有通过。
失败后按 2026-08-09 预注册顺序继续跑 C1/C2/C3；C1 `market_power_temperature` 成为首个
shadow qualifier：logloss 相对 market 改善 `-0.03134`，target-date block CI
`[-0.06523,-0.00892]`；Brier 改善 `-0.00529`，但 CI `[-0.01563,+0.00175]`
仍跨 0。它足以冻结为可运行 challenger，不足以升格 confirmed alpha。

选模完全不看 ROI。C1 分布下，single/adjacent/basket 的 OOF ROI 分别为 `-48.68%`、
`-0.42%`、`-3.85%`，三者 date-bootstrap CI 均跨 0；因此 runner 同时输出三个预声明
expression ledger，不根据事后表现指定 basket 为主表达。策略本体是完整 ladder distribution 与连续
tail residual；任何 ticket 都属于低胜率凸性，不是高胜率 Carry。

动作保持：

```text
family = weather.convective_tail_distribution
portfolio_relation = independent_from_core_carry
live = off
orders = none
allowed = runnable collector + zero-notional shadow
promotion = blocked until 30 new PIT dates + 80 model-qualified tickets + >=90% fresh-book coverage
```

不新增城市、source、价格、POP 或天气 hard filter；`POP>=50%` 只保留为旧 baseline 对照。
冻结模型 artifact 为 `c1_market_power_temperature`；runner 已用当前共享 raw 完成一次真实
zero-notional smoke，未部署常驻进程、未恢复 HeadA live、未调用交易所。

## 可证伪假设与固定比较臂

假设：降雨、对流、云量与风场快速变化会使 market exact-bracket distribution 过窄；连续
weather innovation 应能解释完整 ladder 上的 probability residual。

同一 OOF state 比较：

1. `raw market ladder`：direct midpoint 归一化；至少 80% rungs 有 direct two-sided quote；
2. `weather-only distribution`：PIT forecast vintage + expanding city/source bias + POP/cloud/wind +
   24h forecast-vintage dispersion + target-day first-seen warming innovation，映射到 settlement-native ladder；
3. `market-logit offset`：固定 `70% market + 30% weather` 的 log-probability pool，不按结果调权重；
4. 失败后继续跑完预注册 challenger：C1 market power-temperature、C2 adjacent-kernel diffusion、
   C3 center+curvature offset；三者都只用相同 train-prior-date 连续特征；
5. 表达独立评估 `single YES`、`candidate + hotter adjacent`、`candidate + next two hotter`，
   每个 policy 只按当时预测 net edge 选自身表达，不用事后 ROI 在三者间择优。

humidity 已纳入输入合同，但历史 PIT normalized curve 与 observation lineage 的有效覆盖为 `0%`；
本轮没有拿事后 archive weather 冒充 PIT humidity，也没有静默 fallback。它是 frozen-forward
collector 必须补的 schema gap，不是一个可据此调参的缺失值信号。

## 固定分母与双漏斗

### Signal funnel

| 层 | grain | rows | dates | cities | 说明 |
|---|---|---:|---:|---:|---|
| 原始 HeadA 5–20¢ universe | candidate | 1,056 | 25 | 47 | selected 441 + unselected 615，全部保留 |
| canonical PIT lifecycle + quote≥80% | city-date-2h state | 1,646 | 14 | 40 | 每两小时首个 state；不按 winner/rain 选择 |
| expanding OOF | state | 498 | 9 | 35 | 前 5 个 target dates 仅训练 |
| OOF 中可回连 HeadA condition | condition | 140 | 9 | 35 | 其余为 coverage gap，不是策略筛除 |

### Evidence / execution funnel

| 层 | grain | 数量 | 说明 |
|---|---|---:|---|
| PIT POP/cloud/wind | OOF state | 498 | forecast capture available 不晚于 feature clock |
| forecast dispersion（≥2 个 prior selected vintages） | state | 80.26% | 只使用同 city-date、当前 clock 前 24h 的 PIT vintages |
| target-day first-seen warming innovation | state | 100% | first observation 与当前 observation 都不晚于 feature clock |
| humidity | state | 0% | 历史 PIT contract 缺失，显式 blocker |
| C1 model-qualified single / strip / basket | ticket | 17 / 18 / 15 | 每 city-date 首个正 predicted net edge；不以 ROI 择表达 |
| actual fill | fill | 0 | displayed ask/depth replay；zero-notional，无真实订单 |

full-ladder canonical 只覆盖到 2026-07-28。7/29–8/7 没有 current raw full-ladder collector 可回补；
8/8 起的新 collector raw 尚未形成 settled model denominator，因此本报告没有把缺口伪装成策略过滤。

## 概率层：先裁决 distribution

负数 delta 才优于 raw market。

| model | states / dates | Brier | logloss | RPS | ECE | ΔBrier 95% CI | Δlogloss 95% CI | ΔRPS 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| market | 498 / 9 | 0.73129 | 1.53984 | 0.05962 | 0.00753 | baseline | baseline | baseline |
| C1 intercept-only attribution control | 498 / 9 | 0.73451 | 1.54941 | 0.05981 | 0.00910 | +0.00322 [ -0.00100, +0.00709 ] | +0.00957 [ -0.00515, +0.02313 ] | +0.00019 [ -0.00006, +0.00038 ] |
| weather-only | 498 / 9 | 0.78186 | 1.71275 | 0.07334 | 0.01162 | +0.05058 [ +0.01258, +0.10221 ] | +0.17291 [ +0.02803, +0.34835 ] | +0.01372 [ +0.00625, +0.02382 ] |
| market offset | 498 / 9 | 0.72629 | 1.51907 | 0.05938 | 0.00568 | -0.00499 [ -0.01526, +0.00814 ] | -0.02077 [ -0.06233, +0.03138 ] | -0.00024 [ -0.00172, +0.00187 ] |
| **C1 market power-temperature** | 498 / 9 | **0.72600** | **1.50850** | **0.05930** | 0.01177 | **-0.00529 [ -0.01563, +0.00175 ]** | **-0.03134 [ -0.06523, -0.00892 ]** | **-0.00032 [ -0.00112, +0.00025 ]** |
| C2 adjacent-kernel diffusion | 498 / 9 | 0.72722 | 1.52118 | 0.05943 | 0.00633 | -0.00407 [ -0.00938, -0.00040 ] | -0.01866 [ -0.04556, +0.00033 ] | -0.00020 [ -0.00056, +0.00004 ] |
| C3 center+curvature offset | 498 / 9 | 0.73636 | 1.54834 | 0.06053 | 0.00944 | +0.00508 [ -0.00974, +0.02478 ] | +0.00850 [ -0.03633, +0.09244 ] | +0.00090 [ -0.00222, +0.00547 ] |

weather-only 的三项 proper score CI 都显著更差；固定 offset 失败后，C1/C2/C3 没有停跑。
C1 按预注册规则成为首个 runnable shadow qualifier，且 logloss block-CI 全负；但 Brier/RPS
仍未达到 formal promotion gate，ECE 也高于 market。故当前只能说“连续天气特征对 market ladder
宽窄有可运行、可证伪的增量”，不能说已证明普遍宽尾低估。

C1 也不是永久 widening：498 个 OOF states 中 `34.54%` 被加宽，`62.25%` 的 hotter-tail mass
上调，平均 variance ratio `0.9957`。ECMWF 平均 variance ratio `1.093`，GFS 为 `0.835`。
因此可证伪对象是天气条件化的 distribution width/shape residual，而不是“逢对流永远买热尾”。

归因上，去掉全部天气特征、只保留 expanding-date 全局 market-temperature 截距后，模型反而输
market。完整 C1 相对这个 intercept-only control 的 Brier/logloss/RPS delta 为
`-0.00851/-0.04091/-0.00051`；date-block CI 分别为
`[-0.01707,-0.00045] / [-0.07227,-0.01446] / [-0.00122,+0.00011]`。
所以当前 proper-score 增量来自连续天气条件化，不是一个无天气的全局 ladder flattening；但只有
9 个 OOF dates，尚不能细分为某一个天气字段或某个固定 clock。

### POP baseline、weather clock 与 book quality

| slice | states | ΔBrier | Δlogloss | ΔRPS |
|---|---:|---:|---:|---:|
| POP≥50 baseline | 139 | **+0.00591** | **+0.01764** | **+0.00068** |
| POP<50 complement | 359 | -0.00962 | -0.05031 | -0.00071 |
| fresh full90 | 121 | -0.00524 | -0.02551 | -0.00038 |
| partial quote80–90 / other | 377 | -0.00530 | -0.03321 | -0.00031 |
| local 08–10 | 57 | -0.00912 | -0.05427 | -0.00058 |
| local 10–12 | 22 | -0.00757 | -0.02451 | -0.00043 |

盘口不是旧：以整份 ladder 最后一条 direct book fetch 作为 market clock 后，median book age 约
`0.03 min`。C1 在 `fresh_full90` 与 80–90% coverage 两边都有相近点估改善，所以已不能把 edge
归因为旧/薄盘口；但 `POP>=50` 反而退化，说明“rain winner”并不是机制本体。local 08–12 的较好
点估支持连续记录 warming clock，但不能变成时钟 hard gate。

source 稳定性也未通过：

| source | states / dates | C1 ΔBrier | C1 Δlogloss | C1 ΔRPS |
|---|---:|---:|---:|---:|
| ECMWF | 310 / 9 | -0.00889 | -0.04143 | -0.00048 |
| GFS | 188 / 9 | +0.00065 | -0.01471 | -0.00007 |

GFS logloss/RPS 改善但 Brier 轻微退化，仍未完全跨 source 稳定；这不是追加 source filter 的理由。

## 交易层：低胜率凸性，不是 Carry

成本使用当时 direct ask + 官方 `0.05*p*(1-p)` fee；容量使用每条腿最小 top-ask size，
5c displayed depth 只作次级上界，不当 fill。

| expression | tickets / dates | wins | win rate | fee PnL | ROI | 95% date CI | 去最佳日 ROI | median top-ask capacity | 5 / 10-share coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| single YES | 17 / 6 | 1 | 5.88% | -$0.95 | -48.68% | [-100.00%, +108.17%] | -100.00% | 32.50 shares | 100.00% / 82.35% |
| adjacent hot strip | 18 / 7 | 6 | 33.33% | -$0.03 | -0.42% | [-52.67%, +50.34%] | -12.10% | 21.30 shares | 94.44% / 83.33% |
| bounded 3-rung hot basket | 15 / 7 | 6 | 40.00% | -$0.24 | -3.85% | [-36.56%, +9.27%] | -12.35% | 10.00 shares | 86.67% / 73.33% |

三个表达都没有通过 fee ROI；因此不能从旧 offset 的单日 basket 暴利继续指定表达。runner 将
三种表达全部记录，正式 forward 后才按预注册的概率与执行门槛裁决。即使未来成立，它也应按低命中、
互斥多腿、凸性 payoff 单独预算；不能用 accuracy 否定，也不能用单日 ROI 证明。

PnL concentration 仍高：最佳日期占 single / adjacent / basket 全部正日 PnL 的
`100.0% / 46.4% / 62.4%`；去掉最佳日期后的 ROI 如表为
`-100.0% / -12.10% / -12.35%`。这三项都不支持资金晋升。

历史 displayed top ask 对最窄的三档表达中位支持约 10 shares / expression；超过 10 shares 时仅
73.33% basket 可在 top ask 完整覆盖。没有 fill/queue 证据，因此当前预期容量只可写成“小额、约 5–10 shares”，
不能把 5c depth 的中位 209.72 shares 当真实容量。

## 与 Core Carry 的组合关系

本研究没有复用、重训或修改 Core Carry。两者经济形态不同：

```text
Core Carry = 高价、高命中 exact persistence
Convective Tail = 低价/多腿、低命中、凸性 terminal distribution
```

但现有两边没有可用于日期级收益相关性的同口径窗口：

| series | 时间 | dates | 与 Convective OOF 重叠 | 能否作为相关性分母 |
|---|---|---:|---:|---|
| Convective raw HeadA universe | 7/04–7/28 | 25 | 9 | 只是原始候选分母 |
| Convective PIT ladder | 7/15–7/28 | 14 | 9 | 前 5 日训练 |
| Convective expanding OOF | 7/20–7/28 | 9 | 9 | target series |
| frozen Core historical replay | 6/02–7/08 | 31 | **0** | 口径正确，但日期不重叠 |
| Core runtime score telemetry | 7/24–8/09 | 17 | 5 | 不是 portfolio return，且重叠窗内由 v2 切到 v3/10-share |
| Core `live_real` settled fills | 7/25–8/07 | 11 | 4 | 实盘 fill 与 replay 混合，且无成交日被省略 |

所以“四天”只是 `7/25–7/28` 的机械日期交集，不是有效相关性样本。此前发布的 basket
`+0.478`、single `+0.552`、strip `+0.898` 全部撤回；当前相关性应记为 **`NA / not
identifiable`**。正式组合审计必须在接下来的共同 frozen-forward target dates 上，同时生成
Convective 与固定版本 Core 的 signal-level counterfactual daily return，保留双方无交易日，不能再把
replay 与实际 fills 配对。

## Frozen forward 状态

截至 2026-08-09 17:03 北京时间：

- 新 runner `convective_tail_distribution_shadow_v1.py` 已读取当前共享 ladder/books、strategy snapshot、
  PIT forecast vintages、observation cache 和 target-day first-seen observation，产出完整 C1 distribution；
- 一次真实 raw smoke：94 events seen，5 个 D0 events scored，89 个显式 blockers；15 个候选中
  9 个正 residual zero-notional tickets；`orders_enabled=false`、`notional_usd=0`，journal 中没有
  TradeIntent/plan/order/fill；
- 5 个 scored events 的 humidity、forecast dispersion 与 warming innovation 均已进入 ModelOutput；
  但 direct quote fraction 都是 `81.82%`，所以 fresh-book coverage（要求 full90）为 `0%`；
- canonical settlement 只到 8/7，所以 formal settled tail tickets=`0`；
- 当前 smoke 只覆盖 8/9 一个新 PIT target date；8/8、8/10 的 market-only telemetry 不冒充本模型日期。

因此 collector gate 当前是 `1/30 dates, 9/80 zero-notional tickets, fresh coverage 0%/<90% required`；
formal settled gate 仍是 `0`。
本轮冻结模型结构、表达与 gate；后续新标签只作 formal forward，不据中间结果换城市/source/价格/
天气阈值。

## 最终裁决

| 问题 | 回答 |
|---|---|
| 市场是否确实低估 convective tail？ | **尚未正式证实，但已出现可运行的 distribution challenger。** C1 logloss block-CI 全负，Brier/RPS 尚未过 formal gate；weather-only 显著输 market。 |
| edge 来自概率分布、天气时钟还是旧/薄盘口？ | **当前更像 market distribution 宽窄的连续 residual，不像旧盘口。** C1 在 fresh_full90 与 partial ladder 均改善；天气 clock 有贡献但尚不能单独归因。 |
| 最合适表达和容量？ | **策略本体是完整 C1 distribution + continuous residual；single/strip/basket 全部并行记账，不事后择优。** 若未来可下单，初始容量仅约 5–10 shares/expression。 |
| 与 Core Carry 的日期级收益相关性？ | **NA。** 冻结 Core 历史 replay 与 Convective OOF 重叠为 0；四日只是混合 `live_real` fills 与 replay 的无效交集，原 Pearson 点估已撤回。 |
| 值得成为独立 portfolio sleeve？ | **值得作为独立 runnable zero-notional sleeve 积累 forward；现在不值得分配资金。** 不并入 Core、不恢复 HeadA live。 |

## 产物与复现

```bash
.venv/bin/python scripts/analysis/forecast_quality/research_convective_tail_distribution_v1.py --draws 5000
```

- runner: `scripts/analysis/forecast_quality/research_convective_tail_distribution_v1.py`
- frozen challenger preregistration: `docs/analysis/2026-08/2026-08-09-convective-tail-challenger-preregistration-v1.md`
- zero-notional runner: `scripts/ops/convective_tail_distribution_shadow_v1.py`
- frozen model artifact: `src/strategies/weather_edge_v1/config/convective_tail_distribution_v1.json`
- artifact root: `/Volumes/jrs-archive/pm_agents/research/artifact_store/convective_tail_distribution_v1/2026-08-09`
- key artifacts: `summary.json`, `heada_denominator.csv`, `state_features.csv`,
  `rung_predictions.csv`, `model_scorecard.csv`, `calibration_bins.csv`, `slice_scorecard.csv`,
  `source_scorecard.csv`, `tickets.csv`, `trade_scorecard.csv`, `daily_portfolio.csv`,
  `research_window_audit.csv`, `portfolio_correlation.csv`, `challenger_fit_diagnostics.csv`,
  `model_artifact.json`, `distribution_shape_diagnostics.csv`,
  `zero_notional_shadow_smoke_v3/{model_outputs,signal_candidates,blockers}.jsonl`
