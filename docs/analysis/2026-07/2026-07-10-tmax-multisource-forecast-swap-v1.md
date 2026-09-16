# Tmax Multi-Source Forecast Swap v1

Generated: 2026-07-10

## Question

在 `tmax_distribution_edge` 的同一 exact-book 机会分母上，把原来 assigned GFS/ECMWF forecast max 换成其他 Open-Meteo 模型源，会不会更好？

## Bottom Line

有发现，但还不能直接切 live 模型：多模型 forecast 在 **source quality** 上明显有信息，尤其 ICON/GDPS/区域模型在部分城市优于旧 GFS/ECMWF；但把这些 forecast max 直接当成 tmax 的 forecast-anchor 执行 selector，整体仍然不如现有 tmax probability model。

最实用的结论是：**其他 forecast source 应先作为 tmax 的 source-quality / uncertainty / sizing 特征接入，而不是替换模型主干。**

## Data Snapshot

```json
{
  "bridge_candidates": "docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/expression_candidates.csv",
  "forecast_enrichment_rows": {
    "forecast_rows": 10588,
    "forecast_min_date": "2026-06-02",
    "forecast_max_date": "2026-07-07",
    "forecast_cities": 36,
    "forecast_models": [
      "ecmwf_aifs025_single",
      "ecmwf_ifs025",
      "gem_global",
      "gem_regional",
      "gem_seamless",
      "gfs_global",
      "gfs_seamless",
      "icon_d2",
      "icon_eu",
      "icon_seamless",
      "jma_seamless",
      "meteofrance_arome_france_hd",
      "ncep_aigfs025",
      "ncep_hrrr_conus",
      "ncep_nam_conus",
      "ncep_nbm_conus"
    ],
    "best_asof_rows": 649
  },
  "states": {
    "rows": 5795,
    "min_date": "2026-06-02",
    "max_date": "2026-07-07",
    "cities": 36,
    "scopes": {
      "dev_cv": 3743,
      "verified_forward": 2048,
      "extension_forward": 4
    }
  },
  "probability_rows": 70495,
  "selected_rows": 11525,
  "policy": {
    "ask_floor": 0.4,
    "ask_ceiling": 0.99,
    "edge_threshold_fee_adjusted": 0.02,
    "active_expressions": [
      "current_no",
      "d1_no",
      "d2_no",
      "d1_yes",
      "d2_yes"
    ],
    "first_city_day": true
  },
  "generated": {
    "probability_rows": "docs/analysis/2026-07/generated/tmax_multisource_forecast_swap_v1/probability_rows.csv",
    "score_summary": "docs/analysis/2026-07/generated/tmax_multisource_forecast_swap_v1/score_summary.csv",
    "selected_trades": "docs/analysis/2026-07/generated/tmax_multisource_forecast_swap_v1/selected_trades.csv",
    "trade_summary": "docs/analysis/2026-07/generated/tmax_multisource_forecast_swap_v1/trade_summary.csv",
    "source_quality": "docs/analysis/2026-07/generated/tmax_multisource_forecast_swap_v1/source_quality_on_tmax_denominator.csv",
    "report": "docs/analysis/2026-07/2026-07-10-tmax-multisource-forecast-swap-v1.md"
  }
}
```

Important limitation: `historical_forecast_enrichment_bias_v1` 是 city-date 级 historical daily max 回放，不是完整决策时刻 PIT forecast version。这里是 source-swap 反事实和模型筛选，不是 live 许可。

## Source Quality On Tmax Denominator

