# City-Day Basket vs Legacy Bucket Baselines — 2026-06-06

> generated_at_utc: `2026-06-07T10:01:28+00:00`
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
| full | legacy_25_75_e10 | legacy_raw_independent | 905 | $4525 | $+160 | +3.54% | +1.92% | $0 | $0 |
| full | legacy_25_75_e10 | legacy_blended_independent | 95 | $475 | $+109 | +22.91% | +8.35% | $1681 | $1630 |
| full | legacy_25_75_e10 | basket_pr2b_same_entry_band | 266 | $1400 | $+60 | +4.27% | -3.58% | $1288 | $1145 |
| full | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 256 | $1319 | $+81 | +6.14% | -2.01% | $1262 | $1145 |
| full | legacy_25_75_e10 | combo_market_risk_same_entry_band | 165 | $835 | $+123 | +14.78% | +1.37% | $1470 | $1365 |
| full | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 153 | $734 | $+148 | +20.13% | +5.07% | $1461 | $1375 |
| full | legacy_side_band | legacy_raw_independent | 448 | $2240 | $+184 | +8.24% | +4.34% | $0 | $0 |
| full | legacy_side_band | legacy_blended_independent | 70 | $350 | $+22 | +6.27% | -3.32% | $1008 | $845 |
| full | legacy_side_band | basket_pr2b_same_entry_band | 279 | $1450 | $+140 | +9.63% | +0.05% | $653 | $575 |
| full | legacy_side_band | basket_pr2b_legacy_raw_triggers | 117 | $805 | $+213 | +26.50% | +10.32% | $710 | $690 |
| full | legacy_side_band | combo_market_risk_same_entry_band | 187 | $911 | $+176 | +19.33% | +3.90% | $853 | $810 |
| full | legacy_side_band | combo_market_risk_legacy_raw_triggers | 58 | $459 | $+174 | +37.96% | +8.77% | $902 | $830 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | legacy_raw_independent | 334 | $1670 | $-93 | -5.55% | -10.01% | $0 | $0 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | legacy_blended_independent | 39 | $195 | $+41 | +21.24% | -14.16% | $541 | $675 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 105 | $579 | $-52 | -8.93% | -28.25% | $445 | $450 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 101 | $559 | $-46 | -8.27% | -27.91% | $430 | $450 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 59 | $312 | $+28 | +9.04% | -27.97% | $509 | $580 |
| holdout_from_2026_05_26 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 54 | $277 | $+23 | +8.27% | -33.64% | $509 | $580 |
| holdout_from_2026_05_26 | legacy_side_band | legacy_raw_independent | 174 | $870 | $+66 | +7.54% | -2.70% | $0 | $0 |
| holdout_from_2026_05_26 | legacy_side_band | legacy_blended_independent | 23 | $115 | $-20 | -17.82% | -51.80% | $446 | $360 |
| holdout_from_2026_05_26 | legacy_side_band | basket_pr2b_same_entry_band | 117 | $649 | $+145 | +22.36% | +1.18% | $199 | $225 |
| holdout_from_2026_05_26 | legacy_side_band | basket_pr2b_legacy_raw_triggers | 60 | $419 | $+153 | +36.51% | +6.08% | $217 | $265 |
| holdout_from_2026_05_26 | legacy_side_band | combo_market_risk_same_entry_band | 73 | $389 | $+167 | +43.05% | +7.17% | $291 | $340 |
| holdout_from_2026_05_26 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 29 | $232 | $+130 | +56.03% | -2.50% | $324 | $340 |
| recent_from_2026_06_01 | legacy_25_75_e10 | legacy_raw_independent | 105 | $525 | $-25 | -4.75% | -14.69% | $0 | $0 |
| recent_from_2026_06_01 | legacy_25_75_e10 | legacy_blended_independent | 10 | $50 | $-7 | -13.01% | -100.00% | $182 | $200 |
| recent_from_2026_06_01 | legacy_25_75_e10 | basket_pr2b_same_entry_band | 43 | $226 | $-65 | -28.78% | -59.14% | $146 | $105 |
| recent_from_2026_06_01 | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 37 | $198 | $-82 | -41.20% | -72.34% | $149 | $105 |
| recent_from_2026_06_01 | legacy_25_75_e10 | combo_market_risk_same_entry_band | 23 | $119 | $-23 | -19.16% | -81.32% | $172 | $160 |
| recent_from_2026_06_01 | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 19 | $97 | $-40 | -41.73% | -100.74% | $172 | $155 |
| recent_from_2026_06_01 | legacy_side_band | legacy_raw_independent | 57 | $285 | $+31 | +10.86% | -12.33% | $0 | $0 |
| recent_from_2026_06_01 | legacy_side_band | legacy_blended_independent | 6 | $30 | $-12 | -40.18% | -100.00% | $148 | $105 |
| recent_from_2026_06_01 | legacy_side_band | basket_pr2b_same_entry_band | 41 | $218 | $+3 | +1.32% | -51.96% | $78 | $40 |
| recent_from_2026_06_01 | legacy_side_band | basket_pr2b_legacy_raw_triggers | 21 | $139 | $-20 | -14.06% | -94.50% | $90 | $50 |
| recent_from_2026_06_01 | legacy_side_band | combo_market_risk_same_entry_band | 25 | $135 | $+25 | +18.81% | -70.24% | $105 | $85 |
| recent_from_2026_06_01 | legacy_side_band | combo_market_risk_legacy_raw_triggers | 10 | $80 | $-12 | -15.51% | -100.00% | $124 | $85 |
| live_filled_only | legacy_25_75_e10 | legacy_raw_independent | 256 | $1280 | $+55 | +4.33% | -1.12% | $0 | $0 |
| live_filled_only | legacy_25_75_e10 | legacy_blended_independent | 36 | $180 | $+12 | +6.49% | -28.95% | $479 | $435 |
| live_filled_only | legacy_25_75_e10 | basket_pr2b_same_entry_band | 81 | $431 | $+37 | +8.53% | -15.66% | $331 | $315 |
| live_filled_only | legacy_25_75_e10 | basket_pr2b_legacy_raw_triggers | 78 | $407 | $+37 | +9.04% | -16.62% | $336 | $320 |
| live_filled_only | legacy_25_75_e10 | combo_market_risk_same_entry_band | 43 | $209 | $+84 | +40.32% | -12.23% | $395 | $410 |
| live_filled_only | legacy_25_75_e10 | combo_market_risk_legacy_raw_triggers | 43 | $209 | $+84 | +40.32% | -12.23% | $395 | $410 |
| live_filled_only | legacy_side_band | legacy_raw_independent | 127 | $635 | $+77 | +12.14% | -0.46% | $0 | $0 |
| live_filled_only | legacy_side_band | legacy_blended_independent | 26 | $130 | $-23 | -17.76% | -48.94% | $310 | $210 |
| live_filled_only | legacy_side_band | basket_pr2b_same_entry_band | 77 | $396 | $+83 | +20.88% | -10.52% | $189 | $140 |
| live_filled_only | legacy_side_band | basket_pr2b_legacy_raw_triggers | 37 | $247 | $+81 | +32.83% | -13.67% | $205 | $180 |
| live_filled_only | legacy_side_band | combo_market_risk_same_entry_band | 44 | $212 | $+140 | +66.17% | +6.62% | $241 | $245 |
| live_filled_only | legacy_side_band | combo_market_risk_legacy_raw_triggers | 16 | $123 | $+93 | +75.58% | -29.38% | $260 | $245 |

