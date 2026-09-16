# HeadA Forward vs Live Denominator Audit v1

Generated: 2026-07-08T12:53:21Z

## Why This Exists

There are three different denominators:

1. `train/holdout opportunity rows`: research rows from `fact_signal_candidates` / generated candidate rows.
2. `fresh append 7/1-7/7`: settled opportunity rows added after the older 6/30 source-quality file.
3. `actual live fills`: real CLOB fills in `fact_trades`, often split across multiple fill rows per ticket.

The source-risk reports use opportunity rows, not actual live fills.

## Data Snapshot

- Candidate rows: `docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v2/candidate_rows.csv`
- `fact_signal_candidates`: 2026-05-05..2026-07-09, built 2026-07-08T09:18:35.918613+00:00
- `settlement_outcomes`: 2026-05-04..2026-07-07

## Window Definitions

`recent_ge_2026_06_21` is the old holdout/recent window used by earlier reports. It includes 6/21..6/30 from the historical generated file plus 7/1..7/7 fresh append.

`fresh_append_2026_07_01_0707` is only the newly appended settled opportunity window. It is not the whole frozen window.

True live performance is a separate denominator in `fact_trades`.

## Opportunity Rows

Recent 6/21+:

| slice | rows | dates | cities | wins | win_rate | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 95 | 16 | 35 | 19 | +20.0% | 78.38 | 73.62 | +93.9% |
| source_quality_mid_high | 57 | 16 | 22 | 16 | +28.1% | 51.08 | 76.92 | +150.6% |
| source_quality_low | 13 | 9 | 6 | 2 | +15.4% | 9.31 | 6.69 | +71.9% |
| source_quality_not_low | 82 | 16 | 30 | 17 | +20.7% | 69.07 | 66.93 | +96.9% |

Holdout 6/21-6/30:

| slice | rows | dates | cities | wins | win_rate | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 58 | 9 | 28 | 10 | +17.2% | 47.53 | 36.47 | +76.7% |
| source_quality_mid_high | 34 | 9 | 17 | 9 | +26.5% | 29.39 | 44.61 | +151.8% |
| source_quality_low | 8 | 5 | 5 | 1 | +12.5% | 6.63 | 3.37 | +50.9% |
| source_quality_not_low | 50 | 9 | 23 | 9 | +18.0% | 40.90 | 33.10 | +80.9% |

Fresh append 7/1-7/7:

| slice | rows | dates | cities | wins | win_rate | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 37 | 7 | 23 | 9 | +24.3% | 30.85 | 37.15 | +120.4% |
| source_quality_mid_high | 23 | 7 | 14 | 7 | +30.4% | 21.69 | 32.31 | +148.9% |
| source_quality_low | 5 | 4 | 3 | 1 | +20.0% | 2.68 | 3.32 | +123.8% |
| source_quality_not_low | 32 | 7 | 20 | 8 | +25.0% | 28.17 | 33.83 | +120.1% |

## Actual Live Fills

| window | fill_rows | tickets | dates | cities | winning_fill_rows | winning_tickets | win_rate_ticket | cost_usd | pnl_usd | roi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| live_all | 41 | 21 | 6 | 16 | 4 | 2 | +9.5% | 21.92 | -2.12 | -9.7% |
| live_2026_07_01_0707 | 41 | 21 | 6 | 16 | 4 | 2 | +9.5% | 21.92 | -2.12 | -9.7% |
| live_2026_07_02_0707 | 41 | 21 | 6 | 16 | 4 | 2 | +9.5% | 21.92 | -2.12 | -9.7% |

Winning live tickets:

| target_date | city | bracket | fill_rows | fill_qty | cost_usd | pnl_usd_at_fill |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-03 | Houston | 96-97 | 1 | 8.8 | 0.79 | 8.01 |
| 2026-07-05 | Helsinki | 17 | 3 | 11.0 | 1.33 | 9.66 |

## Fresh Opportunity vs Live Coverage

This explains why fresh opportunity rows can show more winners than live: many opportunity candidates were never actually filled by the live runner.

| live_ticket_filled | win | rows | cost | pnl | roi |
| --- | --- | --- | --- | --- | --- |
| False | False | 15 | 14.06 | -14.06 | -100.0% |
| False | True | 8 | 7.22 | 52.78 | +731.2% |
| True | False | 13 | 8.74 | -8.74 | -100.0% |
| True | True | 1 | 0.83 | 7.17 | +861.7% |

## Interpretation

- If you remember "live only hit two", that is correct at distinct ticket level for this DB snapshot: Houston 2026-07-03 and Helsinki 2026-07-05.
- If the opportunity report says 7/1..7/7 had 9 winners, that is a backtest/opportunity denominator, not actual live fills.
- Source-risk and source-quality were designed after seeing the historical/recent research set. Their fields are as-of and non-leaky, but the rule itself is not true untouched forward until it is frozen from now onward.

## Decision

No live change. For source-risk, treat 6/21+ as recent/holdout evidence and 7/1..7/7 as fresh append evidence, but use only future settled rows after this report as true forward for promotion.