| model_key | model_label | rows | dates | cities | mae_f | bias_f | rmse_f |
| --- | --- | --- | --- | --- | --- | --- | --- |
| icon_d2 | ICON-D2 | 62 | 33 | 2 | 1.1161 | 0.4613 | 1.4100 |
| meteofrance_arome_france_hd | AROME HD | 92 | 33 | 3 | 1.1641 | -0.3707 | 1.4781 |
| icon_eu | ICON-EU | 236 | 33 | 8 | 1.1936 | 0.7614 | 1.4587 |
| ncep_hrrr_conus | HRRR | 259 | 32 | 10 | 1.5216 | 0.5687 | 2.0774 |
| icon_seamless | ICON | 1008 | 34 | 36 | 1.8208 | 0.9464 | 2.3480 |
| ncep_nam_conus | NAM | 259 | 32 | 10 | 1.8228 | -0.8436 | 2.4308 |
| ncep_nbm_conus | NBM | 259 | 32 | 10 | 1.9328 | 0.9390 | 2.4656 |
| gem_regional | RDPS | 349 | 33 | 13 | 2.0132 | 0.2395 | 2.5932 |
| gem_seamless | GEM | 1008 | 34 | 36 | 2.1452 | 0.9175 | 2.7550 |
| gfs_seamless | GFS | 1008 | 34 | 36 | 2.1834 | 0.6251 | 2.9022 |
| gem_global | GDPS | 1008 | 34 | 36 | 2.2027 | 1.2477 | 2.8355 |
| gfs_global | GFS Global | 1008 | 34 | 36 | 2.4335 | 0.1353 | 3.2398 |
| ecmwf_ifs025 | ECMWF | 1008 | 34 | 36 | 2.4819 | 0.8133 | 3.2060 |
| ncep_aigfs025 | AI-GFS | 1008 | 34 | 36 | 2.6014 | 1.2742 | 3.1889 |
| ecmwf_aifs025_single | ECMWF AIFS | 1008 | 34 | 36 | 2.7302 | 2.0123 | 3.5122 |
| jma_seamless | JMA | 1008 | 34 | 36 | 3.4946 | 2.8982 | 4.5017 |

## Forecast-Anchor Probability Score

这里只看四桶 `current/d1/d2/tail` 的 forecast-anchor proper scoring。数值越低越好。

| variant | rows | dates | cities | logloss | logloss_delta_vs_gfs_seamless | brier | top1 | winner_prob |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| icon_eu | 493 | 14 | 8 | 0.7752 | -0.9882 | 0.4541 | 0.5598 | 0.4980 |
| icon_d2 | 130 | 14 | 2 | 0.8546 | -0.9088 | 0.4914 | 0.5692 | 0.4748 |
| meteofrance_arome_france_hd | 169 | 14 | 3 | 1.0232 | -0.7402 | 0.5638 | 0.5562 | 0.4156 |
| best_full_forecast_f | 2048 | 15 | 36 | 1.0406 | -0.7229 | 0.5271 | 0.5933 | 0.4877 |
| best_asof_forecast_f | 2048 | 15 | 36 | 1.2057 | -0.5577 | 0.5546 | 0.5439 | 0.4775 |
| icon_seamless | 2048 | 15 | 36 | 1.2182 | -0.5452 | 0.5653 | 0.5420 | 0.4854 |
| gem_global | 2048 | 15 | 36 | 1.5137 | -0.2497 | 0.6112 | 0.5327 | 0.4623 |
| gem_seamless | 2048 | 15 | 36 | 1.5791 | -0.1843 | 0.6236 | 0.5415 | 0.4532 |
| ecmwf_aifs025_single | 2048 | 15 | 36 | 1.6405 | -0.1229 | 0.6177 | 0.5342 | 0.4897 |
| ncep_nbm_conus | 499 | 14 | 10 | 1.7559 | -0.0076 | 0.6091 | 0.5772 | 0.5136 |
| ncep_hrrr_conus | 499 | 14 | 10 | 1.7568 | -0.0067 | 0.5003 | 0.6172 | 0.5619 |
| gfs_seamless | 2048 | 15 | 36 | 1.7634 | 0.0000 | 0.6281 | 0.5103 | 0.4679 |
| ncep_aigfs025 | 2048 | 15 | 36 | 1.7846 | 0.0211 | 0.6783 | 0.4893 | 0.4486 |
| ecmwf_ifs025 | 2048 | 15 | 36 | 1.8409 | 0.0775 | 0.6407 | 0.5181 | 0.4600 |
| gfs_global | 2048 | 15 | 36 | 2.2221 | 0.4587 | 0.7072 | 0.4604 | 0.4290 |
| jma_seamless | 2048 | 15 | 36 | 2.3304 | 0.5669 | 0.7263 | 0.5073 | 0.4542 |
| gem_regional | 693 | 14 | 13 | 2.5124 | 0.7490 | 0.6856 | 0.5512 | 0.4497 |
| ncep_nam_conus | 499 | 14 | 10 | 2.8376 | 1.0741 | 0.7272 | 0.4649 | 0.4403 |

