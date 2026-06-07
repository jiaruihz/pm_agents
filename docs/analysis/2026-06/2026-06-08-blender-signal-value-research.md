# Blender Signal Value Research — 2026-06-08

## 数据快照

- 数据源：`/home/rui/projects/pm_agent/runtime/weather.db`；只读 `fact_trades` / `fact_signal_candidates`，不使用 legacy DB。
- 生成时间 UTC：`2026-06-07T16:48:28.978247+00:00`。
- DB mtime UTC：`2026-06-07T16:42:24.587220+00:00`。
- `MAX(fact_built_at_utc)`：`2026-06-07T16:42:07.976007+00:00`。
- CLOB coverage gate：`gate_pass=True`；`missing_order_rows=0`；`over_order_keys=0`；`db_fill_cost_minus_fact_cost=0.0`。
- 目标样本：`mid_price_core_v1_25_75` / `strategy_id=live_weather_edge_v1_4ef9b3ec3e2e` / `trade_class=live_real` / `settlement_status=settled`，共 `774` fills，`2026-05-16 -> 2026-06-06`。
- 当前 strict operational base：剔除 `Ankara, BuenosAires, Jeddah, Karachi, Moscow, Munich` 且 `22<=hours_to_settle<=28`，共 `308` fills。
- unsettled 占比：本研究目标样本只取 settled；全库 settlement_status 分布见 SQL 自检。
- missing_bracket 数：见 SQL 自检；目标样本 missing_bracket=0。

### 5 行 SQL 自检

trade_class 分布：
| trade_class | n |
| --- | --- |
| live_real | 1320 |
| live_simulated | 1093 |
| paper | 2285 |
| snapshot_replay | 636 |

settlement_status 分布：
| settlement_status | n |
| --- | --- |
| None | 301 |
| settled | 5033 |

fact_signal_candidates 覆盖：
| n | eligible | paper_ordered | live_filled |
| --- | --- | --- | --- |
| 23485 | 7641 | 2835 | 513 |

orders/fills by venue/status：
| status | orders | with_fill |
| --- | --- | --- |
| error | 151 | 0 |
| submitted | 1546 | 1320 |

目标策略样本：
| fills | min_target_date | max_target_date | cost_usd | pnl_usd |
| --- | --- | --- | --- | --- |
| 774 | 2026-05-16 | 2026-06-06 | 2092.945888 | 7.515706999999988 |

## Target Metric

`blender_signal_value` = 在当前 operational base 内，不改变历史成交价、成交量和结算结果，只把 `blended_edge` 当作 size/risk 信号，观察 scaled PnL 是否优于等额原策略。

本报告不回答 basket，也不回答城市池是否该继续调整；城市和 `22<=T<=28` 在这里作为已知 strict operational base 固定住。

## 结论

- **hard gate 仍不适合上线**：`hard_blended_edge_ge_0.10` 在 full operational base 的 delta `$-115.28`，post delta `$-36.60`；它靠减少交易面提高 ROI，但会继续牺牲净 PnL。
- **size curve 比 hard gate 更合理，但还不够强**：`gentle_blended_size_curve` full delta `$-67.65`，post delta `$-15.30`，effective cost `39.1%`。它降低风险，但当前样本没有证明能稳定增厚收益。
- **最有研究价值的是负 blended edge / 低 blended edge 的风险识别**：`no_negative_blended_edge` full delta `$-41.36`，post delta `$+0.00`；若这条都不稳定，复杂阈值更不该 live。
- **alpha 不应继续用全样本最优来定**：alpha grid 只说明不同市场收缩强度的诊断结果，不能作为生产调参依据。需要 shadow lineage 的逐 snapshot walk-forward 再决定城市/side 动态 alpha。
- **walk-forward 选择没有通过 live hard-gate 标准**：13 folds，总 delta `$-16.40`，中位 fold delta `$+0.00`，正/负 folds `1/3`。

交易动作：blender 继续保留为 shadow/paper + lineage 字段；当前不建议作为真实 live hard gate。下一步只研究 `size_multiplier` 和 `model-health alert`。

