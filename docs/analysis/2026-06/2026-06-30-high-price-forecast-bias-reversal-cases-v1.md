# High-Price Forecast-Bias Reversal Cases v1

Generated: 2026-06-30

## Verdict

这版修正研究目标：不是设计 selector，而是找 `ask>=0.70` 的高价 YES/NO 历史反转模式。结论是 `inconclusive / case-mining-only`：高价 YES 反转率 +10.9%，高价 NO 反转率 +9.0%；已经能定位若干容易反转的 regime，但还没形成可交易规则。

最重要的机制信号：

- 高价 current_high YES 失败，通常不是 forecast-bias 标签本身，而是高置信盘口与 forecast/obs/regime 冲突：看起来像高点已定，但后续仍有热量、观测刷新或 bracket exact-hit 风险。
- 高价 NO 失败分两类：current-bracket NO 是 capped/hold 住当前档；d1/d2 NO 是最终正好命中那一档。它们不是同一个风险，不能混成一个 NO reversal。
- 历史 station-vs-forecast bias 有解释力，但不能单独决定交易；真正该找的是“盘口高置信 + forecast/obs/regime 冲突”的组合。

significance=NA baseline=NA forward=NA conclusion=inconclusive

## 数据快照

- 数据源：`runtime/weather.db` 自检 + generated expression matrix；本报告不是 live_real fill PnL。
- 数据快照时间：DB mtime `2026-06-30T11:10:46.438186169+00:00`, fact_built_at_utc `2026-06-30T11:10:16.721495+00:00`。
- 记录行数：fact_trades=4411, fact_signal_candidates=41047, expression rows=12606, high-price rows=7612。
- unsettled 占比：fact_trades NULL/unsettled-like=151 / 4411。
- missing_bracket 数：settlement_status_counts={'NULL': 151, 'settled': 4260}; CLOB gate_pass=True。

## Definition

- Row grain：city-hour decision state；同一 city-date 可连续多个小时出现同一高价 token，所以它是“反转状态”库，不是 city-date 去重交易回测。
- 高价 token：`ask >= 0.70`，并另列 `0.80/0.90` stress。
- 反转：买这个高价 token 会输，即 `payoff=0`。
- Opposite payoff：同一 snapshot 下买反面 cheap token 的结果。`current_high_yes` / `current_bracket_no` 用 matrix 里的 observed opposite ask；`d1/d2 YES` 暂无真实 ask，用 `1 - NO bid` proxy，只作诊断。

## High-Price Reversal Summary

| threshold | expression | side | rows | dates | cities | reversal | avg ask | buy high token ROI | CI low | CI high | buy opposite ROI | opp CI low | opp CI high | max token day loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| >=0.70 | current_bracket_no | NO | 270 | 35 | 33 | +47.0% | 0.79 | -31.5% | -43.1% | -18.6% | +0.0% | -19.1% | +18.5% | $-69.06 |
| >=0.70 | d1_no | NO | 2308 | 36 | 36 | +12.7% | 0.88 | -0.4% | -3.3% | +2.4% | -34.9% | -50.0% | -19.5% | $-87.27 |
| >=0.70 | current_high_yes | YES | 2054 | 36 | 36 | +10.9% | 0.87 | +2.5% | -0.5% | +5.4% | -43.9% | -58.6% | -29.2% | $-50.69 |
| >=0.70 | d2_no | NO | 2980 | 36 | 36 | +2.6% | 0.97 | +0.8% | -0.3% | +1.8% | -72.2% | -85.3% | -57.6% | $-29.03 |
| >=0.80 | current_bracket_no | NO | 102 | 23 | 22 | +51.0% | 0.87 | -42.4% | -61.7% | -19.7% | -7.3% | -39.4% | +24.7% | $-55.00 |
| >=0.80 | d1_no | NO | 1815 | 36 | 36 | +9.2% | 0.91 | -0.4% | -3.6% | +2.5% | -36.8% | -55.8% | -16.9% | $-90.26 |
| >=0.80 | current_high_yes | YES | 1648 | 35 | 36 | +8.9% | 0.90 | +1.0% | -2.2% | +4.0% | -42.8% | -59.2% | -25.7% | $-56.72 |
| >=0.80 | d2_no | NO | 2941 | 36 | 36 | +2.4% | 0.97 | +0.6% | -0.4% | +1.6% | -72.3% | -85.6% | -57.2% | $-31.72 |
| >=0.90 | current_bracket_no | NO | 35 | 6 | 9 | +80.0% | 0.95 | -78.6% | -94.2% | -57.5% | +26.1% | -8.8% | +51.0% | $-55.00 |
| >=0.90 | d1_no | NO | 1184 | 36 | 36 | +6.7% | 0.95 | -1.4% | -3.8% | +1.0% | -33.6% | -58.6% | -7.8% | $-31.05 |
| >=0.90 | current_high_yes | YES | 986 | 35 | 36 | +5.0% | 0.94 | +1.3% | -0.9% | +3.3% | -50.1% | -70.0% | -29.7% | $-17.04 |
| >=0.90 | d2_no | NO | 2732 | 36 | 36 | +1.6% | 0.98 | +0.6% | -0.4% | +1.4% | -75.1% | -88.6% | -60.2% | $-32.29 |

## High-Price YES Reversal Patterns

