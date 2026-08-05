# Tmax ABCD Comparison v1

> generated_at_utc: `2026-07-08T14:45:38Z`
> start_date: `2026-06-21`
> Scope: research/shadow only. No live runner/config/order behavior changed.

## 字段解释

- `logloss`: 概率模型给真实赢家桶的负对数损失，越低越好；把真实结果给很低概率会被重罚。
- `delta_vs_A_logloss`: 当前行 logloss 减去同分母 A/market baseline 的 logloss；负数表示比 A 好，正数表示比 A 差。
- `brier`: 四桶概率和真实 one-hot 结果的平方误差和，越低越好；比 logloss 没那么惩罚极端误判。
- `delta_vs_A_brier`: 当前行 Brier 减去同分母 A 的 Brier；同样负数更好。

## 结论

- 时间窗已按 `2026-06-21+` 重算；A/B 概率分母从旧表 6 dates 扩到 12 dates / 1,729 rows。
- B 的两个本地版本都赢 A：`mkt_city_source_blend` 概率分最高；`loo_no_city_source_blend` 是当前 exact-book/target-book 使用版本，略弱但接近。
- C_raw 仍然明显输给它自己同分母的 market baseline；C_blend 仍基本退回 market，不是可直接交易 alpha。
- D 的 rebalance 仍弱于 first-lock；更新报文后应先重估 active target，不应因为 posterior 翻转就机械反手。

## Probability ABCD

| slot | name | source | rows | dates | cities | start_date | end_date | logloss | delta_vs_A_logloss | brier | delta_vs_A_brier | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | market_local_norm | local_p3_same_denominator | 1729.0 | 12.0 | 36.0 | 2026-06-21 | 2026-07-03 | 0.6437 | 0.0000 | 0.3271 | 0.0000 | market-local four-bucket baseline on P3 denominator |
| B_score | mkt_city_source_blend | local_p3_same_denominator | 1729.0 | 12.0 | 36.0 | 2026-06-21 | 2026-07-03 | 0.5779 | -0.0658 | 0.3207 | -0.0065 | best local probability scorer after 6/21+ rerun |
| B_exec | loo_no_city_source_blend | local_p3_same_denominator | 1729.0 | 12.0 | 36.0 | 2026-06-21 | 2026-07-03 | 0.5791 | -0.0645 | 0.3218 | -0.0053 | current exact-book bridge probability used by target-book v2 |
| C_raw | external_gaussian_recency | external_matched_denominator | 783.0 | 11.0 | 33.0 | 2026-06-21 | 2026-07-03 | 1.7194 | 1.0399 | 0.8370 | 0.5247 | external-style Gaussian/recency raw probabilities |
| C_blend | external_gaussian_market_blend | external_matched_denominator | 783.0 | 11.0 | 33.0 | 2026-06-21 | 2026-07-03 | 0.6796 | 0.0000 | 0.3123 | -0.0000 | CV-selected external/market blend; alpha selected upstream |
| D | tmax_target_book_v2 | target_book_policy | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | D is a position/target-book policy, not an independent probability model. |

## Trade / Target-Book ABCD

| slot | name | scope | rows | dates | cities | closes | reopens | cost_net | pnl_net | roi_net | ci_low | ci_high | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A/C_blend | market/external_blend_alpha0_first_lock | verified_forward | 10 | 8 | 8 | 0 | 0 | 6.1681 | 1.8319 | +29.7% | -8.4% | +64.2% | External blend alpha=0, effectively market-local selection; sparse denominator. |
| B_exec | our_current_first_lock_no_current_yes | verified_forward | 126 | 12 | 35 | 0 | 0 | 81.1014 | 7.8986 | +9.7% | -1.5% | +21.4% | Current exact-book bridge target-book primary. |
| D_rebalance | our_current/v2_rebalance_ev02 | verified_forward | 126 | 12 | 35 | 17 | 17 | 100.3924 | 5.6076 | +5.6% | -3.4% | +16.4% | Position-aware close/reopen policy; tests posterior flip as switch alpha. |
| D_guard | our_current_ext_guard/v2_first_lock_no_current_yes | verified_forward | 39 | 11 | 20 | 0 | 0 | 26.5687 | 4.4313 | +16.7% | +3.3% | +33.1% | External disagreement guard; sample-starved diagnostic. |
| C_raw | external_raw_gaussian_recency | verified_forward | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | n/a | n/a | n/a | Not promoted to trade replay because proper scoring badly loses to market. |

## Verdict

```text
A = market baseline / denominator
B_score = current local best probability scorer
B_exec = current target-book execution probability source
C = external tmax is useful as data/calibration checker, not direct alpha
D = target-book architecture is right; rebalance branch not yet proven
live_action = none
next = zero-notional ABCD target-book shadow runner
```

## Artifacts

- `docs/analysis/2026-07/generated/tmax_abcd_comparison_v1/probability_abcd_summary.csv`
- `docs/analysis/2026-07/generated/tmax_abcd_comparison_v1/trade_abcd_summary.csv`
- `docs/analysis/2026-07/2026-07-08-tmax-abcd-comparison-v1.json`
