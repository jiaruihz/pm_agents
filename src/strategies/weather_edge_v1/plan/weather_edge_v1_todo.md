# weather_edge_v1 Historical Status And Roadmap

Last updated: 2026-05-12

This file is a frozen historical planning snapshot. It is not a current
production runbook; current topology and physical paths come only from
`src/strategies/runtime/production.yaml` and the production manifest.

## Strategy Name

The active strategy name is `weather_edge_v1`.

Older `weather_theta_no_v1` references are historical. They describe the earlier NO/theta framing and should not be used as the current strategy name. The current strategy is a weather probability edge strategy:

- `weather-predict` computes weather bracket probabilities and paper decisions.
- `pm_agent` imports signals, builds trade plans, records paper orders, and owns gated live execution.
- Paper and live must share the same trade plan and risk gates.

## Current Production State

### Running Remotely

N100 host:

```text
jiarui@192.168.0.200
```

Running user timers:

- `weather-predict-snapshot.timer`: runs `paper_snapshot.py` every 30 minutes.
- `weather-predict-daily-pipeline.timer`: daily PM settlement, price history, and GFS cache refresh.

Current remote production mode:

- paper snapshot: running
- paper order generation: running
- live order placement: not scheduled, not enabled

### Local Project Boundary

`weather-predict` owns:

- snapshot collection
- weather probability model output
- active city/airport/unit mapping
- historical weather and forecast cache
- paper decision generation
- settlement/replay research reports

`pm_agent` owns:

- signal import
- trade plan generation
- paper/live execution abstraction
- CLOB credentials and approvals
- risk gates and kill switches
- future live reconciliation and reporting

## Data Status

### Historical Weather Data

The 15-city historical supplement has been promoted in `weather-predict`:

- roughly 736 local days per city
- GFS forecast cache aligned to the same range
- observation source currently labeled `obs_source_v1_iem_proxy`

Important caveat:

- The promoted `wu_obs` files are IEM-derived WU proxy data, not fully verified raw Wunderground browser data.
- This is acceptable for the current paper version, but final strategy validation still needs true WU or Polymarket final settlement cross-checks.

### City Exclusions

Seoul data exists but should stay excluded from strategy PnL and live rollout unless revalidated.

Reason from prior calibration notes:

- weak historical calibration
- high RMSE / Brier relative to other cities

The current city/airport source of truth is `/home/rui/projects/weather-predict/pm_edge_compare.py::CITIES` and
`/home/rui/projects/weather-predict/docs/airport-selection-current.md`. Older `pm_agent`
manual airport research has been archived under `archive/manual_airport_research/`
and should not be used to override production mapping without a fresh calibration
and settlement validation run.

### Paper Order Data

Paper data has started accumulating from live snapshots.

Known recent state:

- 2026-05-10 generated 44 paper orders.
- Some Asian markets were already time-settled on 2026-05-10 UTC and could be roughly evaluated from IEM proxy observations.
- Formal Polymarket settlement cache may lag until the next daily pipeline run.

Do not treat same-day proxy results as final settled PnL.

## Execution Chain

Paper-only chain:

```bash
scripts/ops/weather_signal_importer.py \
  runtime/weather_edge_v1/paper_decisions.jsonl

scripts/ops/weather_trade_planner.py \
  --max-order-notional 1 \
  --min-edge 0.10 \
  --accepted-only

scripts/ops/weather_order_executor.py
```

Live smoke-test chain:

```bash
scripts/ops/weather_trade_planner.py \
  --max-order-notional 1 \
  --min-edge 0.10 \
  --accepted-only \
  --enable-live

scripts/ops/weather_order_executor.py \
  --live \
  --confirm-live \
  --cancel-after
```

Live has two explicit gates:

- planner must mark accepted plans with `live_enabled=true`
- executor must run with `--live --confirm-live`

`--cancel-after` is mandatory for the first tiny smoke test.

## What Is Done

- Renamed current strategy surface to `weather_edge_v1`.
- Archived old theta/no framing.
- Added weather-predict integration config.
- Added signal importer.
- Added trade planner.
- Added paper executor.
- Added gated live executor.
- Added core execution pipeline tests.
- Added docs for weather execution architecture.
- Promoted historical weather supplement in `weather-predict`.
- Started remote paper snapshot collection on N100.
- Centralized runtime entry/exit, sizing, default params, and weather bucket distance rules in `src/strategies/weather_edge_v1/core.py`.
- Updated both PMM and unified-engine weather adapters to call the shared core instead of carrying separate strategy logic.

## What Is Not Done

- No weather-triggered live order has been smoke-tested yet.
- No live order service is scheduled on N100.
- No daily `pm_agent` report yet compares signal, paper, live, and settlement in one place.
- Settlement and replay reports still mostly live in `weather-predict`.
- True Wunderground browser-based observation validation remains a later verification step.
- PMM's reusable tick/execution/backtest runtime still lives under `src/strategies/pmm/`; it should move to a neutral `src/platform` runtime namespace in a separate migration.
- Some weather research tools still read archived manual airport config; production code should either migrate those config files back under `config/` or mark those tools research-only.
- Quote strategy protocol types now live in `src/platform/quote_runtime/strategy_base.py`; the old PMM path is a compatibility shim.
- Safety/risk primitives now live in `src/platform/quote_runtime/risk/`; old PMM risk paths are compatibility shims.
- Broker and order diff primitives now live in `src/platform/quote_runtime/execution/`; old PMM execution paths are compatibility shims.

