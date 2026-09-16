# Low-Price YES Take-Profit Replay v1

Generated: 2026-07-03T08:56:36.229519+00:00

## Verdict

This is an execution overlay test for the forecast-tail low-price YES sleeve, not a new weather signal.
The backtest uses future point-in-time YES best bid: a take-profit only counts when a later snapshot has an executable bid at or above the threshold. Friction profiles then stress the quoted bid/ask path with entry add-ons, exit haircuts, and gross fee-rate assumptions.

**2026-07-03 correction / live-like caveat:** this report's TP rows credit exits at the later observed future bid (or stressed future bid), so `bid>=0.20` can realize above 20c. The live overlay currently pre-places a resting `SELL YES @0.20`; that execution is closer to a fixed-20c exit and should not be compared to the max-bid headline. See [2026-07-03-low-price-yes-sizing-stop-v1.md](2026-07-03-low-price-yes-sizing-stop-v1.md) for the live-like replay: fixed `$0.80` + fixed-20c TP is -9.1% ROI full-window, while fixed `$0.80` + fixed-20c TP + simple time/salvage stop is +11.6% with CI crossing 0.

**2026-07-04 data-audit caveat:** 78/476 May rows have no future bid path at all (they degrade to hold inside every TP policy) and 15 winners have no bid path (they can never be capped). The TP-vs-hold deltas in this report are therefore effectively driven by June data, and the live-like fixed-20c damage estimate is, if anything, understated. See [2026-07-04-low-price-yes-data-audit-hot-tail-boundary-v1.md](2026-07-04-low-price-yes-data-audit-hot-tail-boundary-v1.md) §1.2.

```text
significance=PASS for quoted and +1c/-1c stressed full-window full-sell 20c/30c paired delta vs hold
baseline=PASS versus same-denominator hold-to-settlement on full-window replay after friction stress
forward=NA/FAIL because the take-profit rule was not pre-registered and fresh live-forward exits are not observed yet
conclusion=shadow_candidate_exit_overlay; user-approved tiny-live TP20 monitor is allowed for the existing V1 sleeve only, not a selector confirmation or size-up approval
```

First-principles read: full take-profit fights the convex nature of a low-price YES sleeve. It can rescue tickets that pump from 10c to 30c and later lose, but it also caps the rare tickets that settle at 100c. Once friction is included, full-sell still looks stronger than stake-recovery in historical replay; the unresolved live question is fill probability, not just ROI math.

## Tiny-Live Exit Rollout

> **2026-07-03 15:21Z superseded:** the TP20 exit overlay was disabled the same day
> (`disable_tp20_exit_overlay`, all resting SELL orders canceled, LaunchAgent removed) after the
> live-like fixed-20c replay in [sizing-stop v1](2026-07-03-low-price-yes-sizing-stop-v1.md) showed
> -9.1% full-window vs hold +25.9%. Live posture is hold-to-settlement; TP/stop stay shadow telemetry.

2026-07-03 operator decision: run a micro live TP20 exit overlay on Mac for the already-live V1 low-price YES sleeve.

- Script: `scripts/ops/low_price_yes_take_profit_exit_v1.py`.
- LaunchAgent: `com.pm-agents.low-price-yes-take-profit-exit`.
- Status command: `scripts/ops/status_low_price_yes_take_profit_exit.sh`.
- Trigger: wallet position exists for a token bought by `low_price_yes_lottery_tiny_live_v1`, and target date is not past.
- Execution: full-position SELL. If fresh best bid is below `0.20`, immediately pre-place a resting `SELL YES @0.20` maker order. If the monitor first sees the market already bid `>=0.20`, use `best_bid + tick` maker when possible; taker fallback is enabled only after a maker order has been canceled.
- Refresh/cancel: maker TTL is `14400s`; expired maker orders are canceled through `weather_order_executor.py --cancel-expired` and then re-posted if the wallet still holds the position.
- Monitoring files: `runtime/weather_edge_v1/low_price_yes_take_profit_exit_v1/exit_decisions.jsonl`, `trade_plans.jsonl`, `latest_summary.json`, and `runtime/weather_edge_v1/live/low_price_yes_take_profit_exit_v1_orders*.jsonl`.

