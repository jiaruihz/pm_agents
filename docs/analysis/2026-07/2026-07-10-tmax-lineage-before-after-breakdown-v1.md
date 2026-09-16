# Tmax Lineage Before/After Breakdown v1

> generated_at_utc: `2026-07-10T15:04:03.637405+00:00`
> This compares the old live feature-missingness behavior with the repaired full-feature behavior. It does not pretend the missing historical siblings or fresh direct YES asks can be backfilled.

## 结论

- 6/21..7/08 的 16 个 active dates：修复前 172 笔，ROI +12.0%；修复后 159 笔，ROI +8.1%。
- 修复后相对修复前 ROI delta = -3.9%，date-block 95% CI [-10.8%, +3.2%]，跨 0，交易收益差异不显著。
- 修复后有 5 天 PnL 改善、11 天变差。概率 proper score 在 dev/forward 都改善，但 max-edge first-lock 的选单结果没有同步改善。
- 下降主要来自 selection：40 个 before-only city-day 原口径事后赚了约 5.22/每股口径；27 个 after-only city-day 合计亏约 1.29。14 个换表达 city-day 反而改善约 1.45。
- 这说明 feature parity 修复本身必须保留，但下一步应研究 probability -> executable expression 的选择/不确定度，而不是把字段重新删掉。

## 口径边界

- before = `live_missingness_emulation`：GFS/ECMWF gaps、RH、sky 等按旧 live 缺失方式进入模型。
- after = `historical_full_features`：相同 expanding fit、相同 fee/ask/edge/first-city-day policy，只恢复 live 应有字段。
- 每行是一 city-day 的第一笔 eligible expression，PnL 是 1 share 标准化，已扣 `0.05*p*(1-p)` taker fee；不是实际 live fill。
- tail parser、完整 sibling collector、fresh direct YES ask 无法从缺失历史中无损回填，故不计入本表 PnL；它们只从修复后 forward 重新积累。
- 本轮 canonical rebuild 完成 fact tables，但 live fill coverage gate 因历史 over-order/cache mismatch 失败；本报告不使用或发布 live_real PnL。

## Proper Score

| scope | variant | rows | dates | logloss | brier |
| --- | --- | --- | --- | --- | --- |
| dev_cv | historical_full_features | 5462 | 28 | 0.612 | 0.349 |
| dev_cv | live_missingness_emulation | 5462 | 28 | 0.614 | 0.349 |
| verified_forward | historical_full_features | 2077 | 16 | 0.589 | 0.328 |
| verified_forward | live_missingness_emulation | 2077 | 16 | 0.593 | 0.331 |

## Dev / Forward 选单汇总

| scope | version | rows | dates | win_rate | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | after_repaired | 551 | 28 | 64.428 | 348.827 | 6.173 | 1.770 |
| dev_cv | before_missing_live | 584 | 28 | 62.329 | 362.244 | 1.756 | 0.485 |
| verified_forward | after_repaired | 159 | 16 | 69.182 | 101.776 | 8.224 | 8.081 |
| verified_forward | before_missing_live | 172 | 16 | 70.930 | 108.928 | 13.072 | 12.000 |

## 每日对比

