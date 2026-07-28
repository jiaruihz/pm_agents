# D1 distance=2 NO：数据源 × 置信度 × 城市完整研究 v2

## 结论

全城市结论仍是 inconclusive：删源、按城市挑源都没有改善全 5 源，且全分母 proper score 未打赢 market。城市选择后出现一个更窄候选：预定义 `europe_cloud_break` 五城在 absolute ROI、market-selection excess、early/late 上都为正；去掉 AIFS 后点估略好。但这是看过 5 个 family 后得到的 overlay，是否升级仍取决于 proper-score CI 与新日期 frozen forward。

| policy | fee_adjusted_roi | roi_ci_low | roi_ci_high | roi_delta_vs_all5 | delta_ci_low | delta_ci_high | late_roi | brier_delta_vs_market | logloss_delta_vs_market |
|---|---|---|---|---|---|---|---|---|---|
| equal_all5 | 0.013232 | -0.008598 | 0.035604 | 0.000000 | 0.000000 | 0.000000 | 0.001496 | 0.002488 | 0.020323 |
| global_top3_train | 0.012596 | -0.005013 | 0.028976 | -0.000636 | -0.009220 | 0.008921 | -0.000181 | 0.002721 | 0.027095 |
| physical_only_no_aifs | 0.012228 | -0.009326 | 0.033865 | -0.001004 | -0.003569 | 0.001908 | 0.001291 | 0.002588 | 0.024963 |
| city_best1_train | 0.000980 | -0.017378 | 0.017572 | -0.012251 | -0.025956 | 0.000639 | -0.016771 | 0.008211 | 0.130145 |
| city_best2_train | 0.007724 | -0.011066 | 0.026464 | -0.005507 | -0.015919 | 0.004576 | -0.009206 | 0.003415 | 0.052195 |
| city_inverse_mae_all5 | 0.010274 | -0.008061 | 0.028880 | -0.002958 | -0.008951 | 0.003157 | -0.002085 | 0.002343 | 0.019331 |
| market_only_selection | 0.003627 | -0.003150 | 0.009892 | -0.009604 | -0.032197 | 0.010847 | 0.008127 | 0.000000 | 0.000000 |
| mechanical_half_distance2 | -0.000396 | -0.012373 | 0.011616 | -0.013628 | -0.028894 | 0.001701 | -0.014561 | 0.000000 | 0.000000 |

## Source 结论

| model_key | eligible_cities | train_mae_f | train_rmse_f | train_abs_bias_f |
|---|---|---|---|---|
| icon_seamless | 43 | 2.291827 | 3.077394 | 1.241704 |
| gfs_global | 43 | 2.742517 | 3.543718 | 1.767348 |
| ecmwf_ifs025 | 43 | 2.910063 | 3.673544 | 2.134896 |
| ecmwf_aifs025_single | 43 | 3.069203 | 3.816746 | 2.383393 |
| jma_seamless | 43 | 3.837031 | 4.547170 | 2.855591 |

训练期各城市最优来源计数：

| model_key | best_for_cities |
|---|---|
| icon_seamless | 20 |
| gfs_global | 10 |
| ecmwf_aifs025_single | 6 |
| ecmwf_ifs025 | 6 |
| jma_seamless | 1 |

- all5 ROI 1.3232%，95% CI [-0.8598%, 3.5604%]。
- city_best2_train ROI 0.7724%，相对 all5 -0.5507%，paired 95% CI [-1.5919%, 0.4576%]。
- 本轮 source policy/ablation 共 15 个 challenger；BH 多重检验后没有相对 all5 显著改善者。

Europe-cloud-break 内 source A/B：

| policy | fee_adjusted_roi | roi_ci_low | roi_ci_high | roi_delta_vs_all5 | delta_ci_low | delta_ci_high | late_roi | brier_delta_vs_market | brier_delta_ci_low | brier_delta_ci_high | logloss_delta_vs_market |
|---|---|---|---|---|---|---|---|---|---|---|---|
| equal_all5 | 0.083239 | 0.044755 | 0.121867 | 0.000000 | 0.000000 | 0.000000 | 0.090757 | -0.004764 | -0.015624 | 0.005595 | -0.006010 |
| global_top3_train | 0.066820 | 0.024358 | 0.117800 | -0.016418 | -0.056548 | 0.018741 | 0.069995 | -0.003410 | -0.014316 | 0.006664 | -0.006126 |
| physical_only_no_aifs | 0.086149 | 0.044511 | 0.134344 | 0.002910 | -0.018802 | 0.025573 | 0.077452 | -0.005694 | -0.017820 | 0.005757 | -0.010739 |
| market_only_selection | -0.011352 | -0.037320 | 0.013031 | -0.094591 | -0.141146 | -0.051902 | 0.005581 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| mechanical_half_distance2 | 0.029220 | -0.001846 | 0.056647 | -0.054019 | -0.102858 | -0.011235 | 0.029257 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