| expression | bias regime | day regime | intraday | rows | dates | cities | reversal | avg ask | avg fcst error | avg fcst gap | opposite ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_high_yes | cold_overforecast_noisy | day_marginal_runway | active_warming | 23 | 12 | 5 | +43.5% | 0.80 | 0.52 | 1.31 | +33.4% |
| current_high_yes | cold_overforecast_noisy | day_forecast_busted | active_warming | 40 | 11 | 5 | +27.5% | 0.84 | -1.61 | -1.34 | +26.2% |
| current_high_yes | hot_underforecast_noisy | day_forecast_busted | plateau_near_high | 29 | 7 | 3 | +24.1% | 0.88 | -2.23 | -1.95 | +68.7% |
| current_high_yes | hot_underforecast_clean | day_forecast_busted | plateau_near_high | 62 | 17 | 9 | +19.4% | 0.89 | -1.54 | -1.40 | +30.4% |
| current_high_yes | hot_underforecast_clean | day_forecast_busted | active_warming | 144 | 23 | 13 | +16.0% | 0.87 | -2.15 | -1.89 | -29.0% |
| current_high_yes | hot_underforecast_clean | day_forecast_busted | mature_fade | 44 | 11 | 7 | +15.9% | 0.92 | -1.75 | -1.52 | +68.8% |
| current_high_yes | cold_overforecast_noisy | day_marginal_runway | fresh_high | 32 | 13 | 7 | +15.6% | 0.89 | 0.93 | 1.19 | -29.5% |
| current_high_yes | hot_underforecast_clean | day_forecast_capped | fresh_high | 61 | 20 | 11 | +14.8% | 0.86 | -0.40 | -0.18 | +97.3% |
| current_high_yes | mild_or_mixed | day_forecast_capped | active_warming | 28 | 10 | 4 | +14.3% | 0.82 | -0.36 | -0.07 | -54.5% |
| current_high_yes | mild_or_mixed | day_marginal_runway | fresh_high | 30 | 12 | 4 | +13.3% | 0.85 | 0.61 | 0.76 | -4.8% |
| current_high_yes | hot_underforecast_clean | day_forecast_capped | active_warming | 106 | 22 | 11 | +13.2% | 0.86 | -0.24 | -0.08 | -48.5% |
| current_high_yes | hot_underforecast_noisy | day_forecast_capped | active_warming | 26 | 9 | 5 | +11.5% | 0.85 | -0.16 | -0.03 | -58.8% |
| current_high_yes | balanced_tight | day_forecast_capped | plateau_near_high | 28 | 8 | 4 | +10.7% | 0.85 | -0.07 | -0.02 | -73.9% |
| current_high_yes | balanced_tight | day_forecast_capped | fresh_high | 40 | 9 | 4 | +10.0% | 0.90 | -0.37 | -0.18 | -65.5% |
| current_high_yes | cold_overforecast_noisy | day_forecast_capped | active_warming | 61 | 15 | 5 | +9.8% | 0.89 | -0.02 | 0.06 | -19.6% |
| current_high_yes | mild_or_mixed | day_forecast_busted | active_warming | 61 | 15 | 6 | +9.8% | 0.86 | -1.81 | -1.65 | -55.4% |
| current_high_yes | hot_underforecast_noisy | day_forecast_capped | fresh_high | 21 | 6 | 4 | +9.5% | 0.86 | -0.34 | -0.23 | -69.4% |
| current_high_yes | cold_overforecast_clean | day_forecast_capped | pullback_uncertain | 22 | 7 | 2 | +9.1% | 0.84 | 0.14 | 0.23 | -54.5% |
| current_high_yes | mild_or_mixed | day_forecast_capped | plateau_near_high | 24 | 9 | 5 | +8.3% | 0.87 | -0.04 | 0.09 | -72.2% |
| current_high_yes | cold_overforecast_clean | day_forecast_capped | active_warming | 46 | 10 | 2 | +6.5% | 0.87 | -0.12 | -0.05 | -83.7% |
| current_high_yes | hot_underforecast_clean | day_forecast_busted | fresh_high | 78 | 18 | 10 | +2.6% | 0.88 | -1.47 | -1.44 | -95.6% |
| current_high_yes | cold_overforecast_noisy | day_forecast_capped | fresh_high | 42 | 13 | 5 | +2.4% | 0.89 | 0.16 | 0.23 | -66.0% |
| current_high_yes | balanced_tight | day_forecast_capped | active_warming | 58 | 16 | 3 | +1.7% | 0.88 | -0.46 | -0.42 | -95.5% |
| current_high_yes | hot_underforecast_clean | day_forecast_busted | pullback_uncertain | 43 | 11 | 10 | +0.0% | 0.92 | -1.68 | -1.68 | -100.0% |
| current_high_yes | hot_underforecast_clean | day_forecast_capped | mature_fade | 35 | 10 | 8 | +0.0% | 0.88 | -0.25 | -0.25 | -100.0% |
| current_high_yes | hot_underforecast_noisy | day_forecast_busted | active_warming | 29 | 8 | 4 | +0.0% | 0.88 | -1.51 | -1.51 | -100.0% |
| current_high_yes | hot_underforecast_noisy | day_forecast_busted | fresh_high | 21 | 8 | 3 | +0.0% | 0.90 | -2.00 | -2.00 | -100.0% |

_Small-sample 100% reversal clusters are kept in CSV but hidden from the main table unless rows >= 20._

## High-Price NO Reversal Patterns

