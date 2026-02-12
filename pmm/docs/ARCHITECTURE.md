# PMM Architecture

> **Last Updated**: 2026-02-12 (Post-Refactoring)

## Overview

PMM (Polymarket Market Maker) is a layered market-making system with pluggable strategies, paper/live execution modes, and comprehensive backtesting infrastructure.

## Directory Structure

```
pmm/
├── config.py, main.py            # Entry point
├── tick_loop.py (735L)           # Orchestration engine
│
├── core/                         # Pure computation (no I/O)
│   ├── pricing.py                # Quote computation formulas
│   ├── signals.py                # Market signals (weighted_mid, OFI, realized_vol, momentum, etc.)
│   ├── sizing.py                 # Position sizing logic
│   ├── anchoring.py              # Quote anchoring to BBO
│   ├── strategy_base.py          # Strategy protocol + data structures
│   └── strategy_registry.py     # Strategy plugin registry
│
├── data/                         # I/O layer
│   ├── http_client.py            # REST API client
│   ├── market_ws.py              # WebSocket market feed + local L2 book
│   ├── orderbook.py              # Orderbook utilities
│   └── parsers.py                # API response parsers
│
├── execution/                    # Order execution
│   ├── broker_interface.py       # Abstract broker ABC
│   ├── live_broker.py            # Live trading (dry_run mode ready)
│   ├── paper_broker.py           # Simulated execution with local matching
│   └── order_manager.py          # Order diffing + deadband logic
│
├── risk/                         # Risk management
│   ├── safety_guard.py           # Pre-execution checks (whitelist, fat-finger, daily loss)
│   └── circuit_breaker.py        # Price jump detection
│
├── utils/                        # Shared utilities
│   ├── converters.py             # Type conversion (to_float, normalize_levels)
│   ├── quantize.py               # Price quantization to tick size
│   └── metrics.py                # Metrics logging
│
├── strategies/                   # Strategy plugins
│   ├── single_level_v1.py        # Single-level quoting
│   └── multi_level_v1.py         # Multi-level ladder quoting
│
└── backtest/                     # Backtesting infrastructure
    ├── replay_runner.py          # Scenario replay engine
    ├── recorder.py               # Live market data recorder
    ├── scenario_generator.py    # Synthetic scenario generation
    ├── scenario_validator.py    # Scenario validation
    └── plotter.py                # Result visualization
```

## Dependency Rules

```
┌─────────────────────────────────────────────┐
│ Layered Architecture (top → bottom)        │
├─────────────────────────────────────────────┤
│ tick_loop.py (engine)                       │
│   ↓                                         │
│ strategies/ (pluggable)                     │
│   ↓                                         │
│ core/ (pure computation)                    │
│   ↓                                         │
│ execution/ + risk/ (stateful components)    │
│   ↓                                         │
│ data/ (I/O)                                 │
│   ↓                                         │
│ utils/ (shared primitives)                  │
└─────────────────────────────────────────────┘

Rules:
- core/ MUST NOT import from execution/, risk/, data/, or strategies/
- data/ MUST NOT import from core/, execution/, or strategies/
- utils/ MUST NOT import from any other pmm package
```

## Tick Loop Flow

```
┌─────────────────────────────────────────────────────┐
│ 1. Fetch Account State                             │
│    balance + open_orders + positions                │
├─────────────────────────────────────────────────────┤
│ 2. Fetch Market Data                               │
│    ws → local L2 cache (fallback REST if stale)     │
├─────────────────────────────────────────────────────┤
│ 3. Circuit Breaker Check                           │
│    price jump > threshold? → cancel_all + halt      │
├─────────────────────────────────────────────────────┤
│ 4. Signal Computation (per token)                  │
│    inventory_signal → realized_vol → required_spread│
│    → OFI / momentum → side block decision           │
├─────────────────────────────────────────────────────┤
│ 5. Strategy Quote Generation                       │
│    strategy_key → StrategyRegistry → strategy impl  │
│    compute → anchor → quantize → quote_targets[]    │
├─────────────────────────────────────────────────────┤
│ 6. Execution                                        │
│    diff_multi(open_orders, targets)                 │
│    → cancel + place (1..N levels)                   │
├─────────────────────────────────────────────────────┤
│ 7. Auto Merge (periodic)                           │
│    min(yes_pos, no_pos) ≥ threshold → merge → USDC  │
├─────────────────────────────────────────────────────┤
│ 8. Metrics Logging                                 │
│    strategy_key + quote_runtime + pnl/signals       │
└─────────────────────────────────────────────────────┘
```

