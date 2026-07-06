# HeadA Low-Price YES Alpha Decomposition v1

Generated: `2026-07-06T04:59:10Z`

Scope: HeadA `forecast_tail_low_price_yes` only.  This report keeps the live sleeve definition fixed and decomposes the next alpha work into probability ranking, source/PIT integrity, book/fill execution, expression choice, and city/source/execution attribution.

## Verdict

```text
probability_ranking=real_but_not_live_selector
ecmwf_outage_counterfactual=partially_quantified_non_pit_for_forecast_max
book_state=highest_value_next_work; needs fillability cost model
expression=selected_yes remains baseline; hotter/plus/basket not promoted
city_source=explanatory/telemetry, not raw city filter
live_action=no new live selector or size-up from this report
```

人话结论：模型分数确实会排序，高分票比低分票更容易中；但“高分”还没有在同分母上稳定打赢现在的 `dist>0` 选择器。接下来不要再加硬 gate，应该把分数变成仓位/优先级的 shadow ledger，同时把盘口可成交性和 source/PIT 质量作为 EV 成本层。

## 1. 概率模型有没有排序能力

- Train decile Spearman: `+0.837`。
- 低 3 档：115 rows，命中 7.0%。
- 高 3 档：115 rows，命中 24.3%，比低 3 档高 +17.4%。
- bottom decile 5.1%，top decile 30.8%。

这说明分数能排序，但还不是交易规则。原因是同票数 A/B 没过：continuous same-count 相对当前 `dist>0` 的 train excess 只有 +1.4pp，CI 跨 0；recent 还偏负。

| decile | rows | win_rate | avg_p | avg_ask | avg_raw_dist | avg_adj_dist |
| --- | --- | --- | --- | --- | --- | --- |
| 0.000 | 39.000 | 5.1% | 7.1% | 6.3% | -0.300 | -0.664 |
| 1.000 | 38.000 | 7.9% | 8.6% | 7.2% | -0.050 | -0.465 |
| 2.000 | 38.000 | 7.9% | 10.2% | 7.7% | 0.317 | -0.252 |
| 3.000 | 38.000 | 5.3% | 11.8% | 8.3% | 0.447 | -0.102 |
| 4.000 | 39.000 | 7.7% | 13.5% | 8.7% | 0.775 | 0.108 |
| 5.000 | 38.000 | 15.8% | 15.1% | 10.0% | 0.668 | 0.190 |
| 6.000 | 38.000 | 7.9% | 16.6% | 11.4% | 0.527 | 0.062 |
| 7.000 | 38.000 | 21.1% | 18.6% | 13.2% | 0.467 | 0.017 |
| 8.000 | 38.000 | 21.1% | 22.7% | 14.4% | 0.771 | 0.186 |
| 9.000 | 39.000 | 30.8% | 29.5% | 17.5% | 0.837 | 0.183 |

### Selector A/B

| label | period | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | losing_days | le_minus50pct_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_dist_gt0 | full | 333 | 53 | 47 | 15.0% | 10.4% | +41.8% | +10.7% | +76.3% | 19 | 16 | $-11.04 |
| current_dist_gt0 | train_le_2026_06_20 | 275 | 44 | 46 | 14.5% | 10.5% | +35.1% | -0.1% | +76.4% | 18 | 15 | $-11.04 |
| current_dist_gt0 | recent_ge_2026_06_21 | 58 | 9 | 28 | 17.2% | 9.9% | +76.7% | +31.7% | +133.9% | 1 | 1 | $-4.26 |
| continuous_same_count | full | 343 | 53 | 48 | 15.5% | 11.0% | +38.3% | +3.3% | +77.6% | 23 | 18 | $-12.45 |
| continuous_same_count | train_le_2026_06_20 | 275 | 44 | 48 | 15.3% | 10.9% | +36.6% | -5.6% | +87.4% | 21 | 17 | $-12.45 |
| continuous_same_count | recent_ge_2026_06_21 | 68 | 9 | 32 | 16.2% | 11.4% | +44.8% | +10.4% | +81.1% | 2 | 1 | $-1.51 |
| intersection | full | 295 | 52 | 47 | 15.6% | 10.6% | +46.0% | +10.1% | +86.5% | 19 | 18 | $-11.04 |
| intersection | train_le_2026_06_20 | 243 | 44 | 46 | 15.6% | 10.7% | +43.0% | +2.1% | +90.3% | 18 | 17 | $-11.04 |
| intersection | recent_ge_2026_06_21 | 52 | 8 | 28 | 15.4% | 10.2% | +61.2% | +16.0% | +104.6% | 1 | 1 | $-4.26 |
| continuous_only | full | 48 | 31 | 14 | 14.6% | 13.7% | +5.2% | -64.8% | +83.1% | 25 | 25 | $-5.64 |
| continuous_only | train_le_2026_06_20 | 32 | 23 | 13 | 12.5% | 12.8% | -0.9% | -100.0% | +109.8% | 20 | 20 | $-5.64 |
| continuous_only | recent_ge_2026_06_21 | 16 | 8 | 10 | 18.8% | 15.4% | +14.7% | -100.0% | +119.2% | 5 | 5 | $-3.95 |
| dist_only | full | 38 | 26 | 15 | 10.5% | 8.9% | -0.9% | -79.8% | +121.2% | 22 | 22 | $-3.42 |
| dist_only | train_le_2026_06_20 | 32 | 21 | 13 | 6.2% | 9.3% | -40.1% | -100.0% | +64.1% | 19 | 19 | $-3.42 |
| dist_only | recent_ge_2026_06_21 | 6 | 5 | 6 | 33.3% | 7.1% | +318.4% | -100.0% | +871.2% | 3 | 3 | $-0.71 |

