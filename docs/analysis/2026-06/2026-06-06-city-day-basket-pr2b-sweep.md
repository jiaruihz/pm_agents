# City-Day Basket PR2b Parameter Sweep

> generated_at_utc: `2026-06-05T17:30:53+00:00`
> data: `/home/rui/projects/pm_agent/runtime/weather.db` / `fact_signal_candidates`
> input rows: 1878 (2026-05-06 -> 2026-06-04)

## Baseline

| rule | n_legs | cost | pnl | ROI | ROI excl top5 |
|---|---:|---:|---:|---:|---:|
| raw_single | 1696 | $8480 | $+550 | +6.48% | -6.14% |
| market_only | 0 | $0 | $+0 | +0.00% | +0.00% |
| blended_single | 1200 | $6000 | $+869 | +14.48% | -2.36% |

## Gate Summary

- full gate pass configs: 30 / 192
- gate = missed_profit <= avoided_loss, basket ROI >= 80% blended ROI, top-5 removed ROI >= 0, BUY_NO ROI >= 0

## Top Configs

| rank | pass | small/normal | cap | max legs | edge small/normal | prefer NO | n | pnl | ROI | top5 ROI | missed | avoided | net vs blended |
|---:|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | yes | $3/$8 | $15 | 4 | 0.03/0.06 | False | 792 | $+1111 | +28.16% | +3.44% | $784 | $825 | $+41 |
| 2 | yes | $3/$8 | $15 | 4 | 0.03/0.05 | False | 792 | $+1460 | +33.82% | +3.22% | $784 | $825 | $+41 |
| 3 | yes | $3/$8 | $20 | 4 | 0.03/0.05 | False | 792 | $+1670 | +35.47% | +2.95% | $784 | $825 | $+41 |
| 4 | yes | $3/$8 | $20 | 4 | 0.03/0.06 | False | 792 | $+1130 | +26.75% | +2.84% | $784 | $825 | $+41 |
| 5 | yes | $3/$8 | $25 | 4 | 0.03/0.05 | False | 792 | $+1699 | +35.47% | +2.78% | $784 | $825 | $+41 |
| 6 | yes | $3/$8 | $25 | 4 | 0.03/0.06 | False | 792 | $+1154 | +27.01% | +2.55% | $784 | $825 | $+41 |
| 7 | yes | $3/$8 | $15 | 4 | 0.02/0.06 | False | 932 | $+1219 | +27.81% | +5.58% | $759 | $800 | $+41 |
| 8 | yes | $3/$8 | $15 | 4 | 0.02/0.05 | False | 932 | $+1551 | +32.55% | +4.83% | $759 | $800 | $+41 |
| 9 | yes | $3/$8 | $20 | 4 | 0.02/0.06 | False | 932 | $+1232 | +26.40% | +4.76% | $759 | $800 | $+41 |
| 10 | yes | $3/$8 | $25 | 4 | 0.02/0.06 | False | 932 | $+1256 | +26.63% | +4.48% | $759 | $800 | $+41 |
| 11 | yes | $3/$8 | $20 | 4 | 0.02/0.05 | False | 932 | $+1762 | +34.14% | +4.48% | $759 | $800 | $+41 |
| 12 | yes | $3/$8 | $25 | 4 | 0.02/0.05 | False | 932 | $+1791 | +34.16% | +4.30% | $759 | $800 | $+41 |

## Interpretation

At least one PR2b basket configuration passes the offline gate. This is still an offline replay over aggregated decision-window candidates, not a production approval.
Best ranked config: `{'small': 3.0, 'normal': 8.0, 'cap': 15.0, 'max_legs': 4, 'edge_small': 0.03, 'edge_normal': 0.06, 'prefer_no': False}` with basket ROI +28.16%.

Production remains unchanged: no N100 config change, no canary.
