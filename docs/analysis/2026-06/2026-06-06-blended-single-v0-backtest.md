# Weather Edge Engine Blended Single v0 Backtest

> 2026-06-06 口径勘误：本文生成于 near-binary settlement 修复前，自检里的 `missing_bracket=734` 已过时。opportunity selector 逻辑可作历史背景，但任何依赖 final settlement / ROI / live_filled 子集的数字需重算。

> generated_at_utc: `2026-06-05T18:19:26+00:00`
> strategy_id: `weather_edge_engine_blended_single_v0`
> strategy_spec: `weather_dashboard/strategy_specs/weather_edge_engine_blended_single_v0.json`
> data: `/home/rui/projects/pm_agent/runtime/weather.db`

## Strategy Identity

| field | value |
|---|---|
| strategy_id | `weather_edge_engine_blended_single_v0` |
| strategy_family | `weather_edge_engine` |
| probability_source | `blended` |
| decision_mode | `single_leg_shadow` |
| execution_mode | `shadow` |
| code | `weather_dashboard/blend/blender.py` |
| config | `weather_dashboard/blend/city_blend_config.json` |

This strategy does not submit orders. It is a counterfactual shadow replay over the opportunity table.

## Data Self-Check

```json
{
  "fact_trades_freshness": [
    {
      "max_fact_built_at_utc": "2026-06-05T17:01:41.251360+00:00"
    }
  ],
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 792
    },
    {
      "trade_class": "live_simulated",
      "rows": 1014
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
      "rows": 288
    },
    {
      "settlement_status": "missing_bracket",
      "rows": 725
    },
    {
      "settlement_status": "settled",
      "rows": 3714
    }
  ],
  "fact_signal_candidates_coverage": [
    {
      "rows": 21788,
      "eligible": 6982,
      "paper_ordered": 2551,
      "live_filled": 452
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
      "orders": 946,
      "with_fill": 792
    }
  ]
}
```

## Opportunity Backtest

| slice | rule | legs | cost | pnl | ROI | win_rate | top5 ROI |
|---|---|---:|---:|---:|---:|---:|---:|
| full | raw_single | 1696 | $8480 | $+550 | +6.48% | 51.1% | -6.14% |
| full | market_only | 0 | $0 | $+0 | +0.00% | 0.0% | +0.00% |
| full | weather_edge_engine_blended_single_v0 | 1200 | $6000 | $+869 | +14.48% | 51.7% | -2.36% |
| holdout_from_2026_05_26 | raw_single | 537 | $2685 | $-289 | -10.76% | 47.3% | -24.54% |
| holdout_from_2026_05_26 | market_only | 0 | $0 | $+0 | +0.00% | 0.0% | +0.00% |
| holdout_from_2026_05_26 | weather_edge_engine_blended_single_v0 | 396 | $1980 | $-117 | -5.91% | 47.0% | -24.59% |
| recent_from_2026_06_01 | raw_single | 129 | $645 | $+10 | +1.54% | 57.4% | -10.79% |
| recent_from_2026_06_01 | market_only | 0 | $0 | $+0 | +0.00% | 0.0% | +0.00% |
| recent_from_2026_06_01 | weather_edge_engine_blended_single_v0 | 95 | $475 | $+20 | +4.27% | 58.9% | -10.88% |
| live_filled_only | raw_single | 288 | $1440 | $+36 | +2.52% | 58.0% | -3.41% |
| live_filled_only | market_only | 0 | $0 | $+0 | +0.00% | 0.0% | +0.00% |
| live_filled_only | weather_edge_engine_blended_single_v0 | 221 | $1105 | $+61 | +5.56% | 58.4% | -2.15% |

## Blended vs Raw Attribution

| slice | raw winning profit missed | raw losing cost avoided | shared profit | shared loss | net blended vs raw |
|---|---:|---:|---:|---:|---:|
| full | $931 | $1250 | $3764 | $-2895 | $+319 |
| holdout_from_2026_05_26 | $193 | $365 | $933 | $-1050 | $+172 |
| recent_from_2026_06_01 | $70 | $80 | $215 | $-195 | $+10 |
| live_filled_only | $120 | $145 | $521 | $-460 | $+25 |

## Current Live Actual Fills

These rows are real `trade_class='live_real'` settled fills. They are not the same denominator as the shadow opportunity replay.

| strategy_instance | fills | target dates | cost | pnl | ROI | win_rate |
|---|---:|---|---:|---:|---:|---:|
| mid_price_core_v1_25_75 | 331 | 2026-05-16 -> 2026-06-04 | $1152 | $+34 | +2.95% | 57.7% |
| mid_price_core_v1_side_band | 48 | 2026-06-01 -> 2026-06-04 | $135 | $+25 | +18.76% | 60.4% |
| live_weather_edge_v1_edb2b6f8df82 | 36 | 2026-05-27 -> 2026-05-30 | $116 | $+8 | +6.63% | 55.6% |
| live_weather_edge_v1_c13ccf0c3181 | 51 | 2026-05-24 -> 2026-06-01 | $181 | $-37 | -20.15% | 52.9% |
| mid_price_core_v2_25_75 | 125 | 2026-05-29 -> 2026-06-04 | $342 | $-50 | -14.76% | 42.4% |

## Read

- Full opportunity sample: `weather_edge_engine_blended_single_v0` improves raw_single ROI from +6.48% to +14.48%.
- `live_filled_only` opportunity subset: `weather_edge_engine_blended_single_v0` improves raw_single ROI from +2.52% to +5.56%.
- This is not a production approval: the strategy has not been shadow-written on N100逐 snapshot lineage yet.
- Next step is PR3a shadow double-write with the lineage fields listed in the strategy spec.