## Execution Replay Proxy

规则：active expressions=['current_no', 'd1_no', 'd2_no', 'd1_yes', 'd2_yes'], ask in [0.4,0.99], fee-adjusted edge >= 0.02, 每 city-day 第一笔。

### Verified Forward 2026-06-21+

| variant | rows | dates | cities | win_rate | avg_ask | pnl_net | roi_net | roi_net_ci_low | roi_net_ci_high | expr_mix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| icon_d2 | 17 | 12 | 2 | 0.6471 | 0.5269 | 1.8422 | 0.2012 | -0.1207 | 0.5507 | {"d1_no": 7, "d2_no": 10} |
| meteofrance_arome_france_hd | 25 | 13 | 3 | 0.6800 | 0.5781 | 2.2748 | 0.1545 | -0.1331 | 0.3870 | {"current_no": 2, "d1_no": 12, "d2_no": 11} |
| original_tmax_model | 162 | 15 | 36 | 0.6914 | 0.6332 | 7.7168 | 0.0740 | -0.0331 | 0.1840 | {"current_no": 29, "d1_no": 47, "d1_yes": 13, "d2_no": 54, "d2_yes": 19} |
| ncep_hrrr_conus | 80 | 13 | 10 | 0.7500 | 0.7015 | 3.1892 | 0.0561 | -0.0597 | 0.1666 | {"current_no": 8, "d1_no": 17, "d1_yes": 3, "d2_no": 49, "d2_yes": 3} |
| ncep_nam_conus | 83 | 13 | 10 | 0.7349 | 0.7137 | 1.0716 | 0.0179 | -0.0268 | 0.0810 | {"current_no": 13, "d1_no": 17, "d1_yes": 1, "d2_no": 51, "d2_yes": 1} |
| best_asof_forecast_f | 305 | 15 | 36 | 0.6459 | 0.6508 | -4.5315 | -0.0225 | -0.0933 | 0.0478 | {"current_no": 27, "d1_no": 100, "d1_yes": 1, "d2_no": 176, "d2_yes": 1} |
| gfs_seamless | 328 | 15 | 36 | 0.6494 | 0.6620 | -7.3590 | -0.0334 | -0.0841 | 0.0159 | {"current_no": 27, "d1_no": 93, "d1_yes": 3, "d2_no": 202, "d2_yes": 3} |
| best_full_forecast_f | 306 | 15 | 36 | 0.6111 | 0.6335 | -9.9674 | -0.0506 | -0.1396 | 0.0322 | {"current_no": 29, "d1_no": 93, "d1_yes": 3, "d2_no": 179, "d2_yes": 2} |
| gfs_global | 338 | 15 | 36 | 0.6509 | 0.6777 | -12.2858 | -0.0529 | -0.1102 | 0.0158 | {"current_no": 27, "d1_no": 97, "d2_no": 214} |
| gem_regional | 107 | 14 | 13 | 0.6355 | 0.6640 | -4.0561 | -0.0563 | -0.1490 | 0.0404 | {"current_no": 13, "d1_no": 30, "d1_yes": 1, "d2_no": 60, "d2_yes": 3} |
| gem_global | 320 | 15 | 36 | 0.6000 | 0.6326 | -13.7568 | -0.0669 | -0.1293 | -0.0056 | {"current_no": 26, "d1_no": 84, "d1_yes": 2, "d2_no": 205, "d2_yes": 3} |
| gem_seamless | 321 | 15 | 36 | 0.5919 | 0.6302 | -15.6301 | -0.0760 | -0.1346 | -0.0159 | {"current_no": 29, "d1_no": 92, "d1_yes": 1, "d2_no": 196, "d2_yes": 3} |
| ecmwf_ifs025 | 316 | 15 | 36 | 0.6013 | 0.6453 | -17.1333 | -0.0827 | -0.1371 | -0.0261 | {"current_no": 26, "d1_no": 90, "d1_yes": 1, "d2_no": 199} |
| icon_eu | 60 | 13 | 8 | 0.5000 | 0.5336 | -2.7288 | -0.0834 | -0.3123 | 0.1429 | {"current_no": 2, "d1_no": 18, "d2_no": 40} |
| jma_seamless | 321 | 15 | 36 | 0.6012 | 0.6497 | -18.7955 | -0.0887 | -0.1588 | -0.0024 | {"current_no": 16, "d1_no": 75, "d1_yes": 1, "d2_no": 228, "d2_yes": 1} |
| icon_seamless | 307 | 15 | 36 | 0.5896 | 0.6420 | -19.2079 | -0.0959 | -0.1687 | -0.0244 | {"current_no": 20, "d1_no": 83, "d1_yes": 2, "d2_no": 201, "d2_yes": 1} |
| ncep_aigfs025 | 318 | 15 | 36 | 0.5755 | 0.6328 | -21.5104 | -0.1052 | -0.1792 | -0.0332 | {"current_no": 24, "d1_no": 86, "d1_yes": 3, "d2_no": 202, "d2_yes": 3} |
| ecmwf_aifs025_single | 324 | 15 | 36 | 0.5772 | 0.6474 | -26.0280 | -0.1222 | -0.1969 | -0.0487 | {"current_no": 17, "d1_no": 89, "d1_yes": 2, "d2_no": 214, "d2_yes": 2} |
| ncep_nbm_conus | 78 | 13 | 10 | 0.5897 | 0.7063 | -9.7424 | -0.1748 | -0.3297 | -0.0356 | {"current_no": 7, "d1_no": 23, "d1_yes": 2, "d2_no": 46} |

