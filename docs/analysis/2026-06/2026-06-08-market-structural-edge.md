# Market Structural Edge Research

> generated_at_utc: `2026-06-08T17:01:00.048252+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: offline H_B diagnostic; source is `fact_signal_candidates`, de-duplicated to one row per market/date/bracket.

## Data Quality

| field | value |
|---|---:|
| `fact_signal_candidates_rows` | `23893` |
| `eligible_rows` | `7841` |
| `decision_window_missing_rows` | `8251` |
| `decision_window_missing_rate` | `0.3453312685723852` |
| `usable_side_rows_before_market_dedupe` | `1007` |
| `max_fact_built_at_utc` | `2026-06-08T01:32:49.788100+00:00` |
| `market_rows_after_side_dedupe` | `759` |
| `market_row_dates` | `24` |

## Forward Gates

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `PASS` |
| `verdict` | `inconclusive` |

Selected price buckets from train: `0.20-0.25, 0.35-0.40`.

| test policy | n | dates | ROI | ROI CI | PnL | cost |
|---|---:|---:|---:|---:|---:|---:|
| selected BUY_NO buckets | 67 | 8 | +5.1% | [-13.4%, +25.5%] | +2.37 | 46.63 |
| all-band BUY_NO baseline | 292 | 8 | +0.6% | [-3.2%, +5.2%] | +1.10 | 195.90 |

Selected minus baseline ROI: `+4.5%`, CI `[-11.0%, +22.7%]`.

## Price Buckets

| bucket | n | actual YES | implied YES | gap | BUY_NO ROI | ROI CI | Bonf sig | model lift |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| `0.00-0.05` | 48 | +6.2% | +2.4% | +3.9% | -4.0% | [-10.1%, +1.0%] | False | +4.7% |
| `0.05-0.10` | 31 | +6.5% | +7.5% | -1.0% | +1.1% | [-7.3%, +8.5%] | False | +13.3% |
| `0.10-0.15` | 40 | +22.5% | +12.8% | +9.7% | -11.1% | [-25.3%, +2.5%] | False | -0.5% |
| `0.15-0.20` | 48 | +18.8% | +17.5% | +1.3% | -1.6% | [-14.1%, +10.8%] | False | -19.3% |
| `0.20-0.25` | 82 | +24.4% | +22.4% | +1.9% | -2.5% | [-18.5%, +10.9%] | False | +1.2% |
| `0.25-0.30` | 134 | +26.1% | +27.6% | -1.5% | +2.0% | [-10.7%, +14.1%] | False | +14.2% |
| `0.30-0.35` | 117 | +35.0% | +32.8% | +2.2% | -3.3% | [-11.9%, +5.6%] | False | -18.2% |
| `0.35-0.40` | 84 | +27.4% | +37.4% | -10.0% | +16.0% | [-0.7%, +35.6%] | False | -1.1% |
| `0.40-0.45` | 75 | +49.3% | +42.2% | +7.2% | -12.4% | [-29.5%, +4.4%] | False | +4.0% |
| `0.45-0.50` | 46 | +50.0% | +47.2% | +2.8% | -5.3% | [-28.9%, +15.6%] | False | +17.4% |
| `0.50-0.55` | 22 | +59.1% | +52.5% | +6.6% | -14.0% | [-64.7%, +27.2%] | False | +20.0% |
| `0.55-0.60` | 11 | +36.4% | +57.1% | -20.7% | +48.3% | [-0.5%, +129.9%] | False | +6.7% |
| `0.60-0.65` | 6 | +66.7% | +62.2% | +4.4% | -11.7% | [-100.0%, +84.8%] | False | NA |
| `0.65-0.70` | 3 | +66.7% | +68.0% | -1.3% | +4.2% | [-100.0%, +217.5%] | False | NA |
| `0.75-0.80` | 2 | +100.0% | +76.2% | +23.8% | -100.0% | [-100.0%, -100.0%] | False | NA |
| `0.80-0.85` | 5 | +80.0% | +81.3% | -1.3% | +7.0% | [-100.0%, +231.5%] | False | NA |
| `0.85-0.90` | 2 | +100.0% | +85.8% | +14.3% | -100.0% | [-100.0%, -100.0%] | False | NA |
| `0.90-0.95` | 1 | +100.0% | +91.0% | +9.0% | -100.0% | [-100.0%, -100.0%] | False | NA |
| `0.95-1.00` | 2 | +100.0% | +99.2% | +0.8% | -100.0% | [-100.0%, -100.0%] | False | NA |

## Notes

- `significance`, `baseline`, and `forward` are required before any live action. A failed gate means this script is diagnostic only.
- Step 1 uses market YES price as a necessary-condition upper bound. Real executable edge still requires Step 2.
- Multiple-testing risk is surfaced via Bonferroni bucket tests; the forward rule still needs independent future data before live sizing changes.
