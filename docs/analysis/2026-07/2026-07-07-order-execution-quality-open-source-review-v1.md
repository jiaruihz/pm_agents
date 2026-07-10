# Order Execution Quality Open Source Review v1

Status: P0/P1 core implemented locally; runner activation pending
Date: 2026-07-07
Scope: weather order placement / execution-quality design plus shared lifecycle core. No runner has enabled the new policy and no live order was submitted or canceled by this work.

## Implementation Update (2026-07-11)

- Added configurable `order_lifecycle_policy=maker_until_data_update` and lifecycle lineage fields from signal through plan, paper order, and live order records.
- Reused `expires_at_utc = cancel_before_data_update_utc`; expiry cancels now preserve `cancel_reason=pre_data_update`.
- Shared executor blocks live maker placement when this policy lacks a valid future cancel deadline, including plans written directly by runners.
- Expiry cancel is now counted successful only when the exchange response explicitly confirms cancellation. Unconfirmed/failed cancels remain retryable on the next sweep.
- Existing runners are unchanged until they explicitly provide the lifecycle policy and deadline and invoke the existing `--cancel-expired` sweep.

## Verdict

这两个项目值得借鉴，但不能照搬成天气策略的 continuous market maker。

- `poly-maker` 的核心可复用点是 **target quotes -> live orders diff -> cancel/place**、post-only maker discipline、库存/敞口 risk regime、heartbeat / startup cancel / reconcile，而不是它的政治市场双边做市 alpha。
- NautilusTrader Polymarket adapter 的核心可复用点是 **真实订单语义**：post-only 只配 `GTC/GTD` limit、market BUY 是 quote notional、tick/precision 分层、fee/rebate 模型、unknown submit / deferred cancel / dust fill 处理。
- 天气版必须新增一条本项目特有规则：**maker 单不允许跨过天气数据更新时间继续挂着**。任何基于旧 forecast / METAR / source-event epoch 的 resting maker order，都要在下一次预期数据刷新前撤掉；新数据落地后重新生成信号、重取 book、再决定是否补挂。

结论动作：先做 execution-lifecycle shadow / paper 化，不直接改 live size。P0/P1 是补字段和 cancel telemetry；P2 才能启用真实 `cancel_before_data_update`。

## Sources Read

- `warproxxx/poly-maker` at `f35e79030dcc7f6afc0d933d9e7440a3e5b40efd`
  - `README.md`
  - `src/polymaker/execution/reconciler.py`
  - `src/polymaker/execution/gateway.py`
  - `src/polymaker/strategy/quoting.py`
  - `src/polymaker/strategy/regime.py`
  - `src/polymaker/risk/manager.py`
  - `src/polymaker/state/store.py`
  - `src/polymaker/userstream/parse.py`
- `nautechsystems/nautilus_trader` at `105456e4988b0988c821dd84f90c044a325e5754`
  - `docs/integrations/polymarket.md`
  - `nautilus_trader/adapters/polymarket/execution.py`
  - `nautilus_trader/adapters/polymarket/fee_model.py`
  - `nautilus_trader/adapters/polymarket/common/parsing.py`
  - `nautilus_trader/adapters/polymarket/order_fill_tracker.py`
  - `nautilus_trader/adapters/polymarket/providers.py`
- Local pm_agents execution surface:
  - `docs/WEATHER_ANALYSIS_CONTRACT.md`
  - `docs/WEATHER_EXECUTION_ARCHITECTURE.md`
  - `src/strategies/weather_edge_v1/tools/execution_pipeline.py`
  - `src/strategies/weather_edge_v1/tools/execution_policy.py`
  - `scripts/ops/weather_order_executor.py`
  - `tests/pmm_tests/test_weather_execution_pipeline.py`

No DB sync/rebuild was run because this is a code/design review, not a PnL / ROI / live_real performance claim.

## What Local Code Already Has

本项目已经有一部分正确基础：

- `weather_order_executor.py` live path defaults to maker-only and posts with `post_only=True`.
- Executor re-fetches live CLOB book before signing for maker/live-policy paths.
- Executor fetches venue tick size before live pricing when possible.
- `maker_queue_v2` already models bid/ask, spread, tick, required quote edge, and adverse-selection spread fraction.
- `execute_trade_plans()` can write `expires_at_utc`, cancel expired live orders, and perform `cancel_before_order_id` before replacement.
- `WEATHER_ANALYSIS_CONTRACT.md` already requires official Weather taker fee formula and separates maker fill probability / queue risk from taker fee baseline.
- Tests already cover duplicate live signal blocking, lifecycle replacement, cancel-before-replace failure, post-only diagnostics, notional ceilings, and expired-order cancellation.

The missing piece is not "can we place a post-only order"; it is a coherent order lifecycle for weather's data-update hazard.

## Lessons From poly-maker

