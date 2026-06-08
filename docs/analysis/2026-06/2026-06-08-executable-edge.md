# Executable Edge Research

> generated_at_utc: `2026-06-08T15:22:25.083975+00:00`
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

`not_requested`:

## Notes

- 2A uses settled `fact_trades` only and surfaces open/unsettled rows separately.
- 2B here is a decision-entry proxy from `fact_signal_candidates`; it is not a raw orderbook replay.
- Raw orderbook replay is intentionally disabled until it enforces `snapshot_ts <= decision_snapshot_ts_utc` and de-duplicates market rows before aggregation.
