# D1 distance-2 NO：历史 forecast bias / accuracy 复用实验 v6

## 数据快照

- 当前误差层：`historical_forecast_enrichment_bias_v1/daily_error_rows.csv`，2026-05-04..2026-07-07；bias calibration 严格冻结到 2026-06-16。
- 早期长历史层：`historical_forecast_station_bias_v1/daily_error_rows.csv`，35,174 city-source-days；GFS/ECMWF 截止日期均早于当前交易窗口。
- 交易分母：immutable D1 distance=2 paired opportunity，1,190 baskets / 20 target dates / 43 cities / 2,380 candidate rows；settled=100%，unsettled=0，missing bracket=0，actual fill=0。
- exact bracket：NO 只在最终 Tmax 不落入该 exact bracket 时赢；entry 使用 archived executable NO ask + Weather taker fee。

## 结论与动作

**早期 bias 研究值得保留，但它的作用边界很清楚：显著改善天气预报准确率，也显著改善纯 forecast probability；仍未打赢同 rows market。因此 bias/accuracy 应作为共享概率特征，不是 region/source hard gate。**

- 动作：把 frozen city×source bias、MAE、spread 保留在 feature/shadow payload；保持 research / frozen shadow，不改 live。
- Europe 残差不能归因于 bias：移除 city-source bias 后 Europe ROI 反而从 +6.35% 变为 +7.00%。

## Target 与预注册 A/B

```text
在固定 D1 distance=2 candidate rows 上，只改变历史 forecast error 的
bias shift；先检验 holdout weather MAE，再检验 P(NO) proper score 与
同 rows market，最后检验相同 executable quotes 的选边 ROI。
```

- `short_full`：当前五模型 forecast + 冻结 city×model 完整经验残差。
- `short_debiased`：从同一残差逐城逐模型减去均值，保留 dispersion，只移除 directional bias。
- `short_global_bias` / `short_shrink50`：用 global model bias，或城市/global 各 50%，属于预定义 shrinkage sensitivity。
- `long_*`：复用早期 356–737 日 ECMWF/GFS city-source 误差；assigned source 与 equal-two-source 都在全分母测试。
- K=8 frozen probability policies + 5 bias increment comparisons + 5 expanding-OOF market-anchor challengers；分别 BH 校正。

## 实验 1：bias 是否真的提高天气预报准确率

| calibration | rows/dates/cities | raw MAE | corrected MAE | ΔMAE [95% CI] |
|---|---:|---:|---:|---:|
| 当前五模型短窗 | 4340/21/43 | 2.583F | 1.891F | -0.692F [-0.776, -0.612] |
| 早期长历史 ECMWF/GFS | 1736/21/43 | 2.370F | 2.036F | -0.334F [-0.390, -0.281] |

按区域，当前短窗 bias correction 的 MAE 改善：

| region | rows | raw → corrected MAE | ΔMAE [95% CI] |
|---|---:|---:|---:|
| EU | 1035 | 2.43 → 1.52F | -0.91 [-0.99, -0.83] |
| AS | 1365 | 2.85 → 1.85F | -1.01 [-1.12, -0.90] |
| ME | 315 | 2.91 → 1.44F | -1.48 [-1.63, -1.30] |
| OC | 105 | 1.35 → 1.18F | -0.17 [-0.61, +0.29] |
| SA | 370 | 2.37 → 2.08F | -0.29 [-0.53, -0.05] |
| AF | 105 | 2.32 → 1.89F | -0.43 [-0.77, -0.05] |
| US | 1045 | 2.51 → 2.46F | -0.05 [-0.21, +0.10] |

这证明旧 bias 层不是废资产：方向性误差在新日期具有 persistence。但这是 weather accuracy gate，不等于 market alpha gate。

## 实验 2：bias 对 P(NO) 的增量