## Size Policy 对比

### Full operational base
| policy | fills | base_pnl | sized_pnl | delta | eff_cost | sized_roi | avoid_loss | miss_profit | partial | zero | ex_top5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| unit_base | 308 | $+167.35 | $+167.35 | $+0.00 | 100.0% | +20.1% | $-0.00 | $0.00 | 0 | 0 | $+103.20 |
| hard_blended_edge_ge_0.10 | 308 | $+167.35 | $+52.07 | $-115.28 | 17.9% | +34.9% | $260.37 | $375.66 | 0 | 245 | $+10.62 |
| hard_blended_or_raw_gt_0.25 | 308 | $+167.35 | $+92.14 | $-75.22 | 36.8% | +30.0% | $215.73 | $290.95 | 0 | 199 | $+38.92 |
| no_negative_blended_edge | 308 | $+167.35 | $+125.99 | $-41.36 | 64.4% | +23.5% | $74.29 | $115.65 | 0 | 87 | $+61.84 |
| mild_blended_size_curve | 308 | $+167.35 | $+126.83 | $-40.52 | 54.4% | +28.0% | $118.65 | $159.18 | 69 | 87 | $+62.67 |
| gentle_blended_size_curve | 308 | $+167.35 | $+99.70 | $-67.65 | 39.1% | +30.6% | $179.52 | $247.17 | 141 | 87 | $+46.49 |
| disagreement_haircut | 308 | $+167.35 | $+104.11 | $-63.24 | 56.5% | +22.1% | $101.73 | $164.98 | 51 | 87 | $+46.98 |

### Post 2026-06-01
| policy | fills | base_pnl | sized_pnl | delta | eff_cost | sized_roi | avoid_loss | miss_profit | partial | zero | ex_top5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| unit_base | 79 | $+61.37 | $+61.37 | $+0.00 | 100.0% | +34.7% | $-0.00 | $0.00 | 0 | 0 | $+22.10 |
| hard_blended_edge_ge_0.10 | 79 | $+61.37 | $+24.78 | $-36.60 | 25.5% | +54.8% | $53.73 | $90.33 | 0 | 56 | $+0.48 |
| hard_blended_or_raw_gt_0.25 | 79 | $+61.37 | $+31.29 | $-30.08 | 31.1% | +56.9% | $53.73 | $83.81 | 0 | 52 | $+7.00 |
| no_negative_blended_edge | 79 | $+61.37 | $+61.37 | $+0.00 | 100.0% | +34.7% | $-0.00 | $0.00 | 0 | 0 | $+22.10 |
| mild_blended_size_curve | 79 | $+61.37 | $+60.86 | $-0.52 | 81.9% | +42.0% | $14.75 | $15.27 | 28 | 0 | $+22.83 |
| gentle_blended_size_curve | 79 | $+61.37 | $+46.07 | $-15.30 | 56.5% | +46.1% | $34.24 | $49.54 | 52 | 0 | $+18.69 |
| disagreement_haircut | 79 | $+61.37 | $+50.46 | $-10.91 | 88.1% | +32.4% | $7.50 | $18.41 | 16 | 0 | $+13.30 |

### Pre 2026-06-01
| policy | fills | base_pnl | sized_pnl | delta | eff_cost | sized_roi | avoid_loss | miss_profit | partial | zero | ex_top5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| unit_base | 229 | $+105.98 | $+105.98 | $+0.00 | 100.0% | +16.1% | $-0.00 | $0.00 | 0 | 0 | $+45.11 |
| hard_blended_edge_ge_0.10 | 229 | $+105.98 | $+27.30 | $-78.68 | 15.9% | +26.2% | $206.65 | $285.33 | 0 | 189 | $-11.68 |
| hard_blended_or_raw_gt_0.25 | 229 | $+105.98 | $+60.85 | $-45.13 | 38.4% | +24.2% | $162.00 | $207.13 | 0 | 147 | $+9.43 |
| no_negative_blended_edge | 229 | $+105.98 | $+64.62 | $-41.36 | 54.8% | +18.0% | $74.29 | $115.65 | 0 | 87 | $+3.75 |
| mild_blended_size_curve | 229 | $+105.98 | $+65.97 | $-40.01 | 46.9% | +21.4% | $103.90 | $143.90 | 41 | 87 | $+5.10 |
| gentle_blended_size_curve | 229 | $+105.98 | $+53.63 | $-52.35 | 34.4% | +23.7% | $145.28 | $197.63 | 89 | 87 | $+1.67 |
| disagreement_haircut | 229 | $+105.98 | $+53.65 | $-52.33 | 48.0% | +17.0% | $94.24 | $146.57 | 35 | 87 | $+1.46 |

