# Convective / Tail Distribution alpha v1 — May-long retrain

Status: `runnable zero-notional candidate / not confirmed / independent from Core / live off`

Updated: 2026-08-09 Asia/Shanghai. This section supersedes the July-only result and its C1 artifact.

## 结论

从 5 月重新训练并继续跑完方向性 C4/C5 后，**没有证据证明市场系统性低估 convective tail**。

在 15,874 个 expanding-OOF full-ladder states、58 个 target dates 上，raw market 仍是最好的完整分布：

| model | Brier | logloss | RPS | ECE | ΔBrier vs market | Δlogloss | ΔRPS |
|---|---:|---:|---:|---:|---:|---:|---:|
| market | 0.572449 | 1.113613 | 0.061441 | 0.004431 | baseline | baseline | baseline |
| weather-only | 0.728395 | 1.542724 | 0.107811 | 0.008285 | +0.155946 | +0.429110 | +0.046370 |
| fixed 30% weather offset | 0.579627 | 1.136478 | 0.063358 | 0.014494 | +0.007178 | +0.022865 | +0.001918 |
| C1 market power | 0.574277 | 1.119148 | 0.061750 | 0.002660 | +0.001827 | +0.005535 | +0.000309 |
| **C2 adjacent diffusion** | **0.572826** | **1.115083** | **0.061517** | **0.005360** | **+0.000376** | **+0.001470** | **+0.000077** |
| C3 center+curvature | 0.574256 | 1.119094 | 0.061937 | 0.003543 | +0.001806 | +0.005481 | +0.000496 |
| C4 directional split-tail | 0.573267 | 1.114793 | 0.061606 | 0.003475 | +0.000817 | +0.001180 | +0.000165 |
| C5 directional neighbor transport | 0.573834 | 1.117830 | 0.061751 | 0.005216 | +0.001385 | +0.004217 | +0.000310 |

C2 仍是唯一适合作为 frozen runnable candidate 的结构：9/9 folds 收敛且三项 proper score 最接近 market。它仍没有过概率门；Brier、logloss、RPS 点估全部略差，RPS date-block CI `[+0.000002,+0.000161]` 全正。C3 0/9、C4 5/9 folds 收敛，不能用作 artifact。更低维的 C5 9/9 folds 收敛，但 logloss CI `[+0.000537,+0.010549]`、RPS CI `[+0.000011,+0.000754]` 全正，构成相对 market 的负证据。

C2 几乎总在扩散 market（99.79% states widening，平均 variance ratio 1.149），但这种 widening 没有被 outcomes 验证。因此“模型能造宽尾”不等于“市场低估宽尾”。C4 把 center/hot/cold tail 分开，C5 用 one-step hot/cold probability transport；两者均保留全部缺失天气 rows，不加 hard gate。C5 的失败说明物理约束缓解了收敛和方向语义问题，但不能替代稀缺的 convective supervision。

动作：

```text
family = weather.convective_tail_distribution
candidate = c2_adjacent_kernel_diffusion
mode = research / collector / zero-notional only
live = off
real orders = forbidden
portfolio relation = independent from Core Carry
formal review = after 30 new PIT dates + 80 tickets + fresh-book coverage >= 90%
```

## 时间与固定分母

这里把此前“为什么只有 4 个重叠日”的错误彻底修正：

- raw HeadA denominator：2026-05-06–2026-07-28，3,378 candidates / 83 dates / 49 cities；
- selected 1,224，unselected 2,154，全部进入分母；
- immutable legacy PIT ladder：从 2026-05-19 起；
- canonical `tmax_v2` cutover：2026-07-15；
- hybrid PIT probability states：17,339 / 63 dates；
- expanding OOF：2026-05-24–2026-07-28，15,874 states / 58 dates / 47 cities；
- raw dates without usable probability evidence：5/06–5/18、7/07–7/14，共 21 日。这些是 coverage gap，不是策略过滤。

旧/新 adapter 在 7/15–7/28 的 14 个重叠日匹配 330 个 city-date-lifecycle states：normalized market L1 中位数 0、forecast peak 差中位数 0°F、修正后的 decision clock 差中位数 0 分钟。legacy decision time 使用整梯最后一个 direct book fetch；book age 最小 0、负值 0、中位数 0.03 分钟。

## PIT 特征合同

连续输入保留 city/source bias、POP、云量、风、湿度、forecast dispersion、settlement-native bracket distance、target-day first-seen warming innovation、instant innovation、remaining warming 和 local clock。没有新增城市、source、价格或天气 hard filter；`POP>=50` 仅是描述性 baseline。