Initial verification at `2026-07-03T09:18:48Z`: 9 entry BUY tokens, 9 matched wallet positions, 7 below TP20 bid, 2 past target date, 0 exit plans, 0 live exit orders, 0 cancel attempts. A LaunchAgent network issue was fixed by making the positions client use the same `LOW_PRICE_YES_LOTTERY_MARKET_PROXY` proxy as CLOB book fetches.

Implementation update at `2026-07-03T09:40:32Z`: fixed TP20 maker pre-placement is live. Seven non-expired wallet positions have resting maker sell orders at `0.20`, all accepted by CLOB with `place.status=live`; the two past-target-date positions remain skipped. The executor's maker-only cross check was made side-aware so `SELL @0.20` above the current ask is treated as a valid resting sell order, not a BUY-style crossing order.

## Data Snapshot

- Input denominator: `docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv`.
- Rows: 476 city-date-bracket candidates; dates 2026-05-06..2026-06-30.
- Snapshot files scanned: 2829 under `runtime/weather_edge_v1/market_data/paper_snapshots`.
- Future bid path coverage: 83.2%.
- Friction profiles: quoted bid/ask only; sell minus 1c; entry +1c and sell minus 1c; entry +1c and sell minus 2c; entry +1c, sell minus 1c, plus 1% gross notional fee stress.
- Dashboard stack note: `run_stack.sh --no-rebuild` was attempted after sync, but frontend port 5174 stayed busy; this replay reads the refreshed mirror snapshots and existing generated denominator directly.

## Focus Policies

