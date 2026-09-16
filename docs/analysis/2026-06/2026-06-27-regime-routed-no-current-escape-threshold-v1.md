# Regime-Routed NO Current Escape Threshold V1

Generated: `2026-06-27T01:54:46+00:00`

## Verdict

This is a same-denominator A/B replay.  It fixes the payoff boundary for `runway_current_no`: a current-bracket NO only has an upward escape thesis when the forecast max is strictly above `bracket_high + 0.5` in the market's native unit.  A `90-91F` NO therefore needs `forecast_max_f > 91.5F`; `91.2F` is still inside the settlement bucket and should be blocked.

## Policy Summary

| policy | rows | dates | cities | wins | win_rate | current_no_rows | capped_d2_rows | pnl_usd | roi | exec_rows | exec_pnl_usd | exec_roi | avg_weight |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_size_baseline | 271 | 35 | 35 | 140 | +51.7% | 189 | 82 | +155.73 | +11.5% | 271 | +155.73 | +11.5% | +100.0% |
| full_size_escape_gt_0 | 260 | 35 | 35 | 136 | +52.3% | 178 | 82 | +151.49 | +11.7% | 260 | +151.49 | +11.7% | +100.0% |
| soft_balanced_baseline | 271 | 35 | 35 | 140 | +51.7% | 189 | 82 | +123.21 | +26.3% | 77 | +148.45 | +69.9% | +34.6% |
| soft_balanced_escape_gt_0 | 260 | 35 | 35 | 136 | +52.3% | 178 | 82 | +119.18 | +26.9% | 70 | +144.33 | +74.2% | +34.1% |

## Route Summary

| policy | route_leg | day_regime | rows | wins | win_rate | pnl_usd | roi | exec_rows | exec_pnl_usd | exec_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| soft_balanced_baseline | capped_d2_no | day_forecast_capped | 82 | 51 | +62.2% | +4.39 | +5.1% | 2 | +2.59 | +66.9% |
| soft_balanced_escape_gt_0 | capped_d2_no | day_forecast_capped | 82 | 51 | +62.2% | +4.39 | +5.1% | 2 | +2.59 | +66.9% |
| soft_balanced_baseline | runway_current_no | day_marginal_runway | 121 | 62 | +51.2% | +114.70 | +41.3% | 57 | +120.38 | +71.8% |
| soft_balanced_escape_gt_0 | runway_current_no | day_marginal_runway | 110 | 58 | +52.7% | +110.67 | +44.1% | 50 | +116.26 | +77.6% |
| soft_balanced_baseline | runway_current_no | day_open_runway | 68 | 27 | +39.7% | +4.12 | +3.9% | 18 | +25.48 | +62.5% |
| soft_balanced_escape_gt_0 | runway_current_no | day_open_runway | 68 | 27 | +39.7% | +4.12 | +3.9% | 18 | +25.48 | +62.5% |

## Boundary

- This replay does not retune city pools, prices, regime labels, wind, or temperature-context weights.
- The new rule only removes current-bracket NO rows whose forecast peak does not clear the settlement boundary.  It does not touch `capped_d2_no` rows.
- Blocked current-NO rows: `11`; blocked wins: `4`.