5 月旧合同并不拥有完整 forecast-weather fields，不能事后补造：

| feature | state coverage |
|---|---:|
| forecast dispersion，至少两个 vintage | 86.19% |
| target-day warming innovation | 31.24% |
| wind | 31.36% |
| cloud | 26.18% |
| humidity | 21.87% |
| POP | 9.49% |

legacy POP 显式 missing；cloud 使用 PIT METAR sky category 映射，wind/humidity 来自 PIT atlas；warming innovation 使用当时观测相对 D-1 12Z single-run 预期升温。缺失由 missing indicator 进入模型，不能解释成“无对流”。

## 概率、天气时钟与盘口归因

C2 full weather 相对同族 C2 intercept-only 的 Brier/logloss/RPS 改善为 `-0.000177/-0.000627/-0.000037`，但 target-date bootstrap CI 分别为：

- Brier `[-0.001413,+0.000401]`
- logloss `[-0.002455,+0.002119]`
- RPS `[-0.000105,+0.000054]`

所以连续天气条件化有轻微点估贡献，但尚未证明；更重要的是 C2 full 仍输 raw market。

这也不是旧/薄盘口 alpha。fresh full90 有 12,779 states，C2 ΔBrier/Δlogloss/ΔRPS 为 `+0.000426/+0.001261/+0.000082`；partial/stale 为 `+0.000170/+0.002329/+0.000054`，两边都没有胜 market。POP≥50 baseline 同样更差。

local 00–10 的部分 lifecycle 有极小 Brier 点估改善，但 logloss/RPS不一致；不得据此新增时钟 gate。当前无法把 edge 归因于天气时钟，最合理解释是：**没有稳定 edge，少量交易 PnL 来自日期集中和结果噪声。**

source 稳定性也不支持 alpha：ECMWF C2 ΔBrier/Δlogloss/ΔRPS 约 `+0.000020/+0.000588/+0.000045`，GFS 为 `+0.000891/+0.002745/+0.000122`。

C4/C5 continuation 不改变归因。C4 相对自身 intercept-only 有极小天气点估改善
`-0.000373/-0.000559/-0.000023`，三项 date-CI 均跨0且只有5/9 folds收敛。C5相对自身
intercept-only反而退化 `+0.000548/+0.001910/+0.000155`，CI也跨0；其 ECMWF delta
`+0.000787/+0.002646/+0.000223`，GFS `+0.002249/+0.006488/+0.000437`，两源同方向输
market。C5在 fresh full90 与 partial/stale 的三项 delta 也全部为正。因此没有证据把失败归因于旧/薄盘口，
也没有证据据此生成 source gate。

## 交易表达、ROI 与容量

表达没有参与选模。成本为当时 direct ask + 官方 `0.05*p*(1-p)` fee；容量为各腿最小 top-ask size。此前 299/192/123 的三行来自 policy-specific first-positive selectors，不能作为严格 expression A/B。本次修正为同一入场分母：每个 city/target-date 取第一个“任一表达正 net edge 且三种表达均可执行”的 state，并在该 state 同时计入三种表达，包括自身 edge 非正的反事实表达。

共同交易分母为 294 个 city-date events / 53 target dates / 47 cities；三种表达各 294 张，合计 882 expression rows、1,764 条 bracket legs、936 个不同 condition IDs。真正的独立推断 grain 仍是 53 个 target-date blocks。

| expression | tickets / dates | win rate | PnL | ROI | 95% date CI | 去最佳日 ROI | median top-ask | 10-share coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| single YES | 294 / 53 | 9.52% | -$0.99 | -3.42% | [-39.06%,+33.27%] | -13.58% | 34.76 shares | 77.55% |
| adjacent strip | 294 / 53 | 35.03% | -$2.79 | -2.64% | [-11.69%,+6.26%] | -4.07% | 12.65 shares | 61.56% |
| bounded 3-rung basket | 294 / 53 | 54.08% | -$11.06 | -6.50% | [-12.97%,-0.68%] | -7.72% | 10.00 shares | 50.34% |

严格同分母后 single 的历史正点估消失；single/strip 的 CI 跨 0，bounded basket 的 date-block CI 全负，故 bounded basket 在当前统一 entry contract 下是 `rejected_for_expression`。这不等于删除三腿结构；runner 仍记录它作反事实账，但不得成为资金表达。当前最合适的“表达”仍是完整 C2 distribution + 三个并行 zero-notional ledgers；资金表达为 none。

