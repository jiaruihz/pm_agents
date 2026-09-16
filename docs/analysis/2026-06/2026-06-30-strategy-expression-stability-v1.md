# Strategy Expression Stability v1

Generated: 2026-06-30

## Verdict

本报告补充 `regime_routed_no_route_price_disciplined_tiny_live_v1` 与 city-fit overlay 的稳定性切片。结论仍是 `inconclusive_shadow_only`，不改 live。

`current_high_yes` 和 `higher_no_d1` 方向相近，但不是同一个 payoff：`current_high_yes` 只有最终 winner 仍在当前高点 bracket 才赢；`d1 NO` 只要最终 winner 不是下一档 d1 就赢。若温度直接越过 d1 到 d2+，`current_high_yes` 输而 `d1 NO` 赢；若正好落到 d1，两者都输。

## Selected Strategy Stability

| policy | expression | rows | dates | cities | win | avg ask | cost | pnl | ROI | CI low | CI high | loss days | <=-50% days | max loss | median day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| running_current_row_risk_soft | current_bracket_no | 188 | 37 | 33 | +49.5% | 0.48 | $+380.76 | $+126.43 | +33.2% | +8.1% | +58.1% | 14 | 8 | $-10.52 | $+1.77 |
| running_current_row_risk_soft | higher_no_d2 | 84 | 34 | 24 | +61.9% | 0.61 | $+87.87 | $+3.82 | +4.4% | -15.5% | +23.6% | 15 | 8 | $-2.73 | $+0.29 |
| running_current_row_risk_soft | current_high_yes | 11 | 11 | 8 | +72.7% | 0.73 | $+22.69 | $-3.05 | -13.5% | -54.8% | +32.9% | 3 | 3 | $-4.29 | $+0.22 |
| city_fit_soft_overlay_v2 | current_bracket_no | 188 | 37 | 33 | +49.5% | 0.48 | $+320.76 | $+109.26 | +34.1% | +9.8% | +56.8% | 14 | 8 | $-8.17 | $+1.14 |
| city_fit_soft_overlay_v2 | higher_no_d2 | 84 | 34 | 24 | +61.9% | 0.61 | $+63.18 | $+3.41 | +5.4% | -14.1% | +24.0% | 15 | 8 | $-1.90 | $+0.22 |
| city_fit_soft_overlay_v2 | current_high_yes | 11 | 11 | 8 | +72.7% | 0.73 | $+21.08 | $-2.18 | -10.3% | -53.2% | +35.2% | 3 | 3 | $-3.86 | $+0.20 |

## Daily Policy Distribution

| policy | rows | dates | cities | cost | pnl | ROI | CI low | CI high | loss days | <=-50% days | p10 day | median day | p90 day | max loss | best day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| running_current_row_risk_soft | 283 | 38 | 35 | $+491.32 | $+127.20 | +25.9% | +6.9% | +45.6% | 16 | 5 | $-6.45 | $+0.97 | $+13.56 | $-9.52 | $+34.56 |
| city_fit_soft_overlay_v2 | 283 | 38 | 35 | $+405.03 | $+110.50 | +27.3% | +8.3% | +45.7% | 15 | 6 | $-5.89 | $+1.09 | $+10.91 | $-7.67 | $+24.97 |

## Current-High YES vs Same-Trigger D1 NO

| expression | rows | dates | cities | win | avg ask | cost | pnl | ROI | CI low | CI high | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| same_trigger_d1_no | 11 | 11 | 8 | +90.9% | 0.79 | $+8.70 | $+1.30 | +14.9% | -4.5% | +28.9% | 1 | $-0.56 |
| current_high_yes | 11 | 11 | 8 | +72.7% | 0.73 | $+8.00 | $+0.00 | +0.0% | -36.6% | +31.4% | 3 | $-0.80 |

### Current-High Case Details

| city | date | hour | current | d1 | winner | YES ask | YES payoff | d1 NO ask | d1 NO payoff | case |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SaoPaulo | 2026-05-21 | 11 | 17 | 18 | 17 | 0.70 | 1.00 | 0.84 | 1.00 | stays_current_both_win |
| TelAviv | 2026-05-31 | 14 | 28 | 29 | 28 | 0.90 | 1.00 | 0.93 | 1.00 | stays_current_both_win |
| Helsinki | 2026-06-05 | 11 | 19 | 20 | 19 | 0.68 | 1.00 | 0.77 | 1.00 | stays_current_both_win |
| Denver | 2026-06-06 | 14 | 88-89 | 90-91 | 92-93 | 0.80 | 0.00 | 0.81 | 1.00 | skips_d1_d1_no_wins |
| NYC | 2026-06-07 | 14 | 80-81 | 82-83 | 80-81 | 0.68 | 1.00 | 0.73 | 1.00 | stays_current_both_win |
| TelAviv | 2026-06-11 | 14 | 29 | 30 | 29 | 0.90 | 1.00 | 0.95 | 1.00 | stays_current_both_win |
| Helsinki | 2026-06-13 | 13 | 15 | 16 | 15 | 0.89 | 1.00 | 0.87 | 1.00 | stays_current_both_win |
| Busan | 2026-06-19 | 12 | 28 | 29 | 28 | 0.76 | 1.00 | 0.80 | 1.00 | stays_current_both_win |
| Jeddah | 2026-06-21 | 13 | 35 | 36 | 35 | 0.57 | 1.00 | 0.65 | 1.00 | stays_current_both_win |
| Jeddah | 2026-06-24 | 13 | 34 | 35 | 36 | 0.72 | 0.00 | 0.80 | 1.00 | skips_d1_d1_no_wins |
| Shanghai | 2026-06-25 | 10 | 24 | 25 | 25 | 0.40 | 0.00 | 0.56 | 0.00 | lands_d1_both_lose |