## Key Components

### Strategy System

Strategies implement the `MarketMakingStrategy` protocol:

```python
class MarketMakingStrategy(Protocol):
    def generate_quotes(
        self,
        token_ctx: StrategyQuoteInput,
        signal_ctx: Dict[str, Any],
    ) -> List[QuoteTarget]:
        ...
```

**Available Strategies:**
- `single_level_v1`: Single bid/ask per side
- `multi_level_v1`: N-level ladder with configurable spreads

### Execution Modes

Controlled by `PMM_EXEC_MODE` environment variable:

| Mode | Broker | Use Case |
|------|--------|----------|
| `paper` | `PaperBroker` | Backtesting, simulation with real market data |
| `live` | `LiveBroker` | Production trading (dry_run mode available) |

### Market Data Sources

Controlled by `PMM_MARKET_DATA_SOURCE`:

| Source | Latency | Notes |
|--------|---------|-------|
| `ws` | ~ms (push) | Maintains local L2 book, auto-reconnect, staleness detection |
| `rest` | ~tick interval | Simple polling, higher latency |

### Risk Management

**SafetyGuard** (pre-execution):
- Token whitelist enforcement
- Price bounds check [0.01, 0.99]
- Fat-finger protection (max notional per order)
- Daily loss circuit breaker

**CircuitBreaker** (market-level):
- Price jump detection (configurable threshold)
- Automatic cancel-all on trigger

## Backtesting

Full workflow:

```
1. Record live data:    pmm_backtest.py record-live
2. Convert to scenario: pmm_backtest.py convert-live
3. Run backtest:        pmm_backtest.py run <scenario>
4. Run all scenarios:   pmm_backtest.py run-all
5. Visualize:           pmm_backtest.py plot <scenario>
```

**Fill Models:**
- `conservative`: Requires price penetration (best_ask ≤ bid - ε)
- `optimistic`: Triggers on touch (best_ask ≤ bid + ε)

See [BACKTEST_SCENARIO_METHOD.md](BACKTEST_SCENARIO_METHOD.md) for details.

## Configuration

All configuration via environment variables. Key parameters:

```bash
# Core
PMM_TOKEN_IDS="token1,token2"
PMM_EXEC_MODE="paper"  # or "live"
PMM_MARKET_DATA_SOURCE="ws"  # or "rest"
PMM_STRATEGY_KEY="multi_level_v1"

# Strategy
PMM_BASE_SIZE=10.0
PMM_MAX_POSITION=500.0
PMM_TARGET_PROFIT_SPREAD=0.002

# Risk
PMM_CIRCUIT_BREAKER_THRESHOLD=0.05
PMM_MAX_DAILY_LOSS=50.0
```

Full list: see `config.py`

## Metrics

Every tick appends one JSON line to `pmm_logs/metrics.jsonl`:

```json
{
  "timestamp": 1234567890.123,
  "strategy_key": "multi_level_v1",
  "pnl": 12.34,
  "position": {"token1": 100.0},
  "signals": {"inventory_signal": -0.23, "realized_vol": 0.015},
  ...
}
```

Format spec: [METRICS_FORMAT.md](METRICS_FORMAT.md)

## Development Status

✅ **Completed:**
- Layered architecture (6 packages)
- Multi-level quoting
- Paper trading with BBO-join matching
- Live data recording + scenario replay
- WebSocket market feed with auto-reconnect
- OFI + momentum signals
- SafetyGuard + CircuitBreaker

🚧 **In Progress:**
- LiveBroker API integration (dry_run mode ready)

📋 **Roadmap:**
- User private WebSocket (order/fill updates)
- Queue position modeling
- Realized PnL tracking
- Prometheus metrics export

See [TODO_IMPROVEMENTS.md](TODO_IMPROVEMENTS.md) for full roadmap.