### 1. Target quote reconciliation beats append-only placement

`poly-maker` does not think in one-shot orders. Strategy emits a desired quote set; reconciler keeps existing live orders if price/size are within tolerance, cancels stale ones, and places only missing targets.

Weather implication:

- Add a weather target-order layer keyed by `(strategy_instance, target_date, city, bracket, token_id, side, child_order_role, data_epoch_ref)`.
- A new plan should either keep, replace, or cancel an existing order. It should not blindly append another live order unless explicitly marked as a new lifecycle generation.
- Use tolerances like `reprice_ticks` and `resize_frac` so we do not churn queue position for tiny quote changes.

### 2. Regime machine should include data-update hazard

`poly-maker` pulls quotes during event / jump / stale-data regimes. For weather, the predictable "event" is the next forecast / METAR / official observation update.

Weather-specific regime proposal:

| Regime | Meaning | Live maker action |
|---|---|---|
| `QUIET_BETWEEN_UPDATES` | current data epoch is fresh, next update not imminent | allow maker quote if edge survives fresh book |
| `PRE_DATA_UPDATE_CANCEL` | within cancel buffer before expected data update | cancel resting maker orders; do not place new maker orders |
| `WAIT_NEW_DATA_EPOCH` | update should have happened but new source frame not seen yet | no maker placement; data freshness warning |
| `POST_UPDATE_REPRICE` | new epoch seen; book refreshed | recompute signal + quote; place only if edge still exists |
| `STALE_DATA_HALT` | source/event/snapshot stale beyond SLA | cancel or no-op; no live maker |

This should live at order-lifecycle / runner boundary, not as another downstream performance filter.

### 3. Risk caps should throttle, not only hard reject

`poly-maker` has hard caps and a soft headroom scale as exposure approaches caps. Weather tiny-live is not continuous inventory making, so do not copy its inventory skew wholesale. But the risk decision shape is useful:

- hard cap: per city-day, per strategy, per token, daily live notional;
- soft cap: shrink maker order size as correlated same-day exposure accumulates;
- reduce-only: sell/exit logic only if we later support exits; no new same-direction adds.

For exact temperature brackets, cap unit should be `city-target_date` first, because sibling brackets are highly correlated.

## Lessons From Nautilus Polymarket Adapter

### 1. Encode venue semantics explicitly

Important rules to centralize in pm_agents:

- `post_only` only makes sense for `LIMIT + GTC/GTD`; never with market `IOC/FOK`.
- Polymarket `IOC` maps to `FAK`; `FOK/FAK` are marketable semantics.
- Limit order quantity is shares. Market SELL quantity is shares. Market BUY quantity is quote notional in pUSD. A base-denominated market BUY is dangerous and should be denied.
- Batch submit only independent limit orders, max 15 per request.
- Modification is cancel + new order; there is no native replace.

Local code mostly uses limit orders today, but the semantics should be a shared helper rather than embedded across scripts.

### 2. Fee model should be a reusable primitive

Nautilus matches the same fee formula already documented locally:

```text
taker fee = shares * feeRate * price * (1 - price)
maker fee = 0
Weather maker rebate share = 25% of fee-equivalent, modeled only as upside/sensitivity
fee quantum = 0.00001
```

Weather action:

- Add a shared helper for `fee_per_share`, `fee_usd`, `maker_rebate_estimate`, and tick-aware executable cost.
- Every execution replay should record `liquidity_side = maker|taker`, `fee_rate_source = clob|category_default`, and `rebate_included = false` for baseline.

### 3. Tick and precision are dynamic

Nautilus treats tick-size changes as a book epoch transition. Weather currently fetches tick at live placement, which is good, but research/paper paths still often assume `0.01` or `0.001`.

Weather action:

- Record `venue_tick_size`, `price_precision`, `size_precision`, `order_type`, `time_in_force` on every plan/order.
- If a live tick differs from plan tick, reprice or reject; do not silently round into a different edge.
- Store the tick used for signed order construction in the live order record.

### 4. Unknown submit and dust fills need first-class handling

Nautilus keeps ambiguous submit outcomes as submitted pending reconciliation when the venue response is unknown, and tracks per-order fill dust to avoid false overfill/underfill errors.

Weather action:

- Treat transport timeout after signing/posting as `submit_unknown`, not immediate `rejected`, when an expected order hash/order id can be derived.
- Keep `pending_cancel` / deferred cancel support when cancel is requested during submit.
- Add dust threshold handling to fill recovery, separate from real partial fills.

## Required Weather Execution Additions

### A. Data-update-aware order lifetime

Add these fields to trade plans and live order records:

