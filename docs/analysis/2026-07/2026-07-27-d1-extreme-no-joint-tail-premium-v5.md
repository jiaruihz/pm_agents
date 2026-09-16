# D-1 两端 NO：联合 Tail Insurance Premium v5

> 2026-07-27；research replay；zero notional；不改 live。

## 结论

本轮不再使用 source error、native-lattice distance、run stability、ensemble disagreement 或 station basis。唯一输入是同一决策时刻盘口对“最低档或最高档命中”的联合隐含概率，检验市场是否长期对极端保险定价过贵。

结果直接说：没有发现新增 alpha。market-only 校准未显著改善 raw market 的 logloss/Brier；固定 50bp 主规则在两个时段分别为 -3.85% ROI（CI -9.90%..+3.20%）和 -3.33% （CI -11.57%..+4.62%）。前三个最差 target dates 分别贡献主规则总亏损的 55.0% 和 58.5%，不符合“平滑稳健 carry”。

### 概率层

| policy | rows / dates | observed | raw market p | calibrated p | logloss Δ vs raw (95% CI) | Brier Δ vs raw (95% CI) | latest slope |
|---|---:|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | 1681 / 46 | +5.00% | +5.09% | +4.83% | -0.00018 [-0.00121, +0.00096] | +0.00008 [-0.00010, +0.00029] | 1.091 |
| D-1_18_24_first | 1624 / 45 | +5.48% | +5.21% | +4.99% | +0.00041 [-0.00067, +0.00162] | +0.00010 [-0.00018, +0.00039] | 1.049 |

负 delta 才表示 market-only 校准器在同 rows 上优于 raw market。

### 交易层

| policy | selector | baskets / dates | tail hit | break-even | ROI (95% CI) | excess vs market (95% CI) |
|---|---|---:|---:|---:|---:|---:|
| D-1_12_18_first | platt_ev_buffer_0bp | 267 / 42 | +8.99% | +8.81% | -0.09% [-1.08%, +0.91%] | +0.07% [-0.92%, +1.07%] |
| D-1_12_18_first | platt_ev_buffer_50bp | 40 / 19 | +42.50% | +36.19% | -3.85% [-9.90%, +3.20%] | -3.57% [-9.61%, +3.57%] |
| D-1_12_18_first | platt_ev_buffer_100bp | 15 / 9 | +46.67% | +47.19% | +0.34% [-9.98%, +15.34%] | +0.49% [-9.86%, +15.07%] |
| D-1_18_24_first | platt_ev_buffer_0bp | 125 / 30 | +28.00% | +24.92% | -1.76% [-5.63%, +2.09%] | -1.50% [-5.44%, +2.36%] |
| D-1_18_24_first | platt_ev_buffer_50bp | 52 / 15 | +50.00% | +44.84% | -3.33% [-11.57%, +4.62%] | -2.91% [-11.13%, +5.08%] |
| D-1_18_24_first | platt_ev_buffer_100bp | 33 / 11 | +57.58% | +59.47% | +1.35% [-9.59%, +13.09%] | +1.74% [-9.02%, +13.90%] |

### “稳健赚钱”风险层：tail loss 是否同日聚集

| policy / slice | baskets / dates | dates ≥2 hits | max hits | worst day | max DD | worst 3 loss share | calibrated overdispersion (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first / all_oof | 1681 / 46 | 19 | 11 | $-4.02 | $-13.05 | +38.72% | 1.34 [0.67, 2.09] |
| D-1_12_18_first / primary_50bp | 40 / 19 | 3 | 5 | $-1.10 | $-2.63 | +55.00% | 1.10 [0.51, 1.85] |
| D-1_18_24_first / all_oof | 1624 / 45 | 20 | 10 | $-2.58 | $-18.19 | +31.13% | 1.33 [0.79, 1.82] |
| D-1_18_24_first / primary_50bp | 52 / 15 | 5 | 6 | $-1.47 | $-4.75 | +58.46% | 1.53 [0.79, 1.95] |

`overdispersion=1` 近似表示同日 tail-hit 波动与逐篮子独立概率相符；大于 1 表示亏损按天气日聚集。它是风险诊断，不是新的 eligibility filter。

## 与旧研究的边界

- 2026-07-24 的 market-implied-tail-residual P0/P1 研究对象是单个 exact-bracket YES 的 lifecycle/price cell；本研究对象是最低档 NO + 最高档 NO 的联合两腿成本与联合 tail label。
- v3/v4 用 forecast geometry 预测 tail，本研究完全排除天气特征，只检验市场自身是否存在稳定 favorite/long-shot bias。

## Signal / Evidence Funnel

Signal funnel（basket）:

- fixed D-1 executable baskets：4,086 / 56 target dates。
- strictly-prior-date OOF scored：3,305 / 46 dates。
- fixed 50bp primary selector：92 / 24 dates。

Evidence funnel（basket）:

- direct NO ask + official fee + exact-bracket settlement：与 v4 同分母。
- same-row raw market baseline：完整。
- actual fill：0（research replay）。
- frozen forward：NA；当前仍是 post-hoc expanding OOF。

## 裁决

- probability gate：FAIL。
- trade gate：FAIL。
- conclusion：`inconclusive`；action：`do_not_shadow_or_live`。

## 产物

- `docs/analysis/2026-07/generated/d1_extreme_no_joint_tail_premium_v5/probability_summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_joint_tail_premium_v5/trade_summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_joint_tail_premium_v5/clustering_summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_joint_tail_premium_v5/daily_clustering_detail.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_joint_tail_premium_v5/oof_scored_baskets.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_joint_tail_premium_v5/selected_baskets.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_joint_tail_premium_v5/training_audit.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_joint_tail_premium_v5/summary.json`
