# v1_25_75 blocked ECMWF cities side-band overlay

## 数据快照

- 数据源：`/home/rui/projects/pm_agent/runtime/weather.db`。
- DB mtime UTC：`2026-06-07T05:49:19.463464+00:00`；`MAX(fact_built_at_utc)`：`2026-06-07T05:48:57.787507+00:00`。
- 样本：`BuenosAires, Munich, Jeddah, Karachi, Moscow, Ankara` 的 `ECMWF + v1_25_75 + live_real + settled` 历史 fills。
- Overlay 规则：BUY_YES `0.20<=market_price<0.45 and raw_edge>=0.20`；BUY_NO `0.35<=market_price<0.65 and raw_edge>=0.10`。
- 这是同一批已成交 fill 的 side-band filter overlay，不是重新撮合/重新下单模拟。

## 结论

- 对这 6 个 blocked ECMWF 城市，6 月后 baseline PnL `-99.14`；如果只保留会通过 side-band 的历史 fills，PnL `-9.85`，过滤掉的 fills PnL `-89.29`，净改善 `+89.29`。
- 6 月前 baseline PnL `-41.91`；side-band kept PnL `-24.60`，过滤掉的 fills PnL `-17.31`，净变化 `+17.31`。
- 因此 side-band 对这些 ECMWF 城市是有改善的，尤其是 post 期；但它不是完全解决方案，因为 side-band 仍会保留一部分亏损组合。

## 1. period 总览

| period | baseline_fills | baseline_pnl | baseline_roi | side_band_kept_fills | side_band_kept_pnl | side_band_kept_roi | filtered_fills | filtered_pnl | delta_if_side_band_filter |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | 95 | -99.14 | -39.8% | 35 | -9.85 | -10.2% | 60 | -89.29 | +89.29 |
| pre_2026_06_01 | 75 | -41.91 | -19.5% | 28 | -24.60 | -29.1% | 47 | -17.31 | +17.31 |

## 2. city overlay

| period | city | baseline_fills | baseline_pnl | baseline_roi | side_band_kept_fills | side_band_kept_pnl | side_band_kept_roi | filtered_fills | filtered_pnl | delta_if_side_band_filter |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | Ankara | 15 | -8.02 | -20.4% | 8 | -0.56 | -2.3% | 7 | -7.46 | +7.46 |
| post_2026_06_01 | BuenosAires | 14 | -28.59 | -63.6% | 3 | +1.39 | 9.3% | 11 | -29.98 | +29.98 |
| post_2026_06_01 | Jeddah | 18 | -17.81 | -45.0% | 3 | +2.93 | 58.7% | 15 | -20.75 | +20.75 |
| post_2026_06_01 | Karachi | 16 | -14.96 | -37.7% | 10 | +0.03 | 0.1% | 6 | -14.99 | +14.99 |
| post_2026_06_01 | Moscow | 17 | -11.71 | -23.2% | 2 | -2.83 | -37.1% | 15 | -8.87 | +8.87 |
| post_2026_06_01 | Munich | 15 | -18.05 | -52.0% | 9 | -10.82 | -54.3% | 6 | -7.23 | +7.23 |
| pre_2026_06_01 | Ankara | 15 | -9.95 | -22.8% | 8 | -14.12 | -63.7% | 7 | +4.17 | -4.17 |
| pre_2026_06_01 | BuenosAires | 13 | -3.06 | -8.7% | 5 | -6.80 | -45.4% | 8 | +3.74 | -3.74 |
| pre_2026_06_01 | Jeddah | 11 | -3.42 | -13.7% | 0 | +0.00 |  | 11 | -3.42 | +3.42 |
| pre_2026_06_01 | Karachi | 18 | -1.86 | -3.5% | 10 | -3.68 | -10.7% | 8 | +1.83 | -1.83 |
| pre_2026_06_01 | Moscow | 10 | -6.78 | -20.5% | 2 | +1.85 | 58.7% | 8 | -8.63 | +8.63 |
| pre_2026_06_01 | Munich | 8 | -16.85 | -67.6% | 3 | -1.85 | -18.7% | 5 | -15.00 | +15.00 |

## 3. side overlay

| period | side | baseline_fills | baseline_pnl | baseline_roi | side_band_kept_fills | side_band_kept_pnl | side_band_kept_roi | filtered_fills | filtered_pnl | delta_if_side_band_filter |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | BUY_NO | 65 | -49.35 | -24.8% | 35 | -9.85 | -10.2% | 30 | -39.50 | +39.50 |
| post_2026_06_01 | BUY_YES | 30 | -49.79 | -100.0% | 0 | +0.00 |  | 30 | -49.79 | +49.79 |
| pre_2026_06_01 | BUY_NO | 57 | -26.62 | -14.7% | 25 | -28.79 | -38.3% | 32 | +2.17 | -2.17 |
| pre_2026_06_01 | BUY_YES | 18 | -15.29 | -45.2% | 3 | +4.19 | 44.9% | 15 | -19.48 | +19.48 |

