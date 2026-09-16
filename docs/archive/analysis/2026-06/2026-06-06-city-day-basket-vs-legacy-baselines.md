# City-Day Basket vs Legacy Bucket Baselines — 2026-06-06

> generated_at_utc: `2026-06-06T03:56:28+00:00`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: offline fair-baseline diagnostic only; no N100/live behavior changed.

## Target Question

Does city-day basket selection improve over the legacy per-bucket independent selector on the same live-like decision timing and entry universe?

This report separates two failure modes:

- If both legacy and basket lose on the same slice, the weather/probability model or market regime is likely the first problem.
- If legacy wins but basket loses, the city-day grouping/objective is likely the first problem.

## Timing

The source table is `fact_signal_candidates` with `decision_window_missing = 0`, i.e. the existing T-22~24h representative decision snapshot. It is close to the old live timing but is not a full 30-minute snapshot replay.

## Rules

- `legacy_raw_independent`: old-style per bucket raw model gate with live-like entry band and min edge.
- `legacy_blended_independent`: same per bucket gate, replacing raw probability with blend.
- `basket_pr2b_same_entry_band`: PR2b basket over the same entry-band universe.
- `basket_pr2b_legacy_raw_triggers`: PR2b basket restricted to rows the legacy raw rule would have triggered.
- `combo_market_risk_same_entry_band`: combo optimizer over the same entry-band universe.
- `combo_market_risk_legacy_raw_triggers`: combo optimizer restricted to legacy raw triggered rows.

## Backtest Summary

| slice | profile | rule | n | cost | pnl | ROI | top5 ROI | missed vs legacy | avoided vs legacy |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| full | legacy_25_75_e10 | legacy_raw_independent | 884 | $4420 | $+195 | +4.41% | +2.75% | $0 | $0 |
| full | legacy_25_75_e10 | legacy_blended_independent | 92 | $460 | $+115 | +24.93% | +9.97% | $1655 | $1575 |
| full | legacy_25_75_e10 | basket_pr2b_same_entry_band | 257 | $1345 | $+115 | +8.54% | +0.45% | $1258 | $1120 |
| full | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 248 | $1272 | $+123 | +9.71% | +1.33% | $1235 | $1115 |
| full | legacy_25_75_e10 | combo_market_risk_same_entry_band | 160 | $800 | $+158 | +19.80% | +5.94% | $1440 | $1320 |
| full | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 149 | $707 | $+175 | +24.72% | +9.22% | $1431 | $1330 |
| full | legacy_side_band | legacy_raw_independent | 435 | $2175 | $+216 | +9.92% | +5.92% | $0 | $0 |
| full | legacy_side_band | legacy_blended_independent | 68 | $340 | $+23 | +6.70% | -3.16% | $998 | $805 |
| full | legacy_side_band | basket_pr2b_same_entry_band | 270 | $1403 | $+177 | +12.65% | +2.81% | $640 | $565 |
| full | legacy_side_band | basket_pr2b_legacy_raw_triggers | 110 | $764 | $+255 | +33.30% | +16.54% | $697 | $680 |
| full | legacy_side_band | combo_market_risk_same_entry_band | 182 | $881 | $+197 | +22.34% | +6.46% | $839 | $780 |
| full | legacy_side_band | combo_market_risk_legacy_raw_triggers | 55 | $435 | $+198 | +45.57% | +15.37% | $888 | $800 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | legacy_raw_independent | 313 | $1565 | $-58 | -3.70% | -8.44% | $0 | $0 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | legacy_blended_independent | 36 | $180 | $+47 | +26.25% | -11.77% | $515 | $620 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 96 | $524 | $+3 | +0.63% | -20.29% | $414 | $425 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 93 | $512 | $-4 | -0.71% | -21.82% | $402 | $420 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 54 | $277 | $+63 | +22.82% | -17.85% | $479 | $535 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 50 | $250 | $+50 | +19.96% | -25.58% | $479 | $535 |
| holdout_from_2026_05_26 | legacy_side_band | legacy_raw_independent | 161 | $805 | $+97 | +12.03% | +1.07% | $0 | $0 |
| holdout_from_2026_05_26 | legacy_side_band | legacy_blended_independent | 21 | $105 | $-20 | -18.73% | -57.24% | $436 | $320 |
| holdout_from_2026_05_26 | legacy_side_band | basket_pr2b_same_entry_band | 108 | $602 | $+183 | +30.41% | +7.87% | $185 | $215 |
| holdout_from_2026_05_26 | legacy_side_band | basket_pr2b_legacy_raw_triggers | 53 | $378 | $+194 | +51.36% | +18.83% | $204 | $255 |
| holdout_from_2026_05_26 | legacy_side_band | combo_market_risk_same_entry_band | 68 | $359 | $+188 | +52.43% | +14.05% | $277 | $310 |
| holdout_from_2026_05_26 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 26 | $208 | $+154 | +74.03% | +11.43% | $311 | $310 |
| recent_from_2026_06_01 | legacy_25_75_e10 | legacy_raw_independent | 84 | $420 | $+10 | +2.32% | -8.42% | $0 | $0 |
| recent_from_2026_06_01 | legacy_25_75_e10 | legacy_blended_independent | 7 | $35 | $-1 | -1.94% | -100.00% | $155 | $145 |
| recent_from_2026_06_01 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 34 | $171 | $-10 | -5.87% | -43.27% | $116 | $80 |
| recent_from_2026_06_01 | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 29 | $151 | $-39 | -25.84% | -65.07% | $121 | $75 |
| recent_from_2026_06_01 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 18 | $84 | $+12 | +14.52% | -67.15% | $142 | $115 |
| recent_from_2026_06_01 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 15 | $70 | $-13 | -19.26% | -96.43% | $142 | $110 |
| recent_from_2026_06_01 | legacy_side_band | legacy_raw_independent | 44 | $220 | $+62 | +28.25% | -0.44% | $0 | $0 |
| recent_from_2026_06_01 | legacy_side_band | legacy_blended_independent | 4 | $20 | $-11 | -56.14% | +0.00% | $138 | $65 |
| recent_from_2026_06_01 | legacy_side_band | basket_pr2b_same_entry_band | 32 | $171 | $+41 | +23.83% | -42.71% | $64 | $30 |
| recent_from_2026_06_01 | legacy_side_band | basket_pr2b_legacy_raw_triggers | 14 | $98 | $+22 | +22.00% | -93.73% | $76 | $40 |
| recent_from_2026_06_01 | legacy_side_band | combo_market_risk_same_entry_band | 20 | $105 | $+46 | +43.97% | -69.27% | $91 | $55 |
| recent_from_2026_06_01 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 7 | $56 | $+12 | +20.71% | -100.00% | $110 | $55 |
| live_filled_only | legacy_25_75_e10 | legacy_raw_independent | 235 | $1175 | $+90 | +7.67% | +1.79% | $0 | $0 |
| live_filled_only | legacy_25_75_e10 | legacy_blended_independent | 33 | $165 | $+18 | +10.61% | -27.89% | $453 | $380 |
| live_filled_only | legacy_25_75_e10 | basket_pr2b_same_entry_band | 72 | $381 | $+73 | +19.17% | -7.63% | $314 | $285 |
| live_filled_only | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 69 | $357 | $+73 | +20.46% | -8.16% | $319 | $290 |
| live_filled_only | legacy_25_75_e10 | combo_market_risk_same_entry_band | 38 | $179 | $+105 | +58.68% | -1.17% | $375 | $365 |
| live_filled_only | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 38 | $179 | $+105 | +58.68% | -1.17% | $375 | $365 |
| live_filled_only | legacy_side_band | legacy_raw_independent | 114 | $570 | $+108 | +19.00% | +5.21% | $0 | $0 |
| live_filled_only | legacy_side_band | legacy_blended_independent | 24 | $120 | $-22 | -18.55% | -53.22% | $301 | $170 |
| live_filled_only | legacy_side_band | basket_pr2b_same_entry_band | 68 | $349 | $+121 | +34.54% | -0.34% | $175 | $130 |
| live_filled_only | legacy_side_band | basket_pr2b_legacy_raw_triggers | 30 | $206 | $+122 | +59.31% | +6.90% | $191 | $170 |
| live_filled_only | legacy_side_band | combo_market_risk_same_entry_band | 39 | $182 | $+161 | +88.49% | +20.94% | $227 | $215 |
| live_filled_only | legacy_side_band | combo_market_risk_legacy_raw_triggers | 13 | $99 | $+117 | +118.14% | -1.39% | $246 | $215 |

