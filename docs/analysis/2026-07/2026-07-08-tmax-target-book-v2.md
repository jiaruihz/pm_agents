# Tmax Target-Book v2

> generated_at_utc: `2026-07-09T03:42:00Z`
> Scope: research/shadow only. No live runner/config/order behavior changed.

## 结论 / 策略改造方向

- v2 的主改造不是换成外部 tmax，而是把我们当前 tmax 从 `single-leg edge selector` 升级为 `full-ladder-ish posterior + city-day target-book ledger`。
- 外部对照组在 v0 里没有赢概率评分；因此本版只把它作为 disagreement guard/对照，不把它并入主 posterior。
- verified 上继续由 `our_current` 的 first-lock/no-current-YES 形态最稳；rebalance 只能 shadow，因为换仓成本和 posterior flip 还没有被证明能稳定赚钱。
- live 前默认动作：每个 city-day 只允许一个 active target；新报文只 revalue，不直接生成反向新开仓；switch 必须走 close/reopen EV 账本。

## Verified Policy Summary

| model_source | policy | rows | dates | cities | closes | reopens | cost_net | pnl_net | roi_net | roi_net_ci_low | roi_net_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| external_baseline | v2_close_only_ev02 | 12 | 10 | 9 | 0 | 0 | 7.54 | 2.46 | +32.6% | +1.2% | +59.4% |
| external_baseline | v2_first_lock_no_current_yes | 12 | 10 | 9 | 0 | 0 | 7.54 | 2.46 | +32.6% | +1.2% | +59.4% |
| external_baseline | v2_rebalance_ev02 | 12 | 10 | 9 | 0 | 0 | 7.54 | 2.46 | +32.6% | +1.2% | +59.4% |
| our_current_ext_guard | v2_first_lock_no_current_yes | 63 | 14 | 31 | 0 | 0 | 42.39 | 5.61 | +13.2% | +2.7% | +25.7% |
| our_current_ext_guard | v2_rebalance_ev02 | 63 | 14 | 31 | 2 | 2 | 44.60 | 5.40 | +12.1% | +2.5% | +23.9% |
| our_current_ext_guard | v2_close_only_ev02 | 63 | 14 | 31 | 1 | 0 | 43.23 | 4.77 | +11.0% | +1.8% | +23.1% |
| our_current | v2_first_lock_no_current_yes | 162 | 15 | 36 | 0 | 0 | 104.28 | 7.72 | +7.4% | -3.6% | +18.5% |
| our_current | v2_close_only_ev02 | 162 | 15 | 36 | 10 | 0 | 111.30 | 6.70 | +6.0% | -3.1% | +15.5% |
| our_current | v2_rebalance_ev02 | 162 | 15 | 36 | 24 | 24 | 131.55 | 5.45 | +4.1% | -2.9% | +12.0% |

## All Policy Summary