## 4. 被过滤原因

| period | side_band_reason | fills | cost_usd | pnl_usd | roi | win_rate | avg_entry_price | avg_raw_edge_entry |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | no_price_at_or_above_0.65 | 30 | 102.28 | -39.50 | -38.6% | 40.0% | 0.696 | 0.200 |
| post_2026_06_01 | yes_edge_below_0.20 | 25 | 39.79 | -39.79 | -100.0% | 0.0% | 0.270 | 0.127 |
| post_2026_06_01 | yes_price_at_or_above_0.45 | 5 | 10.00 | -10.00 | -100.0% | 0.0% | 0.512 | 0.290 |
| pre_2026_06_01 | no_price_at_or_above_0.65 | 28 | 85.72 | 8.19 | 9.6% | 75.0% | 0.698 | 0.175 |
| pre_2026_06_01 | no_price_below_0.35 | 4 | 19.91 | -6.02 | -30.2% | 50.0% | 0.297 | 0.609 |
| pre_2026_06_01 | yes_edge_below_0.20 | 15 | 24.48 | -19.48 | -79.6% | 6.7% | 0.267 | 0.137 |

## 5. side-band 会保留的组合

| period | city | side | fills | cost_usd | pnl_usd | roi | win_rate | avg_entry_price | avg_raw_edge_entry |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| post_2026_06_01 | Ankara | BUY_NO | 8 | 24.48 | -0.56 | -2.3% | 50.0% | 0.606 | 0.275 |
| post_2026_06_01 | BuenosAires | BUY_NO | 3 | 14.99 | 1.39 | 9.3% | 66.7% | 0.592 | 0.146 |
| post_2026_06_01 | Jeddah | BUY_NO | 3 | 5.00 | 2.93 | 58.7% | 100.0% | 0.635 | 0.152 |
| post_2026_06_01 | Karachi | BUY_NO | 10 | 24.73 | 0.03 | 0.1% | 70.0% | 0.597 | 0.156 |
| post_2026_06_01 | Moscow | BUY_NO | 2 | 7.65 | -2.83 | -37.1% | 50.0% | 0.578 | 0.200 |
| post_2026_06_01 | Munich | BUY_NO | 9 | 19.91 | -10.82 | -54.3% | 11.1% | 0.544 | 0.150 |
| pre_2026_06_01 | Ankara | BUY_NO | 8 | 22.18 | -14.12 | -63.7% | 25.0% | 0.594 | 0.137 |
| pre_2026_06_01 | BuenosAires | BUY_NO | 5 | 14.99 | -6.80 | -45.4% | 20.0% | 0.595 | 0.273 |
| pre_2026_06_01 | Karachi | BUY_NO | 7 | 24.98 | -7.87 | -31.5% | 42.9% | 0.556 | 0.260 |
| pre_2026_06_01 | Karachi | BUY_YES | 3 | 9.32 | 4.19 | 44.9% | 66.7% | 0.343 | 0.409 |
| pre_2026_06_01 | Moscow | BUY_NO | 2 | 3.15 | 1.85 | 58.7% | 100.0% | 0.635 | 0.186 |
| pre_2026_06_01 | Munich | BUY_NO | 3 | 9.91 | -1.85 | -18.7% | 66.7% | 0.625 | 0.180 |

## 交易含义

1. 这 6 个 city×ECMWF 从 v1_25_75 live 剔除是合理的；历史 post 期 side-band overlay 比原 v1_25_75 明显改善。
2. 不建议把这些城市的 ECMWF 直接转成 side-band live；应先 shadow，因为 overlay 不是重新下单模拟，且 side-band 保留样本仍可能亏。
3. 如果要探索恢复，优先 shadow `side-band + ECMWF + raw_edge/ timing 二级过滤`，而不是恢复 flat 25-75。

## 口径限制

- side-band overlay 只能说明这些已成交 v1_25_75 fills 是否会被 side-band gate 拦住；它不包含 side-band 可能新增的其他机会。
- 使用 `market_price` 作为原始信号入场价，PnL 仍使用 `pnl_usd_at_fill`。
- post realized 样本当前到 target_date 2026-06-05；未结算目标日不纳入 realized PnL。