## 置信度

以下均为 holdout 描述性切片，不作为已冻结 gate：

| dimension | slice | baskets | target_dates | fee_adjusted_roi | roi_ci_low | roi_ci_high | market_selection_roi | roi_delta_vs_market_selection | market_delta_ci_low | market_delta_ci_high | early_roi | late_roi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| vote_bucket | 1/5 | 26 | 15 | 0.011601 | -0.024979 | 0.042373 | 0.015567 | -0.003966 | -0.055641 | 0.034784 | 0.018199 | 0.005587 |
| vote_bucket | 2/5 | 170 | 19 | -0.008124 | -0.068027 | 0.034601 | -0.008661 | 0.000537 | -0.038220 | 0.032954 | -0.037345 | 0.025884 |
| vote_bucket | 3/5 | 392 | 19 | 0.002860 | -0.034499 | 0.036769 | 0.009491 | -0.006631 | -0.044578 | 0.028369 | 0.034759 | -0.038296 |
| vote_bucket | 4/5 | 410 | 20 | 0.030330 | 0.000307 | 0.058123 | 0.006533 | 0.023796 | -0.007372 | 0.053097 | 0.039594 | 0.018712 |
| vote_bucket | 5/5 | 192 | 19 | 0.018553 | -0.033233 | 0.059997 | -0.005736 | 0.024289 | -0.014143 | 0.059988 | 0.015340 | 0.022075 |
| edge_bucket | 0<edge<=1c | 213 | 20 | 0.009504 | -0.004098 | 0.022741 | 0.014427 | -0.004923 | -0.018394 | 0.007796 | 0.005070 | 0.014476 |
| edge_bucket | edge<=0 | 576 | 20 | -0.002359 | -0.020989 | 0.013773 | 0.003892 | -0.006252 | -0.020922 | 0.006207 | -0.003756 | -0.000777 |
| edge_bucket | edge>1c | 401 | 19 | 0.044019 | -0.018187 | 0.101504 | -0.002943 | 0.046962 | -0.012755 | 0.106923 | 0.076483 | -0.003810 |
| prob_disagreement_tercile | high_disagreement | 397 | 19 | 0.047152 | -0.028326 | 0.114562 | 0.004920 | 0.042232 | -0.026779 | 0.105902 | 0.073393 | 0.003995 |
| prob_disagreement_tercile | low_disagreement | 397 | 20 | -0.000412 | -0.015947 | 0.010888 | 0.002565 | -0.002978 | -0.021124 | 0.013353 | 0.004163 | -0.005280 |
| prob_disagreement_tercile | mid_disagreement | 396 | 20 | -0.001191 | -0.022679 | 0.017755 | 0.003468 | -0.004659 | -0.022273 | 0.012355 | -0.008724 | 0.007060 |
| edge_margin_tercile | high_margin | 397 | 19 | 0.043178 | -0.004516 | 0.086504 | 0.001130 | 0.042048 | -0.000665 | 0.083566 | 0.052094 | 0.029865 |
| edge_margin_tercile | low_margin | 397 | 20 | -0.000108 | -0.023100 | 0.019375 | 0.007170 | -0.007278 | -0.029209 | 0.012410 | 0.010699 | -0.010741 |
| edge_margin_tercile | mid_margin | 396 | 20 | 0.000051 | -0.026482 | 0.024527 | 0.002441 | -0.002390 | -0.031387 | 0.026247 | 0.005853 | -0.007302 |

- `4/5` vote组 absolute ROI 为正，但相对 market-selection 的 CI 跨 0；`5/5` 没有更高，故 unanimity 不单调。
- 收益反而集中在 high-disagreement / high-margin，说明这里的“模型分歧”更像价格/尾部机会强度，不是低风险置信度；不能把低 disagreement 设成 gate。
- `edge>1c` early 为正、late 转负，正 edge 也没有 frozen-forward 稳定性。

## 城市与训练期准确度

| slice | baskets | cities | fee_adjusted_roi | roi_ci_low | roi_ci_high | market_selection_roi | roi_delta_vs_market_selection | market_delta_ci_low | market_delta_ci_high | brier_delta_vs_market | early_roi | late_roi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| train_accurate | 366 | 14 | 0.021777 | -0.018372 | 0.061781 | 0.005909 | 0.015868 | -0.021862 | 0.052703 | 0.003295 | 0.035742 | 0.003640 |
| train_middle | 412 | 14 | 0.030659 | -0.002416 | 0.057945 | 0.000675 | 0.029984 | 0.009510 | 0.049108 | -0.001854 | 0.024733 | 0.037165 |
| train_weak | 412 | 15 | -0.012489 | -0.052122 | 0.022766 | 0.004608 | -0.017097 | -0.053822 | 0.019314 | 0.006114 | 0.009477 | -0.040638 |