| policy | friction | period | rows | dates | win_rate_final | avg_entry | tp_hit_rate | roi | delta_vs_hold | delta_ci_low | delta_ci_high | roi_ci_low | roi_ci_high | top5_removed_roi | losing_days | le_minus50pct_days | max_daily_loss_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold_to_settlement | quoted_bidask | full | 476 | 53 | +13.7% | 0.105 | +0.0% | +25.9% | +0.0% | +0.0% | +0.0% | -3.5% | +78.0% | +9.7% | 27 | 18 | -100.0% |
| hold_to_settlement | sell_minus_1c | full | 476 | 53 | +13.7% | 0.105 | +0.0% | +25.9% | +0.0% | +0.0% | +0.0% | -3.5% | +78.0% | +9.7% | 27 | 18 | -100.0% |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +13.7% | 0.115 | +0.0% | +13.9% | +0.0% | +0.0% | +0.0% | -12.3% | +58.7% | +0.0% | 29 | 20 | -100.0% |
| full_sell_bid_ge_0p20 | quoted_bidask | full | 476 | 53 | +13.7% | 0.105 | +32.4% | +94.2% | +68.3% | +41.4% | +76.7% | +58.3% | +134.5% | +78.8% | 14 | 6 | -100.0% |
| full_sell_bid_ge_0p20 | sell_minus_1c | full | 476 | 53 | +13.7% | 0.105 | +32.4% | +91.8% | +65.9% | +39.7% | +74.1% | +56.2% | +132.2% | +76.4% | 14 | 7 | -100.0% |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +13.7% | 0.115 | +31.3% | +72.5% | +58.6% | +35.4% | +66.1% | +41.1% | +107.2% | +59.4% | 15 | 7 | -100.0% |
| full_sell_bid_ge_0p30 | quoted_bidask | full | 476 | 53 | +13.7% | 0.105 | +21.2% | +69.2% | +43.3% | +23.8% | +50.0% | +34.1% | +114.6% | +53.5% | 19 | 10 | -100.0% |
| full_sell_bid_ge_0p30 | sell_minus_1c | full | 476 | 53 | +13.7% | 0.105 | +21.2% | +67.9% | +42.0% | +22.9% | +48.6% | +32.9% | +113.1% | +52.3% | 20 | 10 | -100.0% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +13.7% | 0.115 | +21.2% | +52.0% | +38.1% | +21.0% | +44.1% | +20.9% | +90.8% | +38.7% | 22 | 10 | -100.0% |
| full_sell_bid_ge_0p40 | quoted_bidask | full | 476 | 53 | +13.7% | 0.105 | +15.8% | +53.2% | +27.3% | +11.4% | +32.9% | +18.9% | +99.0% | +37.4% | 23 | 11 | -100.0% |
| full_sell_bid_ge_0p40 | sell_minus_1c | full | 476 | 53 | +13.7% | 0.105 | +15.8% | +52.4% | +26.5% | +10.9% | +32.1% | +18.2% | +98.2% | +36.6% | 23 | 11 | -100.0% |
| full_sell_bid_ge_0p40 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +13.7% | 0.115 | +15.8% | +37.8% | +23.9% | +9.8% | +28.8% | +7.2% | +76.7% | +24.4% | 26 | 11 | -100.0% |
| recover_stake_bid_ge_0p30 | quoted_bidask | full | 476 | 53 | +13.7% | 0.105 | +21.2% | +36.7% | +10.8% | +6.5% | +12.3% | +6.1% | +86.9% | +20.6% | 24 | 14 | -100.0% |
| recover_stake_bid_ge_0p30 | sell_minus_1c | full | 476 | 53 | +13.7% | 0.105 | +21.2% | +36.7% | +10.8% | +6.5% | +12.3% | +6.1% | +86.9% | +20.6% | 24 | 14 | -100.0% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +13.7% | 0.115 | +21.2% | +24.7% | +10.8% | +6.5% | +12.3% | -2.7% | +67.5% | +11.0% | 26 | 14 | -100.0% |
| recover_stake_bid_ge_3x | quoted_bidask | full | 476 | 53 | +13.7% | 0.105 | +20.2% | +35.7% | +9.8% | +5.1% | +11.0% | +4.8% | +84.9% | +19.6% | 25 | 14 | -100.0% |
| recover_stake_bid_ge_3x | sell_minus_1c | full | 476 | 53 | +13.7% | 0.105 | +20.2% | +35.7% | +9.7% | +5.0% | +11.0% | +4.8% | +84.8% | +19.5% | 25 | 14 | -100.0% |
| recover_stake_bid_ge_3x | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +13.7% | 0.115 | +16.8% | +20.3% | +6.4% | +2.8% | +7.3% | -6.6% | +62.8% | +6.5% | 27 | 14 | -100.0% |

## Recent / Forward Windows

