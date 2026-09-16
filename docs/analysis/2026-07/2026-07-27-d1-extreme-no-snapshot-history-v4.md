# D-1 Extreme NO Full Snapshot History v4

> 2026-07-27；research replay；zero notional；不改 live。

## 结论

此前 6–12 天是 `tmax_v2` ladder 与严格 hourly-curve lineage 的交集，不是历史数据总量。本版回到 immutable paper snapshot：5 月 19 日起已有 direct YES/NO book，同时保留 decision-time `forecast_max_f`，因此可在更长的同分母上重跑。

### 不加 forecast 的机械两端 NO

| slice | baskets | dates | tail hit | break-even tail | market tail | avg cost | PnL | ROI (95% CI) | excess vs market (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | 2071 | 56 | +7.15% | +6.57% | +7.22% | 1.9343 | $-11.932 | -0.30% [-0.72%, +0.12%] | +0.04% [-0.38%, +0.47%] |
| D-1_18_24_first | 2015 | 55 | +7.49% | +6.70% | +7.32% | 1.9330 | $-15.946 | -0.41% [-0.83%, +0.01%] | -0.09% [-0.50%, +0.35%] |

### Forecast expanding-OOF 概率层

| policy | rows | dates | observed tail | forecast p | market p | logloss Δ vs market (95% CI) | Brier Δ vs market (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | 1880 | 51 | +6.17% | +9.42% | +6.21% | +0.06275 [+0.04874, +0.07680] | +0.01575 [+0.01120, +0.02043] |
| D-1_18_24_first | 1813 | 50 | +6.73% | +8.81% | +6.32% | +0.05426 [+0.04193, +0.06726] | +0.01344 [+0.00969, +0.01732] |

负 delta 才表示 forecast 模型在同 rows 上优于 market tail probability。

### Forecast EV 交易层

| policy | selector | baskets | dates | tail hit (exact 95% CI) | break-even tail | ROI (95% CI) | excess vs market (95% CI) |
|---|---|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | forecast_ev_buffer_0bp | 205 | 49 | +38.05% [+31.38%, +45.07%] | +33.28% | -2.86% [-5.45%, -0.17%] | -2.06% [-4.67%, +0.72%] |
| D-1_12_18_first | forecast_ev_buffer_50bp | 176 | 46 | +43.18% [+35.75%, +50.85%] | +37.78% | -3.33% [-6.60%, -0.23%] | -2.44% [-5.69%, +0.78%] |
| D-1_12_18_first | forecast_ev_buffer_100bp | 161 | 45 | +44.72% [+36.89%, +52.75%] | +40.87% | -2.42% [-5.63%, +0.92%] | -1.47% [-4.70%, +1.97%] |
| D-1_18_24_first | forecast_ev_buffer_0bp | 220 | 44 | +39.55% [+33.04%, +46.34%] | +34.83% | -2.86% [-5.96%, +0.27%] | -2.06% [-5.28%, +1.01%] |
| D-1_18_24_first | forecast_ev_buffer_50bp | 197 | 41 | +44.16% [+37.11%, +51.39%] | +38.37% | -3.59% [-6.93%, -0.20%] | -2.72% [-6.15%, +0.67%] |
| D-1_18_24_first | forecast_ev_buffer_100bp | 183 | 38 | +46.45% [+39.06%, +53.95%] | +40.96% | -3.45% [-7.00%, +0.13%] | -2.56% [-6.01%, +0.95%] |

裁决：`inconclusive`。probability-vs-market=`FAIL`；trade significance=`FAIL`；frozen forward=`NA`。

## 数据与血缘

- snapshot 文件：总计 5,785；direct schema 窗口扫描 5,154。
- settled D-1 complete-direct snapshots：189,990；65 target dates；3,011 city-days。
- executable first-window baskets：4,086；56 target dates。
- expanding-OOF：3,693 baskets；51 target dates；238 tail hits。
- ladder/book/forecast 值来自同一 immutable snapshot；snapshot ts 是 capture availability boundary。
- `model_init_utc_estimated` 仍只是 estimated run lineage；本报告没有把 point forecast 冒充 verified hourly curve。
- settlement 只在 PIT 候选生成后从 canonical `settlement_outcomes` join。
- significance 以 target_date block bootstrap；actual fill=0。

Artifacts:

- `scripts/analysis/market_structure_edge/research_d1_extreme_no_snapshot_history_v4.py`
- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/summary.json`
- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/baseline_summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/probability_summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/trade_summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/oof_scored_baskets.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/training_audit.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/selected_baskets.csv`
