# Plateau Near-High Resolution Shadow V1

## 结论

修复 running-max clock 后，plateau 不再是之前那 14 笔混杂 stale 样本；当前 atlas 里 `near_high_plateau/plateau_near_high` 共 62 个 state rows，覆盖 2026-05-20..2026-06-26。这是 shadow ledger，不是交易规则。

人话：plateau 是“温度贴着高点横住”，核心问题不是直接买 NO 或 YES，而是观察它之后会不会 rebreak。现在只记录，不进 v3 live。

## By Shadow Hint

| shadow_hint | rows | dates | cities | future_break_rate | no_rows | no_win_rate | avg_no_ask | no_pnl_usd | no_roi | yes_rows | yes_win_rate | avg_yes_ask | yes_pnl_usd | yes_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| unresolved_watch | 51 | 14 | 23 | +58.8% | 35 | 0.5428571428571428 | 0.625 | $-74.08 | -42.3% | 39 | 0.38461538461538464 | 0.360 | $-74.61 | -38.3% |
| hold_or_fade_watch | 7 | 2 | 6 | +85.7% | 6 | 0.6666666666666666 | 0.655 | $-4.26 | -14.2% | 7 | 0.2857142857142857 | 0.339 | $-18.74 | -53.6% |
| rebreak_no_watch | 4 | 4 | 4 | +50.0% | 3 | 0.3333333333333333 | 0.456 | $-9.94 | -66.3% | 4 | 0.5 | 0.443 | $-8.27 | -41.3% |

## By State

| running_max_state | intraday_state | rows | dates | cities | future_break_rate | no_rows | no_win_rate | avg_no_ask | no_pnl_usd | no_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| near_high_plateau | active_warming | 31 | 8 | 16 | +90.3% | 20 | 0.8 | 0.850 | $-13.96 | -14.0% |
| near_high_plateau | fresh_high | 13 | 6 | 8 | +46.2% | 11 | 0.45454545454545453 | 0.589 | $-24.40 | -44.4% |
| near_high_plateau | plateau_near_high | 12 | 7 | 10 | +16.7% | 10 | 0.2 | 0.262 | $-39.93 | -79.9% |
| near_high_plateau | state_unknown | 6 | 4 | 6 | +33.3% | 3 | 0.3333333333333333 | 0.365 | $-9.99 | -66.6% |

## By Moisture

| moisture_cloud_regime | rows | dates | cities | future_break_rate | no_rows | no_win_rate | avg_no_ask | no_pnl_usd | no_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mixed_moisture | 35 | 11 | 18 | +62.9% | 25 | 0.52 | 0.629 | $-52.00 | -41.6% |
| dry_heat_inertia | 19 | 8 | 10 | +63.2% | 15 | 0.6 | 0.624 | $-26.31 | -35.1% |
| moisture_cloud_unknown | 6 | 4 | 6 | +33.3% | 3 | 0.3333333333333333 | 0.365 | $-9.99 | -66.6% |
| humid_convective_risk | 2 | 1 | 2 | +100.0% | 1 | 1.0 | 0.999 | $+0.01 | +0.1% |

Verdict: `shadow_ledger_only`。下一步用 forward 新样本校准 `rebreak_no_watch` / `hold_or_fade_watch`，不要把 plateau 粗暴并入 runway NO。