## Actual Live Reference

This is actual settled `live_real` PnL by recorded instance; it is not forced to the same representative snapshot, so use it as context, not a strict counterfactual.

| instance | fills | date range | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| live_weather_edge_v1_4b07f7abc42f | 57 | 2026-06-01 -> 2026-06-05 | $150 | $-6 | -3.87% | 52.6% |
| live_weather_edge_v1_4ef9b3ec3e2e | 707 | 2026-05-16 -> 2026-06-05 | $1950 | $+10 | +0.51% | 52.9% |
| live_weather_edge_v1_91f019941593 | 47 | 2026-06-01 -> 2026-06-05 | $92 | $+31 | +33.14% | 40.4% |
| live_weather_edge_v1_986d901ccc58 | 175 | 2026-05-29 -> 2026-06-05 | $423 | $-73 | -17.21% | 38.9% |
| live_weather_edge_v1_c13ccf0c3181 | 76 | 2026-05-24 -> 2026-06-01 | $213 | $-28 | -13.28% | 53.9% |
| live_weather_edge_v1_edb2b6f8df82 | 59 | 2026-05-27 -> 2026-05-30 | $188 | $+23 | +12.21% | 52.5% |

## Quant Read

- Judge basket against `legacy_raw_independent` first; ROI alone is not enough.
- `basket_pr2b_legacy_raw_triggers` isolates the grouping/sizing effect on the exact old-triggered rows.
- `same_entry_band` variants test whether basket finds better opportunities when given the same live-like price universe.
- Negative top5-removed ROI remains a tail-dependency warning even if headline ROI is positive.

Production remains unchanged.
