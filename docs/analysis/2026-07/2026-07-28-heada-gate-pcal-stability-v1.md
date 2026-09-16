# HeadA 原筛选条件与 expanded p_cal 稳定性 v1

Generated: 2026-07-28T15:43:09+00:00

## 数据快照

| 字段 | 值 |
| --- | --- |
| historical HeadA | `docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/base_rows.csv`；476 rows / 53 dates / 2026-05-06..06-30 |
| current would-live | `docs/analysis/2026-07/generated/heada_would_live_shadow_v2/entries.csv`；104 rows / 9 dates / 2026-07-16..25 |
| broad cheap-YES | canonical `fact_signal_candidates` loader；settled through 2026-07-27 |
| settlement | scored rows 100% settled；missing_bracket=0 |
| execution | historical decision ask / current captured fresh ask；Weather taker feeRate=0.05 |
| trade class | research replay / zero-notional shadow；actual fill=0 |

## 先给结论

1. **expanded p_cal 没有加新特征，也尚未证明比旧 p_cal 更好。** 只把训练截止从 6/20 扩到 7/12，
   训练量增至 2102 rows / 67 dates，并重估原来的
   `model_p、ask、bias mean/p90、hot/cold tail、assigned source、UTC decision-hour bucket`。
   theta 从 0.0188 提到 0.0241。
2. **原 HeadA 里最像稳定机制的是 `dist>0`，但它不是“dist<=0 永远不会中”。**
   历史和当前都提高点估 ROI；当前被剔除的 20 张仍有 3 个赢家。
3. **`edge>=0.20` 不稳定。** 它在 6/20 前、6/21..30、7/1..12 都为正，
   到 7/13..27 同分母转为负，且低于 `edge<0.20` complement；这更像旧
   `model_p` 过度自信，不应继续当永久物理 gate。
4. **5–20c 是表达边界，不是越便宜越有 edge。** 14–20c 历史和当前都正；
   5–8c 历史为正、当前 18/18 全输，说明价格桶会换 regime。
5. **fresh-book 是证据有效性 gate，不是天气 alpha。** 历史 missing/thin book
   反而贡献高 ROI，说明晚/缺盘口会制造 archive bias；只有拿到 fresh executable ask
   才能谈真实策略。
6. `HeadA mechanism → p_cal overlay` 是值得并行验证的一个 profile，不是唯一正确架构。
   expanded p_cal 单独替换 HeadA gates 失败，但在当前 84 张 HeadA 上二次筛到 63 张、12 中、ROI +44.3%；
   这是 8 日 retrospective 结果；而且去掉最高5个赢家后仍为负，不能当 fresh confirmed。

## 新 p_cal 到底升级了什么

没有 feature upgrade，只有 sample/recalibration upgrade。不同训练集的 scaler 也不同，
所以 coefficient 大小不能机械横比；可看符号和相对结构：

| feature | old_coef | new_coef | delta |
| --- | --- | --- | --- |
| logit_model_p | 0.0821 | 0.1473 | 0.0651 |
| logit_ask | 0.4074 | 0.4352 | 0.0278 |
| bias_mean | 0.1017 | -0.0715 | -0.1732 |
| bias_p90 | -0.2252 | -0.1605 | 0.0647 |
| hot_tail_pct | 0.0763 | 0.2935 | 0.2172 |
| cold_tail_pct | 0.0252 | 0.1342 | 0.1090 |
| forecast_model_ecmwf | -0.4824 | -0.6102 | -0.1279 |
| forecast_model_gfs | -0.8402 | -0.6910 | 0.1492 |
| dec_hour_bucket_h00_05 | -0.4033 | -0.2816 | 0.1217 |
| dec_hour_bucket_h06_11 | -0.3830 | -0.4165 | -0.0334 |
| dec_hour_bucket_h12_17 | -0.0164 | -0.0859 | -0.0694 |
| dec_hour_bucket_h18_23 | -0.5198 | -0.5173 | 0.0025 |

主要变化：`model_p`、`ask`、`hot_tail_pct` 权重上升；`bias_mean` 从正转负；
ECMWF/GFS 类别差从旧版约 0.36 logit 缩到约 0.08。新增训练窗中 GFS 城组短期变好，
模型因此弱化了原 source 差异，但该关系在 7/13..27 又反转。模型仍没有 multi-source
spread、forecast innovation、云雨风 regime、settlement-lattice overshoot 或 fresh-book 特征。

## 条件一：`edge>=0.20`

固定 broad cheap-YES city-date denominator；这是在测旧 model edge，不是 HeadA 全机制：

