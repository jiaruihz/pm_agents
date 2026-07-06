# Feature Layer Forecast-Tail vs Regime-Route Capture Check v1

Status: snapshot
Date: 2026-07-07
Scope: offline/dry-run only; no live order path touched

## Question

If forecast-tail and regime-route both attach `feature_frame_ref`, are they
actually using the same feature set, or are their strategy features different?

## Method

Ran both strategy families through their non-live feature capture paths with a
temporary `WEATHER_FEATURE_STORE_DIR`.

Commands:

```bash
tmp=$(mktemp -d)
WEATHER_FEATURE_STORE_DIR="$tmp/store" \
  .venv/bin/python scripts/ops/low_price_yes_integrated_tail_shadow_v2.py \
  run --dry-run --allow-settled --max-candidates-per-run 5 > "$tmp/forecast_tail.json"

WEATHER_FEATURE_STORE_DIR="$tmp/store" \
  .venv/bin/python scripts/ops/regime_routed_no_shadow_v1.py \
  --journal "$tmp/regime.jsonl" \
  --summary "$tmp/regime_summary.json" \
  --min-target-date 2026-07-01 \
  --max-candidates-per-run 5
```

## Capture Result

| family | feature grain | rows | stored refs | errors |
|---|---:|---:|---:|---:|
| forecast-tail / low-price YES integrated shadow | `low_price_yes_integrated_tail_shadow_decision` | 2 | 2 | 0 |
| regime-route NO shadow | `regime_routed_no_shadow_candidate` | 4 | 4 | 0 |

## Field Overlap

The two captured grains shared only 25 columns, mostly metadata and identity:

- feature metadata: `feature_schema_version`, `feature_grain`,
  `feature_version_manifest`, `pit_provenance`, `builder_version`,
  `input_snapshot_id`, `feature_frame_ref`
- row identity: `city`, `target_date`, `bracket`, `side`,
  `decision_snapshot_ts_utc`, `shadow_decision_id`
- coarse forecast fields: `forecast_source`, `forecast_max_native`,
  `forecast_peak_hour_local`
- execution/capture flags: `execution_mode`, `no_order_placed`, `rule_id`,
  `strategy_id`, `created_at_utc`

That overlap is the shared feature-layer spine. It is not enough to make the two
strategies interchangeable.

## Forecast-Tail Specific Feature Shape

Forecast-tail captured 127 columns. The strategy-specific surface is dominated
by:

- forecast-tail geometry:
  `forecast_to_bracket_low_native`, `forecast_above_bracket_high_native`,
  `forecast_inside_bracket_bounds`, `bracket_low_native`,
  `bracket_high_native`, `bracket_dist_br_v1`
- station/source bias:
  `bias_n_asof`, `bias_mean_asof`, `bias_p50_asof`, `bias_p90_asof`,
  `hot_tail_pct_asof`, `hot_tail2_pct_asof`, `cold_tail_pct_asof`
- low-price YES market fields:
  `ask`, `decision_entry_price`, `market_yes_price`, `edge`,
  `dec_yes_spread`, `dec_yes_depth_ask_5c`
- HeadA private model/telemetry:
  `p_cal_no_city`, `p_cal_city_diag`, `pcal_v2_*`,
  `source_aware_v3*`, `integrated_tail_shadow_score_v2`
- live observation tags:
  `live_obs_current_native`, `live_obs_running_native`,
  `live_day_regime`, `live_intraday_state`, `live_metar_regime_score`

Sample row:

| field | value |
|---|---|
| city / date | Shanghai / 2026-07-07 |
| side / bracket | BUY_YES / 34 |
| forecast_max_native | 32.7 |
| forecast_to_bracket_low_native | 1.3 |
| bias_p90_asof | 4.1 |
| hot_tail_pct_asof | 0.672999 |
| ask | 0.12 |
| model_p_yes / edge | 0.3899 / 0.2699 |

## Regime-Route Specific Feature Shape

Regime-route captured 52 columns. The strategy-specific surface is dominated by:

- weather regime state:
  `day_regime`, `intraday_state`, `moisture_cloud_regime`,
  `wind_regime`, `running_max_state`, `city_family`
- temperature path state:
  `current_native`, `running_native`
- route expression and price:
  `expression`, `current_bracket`, `d2_no_bracket`, `no_ask`,
  `no_ask_size`, `ask_notional`
- route sizing/weighting:
  `route_multiplier`, `price_multiplier`, `weather_multiplier`,
  `day_multiplier`, `soft_balanced_multiplier`, `day_risk`

Sample row:

| field | value |
|---|---|
| city / date | Dallas / 2026-07-03 |
| side / bracket | BUY_NO / 98-99 |
| forecast_max_native | 95.1 |
| day_regime | day_forecast_capped |
| intraday_state | active_warming |
| route_multiplier | 0.45 |
| soft_balanced_multiplier | 0.2306475 |
| no_ask | 0.56 |

## Verdict

Yes, they use meaningfully different strategy features.

The right abstraction is not “one universal feature vector for every strategy.”
The shared layer should provide common mechanism primitives and PIT references:

- `city/date/as_of` identity and provenance
- forecast max / peak clock / source metadata
- thermal path and regime labels
- bracket/market geometry
- source/station bias references where relevant
- feature-store refs for replay and audit

Then each strategy keeps its private head:

- forecast-tail keeps station/source bias, pcal/source-aware, low-price YES
  selector, and tail-specific bracket distance.
- regime-route keeps route expression, day/intraday/weather multipliers, NO
  ask/liquidity, and route policy.

The migration implication is: use `weather_feature_layer` as the shared
mechanism substrate and PIT capture spine, not as a forced single feature vector.
Decision-input rewiring must stay per-strategy with replay parity.