| policy | Brier | Δmarket [95% CI] | logloss | Δmarket [95% CI] |
|---|---:|---:|---:|---:|
| short_full | 0.05422 | +0.00251 [-0.00041, +0.00545] | 0.19888 | +0.02033 [+0.01028, +0.03051] |
| short_debiased | 0.05897 | +0.00729 [+0.00438, +0.01076] | 0.23340 | +0.05482 [+0.03763, +0.07511] |
| short_global_bias | 0.05766 | +0.00594 [+0.00287, +0.00967] | 0.22040 | +0.04187 [+0.02745, +0.05812] |
| short_shrink50 | 0.05566 | +0.00395 [+0.00096, +0.00739] | 0.20849 | +0.02999 [+0.01630, +0.04611] |
| long_assigned_full | 0.06420 | +0.01247 [+0.00891, +0.01595] | 0.29616 | +0.11769 [+0.07173, +0.16597] |
| long_assigned_debiased | 0.06585 | +0.01413 [+0.01087, +0.01736] | 0.28232 | +0.10391 [+0.06833, +0.14499] |
| long_equal2_full | 0.05737 | +0.00567 [+0.00189, +0.00957] | 0.24079 | +0.06218 [+0.02575, +0.10605] |
| long_equal2_debiased | 0.05952 | +0.00780 [+0.00445, +0.01154] | 0.23149 | +0.05297 [+0.02994, +0.07882] |

- `short_full` 相对 `short_debiased` 的 Brier 增量为 -0.00475，CI [-0.00728, -0.00241]，说明 city-source bias 本身具有概率信息。
- 但 `short_full` 仍比 market Brier 差 +0.00251；历史 ECMWF/GFS assigned/average 也没有打赢 market。
- 所以旧方法恢复的是 weather probability quality，不是已经确认的`P(NO)-market` residual。

## 实验 3：相同盘口下的选边交易

| policy | baskets/dates | ROI [95% CI] | ROI Δmarket-selection [95% CI] |
|---|---:|---:|---:|
| short_full | 1190/20 | +1.32% [-0.88%, +3.53%] | +0.94% [-1.09%, +3.12%] |
| short_debiased | 1190/20 | +0.19% [-1.62%, +1.95%] | -0.19% [-2.03%, +1.71%] |
| short_global_bias | 1190/20 | +1.28% [-0.71%, +3.27%] | +0.90% [-0.96%, +2.84%] |
| short_shrink50 | 1190/20 | +1.21% [-0.85%, +3.28%] | +0.83% [-1.11%, +2.91%] |
| long_assigned_full | 1190/20 | +0.63% [-1.26%, +2.42%] | +0.26% [-1.49%, +1.94%] |
| long_assigned_debiased | 1190/20 | +0.50% [-1.30%, +2.13%] | +0.12% [-1.59%, +1.75%] |
| long_equal2_full | 1190/20 | +1.88% [-0.02%, +3.69%] | +1.51% [-0.22%, +3.29%] |
| long_equal2_debiased | 1190/20 | +1.19% [-1.04%, +3.39%] | +0.83% [-1.24%, +2.95%] |
| market | 1190/20 | +0.36% [-0.30%, +1.01%] | +0.00% [+0.00%, +0.00%] |

- bias-aware all5 ROI +1.32%，debiased 为 +0.19%；paired Δ +1.13%，CI [-0.29%, +2.30%]；bias 改善点估但 CI 跨 0，也不是预先独立 frozen forward。
- 早期长历史单一 assigned source 不如五模型经验分布；旧长历史适合做 prior/稳定器，不应退回单源策略。

### Region A/B：Europe 是否由 bias 驱动

| region | bias-aware ROI [CI] | debiased ROI [CI] | full−debiased Δ [CI] | agreement |
|---|---:|---:|---:|---:|
| EU | +6.35% [+2.57%, +9.78%] | +7.00% [+4.57%, +9.59%] | -0.65% [-3.18%, +1.60%] | 70.2% |
| AS | +0.55% [-4.38%, +5.32%] | -1.68% [-6.09%, +2.30%] | +2.24% [-0.00%, +4.42%] | 70.9% |
| ME | +6.14% [-2.87%, +14.31%] | +4.76% [-4.89%, +13.00%] | +1.42% [-4.99%, +8.29%] | 65.3% |
| OC | +9.78% [+2.56%, +19.96%] | +6.47% [-5.09%, +15.51%] | +3.66% [-6.57%, +20.90%] | 34.8% |
| SA | -2.54% [-7.89%, +2.41%] | -3.02% [-8.25%, +1.98%] | +0.48% [-0.65%, +2.36%] | 66.7% |
| AF | -5.71% [-23.25%, +7.67%] | -5.23% [-22.89%, +8.10%] | -0.48% [-0.89%, -0.14%] | 73.5% |
| US | -2.00% [-5.91%, +1.84%] | -3.81% [-8.29%, +0.42%] | +1.83% [-0.94%, +5.13%] | 68.1% |