| window | selector | rows | dates | wins | win_rate | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| old_train | edge_ge_0p20 | 340 | 45 | 39 | 0.1147 | 0.0957 | -0.2393 | 0.4644 | -0.0357 |
| old_train | edge_lt_0p20 | 1011 | 46 | 97 | 0.0959 | -0.1246 | -0.2978 | 0.0485 | -0.1677 |
| extension | edge_ge_0p20 | 86 | 9 | 14 | 0.1628 | 0.5259 | 0.0519 | 1.1262 | 0.0250 |
| extension | edge_lt_0p20 | 244 | 9 | 15 | 0.0615 | -0.4431 | -0.6167 | -0.2788 | -0.6235 |
| development | edge_ge_0p20 | 99 | 12 | 16 | 0.1616 | 0.6194 | -0.0282 | 1.3784 | 0.1557 |
| development | edge_lt_0p20 | 322 | 12 | 35 | 0.1087 | 0.0037 | -0.2726 | 0.3425 | -0.1319 |
| retro_forward | edge_ge_0p20 | 134 | 15 | 10 | 0.0746 | -0.3693 | -0.6790 | -0.0423 | -0.6715 |
| retro_forward | edge_lt_0p20 | 376 | 15 | 30 | 0.0798 | -0.3025 | -0.4844 | -0.1166 | -0.4146 |

同窗 `edge>=0.20 − edge<0.20`：

| window | selected_minus_rejected_roi | ci_low | ci_high |
| --- | --- | --- | --- |
| old_train | 0.2203 | -0.1686 | 0.6512 |
| extension | 0.9690 | 0.4163 | 1.6846 |
| development | 0.6157 | -0.1542 | 1.4955 |
| retro_forward | -0.0668 | -0.4524 | 0.3384 |

结论：训练期漂亮、7/13 后翻号。它是 regime-sensitive model output，不是稳定天气边界。

## 条件二：`dist>0`

`dist>0` 表示买的 exact bracket 下沿仍高于 assigned forecast，确实对应“押热尾”
这一物理机制：

| window | selector | rows | dates | wins | win_rate | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_le_2026_06_20 | dist_gt_0 | 275 | 44 | 40 | 0.1455 | 0.3251 | -0.0211 | 0.7248 | 0.1724 |
| historical_le_2026_06_20 | dist_le_0 | 108 | 41 | 10 | 0.0926 | -0.1439 | -0.7237 | 0.5037 | -0.5535 |
| historical_2026_06_21_30 | dist_gt_0 | 58 | 9 | 10 | 0.1724 | 0.6678 | 0.1805 | 1.3394 | -0.0956 |
| historical_2026_06_21_30 | dist_le_0 | 35 | 9 | 5 | 0.1429 | 0.1463 | -0.7118 | 1.0501 | -1.0000 |
| current_2026_07_16_25 | dist_gt_0 | 84 | 8 | 12 | 0.1429 | 0.1804 | -0.4731 | 0.7229 | -0.2694 |
| current_2026_07_16_25 | dist_le_0 | 20 | 7 | 3 | 0.1500 | 0.0434 | -0.5367 | 0.3979 | -1.0000 |

`dist>0 − dist<=0`：

| window | dist_gt0_minus_le0_roi | ci_low | ci_high |
| --- | --- | --- | --- |
| historical_le_2026_06_20 | 0.4689 | -0.1569 | 1.0565 |
| historical_2026_06_21_30 | 0.5215 | -0.2817 | 1.5403 |
| current_2026_07_16_25 | 0.1370 | -0.7321 | 1.2813 |

历史总体支持它；当前方向仍正但差距变小、CI 宽。应保留为 mechanism boundary，
不能描述为“另一侧完全不会中”。

## 原 HeadA 跨窗稳定性

| window | rows | dates | wins | win_rate | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_full | 333 | 53 | 50 | 0.1502 | 0.3819 | 0.0712 | 0.7234 | 0.2549 |
| historical_may | 134 | 24 | 24 | 0.1791 | 0.5801 | -0.0053 | 1.2425 | 0.2805 |
| historical_jun_01_20 | 141 | 20 | 16 | 0.1135 | 0.0668 | -0.3293 | 0.4979 | -0.2483 |
| historical_jun_21_30 | 58 | 9 | 10 | 0.1724 | 0.6678 | 0.1805 | 1.3394 | -0.0956 |
| current_jul_16_25 | 84 | 8 | 12 | 0.1429 | 0.1804 | -0.4731 | 0.7229 | -0.2694 |

四段 point ROI 都为正，说明不是只靠单一日期；但 6 月两段和当前窗去掉最高
5 个赢家后均转负，当前 8 日 CI 很宽。它有历史模式，但仍是高度凸、赢家集中的彩票表达。

## 价格条件

