# Regime-Routed NO Mechanism Split V2

## 结论

这次不是再加一条 gate，而是把旧 `runway_current_no` 拆成两个物理含义不同的 route：

- `fresh_runway_current_no`：当前观测仍是新高/近新高，且日内状态还在升温。
- `false_fade_reheat_current_no`：当前已经回落或停住，但状态像 false fade / reheating after dip，下注逻辑是后面还能重新加热打穿当前档。
- `unapproved_stale_current_no`：停滞、pullback uncertain、clock unknown 等不干净形态，先只做诊断，不并回主策略。

同分母历史主样本仍是 `docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv` 的 `routed_capped_d2_no_relaxed70_best_ask`：284 rows / 38 dates / 35 cities。

- 原始混合 v1：284 笔，full ROI +12.0%，soft weighted ROI +26.7%，CI [+6.3%, +48.0%]。
- 只保留 fresh runway + capped d2：232 笔，full ROI +8.4%，soft weighted ROI +22.4%，CI [+2.0%, +43.6%]。
- 机制拆分 v2（fresh + false-fade/reheat + capped d2）：263 笔，full ROI +11.7%，soft weighted ROI +25.6%，CI [+5.9%, +45.8%]。

所以 ROI 降低的原因不是“新定义不符合物理”，而是 fresh-only 把一批不是 runway、但可能属于 reheat-after-dip 的盈利样本一起切掉了。正确修法是拆 route，不是把 stale/fade 全部塞回 runway。

## Strategy Summary

| slice | rows | dates | cities | wins | win_rate | avg_ask | roi | weighted_roi | weighted_roi_ci_low | weighted_roi_ci_high | daily_roi_eq_minus100 | worst_day_pnl_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_mixed_v1 | 284 | 38 | 35 | 149 | +52.5% | 0.512 | +12.0% | +26.7% | +6.3% | +48.0% | 1 | $-25.39 |
| date_lineage_only_same_historical | 284 | 38 | 35 | 149 | +52.5% | 0.512 | +12.0% | +26.7% | +6.3% | +48.0% | 1 | $-25.39 |
| fresh_plus_capped | 232 | 38 | 34 | 123 | +53.0% | 0.529 | +8.4% | +22.4% | +2.0% | +43.6% | 2 | $-20.39 |
| mechanism_split_v2 | 263 | 38 | 34 | 141 | +53.6% | 0.525 | +11.7% | +25.6% | +5.9% | +45.8% | 1 | $-25.39 |
| shadow_tail_all_current_no | 284 | 38 | 35 | 149 | +52.5% | 0.512 | +12.0% | +26.7% | +6.3% | +48.0% | 1 | $-25.39 |
| diagnostic_unapproved_stale_only | 21 | 19 | 14 | 8 | +38.1% | 0.346 | +16.6% | +39.2% | -45.7% | +126.5% | 11 | $-10.00 |
| diagnostic_false_fade_reheat_only | 31 | 21 | 23 | 18 | +58.1% | 0.500 | +36.2% | +44.4% | -6.6% | +88.2% | 8 | $-5.00 |

## Train / Forward Split

| slice | window | rows | dates | cities | win_rate | avg_ask | roi | weighted_roi | daily_roi_eq_minus100 | worst_day_pnl_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_mixed_v1 | forward_since_2026_06_21 | 31 | 6 | 17 | +48.4% | 0.545 | -2.9% | +16.9% | 1 | $-15.00 |
| original_mixed_v1 | train_to_2026_06_20 | 253 | 32 | 35 | +53.0% | 0.508 | +13.8% | +27.7% | 0 | $-25.39 |
| date_lineage_only_same_historical | forward_since_2026_06_21 | 31 | 6 | 17 | +48.4% | 0.545 | -2.9% | +16.9% | 1 | $-15.00 |
| date_lineage_only_same_historical | train_to_2026_06_20 | 253 | 32 | 35 | +53.0% | 0.508 | +13.8% | +27.7% | 0 | $-25.39 |
| fresh_plus_capped | forward_since_2026_06_21 | 22 | 6 | 15 | +45.5% | 0.568 | -18.7% | -12.2% | 2 | $-9.39 |
| fresh_plus_capped | train_to_2026_06_20 | 210 | 32 | 33 | +53.8% | 0.525 | +11.2% | +25.6% | 0 | $-20.39 |
| mechanism_split_v2 | forward_since_2026_06_21 | 26 | 6 | 16 | +46.2% | 0.577 | -18.3% | -8.9% | 1 | $-10.00 |
| mechanism_split_v2 | train_to_2026_06_20 | 237 | 32 | 34 | +54.4% | 0.520 | +14.9% | +28.7% | 0 | $-25.39 |
| shadow_tail_all_current_no | forward_since_2026_06_21 | 31 | 6 | 17 | +48.4% | 0.545 | -2.9% | +16.9% | 1 | $-15.00 |
| shadow_tail_all_current_no | train_to_2026_06_20 | 253 | 32 | 35 | +53.0% | 0.508 | +13.8% | +27.7% | 0 | $-25.39 |
| diagnostic_unapproved_stale_only | forward_since_2026_06_21 | 5 | 5 | 3 | +60.0% | 0.380 | +77.0% | +128.6% | 2 | $-5.00 |
| diagnostic_unapproved_stale_only | train_to_2026_06_20 | 16 | 14 | 13 | +31.2% | 0.335 | -2.3% | +13.9% | 9 | $-10.00 |
| diagnostic_false_fade_reheat_only | forward_since_2026_06_21 | 4 | 4 | 4 | +50.0% | 0.623 | -16.0% | +11.8% | 2 | $-5.00 |
| diagnostic_false_fade_reheat_only | train_to_2026_06_20 | 27 | 17 | 21 | +59.3% | 0.482 | +43.9% | +47.3% | 6 | $-5.00 |