### Dev CV / Pre-2026-06-21

| variant | rows | dates | cities | win_rate | avg_ask | pnl_net | roi_net | roi_net_ci_low | roi_net_ci_high | expr_mix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| icon_d2 | 33 | 19 | 2 | 0.6667 | 0.5426 | 3.7071 | 0.2027 | -0.1116 | 0.5237 | {"d1_no": 12, "d2_no": 21} |
| meteofrance_arome_france_hd | 52 | 19 | 3 | 0.6154 | 0.5699 | 1.7862 | 0.0591 | -0.1353 | 0.2662 | {"current_no": 4, "d1_no": 18, "d2_no": 30} |
| original_tmax_model | 373 | 19 | 36 | 0.6729 | 0.6342 | 10.5338 | 0.0438 | -0.0023 | 0.0850 | {"current_no": 42, "d1_no": 133, "d1_yes": 23, "d2_no": 117, "d2_yes": 58} |
| ncep_hrrr_conus | 152 | 18 | 10 | 0.6776 | 0.6478 | 3.0801 | 0.0308 | -0.0780 | 0.1396 | {"current_no": 15, "d1_no": 35, "d1_yes": 5, "d2_no": 88, "d2_yes": 9} |
| best_asof_forecast_f | 257 | 9 | 36 | 0.6381 | 0.6157 | 3.0318 | 0.0188 | -0.0981 | 0.1153 | {"current_no": 29, "d1_no": 79, "d2_no": 144, "d2_yes": 5} |
| best_full_forecast_f | 576 | 19 | 36 | 0.6233 | 0.6216 | -5.1172 | -0.0141 | -0.0640 | 0.0409 | {"current_no": 52, "d1_no": 173, "d1_yes": 2, "d2_no": 336, "d2_yes": 13} |
| ncep_nam_conus | 157 | 18 | 10 | 0.6688 | 0.6693 | -1.5280 | -0.0143 | -0.1198 | 0.0790 | {"current_no": 12, "d1_no": 50, "d1_yes": 1, "d2_no": 81, "d2_yes": 13} |
| ncep_aigfs025 | 597 | 19 | 36 | 0.6181 | 0.6284 | -12.4624 | -0.0327 | -0.0947 | 0.0271 | {"current_no": 31, "d1_no": 177, "d1_yes": 1, "d2_no": 377, "d2_yes": 11} |
| gfs_global | 587 | 19 | 36 | 0.6371 | 0.6495 | -13.1694 | -0.0340 | -0.0816 | 0.0139 | {"current_no": 43, "d1_no": 177, "d2_no": 360, "d2_yes": 7} |
| gfs_seamless | 584 | 19 | 36 | 0.6199 | 0.6322 | -13.2589 | -0.0353 | -0.0853 | 0.0121 | {"current_no": 43, "d1_no": 165, "d1_yes": 5, "d2_no": 362, "d2_yes": 9} |
| gem_regional | 210 | 19 | 13 | 0.6143 | 0.6290 | -5.2072 | -0.0388 | -0.1376 | 0.0470 | {"current_no": 19, "d1_no": 69, "d1_yes": 3, "d2_no": 108, "d2_yes": 11} |
| gem_global | 594 | 19 | 36 | 0.6010 | 0.6216 | -18.5178 | -0.0493 | -0.1189 | 0.0101 | {"current_no": 44, "d1_no": 155, "d1_yes": 4, "d2_no": 383, "d2_yes": 8} |
| jma_seamless | 595 | 19 | 36 | 0.5966 | 0.6284 | -25.2281 | -0.0663 | -0.1306 | -0.0020 | {"current_no": 25, "d1_no": 152, "d1_yes": 3, "d2_no": 409, "d2_yes": 6} |
| gem_seamless | 590 | 19 | 36 | 0.5932 | 0.6253 | -25.1303 | -0.0670 | -0.1212 | -0.0109 | {"current_no": 50, "d1_no": 167, "d1_yes": 3, "d2_no": 359, "d2_yes": 11} |
| ecmwf_ifs025 | 594 | 19 | 36 | 0.5909 | 0.6229 | -25.3231 | -0.0673 | -0.1349 | -0.0054 | {"current_no": 36, "d1_no": 161, "d1_yes": 5, "d2_no": 387, "d2_yes": 5} |
| icon_eu | 133 | 19 | 8 | 0.5188 | 0.5468 | -5.2698 | -0.0710 | -0.1887 | 0.0320 | {"current_no": 8, "d1_no": 37, "d2_no": 88} |
| ecmwf_aifs025_single | 591 | 19 | 36 | 0.5787 | 0.6166 | -28.7849 | -0.0776 | -0.1359 | -0.0108 | {"current_no": 38, "d1_no": 149, "d1_yes": 2, "d2_no": 394, "d2_yes": 8} |
| icon_seamless | 578 | 19 | 36 | 0.5796 | 0.6194 | -29.1401 | -0.0800 | -0.1382 | -0.0222 | {"current_no": 44, "d1_no": 174, "d1_yes": 3, "d2_no": 346, "d2_yes": 11} |
| ncep_nbm_conus | 155 | 18 | 10 | 0.5935 | 0.6493 | -10.1286 | -0.0992 | -0.1788 | -0.0245 | {"current_no": 5, "d1_no": 33, "d1_yes": 3, "d2_no": 102, "d2_yes": 12} |

