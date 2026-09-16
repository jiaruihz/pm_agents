# METAR Reversal Take-Profit Replay v1

Generated: 2026-07-03T13:35:29.680811+00:00

## Verdict

```text
branch: false_fade_reheat_conflict -> BUY d1_yes
same-denominator rows: 32 historical settled triggers
exit verdict: TP is not yet a better default than hold-to-settlement.
recommended live posture: if this branch is promoted, start as hold-to-settlement micro live, while shadow-recording TP20/30/40 paths.
conclusion: shadow_candidate, no TP rule promoted from this replay.
```

This replay tests exits only. It does not change the branch trigger and does not add a new weather gate.
Each policy uses the same 32 trigger rows; no sample mixing.

## Key Stress Table

| policy | friction | period | rows | dates | cities | avg_entry | tp_hit_rate | win_rate_final | roi | delta_vs_hold | delta_ci_low | delta_ci_high | top5_removed_roi | losing_days | le_minus50pct_days | max_daily_loss_roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 15 | 0.185 | +0.0% | +43.8% | +107.6% | +0.0% | +0.0% | +0.0% | +22.5% | 10 | 10 | -100.0% |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 15 | 0.185 | +21.9% | +43.8% | +121.7% | +14.1% | -0.8% | +36.6% | +40.7% | 8 | 8 | -100.0% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 15 | 0.185 | +46.9% | +43.8% | +115.9% | +8.3% | -7.9% | +15.6% | +34.1% | 10 | 9 | -100.0% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 15 | 0.185 | +46.9% | +43.8% | +112.7% | +5.1% | -2.4% | +8.4% | +28.8% | 10 | 10 | -100.0% |

## Focus Policies