| Field | Purpose |
|---|---|
| `data_epoch_ref` | hash/id of forecast + observation/source-event frame used by the decision |
| `data_epoch_ts_utc` | timestamp of the data frame |
| `next_data_update_due_utc` | expected next relevant forecast/METAR/source update |
| `cancel_before_data_update_utc` | hard maker cancel deadline, usually due minus buffer |
| `cancel_buffer_sec` | configured buffer, e.g. 60-180 sec depending on source latency |
| `order_lifecycle_policy` | `maker_until_data_update`, `taker_now`, `paper_only`, etc. |
| `cancel_reason` | `pre_data_update`, `expired_order_ttl`, `replaced_by_new_epoch`, etc. |
| `post_update_reprice_required` | true when a new data epoch invalidates the old quote |

Implementation path:

1. In runner/plan generation, compute `next_data_update_due_utc` from the same source clock used for the signal, not from wall-clock guesses.
2. Set `expires_at_utc = cancel_before_data_update_utc` for existing `cancel_expired` compatibility.
3. Extend cancel records to preserve `cancel_reason=pre_data_update` instead of only `expired_order_ttl`.
4. Run the cancel sweep before every live placement cycle and on a small timer. If cancel is not confirmed, block replacement for the same opportunity.
5. After new data epoch lands, require a fresh signal + fresh book before placing again.

### B. Target-order reconciler

Current live path is closer to plan append + duplicate guards. Add a target-order reconciler:

```text
desired target orders
  vs
open live orders scoped to this strategy/token/opportunity
  -> keep within tolerance
  -> cancel stale / old epoch / wrong price / wrong size
  -> place missing
```

Start with shadow output only:

- `would_keep_order_id`
- `would_cancel_order_id`
- `would_place_new`
- `reprice_ticks_diff`
- `resize_frac_diff`
- `old_data_epoch_ref`
- `new_data_epoch_ref`

Then promote to paper/live after a week of telemetry.

### C. Execution-quality metrics

For every live or shadow maker policy, report:

- `fill_rate_before_update`
- `cancel_submitted_before_update`
- `cancel_confirmed_before_update`
- `order_alive_across_data_update`
- `fill_after_data_update`
- `post_update_quote_drift_cents`
- `edge_at_signal`, `edge_at_place`, `edge_after_update`
- `maker_fill_winner_rate` vs `maker_fill_loser_rate` to detect adverse selection
- `post_only_reject_rate`
- `unknown_submit_rate`
- `partial_fill_rate`
- `cancel_latency_ms`

The hard failure metric is `order_alive_across_data_update > 0` for maker orders unless explicitly waived for a strategy.

## What Not To Copy

- Do not copy continuous two-sided quoting into exact-temperature weather markets. Weather outcomes are event-clocked and source-update-sensitive; stale maker quotes get picked off around data updates.
- Do not count maker rebates in baseline edge. Keep them as upside sensitivity only.
- Do not use future max-bid touch as a maker fill. Existing local memory/reporting already flagged this as a common execution mistake.
- Do not rely on `GTD` alone for pre-update safety. Nautilus notes venue expiry can have buffer behavior; active cancel + confirmation is still required.
- Do not add broad guards as a substitute for root cause. The root cause here is stale order lifetime across known data epochs.

## Recommended Rollout

### P0: Contract and telemetry

- Add the lifecycle fields above to plan/order JSONL.
- Write `pre_data_update_cancel_policy_v1` as policy name.
- Shadow-record what would be canceled before updates; no live behavior change.

### P1: Cancel sweep

- Reuse `expires_at_utc` / `cancel_expired_live_orders()` but allow structured cancel reasons.
- Add tests for:
  - cancel before `cancel_before_data_update_utc`;
  - no replacement unless cancel confirmed;
  - new data epoch blocks old-order keep;
  - duplicate signal allowed only when replacing a confirmed-canceled older order.

### P2: Target reconciler

- Build a pure reconciler modeled after `poly-maker` but weather-scoped.
- Keep tolerances simple: `reprice_ticks`, `resize_frac`, `same_data_epoch_ref`.
- First output shadow diff rows; later wire to live.

### P3: Venue semantics helper

- Centralize fee/tick/order-type semantics.
- Add tests for post-only TIF, market BUY quote quantity denial, tick rounding, fee quantum, and maker rebate off-by-default.

### P4: Live readiness gate

Do not size up maker-first weather execution until:

- no maker order survives across data updates in shadow/live telemetry;
- cancel latency p95 is below the configured buffer;
- fill-after-update adverse-selection rate is understood;
- CLOB fill coverage gate remains passing;
- execution replay reports maker/taker separately with official fees.

## Immediate Recommendation

下一步优先做 **P0 + P1**：把 `cancel_before_data_update_utc` 接进已有 `expires_at_utc` / `cancel_expired` 机制，并把原因从普通 TTL 扩展成 `pre_data_update`。这是最小改动、最大风险降低；不需要先重写整个 executor，也不需要引入 Nautilus。
