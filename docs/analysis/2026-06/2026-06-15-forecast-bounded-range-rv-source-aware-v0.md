# Forecast-Bounded Range RV Source-Aware v0

> generated_at_utc: `2026-06-14T16:51:53.227722+00:00`
> target_metric: `source_aware_forecast_bounded_range_rv_expression_alpha_v0`
> DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
> Scope: opportunity/proxy + time-aligned orderbook research only; no N100/live config changed; no live action.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates`; forecast-quality/source labels from `research_forecast_quality_source_adjusted_v0` helpers.
- DB last_modified: `2026-06-14T16:46:46.448210+00:00`.
- fact_signal_candidates rows: `29315`.
- decision_sets used: `262` at `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc` grain.
- strategy rows: `1346` across `6` algorithms.
- train: `2026-05-06` -> `2026-05-28` (21 event_dates).
- holdout: `2026-05-29` -> `2026-06-10` (10 event_dates).
- CLOB coverage gate: `True`; this report still does not publish live_real PnL/ROI/rank/curve.

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-14T16:46:35.279010+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "rows": 855
    },
    {
      "trade_class": "live_simulated",
      "rows": 624
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
  "settlement_status_distribution": [
    {
      "settlement_status": "",
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4250
    }
  ],
  "candidate_coverage": {
    "rows": 29315,
    "eligible": 10032,
    "paper_ordered": 3824,
    "live_filled": 348
  },
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 961,
      "with_fill": 855
    }
  ]
}
```

## Target Metric

`source_aware_forecast_bounded_range_rv_expression_alpha_v0` = fixed compact Range RV expressions around the forecast mode, evaluated after joining forecast-quality and settlement-source labels at the same decision-set grain.

The three consumer rows per algorithm are exactly: `no_filter`, `exclude_forecast_quality_low`, and `source_bucket_default_wu`. HK/Jakarta/station-diff rows are marked `source_sensitive` and excluded from the generic `default_wu` conclusion.

## Source Bucket Funnel

| source_bucket | decision_sets | cities | holdout_decision_sets |
| --- | --- | --- | --- |
| default_wu | 188 | 34 | 46 |
| source_sensitive_confirmed | 45 | 8 | 11 |
| blocked_unresolved | 19 | 3 | 5 |
| other_or_unknown | 10 | 3 | 2 |

## Decision-Price Proxy: Top Rows

Proxy rows are not live edge; they are the same decision-price counterfactual layer used for opportunity research.

