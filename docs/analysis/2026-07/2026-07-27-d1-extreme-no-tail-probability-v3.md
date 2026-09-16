# D-1 Extreme NO Tail Probability v3

> 2026-07-27；research replay；zero notional；不改 live。

## 数据快照

- 数据源：`runtime/weather.db` 的 canonical Tmax v2 ladder / PIT forecast / `settlement_outcomes`；DB mtime UTC `2026-07-27T11:17:42.537391663+00:00`。
- canonical state：84,874 rows，target_date 2026-07-04..2026-07-23。
- 本研究 OOF 记录：109 个 settled executable baskets；unsettled=0，missing_bracket=0。
- 7/4–8 定向补入的旧 forecast 中，有 17,249 条缺可靠 `available_at_utc`，全部排除，不作为 PIT 训练。

## 结论

把 point forecast 改成了真正的二元概率问题：用先前 target dates 的 forecast-to-outer-bracket geometry 估计 `P(low hit)+P(high hit)`，逐日 expanding OOF；再在完全相同的 direct-NO-ask basket 上检验概率和交易。

### 概率层

| policy | rows | dates | observed tail | forecast p | market p | logloss Δ vs market (95% CI) | Brier Δ vs market (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | 59 | 4 | +0.00% | +0.96% | +0.75% | +0.00251 [-0.00190, +0.00609] | -0.00011 [-0.00048, +0.00010] |
| D-1_18_24_first | 50 | 6 | +2.00% | +2.27% | +1.13% | +0.00002 [-0.01364, +0.00814] | -0.00012 [-0.00051, +0.00017] |

负 delta 才表示 forecast 模型优于同 rows 的 market tail probability。

### 交易层

| policy | selector | baskets | dates | tail hit (exact 95% CI) | break-even tail | ROI (95% CI) | excess vs market (95% CI) |
|---|---|---:|---:|---:|---:|---:|---:|
| D-1_12_18_first | forecast_ev_buffer_0bp | 2 | 2 | +0.00% [+0.00%, +84.19%] | +5.77% | +2.97% [n/a, n/a] | +3.33% [n/a, n/a] |
| D-1_12_18_first | forecast_ev_buffer_50bp | 1 | 1 | +0.00% [+0.00%, +97.50%] | +9.65% | +5.07% [n/a, n/a] | +5.70% [n/a, n/a] |
| D-1_12_18_first | forecast_ev_buffer_100bp | 1 | 1 | +0.00% [+0.00%, +97.50%] | +9.65% | +5.07% [n/a, n/a] | +5.70% [n/a, n/a] |
| D-1_18_24_first | forecast_ev_buffer_0bp | 2 | 2 | +0.00% [+0.00%, +84.19%] | +9.65% | +5.07% [n/a, n/a] | +5.45% [n/a, n/a] |
| D-1_18_24_first | forecast_ev_buffer_50bp | 2 | 2 | +0.00% [+0.00%, +84.19%] | +9.65% | +5.07% [n/a, n/a] | +5.45% [n/a, n/a] |
| D-1_18_24_first | forecast_ev_buffer_100bp | 2 | 2 | +0.00% [+0.00%, +84.19%] | +9.65% | +5.07% [n/a, n/a] | +5.45% [n/a, n/a] |

主规则固定为 50bp probability safety buffer，共选择 3 个 policy-basket，但只有 2 个独立 city-date。裁决：`inconclusive`。

## Signal / Evidence Funnel

Signal funnel（city-date-policy / basket）:

- PIT forecast mechanism states：794。
- prior-date expanding OOF scored：109 baskets / 6 dates。
- 50bp primary selected：3 policy-baskets / 2 unique city-dates。

Evidence funnel（basket）:

- complete PIT settled ladder snapshots：84,874。
- v1 executable D-1 baskets：646。
- PIT forecast-ready executable：227。
- expanding OOF + same-row market baseline：109。
- actual fill：0（research replay；不冒充 paper/live fill）。

## Gate 与边界

- significance：FAIL；交易 ROI / excess 按 target_date block bootstrap。
- baseline：FAIL；概率层用同 rows market tail proper score。
- forward：NA；虽然每行预测只用更早 target dates，且要求至少 5 个独立训练日，但规则是本轮事后提出，不是 frozen forward。
- conclusion：`inconclusive`。

8 环：覆盖概率判别/校准、统计推断、PIT 盘口、fee-adjusted execution replay、date correlation 与 market baseline；没有真实 fill、容量和 frozen forward。

Artifacts:

- `scripts/analysis/market_structure_edge/research_d1_extreme_no_tail_probability_v3.py`
- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/summary.json`
- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/probability_summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/trade_summary.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/training_audit.csv`
- `docs/analysis/2026-07/generated/d1_extreme_no_tail_probability_v3/oof_scored_baskets.csv`