| policy | friction | period | rows | dates | tp_hit_rate | roi | delta_vs_hold | delta_ci_low | delta_ci_high | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold_to_settlement | quoted_bidask | holdout_2026_06_21_26 | 74 | 6 | +0.0% | +43.9% | +0.0% | +0.0% | +0.0% | -40.9% | +153.4% | -34.5% |
| hold_to_settlement | quoted_bidask | closed_forward_2026_06_27_30 | 19 | 3 | +0.0% | +65.9% | +0.0% | +0.0% | +0.0% | -48.1% | +809.1% | -100.0% |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | holdout_2026_06_21_26 | 74 | 6 | +0.0% | +30.7% | +0.0% | +0.0% | +0.0% | -44.4% | +127.5% | -38.6% |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +0.0% | +46.7% | +0.0% | +0.0% | +0.0% | -50.9% | +669.2% | -100.0% |
| full_sell_bid_ge_0p30 | quoted_bidask | holdout_2026_06_21_26 | 74 | 6 | +35.1% | +127.0% | +83.0% | +50.2% | +120.6% | +36.0% | +233.6% | +55.5% |
| full_sell_bid_ge_0p30 | quoted_bidask | closed_forward_2026_06_27_30 | 19 | 3 | +15.8% | +80.0% | +14.0% | -54.6% | +54.0% | -27.4% | +809.1% | -100.0% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | holdout_2026_06_21_26 | 74 | 6 | +35.1% | +103.0% | +72.3% | +45.3% | +103.3% | +25.1% | +196.9% | +39.9% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +15.8% | +57.9% | +11.2% | -51.9% | +47.6% | -33.7% | +669.2% | -100.0% |
| recover_stake_bid_ge_0p30 | quoted_bidask | holdout_2026_06_21_26 | 74 | 6 | +35.1% | +62.7% | +18.8% | +13.6% | +23.9% | -24.0% | +172.6% | -14.3% |
| recover_stake_bid_ge_0p30 | quoted_bidask | closed_forward_2026_06_27_30 | 19 | 3 | +15.8% | +67.2% | +1.3% | -12.5% | +9.0% | -39.1% | +809.1% | -100.0% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | holdout_2026_06_21_26 | 74 | 6 | +35.1% | +49.4% | +18.7% | +13.5% | +23.9% | -27.5% | +146.4% | -18.4% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +15.8% | +47.8% | +1.1% | -13.0% | +9.0% | -41.9% | +669.2% | -100.0% |
| recover_stake_bid_ge_3x | quoted_bidask | holdout_2026_06_21_26 | 74 | 6 | +33.8% | +61.3% | +17.4% | +7.9% | +25.2% | -18.6% | +162.5% | -15.7% |
| recover_stake_bid_ge_3x | quoted_bidask | closed_forward_2026_06_27_30 | 19 | 3 | +21.1% | +72.5% | +6.5% | -12.5% | +18.1% | -30.0% | +809.1% | -100.0% |
| recover_stake_bid_ge_3x | entry_plus_1c_sell_minus_1c | holdout_2026_06_21_26 | 74 | 6 | +29.7% | +44.0% | +13.3% | +5.4% | +21.2% | -25.3% | +131.8% | -24.2% |
| recover_stake_bid_ge_3x | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +15.8% | +47.8% | +1.1% | -13.0% | +9.0% | -41.9% | +669.2% | -100.0% |

## Adaptive / Score-Aware Policies

These are pre-declared diagnostics, not fitted live rules. Cost-aware exits use the original entry ask; score-aware exits use existing `model_p_yes`, `edge`, `p_cal_no_city_ev`, `source_aware_v3`, and METAR-integrated score columns from the forecast-tail denominator.

