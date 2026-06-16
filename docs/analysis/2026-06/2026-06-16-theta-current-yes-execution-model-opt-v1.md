# Theta Current YES Execution / Model Optimization v1

Status: research_only
Generated: 2026-06-16T16:54:31+00:00
Target metric: `theta_current_yes_execution_loss` = today's submitted current-YES orders where snapshot-ask limit became a resting CLOB order, and the cost of converting that intent into true taker execution.

## Data Snapshot

- Evidence layer: raw live order response copied from the live-control thread + public CLOB book check + local model artifacts. This is not settled PnL and not wallet cashflow.
- Row grain: one row = one submitted live order (`exchange_response.place.status=live`), not one fill.
- Local DB self-check fact_built_at_utc: `2026-06-16T15:50:05.971834+00:00`.
- fact_trades by class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`.
- fact_trades settlement: `[{'settlement_status': None, 'rows': 90}, {'settlement_status': 'settled', 'rows': 4310}]`.
- fact_signal_candidates coverage: `{'rows': 30919, 'eligible': 10685, 'paper_ordered': 4123, 'live_filled': 348}`.
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`.

## Trading Recommendation

Do not convert the current live branch into unconditional true-taker.

The correct patch direction is: refresh CLOB book immediately before placement and only cross if the fresh best ask is still close to the decision snapshot. For the current $5 test branch, the executable guard should be:

```text
initial signal: p_yes_win - snapshot_ask >= 0.05
execution guard: fresh_ask <= snapshot_ask + 0.02
depth guard: fresh ask has at least $5 available
```

Do not add a second hard `p_yes_win - fresh_ask >= 0.05` gate. The `0.02` cushion already bounds the worst-case edge decay from the initial 5-point edge to about 3 points. For tiny-live discovery, that is a better tradeoff than over-filtering away the few executable cases.

## Today's Orders

| city | posted limit | CLOB status | checked best ask | edge at posted | edge at checked ask | extra cost for same shares | shares lost if still $5 |
|---|---:|---|---:|---:|---:|---:|---:|
| Shanghai | 0.830 | live | 0.990 | +12.1% | -3.9% | $0.96 | 0.974 |
| Taipei | 0.794 | live | NA | +6.4% | NA | NA | NA |
| Chongqing | 0.820 | live | 0.998 | +5.9% | -11.9% | $1.09 | 1.088 |

Interpretation:

- These orders did not fail; they were accepted as live/resting orders.
- The runner used the snapshot ask as the limit price. If the real CLOB ask moved up before submission, the order becomes a bid below the ask instead of an immediate fill.
- True taker is only good if the fresh executable ask is still close to the snapshot ask. When the ask has jumped to 0.99+, buying just to get filled turns a positive modeled edge into negative edge.

## Why Passive Became Unfilled

1. The signal snapshot and order placement are not atomic.
2. The current runner writes a plan using `yes_current_ask` from the weather snapshot.
3. The executor is allowed to take, but it does not raise the limit to the fresh CLOB ask for this branch.
4. If market makers reprice the current winner toward 0.99 before the order lands, the old 0.79-0.83 limit rests.

## Model Optimization Directions

1. Split the problem into two models:
   - `p_yes_win`: probability the current bracket settles YES.
   - `p_fill_or_edge_survives`: probability the edge is still executable by the time the order reaches CLOB.

2. Add execution features:
   - snapshot age in seconds,
   - latest live book best ask/bid,
   - ask jump from snapshot ask to fresh ask,
   - top-of-book ask depth,
   - market near-certain flag (`fresh_ask >= 0.95` or no ask),
   - local-minute bucket inside 13-15.

3. Recalibrate high probabilities:
   - today's p values were high enough for the signal model, but execution at 0.99 would be negative edge.
   - calibration should be checked by city/hour/price bucket, especially `yes_ask >= 0.80`.

4. City pool refinement:
   - Keep source-aligned cities, but add a separate execution-quality gate by city.
   - Asian afternoon markets may reprice fast near the close; this should be measured before raising size.

5. Candidate decision change:
   - Replace `snapshot_best_ask_taker` with `fresh_book_guarded_taker`.
   - Recommended logic: if `fresh_ask <= snapshot_ask + 0.02` and there is enough top-of-book depth, cross; else do not chase.

## Expected Profit Convention

For a fixed-notional BUY_YES order, model EV should be reported in dollars as:

```text
expected_profit_usd = notional * (p_yes_win / execution_price - 1)
```

The 2-cent taker cushion is usually not fatal at $5 size. Example intuition:

- Shanghai: `p=0.9506`, $5 at `0.83` has model EV about `+$0.73`; at `0.85`, about `+$0.59`.
- Chongqing: `p=0.8792`, $5 at `0.82` has model EV about `+$0.36`; at `0.84`, about `+$0.23`.

So the cushion can cost roughly 10-15 cents per $5 order in expected value while materially improving fill probability. The live runner should record `edge_at_limit` and `expected_profit_usd_model` for every accepted plan.

## Current Model Context

- Model artifact: `theta_current_yes_live_logistic_v1`.
- Train rows: 1527.
- Numeric features: 24.
- City categories: 36.
- City pool count: 36; units: {'C': 26, 'F': 10}.

## Verdict

The weather/no-reheat model may still be right, but today's bottleneck is execution, not temperature logic. A taker upgrade must be conditional on a fresh executable ask. Unconditional taker would likely overpay exactly when the market has already repriced the bracket to near-certain.