训练期最准确城市层 ROI 2.1777%，95% CI [-1.8372%, 6.1781%]；准确度分层没有形成单调关系。train-middle 相对 market-selection 有正 excess，但 absolute ROI CI 仍擦过 0；train-weak late 为负且 proper score 更差。因此训练 MAE 可作 soft reliability，不足以生成 allowlist。

预定义 climate family：

| slice | baskets | cities | fee_adjusted_roi | roi_ci_low | roi_ci_high | roi_q_bh | market_selection_roi | roi_delta_vs_market_selection | market_delta_ci_low | market_delta_ci_high | market_delta_q_bh | brier_delta_vs_market | brier_delta_ci_low | brier_delta_ci_high | early_roi | late_roi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| continental_dry_hot | 231 | 8 | 0.007906 | -0.035610 | 0.047365 | 0.765600 | 0.002813 | 0.005093 | -0.034454 | 0.044895 | 0.846400 | 0.002243 | -0.003547 | 0.007984 | 0.036899 | -0.022434 |
| europe_cloud_break | 139 | 5 | 0.083239 | 0.045258 | 0.122127 | 0.000000 | -0.011352 | 0.094591 | 0.051845 | 0.141784 | 0.000000 | -0.004764 | -0.016234 | 0.005723 | 0.077495 | 0.090757 |
| humid_low_latitude | 351 | 13 | -0.007676 | -0.057901 | 0.037787 | 0.765600 | 0.001062 | -0.008738 | -0.047461 | 0.029653 | 0.846400 | 0.002238 | -0.001943 | 0.006291 | -0.009389 | -0.005335 |
| southern_or_maritime | 266 | 10 | -0.007304 | -0.046036 | 0.029455 | 0.765600 | -0.001452 | -0.005852 | -0.047057 | 0.031372 | 0.846400 | 0.007131 | 0.003180 | 0.011932 | 0.007735 | -0.026184 |
| unmapped | 203 | 7 | 0.036147 | 0.006690 | 0.068772 | 0.025000 | 0.025776 | 0.010370 | -0.018828 | 0.039973 | 0.846400 | 0.002082 | -0.003604 | 0.007634 | 0.050710 | 0.020276 |

`europe_cloud_break` ROI 8.3239%，相对 market-only +9.4591%；交易层显著且五城聚合 early/late 都为正，但 proper-score CI 跨 0。

Europe-cloud-break 单城贡献：

| city | baskets | active_days | fee_adjusted_roi | roi_ci_low | roi_ci_high | market_selection_roi | roi_delta_vs_market_selection | early_roi | late_roi | brier_delta_vs_market |
|---|---|---|---|---|---|---|---|---|---|---|
| Munich | 30 | 17 | 0.143585 | 0.061385 | 0.250485 | 0.044417 | 0.099167 | 0.105797 | 0.184046 | -0.006740 |
| Amsterdam | 31 | 18 | 0.114318 | 0.054527 | 0.189000 | -0.037859 | 0.152177 | 0.115913 | 0.112387 | -0.012435 |
| Warsaw | 20 | 12 | 0.067254 | -0.065423 | 0.208580 | 0.005928 | 0.061326 | 0.112913 | -0.386100 | -0.000831 |
| Madrid | 28 | 16 | 0.052073 | -0.082884 | 0.176572 | -0.083838 | 0.135911 | 0.013431 | 0.087147 | -0.006068 |
| Helsinki | 30 | 18 | 0.031584 | -0.056523 | 0.116405 | 0.008622 | 0.022962 | 0.016050 | 0.046517 | 0.003735 |

Europe-cloud-break leave-one-city-out：

| excluded_city | baskets | fee_adjusted_roi | roi_ci_low | roi_ci_high | market_selection_roi | roi_delta_vs_market_selection | market_delta_ci_low | market_delta_ci_high | early_roi | late_roi |
|---|---|---|---|---|---|---|---|---|---|---|
| Amsterdam | 108 | 0.074134 | 0.028465 | 0.123885 | -0.003547 | 0.077681 | 0.033119 | 0.130779 | 0.066731 | 0.084050 |
| Helsinki | 109 | 0.098578 | 0.052974 | 0.145988 | -0.017133 | 0.115711 | 0.059772 | 0.176546 | 0.092661 | 0.107083 |
| Madrid | 111 | 0.090351 | 0.049006 | 0.137897 | 0.004692 | 0.085659 | 0.039522 | 0.135979 | 0.089331 | 0.091805 |
| Munich | 109 | 0.066842 | 0.018757 | 0.114772 | -0.026787 | 0.093629 | 0.038045 | 0.157088 | 0.070645 | 0.061581 |
| Warsaw | 119 | 0.085950 | 0.049581 | 0.121248 | -0.014402 | 0.100352 | 0.057002 | 0.151866 | 0.066774 | 0.105827 |