字段说明：`delta = sized_pnl - base_pnl`；`eff_cost` 是按 size 后的实际成本占原等额成本比例；`ex_top5` 是去掉前 5 个 scaled winner 后的 PnL。

## Blender 分层诊断

### 按 blended_edge 分层
| blended_edge_bin | fills | pnl | roi | win_rate | avg_raw_edge | avg_blended_edge | avg_div |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.05-0.10 | 89 | $+75.59 | +34.3% | 52.8% | +0.211 | +0.070 | 0.202 |
| >=0.10 | 63 | $+52.07 | +34.9% | 60.3% | +0.365 | +0.143 | 0.324 |
| <0.00 | 87 | $+41.36 | +13.9% | 72.4% | +0.215 | -0.190 | 0.549 |
| 0.00-0.05 | 69 | $-1.67 | -1.0% | 46.4% | +0.140 | +0.035 | 0.143 |

### 按 raw-market 绝对分歧分层
| divergence_bin | fills | pnl | roi | win_rate | avg_raw_edge | avg_blended_edge | avg_div |
| --- | --- | --- | --- | --- | --- | --- | --- |
| >0.20 | 188 | $+116.99 | +21.5% | 64.9% | +0.272 | -0.033 | 0.420 |
| 0.10-0.20 | 118 | $+59.57 | +21.2% | 49.2% | +0.155 | +0.056 | 0.143 |
| <=0.05 | 1 | $-4.21 | -100.0% | 0.0% | +0.495 | +0.477 | 0.020 |
| 0.05-0.10 | 1 | $-5.00 | -100.0% | 0.0% | +0.116 | +0.171 | 0.080 |

### 按 raw_edge 分层
| raw_edge_bin | fills | pnl | roi | win_rate | avg_raw_edge | avg_blended_edge | avg_div |
| --- | --- | --- | --- | --- | --- | --- | --- |
| >0.25 | 98 | $+74.68 | +26.6% | 61.2% | +0.352 | +0.046 | 0.427 |
| 0.15-0.25 | 136 | $+60.55 | +16.6% | 56.6% | +0.195 | -0.024 | 0.298 |
| 0.10-0.15 | 74 | $+32.12 | +17.1% | 58.1% | +0.123 | -0.003 | 0.183 |

### 按 side / model / timing 分层

side：
| side | fills | pnl | roi | win_rate | avg_raw_edge | avg_blended_edge | avg_div |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BUY_YES | 119 | $+97.42 | +39.5% | 44.5% | +0.196 | +0.063 | 0.186 |
| BUY_NO | 189 | $+69.94 | +11.9% | 67.2% | +0.247 | -0.034 | 0.391 |

model_version：
| model_version | fills | pnl | roi | win_rate | avg_raw_edge | avg_blended_edge | avg_div |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gfs | 225 | $+97.28 | +16.3% | 57.8% | +0.246 | +0.016 | 0.324 |
| ecmwf | 83 | $+70.08 | +29.9% | 60.2% | +0.177 | -0.029 | 0.278 |

hours_bin：
| hours_bin | fills | pnl | roi | win_rate | avg_raw_edge | avg_blended_edge | avg_div |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T-22-24 | 89 | $+84.66 | +40.4% | 58.4% | +0.236 | +0.047 | 0.263 |
| T-24-26 | 204 | $+78.06 | +13.4% | 59.8% | +0.230 | -0.018 | 0.345 |
| T-26-28 | 15 | $+4.63 | +10.8% | 40.0% | +0.148 | +0.049 | 0.142 |