Europe 在移除 bias 后没有消失，故其剩余 market residual 不是 city-source mean bias 造成。Asia/US 反而从 bias correction 得到更明显选边改善。

## 实验 4：market-anchor expanding OOF

- 每个 target date 只用更早日期拟合；前 5 日 warm-up，后 15 日共 1,734 candidate rows / 867 baskets。固定 L2 logistic `C=0.1`，无阈值调参。

| model | Brier Δmarket [95% CI] | logloss Δmarket [95% CI] | BH beats market? |
|---|---:|---:|---:|
| market_platt | +0.00095 [-0.00070, +0.00294] | +0.00446 [+0.00029, +0.00942] | NO |
| market_plus_short_full | +0.00096 [-0.00080, +0.00306] | +0.00408 [-0.00032, +0.00928] | NO |
| market_plus_short_debiased | +0.00100 [-0.00068, +0.00304] | +0.00512 [+0.00098, +0.01003] | NO |
| market_plus_bias_accuracy | +0.00134 [-0.00073, +0.00371] | +0.00716 [+0.00100, +0.01433] | NO |
| market_plus_long_history | +0.00125 [-0.00096, +0.00360] | +0.00879 [+0.00142, +0.01587] | NO |

| OOF selector | baskets/dates | ROI [95% CI] | Δmarket selector [95% CI] |
|---|---:|---:|---:|
| market | 867/15 | +0.48% [-0.35%, +1.26%] | +0.00% [+0.00%, +0.00%] |
| market_platt | 867/15 | -0.45% [-2.94%, +1.85%] | -0.96% [-3.09%, +0.95%] |
| market_plus_short_full | 867/15 | -0.92% [-3.36%, +1.32%] | -1.42% [-3.52%, +0.56%] |
| market_plus_short_debiased | 867/15 | -0.61% [-2.92%, +1.50%] | -1.11% [-3.12%, +0.74%] |
| market_plus_bias_accuracy | 867/15 | -1.45% [-4.21%, +1.02%] | -1.97% [-4.27%, +0.14%] |
| market_plus_long_history | 867/15 | -1.11% [-3.77%, +1.52%] | -1.61% [-3.98%, +0.68%] |

market-anchor 的作用是检验 bias 是否在市场价格之外还有增量；若 proper score 不过，任何 selected ROI 都只能作探索性诊断。

## Signal / evidence funnel、完整性与三门

- signal：2,380 fixed candidates → 1,190 paired baskets → 每 policy 1,190 selections；OOF 1,734 candidates → 867 baskets。
- evidence：frozen forecast residual + PIT forecast + PIT quote + settlement 全覆盖；actual fill=0。
- 8 环已覆盖：描述、统计、判别、概率、executable fee、target-date correlation、同分母 market baseline；缺真实 fill/queue、capacity、外部 frozen forward。
- significance=bias weather/probability increment PASS；baseline=FAIL；forward=NA；conclusion=`inconclusive_reusable_feature`；动作=复用 bias/accuracy 特征，保持 research/frozen shadow，不改 live。

## Bloodline placement

- 共享实现继续使用 `weather_feature_layer.bias` 的 as-of city×source bias/MAE/tails；不新增平行事实表。
- 新 forecast snapshot 应保存 issue/run/first-seen；本报告的历史误差层只负责 prior calibration，不冒充 decision-time forecast version。
- 后续 `fact_signal_candidates` opportunity payload 记录 frozen bias、MAE、spread、bias-corrected probability 与 market residual；region 只作审计切片，不作 eligibility。
