# Low-Price YES Maker Queue v1

Generated: `2026-07-04T22:54:58.725716+00:00`

## Verdict

`forecast_tail_low_price_yes` stays live at tiny size; do not size up to $3/$5 yet.

The current evidence says maker entry can fill small $0.8-style orders, but it does not prove deeper capacity. The two new price-tier orders are still unfilled/open, so they are useful telemetry, not a sizing verdict.

## Data

- Live order rows: 18 (15 settled, 3 open).
- Fill coverage: any-fill 83.3%; full-fill 77.8%; median fill fraction 100.0%.
- Wait: median first fill 6.2 min; median complete/last fill 6.8 min.
- Settled actual ROI on filled shares: -30.5% ($-3.86 on $12.66).
- Actual missed-winner cost from unfilled winning shares: $0.00.
- Sizing modes: `{'notional': 15, 'price_tier_6_8_10_shares': 3}`.

## $1/$3/$5 Scenarios

Two queue models are shown:

- `absolute_fill_cap`: conservative; assumes we could only have filled the absolute number of shares we actually observed on that order.
- `proportional_fill_fraction`: optimistic; assumes larger orders get the same fill percentage as the actual order.

| Notional | Model | Orders | Any Fill | Full Fill | Mean Fill | Settled ROI | Missed Winner Cost | Filled Cost |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| $1 | absolute_fill_cap | 18 | 83.3% | 27.8% | 72.7% | -27.9% | $1.99 | $13.09 |
| $1 | proportional_fill_fraction | 18 | 83.3% | 77.8% | 83.3% | -21.5% | $0.00 | $15.00 |
| $3 | absolute_fill_cap | 18 | 83.3% | 0.0% | 26.1% | -33.4% | $21.97 | $14.09 |
| $3 | proportional_fill_fraction | 18 | 83.3% | 77.8% | 83.3% | -21.5% | $0.00 | $45.00 |
| $5 | absolute_fill_cap | 18 | 83.3% | 0.0% | 15.7% | -33.4% | $41.95 | $14.09 |
| $5 | proportional_fill_fraction | 18 | 83.3% | 77.8% | 83.3% | -21.5% | $0.00 | $75.00 |

## Read

- The old fixed-notional maker sample mostly filled, but that sample is only 15 orders and one settled winner. It supports continuing tiny maker-first probing, not increasing ticket size.
- The current price-tier sample is 3 orders and 1 fills so far. Keep it running until it has enough elapsed local time and settlement labels.
- $3/$5 needs a real partial-fill model before promotion: conservative absolute-cap fill collapses as notional rises, while proportional fill is an optimistic upper bound.
- Missed-winner cost is currently zero in the settled order sample because the only settled winner was filled. That is good news, but too early to trust.

## Order Rows

| Created UTC | City | Date | Bracket | Mode | Price | Shares | Filled | Fill Frac | First Fill min | Last Fill min | Settled | Final YES |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|---:|
| 2026-07-02T06:20:14+00:00 | NYC | 2026-07-02 | 104-105 | notional | 0.050 | 30.00 | 30.00 | 100.0% | 0.0 | 0.0 | settled | 0 |
| 2026-07-02T06:37:19+00:00 | Chicago | 2026-07-02 | 98-99 | notional | 0.060 | 25.00 | 25.00 | 100.0% | 0.0 | 0.0 | settled | 0 |
| 2026-07-02T23:13:48+00:00 | TelAviv | 2026-07-03 | 32 | notional | 0.140 | 7.14 | 0.00 | 0.0% | NA | NA | settled | 0 |
| 2026-07-02T23:13:49+00:00 | London | 2026-07-03 | 28 | notional | 0.080 | 12.50 | 12.50 | 100.0% | 0.0 | 0.0 | settled | 0 |
| 2026-07-02T23:15:24+00:00 | TelAviv | 2026-07-03 | 32 | notional | 0.140 | 7.15 | 7.15 | 100.0% | 0.0 | 0.0 | settled | 0 |
| 2026-07-02T23:43:24+00:00 | Ankara | 2026-07-03 | 30 | notional | 0.080 | 12.50 | 12.50 | 100.0% | 0.0 | 0.0 | settled | 0 |
| 2026-07-03T03:53:01+00:00 | Dallas | 2026-07-03 | 98-99 | notional | 0.111 | 7.21 | 7.21 | 100.0% | 84.0 | 84.0 | settled | 0 |
| 2026-07-03T05:03:50+00:00 | Houston | 2026-07-03 | 96-97 | notional | 0.091 | 8.80 | 8.80 | 100.0% | 4.5 | 4.5 | settled | 1 |
| 2026-07-03T06:39:45+00:00 | Chicago | 2026-07-03 | 94-95 | notional | 0.059 | 13.56 | 13.56 | 100.0% | 101.6 | 101.6 | settled | 0 |
| 2026-07-03T07:25:23+00:00 | NYC | 2026-07-03 | 104-105 | notional | 0.065 | 12.31 | 12.31 | 100.0% | 5.0 | 5.0 | settled | 0 |
| 2026-07-03T09:51:42+00:00 | LA | 2026-07-03 | 70-71 | notional | 0.141 | 5.68 | 5.68 | 100.0% | 13.9 | 13.9 | settled | 0 |
| 2026-07-03T15:25:01+00:00 | Manila | 2026-07-04 | 36+ | notional | 0.056 | 14.29 | 14.29 | 100.0% | 21.1 | 702.4 | settled | 0 |
| 2026-07-03T16:25:40+00:00 | Busan | 2026-07-04 | 24 | notional | 0.048 | 16.67 | 16.67 | 100.0% | 6.2 | 6.8 | settled | 0 |
| 2026-07-03T19:46:35+00:00 | Helsinki | 2026-07-04 | 21 | notional | 0.151 | 5.30 | 5.30 | 100.0% | 109.2 | 110.9 | settled | 0 |
| 2026-07-03T20:21:59+00:00 | Paris | 2026-07-04 | 32 | notional | 0.061 | 13.12 | 13.11 | 99.9% | 464.8 | 533.9 | settled | 0 |
| 2026-07-04T10:19:43+00:00 | Wellington | 2026-07-05 | 15 | price_tier_6_8_10_shares | 0.111 | 8.00 | 8.00 | 100.0% | 446.6 | 446.6 | unsettled_or_missing | NA |
| 2026-07-04T15:00:22+00:00 | Shanghai | 2026-07-05 | 28 | price_tier_6_8_10_shares | 0.054 | 6.00 | 0.00 | 0.0% | NA | NA | unsettled_or_missing | NA |
| 2026-07-04T19:11:22+00:00 | Helsinki | 2026-07-05 | 17 | price_tier_6_8_10_shares | 0.073 | 6.00 | 0.00 | 0.0% | NA | NA | unsettled_or_missing | NA |

## Artifacts

- `docs/analysis/2026-07/generated/low_price_yes_maker_queue_v1/order_queue_rows.csv`
- `docs/analysis/2026-07/generated/low_price_yes_maker_queue_v1/scenario_rows.csv`
- `docs/analysis/2026-07/generated/low_price_yes_maker_queue_v1/summary.json`
