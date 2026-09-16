# D-1 Extreme NO + Forecast Overlay v2

> 2026-07-27；research replay；zero notional；不改 live。

## 数据快照

- 数据源：`runtime/weather.db` canonical Tmax v2 ladder / effective PIT forecast / `settlement_outcomes`。
- DB mtime UTC：`2026-07-27T11:14:26.511879921+00:00`。
- 记录：835,031 rung rows；base=646 settled policy-baskets；PIT forecast-ready=227；unsettled=0，missing_bracket=0。

## 结论

上一版已经是 D-1。本版只在完全相同的 direct-NO-ask basket 分母上加入 assigned canonical PIT forecast：forecast daily max 必须同时避开最低、最高两个挂牌 condition；再报告距离两端至少 1/2 个 native step 的敏感性。

| policy | overlay | baskets | dates | tail hit (exact 95% CI) | break-even tail | avg cost | ROI (95% CI) | excess vs market (95% CI) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | same_rows_no_forecast_filter | 119 | 7 | +2.52% [+0.52%, +7.19%] | +1.67% | 1.9833 | -0.43% [-1.40%, +0.25%] | -0.25% [-1.26%, +0.39%] |
| D-1_12_18_first | forecast_excludes_both_extremes | 117 | 7 | +1.71% [+0.21%, +6.04%] | +1.33% | 1.9867 | -0.19% [-1.39%, +0.26%] | -0.01% [-1.14%, +0.44%] |
| D-1_12_18_first | forecast_cushion_ge_1step | 111 | 7 | +0.00% [+0.00%, +3.27%] | +0.64% | 1.9936 | +0.32% [+0.20%, +0.42%] | +0.47% [+0.33%, +0.58%] |
| D-1_12_18_first | forecast_cushion_ge_2steps | 95 | 7 | +0.00% [+0.00%, +3.81%] | +0.46% | 1.9954 | +0.23% [+0.14%, +0.35%] | +0.38% [+0.27%, +0.51%] |
| D-1_18_24_first | same_rows_no_forecast_filter | 108 | 8 | +3.70% [+1.02%, +9.21%] | +2.51% | 1.9749 | -0.60% [-1.44%, +0.11%] | -0.38% [-1.22%, +0.32%] |
| D-1_18_24_first | forecast_excludes_both_extremes | 106 | 8 | +2.83% [+0.59%, +8.05%] | +1.84% | 1.9816 | -0.50% [-1.85%, +0.45%] | -0.29% [-1.70%, +0.67%] |
| D-1_18_24_first | forecast_cushion_ge_1step | 97 | 8 | +1.03% [+0.03%, +5.61%] | +1.00% | 1.9900 | -0.02% [-1.15%, +0.57%] | +0.16% [-1.00%, +0.73%] |
| D-1_18_24_first | forecast_cushion_ge_2steps | 71 | 8 | +1.41% [+0.04%, +7.60%] | +0.70% | 1.9930 | -0.36% [-2.09%, +0.44%] | -0.19% [-1.90%, +0.61%] |

该 overlay 没有形成可发布的 forecast alpha：可用 forecast 的独立 target dates 太少，而且这里只有 point forecast，不是经过 expanding/OOF 校准的 `P(low hit)+P(high hit)`。即使某个小切片 ROI 为正，也不能与 market probability 作 proper-score 比较，更不能据此挑阈值。

更关键的是 coverage：原始两种 policy 合计 15 条 tail-hit 记录中，forecast-ready 只覆盖 7 条，缺 forecast 的日期漏掉 8 条。覆盖缺口包括 7/4–7 的历史 forecast 无可靠 available_at（漏 4 条 tail hit），以及 7/17–19 的部分覆盖（再漏 4 条）；这些缺口不能被记成 forecast filter 的成功。

零 tail-hit 切片的 date bootstrap 只会重采样“没发生 tail”的日期，不能表达未观察到的 rare-event 风险。因此表中同时给出 exact binomial 95% CI；其 tail-hit 上界仍明显高于策略 break-even tail rate，不能据正的点估 ROI 推进。

已有宽分母 `d1 exact landing v2` 也给出相同边界：加入 forecast ceiling、distance、peak clock 等物理因子后，OOF logloss/Brier 均没有打败同 rows 的 market。因此当前没有证据证明“加 forecast”能把机械两端 NO 变成 alpha。

## Signal / Evidence Funnel

- `raw_rung_rows` = 835,031
- `complete_pit_settled_snapshots` = 84,874
- `both_extreme_labels_available_snapshots` = 83,047
- `d1_snapshots` = 49,291
- `d1_both_extremes_executable_snapshots` = 13,780
- `d1_full_ladder_80_snapshots` = 13,771
- `base_selected_baskets` = 646
- `base_selected_dates` = 12
- `pit_forecast_ready_baskets` = 227
- `pit_forecast_ready_dates` = 8
- `pit_forecast_ready_cities` = 47
- `base_tail_hit_rows` = 15
- `forecast_ready_tail_hit_rows` = 7
- `forecast_missing_tail_hit_rows` = 8

## Gate

- significance：所有 overlay 按 target_date block bootstrap；不从 4 个版本中事后挑正 ROI。
- baseline：仍用同 snapshot normalized full-ladder market tail mass。
- probability：FAIL/NA；point forecast 不能替代 calibrated tail probability。
- forward：NA；forecast-ready 独立日期不足，且本轮不是样本开始前冻结。
- conclusion：`inconclusive`；不启 shadow/live。

要让这个方向成立，下一版必须直接输出 expanding/OOF `p_tail_forecast=P(low hit)+P(high hit)`，并验证其 proper score 相对 market tail probability 为负 delta；交易只在 `p_tail_forecast < 2 - executable_cost - safety_buffer` 时发生。

Artifacts:

- `scripts/analysis/market_structure_edge/research_d1_extreme_no_forecast_overlay_v2.py`
- `docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2/summary.json`
- `docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2/summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2/selected_variants.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_forecast_overlay_v2/forecast_coverage_by_date.csv`
