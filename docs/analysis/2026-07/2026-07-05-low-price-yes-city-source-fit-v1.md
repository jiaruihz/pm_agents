# HeadA Low-Price YES City/Source Fit v1

Generated: 2026-07-05T14:07:50+00:00

## 数据快照

- 数据已同步 Mac weather-data-feed，并重建 `runtime/weather.db`；`weather_clob_fill_coverage_gate.py` 通过，`gate_pass=true`。
- 分母：HeadA 当前 hot-only `dist>0` denominator，333 rows / 53 dates / 47 cities，窗口 2026-05-06..2026-06-30。
- 训练/近期：train<=2026-06-20 为 275 rows；recent>=2026-06-21 为 58 rows；fresh>=2026-07-04 为 0 rows。
- 绩效口径：`price_tier_6_8_10_shares` + taker at ask + Polymarket Weather 官方 fee `shares * 0.05 * price * (1-price)` + hold to settlement。
- 历史 source-fit 来自 `city_model_error_summary.csv` 和 `city_strategy_fit_by_forecast_bias.csv`；live 执行质量只作诊断，不进入历史 selector。

## 结论

一句话：**城市确实有相关性，但现在不该把 city 做成 hard filter；更干净的方向是把 city 拆成 forecast source fit + book attention + execution quality，先进入 shadow telemetry。**

当前 baseline：333 rows / 53 dates / ROI +41.8%，date-block CI [+10.7%, +76.3%]，win 15.0%。

```text
significance=PARTIAL
baseline=PARTIAL
forward=FAIL/NA
conclusion=shadow_candidate / no live selector change
```

最有启发的点：

1. `source_hot_clean` 能保留大部分赢家，但点估没有稳定压过 baseline，不能直接切 live。
2. `source_hot_clean + thin/missing book` 历史点估更好，说明“source fit 解释天气方向，book state 解释市场是否没跟上”这条机制值得继续 shadow。
3. `wrong_source_hot_alt` 不是简单坏分支；说明“当前 model 不是城市历史最热尾模型”不能直接当过滤器，因为市场/runner 已经隐含很多 source selection。
4. live 执行质量按城市样本还很薄，不能用今天几张 Shanghai/Amsterdam 订单决定城市池。

## 同分母 selector A/B

| label | rows | row_share | dates | cities | win_rate | avg_entry | avg_shares | roi | roi_ci_low | roi_ci_high | top5_removed_roi | complement_roi | roi_minus_complement | delta_ci_low | delta_ci_high | losing_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_hot_only | 333 | 100.0% | 53 | 47 | 15.0% | 10.4% | 7.61 | +41.8% | +10.7% | +76.3% | +28.3% |  |  |  |  | 19 | $-11.04 |
| source_hot_clean | 221 | 66.4% | 51 | 23 | 13.6% | 10.4% | 7.60 | +25.6% | -12.2% | +66.0% | +4.4% | +74.0% | -48.4% | -132.1% | +31.6% | 26 | $-8.89 |
| source_hot_clean_and_fit_best | 176 | 52.9% | 50 | 20 | 12.5% | 10.4% | 7.59 | +11.9% | -26.4% | +54.7% | -15.5% | +75.2% | -63.3% | -129.4% | +1.1% | 30 | $-7.28 |
| source_hot_clean_attention | 80 | 24.0% | 37 | 20 | 20.0% | 11.4% | 7.88 | +60.1% | -4.1% | +129.8% | +9.3% | +35.0% | +25.0% | -47.4% | +105.3% | 24 | $-3.87 |
| source_hot_clean_feasible_book | 141 | 42.3% | 41 | 23 | 9.9% | 9.9% | 7.45 | +1.8% | -40.1% | +48.6% | -36.6% | +67.8% | -65.9% | -135.8% | +5.3% | 27 | $-6.46 |
| fit_best_model_match | 268 | 80.5% | 52 | 42 | 14.6% | 10.4% | 7.60 | +35.3% | -1.9% | +77.2% | +18.1% | +68.3% | -33.0% | -131.2% | +61.0% | 24 | $-9.42 |
| best_hot_model_match | 174 | 52.3% | 53 | 23 | 14.9% | 10.4% | 7.59 | +44.3% | +0.4% | +91.6% | +17.9% | +39.2% | +5.1% | -60.2% | +71.9% | 30 | $-6.17 |
| wrong_source_hot_alt | 118 | 35.4% | 45 | 19 | 17.8% | 10.9% | 7.85 | +52.9% | -0.5% | +108.1% | +17.5% | +35.0% | +17.9% | -49.7% | +85.4% | 27 | $-5.26 |
| source_score_ge4 | 276 | 82.9% | 52 | 33 | 14.9% | 10.5% | 7.65 | +37.5% | +4.6% | +75.1% | +21.2% | +65.2% | -27.7% | -126.5% | +72.4% | 22 | $-9.86 |
| source_score_le2 | 27 | 8.1% | 20 | 9 | 14.8% | 9.8% | 7.48 | +62.3% | -59.5% | +191.0% | -100.0% | +40.2% | +22.1% | -112.0% | +156.2% | 16 | $-2.03 |
| source_attention_score_ge5 | 211 | 63.4% | 52 | 30 | 13.7% | 10.7% | 7.68 | +24.7% | -13.8% | +66.7% | +3.1% | +74.0% | -49.3% | -122.1% | +20.6% | 29 | $-8.89 |