| expression | bias regime | day regime | intraday | rows | dates | cities | reversal | avg ask | avg fcst error | avg fcst gap | opposite ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | hot_underforecast_clean | day_forecast_capped | active_warming | 32 | 12 | 7 | +75.0% | 0.79 | -0.22 | 0.13 | +52.8% |
| d1_no | hot_underforecast_clean | day_marginal_runway | fresh_high | 20 | 8 | 5 | +45.0% | 0.77 | 0.70 | 1.18 | +2.6% |
| d1_no | cold_overforecast_noisy | day_marginal_runway | active_warming | 29 | 13 | 6 | +34.5% | 0.81 | 0.61 | 1.23 | +20.4% |
| current_bracket_no | hot_underforecast_clean | day_forecast_busted | active_warming | 23 | 7 | 4 | +30.4% | 0.79 | -2.68 | -1.70 | -26.0% |
| d1_no | cold_overforecast_noisy | day_forecast_busted | active_warming | 44 | 12 | 5 | +25.0% | 0.84 | -1.54 | -1.30 | +32.5% |
| d1_no | hot_underforecast_clean | day_forecast_capped | false_fade_risk | 25 | 8 | 4 | +24.0% | 0.86 | -0.01 | 0.12 | -23.3% |
| d1_no | cold_overforecast_noisy | day_marginal_runway | fresh_high | 38 | 14 | 7 | +21.1% | 0.87 | 0.84 | 1.15 | -9.6% |
| d1_no | balanced_tight | day_forecast_capped | plateau_near_high | 31 | 9 | 4 | +19.4% | 0.85 | -0.19 | -0.02 | -29.9% |
| d1_no | hot_underforecast_clean | day_forecast_busted | active_warming | 161 | 26 | 13 | +19.3% | 0.87 | -2.22 | -1.89 | -21.5% |
| d1_no | hot_underforecast_clean | day_forecast_busted | plateau_near_high | 63 | 17 | 9 | +19.0% | 0.92 | -1.54 | -1.40 | +88.6% |
| d1_no | hot_underforecast_clean | day_forecast_capped | active_warming | 129 | 25 | 11 | +17.8% | 0.87 | -0.38 | -0.09 | -35.9% |
| d1_no | hot_underforecast_noisy | day_forecast_busted | plateau_near_high | 36 | 8 | 3 | +16.7% | 0.87 | -2.30 | -2.06 | +70.5% |
| d1_no | hot_underforecast_clean | day_forecast_busted | mature_fade | 44 | 11 | 7 | +15.9% | 0.93 | -1.75 | -1.52 | +68.8% |
| d1_no | mild_or_mixed | day_forecast_busted | active_warming | 72 | 16 | 6 | +13.9% | 0.87 | -1.72 | -1.51 | -58.5% |
| d1_no | hot_underforecast_clean | day_forecast_capped | fresh_high | 67 | 21 | 11 | +13.4% | 0.87 | -0.40 | -0.21 | +92.7% |
| d1_no | mild_or_mixed | day_marginal_runway | fresh_high | 33 | 12 | 4 | +12.1% | 0.86 | 0.65 | 0.79 | +25.0% |
| d1_no | cold_overforecast_noisy | day_forecast_capped | active_warming | 75 | 17 | 5 | +12.0% | 0.87 | -0.07 | 0.12 | +31.8% |
| d1_no | mild_or_mixed | day_forecast_capped | active_warming | 34 | 11 | 4 | +11.8% | 0.82 | -0.37 | -0.14 | -61.2% |
| d1_no | balanced_tight | day_forecast_capped | fresh_high | 40 | 9 | 4 | +10.0% | 0.91 | -0.37 | -0.18 | -60.8% |
| d1_no | hot_underforecast_clean | day_forecast_capped | pullback_uncertain | 20 | 6 | 4 | +10.0% | 0.88 | -0.03 | 0.07 | -76.2% |
| d1_no | hot_underforecast_noisy | day_forecast_capped | fresh_high | 21 | 6 | 4 | +9.5% | 0.88 | -0.34 | -0.23 | -57.6% |
| d2_no | hot_underforecast_clean | day_forecast_busted | plateau_near_high | 65 | 16 | 9 | +9.2% | 0.98 | -1.57 | -1.29 | +44.5% |
| d1_no | cold_overforecast_clean | day_forecast_capped | pullback_uncertain | 22 | 7 | 2 | +9.1% | 0.89 | 0.14 | 0.23 | -56.7% |
| d1_no | hot_underforecast_clean | day_forecast_busted | false_fade_risk | 22 | 8 | 8 | +9.1% | 0.88 | -1.79 | -1.71 | +23.2% |
| d1_no | hot_underforecast_noisy | day_forecast_busted | active_warming | 35 | 9 | 4 | +8.6% | 0.88 | -1.52 | -1.43 | -81.4% |
| d1_no | hot_underforecast_noisy | day_forecast_capped | active_warming | 25 | 9 | 4 | +8.0% | 0.87 | -0.13 | -0.04 | -79.5% |
| d2_no | hot_underforecast_clean | day_marginal_runway | fresh_high | 39 | 10 | 8 | +7.7% | 0.92 | 0.67 | 1.09 | -66.6% |
| d1_no | mild_or_mixed | day_forecast_capped | plateau_near_high | 28 | 9 | 5 | +7.1% | 0.86 | 0.01 | 0.13 | -76.2% |
| d1_no | cold_overforecast_clean | day_forecast_capped | active_warming | 49 | 10 | 2 | +6.1% | 0.88 | -0.07 | -0.01 | -84.3% |
| d2_no | mild_or_mixed | day_marginal_runway | fresh_high | 54 | 17 | 5 | +5.6% | 0.96 | 0.47 | 0.90 | -73.7% |
| d2_no | hot_underforecast_noisy | day_forecast_capped | active_warming | 41 | 17 | 5 | +4.9% | 0.96 | -0.41 | -0.04 | -54.4% |
| d1_no | hot_underforecast_clean | day_marginal_runway | mature_fade | 21 | 6 | 5 | +4.8% | 0.79 | 0.93 | 0.98 | -81.7% |
| d1_no | hot_underforecast_noisy | day_forecast_busted | fresh_high | 22 | 9 | 3 | +4.5% | 0.90 | -2.12 | -2.07 | -85.8% |
| d2_no | hot_underforecast_noisy | day_forecast_busted | active_warming | 52 | 13 | 4 | +3.8% | 0.96 | -1.79 | -1.54 | -66.4% |
| d2_no | mild_or_mixed | day_marginal_runway | active_warming | 28 | 9 | 6 | +3.6% | 0.97 | 0.57 | 1.03 | -88.8% |

_Small-sample 100% reversal clusters are kept in CSV but hidden from the main table unless rows >= 20._

## Forecast Conflict Bins

