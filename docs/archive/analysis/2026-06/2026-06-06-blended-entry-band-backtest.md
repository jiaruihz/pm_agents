# Weather Edge Engine Blended Entry-Band Backtest

> 2026-06-06 口径勘误：本文生成于 near-binary settlement 修复前，自检里的 `missing_bracket=734` 已过时。opportunity selector 逻辑可作历史背景，但任何依赖 final settlement / ROI / live_filled 子集的数字需重算。

> generated_at_utc: `2026-06-05T18:35:43+00:00`
> strategy_id: `weather_edge_engine_blended_single_v0`
> strategy_spec: `weather_dashboard/strategy_specs/weather_edge_engine_blended_single_v0.json`
> db: `/home/rui/projects/pm_agent/runtime/weather.db`

## Target Metric

`blended_entry_band_shadow` = keep the current live entry-price window and min-edge gates, but replace raw probability with blended probability for the side-aware edge.

This is an opportunity-level counterfactual over `fact_signal_candidates`, not actual wallet PnL.

## Selector Profiles

| profile | source live instance | BUY_YES gate | BUY_NO gate |
|---|---|---|---|
| current_25_75 | mid_price_core_v1_25_75 / mid_price_core_v2_25_75 signal selector | 0.25-0.75, edge>=0.10 | 0.25-0.75, edge>=0.10 |
| current_side_band | mid_price_core_v1_side_band signal selector | 0.20-0.45, edge>=0.20 | 0.35-0.65, edge>=0.10 |

## Data Self-Check

```json
{
  "fact_trades_freshness": [
    {
      "max_fact_built_at_utc": "2026-06-05T18:31:00.166369+00:00"
    }
  ],
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 844
    },
    {
      "trade_class": "live_simulated",
      "rows": 1021
    },
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    }
  ],
  "fact_trades_by_settlement": [
    {
      "settlement_status": null,
      "rows": 299
    },
    {
      "settlement_status": "missing_bracket",
      "rows": 734
    },
    {
      "settlement_status": "settled",
      "rows": 3753
    }
  ],
  "fact_signal_candidates_coverage": [
    {
      "rows": 21797,
      "eligible": 6963,
      "paper_ordered": 2570,
      "live_filled": 455
    }
  ],
  "clob_orders_with_fills": [
    {
      "status": "error",
      "orders": 151,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 953,
      "with_fill": 844
    }
  ]
}
```

## Backtest Summary

| slice | profile | source | legs | cost | pnl | ROI | win_rate | top5 ROI |
|---|---|---|---:|---:|---:|---:|---:|---:|
| full | current_25_75 | raw | 790 | $3950 | $+196 | +4.95% | 61.1% | +3.11% |
| full | current_25_75 | blended | 80 | $400 | $+95 | +23.87% | 60.0% | +7.82% |
| full | current_side_band | raw | 385 | $1925 | $+131 | +6.80% | 56.4% | +2.40% |
| full | current_side_band | blended | 62 | $310 | $+34 | +11.03% | 59.7% | +0.52% |
| holdout_from_2026_05_26 | current_25_75 | raw | 235 | $1175 | $-66 | -5.62% | 56.2% | -11.11% |
| holdout_from_2026_05_26 | current_25_75 | blended | 26 | $130 | $+18 | +13.90% | 50.0% | -33.35% |
| holdout_from_2026_05_26 | current_side_band | raw | 119 | $595 | $+21 | +3.56% | 52.1% | -10.66% |
| holdout_from_2026_05_26 | current_side_band | blended | 16 | $80 | $-13 | -16.55% | 43.8% | -68.79% |
| recent_from_2026_06_01 | current_25_75 | raw | 84 | $420 | $+10 | +2.32% | 60.7% | -8.42% |
| recent_from_2026_06_01 | current_25_75 | blended | 7 | $35 | $-1 | -1.94% | 42.9% | -100.00% |
| recent_from_2026_06_01 | current_side_band | raw | 44 | $220 | $+62 | +28.25% | 63.6% | -0.44% |
| recent_from_2026_06_01 | current_side_band | blended | 4 | $20 | $-11 | -56.14% | 25.0% | +0.00% |
| live_filled_only | current_25_75 | raw | 206 | $1030 | $+59 | +5.74% | 60.2% | -0.83% |
| live_filled_only | current_25_75 | blended | 28 | $140 | $-5 | -3.54% | 46.4% | -39.30% |
| live_filled_only | current_side_band | raw | 99 | $495 | $+88 | +17.88% | 58.6% | +2.27% |
| live_filled_only | current_side_band | blended | 21 | $105 | $-18 | -16.74% | 42.9% | -56.35% |

## Blended vs Raw Attribution

| slice | profile | raw winning profit missed | raw losing cost avoided | net blended vs raw |
|---|---|---:|---:|---:|
| full | current_25_75 | $1475 | $1375 | $-100 |
| full | current_side_band | $812 | $715 | $-97 |
| holdout_from_2026_05_26 | current_25_75 | $366 | $450 | $+84 |
| holdout_from_2026_05_26 | current_side_band | $274 | $240 | $-34 |
| recent_from_2026_06_01 | current_25_75 | $155 | $145 | $-10 |
| recent_from_2026_06_01 | current_side_band | $138 | $65 | $-73 |
| live_filled_only | current_25_75 | $399 | $335 | $-64 |
| live_filled_only | current_side_band | $251 | $145 | $-106 |

## Current Live Actual Fills

These are actual settled `live_real` fills. They are not the same denominator as the opportunity replay.

| strategy_instance | fills | dates | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| live_weather_edge_v1_c13ccf0c3181 | 65 | 2026-05-24 -> 2026-06-01 | $251 | $-17 | -6.73% | 55.4% |
| live_weather_edge_v1_edb2b6f8df82 | 54 | 2026-05-27 -> 2026-05-30 | $203 | $+20 | +9.69% | 59.3% |
| mid_price_core_v1_25_75 | 331 | 2026-05-16 -> 2026-06-04 | $1152 | $+34 | +2.95% | 57.7% |
| mid_price_core_v1_side_band | 48 | 2026-06-01 -> 2026-06-04 | $135 | $+25 | +18.76% | 60.4% |
| mid_price_core_v2_25_75 | 132 | 2026-05-29 -> 2026-06-04 | $367 | $-43 | -11.73% | 42.4% |

## Read

- This is the fairer comparison for PR3a: current selector gates are preserved, only the probability/edge source changes.
- If blended has too few/no legs under a current gate, the blend is acting as a stricter confidence filter, not as a new execution strategy.
- Production remains unchanged; this is still shadow research.
