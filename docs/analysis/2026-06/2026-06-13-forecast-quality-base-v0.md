# Forecast Quality / Reliability Base v0

> generated_at_utc: `2026-06-13T17:23:50.739970+00:00`
> target_metric: `forecast_quality_reliability_base_v0`
> DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
> Scope: reusable forecast-quality base research only; no N100/live config changed; no live action.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` for opportunity/reliability labels; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-13T02:33:01.796274+00:00`.
- fact_signal_candidates rows: `28197`.
- decision_sets used: `262` settled city/event/model/snapshot distributions.
- strategy overlay rows: `776` across `3` algorithms.
- train: `2026-05-06` -> `2026-05-28` (21 event_dates).
- holdout: `2026-05-29` -> `2026-06-10` (10 event_dates).
- 本报告不发布 `live_real` PnL/ROI/rank/curve，因此不使用 CLOB coverage gate 作为结论来源。

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-13T02:32:52.534281+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "rows": 856
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
      "rows": 4251
    }
  ],
  "candidate_coverage": {
    "rows": 28197,
    "eligible": 9583,
    "paper_ordered": 3591,
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
      "orders": 962,
      "with_fill": 856
    }
  ]
}
```

## Target Metric

`forecast_quality_reliability_base_v0` = at city + event_date + model/source + decision checkpoint grain, classify whether the model distribution was historically reliable before applying any strategy-private thresholds.

The denominator is settled decision-set distributions built from `BUY_YES + BUY_NO` union rows. Settlement fields are labels only; no raw CSV, legacy DB, or old replay source is used.

## Reusable Labels

| label | all_rows | all_dates | train_adj3 | holdout_rows | holdout_dates | holdout_adj3 | holdout_tail_miss | holdout_distance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| forecast_quality_high | 18 | 12 | +100.0% | 7 | 5 | +100.0% | +0.0% | 1.14 |
| forecast_quality_medium | 58 | 20 | +98.0% | 9 | 3 | +100.0% | +0.0% | 0.56 |
| forecast_quality_medium_plus | 71 | 23 | +98.3% | 13 | 5 | +100.0% | +0.0% | 0.77 |
| forecast_quality_low | 191 | 30 | +90.7% | 51 | 10 | +94.1% | +5.9% | 1.02 |
| city_model_reliable | 81 | 26 | +98.1% | 29 | 9 | +100.0% | +0.0% | 0.76 |
| city_model_unreliable | 46 | 22 | +92.3% | 20 | 8 | +90.0% | +10.0% | 1.25 |
| model_market_disagreement_high | 76 | 18 | +77.6% | 18 | 2 | +83.3% | +16.7% | 1.56 |
| market_lag_candidate | 4 | 3 | +100.0% | 2 | 1 | +100.0% | +0.0% | 2.00 |
| high_uncertainty | 157 | 28 | +89.0% | 39 | 9 | +92.3% | +7.7% | 1.10 |
| sharp_model_confident | 29 | 17 | +100.0% | 10 | 5 | +100.0% | +0.0% | 0.90 |
| tail_risk_high | 91 | 18 | +81.9% | 19 | 2 | +84.2% | +15.8% | 1.21 |

## Cross-Family Overlay

Rows below are decision-price proxy checks. They answer whether the same base labels help multiple strategy families; they are not executable/live conclusions.

| algorithm | filter | train_rows | train_roi | holdout_rows | holdout_dates | holdout_roi | holdout_excess | top5_removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adjacent3_yes_cost085 | no_quality_filter | 124 | +27.5% | 36 | 3 | +26.8% | +0.0% | NA |
| adjacent3_yes_cost085 | forecast_quality_medium_plus | 31 | +20.1% | 6 | 2 | +28.6% | +1.7% | NA |
| adjacent3_yes_cost085 | exclude_forecast_quality_low | 31 | +20.1% | 6 | 2 | +28.6% | +1.7% | NA |
| adjacent3_yes_cost085 | city_model_reliable | 26 | +24.1% | 11 | 2 | +11.1% | -15.7% | NA |
| side_band_best_leg_mid_cost_e008_top4 | no_quality_filter | 302 | +2.9% | 103 | 10 | -0.6% | +0.0% | -15.2% |
| side_band_best_leg_mid_cost_e008_top4 | forecast_quality_medium_plus | 89 | +1.0% | 27 | 5 | +10.3% | +10.9% | NA |
| side_band_best_leg_mid_cost_e008_top4 | exclude_forecast_quality_low | 89 | +1.0% | 27 | 5 | +10.3% | +10.9% | NA |
| side_band_best_leg_mid_cost_e008_top4 | city_model_reliable | 79 | +6.5% | 52 | 9 | +11.2% | +11.8% | -76.4% |
| single_leg_buy_no_cost40_75_edge010_top1 | no_quality_filter | 157 | +3.3% | 54 | 10 | -6.8% | +0.0% | -27.1% |
| single_leg_buy_no_cost40_75_edge010_top1 | forecast_quality_medium_plus | 41 | +11.7% | 11 | 5 | +10.9% | +17.7% | NA |
| single_leg_buy_no_cost40_75_edge010_top1 | exclude_forecast_quality_low | 41 | +11.7% | 11 | 5 | +10.9% | +17.7% | NA |
| single_leg_buy_no_cost40_75_edge010_top1 | city_model_reliable | 37 | +11.9% | 25 | 8 | +11.4% | +18.2% | -22.0% |

## What Looks Reusable

- `forecast_quality_low` is the broad weak-quality bucket so far: holdout adjacent3 hit `+94.1%` and tail miss `+5.9%` are worse than the medium/high buckets, but this is still a soft diagnostic tag rather than a hard no-trade rule.
- `forecast_quality_medium_plus` is a better soft allow tag than `forecast_quality_high`: high is interpretable but narrow; medium_plus keeps `13` holdout decision sets.
- `city_model_reliable` remains promising as a soft overlay, but it is still sample-thin and city/model-history dependent.
- `market_lag_candidate` and `model_market_disagreement_high` are diagnostic tags, not green lights. They should be logged in shadow and tested as interaction terms, not used alone.

## Reuse Contract

Future strategy research should consume this as a shared reliability layer, not as a private filter copied into one strategy file.

- Source grain: build labels from `fact_signal_candidates` at `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc` decision-set grain. Use `fact_trades` only for mandatory self-checks or realized-fill analysis.
- Threshold rule: derive thresholds on the train window for each rerun. Do not hard-code the 2026-06-13 quantiles as live config.
- Baseline rule: every consumer must compare against its own no-quality-filter family baseline before claiming benefit.
- Execution rule: any executable claim must reprice selected legs using orderbook snapshots with `snapshot_ts_utc <= decision_snapshot_ts_utc`; this v0 report only gives decision-price proxy overlays.
- Verdict rule: labels can support research/shadow segmentation now. They cannot change N100 live behavior unless a later consumer passes significance, baseline, and forward gates.

Consumer guidance:

- Range RV / adjacent3: test `forecast_quality_medium_plus` and `exclude_forecast_quality_low` as soft slices around the same range expression. Report no-filter, medium-plus, exclude-low, and city-model-reliable side by side.
- Side-band: use `city_model_reliable`, `forecast_quality_medium_plus`, `model_market_disagreement_high`, and `tail_risk_high` as interaction tags. Keep side-band's same-price or same-family baseline; do not let the reliability tag become the strategy definition.
- BUY_NO single-leg: use `forecast_quality_medium_plus` and `city_model_reliable` as candidate ranking/risk tags, then still enforce price, edge, orderbook, and top-date stress gates.
- Basket / portfolio: aggregate labels to city-day/model level as risk and sizing inputs. Treat `forecast_quality_low` as weak-quality exposure, not an automatic no-trade ban.
- Shadow journals: log all labels next to would-trade rows so later settlement can answer whether the label was broadly useful or only helped one family.

## Next Research Directions

1. Materialize `forecast_run_ts_utc`, forecast issuance/checkpoint age, and forecast-source run id into `fact_signal_candidates` so reliability can distinguish stale forecasts from fresh ones.
2. Materialize same-checkpoint ECMWF/GFS paired distribution features, including mode distance, L1 distribution gap, and entropy gap, instead of approximating by city/event/snapshot grouping.
3. Promote the base label builder into a reusable artifact, ideally a fact-table sidecar or generated parquet/JSON, so Range RV, adjacent3, side-band, BUY_NO, and basket scripts consume the same labels.
4. Re-run after more settled forward dates, then evaluate by active event_date, city concentration, top5 removed, and event-date cluster bootstrap before any shadow-to-paper promotion.
5. Add time-aligned orderbook overlays for the strongest consumers, especially BUY_NO single-leg and side-band. Proxy improvements without orderbook survival should stay research-only.
6. Test basket-level use separately: the label may be more valuable for exposure control and sizing than for single-leg selection.

## Three-Gate Verdict

| gate | status | reason |
| --- | --- | --- |
| significance | FAIL | Base calibration tags have direction, but cross-family overlay excess ROI still has thin holdout support and unstable top-date stress. |
| baseline | FAIL | The same label does not yet beat each family baseline across adjacent3, BUY_NO single-leg, and side-band proxy in a durable way. |
| forward | FAIL | Holdout exists, but many useful overlays are only a few dates or collapse under top5 removed. |

`significance=FAIL`, `baseline=FAIL`, `forward=FAIL`, `conclusion=inconclusive` for live action.

## Plain-English Conclusion

Forecast quality has real value as a shared reliability layer, but v0 is not a confirmed live edge. The useful product of this pass is the reusable label set and a common denominator for later strategy families, not a new trading rule.

Best next step: materialize forecast run age / forecast issuance timestamp and same-checkpoint ECMWF-GFS paired distributions into `fact_signal_candidates`, then rerun this base as a stable feature table before letting Range RV, adjacent3, side-band, single-leg, and basket consume it.
