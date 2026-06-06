# Weather Data Protocol And Collection Unification Plan

Last updated: 2026-06-03

## Conclusion

Unifying the data protocol is high value and should happen first. Unifying the
collection runtime is also worthwhile, but it should be staged. Today `pm_agent`
has the stronger lineage/execution/dashboard stack, while `weather-predict`
still owns the complete production weather market snapshot, paper research
ledger, city pool, weather cache, and settlement cache.

Recommended target:

```text
pm_agent owns canonical protocol, ingest, dashboard, live execution, CLOB fills.
weather-predict either:
  A) becomes a data/model library called by a pm_agent collector, or
  B) keeps running as a producer but emits canonical pm_agent-compatible files.
```

Option B is the safer near-term path. Option A is the longer-term cleanup once
the producer contract is stable.

## Current Coverage

### weather-predict production output

Source paths:

- `output/paper_snapshots/snapshot_*.json`
- `output/orderbook_snapshots/`
- `output/paper_trades/paper_orders.jsonl`
- `output/research/t24_paper_*.csv`
- `cache/pm_history/{City}_{date}.json`
- `cache/wu_obs/`
- `cache/iem_v2_*.csv`

Strengths:

- Full weather market universe from `FULL_CITY_CONFIGS`, including T1 and T2.
- Weather forecast/model fields: `forecast_source`, `model`, `model_prob`,
  `forecast_max_f`, METAR fields, WU/IEM-derived observation metadata.
- Rich per-token orderbook fields in snapshots:
  `yes_best_bid`, `yes_best_ask`, `yes_depth_*`, `no_best_bid`,
  `no_best_ask`, `no_depth_*`, token IDs, book status/archive path.
- Paper research ledger that intentionally covers all configured city pools,
  with `city_pool` and `eligible_for_paper_order` attached.
- Settlement truth via `pm_history`.

Gaps:

- Uses legacy names in some outputs: `event_date`, `model`, `model_prob`,
  `market_yes_price`, `side`.
- Paper ledger is not the same lineage model as live execution: no canonical
  `signal_id` / `plan_id` / `execution_id` unless generated later by ingest.
- Derived settlement CSV refresh is still manual in the current runbook.

### pm_agent production/local output

Source paths:

- `runtime/weather_edge_v1/signals/`
- `runtime/weather_edge_v1/plans/`
- `runtime/weather_edge_v1/paper/`
- `runtime/weather_edge_v1/live/`
- `runtime/weather_edge_v1/live_cycle/`
- local mirror `runtime/weather_edge_v1/market_data/`
- dashboard DB `runtime/weather.db`

Strengths:

- Canonical strategy lineage: `signal_id -> plan_id -> execution_id`.
- Strategy identity is explicit: `strategy_instance`, `execution_policy`,
  entry bands, sizing, quote constraints.
- Real live order records include CLOB placement context:
  `requested_price`, `posted_price`, `quote_*`, `exchange_response`, status.
- Parallel paper shadow exists for live plans, with the same strategy instance
  and execution parameters as the live order pipeline.
- Dashboard DB already normalizes paper/live/explore into signals, plans,
  orders, fills, settlements, fact tables, and aliases.
- `weather_edge_market_data.py` can discover Gamma events, backfill CLOB price
  history, and capture live orderbooks.

Gaps:

- Live signal builder currently consumes `weather-predict` snapshots rather
  than independently producing weather model probabilities.
- Standalone `weather_edge_market_data.py` city list is older and narrower than
  the current `weather-predict` city pool.
- Live signal JSONL still uses some non-canonical names:
  `profile` instead of `forecast_source`, `model_probability_yes` instead of
  `model_p_yes`, and `order_side='BUY'` in live/paper execution records.
- pm_agent paper shadow is a live-execution comparison artifact, not a full
  T1+T2 research paper ledger replacement.

## Protocol Unification Target

Introduce a producer-facing canonical event model. Every producer should write
one of these record types:

| Record type | Purpose | Primary owner |
|---|---|---|
| `weather_market_snapshot` | Full opportunity universe at a timestamp | producer |
| `weather_signal_candidate` | One market-side opportunity before strategy filtering | producer or derived builder |
| `weather_strategy_signal` | Executable signal for one strategy instance | pm_agent |
| `weather_trade_plan` | Strategy decision and sizing | pm_agent |
| `weather_order` | Paper/live order attempt | pm_agent or paper ledger producer |
| `weather_fill` | Simulated or real fill | dashboard ingest / CLOB sync |
| `weather_settlement` | Final token/bracket outcome | settlement producer |

Canonical field names should follow `WEATHER_SYSTEM_CONTRACT.md`:

- `target_date`, not `event_date`
- `model_version`, not `model`
- `model_p_yes`, not `model_prob` or `model_probability_yes`
- `market_price`, not `market_yes_price`
- `forecast_source`, not `profile`
- `signal_side` in `YES|NO`
- `order_side` in `BUY_YES|BUY_NO`
- `execution_policy` only on strategy/plan/order records, not raw market facts

## Collection Unification Target

Do not start by moving everything into one daemon. Start by making both
producers emit the same contract.

### Phase 0: Documentation and audit

Status: this document.

Actions:

- Keep `WEATHER_REPO_BOUNDARY.md` as the runtime ownership map.
- Keep `WEATHER_SYSTEM_CONTRACT.md` as the canonical field contract.
- Treat dashboard adapters as temporary compatibility layers, not permanent
  business logic.

### Phase 1: Canonicalize outputs in place

Scope:

