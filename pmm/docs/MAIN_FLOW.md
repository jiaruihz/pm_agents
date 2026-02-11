# Main Flow

主循环的时序图和关键路径。更详细的策略逻辑见 [STRATEGY_PLAYBOOK.md](STRATEGY_PLAYBOOK.md)，代码走读见 [CODE_IMPLEMENTATION.md](CODE_IMPLEMENTATION.md)。

## Tick 生命周期

```
┌─────────────────────────────────────────────────────┐
│ 1. Fetch Account State                              │
│    balance + open_orders + positions                 │
│    (live → REST API / paper → local memory)          │
├─────────────────────────────────────────────────────┤
│ 2. Fetch Market Data                                │
│    ws → local L2 cache (fallback REST if stale)      │
│    rest → GET /orderbook/{token_id}                  │
│    → compute mid, spread, book_tops                  │
├─────────────────────────────────────────────────────┤
│ 3. Circuit Breaker                                  │
│    |mid - MA(mid)| / MA(mid) ≥ threshold?            │
│    yes → cancel_all, halt / cooldown                 │
├─────────────────────────────────────────────────────┤
│ 4. Signal Stack (per token)                         │
│    inventory_signal → realized_vol → required_spread │
│    → OFI / momentum → side block decision            │
├─────────────────────────────────────────────────────┤
│ 5. Strategy Routing + Quote Generation              │
│    strategy_key → StrategyRegistry → strategy impl   │
│    single_level_v1: compute → anchor → quantize      │
├─────────────────────────────────────────────────────┤
│ 6. Execution                                        │
│    diff(open_orders, target) → cancel + place        │
├─────────────────────────────────────────────────────┤
│ 7. Auto Merge (periodic)                            │
│    min(yes_pos, no_pos) ≥ threshold → merge → USDC   │
├─────────────────────────────────────────────────────┤
│ 8. Metrics                                          │
│    strategy_key + quote_runtime + pnl/signals        │
│    → append to metrics.jsonl                         │
└─────────────────────────────────────────────────────┘
        │
        ▼ sleep(tick_interval_sec) → repeat
```

## 时序图

```mermaid
sequenceDiagram
    autonumber
    participant Main as main.py
    participant Engine as tick_loop
    participant Registry as StrategyRegistry
    participant Strat as single_level_v1
    participant API as ToolService / PaperBroker
    participant WS as MarketWsFeed
    participant Diff as OrderManager
    participant Log as MetricsLogger

    Main->>Engine: tick_loop(config)
    Engine->>Registry: get(strategy_key)
    Registry-->>Engine: strategy instance

    loop every tick
        par account snapshot
            Engine->>API: get_balance
            Engine->>API: get_orders
            Engine->>API: get_positions
        end

        alt ws mode
            Engine->>WS: read local book cache
            opt stale / missing
                Engine->>API: GET /orderbook (fallback)
            end
        else rest mode
            Engine->>API: GET /orderbook × N
        end

        Engine->>Engine: circuit breaker check
        alt triggered
            Engine->>API: cancel_all
            Engine->>Log: breaker event
        else normal
            Engine->>Engine: compute signals (inv/rv/ofi/momentum)
            Engine->>Strat: generate_quotes(token_ctx, signal_ctx)
            Strat-->>Engine: quote_targets[]
            loop each quote_target
                Engine->>Diff: diff(open_orders, target)
                Diff-->>Engine: cancel_ids, create?
                opt cancel
                    Engine->>API: cancel
                end
                opt create
                    Engine->>API: place
                end
            end
            opt merge due
                Engine->>API: merge
            end
            Engine->>Log: tick metrics(strategy_key, quote_runtime, pnl...)
        end
    end
```