| policy | friction | period | rows | dates | tp_hit_rate | roi | delta_vs_hold | delta_ci_low | delta_ci_high | roi_ci_low | roi_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +0.0% | +13.9% | +0.0% | +0.0% | +0.0% | -12.3% | +58.7% | +0.0% |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +0.0% | +46.7% | +0.0% | +0.0% | +0.0% | -50.9% | +669.2% | -100.0% |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +0.0% | +34.0% | +0.0% | +0.0% | +0.0% | -22.3% | +262.7% | -27.8% |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +31.3% | +72.5% | +58.6% | +35.4% | +66.1% | +41.1% | +107.2% | +59.4% |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +31.6% | +85.6% | +38.8% | -51.9% | +95.4% | -33.7% | +669.2% | -79.7% |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +40.9% | +115.7% | +81.7% | +25.3% | +102.8% | +46.5% | +309.5% | +59.2% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +21.2% | +52.0% | +38.1% | +21.0% | +44.1% | +20.9% | +90.8% | +38.7% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +15.8% | +57.9% | +11.2% | -51.9% | +47.6% | -33.7% | +669.2% | -100.0% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +31.2% | +93.8% | +59.8% | +13.2% | +80.4% | +23.1% | +300.7% | +36.0% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +21.2% | +24.7% | +10.8% | +6.5% | +12.3% | -2.7% | +67.5% | +11.0% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +15.8% | +47.8% | +1.1% | -13.0% | +9.0% | -41.9% | +669.2% | -100.0% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +31.2% | +49.1% | +15.1% | +4.0% | +19.5% | -10.8% | +269.3% | -11.7% |
| adaptive_entry_2x_floor20_cap30_full | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +27.7% | +66.9% | +53.0% | +31.6% | +60.0% | +36.1% | +102.4% | +53.7% |
| adaptive_entry_2x_floor20_cap30_full | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +26.3% | +79.9% | +33.1% | -51.9% | +85.5% | -33.7% | +669.2% | -87.4% |
| adaptive_entry_2x_floor20_cap30_full | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +37.6% | +109.8% | +75.9% | +22.8% | +96.4% | +42.6% | +305.5% | +53.0% |
| adaptive_entry_le10c_full20_else30 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +25.4% | +63.7% | +49.8% | +30.0% | +55.5% | +32.2% | +100.2% | +50.5% |
| adaptive_entry_le10c_full20_else30 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +21.1% | +70.6% | +23.9% | -51.9% | +69.5% | -33.7% | +669.2% | -100.0% |
| adaptive_entry_le10c_full20_else30 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +35.5% | +106.0% | +72.0% | +20.4% | +92.5% | +36.8% | +304.2% | +48.9% |
| adaptive_model_high_full30_else20 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +23.5% | +56.9% | +43.0% | +25.3% | +49.6% | +25.9% | +95.8% | +43.7% |
| adaptive_model_high_full30_else20 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +21.1% | +70.6% | +23.9% | -51.9% | +69.5% | -33.7% | +669.2% | -100.0% |
| adaptive_model_high_full30_else20 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +32.3% | +96.4% | +62.4% | +15.0% | +83.0% | +27.0% | +300.7% | +38.8% |
| adaptive_model_high_recover30_else_full20 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +23.5% | +33.9% | +20.0% | +11.3% | +24.2% | +6.4% | +75.8% | +20.3% |
| adaptive_model_high_recover30_else_full20 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +21.1% | +48.2% | +1.5% | -51.9% | +30.9% | -33.7% | +669.2% | -100.0% |
| adaptive_model_high_recover30_else_full20 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +32.3% | +60.7% | +26.7% | -3.7% | +42.6% | -0.9% | +273.8% | +0.5% |
| adaptive_path_high_full30_else20 | entry_plus_1c_sell_minus_1c | full | 476 | 53 | +28.2% | +68.3% | +54.5% | +31.8% | +60.7% | +35.4% | +103.7% | +55.2% |
| adaptive_path_high_full30_else20 | entry_plus_1c_sell_minus_1c | closed_forward_2026_06_27_30 | 19 | 3 | +21.1% | +70.6% | +23.9% | -51.9% | +69.5% | -33.7% | +669.2% | -100.0% |
| adaptive_path_high_full30_else20 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 93 | 9 | +37.6% | +111.1% | +77.1% | +22.5% | +98.3% | +41.9% | +306.8% | +54.3% |

## Best Stressed TP By Diagnostic Slice

Each row picks the best non-hold TP policy inside that diagnostic slice under +1c entry / -1c exit stress. This table is for understanding where TP helps; it is not a permission slip to cherry-pick slices.

