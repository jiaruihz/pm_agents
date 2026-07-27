# D-1 两端 NO：可靠城市 / 指定 Source v6

## 数据快照

- 数据源：v4 immutable snapshot executable baskets + `runtime/weather.db` canonical `settlement_outcomes`；DB mtime UTC `2026-07-27T15:47:46.397522211+00:00`。
- 输入：4,086 research-replay baskets / 56 target dates / 48 cities；unsettled=0；missing_bracket=0。
- forecast 是 Open-Meteo ECMWF/GFS；项目没有 proprietary forecast，`CITY_MODEL` 只是基于历史 calibration 为每城固定选 ECMWF 或 GFS。

## 结论

前半段 `2026-05-20..2026-06-16` 只用 assigned-source forecast 的 exact-bracket lattice MAE 排名城市，冻结最准的一半/四分之一；后半段 `2026-06-17..2026-07-23` 只评估，不重新选城市。

结果是“部分成立”：准城市的天气误差在 holdout 明显更小，机械两端 NO 相对其余 eligible 城市的 ROI delta 也显著为正；但绝对 ROI 只有 +0.19% / +0.21%，两边 CI 都跨 0，尚未证明扣除两腿成本后存在稳定 alpha。
单纯要求实际 source 与 `CITY_MODEL` 一致并没有救回来：机械 ROI 为 -0.44% / -0.66%。只用可靠城市重训 forecast-tail model 仍显著输给同 rows market。

两个时段共同选中的 22 个城市（research cohort，不是 production allowlist）：Atlanta, Austin, BuenosAires, Busan, Chicago, Dallas, Denver, Istanbul, LA, Lucknow, Madrid, Manila, MexicoCity, Miami, NYC, SaoPaulo, Seattle, Singapore, TelAviv, Tokyo, Warsaw, Wuhan。

### 城市准确度是否前向保持

| policy | accurate / complement rows | holdout MAE Δ steps (95% CI) | mechanical ROI Δ (95% CI) |
|---|---:|---:|---:|
| D-1_12_18_first | 431 / 420 | -0.526 [-0.675, -0.378] | +1.29% [+0.41%, +2.13%] |
| D-1_18_24_first | 409 / 398 | -0.389 [-0.548, -0.226] | +1.79% [+0.81%, +2.77%] |

这个 selector 可能同时代理城市气候稳定性、market lattice 宽度和 forecast source quality；当前只能说它是 ex-ante 可重复的 universe signal，不能把因果全部归给 ECMWF/GFS。

### 概率层：可靠城市上的 forecast 是否打败同 rows market

| policy | cohort | rows / dates | observed | forecast p | market p | logloss Δ vs market (95% CI) | Brier Δ vs market (95% CI) |
|---|---|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | all_holdout | 908 / 28 | +3.30% | +5.79% | +2.87% | +0.04404 [+0.02670, +0.06295] | +0.00958 [+0.00467, +0.01463] |
| D-1_18_24_first | all_holdout | 866 / 27 | +3.70% | +5.35% | +3.04% | +0.04299 [+0.02542, +0.06273] | +0.01004 [+0.00485, +0.01538] |
| D-1_12_18_first | assigned_source | 851 / 28 | +3.29% | +5.81% | +2.87% | +0.04301 [+0.02550, +0.06217] | +0.00900 [+0.00408, +0.01447] |
| D-1_18_24_first | assigned_source | 807 / 27 | +3.84% | +5.29% | +3.03% | +0.04032 [+0.02186, +0.06107] | +0.00930 [+0.00413, +0.01525] |
| D-1_12_18_first | accurate_half | 431 / 28 | +0.93% | +4.03% | +1.69% | +0.02387 [+0.01370, +0.03348] | +0.00381 [+0.00169, +0.00622] |
| D-1_18_24_first | accurate_half | 409 / 27 | +0.98% | +3.58% | +1.81% | +0.01553 [+0.00164, +0.02698] | +0.00222 [-0.00023, +0.00532] |
| D-1_12_18_first | accurate_quartile | 209 / 28 | +1.44% | +4.47% | +2.21% | +0.03749 [+0.02460, +0.05195] | +0.00691 [+0.00259, +0.01197] |
| D-1_18_24_first | accurate_quartile | 195 / 27 | +1.03% | +4.39% | +2.21% | +0.01119 [-0.01403, +0.03139] | +0.00149 [-0.00212, +0.00580] |
| D-1_12_18_first | accurate_half_retrained | 431 / 28 | +0.93% | +3.83% | +1.69% | +0.02147 [+0.01127, +0.03068] | +0.00328 [+0.00122, +0.00581] |
| D-1_18_24_first | accurate_half_retrained | 409 / 27 | +0.98% | +3.64% | +1.81% | +0.01528 [+0.00133, +0.02714] | +0.00217 [-0.00058, +0.00566] |

负 delta 才表示 forecast 优于同 rows market；城市天气预测更准本身不等于 market residual。
`accurate_half_retrained` 只用冻结可靠城市的前半段 rows 重训 forecast-tail model，再一次性评估后半段。

### 交易层