| policy | friction | period | rows | dates | avg_entry | tp_hit_rate | roi | delta_vs_hold | roi_ci_low | roi_ci_high | top5_removed_roi | path_coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold_to_settlement | quoted_bidask | full | 32 | 22 | 0.175 | +0.0% | +119.5% | +0.0% | +34.7% | +220.2% | +27.2% | +93.8% |
| hold_to_settlement | quoted_bidask | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| hold_to_settlement | quoted_bidask | month_2026_05 | 13 | 8 | 0.172 | +0.0% | +237.8% | +0.0% | +64.8% | +396.0% | +37.5% | +84.6% |
| hold_to_settlement | quoted_bidask | month_2026_06 | 19 | 14 | 0.177 | +0.0% | +38.6% | +0.0% | -25.2% | +164.9% | -75.4% | +100.0% |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 0.185 | +0.0% | +107.6% | +0.0% | +27.7% | +202.5% | +22.5% | +93.8% |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | month_2026_05 | 13 | 8 | 0.182 | +0.0% | +217.6% | +0.0% | +55.3% | +363.6% | +32.6% | +84.6% |
| hold_to_settlement | entry_plus_1c_sell_minus_1c | month_2026_06 | 19 | 14 | 0.187 | +0.0% | +32.3% | +0.0% | -28.6% | +154.6% | -76.2% | +100.0% |
| full_sell_bid_ge_0p20 | quoted_bidask | full | 32 | 22 | 0.175 | +21.9% | +135.4% | +15.9% | +55.4% | +235.1% | +47.2% | +93.8% |
| full_sell_bid_ge_0p20 | quoted_bidask | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_0p20 | quoted_bidask | month_2026_05 | 13 | 8 | 0.172 | +30.8% | +253.6% | +15.8% | +76.0% | +406.8% | +66.2% | +84.6% |
| full_sell_bid_ge_0p20 | quoted_bidask | month_2026_06 | 19 | 14 | 0.177 | +15.8% | +54.5% | +15.9% | -6.3% | +176.8% | -53.3% | +100.0% |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 0.185 | +21.9% | +121.7% | +14.1% | +47.1% | +214.9% | +40.7% | +93.8% |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | month_2026_05 | 13 | 8 | 0.182 | +30.8% | +231.7% | +14.1% | +66.8% | +371.5% | +59.0% | +84.6% |
| full_sell_bid_ge_0p20 | entry_plus_1c_sell_minus_1c | month_2026_06 | 19 | 14 | 0.187 | +15.8% | +46.5% | +14.2% | -11.0% | +164.2% | -56.3% | +100.0% |
| full_sell_bid_ge_0p30 | quoted_bidask | full | 32 | 22 | 0.175 | +46.9% | +129.4% | +9.9% | +40.3% | +222.3% | +40.3% | +93.8% |
| full_sell_bid_ge_0p30 | quoted_bidask | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_0p30 | quoted_bidask | month_2026_05 | 13 | 8 | 0.172 | +61.5% | +252.4% | +14.6% | +74.9% | +406.0% | +65.3% | +84.6% |
| full_sell_bid_ge_0p30 | quoted_bidask | month_2026_06 | 19 | 14 | 0.177 | +36.8% | +45.2% | +6.7% | -28.5% | +156.7% | -64.7% | +100.0% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 0.185 | +46.9% | +115.9% | +8.3% | +32.4% | +203.4% | +34.1% | +93.8% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | month_2026_05 | 13 | 8 | 0.182 | +61.5% | +230.0% | +12.4% | +65.4% | +370.1% | +58.0% | +84.6% |
| full_sell_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | month_2026_06 | 19 | 14 | 0.187 | +36.8% | +37.9% | +5.5% | -31.6% | +145.5% | -66.6% | +100.0% |
| full_sell_bid_ge_0p40 | quoted_bidask | full | 32 | 22 | 0.175 | +43.8% | +122.2% | +2.7% | +35.3% | +215.8% | +31.8% | +93.8% |
| full_sell_bid_ge_0p40 | quoted_bidask | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_0p40 | quoted_bidask | month_2026_05 | 13 | 8 | 0.172 | +53.8% | +234.7% | -3.1% | +63.5% | +391.2% | +36.6% | +84.6% |
| full_sell_bid_ge_0p40 | quoted_bidask | month_2026_06 | 19 | 14 | 0.177 | +36.8% | +45.2% | +6.7% | -28.5% | +156.7% | -64.7% | +100.0% |
| full_sell_bid_ge_0p40 | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 0.185 | +43.8% | +109.3% | +1.7% | +28.7% | +196.9% | +26.2% | +93.8% |
| full_sell_bid_ge_0p40 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_0p40 | entry_plus_1c_sell_minus_1c | month_2026_05 | 13 | 8 | 0.182 | +53.8% | +213.8% | -3.8% | +53.6% | +357.8% | +31.6% | +84.6% |
| full_sell_bid_ge_0p40 | entry_plus_1c_sell_minus_1c | month_2026_06 | 19 | 14 | 0.187 | +36.8% | +37.9% | +5.5% | -31.6% | +145.5% | -66.6% | +100.0% |
| full_sell_bid_ge_2x | quoted_bidask | full | 32 | 22 | 0.175 | +46.9% | +129.4% | +9.9% | +40.3% | +222.3% | +40.3% | +93.8% |
| full_sell_bid_ge_2x | quoted_bidask | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_2x | quoted_bidask | month_2026_05 | 13 | 8 | 0.172 | +61.5% | +252.4% | +14.6% | +74.9% | +406.0% | +65.3% | +84.6% |
| full_sell_bid_ge_2x | quoted_bidask | month_2026_06 | 19 | 14 | 0.177 | +36.8% | +45.2% | +6.7% | -28.5% | +156.7% | -64.7% | +100.0% |
| full_sell_bid_ge_2x | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 0.185 | +46.9% | +115.9% | +8.3% | +32.4% | +203.4% | +34.1% | +93.8% |
| full_sell_bid_ge_2x | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_2x | entry_plus_1c_sell_minus_1c | month_2026_05 | 13 | 8 | 0.182 | +61.5% | +230.0% | +12.4% | +65.4% | +370.1% | +58.0% | +84.6% |
| full_sell_bid_ge_2x | entry_plus_1c_sell_minus_1c | month_2026_06 | 19 | 14 | 0.187 | +36.8% | +37.9% | +5.5% | -31.6% | +145.5% | -66.6% | +100.0% |
| full_sell_bid_ge_3x | quoted_bidask | full | 32 | 22 | 0.175 | +37.5% | +117.6% | -2.0% | +33.4% | +217.1% | +26.2% | +93.8% |
| full_sell_bid_ge_3x | quoted_bidask | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_3x | quoted_bidask | month_2026_05 | 13 | 8 | 0.172 | +53.8% | +234.7% | -3.1% | +63.5% | +391.2% | +36.6% | +84.6% |
| full_sell_bid_ge_3x | quoted_bidask | month_2026_06 | 19 | 14 | 0.177 | +26.3% | +37.4% | -1.2% | -25.5% | +162.8% | -75.4% | +100.0% |
| full_sell_bid_ge_3x | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 0.185 | +37.5% | +105.1% | -2.5% | +26.2% | +198.8% | +21.3% | +93.8% |
| full_sell_bid_ge_3x | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| full_sell_bid_ge_3x | entry_plus_1c_sell_minus_1c | month_2026_05 | 13 | 8 | 0.182 | +53.8% | +213.8% | -3.8% | +53.6% | +357.8% | +31.6% | +84.6% |
| full_sell_bid_ge_3x | entry_plus_1c_sell_minus_1c | month_2026_06 | 19 | 14 | 0.187 | +26.3% | +30.8% | -1.5% | -29.2% | +151.9% | -76.2% | +100.0% |
| recover_stake_bid_ge_0p30 | quoted_bidask | full | 32 | 22 | 0.175 | +46.9% | +124.8% | +5.3% | +37.9% | +222.1% | +33.7% | +93.8% |
| recover_stake_bid_ge_0p30 | quoted_bidask | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| recover_stake_bid_ge_0p30 | quoted_bidask | month_2026_05 | 13 | 8 | 0.172 | +61.5% | +245.0% | +7.1% | +71.8% | +401.4% | +49.7% | +84.6% |
| recover_stake_bid_ge_0p30 | quoted_bidask | month_2026_06 | 19 | 14 | 0.177 | +36.8% | +42.6% | +4.0% | -25.8% | +162.3% | -69.6% | +100.0% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | full | 32 | 22 | 0.185 | +46.9% | +112.7% | +5.1% | +31.5% | +203.6% | +28.8% | +93.8% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | recent_ge_2026_06_21 | 0 |  |  |  |  |  |  |  |  |  |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | month_2026_05 | 13 | 8 | 0.182 | +61.5% | +224.5% | +7.0% | +60.9% | +368.1% | +44.8% | +84.6% |
| recover_stake_bid_ge_0p30 | entry_plus_1c_sell_minus_1c | month_2026_06 | 19 | 14 | 0.187 | +36.8% | +36.2% | +3.8% | -28.6% | +150.4% | -70.5% | +100.0% |

## Artifacts

- `docs/analysis/2026-07/generated/metar_reversal_take_profit_v1/candidate_path_stats.csv`
- `docs/analysis/2026-07/generated/metar_reversal_take_profit_v1/policy_rows.csv`
- `docs/analysis/2026-07/generated/metar_reversal_take_profit_v1/policy_summary.csv`
- `docs/analysis/2026-07/2026-07-03-metar-reversal-take-profit-v1.json`