若未来 proper-score 和 frozen-forward ROI 同时过门，保守初始容量仍应按 5–10 shares/expression，而不是把 5c depth 当可成交容量。即使成立，它也是低胜率凸性收益，不是高胜率 Carry。

## 与 Core Carry 的日期级收益相关性

使用 frozen Core replay，在完整共享 calendar 上保留双方 no-trade=0；不使用 runtime score telemetry，也不混入 live fills。有效重叠为 29 target dates：

| expression | Pearson daily PnL correlation |
|---|---:|
| single YES | -0.178 |
| adjacent strip | +0.145 |
| bounded basket | +0.102 |

统一 entry denominator 后三者与 Core 的相关性都较低，但没有任何表达具备稳健 alpha。低相关不能替代 proper-score 和 ROI 门。

## Runnable candidate 与 frozen forward

已冻结 `src/strategies/weather_edge_v1/config/convective_tail_distribution_v1.json`：

- C2 adjacent diffusion；
- training 2026-05-19–2026-07-28；
- 63 target dates、17,322 settled training states；
- final fit converged；
- execution mode 固定 zero-notional。

当前定向测试 5/5 通过，覆盖 C4/C5 distribution semantics、方向性 transport、共同 entry-state selector 和 zero-notional 禁单合同。2026-08-09 当前 collector 的一次真实 zero-notional smoke：79 events seen，2 scored，77 blocked；51 个缺 target-day observation，26 个 full-ladder direct quote<80%/bracket blocker；该时点 0 common-entry tickets、0 orders、`notional_usd=0`。这是 evidence coverage，不是策略筛选。

本次 May-long artifact 重新冻结后，正式 forward counter 重新从 0 开始：`0/30 new PIT dates, 0/80 tickets`；fresh-book coverage 尚不可计算。此前 July-only C1 smoke 的票不计入新模型 forward。

## 最终回答

| 问题 | 回答 |
|---|---|
| 市场是否确实低估 convective tail？ | **否，当前证据不支持。** C4/C5 已把方向性物理结构纳入，但仍未胜 raw market；C5 的 logloss/RPS date-CI 全劣于 market。 |
| edge 来自概率分布、天气时钟还是旧/薄盘口？ | **尚无可确认 edge。** 天气条件化相对同族 intercept 有极小点估改善但 CI 跨 0；fresh 与 partial books 都输 market；时钟切片不稳定。 |
| 最合适的交易表达与容量？ | **现在没有 funded expression。** 同分母下 bounded basket CI 全负，标 `rejected_for_expression`；single/strip 继续 zero-notional，若未来过门初始只按 5–10 shares。 |
| 与 Core Carry 的日期级收益相关性？ | frozen 同日 29 天：single -0.178，strip +0.145，basket +0.102。 |
| 是否值得成为独立 portfolio sleeve？ | **不值得成为资金 sleeve；值得继续作为独立 zero-notional research sleeve。** 不并入 Core，不恢复 HeadA live。 |

## 产物

- research runner: `scripts/analysis/forecast_quality/research_convective_tail_distribution_v1.py`
- zero-notional runner: `scripts/ops/convective_tail_distribution_shadow_v1.py`
- frozen artifact: `src/strategies/weather_edge_v1/config/convective_tail_distribution_v1.json`
- current artifact root: `/Volumes/jrs-archive/pm_agents/research/artifact_store/convective_tail_distribution_v1/2026-08-09-directional-c5`
- C4 numerical-failure snapshot: `/Volumes/jrs-archive/pm_agents/research/artifact_store/convective_tail_distribution_v1/2026-08-09-directional-c4`
- key files: `summary.json`, `adapter_parity.csv`, `heada_denominator.csv`, `state_features.csv`, `rung_predictions.csv`, `model_scorecard.csv`, `weather_attribution_scorecard.csv`, `calibration_bins.csv`, `slice_scorecard.csv`, `source_scorecard.csv`, `expression_ledger.csv`, `policy_specific_tickets_diagnostic.csv`, `tickets.csv`, `trade_scorecard.csv`, `daily_portfolio.csv`, `portfolio_correlation.csv`, `challenger_fit_diagnostics.csv`, `model_artifact.json`.

Reproduce:

```bash
.venv/bin/python scripts/analysis/forecast_quality/research_convective_tail_distribution_v1.py --draws 5000
```
