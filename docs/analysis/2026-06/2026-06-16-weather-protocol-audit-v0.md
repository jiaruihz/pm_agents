# Weather Protocol Audit v0

> generated_at_utc: `2026-06-15T16:16:28.587238+00:00`
> Scope: read-only protocol audit for weather-predict and pm_agent producer outputs.

## Summary

- `weather_predict_orderbook_snapshot`: sampled 200 rows from `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/orderbook_snapshots/2026-06-16/orderbook_snapshot_20260616_0000.jsonl.gz`; canonical_complete=False.
  Missing required: `{'market_price': 200, 'producer_run_id': 200, 'producer_system': 200, 'schema_version': 200, 'signal_side': 200, 'target_date': 200}`.
  Legacy aliases/values: `{'event_date': 200, 'outcome': 200}`.
- `weather_predict_research_paper_order`: sampled 200 rows from `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/paper_trades/paper_orders.jsonl`; canonical_complete=False.
  Missing required: `{'market_price': 200, 'model_p_yes': 200, 'model_version': 200, 'order_side': 200, 'producer_run_id': 200, 'producer_system': 200, 'schema_version': 200, 'target_date': 200}`.
  Legacy aliases/values: `{'event_date': 200, 'market_yes_price': 200, 'mode': 200, 'model': 200, 'model_prob': 200, 'side': 200}`.
- `pm_agent_live_signal`: sampled 1 rows from `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/remote_pm_agent/signals/live_mid_price_core_v1_side_band_20260610T234812Z_signals.jsonl`; canonical_complete=False.
  Missing required: `{'forecast_source': 1, 'model_p_yes': 1, 'producer_run_id': 1, 'producer_system': 1, 'schema_version': 1}`.
  Legacy aliases/values: `{'model_probability_yes': 1, 'profile': 1, 'source_run_id': 1, 'source_system': 1, 'order_side=BUY': 1}`.
- `pm_agent_trade_plan`: sampled 1 rows from `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/remote_pm_agent/plans/live_mid_price_core_v1_side_band_20260610T201723Z_trade_plans.jsonl`; canonical_complete=False.
  Missing required: `{'forecast_source': 1, 'model_p_yes': 1, 'producer_run_id': 1, 'producer_system': 1, 'schema_version': 1}`.
  Legacy aliases/values: `{'model_token_probability': 1, 'profile': 1, 'order_side=BUY': 1}`.
- `pm_agent_live_order`: sampled 1 rows from `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/remote_pm_agent/live/live_mid_price_core_v1_side_band_20260610T201723Z_orders.jsonl`; canonical_complete=False.
  Missing required: `{'producer_run_id': 1, 'producer_system': 1, 'schema_version': 1}`.
  Legacy aliases/values: `{'model_token_probability': 1, 'order_side=BUY': 1}`.
- `settlement_outcomes`: table_exists=True, rows=18011, city_days=1639, max_target_date=2026-06-14.

## Recommended Next Steps

1. Add canonical aliases to weather-predict snapshots and research paper orders first.
2. Add canonical aliases to pm_agent live signals/plans/orders without changing execution.
3. Keep `settlement_outcomes` as the DB bridge for pm_history truth while producer ownership remains in weather-predict.