## Route Mechanisms

| slice | rows | dates | cities | win_rate | avg_ask | roi | weighted_roi | weighted_roi_ci_low | weighted_roi_ci_high | daily_roi_eq_minus100 | worst_day_pnl_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| capped_d2_no | 84 | 34 | 24 | +61.9% | 0.612 | +2.4% | +4.4% | -16.4% | +24.6% | 6 | $-12.65 |
| unapproved_stale_current_no | 21 | 19 | 14 | +38.1% | 0.346 | +16.6% | +39.2% | -45.7% | +126.5% | 11 | $-10.00 |
| false_fade_reheat_current_no | 31 | 21 | 23 | +58.1% | 0.500 | +36.2% | +44.4% | -6.6% | +88.2% | 8 | $-5.00 |
| fresh_runway_current_no | 148 | 35 | 31 | +48.0% | 0.482 | +11.8% | +27.7% | +0.4% | +53.7% | 6 | $-20.00 |

## 为什么 fresh-only 会降 ROI

被 fresh-only 切掉的 current-NO 里，false-fade/reheat 子组是 31 笔，full ROI +36.2%，soft weighted ROI +44.4%；不干净 stale 子组是 21 笔，full ROI +16.6%，soft weighted ROI +39.2%。

这说明旧策略混了三类东西：干净 runway、另一条可能有效的 reheat-after-dip、以及目前缺乏机制解释的尾部样本。fresh-only 物理更干净，但会牺牲 reheat-after-dip 这条候选 alpha；v2 的科学性在于把它们命名分账，而不是用一个大 `runway_current_no` 继续混。

## Mechanism Split V2 Worst Days

| target_date | rows | cities | wins | win_rate | avg_ask | pnl_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-25 | 8 | 8 | 2 | +25.0% | 0.509 | $-25.39 | -63.5% | capped_d2_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:6 | CapeTown,Jeddah,Shanghai,TelAviv,Tokyo,Wuhan |
| 2026-05-31 | 7 | 7 | 2 | +28.6% | 0.454 | $-19.42 | -55.5% | capped_d2_no:3,fresh_runway_current_no:4 | Busan,Karachi,Miami,Tokyo,Wuhan |
| 2026-05-22 | 8 | 8 | 2 | +25.0% | 0.477 | $-14.79 | -37.0% | capped_d2_no:4,fresh_runway_current_no:4 | Chongqing,Helsinki,Istanbul,Manila,Munich,Shanghai |
| 2026-06-17 | 9 | 9 | 4 | +44.4% | 0.580 | $-13.41 | -29.8% | capped_d2_no:3,false_fade_reheat_current_no:1,fresh_runway_current_no:5 | Beijing,Chengdu,Istanbul,Karachi,Wuhan |
| 2026-06-01 | 4 | 4 | 1 | +25.0% | 0.465 | $-11.67 | -58.3% | capped_d2_no:1,fresh_runway_current_no:3 | CapeTown,Houston,NYC |
| 2026-06-20 | 7 | 7 | 3 | +42.9% | 0.497 | $-11.08 | -31.7% | capped_d2_no:3,false_fade_reheat_current_no:2,fresh_runway_current_no:2 | Jeddah,Karachi,Manila,Tokyo |
| 2026-06-22 | 2 | 2 | 0 | +0.0% | 0.654 | $-10.00 | -100.0% | capped_d2_no:1,false_fade_reheat_current_no:1 | NYC,Tokyo |
| 2026-06-21 | 8 | 8 | 3 | +37.5% | 0.553 | $-9.39 | -23.5% | capped_d2_no:4,fresh_runway_current_no:4 | Beijing,CapeTown,Denver,Singapore,TelAviv |
| 2026-05-27 | 5 | 5 | 2 | +40.0% | 0.584 | $-8.98 | -35.9% | false_fade_reheat_current_no:1,fresh_runway_current_no:4 | Beijing,Manila,SaoPaulo |
| 2026-06-23 | 5 | 5 | 2 | +40.0% | 0.620 | $-8.87 | -35.5% | capped_d2_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 | Chongqing,Jeddah,Karachi |
| 2026-05-21 | 7 | 7 | 3 | +42.9% | 0.583 | $-7.35 | -21.0% | capped_d2_no:3,false_fade_reheat_current_no:3,fresh_runway_current_no:1 | Manila,Munich,SanFrancisco,TelAviv |
| 2026-06-02 | 8 | 8 | 4 | +50.0% | 0.565 | $-6.23 | -15.6% | capped_d2_no:3,fresh_runway_current_no:5 | Beijing,Busan,Houston,TelAviv |

