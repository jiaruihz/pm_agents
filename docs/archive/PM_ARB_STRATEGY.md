# PM-Arb Bot Strategy and Monitoring Guide

This document explains:

1. The PM-Arb strategy design (what it does and why it should work).
2. The meaning of the simulation command output fields.
3. What to monitor (which indicators matter in practice).

Code lives in `src/strategies/arb/`.

## Identifier Model (Event vs Market vs Token)

Polymarket has multiple layers of identifiers. PM-Arb trades at the **market/outcome** layer, not at the event layer.

| Layer | What it represents | Typical fields | Cardinality |
|---|---|---|---|
| Event (topic/group) | A thematic grouping of markets | `event.ticker`, `event.slug`, `event.title` | 1 event -> N markets |
| Market (tradable contract) | A specific question/contract | `market.id`, `market.slug`, `market.question`, `condition_id` | 1 market -> K outcomes |
| Outcome (YES/NO/...) | One outcome inside a market | `outcomes[i]` | 1 outcome -> 1 token_id |
| Token (CLOB asset) | What you subscribe/orderbook/order on | `token_id` (CLOB) | 1 token_id -> 1 outcome |

In this repo:

- PM-Arb config uses a **pair** as the trading unit: one binary market expressed as `(yes_token_id, no_token_id, condition_id, partition)`.
- `pair.name` is just a human-readable label. It can be event-level or market-level, but we recommend using a market-specific label to avoid collisions when one event has multiple markets.

## 1) Strategy Design (Core Idea)

Polymarket binary markets (YES/NO) are built on CTF collateral, so the paired claims satisfy a no-arb identity:

- In an ideal market: `Price(YES) + Price(NO) ~= 1.0` (ignoring fees and execution frictions)

PM-Arb monitors top-of-book prices for a configured YES/NO pair and looks for two types of opportunities.

### Merge Arb (Buy Both, Then Merge)

Trigger condition:

- `ask_yes + ask_no + fee_buffer < 1.0`

Interpretation:

- You can buy 1 YES share at `ask_yes` and 1 NO share at `ask_no`.
- Then you merge `(YES, NO)` back to ~1 USDC of collateral.
- If total cost plus buffers is below 1.0, the gap is edge.

Action sequence:

1. Place BUY on YES at `ask_yes`
2. Place BUY on NO at `ask_no`
3. Call `mergePositions` (tool service: `POST /ctf/merge`)

### Split Arb (Split First, Then Sell Both)

Trigger condition:

- `bid_yes + bid_no > 1.0 + fee_buffer`

Interpretation:

- If you can sell YES at `bid_yes` and NO at `bid_no`, total revenue exceeds 1.0.
- You can split 1 USDC collateral into 1 YES + 1 NO, then sell both.

Action sequence:

1. Call `splitPosition` (tool service: `POST /ctf/split`)
2. Place SELL on YES at `bid_yes`
3. Place SELL on NO at `bid_no`

### What Is `partition: [1,2]`?

`partition` is a CTF split/merge parameter for how to partition the outcome space.

- For binary YES/NO markets, `partition=[1,2]` is the standard way to represent the two complementary outcomes.
- PM-Arb passes this into `/ctf/split` and `/ctf/merge` along with `condition_id`.

### Decision and Sizing Logic (How It Chooses Trades)

PM-Arb uses:

- `edge_merge = 1.0 - (ask_yes + ask_no + fee_buffer)`
- `edge_split = (bid_yes + bid_no) - 1.0 - fee_buffer`
- `notional_merge = size * (ask_yes + ask_no)`
- `notional_split = size * 1.0` (collateral is 1 USDC per paired share)
- `expected_profit_usdc = max(0, edge) * notional - gas_estimate_usdc`

Trade size is capped by:

- Available top-of-book size on both legs (`min(yes_size, no_size)`)
- A notional limit (`PM_ARB_MAX_NOTIONAL_USDC_PER_TRADE`)

Risk guards:

- `PM_ARB_MIN_EXPECTED_PROFIT_USDC`
- `PM_ARB_GAS_MULTIPLIER_GUARD * PM_ARB_GAS_ESTIMATE_USDC`

Execution safety boundary:

- Current tool-service API does not provide strict atomic FOK for a two-leg order pair.
- If `PM_ARB_STRICT_FOK_REQUIRED=1` and `PM_ARB_ALLOW_DEGRADED_EXECUTION=0`, live execution is blocked.

## 2) How PM-Arb Relates to PMM

PMM and PM-Arb solve different problems and can run together:

- PMM (`src/strategies/pmm/`) provides continuous liquidity with inventory-aware quoting.
- PM-Arb (`src/strategies/arb/`) only acts when the YES/NO invariant is violated enough to cover frictions.

Recommended coordination (next step to implement):

- Share one WS orderbook cache between both processes (avoid duplicate sockets and inconsistent views).
- When PM-Arb triggers, temporarily pause PMM quoting for that pair (or cancel one side) to avoid self-interference.

## 3) Simulation Command Output Fields

When running PM-Arb you will see logs like:

- `[PM-ARB][tick=0] pairs=3 actions=1 top_pair=... top_action=... top_edge=... top_expected_profit=... top_size=...`

Meaning:

- `tick`: loop counter
- `pairs`: number of configured pairs evaluated this tick
- `actions`: number of actions actually attempted this tick
- `top_*`: the best candidate by `expected_profit` after sorting (useful when you configured multiple pairs)

Then each executed action prints an object:

- `pair`: the configured pair name
- `action`: `merge_arb` or `split_arb`
- `size`: chosen trade size (shares, approximated as USDC collateral amount in this scaffold)
- `expected_profit`: expected profit after `gas_estimate_usdc`

For `merge_arb` action objects:

- `buy_resp`: two leg BUY intents (dry-run payloads, or live order responses)
- `merge_resp`: merge intent (dry-run payload, or live tx receipt summary)

For `split_arb` action objects:

- `split_resp`: split intent (dry-run payload, or live tx receipt summary)
- `sell_resp`: two leg SELL intents (dry-run payloads, or live order responses)

If an action cannot be executed due to safety flags, you will see:

- `status: "blocked"` with `reason`

## 4) What to Monitor (Practical Checklist)

At minimum, you want to track per tick and per pair:

- `edge` and `expected_profit`
If you see lots of positive `edge` but few actions, your guards are too strict or market data is stale.
If you see lots of actions but profits are tiny, your `fee_buffer` or `gas_estimate_usdc` is too optimistic.
- `size` chosen
If size is always small, top-of-book is too thin or your notional cap is too low.
- Action type frequency
Merge-leaning or split-leaning can indicate systematic bias in your data source or fee buffer.
- Blocked/error rate
Frequent `blocked` means you are still in dry-run/safety mode (expected), or execution boundary isn’t met.
Frequent errors mean API connectivity issues or schema mismatches.

In live trading, add these (not yet fully implemented in this scaffold):

- Legging risk stats (when executing two legs sequentially)
- Filled vs placed ratios on each leg
- Time-to-finality for split/merge transactions
- Inventory drift: paired positions accumulating or failing to recycle

## 5) Mock Simulation (No Network)

PM-Arb supports a mock data source to validate logic end-to-end:

- `PM_ARB_MARKET_DATA_SOURCE=mock`
- `PM_ARB_MOCK_SCENARIO=toggle|merge_arb|split_arb|neutral`

Example:

```bash
PM_ARB_MARKET_DATA_SOURCE=mock \\
PM_ARB_MOCK_SCENARIO=toggle \\
PM_ARB_MAX_TICKS=6 \\
PM_ARB_TICK_INTERVAL_SEC=0.2 \\
PM_ARB_DRY_RUN=1 \\
PM_ARB_PAIRS_JSON='[{\"name\":\"pair\",\"yes_token_id\":\"YES\",\"no_token_id\":\"NO\",\"condition_id\":\"0x..\",\"partition\":[1,2]}]' \\
python3 -m src.strategies.arb.main
```