| expression | actual - forecast | forecast - running | rows | dates | reversal | avg ask | opposite ROI | high token ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | 0..1 | 0..1 | 38 | 13 | +100.0% | 0.80 | +102.6% | -100.0% |
| d1_no | -3..-2 | -1..0 | 24 | 6 | +100.0% | 0.85 | +296.0% | -100.0% |
| current_high_yes | -3..-2 | -1..0 | 22 | 6 | +100.0% | 0.83 | +281.5% | -100.0% |
| current_high_yes | -1..0 | 1..2 | 19 | 4 | +100.0% | 0.83 | +334.9% | -100.0% |
| d1_no | -1..0 | 1..2 | 19 | 4 | +100.0% | 0.87 | +458.2% | -100.0% |
| current_bracket_no | -2..-1 | -2..-1 | 12 | 4 | +100.0% | 0.82 | +112.8% | -100.0% |
| current_high_yes | -2..-1 | 0..1 | 9 | 5 | +100.0% | 0.77 | +239.8% | -100.0% |
| current_bracket_no | 1..2 | 1..2 | 8 | 5 | +100.0% | 0.79 | +154.9% | -100.0% |
| current_bracket_no | 2..3 | 2..3 | 6 | 4 | +100.0% | 0.76 | +148.4% | -100.0% |
| d2_no | -2..-1 | 2..3 | 6 | 2 | +100.0% | 0.87 | +385.1% | -100.0% |
| d2_no | -3..-2 | 0..1 | 5 | 2 | +100.0% | 0.93 | +2183.3% | -100.0% |
| current_bracket_no | -3..-2 | <=-2 | 4 | 2 | +100.0% | 0.86 | +16.3% | -100.0% |
| d1_no | <=-3 | -1..0 | 3 | 1 | +100.0% | 0.72 | +222.6% | -100.0% |
| current_high_yes | -1..0 | 2..3 | 1 | 1 | +100.0% | 0.78 | +244.8% | -100.0% |
| d1_no | -2..-1 | 0..1 | 19 | 8 | +94.7% | 0.79 | +193.3% | -94.0% |
| d1_no | 0..1 | 1..2 | 36 | 12 | +94.4% | 0.81 | +244.9% | -94.4% |
| current_bracket_no | >=3 | >=3 | 15 | 6 | +93.3% | 0.81 | +102.1% | -92.3% |
| current_bracket_no | -1..0 | -1..0 | 33 | 10 | +90.9% | 0.76 | +101.6% | -87.4% |
| current_high_yes | 0..1 | 1..2 | 21 | 9 | +90.5% | 0.79 | +259.0% | -86.4% |
| d1_no | 1..2 | 2..3 | 7 | 4 | +85.7% | 0.76 | +127.7% | -83.8% |
| d1_no | -2..-1 | -1..0 | 48 | 15 | +83.3% | 0.85 | +464.1% | -81.0% |
| current_high_yes | -2..-1 | -1..0 | 41 | 14 | +80.5% | 0.85 | +436.4% | -77.8% |
| current_high_yes | -1..0 | 0..1 | 68 | 20 | +73.5% | 0.84 | +245.6% | -70.5% |
| d1_no | -1..0 | 0..1 | 99 | 22 | +72.7% | 0.82 | +208.7% | -67.9% |
| d1_no | -3..-2 | -2..-1 | 32 | 8 | +71.9% | 0.87 | +559.4% | -65.6% |
| current_high_yes | -3..-2 | -2..-1 | 28 | 7 | +71.4% | 0.85 | +447.0% | -63.9% |
| d2_no | <=-3 | -2..-1 | 9 | 4 | +66.7% | 0.91 | +582.1% | -64.2% |
| d2_no | 0..1 | >=3 | 3 | 2 | +66.7% | 0.88 | +33.3% | -61.7% |
| d2_no | -1..0 | 2..3 | 11 | 4 | +63.6% | 0.90 | +267.9% | -59.3% |
| current_bracket_no | nan | nan | 8 | 3 | +62.5% | 0.78 | +49.4% | -54.8% |
| d1_no | <=-3 | -2..-1 | 5 | 2 | +60.0% | 0.73 | +27.7% | -48.3% |
| d2_no | 0..1 | 2..3 | 8 | 4 | +50.0% | 0.91 | +69.6% | -49.8% |
| d2_no | <=-3 | -1..0 | 6 | 2 | +50.0% | 0.96 | +880.4% | -47.9% |
| current_bracket_no | <=-3 | <=-2 | 12 | 3 | +33.3% | 0.81 | -20.6% | -11.9% |
| d1_no | -1..0 | 2..3 | 3 | 2 | +33.3% | 0.77 | +23.5% | -14.5% |
| d1_no | <=-3 | <=-2 | 82 | 17 | +32.9% | 0.88 | +48.4% | -26.2% |
| current_high_yes | <=-3 | <=-2 | 82 | 17 | +29.3% | 0.89 | +47.0% | -21.3% |
| d2_no | -2..-1 | 0..1 | 51 | 14 | +23.5% | 0.93 | +42.4% | -18.8% |
| current_bracket_no | -3..-2 | -2..-1 | 14 | 5 | +21.4% | 0.81 | -71.8% | +1.1% |
| current_high_yes | >=3 | >=3 | 32 | 9 | +18.8% | 0.87 | -16.8% | -5.4% |
| d2_no | -3..-2 | -1..0 | 34 | 9 | +17.6% | 0.96 | +39.1% | -15.3% |
| d2_no | -1..0 | 1..2 | 48 | 13 | +10.4% | 0.96 | +44.8% | -6.6% |
| d2_no | >=3 | >=3 | 63 | 14 | +9.5% | 0.94 | +79.9% | -2.4% |
| current_bracket_no | 0..1 | 1..2 | 27 | 13 | +7.4% | 0.79 | -89.4% | +20.8% |
| d2_no | -1..0 | 0..1 | 176 | 29 | +6.8% | 0.95 | -31.2% | -1.5% |

## Moisture / Wind Patterns

