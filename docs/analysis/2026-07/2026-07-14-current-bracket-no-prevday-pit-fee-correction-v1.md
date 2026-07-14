# Current-Bracket NO Prevday PIT Fee Correction v1

## 结论

固定旧报告同一 `220` raw signals、同一 first-per-city-day 选择后，精确复现 `191` trades / `32` target dates / gross PnL `+283.455042` / gross ROI `+29.68%`。加官方 taker fee `37.13600` 后，PnL 为 `+246.319042`，ROI `+25.79%`。

关键变化不是点估转负，而是 significance：旧 gross CI `+3.82%`..`+54.64%`；fee-adjusted CI `-0.07%`..`+50.75%`，lower bound 过零，所以绝对收益 gate **PASS → FAIL**。

相对同 NO ask/cap baseline 的结构仍在：fee-adjusted excess ROI `+38.08%`，CI `+23.30%`..`+53.69%`。这说明 classifier 仍可能在做有效排序，但当前证据不足以称为“已确认可盈利”，更不能直接 live。

## Bug 与影响半径

- 根因：`research_current_bracket_no_pass_through_v1.py:348-350` 定义 `stake_profit_usd = label * shares - STAKE_USD`，`:374-378` 与 `:426-431` 的日级 bootstrap/summary 直接汇总该 gross 字段，未扣 entry taker fee。
- 污染窗口：`2026-05-20`..`2026-06-20`，`191` trades / `32` dates / `30` cities。
- 受影响结论：`2026-06-23-current-bracket-no-prevday-pit-shadow-v1.md` 的 `+29.7% CI [+3.8%, +54.6%]` 与 significance PASS。
- 没被本审计证明：forward fill、slippage、queue/latency；本报告只纠正冻结 historical replay。

## 口径

- entry：真实 NO ask 的 taker `BUY_NO`；固定 `$5` fill cost，shares=`5/no_ask`。
- fee：`shares * 0.05 * price * (1-price)`，每笔 round 到 5 decimals。
- bootstrap：沿用旧 seed `20260622`，按 `target_date` block，`5000` reps；gross CI 精确复现旧报告。

## 可复查输出

- exact 191 trades：`docs/analysis/2026-07/generated/current_bracket_no_prevday_pit_fee_correction_v1/exact_191_trades.csv`
- exact 361 baseline trades：`docs/analysis/2026-07/generated/current_bracket_no_prevday_pit_fee_correction_v1/exact_361_baseline_trades.csv`
- daily：`docs/analysis/2026-07/generated/current_bracket_no_prevday_pit_fee_correction_v1/daily_summary.csv`
- machine summary：`docs/analysis/2026-07/generated/current_bracket_no_prevday_pit_fee_correction_v1/summary.json`