| algorithm | row_filter | family | train rows | train dates | train ROI | train excess | train excess CI | holdout rows | holdout dates | holdout ROI | holdout excess | holdout excess CI | top5 removed | gates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `forecast_bounded_w4_cheaper` | `exclude_forecast_quality_low` | 31 | 12 | 7 | -3.9% | -3.6% | [-16.3%, +7.5%] | 3 | 1 | +14.0% | +7.5% | [+3.4%, +7.5%] | NA | `FAIL/FAIL/PASS -> inconclusive` |
| `forecast_bounded_w4_inside_yes` | `exclude_forecast_quality_low` | 31 | 12 | 7 | -5.0% | -4.3% | [-21.1%, +6.6%] | 3 | 1 | +13.1% | +7.0% | [+3.1%, +7.0%] | NA | `FAIL/FAIL/PASS -> inconclusive` |
| `forecast_bounded_w3_inside_yes` | `no_filter` | 262 | 164 | 20 | +20.6% | +5.1% | [+2.5%, +8.8%] | 57 | 8 | +19.2% | +2.6% | [+0.9%, +5.8%] | +6.4% | `PASS/PASS/PASS -> confirmed` |
| `forecast_bounded_w3_inside_yes` | `source_bucket_default_wu` | 188 | 119 | 20 | +21.9% | +5.4% | [+2.1%, +10.2%] | 41 | 7 | +18.3% | +2.5% | [+0.4%, +9.0%] | +6.4% | `PASS/PASS/PASS -> confirmed` |
| `forecast_bounded_w3_cheaper` | `no_filter` | 262 | 165 | 20 | +14.9% | +2.0% | [+1.2%, +3.2%] | 57 | 8 | +17.4% | +2.2% | [+0.7%, +5.2%] | +6.4% | `PASS/PASS/PASS -> confirmed` |
| `forecast_bounded_w3_inside_yes` | `exclude_forecast_quality_low` | 71 | 50 | 17 | +13.4% | +1.9% | [+0.4%, +3.7%] | 12 | 4 | +18.5% | +2.0% | [+0.0%, +9.0%] | NA | `PASS/PASS/FAIL -> inconclusive` |
| `forecast_bounded_w3_cheaper` | `source_bucket_default_wu` | 188 | 121 | 20 | +15.8% | +2.1% | [+1.5%, +3.0%] | 41 | 7 | +16.1% | +1.9% | [+0.3%, +7.4%] | +6.4% | `PASS/PASS/PASS -> confirmed` |
| `forecast_bounded_w3_cheaper` | `exclude_forecast_quality_low` | 71 | 50 | 17 | +12.4% | +1.6% | [+0.2%, +3.6%] | 12 | 4 | +15.4% | +1.4% | [+0.0%, +6.7%] | NA | `PASS/PASS/FAIL -> inconclusive` |
| `forecast_bounded_w4_cheaper` | `no_filter` | 149 | 78 | 13 | +5.4% | +1.7% | [+0.5%, +4.3%] | 22 | 2 | +3.8% | +0.0% | [-1.7%, +2.8%] | NA | `PASS/PASS/FAIL -> inconclusive` |
| `forecast_bounded_w4_inside_yes` | `no_filter` | 149 | 75 | 13 | +5.7% | +4.0% | [+1.3%, +7.2%] | 22 | 2 | +3.3% | -0.1% | [-2.1%, +2.6%] | NA | `FAIL/PASS/FAIL -> inconclusive` |

## Time-Aligned Orderbook: Top Rows

Orderbook rows require every selected leg to match an orderbook snapshot with `snapshot_ts_utc <= decision_snapshot_ts_utc`.

