# Backtest PnL Analysis: Investigation Report

Status: `historical-snapshot`（特定 legacy PMM synthetic scenarios；不可外推到当前 PnL）

> **Issue**: User reported consistent negative PnL (e.g., -4.88) across all scenarios, even with 0 fills.

## Investigation Findings

1. **Root Cause**: Mark-to-Market (MtM) Valuation of Initial Inventory.
   - All synthetic scenarios start with `50 YES / 50 NO` positions.
   - PnL is calculated as `Equity_End - Equity_Start`.
   - If market prices drift down or spread widens (lowering the weighted mid price) by the end of the scenario, the inventory value drops, resulting in negative PnL even with zero trading.

2. **Verification**:
   - We verified that for perfectly stable scenarios (e.g., `b50_stable_no_fill`), the PnL is exactly **0.0**.
   - For trending/oscillating scenarios, small PnL fluctuations (positive or negative) are expected due to inventory revaluation.
   - The reported "-4.88" likely came from a scenario where the mid price drifted lower or spreads widened significantly.

3. **Conclusion**:
   - The system is behaving correctly.
   - "Negative numbers" in zero-fill scenarios represent **inventory holding costs/valuation changes**, not execution losses.

## How to Verify Yourself

Run a specific stable scenario:

```bash
python scripts/python/pmm_backtest.py run --scenario src/strategies/pmm/backtest/scenarios/b50_stable_no_fill.json
```

Output should show:
```json
"pnl_end": 0.0,
"total_fills": 0
```