## 2. ECMWF outage 期间的反事实到底知道多少

精确答案：我们本地没有 7/02-7/05 outage 窗口里当时决策时刻的 PIT ECMWF forecast curve，所以不能把当前 API 查回来的过去日期 forecast 当成严格回测。forecast 是能查，但缺的是当时那一版的 issue/run-time snapshot。

可用答案：已经用 current API forecast-max 做了非 PIT 近似，只能估数量级和方向，不能当 confirmed alpha。这个近似显示确实会换一批票：

- live fills 全部：20 settled / 1 wins / ROI -30.5%。
- source-aligned：13 settled / 1 wins / ROI +2.7%。
- source-mismatch：7 settled / 0 wins / ROI -100.0%。
- `dist>0` current-API 近似：observed dirty 22 rows / settled win 12.5%；approx 25 rows / settled win 18.8%。
- `dist>0` 近似增删：新增 9，剔除 6，overlap 16。

因此，outage 改变了短窗 forward 的交易集合和解释；但它不推翻 5/06..6/30 的主回测，因为主回测来自 stored/canonical historical denominator，不依赖这几天 Mac cache 的错误 source。

## 3. Book State / Fillability

Book state 的意思是：这个低价 YES 的盘口是真能买到的错价，还是 missing/thin/wide 导致历史 ask 看起来便宜但实际 fill 不了、或只有 stale quote。它不是天气特征，是执行和市场注意力特征。

| slice_value | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi | losing_days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| feasible | 212 | 41 | 44 | 11.8% | 9.9% | +17.8% | -14.8% | +53.4% | -6.4% | 20 |
| missing | 62 | 17 | 29 | 22.6% | 10.7% | +104.6% | +18.7% | +187.0% | +38.2% | 8 |
| thin_wide | 59 | 28 | 24 | 18.6% | 11.8% | +51.1% | -19.8% | +126.4% | -17.6% | 18 |

历史上 missing/thin 看起来更强，但这恰好是最容易混入 stale quote 幻觉的地方。下一步应该建 `fill_probability * expected_edge - fee/slippage`，不是把 missing/thin 直接变 hard filter。

Maker/size-up 现有证据：

| scenario_notional_usd | scenario_model | orders | any_fill_rate | full_fill_rate | mean_fill_fraction | median_first_fill_wait_min | missed_winner_cost_usd | settled_orders | settled_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.000 | absolute_fill_cap | 18 | 83.3% | 27.8% | 72.7% | 6.217 | $+1.99 | 15 | -27.9% |
| 1.000 | proportional_fill_fraction | 18 | 83.3% | 77.8% | 83.3% | 6.217 | $+0.00 | 15 | -21.5% |
| 3.000 | absolute_fill_cap | 18 | 83.3% | 0.0% | 26.1% | 6.217 | $+21.97 | 15 | -33.4% |
| 3.000 | proportional_fill_fraction | 18 | 83.3% | 77.8% | 83.3% | 6.217 | $+0.00 | 15 | -21.5% |
| 5.000 | absolute_fill_cap | 18 | 83.3% | 0.0% | 15.7% | 6.217 | $+41.95 | 15 | -33.4% |
| 5.000 | proportional_fill_fraction | 18 | 83.3% | 77.8% | 83.3% | 6.217 | $+0.00 | 15 | -21.5% |