## Alpha Grid 诊断

这里使用统一 alpha，不使用后验城市调参。`brier` 只在已成交 selected fills 上计算，不能外推到完整机会宇宙。

| alpha | brier | hard_delta | hard_eff_cost | gentle_delta | gentle_eff_cost | post_gentle_delta | post_gentle_eff_cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.0 | 0.2793 | $-154.45 | 4.1% | $-122.15 | 18.6% | $-44.68 | 24.2% |
| 0.1 | 0.2657 | $-154.45 | 4.1% | $-126.10 | 23.9% | $-47.53 | 36.3% |
| 0.2 | 0.2547 | $-140.74 | 8.7% | $-76.16 | 34.3% | $-21.68 | 50.3% |
| 0.3 | 0.2464 | $-115.28 | 17.9% | $-68.56 | 39.6% | $-15.30 | 56.5% |
| 0.5 | 0.2377 | $-54.99 | 36.0% | $-43.48 | 53.6% | $-9.50 | 75.9% |
| 0.7 | 0.2395 | $-49.05 | 59.6% | $-18.92 | 75.0% | $-5.52 | 84.7% |
| 1.0 | 0.2621 | $+0.00 | 100.0% | $+0.00 | 100.0% | $+0.00 | 100.0% |

## Walk-forward

方法：每个 `target_date` 只用之前 7 个 target_date 选择训练期 scaled PnL 最高的 size policy，再评价当天；候选包含 `unit_base`，因此如果 blender 没有训练期优势，模型可以选择不使用 blender。

- folds：`13`
- total_delta：`$-16.40`
- median_delta：`$+0.00`
- positive/negative folds：`1/3`
- chosen_counts：`{"unit_base": 9, "hard_blended_or_raw_gt_0.25": 2, "no_negative_blended_edge": 1, "mild_blended_size_curve": 1}`

最大 delta 日期：
| target_date | fills | base_pnl | sized_pnl | delta |
| --- | --- | --- | --- | --- |
| 2026-05-22 | 20 | $+35.00 | $+14.61 | $-20.39 |
| 2026-06-03 | 6 | $+31.41 | $+14.74 | $-16.67 |
| 2026-05-29 | 17 | $+37.17 | $+21.19 | $-15.98 |
| 2026-05-20 | 12 | $+16.92 | $+2.39 | $-14.53 |
| 2026-05-24 | 27 | $+28.50 | $+14.27 | $-14.23 |
| 2026-05-31 | 13 | $-31.27 | $-17.67 | $+13.60 |
| 2026-05-25 | 25 | $+12.89 | $+24.08 | $+11.19 |
| 2026-05-26 | 22 | $-15.40 | $-5.59 | $+9.81 |
| 2026-05-17 | 4 | $+8.40 | $+0.00 | $-8.40 |
| 2026-05-30 | 19 | $-15.32 | $-8.37 | $+6.95 |
| 2026-06-05 | 10 | $-19.87 | $-13.02 | $+6.85 |
| 2026-06-01 | 30 | $+48.34 | $+42.95 | $-5.39 |

## 下一步

1. 在 shadow/paper lineage 中写入 `raw_p_yes / market_p_yes / blended_p_yes / blended_edge / size_multiplier_candidate / raw_market_disagreement`。
2. 用未来 7-14 天逐 snapshot 数据重新跑本脚本；验收看中位日 delta、avoided_loss/missed_profit、top winner 依赖，而不是单个 headline ROI。
3. 若 `blended_edge<0` 连续稳定过滤净亏损，再考虑 live 上只做 `size=0 or 0.25x` 的小范围 canary；不要先上 `edge>=0.10` hard gate。
4. 动态 alpha 只允许按事前 slice 生成，例如 `city x side x model_version x hours_bin`，并必须 walk-forward；禁止全样本挑最优 alpha 直接上线。