| slice | slice_value | policy | rows | dates | tp_hit_rate | roi | delta_vs_hold | delta_ci_low | delta_ci_high | top5_removed_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| entry_band | entry_10_15c | full_sell_bid_ge_0p20 | 137 | 48 | +43.1% | +70.8% | +71.2% | +31.5% | +88.5% | +46.5% |
| entry_band | entry_7_10c | full_sell_bid_ge_0p20 | 130 | 48 | +24.6% | +73.1% | +59.6% | +35.0% | +91.4% | +33.5% |
| entry_band | entry_<=7c | full_sell_bid_ge_0p20 | 125 | 49 | +16.8% | +54.3% | +44.7% | +12.4% | +66.3% | +2.1% |
| entry_band | entry_>15c | full_sell_bid_ge_0p20 | 84 | 41 | +44.0% | +101.5% | +57.2% | +26.9% | +71.8% | +76.6% |
| model_quality_bucket | model_quality_high | full_sell_bid_ge_0p20 | 362 | 52 | +34.3% | +86.4% | +62.7% | +35.7% | +68.9% | +69.9% |
| model_quality_bucket | model_quality_low | full_sell_bid_ge_0p20 | 114 | 48 | +21.9% | +28.5% | +45.7% | +24.9% | +76.0% | -18.5% |
| path_quality_bucket | path_quality_high | full_sell_bid_ge_0p20 | 105 | 46 | +46.7% | +175.7% | +61.2% | +12.9% | +76.0% | +120.4% |
| path_quality_bucket | path_quality_low | full_sell_bid_ge_0p20 | 371 | 53 | +27.0% | +43.3% | +57.9% | +32.2% | +65.3% | +27.0% |
| pcal_ev_band | pcal_ev_25_40pp | adaptive_path_high_full30_else20 | 112 | 44 | +24.1% | +11.3% | +57.8% | +22.4% | +79.5% | -29.1% |
| pcal_ev_band | pcal_ev_40_55pp | full_sell_bid_ge_0p20 | 151 | 48 | +33.8% | +94.1% | +59.6% | +30.6% | +66.2% | +55.3% |
| pcal_ev_band | pcal_ev_<25pp | full_sell_bid_ge_0p20 | 111 | 47 | +27.9% | +82.7% | +47.4% | +28.5% | +99.9% | +30.0% |
| pcal_ev_band | pcal_ev_>=55pp | full_sell_bid_ge_0p20 | 102 | 44 | +35.3% | +97.3% | +70.9% | +35.8% | +87.2% | +44.9% |

## Interpretation

- Hold-to-settlement full-window quoted-bidask ROI is +25.9%; full sell at 30c is +69.2% with delta +43.3%.
- Under +1c entry / -1c exit stress, full sell at 30c is +52.0% with delta +38.1%.
- Stake recovery at 30c is +36.7% with delta +10.8%.
- Cost-aware full exit `2x floor20 cap30` is +66.9% under +1c/-1c stress; score-aware `model_high full30 else20` is +56.9%; score-aware `model_high recover30 else full20` is +33.9%.
- The cost/model adaptive policies are useful diagnostics but do not beat the simple `full_sell_bid_ge_0p20` benchmark in this replay; do not promote them to live exit rules without fresh-forward telemetry.
- If full-sell improves in a slice but stake-recovery does not, the slice is probably dominated by temporary market repricing rather than true tail probability.
- If stake-recovery improves with similar or lower drawdown, it is a cleaner candidate because it preserves convex payout after de-risking.
- Current evidence is enough for a tiny live TP20 exit overlay on already-open V1 positions, but not enough to confirm a strategy alpha, change the entry selector, or increase size. Continue collecting first bid>=20/30/40c, max future bid, maker fill/cancel/taker fallback, and final settlement.

## Generated Artifacts

- `docs/analysis/2026-07/generated/low_price_yes_take_profit_v1/candidate_path_stats.csv`
- `docs/analysis/2026-07/generated/low_price_yes_take_profit_v1/policy_rows.csv`
- `docs/analysis/2026-07/generated/low_price_yes_take_profit_v1/policy_summary.csv`
- `docs/analysis/2026-07/generated/low_price_yes_take_profit_v1/slice_policy_summary.csv`
- `docs/analysis/2026-07/2026-07-03-low-price-yes-take-profit-v1.json`
- `scripts/ops/low_price_yes_take_profit_exit_v1.py`
- `scripts/ops/status_low_price_yes_take_profit_exit.sh`