Dynamic maker fallback 现在只能当 tiny live 执行 overlay，样本太少，不是 alpha 证据：

| ttl_min | spread_cap | price_cushion | orders | fallback_taker_orders | policy_roi | baseline_roi | pnl_delta_usd | taker_fees_usd_total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 0.005 | 0.005 | 12 | 2 | 0.321 | 0.225 | 0.521 | 0.045 |
| 10 | 0.005 | 0.010 | 12 | 2 | 0.321 | 0.225 | 0.521 | 0.045 |
| 10 | 0.005 | 0.020 | 12 | 2 | 0.321 | 0.225 | 0.521 | 0.045 |
| 10 | 0.010 | 0.005 | 12 | 2 | 0.321 | 0.225 | 0.521 | 0.045 |
| 10 | 0.010 | 0.010 | 12 | 3 | 0.290 | 0.225 | 0.360 | 0.088 |
| 10 | 0.010 | 0.020 | 12 | 3 | 0.290 | 0.225 | 0.360 | 0.088 |
| 10 | 0.020 | 0.005 | 12 | 2 | 0.321 | 0.225 | 0.521 | 0.045 |
| 10 | 0.020 | 0.010 | 12 | 3 | 0.290 | 0.225 | 0.360 | 0.088 |
| 10 | 0.020 | 0.020 | 12 | 3 | 0.290 | 0.225 | 0.360 | 0.088 |
| 10 | 0.030 | 0.005 | 12 | 3 | 0.388 | 0.225 | 0.841 | 0.066 |
| 10 | 0.030 | 0.010 | 12 | 4 | 0.354 | 0.225 | 0.679 | 0.109 |
| 10 | 0.030 | 0.020 | 12 | 4 | 0.354 | 0.225 | 0.679 | 0.109 |

## 4. Expression / Overshoot

Overshoot 确实存在，但同 snapshot 买更热腿、plus 或 basket 没有赢过原 selected YES。也就是说，问题不是“遇到 overshoot 就改买 hotter”，而是先估 `P(each expression wins) - ask - fee - fill_cost`。

| label | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| higher_plus_yes | 246 | 52 | 42 | 2.8% | 4.1% | -32.4% | -64.2% | +0.1% | -75.0% |
| next_hotter_yes | 307 | 53 | 44 | 17.3% | 17.0% | -1.8% | -24.3% | +20.1% | -10.5% |
| selected_plus_next_basket | 307 | 53 | 44 | 31.9% | 14.2% | +12.4% | -1.2% | +25.7% | +7.2% |
| selected_yes | 333 | 53 | 47 | 15.0% | 10.4% | +38.2% | +7.4% | +72.1% | +25.5% |
| two_hotter_yes | 290 | 53 | 44 | 17.2% | 18.0% | -7.3% | -29.0% | +13.9% | -15.0% |

同分母表达 A/B：

| label | period | rows | dates | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | paired_alt |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| selected_yes | same_denominator_vs_next_hotter_yes | 307 | 53 | 14.7% | 10.4% | +35.5% | +4.9% | +68.9% | next_hotter_yes |
| next_hotter_yes | same_denominator_vs_next_hotter_yes | 307 | 53 | 17.3% | 17.0% | -1.8% | -24.3% | +20.1% | next_hotter_yes |
| selected_yes | same_denominator_vs_two_hotter_yes | 290 | 53 | 15.2% | 10.5% | +38.2% | +6.9% | +72.6% | two_hotter_yes |
| two_hotter_yes | same_denominator_vs_two_hotter_yes | 290 | 53 | 17.2% | 18.0% | -7.3% | -29.0% | +13.9% | two_hotter_yes |
| selected_yes | same_denominator_vs_higher_plus_yes | 246 | 52 | 15.0% | 10.3% | +39.4% | +1.6% | +81.4% | higher_plus_yes |
| higher_plus_yes | same_denominator_vs_higher_plus_yes | 246 | 52 | 2.8% | 4.1% | -32.4% | -64.2% | +0.1% | higher_plus_yes |
| selected_yes | same_denominator_vs_selected_plus_next_basket | 307 | 53 | 14.7% | 10.4% | +35.5% | +4.9% | +68.9% | selected_plus_next_basket |
| selected_plus_next_basket | same_denominator_vs_selected_plus_next_basket | 307 | 53 | 31.9% | 14.2% | +12.4% | -1.2% | +25.7% | selected_plus_next_basket |

