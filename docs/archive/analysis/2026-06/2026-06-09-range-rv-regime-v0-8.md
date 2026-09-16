# Range RV Regime-Conditioned Scanner v0.8

> generated_at_utc: `2026-06-09T02:07:58.732744+00:00`
> target_metric: `regime_conditioned_range_rv_alpha`
> DB: `/home/rui/projects/pm_agent/runtime/weather.db`
> Scope: local research only; no N100/live config changed.

## Data Snapshot

- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.
- DB last_modified: `2026-06-08T17:23:59.056493+00:00`
- fact built at: `2026-06-08T17:23:55.700271+00:00`
- Decision sets: `647`; generated regime rows `1324`.
- Algorithms tested: `7`.
- Full opportunity set: old single-leg `eligible` is not used as a hard filter.
- No live_real PnL is published; this is opportunity-grain counterfactual research.

## Verdict

| gate | status |
|---|---|
| `significance` | `FAIL` |
| `baseline` | `FAIL` |
| `forward` | `FAIL` |
| `verdict` | `inconclusive` |

Final verdict: `inconclusive`. No live action.

## Algorithm Definitions

- `regime_model_confident_mode_cluster_yes_e010`: model distribution is tighter than market; buy YES around model mode cluster.
- `regime_market_overconfident_mode_no_e012`: market mode is richer and more concentrated than model; buy NO market mode.
- `regime_market_diffuse_model_cluster_yes_e014`: market is diffuse while model concentrates around one cluster; buy YES cluster.
- `regime_below/above_tail_fade_inner_pair_e010`: fade overpriced tail and buy adjacent inner bracket.
- `regime_center_band_yes_tail_no_e012`: buy center band YES and fade tails when center is underpriced vs tails.
- `regime_consensus_underpriced_single_yes_e008`: model and market share a mode, but model still prices it higher.

Forward gate requires holdout active_dates >= 5, rows >= 10, and top5-removed ROI > 0.

## Decision Proxy

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `regime_consensus_underpriced_single_yes_e008` | 60 | 31 | 16 | 17 | 5 | 3 | +27.2% | [-4.5%, +68.8%] | +21.3% | [-3.1%, +52.8%] | +63.0% | [-100.0%, +185.7%] | +65.8% | [-68.5%, +159.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_below_tail_fade_inner_pair_e010` | 116 | 63 | 19 | 34 | 14 | 4 | +10.0% | [-4.3%, +26.8%] | +2.9% | [-6.0%, +12.7%] | +27.3% | [-69.3%, +49.1%] | +12.4% | [-72.7%, +29.6%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_market_overconfident_mode_no_e012` | 221 | 137 | 20 | 71 | 43 | 6 | -0.0% | [-19.0%, +16.7%] | +0.1% | [-7.5%, +9.1%] | +12.8% | [-49.0%, +26.9%] | +7.5% | [-61.4%, +20.9%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `regime_model_confident_mode_cluster_yes_e010` | 221 | 117 | 16 | 73 | 33 | 4 | +8.0% | [-3.4%, +17.0%] | +2.6% | [-6.9%, +10.1%] | +9.4% | [-9.6%, +32.2%] | -0.8% | [-11.6%, +14.8%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_center_band_yes_tail_no_e012` | 57 | 27 | 11 | 14 | 3 | 2 | -6.4% | [-18.0%, +5.1%] | -4.5% | [-14.5%, +5.6%] | +0.3% | [-15.1%, +32.4%] | -1.4% | [-18.1%, +31.7%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_market_diffuse_model_cluster_yes_e014` | 220 | 112 | 16 | 72 | 30 | 4 | +9.4% | [-3.9%, +19.4%] | +4.0% | [-7.6%, +14.0%] | +6.7% | [-11.0%, +30.4%] | -3.3% | [-12.9%, +13.6%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_above_tail_fade_inner_pair_e010` | 109 | 53 | 17 | 39 | 15 | 8 | +20.6% | [+2.9%, +36.0%] | +10.4% | [-1.8%, +24.8%] | -8.5% | [-76.7%, +20.5%] | -4.6% | [-36.9%, +18.0%] | -100.0% | `PASS/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |

## Orderbook Executable Subset

Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; rows require all legs matched.

- Fully matched strategy rows: `657` / `1324`.

| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `regime_consensus_underpriced_single_yes_e008` | 21 | 16 | 6 | 17 | 5 | 3 | +7.1% | [-18.8%, +65.3%] | +11.6% | [-5.8%, +53.0%] | +53.8% | [-100.0%, +172.7%] | +61.5% | [-63.6%, +171.9%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_below_tail_fade_inner_pair_e010` | 44 | 21 | 8 | 33 | 14 | 4 | +5.5% | [-24.5%, +35.7%] | -1.2% | [-22.7%, +12.9%] | +24.9% | [-68.4%, +45.6%] | +12.5% | [-70.1%, +27.1%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_market_overconfident_mode_no_e012` | 75 | 42 | 8 | 71 | 43 | 6 | -13.6% | [-49.0%, +38.0%] | -5.0% | [-20.7%, +24.0%] | +10.0% | [-63.2%, +23.0%] | +7.6% | [-63.9%, +20.7%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `regime_model_confident_mode_cluster_yes_e010` | 74 | 38 | 4 | 73 | 33 | 4 | +2.5% | [-37.6%, +19.0%] | +3.1% | [-24.8%, +16.3%] | +1.5% | [-14.1%, +22.5%] | -2.2% | [-11.5%, +10.6%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_center_band_yes_tail_no_e012` | 21 | 14 | 4 | 12 | 3 | 2 | -8.7% | [-24.4%, +3.1%] | -4.1% | [-15.4%, +3.6%] | -2.5% | [-17.6%, +28.8%] | -4.0% | [-18.7%, +27.0%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_rows<10,holdout_top5_removed_roi<=0_or_not_available` |
| `regime_above_tail_fade_inner_pair_e010` | 32 | 13 | 6 | 39 | 15 | 8 | +32.4% | [-4.5%, +76.7%] | +26.1% | [-10.0%, +66.7%] | -10.1% | [-79.3%, +18.1%] | -4.7% | [-36.0%, +14.6%] | -100.0% | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_top5_removed_roi<=0_or_not_available` |
| `regime_market_diffuse_model_cluster_yes_e014` | 73 | 35 | 4 | 72 | 30 | 4 | +3.8% | [-28.6%, +23.7%] | +3.9% | [-20.8%, +23.5%] | -1.2% | [-16.6%, +38.9%] | -4.9% | [-13.6%, +28.5%] | NA | `FAIL/FAIL/FAIL -> inconclusive` | `holdout_active_dates<5,holdout_top5_removed_roi<=0_or_not_available` |

## Notes

- Regimes are computed from current normalized market/model probabilities only.
- Baseline is each algorithm's own top unfiltered regime candidate per city-day decision snapshot.
- This tests whether Range RV needs a distribution regime instead of a global rule.
