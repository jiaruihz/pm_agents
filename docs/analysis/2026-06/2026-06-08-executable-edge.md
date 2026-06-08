# Executable Edge Research

> generated_at_utc: `2026-06-08T16:07:58.638096+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> trade_class: `live_real`
> Scope: offline Step 2 diagnostic; no N100/live behavior changed.

## Gates

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `NA` |
| `verdict` | `inconclusive` |

## 2A Fill Audit

| fills | settled | open | cost | pnl | ROI | ROI CI | win rate |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1333 | 1202 | 131 | 3207.23 | -51.82 | -1.6% | [-16.8%, +13.4%] | +50.2% |

Drop top1 fill PnL: `-70.62`; drop top5 fill PnL: `-129.16`.

## Edge Predictiveness

| metric | value |
|---|---:|
| edge-pnl Pearson by fill | `-0.05619825496389603` |
| high edge ROI | `-4.2%` |
| low edge ROI | `+0.9%` |
| high-low ROI delta | `-5.1%` |
| high-low delta CI | `[-31.0%, +25.2%]` |

## 2B Decision-Entry Proxy

| side | n | dates | cf ROI | ROI CI | live fill rate |
|---|---:|---:|---:|---:|---:|
| `BUY_NO` | 215 | 24 | +69.7% | [+10.4%, +127.3%] | +64.2% |
| `BUY_YES` | 97 | 22 | -19.6% | [-328.0%, +308.2%] | +41.2% |

## Raw Orderbook Replay

| scope | candidates | matched | taker rows | taker ROI | taker ROI CI | maker proxy ROI | avg age min |
|---|---:|---:|---:|---:|---:|---:|---:|
| selected Step1 buckets | 312 | 259 | 259 | +2.6% | [-6.0%, +11.1%] | +8.8% | 8.920334620334621 |

| side | rows | taker ROI | taker CI | maker proxy ROI | avg spread |
|---|---:|---:|---:|---:|---:|
| `BUY_NO` | 180 | +3.6% | [-2.4%, +9.5%] | +9.2% | 0.036000000000000004 |
| `BUY_YES` | 79 | -2.1% | [-35.1%, +31.7%] | +7.0% | 0.027658227848101267 |

## Notes

- 2A uses settled `fact_trades` only and surfaces open/unsettled rows separately.
- 2B decision-entry proxy uses `fact_signal_candidates`; raw orderbook replay separately uses the latest orderbook row with `snapshot_ts_utc <= decision_snapshot_ts_utc`.
- Raw orderbook taker ROI uses side-token `best_ask`; maker ROI is only a `best_bid` proxy and does not prove fill probability.
