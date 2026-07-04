# Low-Price YES Maker Fallback v1

Generated: `2026-07-04T23:05:51.521066+00:00`

## Verdict

`forecast_tail_low_price_yes` maker-first has non-fill and long-wait behavior, but the current settled evidence does **not** support a blanket timed taker fallback.

The useful signal is more specific: when a maker order sits unfilled and the book moves down sharply, the old maker order can become a stale overbid. In the current small sample, TTL refresh would have helped mainly by buying one loser much cheaper or by avoiding later stale fills, not by rescuing missed winners.

Recommended action: keep current tiny maker-first live behavior unchanged for now; add a shadow lifecycle ledger for TTL refresh/reprice/taker fallback. A live fallback can be reconsidered only after forward rows show whether the improvement comes from genuine fill rescue, cheaper re-entry, or accidental loser underfill.

Post-report rollout note: after user approval, a tiny live dynamic-maker lifecycle overlay was enabled on 2026-07-04 23:21 UTC. The live version is stricter than a blanket taker fallback: it can cancel/repost maker orders after 15m, reprice maker orders inside the 20c band when edge remains >=20c, and only taker fallback after 30m when spread <=1c and the taker price is not above the source maker price. The first live cycle submitted two maker-only reprices and no taker fallback.

```text
significance=NA
baseline=PASS (same live orders vs maker-only realized baseline)
forward=FAIL (fresh settled sample too small; 7/05 orders still open)
conclusion=inconclusive_execution_overlay_live_tiny_probe
```

## Data Snapshot

- Live maker order rows: 12 (9 settled, 3 open).
- Real maker fill coverage: any-fill 83.3%; full-fill 75.0%; median first fill 52.5 min.
- Settled maker-only actual ROI: 22.5% ($1.62 on $7.18); wins 1/9.
- Actual missed-winner cost from unfilled winning shares: $0.00.
- Long-wait fills >=60m: 5 orders; winners among them: 0.
- Orderbook replay: 10/12 order tokens matched; 338 book rows from 175 files. Unmatched open rows are data gaps, not strategy evidence.
- Sizing modes: `{'notional': 9, 'price_tier_6_8_10_shares': 3}`.

## TTL Taker Fallback Grid

Policy definition: keep the maker order until TTL; if remaining shares are unfilled and the first book snapshot after TTL has `spread<=cap` and `best_ask<=posted_price+cushion` (also capped at 20c), buy the remaining shares as taker. Taker fees use the official weather formula `shares * 0.05 * price * (1-price)`. If the condition is not met, keep maker-only behavior.

| TTL | Spread cap | Cushion | Fallback orders | Settled fallback | Taker cost | Fees | Baseline ROI | Policy ROI | PnL delta |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 15m | 0.010 | 0.010 | 3/12 | 3 | $1.95 | $0.09 | 22.5% | 29.0% | $0.36 |
| 30m | 0.010 | 0.010 | 2/12 | 2 | $1.03 | $0.05 | 22.5% | 32.1% | $0.52 |
| 60m | 0.010 | 0.010 | 2/12 | 2 | $1.03 | $0.05 | 22.5% | 32.1% | $0.52 |
| 120m | 0.010 | 0.010 | 0/12 | 0 | $0.00 | $0.00 | 22.5% | 22.5% | $0.00 |
| 120m | 0.020 | 0.020 | 1/12 | 1 | $1.06 | $0.05 | 22.5% | 17.4% | $-0.31 |

## Interpretation

- There are maker-driven non-fills and long waits: current price-tier live has open/unfilled rows, and several older maker fills waited hours.
- The settled sample does not show profitable missed winners. `actual_missed_winner_cost` is zero because the only settled winner filled quickly.
- The positive 15/30/60m point estimates are mostly a stale-overbid / cheaper-re-entry effect, not proof that sweeping faster finds winners.
- Naive fast fallback is risky for this sleeve: when the book has not moved down, taker fallback simply pays spread and fee on the same lottery ticket.
- Repricing maker is less dangerous than taker fallback, but historical book snapshots cannot prove queue fills. Treat reprice as telemetry until live cancel/repost logs exist.

## Driver Cases

Rows below are the actual fallback triggers inside the focused 15/30/60/120 minute policies. They show why this is a lifecycle problem rather than a simple taker switch.

| TTL | City | Date | Bracket | Posted | TTL ask | Spread | Final | Baseline PnL | Policy PnL | Delta |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 15m | Chicago | 2026-07-03 | 94-95 | 0.059 | 0.014 | 0.004 | 0 | $-0.80 | $-0.22 | $0.58 |
| 30m | Chicago | 2026-07-03 | 94-95 | 0.059 | 0.014 | 0.004 | 0 | $-0.80 | $-0.22 | $0.58 |
| 60m | Chicago | 2026-07-03 | 94-95 | 0.059 | 0.014 | 0.004 | 0 | $-0.80 | $-0.22 | $0.58 |
| 15m | Helsinki | 2026-07-04 | 21 | 0.151 | 0.156 | 0.005 | 0 | $-0.80 | $-0.86 | $-0.06 |
| 30m | Helsinki | 2026-07-04 | 21 | 0.151 | 0.156 | 0.005 | 0 | $-0.80 | $-0.86 | $-0.06 |
| 60m | Helsinki | 2026-07-04 | 21 | 0.151 | 0.156 | 0.005 | 0 | $-0.80 | $-0.86 | $-0.06 |
| 15m | Paris | 2026-07-04 | 32 | 0.061 | 0.070 | 0.009 | 0 | $-0.80 | $-0.96 | $-0.16 |

