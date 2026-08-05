# HeadA Hot-Tail Overshoot Exit v1

Generated: 2026-07-06T17:21:44+00:00

Scope: HeadA `forecast_tail_low_price_yes` only. Denominator is the current hot-only live posture:
`dist>0`, ask 5-20c, edge>=20c, 333 historical rows from `docs/analysis/2026-07/generated/low_price_yes_heada_refinement_v1/hot_rows.csv`.

## Verdict

Historical data does **not** support a static take-profit exit for the current hot-tail sleeve.
The broader history says the opposite of the Munich case: once a hot-tail ticket reprices to
20c/30c, its settlement win rate is high enough that selling gives back convexity. Munich is a
real regret case, but it is not the dominant historical path.

Conclusion: keep `hold-to-settlement` as default. Continue logging overshoot/touch telemetry;
do not promote TP20/TP30/TP50, half-sell, or recover-stake exits from this evidence.

```text
significance=FAIL for exit overlay improvement
baseline=hold-to-settlement, same rows/sizing/fee
forward=NA for new exit selector
conclusion=inconclusive/negative for live exit change
```

## Touch Diagnostics

| touch_bucket | rows | win_rate | overshoot_rate | below_rate | avg_entry |
| --- | --- | --- | --- | --- | --- |
| all | 333 | +15.0% | +36.6% | +48.3% | 0.104 |
| no_touch30 | 261 | +5.0% | +39.5% | +55.6% | 0.099 |
| touch20 | 115 | +33.0% | +31.3% | +35.7% | 0.123 |
| touch30 | 72 | +51.4% | +26.4% | +22.2% | 0.123 |
| touch50 | 48 | +77.1% | +14.6% | +8.3% | 0.124 |
| touch70 | 40 | +90.0% | +7.5% | +2.5% | 0.125 |

## Full Window Policy Replay

| policy | rows | dates | win_rate | exit_hit_rate | roi | delta_vs_hold | delta_ci_low | delta_ci_high | losing_days | le_minus50pct_days | max_daily_loss_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold | 333 | 53 | +15.0% | +0.0% | +41.8% | +0.0% |  |  | 19 | 16 | $-11.04 |
| full_tp20 | 333 | 53 | +15.0% | +34.5% | -0.5% | -42.3% | -71.6% | -14.7% | 35 | 11 | $-5.38 |
| full_tp30 | 333 | 53 | +15.0% | +21.6% | -1.7% | -43.6% | -65.5% | -22.4% | 31 | 17 | $-7.78 |
| full_tp50 | 333 | 53 | +15.0% | +14.4% | +5.9% | -36.0% | -53.7% | -19.0% | 28 | 15 | $-11.04 |
| full_tp70 | 333 | 53 | +15.0% | +12.0% | +20.1% | -21.8% | -34.3% | -8.7% | 24 | 14 | $-11.04 |
| half_tp30 | 333 | 53 | +15.0% | +21.6% | +20.1% | -21.8% | -32.7% | -11.2% | 24 | 16 | $-8.64 |
| half_tp50 | 333 | 53 | +15.0% | +14.4% | +23.9% | -18.0% | -26.9% | -9.5% | 20 | 15 | $-11.04 |
| recover_stake_tp30 | 333 | 53 | +15.0% | +21.6% | +21.7% | -20.1% | -30.7% | -10.3% | 24 | 16 | $-8.95 |
| recover_stake_tp50 | 333 | 53 | +15.0% | +14.4% | +32.1% | -9.8% | -14.8% | -5.1% | 21 | 16 | $-11.04 |
| full_tp30_before_peak | 333 | 53 | +15.0% | +1.2% | +41.9% | +0.1% | -6.2% | +4.9% | 20 | 17 | $-11.04 |
| full_tp30_low_price | 333 | 53 | +15.0% | +4.2% | +32.1% | -9.8% | -18.8% | -1.2% | 20 | 17 | $-11.04 |
| full_tp30_feasible_book | 333 | 53 | +15.0% | +14.1% | +11.0% | -30.8% | -49.0% | -13.3% | 30 | 18 | $-7.78 |
| full_tp30_raw_dist_le1 | 333 | 53 | +15.0% | +13.5% | +12.9% | -29.0% | -47.0% | -12.5% | 29 | 18 | $-11.04 |

## Train Window

| policy | rows | dates | roi | delta_vs_hold | delta_ci_low | delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- |
| full_tp30 | 275 | 44 | -2.0% | -37.1% | -62.2% | -14.0% |
| full_tp50 | 275 | 44 | +4.4% | -30.7% | -49.6% | -12.9% |
| hold | 275 | 44 | +35.1% | +0.0% |  |  |
| recover_stake_tp30 | 275 | 44 | +19.1% | -16.0% | -27.5% | -5.1% |
| recover_stake_tp50 | 275 | 44 | +27.1% | -8.0% | -13.2% | -3.1% |

## Recent Window

| policy | rows | dates | roi | delta_vs_hold | delta_ci_low | delta_ci_high |
| --- | --- | --- | --- | --- | --- | --- |
| full_tp30 | 58 | 9 | -0.3% | -77.0% | -127.3% | -39.5% |
| full_tp50 | 58 | 9 | +13.6% | -63.1% | -102.8% | -20.8% |
| hold | 58 | 9 | +76.7% | +0.0% |  |  |
| recover_stake_tp30 | 58 | 9 | +35.3% | -41.4% | -60.0% | -23.5% |
| recover_stake_tp50 | 58 | 9 | +57.6% | -19.1% | -28.0% | -8.2% |

## Interpretation

- Fixed TP20/TP30 is structurally wrong for HeadA hot-tail. It converts a convex YES into a capped
  low-upside trade exactly when the market is starting to agree with the thesis.
- Recover-stake helps psychologically but still underperforms hold in this denominator.
- Higher thresholds like TP50/TP70 are less damaging, but they still do not beat hold robustly.
- The right research direction is not a blanket exit. It is a future state model that asks:
  after a touch, is this ticket likely to finish exactly in this bracket, or overshoot to the next
  bracket? That requires touch-time weather/orderbook features, not only entry-time features.

## Data Notes

- Entry fee uses official Polymarket Weather formula: `shares * 0.05 * price * (1-price)`.
- Resting TP exits are modeled as maker/no-fee at the threshold.
- TP50/TP70 use `max_future_yes_bid` touch detection; TP20/TP30 also have first-touch timestamps.
- This is historical research only and changes no live runner.
