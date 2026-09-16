# Forecast Quality Filter Range RV Overlay

> generated_at_utc: `2026-06-09T17:34:52.815087+00:00`
> target_metric: `forecast_quality_filter_range_rv_proxy_value`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed; no live action.

## 数据快照

- fact_signal_candidates rows: `25117`
- fact_trades rows: `5473`
- decision_sets: `270`
- range rows: `526`
- train: `2026-05-06` -> `2026-05-28` (21 dates)
- holdout: `2026-05-29` -> `2026-06-07` (9 dates)

## 大白话结论

- 如果只看这个 decision-price proxy，Range RV 本身是赚钱的：不加过滤的 adjacent2 在 holdout 大约 `+22.4%`，adjacent3 大约 `+26.9%`。
- 但这不是 live/executable 结论，只是用 fact 表里的 `market_yes_price` 做的历史 counterfactual。
- forecast quality 过滤没有稳定证明“比不筛更赚钱”。adjacent2 严格过滤从 `65` 行砍到 `12` 行，ROI 没明显变好。
- adjacent3 的中等过滤看起来有帮助：holdout ROI 从 `+26.9%` 到 `+33.5%`，但样本只剩 `20` 行，不能当定论。
- 所以目前最像真的东西是：Range RV 的核心机会可能在“买模型 mode 附近的相邻温度区间”，forecast quality 更适合做软分层，不适合做硬开关。

## ROI 对比

ROI 是用 decision-time `market_yes_price` 做的相邻区间 counterfactual：买入区间内所有 YES，若最终温度落入区间则 payout=1，否则 payout=0。不是 live PnL。