## City Contribution

| city | rows | dates | wins | win_rate | avg_ask | pnl_usd | roi | route_mix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TelAviv | 15 | 15 | 4 | +26.7% | 0.495 | $-36.04 | -48.0% | false_fade_reheat_current_no:2,fresh_runway_current_no:13 |
| Manila | 11 | 11 | 3 | +27.3% | 0.553 | $-27.80 | -50.5% | capped_d2_no:6,fresh_runway_current_no:5 |
| Singapore | 4 | 4 | 1 | +25.0% | 0.603 | $-12.75 | -63.8% | capped_d2_no:3,fresh_runway_current_no:1 |
| SanFrancisco | 9 | 9 | 2 | +22.2% | 0.551 | $-9.29 | -20.6% | false_fade_reheat_current_no:1,fresh_runway_current_no:8 |
| Tokyo | 8 | 8 | 3 | +37.5% | 0.470 | $-9.16 | -22.9% | capped_d2_no:2,false_fade_reheat_current_no:1,fresh_runway_current_no:5 |
| Wuhan | 13 | 13 | 6 | +46.2% | 0.479 | $-8.31 | -12.8% | capped_d2_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:11 |
| SaoPaulo | 3 | 3 | 1 | +33.3% | 0.527 | $-7.65 | -51.0% | false_fade_reheat_current_no:1,fresh_runway_current_no:2 |
| Istanbul | 11 | 11 | 5 | +45.5% | 0.518 | $-5.02 | -9.1% | capped_d2_no:1,false_fade_reheat_current_no:2,fresh_runway_current_no:8 |
| CapeTown | 10 | 10 | 5 | +50.0% | 0.549 | $-5.01 | -10.0% | fresh_runway_current_no:10 |
| Warsaw | 5 | 5 | 2 | +40.0% | 0.486 | $-3.76 | -15.0% | capped_d2_no:1,false_fade_reheat_current_no:1,fresh_runway_current_no:3 |
| Ankara | 7 | 7 | 3 | +42.9% | 0.544 | $-3.29 | -9.4% | capped_d2_no:5,fresh_runway_current_no:2 |
| Denver | 2 | 2 | 1 | +50.0% | 0.660 | $-2.54 | -25.4% | false_fade_reheat_current_no:1,fresh_runway_current_no:1 |
| Chengdu | 6 | 6 | 4 | +66.7% | 0.637 | $-0.79 | -2.6% | capped_d2_no:4,false_fade_reheat_current_no:1,fresh_runway_current_no:1 |
| Chongqing | 6 | 6 | 3 | +50.0% | 0.507 | $-0.19 | -0.6% | capped_d2_no:3,fresh_runway_current_no:3 |
| Shanghai | 9 | 9 | 5 | +55.6% | 0.448 | $+0.76 | +1.7% | capped_d2_no:2,false_fade_reheat_current_no:1,fresh_runway_current_no:6 |
| Munich | 10 | 10 | 6 | +60.0% | 0.579 | $+1.39 | +2.8% | capped_d2_no:10 |
| Karachi | 19 | 19 | 11 | +57.9% | 0.582 | $+3.88 | +4.1% | capped_d2_no:13,false_fade_reheat_current_no:1,fresh_runway_current_no:5 |
| NYC | 10 | 10 | 5 | +50.0% | 0.532 | $+5.15 | +10.3% | false_fade_reheat_current_no:2,fresh_runway_current_no:8 |
| Dallas | 4 | 4 | 3 | +75.0% | 0.605 | $+5.32 | +26.6% | capped_d2_no:4 |
| Houston | 11 | 11 | 7 | +63.6% | 0.507 | $+6.27 | +11.4% | capped_d2_no:2,false_fade_reheat_current_no:1,fresh_runway_current_no:8 |

## Verdict

significance=PASS：v2 soft weighted ROI 的历史 date-block CI 大于 0。
baseline=PARTIAL：相对旧混合 v1 没有明显提升，主要价值是机制解释、避免错名，并把 unapproved stale 从主策略里分离出来。
forward=FAIL：2026-06-21 以来已结算 forward 子窗仍薄且表现为负，不能作为 live confirmation。
conclusion=inconclusive_shadow_only。

当前可采取的动作：本地策略研究口径改成机制分裂；live 不恢复。shadow 可以记录三个 route 的独立命中率、价格、wind/cloud/freshness 分布，后续再看 false-fade/reheat 是否能单独过前瞻门。
