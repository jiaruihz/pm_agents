# Backtest Analysis: Why Trades Are Zero (0 Fills)

> **Issue**: Even in "fillable" scenarios like `b50_oscillating_fill`, the backtest reports 0 fills.

## Root Cause: Parameter Mismatch ("The Stubborn Market Maker")

The strategy parameters are set too conservatively compared to the scenario's market conditions, and the strategy logic actively prevents fills by moving quotes away from the price.

### 1. The Numbers
- **Market Spread** (in scenario): `0.020` (e.g., Bid `0.490` / Ask `0.510`)
- **Strategy Spread** (configured): `0.025` (e.g., Bid `0.4875` / Ask `0.5125`)

### 2. The Mechanism
1.  **Always Outside**: Because `Strategy Spread > Market Spread`, your quotes are always placed *behind* the best Best/Ask. You are not at the front of the queue.
2.  **Moving Goalposts**: The strategy recalculates quotes **every tick** based on the *current* Mid Price.
    - If the market moves UP, the Mid moves UP.
    - The strategy immediately moves its Buy order UP, keeping it exactly `0.0125` away from the Mid.
    - The Market Best Bid (which dictates the price move) is `0.0100` away from the Mid.
    - **Result**: You always maintain a `0.0025` gap behind the market. You are effectively "running away" from the price as it moves.
3.  **No Takers**: The backtest scenarios lack explicit `trade_flow` data (takers hitting the book).
    - `PaperBroker` relies on the Market BBO *crossing* your limit order to generate a fill.
    - Since you run away from the BBO perfectly in sync, the BBO never catches you.

## Conclusion / Strategy Gap

This is **not a code bug**, but a **strategy/configuration defect**. The strategy is correctly doing what it is told: "Maintain a 2.5% spread around the mid."

To get fills, the strategy must either:
1.  **Quote Tighter**: Set `base_spread` (e.g., `0.015`) to be inside the market spread.
2.  **Be Sticker**: Don't update quotes every single tick; let the market move into your resting orders.
3.  **Use Aggressive Logic**: Detect when spread is tight and quote AT the BBO (Join), not fixed spread.

## How to Fix (Without Code Changes)
To verify this, you would need to adjust the **Scenario Configuration** (data, not code):
- Change `strategy_overrides.base_spread` in `b50_oscillating_fill.json` from `0.025` to `0.015`.