| width | cost_cap | filter | train_rows | train_roi | train_ci | holdout_rows | holdout_roi | holdout_ci | holdout_delta | holdout_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 0.7 | loose_drop_worst_tail | 115 | +23.4% | [+7.1%, +38.1%] | 49 | +21.4% | [-40.1%, +38.4%] | +0.5% | +5.46 |
| 2 | 0.7 | medium_quality | 89 | +25.8% | [+9.7%, +42.3%] | 38 | +23.7% | [-39.1%, +42.4%] | +2.9% | +4.80 |
| 2 | 0.7 | no_filter | 153 | +20.9% | [+7.2%, +33.9%] | 61 | +20.9% | [-40.1%, +35.9%] | +0.0% | +6.04 |
| 2 | 0.7 | strict_low_uncertainty | 29 | +13.7% | [-17.5%, +46.6%] | 10 | +17.5% | [-100.0%, +81.2%] | -3.4% | +0.89 |
| 2 | 0.75 | loose_drop_worst_tail | 127 | +24.0% | [+10.6%, +37.7%] | 53 | +23.0% | [-23.2%, +38.4%] | +0.6% | +6.54 |
| 2 | 0.75 | medium_quality | 101 | +26.1% | [+13.6%, +40.1%] | 41 | +24.9% | [-26.1%, +42.0%] | +2.6% | +5.59 |
| 2 | 0.75 | no_filter | 165 | +21.7% | [+9.6%, +33.7%] | 65 | +22.4% | [-23.2%, +36.0%] | +0.0% | +7.13 |
| 2 | 0.75 | strict_low_uncertainty | 31 | +16.1% | [-11.7%, +45.4%] | 12 | +21.9% | [-70.3%, +69.0%] | -0.4% | +1.44 |
| 2 | 0.8 | loose_drop_worst_tail | 142 | +20.7% | [+8.1%, +33.7%] | 54 | +23.2% | [-23.2%, +38.2%] | +0.6% | +6.78 |
| 2 | 0.8 | medium_quality | 113 | +21.6% | [+9.9%, +34.2%] | 42 | +25.1% | [-26.1%, +41.7%] | +2.6% | +5.83 |
| 2 | 0.8 | no_filter | 183 | +18.3% | [+7.2%, +30.3%] | 66 | +22.6% | [-23.2%, +35.9%] | +0.0% | +7.36 |
| 2 | 0.8 | strict_low_uncertainty | 33 | +17.1% | [-11.3%, +45.3%] | 13 | +22.8% | [-70.3%, +61.6%] | +0.3% | +1.67 |
| 2 | 0.85 | loose_drop_worst_tail | 148 | +20.8% | [+8.9%, +32.9%] | 55 | +23.2% | [-23.2%, +37.5%] | +0.6% | +6.97 |
| 2 | 0.85 | medium_quality | 119 | +21.6% | [+10.7%, +33.4%] | 43 | +25.1% | [-26.1%, +40.6%] | +2.5% | +6.02 |
| 2 | 0.85 | no_filter | 190 | +18.6% | [+7.9%, +29.6%] | 67 | +22.6% | [-23.2%, +35.6%] | +0.0% | +7.56 |
| 2 | 0.85 | strict_low_uncertainty | 34 | +17.3% | [-9.6%, +44.7%] | 14 | +23.0% | [-70.3%, +61.1%] | +0.4% | +1.87 |
| 3 | 0.7 | loose_drop_worst_tail | 42 | +39.5% | [+24.2%, +52.7%] | 14 | +20.7% | [-19.3%, +53.3%] | -2.8% | +1.72 |
| 3 | 0.7 | medium_quality | 32 | +31.9% | [+15.4%, +48.4%] | 11 | +37.5% | [-4.3%, +75.8%] | +13.9% | +2.45 |
| 3 | 0.7 | no_filter | 77 | +32.2% | [+19.3%, +42.1%] | 24 | +23.5% | [-11.8%, +54.4%] | +0.0% | +2.86 |
| 3 | 0.7 | strict_low_uncertainty | 4 | +14.3% | [-64.0%, +67.4%] | 3 | +62.7% | [+45.0%, +73.3%] | +39.2% | +1.16 |
| 3 | 0.75 | loose_drop_worst_tail | 58 | +35.6% | [+24.5%, +46.1%] | 20 | +26.6% | [+2.0%, +48.1%] | -0.7% | +3.37 |
| 3 | 0.75 | medium_quality | 43 | +29.2% | [+11.2%, +43.1%] | 14 | +37.8% | [+4.2%, +64.4%] | +10.5% | +3.29 |
| 3 | 0.75 | no_filter | 95 | +29.3% | [+22.1%, +35.8%] | 30 | +27.3% | [+2.1%, +50.1%] | +0.0% | +4.51 |
| 3 | 0.75 | strict_low_uncertainty | 6 | +22.1% | [-28.4%, +55.8%] | 3 | +62.7% | [+45.0%, +73.3%] | +35.4% | +1.16 |
| 3 | 0.8 | loose_drop_worst_tail | 69 | +34.2% | [+25.4%, +42.6%] | 23 | +27.2% | [+8.1%, +48.1%] | -0.4% | +4.06 |
| 3 | 0.8 | medium_quality | 53 | +29.0% | [+15.4%, +39.2%] | 17 | +36.2% | [+12.0%, +64.4%] | +8.7% | +3.99 |
| 3 | 0.8 | no_filter | 106 | +29.2% | [+23.3%, +34.8%] | 35 | +27.6% | [+8.5%, +48.1%] | +0.0% | +5.62 |
| 3 | 0.8 | strict_low_uncertainty | 11 | +24.9% | [-4.1%, +44.3%] | 5 | +48.7% | [+35.8%, +73.3%] | +21.1% | +1.64 |
| 3 | 0.85 | loose_drop_worst_tail | 88 | +30.6% | [+23.8%, +36.6%] | 26 | +26.4% | [+10.8%, +44.7%] | -0.5% | +4.59 |
| 3 | 0.85 | medium_quality | 68 | +26.7% | [+16.9%, +33.9%] | 20 | +33.5% | [+14.6%, +57.4%] | +6.6% | +4.52 |
| 3 | 0.85 | no_filter | 126 | +27.4% | [+22.5%, +32.0%] | 38 | +26.9% | [+10.5%, +45.6%] | +0.0% | +6.15 |
| 3 | 0.85 | strict_low_uncertainty | 16 | +23.2% | [+3.4%, +35.4%] | 6 | +42.4% | [+35.8%, +49.7%] | +15.5% | +1.79 |

## Filters

- `no_filter`: 不用 forecast quality 过滤。
- `loose_drop_worst_tail`: 只去掉模型自己也认为尾部风险最高的 25%。
- `medium_quality`: 要求模型尾部风险低于中位数，且 adjacent3 模型质量高于中位数。
- `strict_low_uncertainty`: 低 entropy、高 mode probability、高 adjacent3 mass、历史 miss 不高，最接近上一版 strict regime。

## 结论等级

`proxy_significance=mixed_positive`, `executable=NA`, `baseline=not_full_gate`, `forward=proxy_only`, `conclusion=inconclusive` for live action. No live action.
