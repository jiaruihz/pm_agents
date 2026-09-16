# PM-Arb Bot

Polymarket atomic-arbitrage style bot scaffold.

## What It Does

- Watches YES/NO top-of-book prices.
- Detects two opportunities:
  - `merge_arb`: `ask_yes + ask_no + fee_buffer < 1.0`
  - `split_arb`: `bid_yes + bid_no > 1.0 + fee_buffer`
- Applies gas guard and minimum expected profit filters.
- Executes actions (dry-run by default).
- Performs periodic merge to recycle capital.

## Folder Structure

- `src/strategies/arb/config.py`
- `src/strategies/arb/main.py`
- `src/strategies/arb/arb_engine.py`
- `src/strategies/arb/execution.py`
- `src/strategies/arb/market_data.py`
- `src/strategies/arb/utils.py`

## Reused Components

- `pmm.market_ws.MarketWsFeed` for WS orderbook streaming and local cache.
- `pmm.http_client.ToolServiceClient` for REST calls.

## Important Execution Boundary

Current tool-service API does **not** provide strict atomic pair FOK across YES+NO legs.

- If `PM_ARB_STRICT_FOK_REQUIRED=1` and `PM_ARB_ALLOW_DEGRADED_EXECUTION=0`, live execution is blocked by design.
- If degraded mode is enabled, the bot sends two sequential limit orders (risk: legging).

This is intentional safety behavior.

## Quick Start

```bash
export PM_ARB_DRY_RUN=1
export PM_ARB_PAIRS_JSON='[
  {
    "name":"demo_pair",
    "yes_token_id":"<YES_TOKEN_ID>",
    "no_token_id":"<NO_TOKEN_ID>",
    "condition_id":"<CONDITION_ID>",
    "partition":[1,2]
  }
]'
python3 -m src.strategies.arb.main
```

## Key Config

- `PM_ARB_MARKET_DATA_SOURCE=ws|rest`
  - `mock` 也支持：用于在网络不可用时做逻辑仿真（不依赖真实盘口）
- `PM_ARB_TICK_INTERVAL_SEC=1.0`
- `PM_ARB_FEE_BUFFER=0.003`
- `PM_ARB_GAS_ESTIMATE_USDC=0.05`
- `PM_ARB_GAS_MULTIPLIER_GUARD=2.0`
- `PM_ARB_MIN_EXPECTED_PROFIT_USDC=0.20`
- `PM_ARB_MAX_NOTIONAL_USDC_PER_TRADE=100`
- `PM_ARB_STRICT_FOK_REQUIRED=1`
- `PM_ARB_ALLOW_DEGRADED_EXECUTION=0`
- `PM_ARB_AUTO_MERGE_EVERY_TICKS=30`
- `PM_ARB_AUTO_MERGE_MIN_SHARES=1.0`

## Mock Simulation

```bash
export PM_ARB_MARKET_DATA_SOURCE=mock
export PM_ARB_MOCK_SCENARIO=toggle   # toggle|merge_arb|split_arb|neutral
export PM_ARB_MAX_TICKS=5
export PM_ARB_DRY_RUN=1
python3 -m src.strategies.arb.main
```