## Actual Live Reference

This is actual settled `live_real` PnL by recorded instance; it is not forced to the same representative snapshot, so use it as context, not a strict counterfactual.

| instance | fills | date range | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| live_weather_edge_v1_4b07f7abc42f | 28 | 2026-06-01 -> 2026-06-04 | $90 | $-8 | -8.70% | 57.1% |
| live_weather_edge_v1_4ef9b3ec3e2e | 382 | 2026-05-16 -> 2026-06-04 | $1332 | $+34 | +2.58% | 57.6% |
| live_weather_edge_v1_91f019941593 | 20 | 2026-06-01 -> 2026-06-04 | $44 | $+33 | +74.45% | 65.0% |
| live_weather_edge_v1_986d901ccc58 | 176 | 2026-05-29 -> 2026-06-04 | $475 | $-48 | -10.01% | 44.3% |
| live_weather_edge_v1_c13ccf0c3181 | 74 | 2026-05-24 -> 2026-06-01 | $285 | $-23 | -8.10% | 55.4% |
| live_weather_edge_v1_edb2b6f8df82 | 76 | 2026-05-27 -> 2026-05-30 | $271 | $+37 | +13.57% | 59.2% |

## Quant Read

- Judge basket against `legacy_raw_independent` first; ROI alone is not enough.
- `basket_pr2b_legacy_raw_triggers` isolates the grouping/sizing effect on the exact old-triggered rows.
- `same_entry_band` variants test whether basket finds better opportunities when given the same live-like price universe.
- Negative top5-removed ROI remains a tail-dependency warning even if headline ROI is positive.

Production remains unchanged.
