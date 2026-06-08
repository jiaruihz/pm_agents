# Market Structural Edge Research

> generated_at_utc: `2026-06-08T15:22:24.360521+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: offline H_B diagnostic; source is `fact_signal_candidates`, de-duplicated to one row per market/date/bracket.

## Data Quality

| field | value |
|---|---:|
| `fact_signal_candidates_rows` | `23893` |
| `eligible_rows` | `7841` |
| `decision_window_missing_rows` | `10437` |
| `decision_window_missing_rate` | `0.43682250031389946` |
| `usable_side_rows_before_market_dedupe` | `854` |
| `max_fact_built_at_utc` | `2026-06-08T01:32:49.788100+00:00` |
| `market_rows_after_side_dedupe` | `754` |
| `market_row_dates` | `24` |

## Forward Gates

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `PASS` |
| `verdict` | `inconclusive` |

Selected price buckets from train: `0.20-0.25, 0.30-0.35, 0.35-0.40`.

| test policy | n | dates | ROI | ROI CI | PnL | cost |
|---|---:|---:|---:|---:|---:|---:|
| selected BUY_NO buckets | 121 | 8 | +0.1% | [-8.5%, +9.0%] | +0.08 | 82.92 |
| all-band BUY_NO baseline | 291 | 8 | +0.2% | [-3.5%, +4.9%] | +0.29 | 195.71 |

Selected minus baseline ROI: `-0.1%`, CI `[-5.1%, +5.3%]`.

## Price Buckets

| bucket | n | actual YES | implied YES | gap | BUY_NO ROI | ROI CI | Bonf sig | model lift |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| `0.00-0.05` | 47 | +6.4% | +2.3% | +4.1% | -4.2% | [-10.3%, +0.7%] | False | +4.5% |
| `0.05-0.10` | 32 | +6.2% | +7.4% | -1.1% | +1.2% | [-6.8%, +8.3%] | False | +13.3% |
| `0.10-0.15` | 41 | +22.0% | +12.8% | +9.1% | -10.5% | [-24.4%, +2.5%] | False | -13.6% |
| `0.15-0.20` | 48 | +20.8% | +17.5% | +3.3% | -4.0% | [-16.6%, +9.2%] | False | -6.6% |
| `0.20-0.25` | 79 | +22.8% | +22.4% | +0.4% | -0.6% | [-17.0%, +13.9%] | False | +5.6% |
| `0.25-0.30` | 139 | +28.1% | +27.6% | +0.5% | -0.6% | [-13.8%, +12.1%] | False | +10.5% |
| `0.30-0.35` | 115 | +33.9% | +32.8% | +1.1% | -1.7% | [-9.7%, +5.5%] | False | -18.5% |
| `0.35-0.40` | 79 | +26.6% | +37.4% | -10.8% | +17.3% | [-0.9%, +36.7%] | False | +8.3% |
| `0.40-0.45` | 75 | +49.3% | +42.0% | +7.4% | -12.7% | [-29.1%, +5.3%] | False | -1.4% |
| `0.45-0.50` | 45 | +51.1% | +47.2% | +3.9% | -7.4% | [-29.7%, +15.0%] | False | +11.3% |
| `0.50-0.55` | 23 | +60.9% | +52.5% | +8.4% | -17.6% | [-67.0%, +24.4%] | False | +5.3% |
| `0.55-0.60` | 11 | +27.3% | +57.3% | -30.0% | +70.3% | [+26.0%, +132.3%] | False | -13.3% |
| `0.60-0.65` | 5 | +80.0% | +61.8% | +18.2% | -47.6% | [-100.0%, +63.9%] | False | NA |
| `0.65-0.70` | 3 | +66.7% | +67.2% | -0.5% | +1.5% | [-100.0%, +194.1%] | False | NA |
| `0.75-0.80` | 2 | +100.0% | +76.2% | +23.8% | -100.0% | [-100.0%, -100.0%] | False | NA |
| `0.80-0.85` | 5 | +80.0% | +81.1% | -1.1% | +5.8% | [-100.0%, +229.7%] | False | NA |
| `0.85-0.90` | 2 | +100.0% | +85.8% | +14.3% | -100.0% | [-100.0%, -100.0%] | False | NA |
| `0.90-0.95` | 1 | +100.0% | +91.0% | +9.0% | -100.0% | [-100.0%, -100.0%] | False | NA |
| `0.95-1.00` | 2 | +100.0% | +99.2% | +0.8% | -100.0% | [-100.0%, -100.0%] | False | NA |

## Notes

- `significance`, `baseline`, and `forward` are required before any live action. A failed gate means this script is diagnostic only.
- Step 1 uses market YES price as a necessary-condition upper bound. Real executable edge still requires Step 2.
- Multiple-testing risk is surfaced via Bonferroni bucket tests; the forward rule still needs independent future data before live sizing changes.