| policy | expression | cohort | baskets / dates | tail hit | break-even | ROI (95% CI) | excess vs market (95% CI) |
|---|---|---|---:|---:|---:|---:|---:|
| D-1_12_18_first | mechanical | all_holdout | 908 / 28 | +3.30% | +2.42% | -0.45% [-0.85%, -0.04%] | -0.22% [-0.62%, +0.17%] |
| D-1_12_18_first | forecast_ev_50bp | all_holdout | 61 / 25 | +29.51% | +18.74% | -5.94% [-11.15%, -0.42%] | -5.23% [-10.50%, -0.06%] |
| D-1_18_24_first | mechanical | all_holdout | 866 / 27 | +3.70% | +2.55% | -0.58% [-1.03%, -0.15%] | -0.33% [-0.81%, +0.10%] |
| D-1_18_24_first | forecast_ev_50bp | all_holdout | 71 / 21 | +28.17% | +18.90% | -5.12% [-11.31%, +0.91%] | -4.35% [-10.64%, +1.96%] |
| D-1_12_18_first | mechanical | assigned_source | 851 / 28 | +3.29% | +2.42% | -0.44% [-0.87%, -0.00%] | -0.21% [-0.64%, +0.23%] |
| D-1_12_18_first | forecast_ev_50bp | assigned_source | 58 / 25 | +29.31% | +18.00% | -6.22% [-11.25%, -0.85%] | -5.56% [-10.42%, -0.42%] |
| D-1_18_24_first | mechanical | assigned_source | 807 / 27 | +3.84% | +2.54% | -0.66% [-1.13%, -0.23%] | -0.41% [-0.86%, +0.03%] |
| D-1_18_24_first | forecast_ev_50bp | assigned_source | 67 / 21 | +28.36% | +18.08% | -5.65% [-11.58%, +0.70%] | -4.91% [-10.93%, +1.12%] |
| D-1_12_18_first | mechanical | accurate_half | 431 / 28 | +0.93% | +1.31% | +0.19% [-0.30%, +0.56%] | +0.38% [-0.12%, +0.75%] |
| D-1_12_18_first | forecast_ev_50bp | accurate_half | 17 / 14 | +17.65% | +18.79% | +0.63% [-6.64%, +7.45%] | +1.42% [-5.69%, +8.47%] |
| D-1_18_24_first | mechanical | accurate_half | 409 / 27 | +0.98% | +1.40% | +0.21% [-0.35%, +0.67%] | +0.42% [-0.15%, +0.88%] |
| D-1_18_24_first | forecast_ev_50bp | accurate_half | 20 / 14 | +15.00% | +17.43% | +1.33% [-6.09%, +8.16%] | +2.11% [-5.69%, +9.09%] |
| D-1_12_18_first | mechanical | accurate_quartile | 209 / 28 | +1.44% | +1.80% | +0.18% [-0.34%, +0.70%] | +0.39% [-0.15%, +0.91%] |
| D-1_12_18_first | forecast_ev_50bp | accurate_quartile | 11 / 8 | +27.27% | +23.07% | -2.38% [-12.68%, +7.74%] | -1.41% [-11.53%, +9.10%] |
| D-1_18_24_first | mechanical | accurate_quartile | 195 / 27 | +1.03% | +1.76% | +0.37% [-0.79%, +1.23%] | +0.60% [-0.59%, +1.47%] |
| D-1_18_24_first | forecast_ev_50bp | accurate_quartile | 15 / 12 | +6.67% | +14.74% | +4.36% [-4.27%, +11.48%] | +5.08% [-4.20%, +12.35%] |
| D-1_12_18_first | reliable_model_ev_50bp | accurate_half | 18 / 15 | +16.67% | +18.10% | +0.79% [-5.79%, +7.32%] | +1.53% [-5.20%, +8.20%] |
| D-1_18_24_first | reliable_model_ev_50bp | accurate_half | 18 / 14 | +11.11% | +16.34% | +2.85% [-5.25%, +9.80%] | +3.66% [-4.78%, +10.68%] |

### 指定 source family（描述性，city composition 不同）

| policy | expression | cohort | baskets / dates | tail hit | break-even | ROI (95% CI) | excess vs market (95% CI) |
|---|---|---|---:|---:|---:|---:|---:|
| D-1_12_18_first | mechanical | ecmwf | 489 / 26 | +4.50% | +3.07% | -0.72% [-1.37%, -0.09%] | -0.46% [-1.10%, +0.16%] |
| D-1_12_18_first | mechanical | gfs | 362 / 28 | +1.66% | +1.54% | -0.06% [-0.55%, +0.39%] | +0.12% [-0.36%, +0.56%] |
| D-1_18_24_first | mechanical | ecmwf | 466 / 25 | +5.36% | +3.25% | -1.08% [-1.68%, -0.50%] | -0.80% [-1.40%, -0.21%] |
| D-1_18_24_first | mechanical | gfs | 341 / 27 | +1.76% | +1.56% | -0.10% [-0.70%, +0.44%] | +0.11% [-0.48%, +0.62%] |

## Signal / Evidence Funnel

Signal funnel（basket）:

- raw executable：4,086 / 56 dates。
- frozen training：2,312 baskets。
- chronological holdout：1,774 baskets。
- assigned-source holdout：1,658。
- accurate-half holdout：840。
- accurate-quartile holdout：404。

Evidence funnel（basket）:

- PIT forecast + direct book + settlement + executable two-NO cost：完整。
- holdout same-row OOF probability：1,774。
- actual fill：0（research replay）。
- external frozen forward：NA；chronological split 是本轮 post-hoc research holdout。

## 8 环覆盖

- 已覆盖：描述性绩效、统计推断、概率分布、执行成本、组合 target-date block、同 rows market baseline。
- 未覆盖：真实 fill/queue、容量、外部 frozen forward。

## 裁决

- forecast accuracy persistence：PASS。
- probability vs market：FAIL。
- trade significance：FAIL。
- conclusion：`inconclusive`；action：`do_not_change_live`。

本轮候选 K=4 cohorts（all / assigned / accurate-half / accurate-quartile）+ 2 source-family diagnostics；未做多重检验校正，因此任何孤立正切片都不作晋升依据。