| target_date | before_missing_live_rows | before_missing_live_win_rate | before_missing_live_roi | before_missing_live_pnl | after_repaired_rows | after_repaired_win_rate | after_repaired_roi | after_repaired_pnl | delta_pnl | delta_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | 15.000 | 53.333 | -18.108 | -1.769 | 13.000 | 69.231 | 7.838 | 0.654 | 2.423 | 25.946 |
| 2026-06-22 | 11.000 | 90.909 | 52.388 | 3.438 | 10.000 | 80.000 | 38.602 | 2.228 | -1.210 | -13.786 |
| 2026-06-23 | 11.000 | 72.727 | 4.403 | 0.337 | 8.000 | 75.000 | 11.026 | 0.596 | 0.259 | 6.624 |
| 2026-06-25 | 18.000 | 72.222 | 10.466 | 1.232 | 16.000 | 62.500 | 1.327 | 0.131 | -1.101 | -9.139 |
| 2026-06-26 | 12.000 | 75.000 | 22.955 | 1.680 | 12.000 | 75.000 | 15.477 | 1.206 | -0.474 | -7.478 |
| 2026-06-27 | 21.000 | 76.190 | 24.460 | 3.144 | 19.000 | 78.947 | 25.733 | 3.070 | -0.075 | 1.273 |
| 2026-06-28 | 4.000 | 75.000 | 23.170 | 0.564 | 3.000 | 66.667 | 28.440 | 0.443 | -0.121 | 5.270 |
| 2026-06-29 | 8.000 | 50.000 | -15.037 | -0.708 | 6.000 | 33.333 | -43.906 | -1.565 | -0.858 | -28.869 |
| 2026-06-30 | 12.000 | 75.000 | 17.253 | 1.324 | 12.000 | 66.667 | -0.164 | -0.013 | -1.337 | -17.416 |
| 2026-07-01 | 13.000 | 84.615 | 34.175 | 2.802 | 9.000 | 88.889 | 26.144 | 1.658 | -1.144 | -8.031 |
| 2026-07-02 | 3.000 | 100.000 | 59.160 | 1.115 | 3.000 | 66.667 | 1.489 | 0.029 | -1.086 | -57.671 |
| 2026-07-03 | 7.000 | 71.429 | 6.591 | 0.309 | 8.000 | 75.000 | 8.181 | 0.454 | 0.145 | 1.590 |
| 2026-07-05 | 14.000 | 50.000 | -25.625 | -2.412 | 16.000 | 56.250 | -14.735 | -1.555 | 0.856 | 10.890 |
| 2026-07-06 | 11.000 | 63.636 | 6.050 | 0.399 | 12.000 | 58.333 | -3.629 | -0.264 | -0.663 | -9.680 |
| 2026-07-07 | 7.000 | 85.714 | 39.240 | 1.691 | 8.000 | 87.500 | 33.234 | 1.746 | 0.055 | -6.006 |
| 2026-07-08 | 5.000 | 60.000 | -2.469 | -0.076 | 4.000 | 50.000 | -22.883 | -0.593 | -0.518 | -20.415 |

没有列出的日期表示两版都没有 settled eligible selection，不是 ROI=0。

## 各表达方向

| version | expression | rows | dates | win_rate | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| after_repaired | current_no | 29 | 13 | 72.414 | 18.149 | 2.851 | 15.712 |
| after_repaired | d1_no | 43 | 14 | 74.419 | 28.817 | 3.183 | 11.047 |
| after_repaired | d1_yes | 15 | 11 | 33.333 | 7.219 | -2.219 | -30.735 |
| after_repaired | d2_no | 53 | 16 | 75.472 | 37.922 | 2.078 | 5.480 |
| after_repaired | d2_yes | 19 | 11 | 63.158 | 9.670 | 2.330 | 24.097 |
| before_missing_live | current_no | 11 | 8 | 90.909 | 6.235 | 3.765 | 60.373 |
| before_missing_live | d1_no | 75 | 15 | 72.000 | 48.162 | 5.838 | 12.121 |
| before_missing_live | d1_yes | 4 | 4 | 0.000 | 1.763 | -1.763 | -100.000 |
| before_missing_live | d2_no | 53 | 16 | 75.472 | 37.816 | 2.184 | 5.775 |
| before_missing_live | d2_yes | 29 | 13 | 62.069 | 14.951 | 3.049 | 20.390 |

## YES / NO 汇总

| version | side | rows | dates | win_rate | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- |
| after_repaired | NO | 125 | 16 | 74.400 | 84.887 | 8.113 | 9.557 |
| after_repaired | YES | 34 | 15 | 50.000 | 16.889 | 0.111 | 0.660 |
| before_missing_live | NO | 139 | 16 | 74.820 | 92.214 | 11.786 | 12.781 |
| before_missing_live | YES | 33 | 14 | 54.545 | 16.714 | 1.286 | 7.692 |

## 每天各表达的 PnL 变化

