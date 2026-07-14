# Peak-Runway Current-NO v1

> Cheap current-NO pass-through on corrected expression-specific PIT states; research-only; zero notional.

## 数据快照

- canonical DB `/Users/deepsleep/projects/pm_agents/runtime/weather.db` mtime `2026-07-14T08:00:04.566763+00:00`; fact candidates `54054`, fact trades `4574`, unsettled `29`, missing_bracket `0`.
- settlement `2026-05-04..2026-07-12` / `70` dates / `49` cities.
- same-snapshot decision states `26383`; executable cheap-current-NO states `1511` / `24` dates / `44` cities.
- observation path is first-seen/as-of; entry quote/depth is from the same saved snapshot; official Weather taker fee is included.

## 核心逻辑

这不是 generic cross 后无差别买 NO。候选先要求 current NO ask 1c..35c（市场锚定 current exact bracket），再看新高是否仍 fresh、forecast peak 是否尚未过去、forecast 是否仍高于 running max、最近 1h 是否继续升温。最终赚的是后续任何一次升温把 current exact bracket 打穿。

## 结论

- **BUY current NO 应否决。** post-hypothesis `16` trades / `9` dates，fee-adjusted ROI `-68.61%`，date-bootstrap 95% CI `[-100.00%, -8.66%]`；相对同日同价 cheap-NO baseline 的 excess ROI `-62.73%`，CI `[-100.01%, -20.04%]`。这不是“数据还不够所以先等等”，而是该方向在纠正分母后明显反向。
- **相反的 BUY current YES 只够建立 shadow 假设。** 同一物理状态 post-hypothesis `16` trades / `9` dates，ROI `+10.01%`，95% CI `[-4.44%, +19.70%]`；相对 baseline excess `+15.64%`，CI `[+5.75%, +24.46%]`。绝对收益 CI 仍跨 0，且规则为事后提出，不能 live。
- Walk-forward 中加入 path/forecast 后的 logloss 与 Brier 都劣于只用盘口，说明这些粗物理特征没有提供可交易的增量概率；市场更像是在给“当前档最终保持”的概率定价，而非机械地漏算后续升温。

## Funnel

| stage | rows |
| --- | --- |
| raw_decision_groups | 380576 |
| canonical_settlement_available | 380386 |
| missing_pit_running_max | 274858 |
| running_from_embedded_history | 105468 |
| running_outside_saved_ladder | 59542 |
| current_anchor_mapped | 45986 |
| d1_rung_absent_but_current_retained | 835 |
| running_from_saved_snapshot | 60 |
| missing_canonical_settlement | 190 |
| deduplicated_expression_states | 26383 |
| generic_cross_events | 1075 |
| cheap_no_executable_states | 1511 |
| cheap_no_dates | 24 |
| cheap_no_cities | 44 |
| model_walk_forward_states | 672 |

## Rule / model results