读法：

- `source_hot_clean` = 当前使用的 forecast source 在该城市历史上 `bias>0 && hot_tail_pct>=40% && cold_tail_pct<=12%`。
- `fit_best_model_match` = 当前 source/model 等于 city-strategy-fit 文档里的 best model（缺失时回落 MAE best）。
- `best_hot_model_match` = 当前 source/model 等于历史 hot-tail rate 最高的 model。
- `wrong_source_hot_alt` = 当前 source/model 不是 hot-tail best，且与 best hot source 差距 >=15pp；这是诊断，不是 sell/avoid 规则。
- `source_attention_score_ge5` = source-fit score 高且 book_state 为 `missing/thin_wide`，用来观察“天气偏差 + 市场注意力/薄书”的交互。

## Forward / Recent

| period | label | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi | losing_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recent_ge_2026_06_21 | baseline_hot_only | 58 | 9 | 28 | 17.2% | 9.9% | +76.7% | +31.7% | +133.9% | -8.4% | 1 | $-4.26 |
| recent_ge_2026_06_21 | source_hot_clean | 40 | 9 | 16 | 17.5% | 9.7% | +81.9% | +7.0% | +172.6% | -51.3% | 2 | $-4.26 |
| recent_ge_2026_06_21 | source_score_ge4 | 48 | 9 | 21 | 16.7% | 10.1% | +67.0% | +16.9% | +138.5% | -38.5% | 1 | $-4.26 |
| train_le_2026_06_20 | baseline_hot_only | 275 | 44 | 46 | 14.5% | 10.5% | +35.1% | -0.1% | +76.4% | +18.7% | 18 | $-11.04 |
| train_le_2026_06_20 | source_hot_clean | 181 | 42 | 23 | 12.7% | 10.6% | +14.7% | -25.1% | +61.6% | -11.5% | 24 | $-8.89 |
| train_le_2026_06_20 | source_score_ge4 | 228 | 43 | 33 | 14.5% | 10.6% | +31.7% | -7.2% | +74.9% | +11.9% | 21 | $-9.86 |

这里最重要的不是谁点估最高，而是 recent 仍然太薄且日期相关性强。任何 source/city selector 都不能因为 full-window 数字好看就上线。

## Source Fit 贡献

按 `forecast_model + source_fit_bucket`：

| forecast_model | source_fit_bucket | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ecmwf | hot_clean | 150 | 47 | 15 | 12.7% | 10.1% | +31.8% | -21.4% | +89.1% | -1.2% | $+40.58 |
| ecmwf | hot_noisy | 4 | 4 | 1 | 0.0% | 8.8% | -100.0% | -100.0% | -100.0% |  | $-2.81 |
| ecmwf | neutral_or_cold | 68 | 39 | 13 | 22.1% | 11.3% | +94.7% | +15.6% | +185.7% | +38.4% | $+65.17 |
| gfs | hot_clean | 71 | 42 | 8 | 15.5% | 11.1% | +14.0% | -43.1% | +84.8% | -42.3% | $+9.57 |
| gfs | hot_noisy | 1 | 1 | 1 | 100.0% | 6.0% | +1491.8% |  |  |  | $+5.62 |
| gfs | neutral_or_cold | 39 | 28 | 9 | 10.3% | 9.1% | +19.2% | -72.7% | +113.4% | -100.0% | $+5.15 |

分母构成：

| forecast_model | source_fit_bucket | rows |
| --- | --- | --- |
| ecmwf | hot_clean | 150 |
| ecmwf | hot_noisy | 4 |
| ecmwf | neutral_or_cold | 68 |
| gfs | hot_clean | 71 |
| gfs | hot_noisy | 1 |
| gfs | neutral_or_cold | 39 |

这说明 source fit 有解释力，但不是单独足够的 alpha。GFS/ECMWF 不能全局选一个，要按城市/source profile 进概率层；同时还要检查盘口是否给了可买价。

## City + Source 贡献

Top positive city/source:

