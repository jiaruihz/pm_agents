# Weather Analysis Script Migration Manifest

Status: current-reference
Updated: 2026-06-09 Phase 3A/3B executed
Source of truth: no
Superseded by / Used by: WEATHER_ARCHITECTURE_SPINE.md; docs/analysis living docs

This manifest records the target owner for scripts moved during Phase 3B. Historical Markdown snapshots and JSON artifacts are not moved in this phase.

## Rules

- Scripts that build or backfill fact-table fields belong to `scripts/etl/`.
- Weather evaluation scripts belong under `scripts/analysis/<living_doc_topic>/`.
- Copy-trade research is a separate strategy family and belongs to `scripts/copy_trade/`.
- Current docs and run scripts should point at the new paths after Phase 3B.
- Historical snapshots may keep old paths as time-point evidence until Phase 3C.

## Manifest

| Old path | New path | Owner |
|---|---|---|
| `scripts/analysis/build_weather_fact_trades.py` | `scripts/etl/build_weather_fact_trades.py` | [0] data |
| `scripts/analysis/build_weather_signal_candidates.py` | `scripts/etl/build_weather_signal_candidates.py` | [0] data |
| `scripts/analysis/backfill_signal_candidate_decision_windows.py` | `scripts/etl/backfill_signal_candidate_decision_windows.py` | [0] data |
| `scripts/analysis/model_vs_market/calibrate_weather_probability.py` | `scripts/analysis/model_vs_market/calibrate_weather_probability.py` | model_vs_market |
| `scripts/analysis/model_vs_market/research_mid_price_core_v1_degradation.py` | `scripts/analysis/model_vs_market/research_mid_price_core_v1_degradation.py` | model_vs_market |
| `scripts/analysis/model_vs_market/research_mid_price_core_v1_raw_calibration_drift.py` | `scripts/analysis/model_vs_market/research_mid_price_core_v1_raw_calibration_drift.py` | model_vs_market |
| `scripts/analysis/model_vs_market/research_mid_price_core_v1_forecast_timing_lineage.py` | `scripts/analysis/model_vs_market/research_mid_price_core_v1_forecast_timing_lineage.py` | model_vs_market |
| `scripts/analysis/model_vs_market/research_mid_price_core_v1_city_model_downgrade.py` | `scripts/analysis/model_vs_market/research_mid_price_core_v1_city_model_downgrade.py` | model_vs_market |
| `scripts/analysis/model_vs_market/research_model_rank_ic.py` | `scripts/analysis/model_vs_market/research_model_rank_ic.py` | model_vs_market |
| `scripts/analysis/model_vs_market/research_v1_raw_regime_filter_walkforward.py` | `scripts/analysis/model_vs_market/research_v1_raw_regime_filter_walkforward.py` | model_vs_market |
| `scripts/analysis/market_structure_edge/research_market_structural_edge.py` | `scripts/analysis/market_structure_edge/research_market_structural_edge.py` | market_structure_edge |
| `scripts/analysis/research_range_rv_scanner.py` | `scripts/analysis/market_structure_edge/research_range_rv_scanner.py` | market_structure_edge |
| `scripts/analysis/execution_quality/research_executable_edge.py` | `scripts/analysis/execution_quality/research_executable_edge.py` | execution_quality |
| `scripts/analysis/backtest_blended_paper_fill_estimate.py` | `scripts/analysis/execution_quality/backtest_blended_paper_fill_estimate.py` | execution_quality |
| `scripts/analysis/execution_quality/weather_window_capture_performance.py` | `scripts/analysis/execution_quality/weather_window_capture_performance.py` | execution_quality |
| `scripts/analysis/weather_clob_fill_coverage_gate.py` | `scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py` | execution_quality |
| `scripts/analysis/inspect_weather_live_timing_distribution.py` | `scripts/analysis/entry_timing/inspect_weather_live_timing_distribution.py` | entry_timing |
| `scripts/analysis/research_entry_timing_effect.py` | `scripts/analysis/entry_timing/research_entry_timing_effect.py` | entry_timing |
| `scripts/analysis/research_city_timing_effect.py` | `scripts/analysis/entry_timing/research_city_timing_effect.py` | entry_timing |
| `scripts/analysis/research_v1_removed_ecmwf_t28_blender_overlay.py` | `scripts/analysis/entry_timing/research_v1_removed_ecmwf_t28_blender_overlay.py` | entry_timing |
| `scripts/analysis/backtest_v1_ecmwf_blocked_side_band_overlay.py` | `scripts/analysis/entry_timing/backtest_v1_ecmwf_blocked_side_band_overlay.py` | entry_timing |
| `scripts/analysis/side_alpha/weather_side_band_alpha_summary.py` | `scripts/analysis/side_alpha/weather_side_band_alpha_summary.py` | side_alpha |
| `scripts/analysis/side_alpha/weather_side_band_entry_analysis.py` | `scripts/analysis/side_alpha/weather_side_band_entry_analysis.py` | side_alpha |
| `scripts/analysis/side_alpha/weather_side_band_timing_impact.py` | `scripts/analysis/side_alpha/weather_side_band_timing_impact.py` | side_alpha |
| `scripts/analysis/city_selection/compare_city_day_basket_vs_legacy_baselines.py` | `scripts/analysis/city_selection/compare_city_day_basket_vs_legacy_baselines.py` | city_selection |
| `scripts/analysis/city_selection/eval_city_day_basket.py` | `scripts/analysis/city_selection/eval_city_day_basket.py` | city_selection |
| `scripts/analysis/city_selection/tune_city_day_basket.py` | `scripts/analysis/city_selection/tune_city_day_basket.py` | city_selection |
| `scripts/analysis/city_selection/validate_city_day_basket_robustness.py` | `scripts/analysis/city_selection/validate_city_day_basket_robustness.py` | city_selection |
| `scripts/analysis/city_selection/research_city_day_basket_optimizer.py` | `scripts/analysis/city_selection/research_city_day_basket_optimizer.py` | city_selection |
| `scripts/analysis/city_selection/research_city_day_basket_walkforward.py` | `scripts/analysis/city_selection/research_city_day_basket_walkforward.py` | city_selection |
| `scripts/analysis/city_selection/research_city_day_distribution_quality.py` | `scripts/analysis/city_selection/research_city_day_distribution_quality.py` | city_selection |
| `scripts/analysis/research_weather_city_alpha_framework.py` | `scripts/analysis/city_selection/research_weather_city_alpha_framework.py` | city_selection |
| `scripts/analysis/city_selection/weather_city_day_portfolio.py` | `scripts/analysis/city_selection/weather_city_day_portfolio.py` | city_selection |
| `scripts/analysis/city_selection/weather_city_pool_contribution_analysis.py` | `scripts/analysis/city_selection/weather_city_pool_contribution_analysis.py` | city_selection |
| `scripts/analysis/city_selection/weather_near_binary_city_reanalysis.py` | `scripts/analysis/city_selection/weather_near_binary_city_reanalysis.py` | city_selection |
| `scripts/analysis/city_selection/research_city_model_conditional_edge.py` | `scripts/analysis/city_selection/research_city_model_conditional_edge.py` | city_selection |
| `scripts/analysis/sizing_entry_band/weather_sizing_band_study.py` | `scripts/analysis/sizing_entry_band/weather_sizing_band_study.py` | sizing_entry_band |
| `scripts/analysis/sizing_entry_band/weather_entry_band_research.py` | `scripts/analysis/sizing_entry_band/weather_entry_band_research.py` | sizing_entry_band |
| `scripts/analysis/blender_shadow/backtest_weather_edge_engine_blended_single.py` | `scripts/analysis/blender_shadow/backtest_weather_edge_engine_blended_single.py` | blender_shadow |
| `scripts/analysis/blender_shadow/backtest_weather_edge_engine_blended_entry_bands.py` | `scripts/analysis/blender_shadow/backtest_weather_edge_engine_blended_entry_bands.py` | blender_shadow |
| `scripts/analysis/blender_shadow/weather_blended_live_instance_overlay.py` | `scripts/analysis/blender_shadow/weather_blended_live_instance_overlay.py` | blender_shadow |
| `scripts/analysis/blender_shadow/build_weather_edge_v2_shadow_lineage.py` | `scripts/analysis/blender_shadow/build_weather_edge_v2_shadow_lineage.py` | blender_shadow |
| `scripts/analysis/blender_shadow/research_weather_edge_v2_filtered_operational_base.py` | `scripts/analysis/blender_shadow/research_weather_edge_v2_filtered_operational_base.py` | blender_shadow |
| `scripts/analysis/blender_shadow/research_blender_signal_value.py` | `scripts/analysis/blender_shadow/research_blender_signal_value.py` | blender_shadow |
| `scripts/analysis/blender_shadow/recalibrate_blend.py` | `scripts/analysis/blender_shadow/recalibrate_blend.py` | blender_shadow |
| `scripts/analysis/live_performance/weather_live_full_research.py` | `scripts/analysis/live_performance/weather_live_full_research.py` | live_performance |
| `scripts/analysis/live_performance/weather_live_pnl_curve.py` | `scripts/analysis/live_performance/weather_live_pnl_curve.py` | live_performance |
| `scripts/analysis/live_performance/weather_live_strategy_period_slice.py` | `scripts/analysis/live_performance/weather_live_strategy_period_slice.py` | live_performance |
| `scripts/analysis/live_performance/weather_account_equity_replay.py` | `scripts/analysis/live_performance/weather_account_equity_replay.py` | live_performance |
| `scripts/analysis/live_performance/weather_recent_live_loss_attribution.py` | `scripts/analysis/live_performance/weather_recent_live_loss_attribution.py` | live_performance |
| `scripts/analysis/live_performance/pnl_curve_detailed.py` | `scripts/analysis/live_performance/pnl_curve_detailed.py` | live_performance |
| `scripts/analysis/live_performance/weather_three_strategy_overlap_analysis.py` | `scripts/analysis/live_performance/weather_three_strategy_overlap_analysis.py` | live_performance |
| `scripts/analysis/live_performance/force_settle_live_real.py` | `scripts/analysis/live_performance/force_settle_live_real.py` | live_performance |
| `scripts/analysis/weather_live_account_reconcile.py` | `scripts/analysis/account_reconcile/weather_live_account_reconcile.py` | account_reconcile |
| `scripts/analysis/account_reconcile/weather_polymarket_account_activity.py` | `scripts/analysis/account_reconcile/weather_polymarket_account_activity.py` | account_reconcile |
| `scripts/analysis/account_reconcile/weather_polymarket_position_snapshot.py` | `scripts/analysis/account_reconcile/weather_polymarket_position_snapshot.py` | account_reconcile |
| `scripts/analysis/account_reconcile/weather_polymarket_snapshot_summary.py` | `scripts/analysis/account_reconcile/weather_polymarket_snapshot_summary.py` | account_reconcile |
| `scripts/analysis/data_integrity/inspect_weather_signal_side_flips.py` | `scripts/analysis/data_integrity/inspect_weather_signal_side_flips.py` | data_integrity |
| `scripts/analysis/data_integrity/inspect_weather_snapshot_bracket_evolution.py` | `scripts/analysis/data_integrity/inspect_weather_snapshot_bracket_evolution.py` | data_integrity |
| `scripts/analysis/data_integrity/inspect_weather_snapshot_side_flip_transitions.py` | `scripts/analysis/data_integrity/inspect_weather_snapshot_side_flip_transitions.py` | data_integrity |
| `scripts/analysis/build_copy_trade_address_pools.py` | `scripts/copy_trade/build_copy_trade_address_pools.py` | copy_trade |
| `scripts/analysis/copy_trade_event_driven_scanner.py` | `scripts/copy_trade/copy_trade_event_driven_scanner.py` | copy_trade |
| `scripts/analysis/copy_trade_position_analyzer.py` | `scripts/copy_trade/copy_trade_position_analyzer.py` | copy_trade |
| `scripts/copy_trade/copy_trade_rule_edge_wallet_research.py` | `scripts/copy_trade/copy_trade_rule_edge_wallet_research.py` | copy_trade |
| `scripts/analysis/copy_trade_tech_market_scanner.py` | `scripts/copy_trade/copy_trade_tech_market_scanner.py` | copy_trade |
| `scripts/analysis/copy_trade_thematic_scan.py` | `scripts/copy_trade/copy_trade_thematic_scan.py` | copy_trade |
| `scripts/analysis/copy_trade_wallet_research.py` | `scripts/copy_trade/copy_trade_wallet_research.py` | copy_trade |