| variant | side | scope | rows | dates | cities | avg_ask | win_rate | roi | roi_ci_low | roi_ci_high | bonferroni_roi_ci_low | bonferroni_roi_ci_high | baseline_roi | date_equal_excess_roi | excess_ci_low | excess_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cheap_no_all_state | NO | all | 291 | 24 | 44 | 0.1887 | 0.1443 | -0.2627 | -0.4408 | -0.0569 | -0.5114 | 0.0686 | -0.2627 | 0.0000 | 0.0000 | 0.0000 |
| cheap_no_all_state | NO | development | 137 | 8 | 39 | 0.1999 | 0.1168 | -0.4367 | -0.6570 | -0.2044 | -0.7366 | -0.1007 | -0.4367 | 0.0000 | 0.0000 | 0.0000 |
| cheap_no_all_state | NO | post_hypothesis | 154 | 16 | 36 | 0.1787 | 0.1688 | -0.0897 | -0.3469 | 0.1849 | -0.4657 | 0.3364 | -0.0897 | 0.0000 | 0.0000 | 0.0000 |
| cheap_no_generic_cross | NO | all | 64 | 18 | 23 | 0.1502 | 0.1406 | -0.0989 | -0.6255 | 0.4785 | -0.8228 | 0.7663 | -0.3400 | -0.1425 | -0.4392 | 0.1276 |
| cheap_no_generic_cross | NO | development | 15 | 6 | 10 | 0.1772 | 0.0667 | -0.6376 | -1.0000 | -0.3194 | -1.0000 | -0.2583 | -0.4695 | -0.3947 | -0.7059 | -0.0782 |
| cheap_no_generic_cross | NO | post_hypothesis | 49 | 12 | 17 | 0.1419 | 0.1633 | 0.1068 | -0.5689 | 0.8277 | -0.8217 | 1.1794 | -0.1629 | -0.0164 | -0.4286 | 0.3265 |
| fresh_high | NO | all | 182 | 23 | 38 | 0.1649 | 0.1154 | -0.3262 | -0.5767 | -0.0304 | -0.6612 | 0.1273 | -0.2722 | -0.0919 | -0.3052 | 0.0643 |
| fresh_high | NO | development | 86 | 8 | 32 | 0.1803 | 0.0930 | -0.5028 | -0.8130 | -0.1311 | -0.9126 | 0.0824 | -0.4367 | -0.0220 | -0.1714 | 0.1497 |
| fresh_high | NO | post_hypothesis | 96 | 15 | 28 | 0.1512 | 0.1354 | -0.1378 | -0.4962 | 0.2885 | -0.6817 | 0.6312 | -0.1049 | -0.1291 | -0.4222 | 0.0891 |
| peak_runway | NO | all | 25 | 12 | 12 | 0.2059 | 0.1200 | -0.4383 | -1.0000 | 0.0229 | -1.0000 | 0.1576 | -0.2687 | -0.4691 | -0.8190 | -0.0989 |
| peak_runway | NO | development | 9 | 3 | 7 | 0.2310 | 0.2222 | -0.0721 | -1.0000 | 0.3056 | -1.0000 | 0.3056 | -0.5912 | 0.0056 | -0.3057 | 0.5165 |
| peak_runway | NO | post_hypothesis | 16 | 9 | 9 | 0.1918 | 0.0625 | -0.6861 | -1.0000 | -0.0866 | -1.0000 | 0.1609 | -0.0996 | -0.6273 | -1.0001 | -0.2004 |
| active_warming_peak_runway | NO | all | 25 | 12 | 12 | 0.2059 | 0.1200 | -0.4383 | -1.0000 | 0.0229 | -1.0000 | 0.1576 | -0.2687 | -0.4691 | -0.8190 | -0.0989 |
| active_warming_peak_runway | NO | development | 9 | 3 | 7 | 0.2310 | 0.2222 | -0.0721 | -1.0000 | 0.3056 | -1.0000 | 0.3056 | -0.5912 | 0.0056 | -0.3057 | 0.5165 |
| active_warming_peak_runway | NO | post_hypothesis | 16 | 9 | 9 | 0.1918 | 0.0625 | -0.6861 | -1.0000 | -0.0866 | -1.0000 | 0.1609 | -0.0996 | -0.6273 | -1.0001 | -0.2004 |
| walk_forward_market_path_edge05 | NO | all | 11 | 6 | 9 | 0.1576 | 0.0000 | -1.0000 | -1.0000 | -1.0000 | -1.0000 | -1.0000 | -0.1839 | -0.8486 | -1.2916 | -0.3628 |
| walk_forward_market_path_edge05 | NO | development | 0 | 0 | 0 | NA | NA | NA | NA | NA | NA | NA | NA | NA | NA | NA |
| walk_forward_market_path_edge05 | NO | post_hypothesis | 11 | 6 | 9 | 0.1576 | 0.0000 | -1.0000 | -1.0000 | -1.0000 | -1.0000 | -1.0000 | -0.1839 | -0.8486 | -1.2916 | -0.3628 |
| cheap_no_all_state_yes_reversal | YES | all | 289 | 24 | 44 | 0.8646 | 0.8547 | -0.0176 | -0.0642 | 0.0254 | -0.0916 | 0.0434 | -0.0176 | 0.0000 | 0.0000 | 0.0000 |
| cheap_no_all_state_yes_reversal | YES | development | 136 | 8 | 39 | 0.8547 | 0.8824 | 0.0254 | -0.0341 | 0.0876 | -0.0611 | 0.1126 | 0.0254 | 0.0000 | 0.0000 | 0.0000 |
| cheap_no_all_state_yes_reversal | YES | post_hypothesis | 153 | 16 | 36 | 0.8734 | 0.8301 | -0.0551 | -0.1116 | -0.0008 | -0.1481 | 0.0253 | -0.0551 | 0.0000 | 0.0000 | 0.0000 |
| cheap_no_generic_cross_yes_reversal | YES | all | 63 | 17 | 23 | 0.8873 | 0.8571 | -0.0390 | -0.1541 | 0.0546 | -0.2230 | 0.0849 | 0.0025 | -0.0056 | -0.0648 | 0.0586 |
| cheap_no_generic_cross_yes_reversal | YES | development | 15 | 6 | 10 | 0.8631 | 0.9333 | 0.0745 | 0.0089 | 0.1861 | -0.0098 | 0.2303 | 0.0324 | 0.0586 | -0.0363 | 0.1916 |
| cheap_no_generic_cross_yes_reversal | YES | post_hypothesis | 48 | 11 | 17 | 0.8949 | 0.8333 | -0.0732 | -0.2274 | 0.0353 | -0.3182 | 0.0764 | -0.0342 | -0.0406 | -0.1056 | 0.0106 |
| fresh_high_yes_reversal | YES | all | 181 | 23 | 38 | 0.8823 | 0.8840 | -0.0035 | -0.0624 | 0.0481 | -0.0926 | 0.0664 | -0.0154 | 0.0196 | -0.0363 | 0.0935 |
| fresh_high_yes_reversal | YES | development | 85 | 8 | 32 | 0.8696 | 0.9059 | 0.0355 | -0.0461 | 0.1074 | -0.0904 | 0.1316 | 0.0254 | -0.0036 | -0.0382 | 0.0249 |
| fresh_high_yes_reversal | YES | post_hypothesis | 96 | 15 | 28 | 0.8936 | 0.8646 | -0.0372 | -0.1169 | 0.0295 | -0.1752 | 0.0621 | -0.0513 | 0.0320 | -0.0504 | 0.1376 |
| peak_runway_yes_reversal | YES | all | 25 | 12 | 12 | 0.8343 | 0.8800 | 0.0466 | -0.0775 | 0.1882 | -0.1165 | 0.2012 | -0.0169 | 0.1188 | 0.0260 | 0.2034 |
| peak_runway_yes_reversal | YES | development | 9 | 3 | 7 | 0.8132 | 0.7778 | -0.0521 | -0.1794 | 0.2018 | -0.1794 | 0.2018 | 0.0669 | 0.0061 | -0.1583 | 0.1492 |
| peak_runway_yes_reversal | YES | post_hypothesis | 16 | 9 | 9 | 0.8461 | 0.9375 | 0.1001 | -0.0444 | 0.1970 | -0.1010 | 0.2065 | -0.0544 | 0.1564 | 0.0575 | 0.2446 |
| active_warming_peak_runway_yes_reversal | YES | all | 25 | 12 | 12 | 0.8343 | 0.8800 | 0.0466 | -0.0775 | 0.1882 | -0.1165 | 0.2012 | -0.0169 | 0.1188 | 0.0260 | 0.2034 |
| active_warming_peak_runway_yes_reversal | YES | development | 9 | 3 | 7 | 0.8132 | 0.7778 | -0.0521 | -0.1794 | 0.2018 | -0.1794 | 0.2018 | 0.0669 | 0.0061 | -0.1583 | 0.1492 |
| active_warming_peak_runway_yes_reversal | YES | post_hypothesis | 16 | 9 | 9 | 0.8461 | 0.9375 | 0.1001 | -0.0444 | 0.1970 | -0.1010 | 0.2065 | -0.0544 | 0.1564 | 0.0575 | 0.2446 |

## Walk-forward probability score

| scope | model | rows | dates | logloss | brier |
| --- | --- | --- | --- | --- | --- |
| walk_forward_all | market_only | 672 | 14 | 0.2055 | 0.0561 |
| walk_forward_all | market_plus_path | 672 | 14 | 0.2331 | 0.0651 |
| post_hypothesis | market_only | 672 | 14 | 0.2055 | 0.0561 |
| post_hypothesis | market_plus_path | 672 | 14 | 0.2331 | 0.0651 |

## Three Gates

- significance=FAIL; baseline=FAIL; forward=FAIL; multiple_testing=FAIL; conclusion=inconclusive; action=reject_buy_no_mechanism.
- K=`11` candidate expressions; Bonferroni-adjusted ROI interval is reported. Post-hypothesis starts `2026-06-25` because the mechanism family was already documented on 6/23-24; exact v1 thresholds remain retrospective and therefore require a new frozen forward check before live.

## 8 环覆盖

- covered: descriptive PnL, date-block inference, same-price baseline, path discrimination, canonical settlement, same-snapshot ask/depth, official fee, city-day dedup, date correlation.
- partial: capacity only uses displayed ask/depth; no queue/fill model. Missing: truly fresh pre-registered forward for this exact rule and real fill evidence.
