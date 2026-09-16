# Model Rank IC Research

> generated_at_utc: `2026-06-08T17:01:51.889238+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: Ring3 signal ranking diagnostic using `fact_signal_candidates`; no live behavior changed.

## What IC / Rank Means

IC is the correlation between a signal score and later realized outcome. Rank means the model does not need to be a calibrated probability; it only needs to sort better opportunities above worse opportunities.

Primary score: `model_edge_at_decision = model_side_prob - decision_entry_price`.

## Data Quality

| field | value |
|---|---:|
| `max_fact_built_at_utc` | `2026-06-08T01:32:49.788100+00:00` |
| `fact_signal_candidates_rows` | `23893` |
| `eligible_rows` | `7841` |
| `decision_window_missing_rows` | `8251` |
| `usable_rows` | `1007` |
| `usable_dates` | `24` |
| `date_range` | `2026-05-12 → 2026-06-06` |

## Gates

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `PASS` |
| `verdict` | `inconclusive` |

## Overall IC

| score | outcome | IC | 95% CI |
|---|---|---:|---:|
| `model_edge_at_decision` | `realized_side_win` | -0.0005 | [-0.0597, +0.0689] |
| `model_edge_at_decision` | `realized_roi_at_decision` | +0.0442 | [-0.0148, +0.1076] |
| `model_edge_at_decision` | `counterfactual_pnl` | +0.0533 | [-0.0067, +0.1158] |
| `model_side_prob` | `realized_side_win` | +0.4237 | [+0.3670, +0.4709] |
| `model_side_prob` | `realized_roi_at_decision` | +0.2118 | [+0.1597, +0.2580] |
| `model_side_prob` | `counterfactual_pnl` | +0.0087 | [-0.0509, +0.0601] |
| `model_vs_market_delta` | `realized_side_win` | +0.0033 | [-0.0544, +0.0700] |
| `model_vs_market_delta` | `realized_roi_at_decision` | +0.0475 | [-0.0088, +0.1063] |
| `model_vs_market_delta` | `counterfactual_pnl` | +0.0537 | [-0.0045, +0.1129] |
| `abs_edge` | `realized_side_win` | -0.0054 | [-0.0623, +0.0630] |
| `abs_edge` | `realized_roi_at_decision` | +0.0445 | [-0.0154, +0.1031] |
| `abs_edge` | `counterfactual_pnl` | +0.0568 | [-0.0071, +0.1197] |

## Forward Top-Rank Test

- Train: `['2026-05-12', '2026-05-29']`
- Test: `['2026-05-30', '2026-06-06']`
- Top threshold from train q=0.8: `0.25106000000000006`
- Test IC ROI: `+0.0500`, CI `[-0.0318, +0.1496]`
- Test top-vs-rest ROI delta: `+10.7%`, CI `[-11.4%, +33.0%]`

## Rank Buckets

| bucket | n | score range | win_rate | mean ROI | cf PnL |
|---:|---:|---:|---:|---:|---:|
| 0 | 202 | -0.1893..0.0420 | +50.0% | -4.3% | +13.6400 |
| 1 | 202 | 0.0421..0.1032 | +50.0% | +2.3% | -81.3100 |
| 2 | 200 | 0.1032..0.1551 | +50.0% | -16.2% | -80.4350 |
| 3 | 201 | 0.1552..0.2429 | +53.7% | +50.3% | +69.9500 |
| 4 | 202 | 0.2432..0.9890 | +47.5% | +26.2% | +57.8650 |

## Group IC Highlights

### By side

| side | n | dates | IC ROI | mean ROI |
|---|---:|---:|---:|---:|
| `BUY_NO` | 600 | 24 | +0.0749 | -2.2% |
| `BUY_YES` | 407 | 24 | +0.0130 | +32.2% |

### By forecast source

| forecast_source | n | dates | IC ROI | mean ROI |
|---|---:|---:|---:|---:|
| `open_meteo_live_gfs` | 506 | 24 | +0.0561 | +32.4% |
| `open_meteo_live_ecmwf` | 501 | 24 | +0.0316 | -9.2% |

## Notes

- Ring3 can only justify rank/sizing research, not live hard gates by itself.
- A failed or inconclusive IC test means model ranking has not been proven useful on this universe.
- Any live action still requires the full three-gate performance workflow in `WEATHER_ANALYSIS_CONTRACT.md`.