| scope | model_source | policy | rows | dates | cities | closes | reopens | cost_net | pnl_net | roi_net | roi_net_ci_low | roi_net_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev_cv | our_current_ext_guard | v2_first_lock_no_current_yes | 75 | 9 | 30 | 0 | 0 | 50.15 | 3.85 | +7.7% | -6.2% | +19.2% |
| dev_cv | our_current_ext_guard | v2_close_only_ev02 | 75 | 9 | 30 | 1 | 0 | 50.74 | 3.26 | +6.4% | -6.8% | +18.4% |
| dev_cv | our_current_ext_guard | v2_rebalance_ev02 | 75 | 9 | 30 | 3 | 3 | 52.99 | 3.01 | +5.7% | -7.4% | +17.9% |
| dev_cv | our_current | v2_first_lock_no_current_yes | 373 | 19 | 36 | 0 | 0 | 240.47 | 10.53 | +4.4% | +0.2% | +8.8% |
| dev_cv | our_current | v2_rebalance_ev02 | 373 | 19 | 36 | 57 | 57 | 300.94 | 6.06 | +2.0% | -2.3% | +6.5% |
| dev_cv | our_current | v2_close_only_ev02 | 373 | 19 | 36 | 24 | 0 | 255.41 | 4.59 | +1.8% | -2.4% | +6.1% |
| dev_cv | external_baseline | v2_close_only_ev02 | 6 | 3 | 6 | 0 | 0 | 3.22 | -0.22 | -6.8% | -16.8% | +5.8% |
| dev_cv | external_baseline | v2_first_lock_no_current_yes | 6 | 3 | 6 | 0 | 0 | 3.22 | -0.22 | -6.8% | -16.8% | +5.8% |
| dev_cv | external_baseline | v2_rebalance_ev02 | 6 | 3 | 6 | 0 | 0 | 3.22 | -0.22 | -6.8% | -16.8% | +5.8% |
| extension_forward | our_current | v2_rebalance_ev02 | 5 | 2 | 5 | 1 | 1 | 4.45 | -1.45 | -32.7% | n/a | n/a |
| extension_forward | our_current | v2_close_only_ev02 | 5 | 2 | 5 | 0 | 0 | 3.15 | -1.15 | -36.4% | n/a | n/a |
| extension_forward | our_current | v2_first_lock_no_current_yes | 5 | 2 | 5 | 0 | 0 | 3.15 | -1.15 | -36.4% | n/a | n/a |
| verified_forward | external_baseline | v2_close_only_ev02 | 12 | 10 | 9 | 0 | 0 | 7.54 | 2.46 | +32.6% | +1.2% | +59.4% |
| verified_forward | external_baseline | v2_first_lock_no_current_yes | 12 | 10 | 9 | 0 | 0 | 7.54 | 2.46 | +32.6% | +1.2% | +59.4% |
| verified_forward | external_baseline | v2_rebalance_ev02 | 12 | 10 | 9 | 0 | 0 | 7.54 | 2.46 | +32.6% | +1.2% | +59.4% |
| verified_forward | our_current_ext_guard | v2_first_lock_no_current_yes | 63 | 14 | 31 | 0 | 0 | 42.39 | 5.61 | +13.2% | +2.7% | +25.7% |
| verified_forward | our_current_ext_guard | v2_rebalance_ev02 | 63 | 14 | 31 | 2 | 2 | 44.60 | 5.40 | +12.1% | +2.5% | +23.9% |
| verified_forward | our_current_ext_guard | v2_close_only_ev02 | 63 | 14 | 31 | 1 | 0 | 43.23 | 4.77 | +11.0% | +1.8% | +23.1% |
| verified_forward | our_current | v2_first_lock_no_current_yes | 162 | 15 | 36 | 0 | 0 | 104.28 | 7.72 | +7.4% | -3.6% | +18.5% |
| verified_forward | our_current | v2_close_only_ev02 | 162 | 15 | 36 | 10 | 0 | 111.30 | 6.70 | +6.0% | -3.1% | +15.5% |
| verified_forward | our_current | v2_rebalance_ev02 | 162 | 15 | 36 | 24 | 24 | 131.55 | 5.45 | +4.1% | -2.9% | +12.0% |

## v2 Contract

```text
probability_source -> expression candidates -> target_book ledger
open: first eligible city-day target only
hold: default after posterior update
close/reopen: only if close_value + new_value - old_hold_value > buffer
current_yes: not active in primary set; only allowed for revalue/complement cost
external_tmax: challenger/guard only until it beats local model on same denominator
```

## Verdict

conclusion=`shadow_candidate_target_book_v2_not_live`.

下一步是把 v2 ledger 接到 fresh shadow runner：每轮写 position_book/target_book/revalue_actions，但仍保持 zero-notional，等新 settled forward rows 验证。

## Artifacts

- `docs/analysis/2026-07/generated/tmax_target_book_v2/target_book_decisions.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_v2/target_book_actions.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_v2/policy_summary.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_v2/daily_summary.csv`
- `docs/analysis/2026-07/generated/tmax_target_book_v2/expression_summary.csv`
- `docs/analysis/2026-07/2026-07-08-tmax-target-book-v2.json`