| city | forecast_model | source_fit_bucket | rows | dates | win_rate | avg_entry | roi | top5_removed_roi | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Amsterdam | ecmwf | hot_clean | 16 | 16 | 37.5% | 13.3% | +183.9% | -32.3% | $+37.57 |
| Madrid | ecmwf | neutral_or_cold | 4 | 4 | 75.0% | 14.8% | +392.2% |  | $+22.31 |
| Shanghai | gfs | hot_clean | 14 | 14 | 35.7% | 10.6% | +168.4% | -100.0% | $+21.33 |
| Moscow | ecmwf | neutral_or_cold | 12 | 12 | 33.3% | 13.0% | +137.0% | -100.0% | $+20.81 |
| BuenosAires | ecmwf | hot_clean | 8 | 8 | 37.5% | 15.7% | +143.3% | -100.0% | $+17.67 |
| Busan | ecmwf | neutral_or_cold | 3 | 3 | 66.7% | 12.7% | +412.7% |  | $+14.49 |
| KualaLumpur | ecmwf | hot_clean | 4 | 4 | 50.0% | 12.7% | +278.9% |  | $+13.25 |
| Manila | gfs | hot_clean | 13 | 13 | 23.1% | 8.8% | +124.0% | -100.0% | $+11.07 |
| Warsaw | ecmwf | neutral_or_cold | 9 | 9 | 22.2% | 10.7% | +115.0% | -100.0% | $+9.63 |
| Istanbul | ecmwf | neutral_or_cold | 3 | 3 | 33.3% | 11.2% | +229.9% |  | $+6.97 |
| Helsinki | ecmwf | hot_clean | 13 | 13 | 15.4% | 8.3% | +68.6% | -100.0% | $+5.70 |
| Guangzhou | gfs | hot_noisy | 1 | 1 | 100.0% | 6.0% | +1491.8% |  | $+5.62 |
| Paris | gfs | neutral_or_cold | 6 | 6 | 16.7% | 8.3% | +123.5% | -100.0% | $+4.42 |
| SaoPaulo | ecmwf | neutral_or_cold | 3 | 3 | 33.3% | 7.5% | +277.3% |  | $+4.41 |
| Dallas | ecmwf | hot_clean | 5 | 5 | 20.0% | 9.4% | +120.2% |  | $+4.37 |
| Tokyo | gfs | neutral_or_cold | 6 | 6 | 16.7% | 8.4% | +119.5% | -100.0% | $+4.35 |
| Wuhan | ecmwf | hot_clean | 7 | 7 | 14.3% | 7.4% | +116.8% | -100.0% | $+4.31 |
| Miami | gfs | neutral_or_cold | 3 | 3 | 33.3% | 9.2% | +201.7% |  | $+4.01 |

Bottom city/source:

| city | forecast_model | source_fit_bucket | rows | dates | win_rate | avg_entry | roi | top5_removed_roi | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Shenzhen | ecmwf | hot_clean | 12 | 12 | 0.0% | 11.8% | -100.0% | -100.0% | $-12.55 |
| Ankara | ecmwf | hot_clean | 16 | 16 | 0.0% | 9.4% | -100.0% | -100.0% | $-11.67 |
| Munich | ecmwf | hot_clean | 15 | 15 | 0.0% | 9.0% | -100.0% | -100.0% | $-10.46 |
| Austin | gfs | hot_clean | 9 | 9 | 0.0% | 11.7% | -100.0% | -100.0% | $-9.23 |
| Chicago | gfs | hot_clean | 9 | 9 | 0.0% | 11.6% | -100.0% | -100.0% | $-9.22 |
| Beijing | ecmwf | hot_clean | 15 | 15 | 0.0% | 8.0% | -100.0% | -100.0% | $-8.85 |
| TelAviv | gfs | neutral_or_cold | 13 | 13 | 0.0% | 8.3% | -100.0% | -100.0% | $-7.68 |
| Seattle | gfs | hot_clean | 4 | 4 | 0.0% | 13.9% | -100.0% |  | $-5.31 |
| Lucknow | ecmwf | neutral_or_cold | 5 | 5 | 0.0% | 11.6% | -100.0% |  | $-5.17 |
| CapeTown | ecmwf | neutral_or_cold | 4 | 4 | 0.0% | 12.9% | -100.0% |  | $-4.97 |
| Atlanta | gfs | hot_clean | 13 | 13 | 7.7% | 12.2% | -30.6% | -100.0% | $-4.41 |
| HongKong | ecmwf | hot_clean | 5 | 5 | 0.0% | 8.1% | -100.0% |  | $-2.96 |
| SanFrancisco | ecmwf | hot_noisy | 4 | 4 | 0.0% | 8.8% | -100.0% |  | $-2.81 |
| NYC | gfs | neutral_or_cold | 2 | 2 | 0.0% | 10.5% | -100.0% |  | $-1.75 |
| Chengdu | ecmwf | hot_clean | 2 | 2 | 0.0% | 10.0% | -100.0% |  | $-1.54 |
| London | ecmwf | neutral_or_cold | 11 | 11 | 9.1% | 10.4% | -15.3% | -100.0% | $-1.45 |
| Houston | gfs | neutral_or_cold | 1 | 1 | 0.0% | 10.5% | -100.0% |  | $-0.88 |
| Jeddah | ecmwf | neutral_or_cold | 1 | 1 | 0.0% | 8.5% | -100.0% |  | $-0.71 |