| expression | moisture/cloud | wind | rows | dates | cities | reversal | avg ask | opposite ROI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | humid_overcast_suppression | moderate_wind | 4 | 2 | 1 | +100.0% | 0.75 | +85.1% |
| current_bracket_no | humid_overcast_suppression | light_wind | 1 | 1 | 1 | +100.0% | 0.76 | +185.7% |
| current_bracket_no | dry_heat_inertia | windy_mixing_noise | 8 | 2 | 2 | +62.5% | 0.76 | +22.5% |
| current_bracket_no | humid_convective_risk | moderate_wind | 8 | 3 | 3 | +62.5% | 0.75 | +62.4% |
| current_bracket_no | cloud_suppression | light_wind | 14 | 6 | 4 | +57.1% | 0.79 | +11.6% |
| current_bracket_no | mixed_moisture | windy_mixing_noise | 10 | 3 | 4 | +50.0% | 0.88 | -24.7% |
| current_high_yes | moisture_cloud_unknown | wind_unknown | 8 | 2 | 2 | +50.0% | 0.91 | +113.7% |
| d2_no | moisture_cloud_unknown | wind_unknown | 8 | 2 | 2 | +50.0% | 0.99 | +900.0% |
| current_bracket_no | mixed_moisture | light_wind | 94 | 26 | 24 | +48.9% | 0.78 | +3.6% |
| current_bracket_no | dry_heat_inertia | moderate_wind | 22 | 8 | 4 | +45.5% | 0.82 | -3.6% |
| current_bracket_no | mixed_moisture | moderate_wind | 77 | 23 | 19 | +44.2% | 0.79 | -5.7% |
| current_bracket_no | humid_convective_risk | light_wind | 12 | 6 | 7 | +41.7% | 0.75 | -0.1% |
| d1_no | humid_overcast_suppression | moderate_wind | 58 | 12 | 8 | +29.3% | 0.88 | +156.0% |
| current_bracket_no | dry_heat_inertia | light_wind | 14 | 8 | 6 | +28.6% | 0.80 | -24.5% |
| d1_no | cloud_suppression | light_wind | 99 | 21 | 16 | +27.3% | 0.86 | -6.7% |
| current_high_yes | humid_overcast_suppression | moderate_wind | 43 | 10 | 6 | +25.6% | 0.87 | +149.5% |
| d1_no | dry_heat_inertia | light_wind | 97 | 23 | 18 | +21.6% | 0.88 | -15.7% |
| d1_no | humid_convective_risk | windy_mixing_noise | 16 | 4 | 2 | +18.8% | 0.85 | -49.3% |
| d1_no | dry_heat_inertia | windy_mixing_noise | 59 | 13 | 8 | +18.6% | 0.88 | +6.3% |
| current_high_yes | dry_heat_inertia | light_wind | 88 | 21 | 18 | +18.2% | 0.89 | -16.7% |
| current_high_yes | dry_heat_inertia | windy_mixing_noise | 46 | 11 | 6 | +17.4% | 0.90 | +10.4% |
| current_high_yes | cloud_suppression | light_wind | 81 | 19 | 14 | +17.3% | 0.85 | -29.2% |
| current_high_yes | dry_heat_inertia | moderate_wind | 178 | 27 | 18 | +15.7% | 0.87 | -40.0% |
| current_high_yes | cloud_suppression | moderate_wind | 79 | 16 | 13 | +15.2% | 0.87 | -47.3% |
| d1_no | dry_heat_inertia | moderate_wind | 204 | 29 | 19 | +14.7% | 0.88 | -33.8% |
| d1_no | humid_convective_risk | light_wind | 173 | 29 | 20 | +13.9% | 0.85 | -37.2% |
| current_high_yes | humid_overcast_suppression | light_wind | 67 | 15 | 12 | +13.4% | 0.87 | +8.8% |
| d1_no | humid_overcast_suppression | light_wind | 68 | 15 | 12 | +13.2% | 0.89 | +6.2% |
| d1_no | cloud_suppression | moderate_wind | 77 | 16 | 13 | +13.0% | 0.89 | -43.0% |
| d1_no | humid_convective_risk | moderate_wind | 73 | 19 | 17 | +12.3% | 0.90 | -39.1% |
| d1_no | mixed_moisture | light_wind | 512 | 36 | 31 | +10.7% | 0.89 | -36.0% |
| current_high_yes | humid_convective_risk | light_wind | 137 | 28 | 20 | +10.2% | 0.85 | -45.8% |
| d1_no | mixed_moisture | moderate_wind | 699 | 36 | 30 | +9.9% | 0.87 | -50.5% |
| current_high_yes | humid_convective_risk | moderate_wind | 71 | 19 | 16 | +9.9% | 0.89 | -55.6% |
| current_high_yes | mixed_moisture | moderate_wind | 633 | 36 | 30 | +8.8% | 0.87 | -53.5% |
| d2_no | humid_convective_risk | moderate_wind | 92 | 18 | 19 | +8.7% | 0.97 | -57.0% |
| current_high_yes | mixed_moisture | light_wind | 451 | 36 | 31 | +8.4% | 0.88 | -55.7% |
| d1_no | mixed_moisture | windy_mixing_noise | 125 | 22 | 12 | +6.4% | 0.87 | -75.9% |
| current_high_yes | mixed_moisture | windy_mixing_noise | 122 | 20 | 11 | +5.7% | 0.85 | -79.9% |
| d2_no | mixed_moisture | light_wind | 699 | 36 | 33 | +3.6% | 0.97 | -67.9% |
| d2_no | humid_convective_risk | light_wind | 235 | 32 | 20 | +3.0% | 0.94 | -72.1% |
| d2_no | dry_heat_inertia | light_wind | 146 | 31 | 21 | +2.7% | 0.97 | -88.9% |
| d2_no | dry_heat_inertia | moderate_wind | 251 | 33 | 21 | +2.4% | 0.97 | -88.1% |
| d2_no | mixed_moisture | moderate_wind | 910 | 36 | 30 | +2.3% | 0.97 | -62.7% |
| d2_no | humid_overcast_suppression | moderate_wind | 74 | 14 | 10 | +1.4% | 0.95 | -80.4% |

## City / Source Concentration