剔除任一城市后组合 ROI 仍为正，说明 family 结果不是单城独占；但 Amsterdam + Munich 合计贡献约 68% PnL，且 Warsaw late 明显不稳，仍不足以据此生成五城 live allowlist。

Region：

| slice | baskets | cities | fee_adjusted_roi | roi_ci_low | roi_ci_high | roi_q_bh | market_selection_roi | roi_delta_vs_market_selection | market_delta_q_bh | brier_delta_vs_market | early_roi | late_roi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AF | 34 | 1 | -0.057128 | -0.227560 | 0.076660 | 0.590800 | -0.020403 | -0.036725 | 0.848400 | 0.003743 | -0.114991 | 0.008829 |
| AS | 354 | 13 | 0.005473 | -0.042243 | 0.052468 | 0.823600 | 0.001252 | 0.004221 | 0.858400 | 0.001338 | 0.016534 | -0.009518 |
| EU | 295 | 10 | 0.063478 | 0.024551 | 0.098027 | 0.007000 | 0.010518 | 0.052960 | 0.047600 | 0.000344 | 0.076129 | 0.048222 |
| ME | 72 | 3 | 0.061385 | -0.030580 | 0.141179 | 0.402267 | -0.008017 | 0.069402 | 0.277200 | -0.002222 | 0.029307 | 0.087022 |
| OC | 23 | 1 | 0.097816 | 0.024787 | 0.197172 | 0.000000 | 0.025187 | 0.072629 | 0.330400 | -0.003020 | 0.144713 | 0.003816 |
| SA | 105 | 4 | -0.025400 | -0.078006 | 0.024244 | 0.497840 | 0.013008 | -0.038408 | 0.277200 | 0.002410 | -0.026183 | -0.024567 |
| US | 307 | 11 | -0.020043 | -0.058984 | 0.018564 | 0.497840 | 0.000230 | -0.020273 | 0.461440 | 0.007280 | -0.000009 | -0.044478 |

单城结果另存 `city_summary.csv`。单城 winner 即使通过 ROI 与 market-selection 多重检验，也仍需 proper-score 与新日期复核；因此本轮不生成 city allowlist。

## Signal / evidence funnel

- raw full-ladder：1338 baskets / 20 target dates / 47 cities。
- distance=2 executable + settled：1336 baskets / 20 dates（至少一侧可执行）。
- two-sided distance=2 paired expression：1298 baskets；其余 38 只有一侧可执行，属于 expression coverage gap。
- all-5-source calibration：1190 baskets / 43 cities；另 108 paired baskets 因冻结训练残差不足而缺失。
- normalized market probability coverage：1190 baskets；另 0 baskets 是盘口归一化 coverage gap，不是策略筛除。
- fixed paired denominator：1190 baskets / 20 dates / 43 cities。
- 这是 opportunity-level research replay，不是 fill；actual fill 环为 0。

## 数据完整性自检

- candidate rows=2380，应为 paired baskets×2=2380。
- candidate pair violations=0；duplicate candidate keys=0。
- finite source probabilities=11900 / 11900；non-binary settlements=0。
- settlement 与 executable quote 均来自同一 immutable snapshot denominator；未同步或重建 canonical DB。

## 三门与动作

- significance=FAIL：全 5 源与 source-selected policy 的 ROI CI 均跨 0。
- baseline=FAIL（全城市）：logloss 显著劣于 market。`europe_cloud_break` 的交易表达打赢 market-only / mechanical，但 Brier/logloss delta CI 仍跨 0。
- forward=FAIL/NA：全 5 源 late ROI 仅 +0.15%；Europe family early/late 同号，但 family 是看过本轮 holdout 后选出，尚无新日期 frozen forward。
- conclusion=全城市 inconclusive；Europe-cloud-break 为`zero-notional shadow_candidate hypothesis`，不改 live。置信度与单城结果只记录连续 score，不转 hard gate。

## 覆盖环

- 已覆盖：描述性绩效、target-date bootstrap、source 判别、概率分布、组合相关日期 block、同分母基准。
- 未覆盖：真实 fill/queue、容量、迁盘后 8 个 target dates 的 full-ladder raw（forecast 已补全但 JRS 文件权限仍阻断）。