- `weather-predict` continues producing snapshots, paper ledger, and settlement
  cache.
- `pm_agent` continues producing live signals/plans/orders/paper shadow.

Changes:

- Add canonical aliases to weather-predict snapshot and paper ledger outputs:
  `target_date`, `model_version`, `model_p_yes`, `market_price`,
  `signal_side`.
- Add canonical aliases to pm_agent live signal/order outputs:
  `forecast_source`, `model_p_yes`, `order_side=BUY_YES|BUY_NO`.
- Add `producer_system`, `producer_run_id`, `schema_version` to all producer
  JSONL/JSON outputs.
- Keep legacy fields for one iteration, but mark them deprecated in the
  contract.

Validation:

- Dashboard rebuild must ingest both legacy and canonical fields.
- Field audit should report zero missing canonical fields for new files.
- PnL/fact tables should be unchanged except for IDs/metadata.

### Phase 2: Create a canonical market snapshot builder in pm_agent

Goal:

`pm_agent` can build `weather_market_snapshot` records from a canonical input
API, while still using weather-predict model/cache functions as needed.

Implementation sketch:

- Move city pool import behind an adapter:
  `WeatherCityUniverseProvider`.
- Move weather model probability generation behind an adapter:
  `WeatherProbabilityProvider`.
- Move Polymarket event/orderbook capture behind an adapter:
  `PolymarketWeatherMarketProvider`.
- Write one canonical snapshot file:
  `runtime/weather_edge_v1/market_data/canonical_snapshots/YYYY-MM-DD/*.jsonl`.

Important: this phase should not change live execution. The live signal builder
should first be able to read either the old weather-predict snapshot or the new
canonical snapshot and produce identical signals for the same timestamp.

### Phase 3: Shadow-run pm_agent collection

Run both collectors:

```text
weather-predict snapshot -> current production source
pm_agent canonical snapshot -> shadow source
```

Compare per timestamp:

- city count
- market count
- token coverage
- model probability equality/tolerance
- entry price equality/tolerance
- top-of-book equality/tolerance
- missing event / missing token / failed book counts
- paper order candidate equality under the same policy

Cutover criteria:

- 7 calendar days of shadow runs.
- No unexplained T1 market omissions.
- No unexplained settlement/cache omissions.
- Paper candidate count and accepted set match within documented intentional
  differences.
- Dashboard can rebuild from canonical snapshots without legacy CSV inputs.

### Phase 4: Promote pm_agent collector or keep weather-predict as canonical producer

Two acceptable end states:

1. `pm_agent` collector becomes the N100 producer for market snapshots and paper
   research ledger. `weather-predict` becomes a library/data-cache dependency.
2. `weather-predict` remains the producer, but it emits the canonical protocol
   and is treated as a data service rather than an independent strategy system.

Prefer end state 1 only if the weather model/cache code has been cleanly
extracted or ported. Otherwise end state 2 still removes most maintenance pain
because adapters, field aliases, and duplicate paper semantics disappear.

## Paper Semantics After Unification

There should be two explicit paper concepts, not one overloaded file name:

| Concept | Current location | Future name | Meaning |
|---|---|---|---|
| Research paper ledger | `weather-predict/output/paper_trades/paper_orders.jsonl` | `weather_research_paper_orders` | Full city-pool paper strategy ledger, including T2 |
| Live paper shadow | `pm_agent/runtime/weather_edge_v1/paper/live_*_paper_orders.jsonl` | `weather_live_shadow_orders` | Parallel simulated order for live strategy plans |

Both can share the same canonical `weather_order` schema, but they must have
different `execution_mode` / `venue` / `producer_system` values.

Recommended tags:

```text
research paper: execution_mode=paper, venue=paper, state=paper_research
live shadow:    execution_mode=live_shadow, venue=paper, state=live
real live:      execution_mode=live, venue=polymarket_clob, state=live
```

If dashboard schema keeps `execution_mode IN ('snapshot_replay','paper','live')`,
then use `state` or `order_role` to distinguish live shadow until the schema is
expanded.

## Migration Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| City universe drift | pm_agent market-data helper currently has an older city list | One city universe provider, sourced from `city_pools.py` until ported |
| Forecast/model drift | pm_agent does not currently reproduce all weather-predict model probability logic | Keep weather-predict as probability provider until parity test passes |
| Settlement drift | dashboard PnL depends on `pm_history` token/bracket truth | Do not move settlement producer until cache parity is proven |
| Paper semantic confusion | research paper and live shadow answer different questions | Separate record role/state fields |
| Adapter permanence | field aliases can hide bad producer output forever | Require schema_version and deprecation date |
| Live cutover risk | collector omissions can suppress live opportunities | Shadow run before live signal builder uses canonical snapshots |

## Recommended Next PRs

1. Add a field audit command:
   `scripts/ops/weather_protocol_audit.py`

   It should scan latest snapshot/signal/plan/order files and report missing
   canonical fields, legacy field usage, and producer/schema versions.

2. Add canonical aliases in pm_agent live outputs:
   `forecast_source`, `model_p_yes`, `order_side=BUY_YES|BUY_NO`.

3. Add canonical aliases in weather-predict outputs:
   `target_date`, `model_version`, `model_p_yes`, `market_price`,
   `signal_side`, `schema_version`.

4. Create a canonical snapshot reader in `weather_snapshot_signal_builder.py`.
   It should accept both existing weather-predict snapshot JSON and future
   canonical JSONL.

5. Shadow-run a pm_agent canonical snapshot producer before changing production
   live input.