| expression | city | model | bias regime | rows | dates | reversal | avg ask | opposite ROI | avg fcst error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_bracket_no | Amsterdam | ecmwf | hot_underforecast_clean | 4 | 2 | +100.0% | 0.79 | +81.5% | -0.40 |
| current_bracket_no | NYC | gfs | cold_overforecast_noisy | 3 | 1 | +100.0% | 0.72 | +118.8% | 7.80 |
| current_bracket_no | Denver | gfs | mild_or_mixed | 2 | 1 | +100.0% | 0.97 | +42.9% | 0.40 |
| current_bracket_no | Houston | gfs | cold_overforecast_clean | 2 | 1 | +100.0% | 0.79 | +88.7% | 2.50 |
| current_bracket_no | Warsaw | gfs | balanced_tight | 2 | 2 | +100.0% | 0.74 | +125.1% | 0.42 |
| current_bracket_no | Wellington | unknown | unclassified | 2 | 1 | +100.0% | 0.71 | +156.4% |  |
| current_bracket_no | Beijing | ecmwf | hot_underforecast_clean | 1 | 1 | +100.0% | 0.77 | +171.0% | 1.78 |
| current_bracket_no | CapeTown | ecmwf | mild_or_mixed | 1 | 1 | +100.0% | 0.88 | +156.4% |  |
| current_bracket_no | Manila | unknown | unclassified | 1 | 1 | +100.0% | 0.70 | +69.5% |  |
| current_bracket_no | Warsaw | ecmwf | hot_underforecast_noisy | 1 | 1 | +100.0% | 0.72 | +177.8% | 0.80 |
| current_bracket_no | TelAviv | gfs | mild_or_mixed | 7 | 3 | +85.7% | 0.76 | +120.4% | 0.70 |
| current_bracket_no | Atlanta | gfs | hot_underforecast_clean | 4 | 2 | +75.0% | 0.75 | +64.8% | 0.15 |
| current_bracket_no | Manila | gfs | hot_underforecast_clean | 15 | 7 | +73.3% | 0.77 | +53.8% | -0.30 |
| current_bracket_no | CapeTown | gfs | cold_overforecast_noisy | 11 | 7 | +72.7% | 0.79 | +59.0% | 2.13 |
| current_bracket_no | Austin | gfs | hot_underforecast_clean | 18 | 5 | +66.7% | 0.84 | +3.3% | 0.53 |
| current_bracket_no | Chongqing | gfs | hot_underforecast_clean | 3 | 3 | +66.7% | 0.81 | +88.1% | 2.24 |
| current_bracket_no | Dallas | gfs | hot_underforecast_clean | 16 | 5 | +62.5% | 0.81 | +13.0% | -3.01 |
| d1_no | Busan | ecmwf | balanced_tight | 8 | 2 | +62.5% | 0.88 | +112.3% | 0.31 |
| current_bracket_no | Taipei | gfs | two_sided_noisy | 7 | 3 | +57.1% | 0.74 | +60.2% | -0.90 |
| current_bracket_no | Jeddah | gfs | hot_underforecast_clean | 30 | 10 | +53.3% | 0.80 | +11.7% | -0.74 |
| current_bracket_no | BuenosAires | gfs | hot_underforecast_noisy | 10 | 5 | +50.0% | 0.77 | +25.3% | -0.22 |
| current_high_yes | Busan | ecmwf | balanced_tight | 6 | 2 | +50.0% | 0.87 | +22.0% | 0.05 |
| current_bracket_no | Madrid | gfs | hot_underforecast_clean | 2 | 2 | +50.0% | 0.72 | +19.0% | -0.86 |
| current_bracket_no | Chengdu | gfs | hot_underforecast_clean | 9 | 5 | +44.4% | 0.81 | +16.3% | -0.25 |
| current_bracket_no | SanFrancisco | gfs | cold_overforecast_clean | 9 | 3 | +44.4% | 0.91 | -9.3% | 2.07 |
| current_bracket_no | Munich | gfs | hot_underforecast_noisy | 7 | 2 | +42.9% | 0.73 | +9.9% | -2.65 |
| current_bracket_no | SaoPaulo | gfs | mild_or_mixed | 7 | 3 | +42.9% | 0.75 | -8.8% | -1.13 |
| current_bracket_no | Karachi | gfs | hot_underforecast_clean | 12 | 4 | +41.7% | 0.89 | -36.9% | -1.85 |
| current_bracket_no | Istanbul | gfs | cold_overforecast_noisy | 23 | 8 | +39.1% | 0.75 | -13.5% | 0.48 |
| current_high_yes | Denver | gfs | mild_or_mixed | 31 | 12 | +35.5% | 0.88 | +81.9% | -2.05 |
| d1_no | Chengdu | gfs | hot_underforecast_clean | 43 | 15 | +34.9% | 0.88 | +30.4% | -0.63 |
| current_high_yes | Chengdu | gfs | hot_underforecast_clean | 42 | 14 | +33.3% | 0.85 | +9.6% | -0.56 |
| d1_no | Chengdu | ecmwf | hot_underforecast_clean | 3 | 2 | +33.3% | 0.81 | -24.8% | -0.93 |
| d1_no | Beijing | gfs | cold_overforecast_noisy | 14 | 6 | +28.6% | 0.84 | -18.2% | 0.09 |
| d1_no | Seattle | gfs | hot_underforecast_clean | 25 | 8 | +28.0% | 0.88 | +37.8% | -1.98 |
| d1_no | Denver | gfs | mild_or_mixed | 44 | 14 | +25.0% | 0.89 | +24.3% | -1.66 |
| current_high_yes | Warsaw | ecmwf | hot_underforecast_noisy | 4 | 3 | +25.0% | 0.91 | +25.0% | 0.16 |
| d1_no | Wuhan | gfs | mild_or_mixed | 54 | 16 | +24.1% | 0.82 | -20.2% | -0.59 |
| d1_no | Guangzhou | gfs | hot_underforecast_noisy | 60 | 18 | +23.3% | 0.87 | -4.0% | -1.70 |
| d1_no | Jeddah | gfs | hot_underforecast_clean | 112 | 25 | +23.2% | 0.85 | -11.5% | -0.75 |
| d1_no | Wuhan | ecmwf | hot_underforecast_noisy | 9 | 3 | +22.2% | 0.85 | -30.6% | -0.47 |
| d2_no | Busan | ecmwf | balanced_tight | 14 | 2 | +21.4% | 0.97 | +18.4% | -0.24 |
| current_high_yes | Guangzhou | gfs | hot_underforecast_noisy | 52 | 14 | +21.2% | 0.86 | -9.6% | -1.65 |
| current_high_yes | Wellington | gfs | hot_underforecast_clean | 83 | 19 | +20.5% | 0.89 | +101.5% | -1.28 |
| d1_no | Wellington | gfs | hot_underforecast_clean | 83 | 19 | +20.5% | 0.91 | +131.9% | -1.28 |
| current_high_yes | NYC | gfs | cold_overforecast_noisy | 69 | 23 | +20.3% | 0.88 | -25.5% | 1.44 |
| current_high_yes | Seattle | gfs | hot_underforecast_clean | 20 | 7 | +20.0% | 0.87 | +5.9% | -1.85 |
| d2_no | Wuhan | ecmwf | hot_underforecast_noisy | 15 | 3 | +20.0% | 0.94 | +53.8% | -0.51 |
| d1_no | Chongqing | gfs | hot_underforecast_clean | 75 | 19 | +17.3% | 0.87 | -25.6% | -1.09 |
| current_high_yes | CapeTown | gfs | cold_overforecast_noisy | 58 | 16 | +17.2% | 0.86 | +12.3% | 0.12 |