| window | selector | rows | dates | wins | win_rate | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_2026_05_06_06_30 | 5_8c | 130 | 49 | 12 | 0.0923 | 0.3485 | -0.2921 | 1.0854 | -0.1838 |
| historical_2026_05_06_06_30 | 8_14c | 138 | 48 | 17 | 0.1232 | 0.0783 | -0.3317 | 0.5344 | -0.2160 |
| historical_2026_05_06_06_30 | 14_20c | 65 | 38 | 21 | 0.3231 | 0.8231 | 0.1754 | 1.5257 | 0.4902 |
| current_2026_07_16_25 | 5_8c | 18 | 8 | 0 | 0.0000 | -1.0000 | -1.0000 | -1.0000 | -1.0000 |
| current_2026_07_16_25 | 8_14c | 45 | 8 | 7 | 0.1556 | 0.3462 | -0.4143 | 1.1014 | -0.5666 |
| current_2026_07_16_25 | 14_20c | 21 | 7 | 5 | 0.2381 | 0.3513 | -0.7622 | 1.3382 | -1.0000 |

稳定信息不是“越便宜越好”，而是 14–20c 档在两阶段都维持正点估；
5–8c 明显换 regime。现阶段价格应进入概率/EV与 sizing，不新增事后 hard block。

## p_cal 放回正确 HeadA 分母

在 current 84 张 `dist>0 + edge/time/fresh-book` ticket 上：

| selector | rows | dates | wins | win_rate | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| old_pcal_v2_selected | 63 | 8 | 11 | 0.1746 | 0.4350 | -0.3839 | 1.2982 | -0.1527 |
| old_pcal_v2_rejected | 21 | 8 | 1 | 0.0476 | -0.6001 | -1.0000 | 0.3193 | -1.0000 |
| expanded_pcal_v3_selected | 63 | 8 | 12 | 0.1905 | 0.4425 | -0.3584 | 1.1724 | -0.0949 |
| expanded_pcal_v3_rejected | 21 | 8 | 0 | 0.0000 | -1.0000 | -1.0000 | -1.0000 | -1.0000 |

同84张的概率质量，负 delta 才优于 fresh market：

| model | rows | dates | observed_rate | mean_market | mean_p | brier_delta_vs_market | brier_ci_low | brier_ci_high | logloss_delta_vs_market | logloss_ci_low | logloss_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| old_pcal_v2 | 84 | 8 | 0.1429 | 0.1160 | 0.1576 | -0.0059 | -0.0181 | 0.0049 | -0.0170 | -0.0611 | 0.0233 |
| expanded_pcal_v3 | 84 | 8 | 0.1429 | 0.1160 | 0.1575 | -0.0032 | -0.0116 | 0.0054 | -0.0086 | -0.0399 | 0.0237 |

expanded 点估优于 market，但 proper-score CI 跨0；selected ROI 仍高度依赖少数赢家。
旧 p_cal 在同84张上的 Brier/logloss 点估还略好于 expanded；expanded 只是通过新 theta
换票后多保住一个赢家，尚不是概率模型意义上的升级。

同窗 ranking delta：

| model | comparison | roi_delta | ci_low | ci_high |
| --- | --- | --- | --- | --- |
| old_pcal_v2 | selected_minus_rejected | 1.0350 | -0.1457 | 2.1686 |
| old_pcal_v2 | selected_minus_all_headA | 0.2546 | -0.0257 | 0.7590 |
| expanded_pcal_v3 | selected_minus_rejected | 1.4425 | 0.6416 | 2.1724 |
| expanded_pcal_v3 | selected_minus_all_headA | 0.2621 | 0.1004 | 0.5357 |

所以 overlay 只作为并行 frozen profile，不能用这 8 日结果回头改 gate。

## 其余原条件怎么理解

- `22–24h to settlement`：当前历史表本身已被这个窗口选择，缺同源 fresh-book 的
  其他时段反事实，**无法识别它是否产生 alpha**。它目前只是 D-1 信息时钟边界。
- `one per city-date/signal`：是去重和风险控制，不是预测条件。
- `fresh ask / depth / snapshot age`：属于 evidence/execution funnel；其作用是防止
  stale quote 假收益，不应和天气筛选混成一层。
- `maker-first`：当前没有真实 shadow fills，不能把 maker limit 当已成交收益；
  本报告统一用 taker-at-fresh-ask。

## 双漏斗与裁决

Signal funnel：broad cheap YES → original `edge/time` → `dist>0` exact-tail ticket
→ optional p_cal overlay。

Evidence funnel：PIT forecast/bias → decision/fresh ask → settlement
→ fee-adjusted taker replay → actual fill=0。

```text
significance = historical HeadA absolute ROI PASS；dist 增量与 current p_cal proper score FAIL（CI跨0）
baseline = expanded overlay 点估优于 fresh market，但 CI 跨0
forward = FAIL/NA（7/28..8/11 尚未开封）
conclusion = shadow_candidate / inconclusive
live_action = none
```

## 产物

- evaluator：`scripts/analysis/forecast_quality/research_heada_gate_pcal_stability_v1.py`
- structured：`generated/heada_gate_pcal_stability_v1/*.csv`