这张表只用于 case review 和 forward telemetry。低样本城市不满足 `active_days>=10 && settled_fills>=30` 的 city-level live 动作门槛，不能直接 keep/cut。

## Live 执行质量诊断

`low_price_yes_lottery_tiny_live_v1` 当前真实订单按 opportunity 去重后的 city fill 质量：

| city | opportunities | any_fill_rate | full_fill_rate | avg_fill_ratio | total_requested_shares | total_filled_shares | total_fill_cost | median_first_fill_wait_min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Chicago | 2 | 100.0% | 100.0% | 100.0% | 38.560 | 38.560 | $+2.30 | 50.81 |
| NYC | 2 | 100.0% | 100.0% | 100.0% | 42.310 | 42.310 | $+2.29 | 2.50 |
| Helsinki | 2 | 50.0% | 50.0% | 50.0% | 11.300 | 5.300 | $+0.80 | 109.25 |
| Ankara | 1 | 100.0% | 100.0% | 100.0% | 12.500 | 12.500 | $+0.95 | 0.00 |
| Wellington | 1 | 100.0% | 100.0% | 100.0% | 8.000 | 8.000 | $+0.88 | 446.62 |
| Manila | 1 | 100.0% | 100.0% | 100.0% | 14.290 | 14.290 | $+0.80 | 21.12 |
| Busan | 1 | 100.0% | 100.0% | 100.0% | 16.670 | 16.670 | $+0.80 | 6.22 |
| Paris | 1 | 100.0% | 100.0% | 99.9% | 13.120 | 13.112 | $+0.80 | 464.83 |
| LA | 1 | 100.0% | 100.0% | 100.0% | 5.680 | 5.680 | $+0.80 | 13.93 |
| Dallas | 1 | 100.0% | 100.0% | 100.0% | 7.210 | 7.210 | $+0.79 | 83.97 |
| Houston | 1 | 100.0% | 100.0% | 100.0% | 8.800 | 8.800 | $+0.79 | 4.55 |
| TelAviv | 1 | 100.0% | 100.0% | 100.0% | 7.150 | 7.150 | $+0.79 | 0.00 |
| London | 1 | 100.0% | 100.0% | 100.0% | 12.500 | 12.500 | $+0.75 | 0.00 |
| Shanghai | 1 | 100.0% | 100.0% | 100.0% | 6.000 | 6.000 | $+0.32 | 587.07 |

这层回答的是“信号出来以后能不能买到”，不是 forecast alpha。样本还太少，但以后要把 city/source fit 与 fillability 一起看：有 forecast bias 但订单薄、maker 买不到，实盘 EV 仍然可能没有。

## 研究动作

下一版不建议继续加城市黑名单，而是把这些字段进 shadow 账本：

- `source_fit_bucket`
- `source_fit_score`
- `source_attention_score`
- `model_is_fit_best`
- `model_is_best_hot`
- `hot_tail_gap_vs_best`
- `live_city_fill_quality_bucket`

后续裁决方式：固定当前 HeadA live selector，只在 fresh forward 上比较这些 shadow tags 的 realized PnL、fill rate、missed-winner cost。若某个 tag 在 fresh forward 同时满足正 ROI、CI 不跨 0、top-winner removed 仍正、且 fillability 不差，再设计接回 live。

## 8 环覆盖自检

- 1 描述性绩效切片：PASS
- 2 统计推断：PARTIAL，date-block bootstrap 已做，但多 selector K=11，未做正式多重检验校正
- 3 信号判别：PARTIAL，source-fit score 有排序诊断但不是概率模型
- 4 概率分布评估：NA，本轮不校准 P(win)
- 5 执行微结构：PARTIAL，live fill 诊断已接，但样本薄
- 6 容量：FAIL，不能由 tiny rows 推 $3/$5
- 7 组合相关性：PARTIAL，按 target_date block bootstrap
- 8 基准/反事实：PARTIAL，同分母 complement 有，零模型/NO 反事实本轮未重跑

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_city_source_fit_v1.py`
- Report: `docs/analysis/2026-07/2026-07-05-low-price-yes-city-source-fit-v1.md`
- JSON: `docs/analysis/2026-07/2026-07-05-low-price-yes-city-source-fit-v1.json`
- Generated CSVs: `docs/analysis/2026-07/generated/low_price_yes_city_source_fit_v1/`