## Example Reversal Cases

| date | city | expression | side | ask | opp ask | winner | current | d1 | d2 | bias regime | day | intraday | moisture | wind | fcst err | fcst gap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-19 | Austin | current_bracket_no | NO | 0.99 | 0.78 | 90-91 | 90-91 | 92-93 | 94-95 | hot_underforecast_clean | day_forecast_capped | active_warming | mixed_moisture | moderate_wind | 0.70 | 0.70 |
| 2026-05-19 | SanFrancisco | current_bracket_no | NO | 0.99 | 0.49 | 80-81 | 80-81 | 82-83 | 84-85 | cold_overforecast_clean | day_open_runway | active_warming | dry_heat_inertia | moderate_wind | 3.40 | 3.40 |
| 2026-06-23 | NYC | d2_no | NO | 0.99 | 0.05 | 74-75 | 70-71 | 72-73 | 74-75 | cold_overforecast_noisy | day_open_runway | state_unknown | moisture_cloud_unknown | wind_unknown | 4.30 | 4.30 |
| 2026-06-12 | CapeTown | d1_no | NO | 0.99 | 0.04 | 18 | 17 | 18 | 19 | cold_overforecast_noisy | day_forecast_capped | active_warming | mixed_moisture | light_wind | -0.67 | -0.11 |
| 2026-05-22 | Ankara | d2_no | NO | 0.98 | 0.02 | 20 | 18 | 19 | 20 | hot_underforecast_clean | day_open_runway | active_warming | mixed_moisture | light_wind | -0.67 | 1.56 |
| 2026-05-21 | Jeddah | current_bracket_no | NO | 0.98 | 0.66 | 34 | 34 | 35 | 36 | hot_underforecast_clean | day_forecast_capped | false_fade_risk | mixed_moisture | light_wind | 0.28 | 0.28 |
| 2026-05-23 | Ankara | d1_no | NO | 0.98 | 0.06 | 19 | 18 | 19 | 20 | hot_underforecast_clean | day_forecast_capped | fresh_high | mixed_moisture | moderate_wind | -0.72 | 0.39 |
| 2026-06-16 | CapeTown | d2_no | NO | 0.98 | 0.03 | 21 | 19 | 20 | 21 | cold_overforecast_noisy | day_forecast_capped | active_warming | mixed_moisture | moderate_wind | -2.00 | 0.22 |
| 2026-06-02 | Guangzhou | d2_no | NO | 0.98 | 0.08 | 37 | 35 | 36 | 37 | hot_underforecast_noisy | day_forecast_busted | active_warming | mixed_moisture | light_wind | -3.28 | -1.06 |
| 2026-06-06 | Seattle | d2_no | NO | 0.98 | 0.06 | 62-63 | 58-59 | 60-61 | 62-63 | hot_underforecast_clean | day_forecast_busted | active_warming | mixed_moisture | moderate_wind | -4.40 | -1.40 |
| 2026-06-15 | Jeddah | d1_no | NO | 0.98 | 0.07 | 38+ | 37 | 38+ |  | hot_underforecast_clean | day_forecast_busted | active_warming | dry_heat_inertia | moderate_wind | -3.89 | -2.22 |
| 2026-06-18 | Ankara | d1_no | NO | 0.97 | 0.08 | 30 | 29 | 30 | 31 | hot_underforecast_clean | day_forecast_busted | false_fade_risk | dry_heat_inertia | light_wind | -1.72 | -0.61 |
| 2026-05-19 | Denver | current_bracket_no | NO | 0.97 | 0.70 | 48-49 | 48-49 | 50-51 | 52-53 | mild_or_mixed | day_marginal_runway | plateau_near_high | mixed_moisture | light_wind | 0.40 | 1.40 |
| 2026-05-21 | Manila | current_bracket_no | NO | 0.97 | 0.85 | 36 | 36 | 37 | 38 | hot_underforecast_clean | day_forecast_busted | flat_or_cooling | mixed_moisture | light_wind | -2.06 | -2.06 |
| 2026-05-25 | Istanbul | d2_no | NO | 0.97 | 0.06 | 23 | 21 | 22 | 23 | cold_overforecast_noisy | day_forecast_capped | flat_or_cooling | mixed_moisture | light_wind | -1.33 | 0.33 |
| 2026-05-25 | Manila | d1_no | NO | 0.97 | 0.06 | 37 | 36 | 37 | 38 | hot_underforecast_clean | day_forecast_busted | plateau_near_high | mixed_moisture | light_wind | -2.28 | -1.17 |
| 2026-06-12 | CapeTown | d2_no | NO | 0.97 | 0.04 | 18 | 16 | 17 | 18 | cold_overforecast_noisy | day_marginal_runway | plateau_near_high | mixed_moisture | moderate_wind | -0.67 | 1.00 |
| 2026-06-15 | Dallas | d2_no | NO | 0.97 | 0.05 | 86-87 | 82-83 | 84-85 | 86-87 | hot_underforecast_clean | day_forecast_capped | fresh_high | mixed_moisture | light_wind | -3.50 | -0.50 |
| 2026-06-17 | Amsterdam | d2_no | NO | 0.97 | 0.09 | 24 | 22 | 23 | 24 | balanced_tight | day_marginal_runway | mature_fade | mixed_moisture | moderate_wind | -0.72 | 0.94 |
| 2026-06-23 | NYC | d2_no | NO | 0.97 | 0.06 | 74-75 | 70-71 | 72-73 | 74-75 | cold_overforecast_noisy | day_open_runway | mature_fade | humid_convective_risk | light_wind | 4.30 | 4.30 |
| 2026-06-13 | Shanghai | d2_no | NO | 0.97 | 0.04 | 28 | 26 | 27 | 28 | hot_underforecast_clean | day_forecast_busted | plateau_near_high | mixed_moisture | moderate_wind | -2.83 | -1.17 |
| 2026-06-17 | Amsterdam | d1_no | NO | 0.96 | 0.26 | 24 | 23 | 24 | 25 | balanced_tight | day_forecast_capped | fresh_high | mixed_moisture | windy_mixing_noise | -0.72 | 0.39 |
| 2026-05-24 | LA | current_high_yes | YES | 0.96 | 0.07 | 68-69 | 66-67 | 68-69 | 70-71 | cold_overforecast_noisy | day_forecast_capped | fresh_high | mixed_moisture | moderate_wind | -0.50 | 0.50 |
| 2026-05-24 | LA | d1_no | NO | 0.96 | 0.07 | 68-69 | 66-67 | 68-69 | 70-71 | cold_overforecast_noisy | day_forecast_capped | fresh_high | mixed_moisture | moderate_wind | -0.50 | 0.50 |
| 2026-06-08 | Singapore | d1_no | NO | 0.96 | 0.07 | 33 | 32 | 33 | 34 | hot_underforecast_clean | day_forecast_busted | false_fade_risk | mixed_moisture | light_wind | -2.00 | -1.44 |
| 2026-06-15 | Jeddah | current_high_yes | YES | 0.96 | 0.08 | 38+ | 37 | 38+ |  | hot_underforecast_clean | day_forecast_busted | active_warming | dry_heat_inertia | moderate_wind | -3.89 | -2.22 |
| 2026-06-12 | Munich | d2_no | NO | 0.96 | 0.07 | 17 | 15 | 16 | 17 | hot_underforecast_noisy | day_forecast_capped | active_warming | humid_overcast_suppression | moderate_wind | -2.56 | -0.33 |
| 2026-06-13 | Wellington | d1_no | NO | 0.96 | 0.07 | 15 | 14 | 15 | 16 | hot_underforecast_clean | day_forecast_capped | fresh_high | humid_overcast_suppression | moderate_wind | -1.44 | -0.33 |
| 2026-05-23 | Ankara | current_high_yes | YES | 0.95 | 0.06 | 19 | 18 | 19 | 20 | hot_underforecast_clean | day_forecast_capped | fresh_high | mixed_moisture | moderate_wind | -0.72 | 0.39 |
| 2026-05-28 | Helsinki | d1_no | NO | 0.95 | 0.10 | 16 | 15 | 16 | 17+ | mild_or_mixed | day_marginal_runway | fresh_high | dry_heat_inertia | windy_mixing_noise | -0.06 | 1.06 |
| 2026-06-08 | Singapore | current_high_yes | YES | 0.95 | 0.07 | 33 | 32 | 33 | 34 | hot_underforecast_clean | day_forecast_busted | false_fade_risk | mixed_moisture | light_wind | -2.00 | -1.44 |
| 2026-06-11 | Munich | d1_no | NO | 0.95 | 0.08 | 17 | 16 | 17 | 18 | hot_underforecast_noisy | day_forecast_busted | plateau_near_high | mixed_moisture | moderate_wind | -2.50 | -1.39 |
| 2026-06-11 | Shanghai | current_high_yes | YES | 0.95 | 0.09 | 32 | 31 | 32 | 33 | hot_underforecast_clean | day_forecast_busted | slow_warming | dry_heat_inertia | light_wind | -2.28 | -1.17 |
| 2026-06-18 | Shanghai | current_high_yes | YES | 0.95 | 0.08 | 28 | 27 | 28 | 29 | hot_underforecast_clean | day_forecast_capped | plateau_near_high | humid_convective_risk | light_wind | -1.00 | -0.44 |
| 2026-06-18 | Shanghai | d1_no | NO | 0.95 | 0.06 | 28 | 27 | 28 | 29 | hot_underforecast_clean | day_forecast_capped | plateau_near_high | humid_convective_risk | light_wind | -1.00 | -0.44 |
| 2026-06-09 | Denver | d1_no | NO | 0.95 | 0.35 | 92-93 | 90-91 | 92-93 | 94-95 | mild_or_mixed | day_forecast_busted | active_warming | dry_heat_inertia | windy_mixing_noise | -4.20 | -2.20 |
| 2026-06-13 | Wellington | current_high_yes | YES | 0.94 | 0.08 | 15 | 14 | 15 | 16 | hot_underforecast_clean | day_forecast_capped | fresh_high | humid_overcast_suppression | moderate_wind | -1.44 | -0.33 |
| 2026-05-22 | Guangzhou | d1_no | NO | 0.94 | 0.18 | 33 | 32 | 33 | 34+ | hot_underforecast_noisy | day_forecast_capped | slow_warming | humid_convective_risk | moderate_wind | -0.22 | 0.33 |
| 2026-05-25 | Manila | current_high_yes | YES | 0.94 | 0.10 | 37 | 36 | 37 | 38 | hot_underforecast_clean | day_forecast_busted | plateau_near_high | mixed_moisture | light_wind | -2.28 | -1.17 |
| 2026-05-29 | Chongqing | current_high_yes | YES | 0.94 | 0.07 | 23 | 22 | 23 | 24 | hot_underforecast_clean | day_forecast_busted | mature_fade | cloud_suppression | light_wind | -1.67 | -1.11 |

## Interpretation

这才是下一步该继续的方向：先把反转 case 当成事故/机会库，而不是立刻做 selector。后续应该训练/验证的是 `P(high token loses | market high ask, forecast/obs/regime conflict)`，并且 YES 反转、current NO 反转、d1/d2 NO exact-hit 反转要分头建模。

当前还不能交易，因为：

- d1/d2 opposite YES 只有 proxy ask，没有真实 YES ask/depth。
- 这些表是 case-mining，不是 train/holdout selector。
- 需要把 high-price reversal score 在 forward telemetry 里记录，并等 settled forward 样本验证。

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_high_price_forecast_bias_reversal_cases_v1.py`
- JSON summary: `docs/analysis/2026-06/2026-06-30-high-price-forecast-bias-reversal-cases-v1.json`
- High-price rows: `docs/analysis/2026-06/generated/high_price_forecast_bias_reversal_cases_v1/high_price_rows.csv`
- Overall summary: `docs/analysis/2026-06/generated/high_price_forecast_bias_reversal_cases_v1/high_price_reversal_summary.csv`
- Reversal cases: `docs/analysis/2026-06/generated/high_price_forecast_bias_reversal_cases_v1/top_reversal_cases.csv`