## Architecture Cleanup Plan

### P0: Keep Weather Rules Single-Sourced

- Treat `src/strategies/weather_edge_v1/core.py` as the only implementation point for strategy defaults, entry, exit, sizing, and weather bucket distance logic.
- Keep `pmm_adapter.py` and `tools/unified_strategy.py` as adapter-only layers.
- Add regression tests when changing `weather_entry_max_price`, `weather_take_profit_abs`, forecast entry/exit, or open-order exposure behavior.

### P1: Move Runtime Infra Out Of PMM

- Create a neutral runtime namespace under `src/platform/` for quote/tick execution concepts.
- Move `StrategyQuoteInput`, `QuoteTarget`, broker interfaces, order manager, risk guards, recorder, and replay runner behind compatibility shims. `StrategyQuoteInput`, `QuoteTarget`, `SafetyGuard`, `CircuitBreaker`, `BrokerInterface`, `PaperBroker`, `LiveBroker`, and `OrderManager` have been moved first.
- Keep `src/strategies/pmm/variants/` for PMM market-making strategies only.

### P2: Consolidate Weather Market Data Tools

- Extract shared orderbook snapshot utilities from `market_query_tool.py`, `weather_edge_market_data.py`, and `edge_orderbook_source.py`.
- Keep both real-time CLOB fetch and local JSONL/GZ replay use cases.
- Remove or convert old modules to shims only after script entry points and tests use the shared implementation.

### P3: Archive Old Research Artifacts Carefully

- Move non-standard March case logs, old prompt reviews, and postmortems into archive after updating README references.
- Do not delete `weather_edge_v1_todo.md` until it is replaced by a maintained roadmap or issue tracker.

## Near-Term Plan

### P0: Keep Paper Running

Let the N100 paper workflow run for several more days.

Target before changing filters:

- 150-300 total paper orders
- 80-150 formally settled orders
- at least 3-5 complete trading days

Daily checks:

- timer health
- new snapshot count
- new paper orders
- settled order count
- missing settlement cache count
- paper PnL and ROI

### P1: Build Daily Analysis

Produce a compact daily analysis that answers:

- How many orders were generated?
- How many are formally settled?
- What is total cost, PnL, ROI, and max drawdown?
- Which cities contributed most?
- YES vs NO performance
- GFS vs ECMWF performance
- edge bucket performance
- time-window performance
- unresolved or missing data issues

### P2: Validate Edge Quality

Do not optimize from one or two days. Once enough orders settle, evaluate:

- edge threshold: 10%, 15%, 20%, 30%
- side filter: YES, NO, both
- city filter
- model filter
- time-to-settle filter
- duplicate bracket and same-city correlation risk

### P3: Validate Price Realism

Compare:

- snapshot price
- last trade
- best ask
- executable order size at best ask
- simulated paper fill price

The goal is to know whether paper PnL is optimistic because of stale or non-executable prices.

### P4: Live Smoke Test

Only after paper data collection is stable:

- select one tiny order, no more than 1 USD notional
- submit through the live executor
- record live response
- cancel immediately
- verify live ledger and cancellation behavior

This proves the execution chain, not strategy profitability.

### P5: Paper/Live Parallel Rollout

Only after the smoke test:

- keep paper as source of truth for research
- run tiny live orders with strict caps
- compare paper and live fills daily
- stop immediately on reconciliation error

Suggested initial caps:

- max order notional: 1 USD
- max daily notional: 5-10 USD
- max daily loss: fixed small amount
- allowlisted cities and sides only

## Research Backlog

### Signal Research

- Does the paper edge survive formal settlement?
- Is edge concentrated in BUY_NO?
- Are certain cities consistently negative?
- Are `gfs` and `ecmwf` complementary or redundant?
- Does the model overbet low-probability YES tails?

### Probability Calibration

- Build realized win-rate curves by model probability bucket.
- Build realized win-rate curves by `model_prob - market_price` bucket.
- Recalibrate probability before increasing size.

### Data Source Research

- Cross-check IEM proxy observations against true WU where possible.
- Cross-check both against Polymarket final resolution.
- Document any station mismatch, timezone mismatch, or rounding rule mismatch.

### Execution Research

- Estimate slippage from paper price to executable best ask.
- Measure orderbook depth at the intended size.
- Detect stale markets and stale snapshots.
- Decide whether market orders are ever acceptable; default remains limit-only.

## Deprecated Or Historical Material

The following should be treated as history, not current operating instructions:

- `docs/archive/WEATHER_THETA_NO_PROGRESS.md`
- old case files under `src/strategies/weather_edge_v1/plan/cases/`
- old notes that describe this as pure theta/no carry
- old TODO items saying WU CSV is the immediate blocker
- old instructions saying "do not do live trading" without distinguishing live smoke tests from scheduled live rollout

Historical files can remain for auditability, but current operating decisions should come from:

- `src/strategies/weather_edge_v1/README.md`
- `docs/WEATHER_EXECUTION_ARCHITECTURE.md`
- this file