Clean EV selector 的同 row 对比也没确认替代表达：

| selector_name | rows | dates | delta_roi | delta_ci_low | delta_ci_high |
| --- | --- | --- | --- | --- | --- |
| physics_ev_positive_cap20 | 315 | 53 | -2.2% | -50.7% | +50.3% |
| physics_same_count_cap20 | 333 | 53 | +13.3% | -8.1% | +37.2% |
| market_blend_same_count_cap20 | 333 | 53 | -19.8% | -71.0% | +33.5% |
| attention_blend_same_count_cap20 | 333 | 53 | -25.2% | -71.4% | +21.2% |

## 5. City / Source / Execution Decomposition

城市不是一个可以直接 hard-filter 的 alpha。更干净的拆法是：这个城市 assigned source 的历史 forecast bias、当天 source/PIT 是否一致、盘口是否能成交、live fill 质量如何。

| forecast_model | source_fit_bucket | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ecmwf | hot_clean | 150 | 47 | 15 | 12.7% | 10.1% | +31.8% | -21.4% | +89.1% | -1.2% |
| ecmwf | hot_noisy | 4 | 4 | 1 | 0.0% | 8.8% | -100.0% | -100.0% | -100.0% |  |
| ecmwf | neutral_or_cold | 68 | 39 | 13 | 22.1% | 11.3% | +94.7% | +15.6% | +185.7% | +38.4% |
| gfs | hot_clean | 71 | 42 | 8 | 15.5% | 11.1% | +14.0% | -43.1% | +84.8% | -42.3% |
| gfs | hot_noisy | 1 | 1 | 1 | 100.0% | 6.0% | +1491.8% |  |  |  |
| gfs | neutral_or_cold | 39 | 28 | 9 | 10.3% | 9.1% | +19.2% | -72.7% | +113.4% | -100.0% |

Source fit 与 book state 交叉后，`feasible_book` 并不强，反而 missing/thin 的收益更高但不稳定。这支持“注意力错价 vs stale quote”作为下一层研究，而不是城市白名单。

| source_fit_bucket | book_state_v1 | rows | dates | cities | win_rate | avg_entry | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hot_clean | feasible | 141 | 41 | 23 | 9.9% | 9.9% | +1.8% | -40.1% | +48.6% |
| hot_clean | missing | 38 | 13 | 17 | 21.1% | 10.8% | +77.9% | -9.9% | +190.4% |
| hot_clean | thin_wide | 42 | 25 | 16 | 19.0% | 11.8% | +45.4% | -40.1% | +142.6% |
| hot_noisy | feasible | 3 | 3 | 2 | 33.3% | 7.3% | +275.0% | -100.0% | +1491.8% |
| hot_noisy | missing | 2 | 2 | 1 | 0.0% | 9.5% | -100.0% |  |  |
| neutral_or_cold | feasible | 68 | 30 | 19 | 14.7% | 10.1% | +43.0% | -25.7% | +122.3% |
| neutral_or_cold | missing | 22 | 12 | 11 | 27.3% | 10.7% | +168.5% | -30.5% | +293.8% |
| neutral_or_cold | thin_wide | 17 | 12 | 8 | 17.6% | 11.8% | +64.6% | -100.0% | +202.4% |

## Next Actions

1. `score_rank_decile` 进 shadow journal：每张 live/shadow 票记概率 decile、同 decile forward hit rate、按 decile 的 notional/PnL。
2. 建 HeadA EV 成本层：`expected_value = p_win - ask - official_fee - slippage - nonfill_penalty`；book_state 进入成本，不当硬 gate。
3. 做 PIT forecast provenance：每个 decision snapshot 记录 `forecast_run_id/issue_time/source_model/fallback_reason`，outage 窗口标 `non_pit_approx_only`。
4. 表达层保持 selected YES baseline；next-hotter/plus/basket 只在同 row EV 明确超出时 shadow 选择，不 blanket 切。
5. 城市/source 不做 raw whitelist；做 `city_source_execution_score`，由历史 bias、assigned-source match、live fill quality 三部分组成，用于解释和容量，不直接 live promote。

## Current Decision

不改 live selector，不 size-up。继续 tiny live + dynamic maker overlay；新增的是 shadow 记录和下一版 EV 研究脚本方向。
