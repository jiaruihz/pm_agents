# HeadA Low-Price YES Sizing / Stop Replay v1

Generated: 2026-07-03T15:18:36.072778+00:00

## Verdict

This is an execution-layer audit for HeadA (`forecast_tail_low_price_yes`), not a new entry alpha search.

```text
conclusion=inconclusive_for_live_change
entry_selector=unchanged
live_action=do_not_size_up; fix token-resolution plumbing first; keep any sizing/stop changes in shadow until fresh-forward fills
```

Plain English: today's miss does not by itself disprove HeadA, but the replay found a real mismatch. The earlier TP replay credited `TP20` at the later maximum executable bid. The live overlay mostly pre-places `SELL @0.20`, so a true live-like replay should cap that exit at 20c. That makes the TP overlay much less magical and explains why the live sleeve can feel worse than the headline replay.

Deployment read after the no-TP stop extension: do not deploy `time_stop` or `late_salvage` yet. The no-TP `hold_plus_time_stop_or_late_salvage` line is worse than plain hold in full, recent, and closed-forward windows, so the current live posture should be hold-to-settlement plus shadow telemetry for stop/salvage triggers.

## Data Snapshot

- Input denominator: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv`.
- Rows: 476 candidates; dates 2026-05-06..2026-06-30; cities 48.
- Snapshot files: `runtime/weather_edge_v1/market_data/paper_snapshots`; path rows with future quote 475/476, with future bid 396/476.
- Path events found: TP20 rows 161, time-stop rows 196, late-salvage rows 27.
- This replay uses only snapshots after `decision_snapshot_ts_utc` for exits. It does not change the entry selector.

## Main Same-Denominator Results

Full window:

| sizing | exit | rows | dates | avg cost | TP hit | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | hold | 476 | 53 | $0.80 | 0.0% | 0.0% | +25.9% | [-6.1%, +61.3%] | 27 | 18 | $-10.40 / -100.0% |
| fixed_cash_0p80 | hold_plus_time_stop_or_late_salvage | 476 | 53 | $0.80 | 0.0% | 45.0% | +10.5% | [-13.2%, +36.1%] | 28 | 15 | $-8.29 / -100.0% |
| fixed_cash_0p80 | tp20_maxbid_backtest_style | 476 | 53 | $0.80 | 33.8% | 0.0% | +96.0% | [+66.7%, +127.2%] | 13 | 6 | $-5.75 / -100.0% |
| fixed_cash_0p80 | tp20_live_fixed20 | 476 | 53 | $0.80 | 33.8% | 0.0% | -9.1% | [-26.0%, +12.3%] | 38 | 11 | $-7.03 / -100.0% |
| fixed_cash_0p80 | tp20_live_fixed20_plus_time_stop_or_late_salvage | 476 | 53 | $0.80 | 33.8% | 32.4% | +11.6% | [-3.4%, +31.1%] | 26 | 5 | $-4.00 / -100.0% |
| fixed_8_shares | hold | 476 | 53 | $0.84 | 0.0% | 0.0% | +29.9% | [-1.2%, +64.1%] | 25 | 14 | $-11.39 / -100.0% |
| fixed_8_shares | hold_plus_time_stop_or_late_salvage | 476 | 53 | $0.84 | 0.0% | 45.0% | +20.0% | [-6.6%, +49.2%] | 25 | 17 | $-9.55 / -100.0% |
| fixed_8_shares | tp20_live_fixed20 | 476 | 53 | $0.84 | 33.8% | 0.0% | -5.7% | [-24.5%, +19.5%] | 39 | 10 | $-6.55 / -100.0% |
| edge_scaled_8_shares | hold_plus_time_stop_or_late_salvage | 476 | 53 | $0.81 | 0.0% | 45.0% | +17.4% | [-9.7%, +48.2%] | 25 | 18 | $-9.67 / -100.0% |
| edge_scaled_8_shares | tp20_live_fixed20 | 476 | 53 | $0.81 | 33.8% | 0.0% | -9.1% | [-27.4%, +15.8%] | 37 | 13 | $-8.54 / -100.0% |
| pcal_scaled_8_shares | hold_plus_time_stop_or_late_salvage | 476 | 53 | $0.84 | 0.0% | 45.0% | +18.8% | [-6.5%, +46.5%] | 24 | 16 | $-9.52 / -100.0% |
| pcal_scaled_8_shares | tp20_live_fixed20 | 476 | 53 | $0.84 | 33.8% | 0.0% | -4.2% | [-21.4%, +18.3%] | 37 | 9 | $-6.09 / -100.0% |
| payout25_cap5 | hold_plus_time_stop_or_late_salvage | 476 | 53 | $2.63 | 0.0% | 45.0% | +20.0% | [-6.6%, +49.2%] | 25 | 17 | $-29.85 / -100.0% |
| payout25_cap5 | tp20_live_fixed20 | 476 | 53 | $2.63 | 33.8% | 0.0% | -5.7% | [-24.5%, +19.5%] | 39 | 10 | $-20.48 / -100.0% |

Recent window (`target_date >= 2026-06-21`):

| sizing | exit | rows | dates | avg cost | TP hit | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | hold | 93 | 9 | $0.80 | 0.0% | 0.0% | +48.4% | [-24.0%, +149.0%] | 4 | 3 | $-6.35 / -56.7% |
| fixed_cash_0p80 | hold_plus_time_stop_or_late_salvage | 93 | 9 | $0.80 | 0.0% | 46.2% | +1.8% | [-26.4%, +27.8%] | 6 | 2 | $-4.23 / -67.3% |
| fixed_cash_0p80 | tp20_maxbid_backtest_style | 93 | 9 | $0.80 | 44.1% | 0.0% | +145.3% | [+72.7%, +236.7%] | 1 | 0 | $-1.31 / -27.4% |
| fixed_cash_0p80 | tp20_live_fixed20 | 93 | 9 | $0.80 | 44.1% | 0.0% | -13.4% | [-31.7%, +3.7%] | 6 | 1 | $-4.09 / -74.6% |
| fixed_cash_0p80 | tp20_live_fixed20_plus_time_stop_or_late_salvage | 93 | 9 | $0.80 | 44.1% | 24.7% | +2.6% | [-16.1%, +21.3%] | 5 | 1 | $-3.58 / -74.6% |
| fixed_8_shares | hold | 93 | 9 | $0.85 | 0.0% | 0.0% | +51.2% | [+1.0%, +115.8%] | 4 | 0 | $-2.96 / -27.0% |
| fixed_8_shares | hold_plus_time_stop_or_late_salvage | 93 | 9 | $0.85 | 0.0% | 46.2% | +21.0% | [-11.9%, +49.5%] | 3 | 2 | $-5.46 / -82.0% |
| fixed_8_shares | tp20_live_fixed20 | 93 | 9 | $0.85 | 44.1% | 0.0% | -17.4% | [-34.4%, -1.8%] | 6 | 2 | $-6.16 / -61.2% |
| edge_scaled_8_shares | hold_plus_time_stop_or_late_salvage | 93 | 9 | $0.81 | 0.0% | 46.2% | +22.8% | [-7.2%, +51.6%] | 2 | 2 | $-4.53 / -81.7% |
| edge_scaled_8_shares | tp20_live_fixed20 | 93 | 9 | $0.81 | 44.1% | 0.0% | -19.0% | [-37.3%, -2.3%] | 6 | 2 | $-6.27 / -64.6% |
| pcal_scaled_8_shares | hold_plus_time_stop_or_late_salvage | 93 | 9 | $0.84 | 0.0% | 46.2% | +0.3% | [-28.1%, +22.7%] | 3 | 2 | $-6.76 / -80.3% |
| pcal_scaled_8_shares | tp20_live_fixed20 | 93 | 9 | $0.84 | 44.1% | 0.0% | -13.5% | [-29.3%, +1.6%] | 7 | 1 | $-4.33 / -76.2% |
| payout25_cap5 | hold_plus_time_stop_or_late_salvage | 93 | 9 | $2.67 | 0.0% | 46.2% | +21.0% | [-11.9%, +49.5%] | 3 | 2 | $-17.06 / -82.0% |
| payout25_cap5 | tp20_live_fixed20 | 93 | 9 | $2.67 | 44.1% | 0.0% | -17.4% | [-34.4%, -1.8%] | 6 | 2 | $-19.26 / -61.2% |

Closed forward (`2026-06-27..2026-06-30`):

| sizing | exit | rows | dates | avg cost | TP hit | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_cash_0p80 | hold | 19 | 3 | $0.80 | 0.0% | 0.0% | +65.9% | [-48.1%, +809.1%] | 1 | 0 | $-4.23 / -48.1% |
| fixed_cash_0p80 | hold_plus_time_stop_or_late_salvage | 19 | 3 | $0.80 | 0.0% | 15.8% | -18.5% | [-67.3%, +27.2%] | 2 | 1 | $-3.04 / -67.3% |
| fixed_cash_0p80 | tp20_maxbid_backtest_style | 19 | 3 | $0.80 | 36.8% | 0.0% | +112.1% | [-27.4%, +807.3%] | 1 | 0 | $-1.31 / -27.4% |
| fixed_cash_0p80 | tp20_live_fixed20 | 19 | 3 | $0.80 | 36.8% | 0.0% | -25.3% | [-74.6%, +81.8%] | 2 | 1 | $-3.58 / -74.6% |
| fixed_cash_0p80 | tp20_live_fixed20_plus_time_stop_or_late_salvage | 19 | 3 | $0.80 | 36.8% | 5.3% | -23.3% | [-74.6%, +81.8%] | 2 | 1 | $-3.58 / -74.6% |
| fixed_8_shares | hold | 19 | 3 | $0.80 | 0.0% | 0.0% | +58.5% | [-15.1%, +400.0%] | 1 | 0 | $-1.42 / -15.1% |
| fixed_8_shares | hold_plus_time_stop_or_late_salvage | 19 | 3 | $0.80 | 0.0% | 15.8% | +14.4% | [-82.0%, +94.0%] | 2 | 1 | $-1.31 / -82.0% |
| fixed_8_shares | tp20_live_fixed20 | 19 | 3 | $0.80 | 36.8% | 0.0% | -26.0% | [-61.2%, +0.0%] | 2 | 1 | $-2.52 / -61.2% |
| edge_scaled_8_shares | hold_plus_time_stop_or_late_salvage | 19 | 3 | $0.77 | 0.0% | 15.8% | +33.9% | [-81.7%, +77.2%] | 1 | 1 | $-1.03 / -81.7% |
| edge_scaled_8_shares | tp20_live_fixed20 | 19 | 3 | $0.77 | 36.8% | 0.0% | -27.6% | [-64.6%, +1.8%] | 2 | 1 | $-2.57 / -64.6% |
| pcal_scaled_8_shares | hold_plus_time_stop_or_late_salvage | 19 | 3 | $0.76 | 0.0% | 15.8% | -33.1% | [-80.3%, +19.2%] | 2 | 1 | $-4.78 / -80.3% |
| pcal_scaled_8_shares | tp20_live_fixed20 | 19 | 3 | $0.76 | 36.8% | 0.0% | -26.8% | [-76.2%, +9.5%] | 2 | 1 | $-2.56 / -76.2% |
| payout25_cap5 | hold_plus_time_stop_or_late_salvage | 19 | 3 | $2.49 | 0.0% | 15.8% | +14.4% | [-82.0%, +94.0%] | 2 | 1 | $-4.10 / -82.0% |
| payout25_cap5 | tp20_live_fixed20 | 19 | 3 | $2.49 | 36.8% | 0.0% | -26.0% | [-61.2%, +0.0%] | 2 | 1 | $-7.89 / -61.2% |

## TP20 Replay Mismatch

| policy | ROI | TP hit | note |
| --- | ---: | ---: | --- |
| hold, current $0.80 cash sizing | +25.9% | 0.0% | settlement-only baseline |
| old-style TP20 credited at max future bid | +96.0% | 33.8% | optimistic versus pre-posted SELL @0.20 |
| live-like TP20 fixed sell at 20c | -9.1% | 33.8% | closer to current live overlay |
| live-like TP20 plus simple stop/salvage | +11.6% | 33.8% | tests whether stops rescue losers |
| hold plus simple stop/salvage, no TP20 | +10.5% | 0.0% | tests your proposed conditional stop without capping winners |

The important point is not the exact point estimate. It is that `TP20 max-bid` and `SELL @0.20` are different execution products. The first is a path-trading oracle unless the live monitor cancels/requotes upward quickly enough. The current live behavior is closer to the fixed-20c line.

## Sizing Read

Fixed cash spends the same dollar amount on 5c and 15c tickets, which means the cheaper and usually farther-tail ticket gets more shares. Fixed-share sizing is closer to the original research thesis: each row gets similar maximum payout, and cash at risk rises with price/confidence. Edge/pcal-scaled fixed shares are directionally sensible, but this replay alone does not confirm them for live because they add score degrees of freedom.

## Stops Read

The tested stops are deliberately simple:

- `time_stop`: two hours after forecast peak, if the ticket never pumped to 15c and current bid is at least 3c, sell at bid minus 1c.
- `late_salvage`: inside the last three hours before settlement, if no TP20 happened and bid is at least 2c, sell at bid minus 1c.

If the combined stop line does not materially improve the live-like TP20 line, then stop-loss is mostly psychological comfort and spread leakage. If it improves drawdown without killing forward ROI, it is a candidate for shadow telemetry before live.

## Today Check

The live 2026-07-03 miss is not impossible under the historical distribution: final win rate is low and zero-win days exist. But today's experience is still useful because it exposed three concrete gaps:

1. Current live sizing is `fixed_cash_0p80`; the research champion was closer to fixed payout/shares.
2. Current TP20 pre-posts at 20c, while the first TP replay headline used max future bid after touch.
3. 2026-07-04 candidates are currently blocked by token resolution, so "not buying the lottery" can be a plumbing failure, not an alpha decision.

## Artifacts

- Runner lifecycle: `superseded-for-now`; the one-off v1 producer was removed
  from the active script tree on 2026-08-12. Its exact source remains recoverable
  as git blob `57c5c4b2428d549be13fbe105d99efc4040a104f`.
- Current owner: `scripts/analysis/forecast_quality/research_low_price_yes_sizing_fee_stop_v2.py`
  keeps the same 476-row entry denominator and corrects official fee/execution math.
- Replay rows: `docs/analysis/2026-07/generated/low_price_yes_sizing_stop_v1/replay_rows.csv`
- Summary: `docs/analysis/2026-07/generated/low_price_yes_sizing_stop_v1/summary.csv`
- JSON: `docs/analysis/2026-07/2026-07-03-low-price-yes-sizing-stop-v1.json`