## Reprice Diagnostics

| TTL | Cushion | Reprice possible | Old maker behind bid | Future ask touched reprice | Mean extra price |
|---:|---:|---:|---:|---:|---:|
| 30m | 0.010 | 4/12 | 2 | 3 | 0.005 |
| 30m | 0.020 | 4/12 | 2 | 3 | 0.008 |
| 60m | 0.010 | 3/12 | 2 | 2 | 0.007 |
| 60m | 0.020 | 3/12 | 2 | 2 | 0.014 |
| 120m | 0.010 | 3/12 | 3 | 2 | 0.010 |
| 120m | 0.020 | 3/12 | 3 | 2 | 0.016 |

## Order Rows

| Created UTC | City | Date | Bracket | Mode | Price | Shares | Filled | First fill | Settled | Final | Latest bid/ask |
|---|---|---|---:|---|---:|---:|---:|---:|---|---:|---:|
| 2026-07-03T03:53:01+00:00 | Dallas | 2026-07-03 | 98-99 | notional | 0.111 | 7.21 | 7.21 | 84.0 | True | 0 | 0.040/0.070 |
| 2026-07-03T05:03:50+00:00 | Houston | 2026-07-03 | 96-97 | notional | 0.091 | 8.80 | 8.80 | 4.5 | True | 1 | 0.998/0.999 |
| 2026-07-03T06:39:45+00:00 | Chicago | 2026-07-03 | 94-95 | notional | 0.059 | 13.56 | 13.56 | 101.6 | True | 0 | 0.001/0.003 |
| 2026-07-03T07:25:23+00:00 | NYC | 2026-07-03 | 104-105 | notional | 0.065 | 12.31 | 12.31 | 5.0 | True | 0 | 0.001/0.003 |
| 2026-07-03T09:51:42+00:00 | LA | 2026-07-03 | 70-71 | notional | 0.141 | 5.68 | 5.68 | 13.9 | True | 0 | 0.330/0.480 |
| 2026-07-03T15:25:01+00:00 | Manila | 2026-07-04 | 36+ | notional | 0.056 | 14.29 | 14.29 | 21.1 | True | 0 | 0.000/0.009 |
| 2026-07-03T16:25:40+00:00 | Busan | 2026-07-04 | 24 | notional | 0.048 | 16.67 | 16.67 | 6.2 | True | 0 | 0.003/0.012 |
| 2026-07-03T19:46:35+00:00 | Helsinki | 2026-07-04 | 21 | notional | 0.151 | 5.30 | 5.30 | 109.2 | True | 0 | 0.001/0.002 |
| 2026-07-03T20:21:59+00:00 | Paris | 2026-07-04 | 32 | notional | 0.061 | 13.12 | 13.11 | 464.8 | True | 0 | 0.000/0.004 |
| 2026-07-04T10:19:43+00:00 | Wellington | 2026-07-05 | 15 | price_tier_6_8_10_shares | 0.111 | 8.00 | 8.00 | 446.6 | False | NA | NA/NA |
| 2026-07-04T15:00:22+00:00 | Shanghai | 2026-07-05 | 28 | price_tier_6_8_10_shares | 0.054 | 6.00 | 0.00 | NA | False | NA | NA/NA |
| 2026-07-04T19:11:22+00:00 | Helsinki | 2026-07-05 | 17 | price_tier_6_8_10_shares | 0.073 | 6.00 | 0.00 | NA | False | NA | 0.150/0.180 |

## Candidate Live Change

Do **not** enable blanket taker fallback from this report. The safer next implementation is shadow-only lifecycle tracking:

- every maker order gets a virtual TTL ladder at 15/30/60/120 minutes;
- log whether the book moved down, the maker is behind bid, a reprice is possible, or a taker fallback is actually tight enough;
- after settlement, score `saved_missed_winner`, `cheaper_reentry_saved_cost`, `paid_extra_loser`, `fee_paid`, and `pnl_delta_vs_maker_only`;
- only promote if forward settled deltas are positive on the same real orders.

If a tiny live fallback is later approved, the least aggressive candidate is a three-way TTL refresh rather than a blind sweep: cancel/repost lower when the book moved down, reprice maker when the old order is behind bid, and taker only when `spread<=1c`, `best_ask<=posted_price+1c`, the 20c hard band still holds, and official weather taker fee leaves edge. Current evidence is not enough to turn this on.

## Artifacts

- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/order_lifecycle_rows.csv`
- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/fallback_policy_rows.csv`
- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/fallback_policy_summary.csv`
- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/reprice_diagnostics.csv`
- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/summary.json`