| target_date | current_no_delta_pnl | d1_no_delta_pnl | d1_yes_delta_pnl | d2_no_delta_pnl | d2_yes_delta_pnl | total_delta_pnl |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-21 | -0.204 | 1.299 | 0.508 | 0.288 | 0.532 | 2.423 |
| 2026-06-22 | 0.000 | -0.778 | -0.014 | 0.000 | -0.418 | -1.210 |
| 2026-06-23 | 0.568 | -1.049 | 0.000 | 0.000 | 0.740 | 0.259 |
| 2026-06-25 | 0.388 | -0.617 | 0.035 | -0.907 | 0.000 | -1.101 |
| 2026-06-26 | -0.150 | 0.232 | 0.000 | -0.048 | -0.508 | -0.474 |
| 2026-06-27 | 0.540 | -0.663 | 0.000 | 0.049 | 0.000 | -0.075 |
| 2026-06-28 | 0.000 | 0.000 | 0.458 | -0.579 | 0.000 | -0.121 |
| 2026-06-29 | -0.482 | 0.177 | -0.552 | 0.000 | 0.000 | -0.858 |
| 2026-06-30 | -0.865 | -0.348 | 0.548 | -0.586 | -0.085 | -1.337 |
| 2026-07-01 | 0.209 | -0.835 | 0.000 | 0.000 | -0.518 | -1.144 |
| 2026-07-02 | -0.329 | -0.295 | -0.462 | 0.000 | 0.000 | -1.086 |
| 2026-07-03 | 0.145 | 0.000 | 0.000 | 0.000 | 0.000 | 0.145 |
| 2026-07-05 | -0.465 | 0.739 | -0.422 | 0.502 | 0.502 | 0.856 |
| 2026-07-06 | -0.381 | 0.000 | -0.552 | 0.798 | -0.528 | -0.663 |
| 2026-07-07 | 0.115 | 0.000 | 0.000 | 0.378 | -0.438 | 0.055 |
| 2026-07-08 | 0.000 | -0.518 | 0.000 | 0.000 | 0.000 | -0.518 |

正数表示修复后更好，负数表示修复后更差；这是 PnL delta，不是单表达 ROI。
完整逐日逐表达 rows/win-rate/cost/PnL/ROI 见 `docs/analysis/2026-07/generated/tmax_lineage_before_after_breakdown_v1/daily_expression_comparison.csv`。

## 选单变化来源

| transition | city_days | dates | pnl_before | pnl_after | delta_pnl |
| --- | --- | --- | --- | --- | --- |
| after_only | 27 | 14 | 0.000 | -1.286 | -1.286 |
| before_only | 40 | 13 | 5.215 | 0.000 | -5.215 |
| same_expression | 118 | 16 | 10.522 | 10.727 | 0.205 |
| switched_expression | 14 | 10 | -2.665 | -1.217 | 1.448 |

### 换表达路径

| expression_before | expression_after | rows | delta_pnl |
| --- | --- | --- | --- |
| d2_no | d1_yes | 3 | 0.740 |
| d1_no | d2_no | 2 | 0.716 |
| d2_yes | d2_no | 2 | 0.694 |
| d2_no | current_no | 1 | 0.719 |
| d2_yes | d1_yes | 1 | 0.080 |
| d1_no | current_no | 1 | -0.030 |
| d2_yes | d1_no | 1 | -0.088 |
| current_no | d2_no | 1 | -0.146 |
| d2_yes | current_no | 1 | -0.446 |
| current_no | d1_yes | 1 | -0.791 |

## 8 笔 live 反事实

旧 live 共 8 笔；按修复后的 PIT feature replay，在原 fill price 上仍有 6/8 原表达过门。Busan 7/09 d1 NO 变为负 edge，北京 7/09 d1 NO 为 1.93c、略低于 2c 门。

## Three Gates

- significance: FAIL for selected-trade delta; paired date CI crosses zero.
- baseline: PASS only for the mechanical same-policy A/B; no claim that after beats a market/no-model baseline here.
- forward: PARTIAL; frozen 6/21+ window exists, but complete-ladder/direct-YES repairs have no historical forward yet.
- conclusion: `inconclusive` for PnL superiority; `confirmed` only for restoring live feature/data/execution parity.