## City Notes

Verified forward 中每 variant 至少 2 笔的 city-level selected replay：

| variant | city | rows | wins | roi_net |
| --- | --- | --- | --- | --- |
| best_asof_forecast_f | Wellington | 5 | 5 | 0.5804 |
| best_asof_forecast_f | Tokyo | 7 | 7 | 0.3272 |
| best_asof_forecast_f | Seattle | 5 | 5 | 0.2978 |
| best_asof_forecast_f | Manila | 10 | 9 | 0.2942 |
| best_asof_forecast_f | LA | 5 | 4 | 0.2569 |
| best_asof_forecast_f | Jeddah | 8 | 7 | 0.2242 |
| best_asof_forecast_f | Munich | 8 | 5 | 0.2199 |
| best_asof_forecast_f | SanFrancisco | 7 | 6 | 0.1896 |
| best_asof_forecast_f | Dallas | 8 | 7 | 0.1616 |
| best_asof_forecast_f | Atlanta | 10 | 8 | 0.1594 |
| best_asof_forecast_f | CapeTown | 12 | 9 | 0.1479 |
| best_asof_forecast_f | Miami | 8 | 7 | 0.1438 |
| best_asof_forecast_f | Lucknow | 9 | 7 | 0.1428 |
| best_asof_forecast_f | Warsaw | 6 | 4 | 0.1405 |
| best_asof_forecast_f | Chongqing | 10 | 8 | 0.1254 |
| best_asof_forecast_f | Busan | 13 | 9 | 0.1064 |
| best_asof_forecast_f | Taipei | 8 | 6 | 0.0748 |
| best_asof_forecast_f | Chengdu | 11 | 9 | 0.0315 |
| best_asof_forecast_f | Madrid | 7 | 4 | 0.0132 |
| best_asof_forecast_f | TelAviv | 6 | 3 | -0.0396 |
| best_asof_forecast_f | NYC | 9 | 7 | -0.0524 |
| best_asof_forecast_f | Amsterdam | 9 | 5 | -0.0728 |
| best_asof_forecast_f | Houston | 9 | 6 | -0.0860 |
| best_asof_forecast_f | Ankara | 10 | 5 | -0.1190 |
| best_asof_forecast_f | SaoPaulo | 9 | 5 | -0.1735 |
| best_asof_forecast_f | Beijing | 10 | 6 | -0.1771 |
| best_asof_forecast_f | Shanghai | 8 | 4 | -0.2097 |
| best_asof_forecast_f | Helsinki | 9 | 4 | -0.2108 |
| best_asof_forecast_f | Guangzhou | 11 | 6 | -0.2351 |
| best_asof_forecast_f | Wuhan | 8 | 4 | -0.2953 |
| best_asof_forecast_f | Karachi | 10 | 4 | -0.3097 |
| best_asof_forecast_f | Denver | 8 | 3 | -0.3902 |
| best_asof_forecast_f | Austin | 7 | 3 | -0.4243 |
| best_asof_forecast_f | Istanbul | 8 | 2 | -0.5226 |
| best_asof_forecast_f | BuenosAires | 9 | 2 | -0.5434 |
| best_asof_forecast_f | Singapore | 8 | 2 | -0.5649 |
| best_full_forecast_f | Wellington | 5 | 5 | 0.5804 |
| best_full_forecast_f | Seattle | 5 | 5 | 0.2978 |
| best_full_forecast_f | LA | 5 | 4 | 0.2569 |
| best_full_forecast_f | Manila | 11 | 9 | 0.2311 |
| best_full_forecast_f | Munich | 8 | 5 | 0.2199 |
| best_full_forecast_f | Amsterdam | 9 | 6 | 0.1860 |
| best_full_forecast_f | Atlanta | 10 | 8 | 0.1594 |
| best_full_forecast_f | Miami | 8 | 7 | 0.1438 |
| best_full_forecast_f | Lucknow | 9 | 7 | 0.1428 |
| best_full_forecast_f | CapeTown | 13 | 10 | 0.1250 |
| best_full_forecast_f | Busan | 13 | 9 | 0.1064 |
| best_full_forecast_f | Chongqing | 9 | 7 | 0.0972 |
| best_full_forecast_f | Taipei | 8 | 6 | 0.0748 |
| best_full_forecast_f | Tokyo | 7 | 5 | 0.0726 |
| best_full_forecast_f | Denver | 8 | 6 | 0.0634 |
| best_full_forecast_f | Jeddah | 8 | 6 | 0.0510 |
| best_full_forecast_f | Houston | 9 | 7 | 0.0465 |
| best_full_forecast_f | Chengdu | 11 | 9 | 0.0315 |
| best_full_forecast_f | Warsaw | 4 | 2 | 0.0157 |
| best_full_forecast_f | Madrid | 7 | 4 | 0.0132 |
| best_full_forecast_f | TelAviv | 6 | 3 | -0.0396 |
| best_full_forecast_f | Shanghai | 7 | 4 | -0.1075 |
| best_full_forecast_f | Guangzhou | 11 | 7 | -0.1190 |
| best_full_forecast_f | Helsinki | 10 | 5 | -0.1310 |

## Interpretation

1. `best_asof_forecast_f` 是更合理的研究方向：只用目标日前历史选每城 best model，避免 full-window 偷看未来。它的 forecast quality 有意义，但直接 forecast-anchor selector 仍偏粗。
2. `best_full_forecast_f` 和单日 best-model 表里的大幅改善只能当诊断，不能当策略，因为用了全窗口未来信息。
3. 如果某个城市的 best model 与 assigned GFS/ECMWF 差距很大，tmax 应该把它变成 `source_reliability / forecast_disagreement / forecast_ceiling_uncertainty`，而不是硬切 source。
4. 下一步工程上应把 `best_model_asof`, `best_model_mae`, `assigned_minus_best_forecast`, `model_spread`, `regional_model_available` 写入 tmax live/shadow feature payload，再用现有 tmax model 做 ablation。

## Verdict

```text
significance=FAIL/NA for live replacement
baseline=current tmax probability model
forward=source-quality useful, source-swap selector not confirmed
conclusion=shadow_feature_upgrade_not_live_selector
```