## Broad Expression Matrix Sanity Check

这张表不是当前策略选单，只是同一批 first-signal rows 上的表达形态 sanity check，用来看 `current YES` / `d1 NO` / `d2 NO` 的天然价格和稳定性。

| rule | expr | rows | dates | cities | win | avg ask | pnl | ROI | CI low | CI high | loss days | max loss |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_edge_components_ge_2 | current_yes | 624 | 36 | 36 | +62.0% | 0.59 | $+20.46 | +5.6% | +0.3% | +11.1% | 12 | $-3.08 |
| first_edge_components_ge_2 | d1_no | 624 | 36 | 36 | +67.6% | 0.67 | $+2.06 | +0.5% | -3.4% | +4.4% | 17 | $-2.66 |
| first_edge_components_ge_2 | d2_no | 598 | 36 | 36 | +94.6% | 0.94 | $+1.78 | +0.3% | -1.6% | +2.1% | 17 | $-2.23 |
| first_edge_market_components_ge_0 | current_yes | 711 | 36 | 36 | +72.3% | 0.70 | $+15.91 | +3.2% | -0.4% | +7.3% | 11 | $-3.93 |
| first_edge_market_components_ge_0 | d1_no | 710 | 36 | 36 | +75.4% | 0.76 | $-3.59 | -0.7% | -3.4% | +2.3% | 17 | $-3.11 |
| first_edge_market_components_ge_0 | d2_no | 662 | 36 | 36 | +96.8% | 0.96 | $+4.99 | +0.8% | -0.5% | +2.0% | 12 | $-1.43 |
| first_edge_market_components_ge_2 | current_yes | 594 | 36 | 36 | +71.2% | 0.67 | $+22.81 | +5.7% | +1.2% | +10.3% | 8 | $-4.29 |
| first_edge_market_components_ge_2 | d1_no | 593 | 36 | 36 | +74.4% | 0.74 | $+3.27 | +0.7% | -2.7% | +4.2% | 17 | $-3.48 |
| first_edge_market_components_ge_2 | d2_no | 559 | 36 | 36 | +96.8% | 0.96 | $+5.73 | +1.1% | -0.5% | +2.6% | 11 | $-1.49 |
| first_low_components_q20 | current_yes | 332 | 36 | 35 | +84.6% | 0.80 | $+14.79 | +5.6% | +1.0% | +10.0% | 13 | $-2.42 |
| first_low_components_q20 | d1_no | 332 | 36 | 35 | +85.8% | 0.84 | $+6.59 | +2.4% | -1.8% | +6.2% | 16 | $-3.00 |
| first_low_components_q20 | d2_no | 306 | 36 | 35 | +98.7% | 0.98 | $+2.84 | +0.9% | -0.4% | +2.0% | 4 | $-0.91 |
| first_low_market_components_q20 | current_yes | 396 | 35 | 35 | +93.4% | 0.91 | $+9.12 | +2.5% | -0.1% | +4.9% | 14 | $-1.78 |
| first_low_market_components_q20 | d1_no | 395 | 35 | 35 | +93.7% | 0.92 | $+5.08 | +1.4% | -1.2% | +3.7% | 15 | $-1.89 |
| first_low_market_components_q20 | d2_no | 355 | 35 | 35 | +99.7% | 0.99 | $+1.67 | +0.5% | -0.2% | +0.8% | 1 | $-0.93 |
| first_low_market_components_q30 | current_yes | 553 | 36 | 36 | +91.5% | 0.89 | $+11.70 | +2.4% | -0.3% | +4.9% | 11 | $-2.50 |
| first_low_market_components_q30 | d1_no | 552 | 36 | 36 | +91.8% | 0.91 | $+4.38 | +0.9% | -1.6% | +3.3% | 13 | $-2.90 |
| first_low_market_components_q30 | d2_no | 500 | 36 | 36 | +99.6% | 0.99 | $+2.98 | +0.6% | -0.0% | +1.0% | 2 | $-0.89 |

## Interpretation

- `current_bracket_no` 是当前策略的主要贡献头；它的总 ROI 高，但 CI 仍宽，且亏损日不少，说明组合收益来自少数好日和 route/price discipline，不是每日稳定提款。
- `higher_no_d2` 胜率高但 ask 很贵，ROI 很薄；它更像降低波动的 capped-day tail 表达，不是主要 alpha。
- `current_high_yes` 样本只有 11 笔，不能单独 live。它的问题不是方向错，而是 exact-bracket 风险太强：停在当前档才赢，正好到 d1 输，跳到 d2+ 也输。
- 同触发点反事实里，`d1 NO` 比 `current_high_yes` 更像“高点已过/不要再刚好升一档”的表达；但全量 first-signal sanity check 里 `d1 NO` 的 avg ask 通常更贵，ROI 反而低于 current YES，所以不能简单替换。
- 明显要改善的地方：把 pullback/peak-fade 这类形态拆成独立表达选择问题。先在 shadow 里同时记录 current-high YES、d1 NO、d2 NO 的同触发点价格和 payoff，再让 route 根据 overshoot risk / exact-stop risk 选择表达，而不是固定接 `current_high_yes`。

## Data Notes

- Selected rows: `docs/analysis/2026-06/generated/strategy_city_fit_soft_sizing_v2/strategy_city_fit_soft_sizing_rows.csv`.
- Expression matrix: `docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv`.
- PnL here is replay/research PnL, not live fill-realized PnL.
- CI is target-date block bootstrap over daily blocks; this is a stability diagnostic, not a live approval gate.