| algorithm | row_filter | family | train rows | train dates | train ROI | train excess | train excess CI | holdout rows | holdout dates | holdout ROI | holdout excess | holdout excess CI | top5 removed | gates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `forecast_bounded_w4_inside_yes` | `exclude_forecast_quality_low` | 15 | 7 | 3 | -9.3% | -2.6% | [-4.3%, +0.0%] | 3 | 1 | +5.3% | +4.3% | [+2.3%, +4.3%] | NA | `FAIL/FAIL/PASS -> inconclusive` |
| `forecast_bounded_w4_cheaper` | `exclude_forecast_quality_low` | 15 | 7 | 3 | -8.6% | -2.5% | [-4.2%, +0.0%] | 3 | 1 | +5.5% | +4.1% | [+2.3%, +4.1%] | NA | `FAIL/FAIL/PASS -> inconclusive` |
| `forecast_bounded_w3_inside_yes` | `no_filter` | 134 | 58 | 8 | +9.2% | +5.0% | [+1.0%, +8.5%] | 57 | 8 | +11.3% | +1.9% | [+0.6%, +5.3%] | +2.9% | `FAIL/PASS/PASS -> inconclusive` |
| `forecast_bounded_w3_inside_yes` | `source_bucket_default_wu` | 98 | 45 | 8 | +9.8% | +4.7% | [+0.0%, +9.4%] | 41 | 7 | +10.8% | +1.9% | [+0.3%, +9.3%] | +1.5% | `FAIL/FAIL/PASS -> inconclusive` |
| `forecast_bounded_w3_inside_yes` | `exclude_forecast_quality_low` | 34 | 17 | 6 | +2.7% | +1.0% | [-3.2%, +5.1%] | 12 | 4 | +10.1% | +1.9% | [+0.0%, +8.4%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `forecast_bounded_w3_cheaper` | `no_filter` | 130 | 56 | 8 | +8.8% | +2.0% | [+0.5%, +3.9%] | 57 | 8 | +10.3% | +1.7% | [+0.5%, +4.7%] | +2.9% | `PASS/PASS/PASS -> confirmed` |
| `forecast_bounded_w3_cheaper` | `source_bucket_default_wu` | 95 | 44 | 8 | +10.1% | +1.1% | [+0.0%, +1.8%] | 41 | 7 | +9.7% | +1.6% | [+0.2%, +8.8%] | +1.5% | `PASS/FAIL/PASS -> inconclusive` |
| `forecast_bounded_w3_cheaper` | `exclude_forecast_quality_low` | 34 | 17 | 6 | +2.5% | +0.8% | [-1.6%, +4.3%] | 12 | 4 | +8.2% | +1.4% | [+0.0%, +6.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` |
| `forecast_bounded_w2_cheaper` | `no_filter` | 130 | 60 | 9 | +11.2% | +2.5% | [+0.0%, +7.9%] | 60 | 10 | +9.9% | -0.7% | [-2.9%, +0.1%] | -32.1% | `PASS/FAIL/FAIL -> inconclusive` |
| `forecast_bounded_w2_cheaper` | `source_bucket_default_wu` | 95 | 46 | 7 | +9.7% | +0.2% | [-2.2%, +10.0%] | 44 | 10 | +15.1% | -0.8% | [-2.5%, +0.0%] | -33.2% | `FAIL/FAIL/FAIL -> inconclusive` |

## Source-Sensitive Diagnostic

These rows include HK/Jakarta/station-diff source-sensitive cities. They are shown to prevent accidental pooling into generic claims.

| algorithm | rows | cities | holdout selected | holdout dates | holdout ROI | note |
| --- | --- | --- | --- | --- | --- | --- |
| `forecast_bounded_w2_cheaper` | 21 | 7 | 10 | 5 | +0.4% | source_sensitive; excluded from generic conclusion |
| `forecast_bounded_w2_inside_yes` | 21 | 7 | 10 | 5 | +1.2% | source_sensitive; excluded from generic conclusion |
| `forecast_bounded_w3_cheaper` | 21 | 7 | 10 | 5 | -1.8% | source_sensitive; excluded from generic conclusion |
| `forecast_bounded_w3_inside_yes` | 21 | 7 | 10 | 5 | -1.4% | source_sensitive; excluded from generic conclusion |
| `forecast_bounded_w4_cheaper` | 10 | 5 | 4 | 2 | -19.3% | source_sensitive; excluded from generic conclusion |
| `forecast_bounded_w4_inside_yes` | 10 | 5 | 4 | 2 | -19.3% | source_sensitive; excluded from generic conclusion |

## Findings

- The data grain is now aligned with the forecast-quality base: source/model are part of the decision-set key.
- `source_bucket_default_wu` is the only generic denominator in the final verdict. The all-source `no_filter` rows are diagnostic only because they include source-sensitive and unresolved settlement-basis cities.
- `exclude_forecast_quality_low` does not become an alpha proof by itself; it is a soft stratification row against the no-filter family baseline.
- Any positive proxy row remains only an opportunity result. The orderbook table is stricter, but still not a live fill result.

## Three-Gate Verdict

| gate | status | reason |
| --- | --- | --- |
| significance | FAIL | No default_wu Range RV expression passed proxy and time-aligned orderbook gates with support/top5 stress. |
| baseline | FAIL | No default_wu Range RV expression passed proxy and time-aligned orderbook gates with support/top5 stress. |
| forward | FAIL | No default_wu Range RV expression passed proxy and time-aligned orderbook gates with support/top5 stress. |

`significance=FAIL`, `baseline=FAIL`, `forward=FAIL`, `conclusion=inconclusive`.

Plain-English conclusion: forecast-bounded Range RV is cleaner after the source-aware base fix, but this run still cannot be promoted beyond research/shadow instrumentation. No live config change.
