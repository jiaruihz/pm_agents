# Weather Unified Execution Module Refactor Plan

Status: foundation implemented; WCIR/non-live direct migration in progress
Updated: 2026-08-02, WCIR compatibility and no-dual-run cutover policy
Owner boundary: weather strategy execution only
Production behavior change: no, until the separately approved rollout phase
Depends on: `WEATHER_EXECUTION_ARCHITECTURE.md`, `WEATHER_SYSTEM_CONTRACT.md`

## 1. Objective

Move all weather taker/maker order construction and order lifecycle behavior
behind one shared execution module.

After the refactor:

```text
strategy runner
  -> TradeIntent (WCIR/strategy boundary; cannot grant live)
  -> WCIR compatibility adapter
  -> ExecutionIntent (existing execution boundary)
  -> versioned ExecutionProfile
  -> shared quote + lifecycle engine
  -> Polymarket venue adapter
  -> order/fill/replacement evidence
```

The strategy continues to decide:

- whether an opportunity is eligible;
- token, side, probability and total risk budget;
- total shares/notional and strategy price cap;
- source data epoch and next expected update;
- which `execution_profile` to use.

The shared execution module decides:

- taker/maker child split;
- quote construction and tick rounding;
- post-only semantics;
- GTC/GTD order semantics;
- rest, reprice, cancel and optional taker fallback;
- cancel/replace remaining quantity;
- durable order-attempt and replacement lineage.

This is a structural migration. It must not change signal eligibility, size,
price caps, fallback conditions or live strategy status unless a later,
separately reviewed profile version explicitly does so.

### Design invariants

1. A strategy signal describes one economic opinion. Taker/maker children are
   alternative execution routes for that opinion, not new signals.
2. A profile is a versioned composition of allocation, quote, lifecycle and
   venue policies. Strategy names do not belong in reusable policy code.
3. Fixed runtime profile rules are part of execution configuration identity.
   The same profile name with different cap policy, TTL or reprice rules is not
   the same execution configuration; realized per-opportunity cap values are
   plan evidence, not new configurations.
4. The pure engine computes decisions. The shared order runtime owns side
   effects, risk rechecks, action claims and journal writes.
5. The venue adapter owns exchange semantics. The engine never guesses tick,
   minimum size, fee, rebate, order type or final order state.
6. Raw evidence is append-only. New fields are additive and mixed old/new rows
   remain readable.
7. An ambiguous submit/cancel result is an uncertainty state, not permission
   to retry blindly.

## 2. Non-goals

Do not do any of the following as part of this work:

- do not restart, deploy or alter a live runner;
- do not submit, cancel or replace a real CLOB order;
- do not change strategy selection, city pools, probability models or sizing;
- do not tune maker fill rate, chase distance, TTL or taker fallback;
- do not rename or rewrite historical raw journals;
- do not delete old runner lifecycle code during migration;
- do not copy code directly into `pm_agents_prod`;
- do not reset, revert or overwrite unrelated changes already present in the
  working tree;
- do not mutate the meaning of an existing versioned profile name;
- do not add silent fallbacks when book, tick, order state or data epoch is
  missing.

The first target is exact behavioral equivalence. Execution optimization is a
separate follow-up after parity is proven.

This v1 module handles one token/side economic exposure per
`ExecutionIntent`, with one or more taker/maker execution children. It is not a
two-sided market-making inventory engine and not an atomic multi-token basket
engine.

Future true MM should sit above it:

```text
MM/inventory strategy
  -> desired bid/ask TargetOrderSet
  -> one ExecutionIntent per target order
  -> shared order runtime
```

Basket/hedge strategies likewise own basket atomicity and portfolio risk above
this module. Do not put inventory skew, basket atomicity or cross-market
hedging into the v1 order lifecycle state machine.

External design references are used at their proper layer:

- [`warproxxx/poly-maker`](https://github.com/warproxxx/poly-maker) informs
  post-only quoting, cancel/replace ownership, risk caps and the future
  inventory/target-order MM layer. Its two-sided inventory skew and
  split/merge loop are not copied into this single-exposure v1.
- [NautilusTrader's Polymarket adapter](https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/integrations/polymarket.md)
  informs venue truth: CLOB protocol/client version, dynamic tick epochs,
  order-type precision, fee schedules, TIF/post-only compatibility, unknown
  submit recovery and separate order/trade states. Those semantics belong in
  the venue adapter, not in strategy runners.

## 3. Current inventory

### Shared pieces already present

- `src/strategies/weather_edge_v1/execution/profiles.py`
  contains a partial profile registry.
- `src/strategies/weather_edge_v1/execution/lifecycle.py`
  contains the pre-data-update lifecycle fields.
- `src/strategies/weather_edge_v1/tools/execution_policy.py`
  contains `taker_top_ask_v1` and `maker_queue_v2` quote logic.
- `src/strategies/weather_edge_v1/tools/execution_pipeline.py`
  owns shared plan/order evidence fields.
- `src/strategies/weather_edge_v1/runtime/order_runtime.py`
  is the already-designated shared runner glue for plan serialization and
  invoking the executor. It must be extended, not bypassed by a second service.
- `scripts/ops/weather_order_executor.py`
  owns most GTC placement, post-only enforcement, cancel/replace and live
  journal behavior.
- `scripts/ops/weather_fast_source_execution.py`
  owns exact-share marketable GTC and short-lived post-only GTD primitives.

### Strategy-specific lifecycle implementations to migrate

- `scripts/ops/d1_yes_high_mid_shadow_v1.py`
- `scripts/ops/weather_current_yes_core_carry_tiny_live_v2.py`
- `scripts/ops/weather_current_yes_heat_death_tiny_live_v1.py`
- `scripts/ops/low_price_yes_lottery_tiny_live.py`
- `scripts/ops/weather_fast_source_prev_no_trial.py`
- `scripts/ops/weather_hko_official_tminus1_no_live.py`

The first four independently implement variations of maker quote, reprice,
cancel and fallback. Fast-source/HKO use a second shared venue path for GTD.

### Other single-token execution entry points

Taker-only and SELL runners also belong on the final shared entry path even
when they have no maker lifecycle. Representative current files include:

- `scripts/ops/regime_routed_no_tiny_live.py`
- `scripts/ops/late_window_residual_split_runner_v1.py`
- `scripts/ops/weather_theta_current_yes_tiny_live.py`
- `scripts/ops/tmax_distribution_edge_live_candidate_v1.py`
- `scripts/ops/low_price_yes_take_profit_exit_v1.py`

Phase 0 must produce the complete dynamic list. Do not conclude the refactor
after only maker-bearing runners have migrated.

### Current behavior families

1. `taker_now`
2. static maker until a data epoch/deadline
3. capped maker chase with no taker fallback
4. capped maker chase followed by conditional taker fallback
5. short-lived exact-share post-only GTD with immediate crossing retries
6. split taker + maker child composition

These are execution behaviors, not weather strategies. Profile names should
describe behavior. Strategy-specific names may remain as compatibility aliases
until their raw lineage is fully migrated.

## 4. Target package

Extend the existing package. Do not create a second execution package.

```text
src/strategies/weather_edge_v1/execution/
  __init__.py
  contracts.py
  profiles.py
  quote_engine.py
  lifecycle.py
  engine.py
  reconciliation.py
  venue/
    __init__.py
    polymarket.py

src/strategies/weather_edge_v1/runtime/
  order_runtime.py
  execution_journal.py
```

Dependency rule:

```text
scripts/ops and strategy runners
  -> runtime/order_runtime
       -> execution domain (contracts/profile/quote/lifecycle/reconcile)
       -> venue/risk/journal protocols through injection
```

The pure execution domain does not import runtime, scripts, strategy models or
network clients.

### `contracts.py`

Define immutable, JSON-serializable dataclasses or typed mappings:

```text
ExecutionIntent
MarketBook
VenueCapabilities
FeeSchedule
ExecutionLegProfile
ExecutionProfile
ChildOrderPlan
RestingOrderState
LifecycleContext
LifecycleDecision
ExecutionAction
ExecutionRunContext
```

Required `ExecutionIntent` fields:

```text
execution_schema_version
signal_id
opportunity_id
comparison_group_id
strategy_id
strategy_instance
config_id
execution_profile
resolved_execution_profile
execution_config_id
plan_dedupe_key
live_exposure_key
token_id
venue_side
outcome_side
signal_side
total_shares
created_at_utc
```

Optional typed execution inputs:

```text
model_token_probability
fair_value
strategy_price_floor
strategy_price_cap
required_edge
required_depth
book_max_age_sec
data_source
data_epoch_ref
data_epoch_ts_utc
next_data_update_due_utc
cancel_buffer_sec
leg_share_overrides
profile_parameters
```

`model_token_probability` and weather update fields are not globally required.
Each profile declares the fields it requires. A short GTD maker does not need
a weather update deadline; a data-epoch maker does.

`execution_config_id` is a deterministic hash of fixed execution behavior:

```text
resolved profile version
allocation policy and fixed weights
quote/lifecycle/venue policy versions
refresh/reprice/fallback/deadline rules
fixed spread/edge/cancel-buffer parameters
fee/tick model version
```

It excludes transient book values and per-opportunity realized values such as
probability, absolute price cap, shares and actual deadline. Those remain on
the plan/order as evidence. `config_id` remains the full strategy identity;
`execution_config_id` is the stable execution-only slice used for comparison.

`execution_profile` preserves the exact configured/historical name.
`resolved_execution_profile` records the behavior-oriented profile after
compatibility alias resolution. Reports may group by the resolved field but
must retain the original name for lineage.

`plan_dedupe_key` is stable across old/new implementation paths for the same
selected strategy configuration, opportunity and child role. It permits
intentional paper/shadow A/B profiles to have distinct plans.

`live_exposure_key` is profile/implementation independent for the same
economic exposure:

```text
strategy family or authorized instance scope
opportunity
token
venue side
outcome side
```

It prevents a live profile migration or runner restart from buying the same
exposure again. Explicit taker/maker children share one exposure root and one
aggregate risk budget; they are not unrelated live opportunities.

Optional strategy telemetry is carried in `metadata`. Metadata is serialized
but never consumed by allocation, quote, lifecycle, risk or venue code. A
value that changes execution must be promoted to a typed field or
`profile_parameters`.

Define typed `ExecutionConstraints` for price, edge, depth, freshness, size
and deadline constraints. This prevents future strategies from hiding
behavior in arbitrary metadata.

`ExecutionIntent` is mode-neutral and cannot enable live submission.
`execution_mode`, `dry_run`, `confirm_live`, pause state and authorization are
provided separately by runtime-owned `ExecutionRunContext`. Profiles and
strategy metadata cannot override them.

Required `ExecutionRunContext` fields:

```text
run_id
execution_mode                  # snapshot_replay | paper | live
run_purpose                     # replay | comparator | shadow | live_probe | production
dry_run
confirm_live
pause_state
authorization_ref
runtime_owner
code_commit
invoked_at_utc
```

`execution_mode=live` is valid only when `dry_run=false`, `confirm_live=true`,
the pause state permits submission and `authorization_ref` identifies the
approved deployment/run. `authorization_ref` may be null for non-live modes
but the key remains explicit. `shadow` is a run purpose, not a fourth canonical
execution mode; a shadow that writes simulated orders uses `paper`, while a
pure comparator performs no submit call. The shared runtime validates this
before reserving exposure or calling the venue. A pure plan/comparator path
does not need a run context because it cannot perform side effects.

During migration, a legacy plan may still serialize `live_enabled` for current
executor compatibility, but that value is derived only from
`ExecutionRunContext`; it is not accepted from the strategy intent.

Required `MarketBook` fields:

```text
token_id
status
fetched_at_utc
venue_timestamp_utc
book_epoch_ref
tick_size
tick_size_source
minimum_order_shares
bids
asks
```

`bids` and `asks` are ordered full-depth levels. Top-of-book alone is
insufficient for taker VWAP, fast-source allocation and fallback depth checks.
`book_epoch_ref` changes when the venue tick grid changes or the adapter drops
and reseeds its local book. A plan built against an older epoch is stale and
must be recomputed; old-grid prices must never be signed after a tick change.

Required `VenueCapabilities`/`FeeSchedule` evidence includes:

```text
venue
protocol_version
client_version
collateral_asset
supported_order_types
post_only_order_types
price_precision
size_precision
amount_precision_by_order_type
gtd_security_threshold_sec
capabilities_fetched_at_utc
fee_schedule_ref
fee_schedule_fetched_at_utc
fee_formula_id
taker_fee_parameters
maker_fee_parameters
maker_rebate_program
```

The Polymarket adapter obtains these from current instrument/market metadata
and the active client contract. The execution domain receives a normalized,
versioned snapshot; it does not hard-code a forever-current Weather fee rate
or assume maker rebates are guaranteed. Estimated maker rebate and realized
rebate are separate fields.

New price/quantity arithmetic uses `Decimal` created from strings or integer
tick units. Do not construct `Decimal` from binary floats. Backward-compatible
JSON fields may remain numeric, but serializers also preserve the exact
normalized price, quantity, tick and rounding decision sent to the venue.

Internal side fields are unambiguous:

```text
venue_side   = BUY | SELL
outcome_side = YES | NO
signal_side  = strategy semantic label retained for lineage
```

Legacy `order_side` is serialized according to the resolved canonical
contract. Phase 0 must reconcile the current documentation/code disagreement
instead of letting each adapter interpret `order_side` differently.

Required `RestingOrderState` fields:

```text
order_id
client_order_id
expected_venue_order_id
root_order_id
source_order_id
plan_id
token_id
venue_side
outcome_side
requested_shares
matched_shares
remaining_shares
posted_price
status
created_at_utc
maker_only
execution_profile
execution_policy
order_lifecycle_policy
reprice_count
```

`client_order_id` is the runtime's deterministic local action identity; it
does not imply that Polymarket offers a client-supplied idempotency key.
`expected_venue_order_id` is nullable until the signed order hash can be
derived or the venue responds.

### `profiles.py`

Keep existing profile names unchanged. Add behavior-oriented profiles only
with new versioned names:

```text
taker_now_v1
maker_static_until_data_update_v1
maker_chase_capped_v1
maker_chase_then_taker_if_ev_v1
maker_short_gtd_v1
split_taker_maker_chase_capped_no_fallback_v1
split_taker_maker_chase_capped_ev_fallback_v1
```

The profile schema must support:

```text
allocation_policy
legs[].role
legs[].quote_policy
legs[].lifecycle_policy
legs[].venue_policy
legs[].share_weight
legs[].maker_only
profile parameter requirements
required venue capabilities
```

Allocation policies:

```text
all_taker
all_maker
fixed_weight_split
explicit_leg_shares
depth_adaptive_taker_then_maker
```

Allocation returns exact child shares and a blocked remainder. It never rounds
a sub-minimum child upward and increases total exposure.

Quote/lifecycle/venue components own typed parameters:

```text
refresh_sec
max_reprices
price_cap_policy
deadline_policy
cancel_buffer_sec
fallback_policy
order_type
post_only
effective_lifetime_sec
immediate_crossing_retries
```

Profile validation fails before planning if the venue capability snapshot does
not support a required order type, post-only mode, expiration mode, tick or
minimum quantity.

Do not encode strategy probability models, city filters or signal gates inside
the profile.

Do not add an under-specified bundle such as `split_taker_maker_v1` whose name
does not resolve maker quote and lifecycle behavior. Existing historical names
remain compatibility aliases; raw history is not rewritten.

Profiles are immutable code-registered bundles. Runtime configuration may
supply only parameters explicitly declared by the profile schema. Structural
behavior changes require a new profile version; materially changing a fixed
TTL/reprice/fallback rule also requires a new version rather than silently
changing an old profile's meaning.

A compatibility alias is allowed only when old and resolved behavior are
fixture-identical. If not, register a preserved legacy profile definition
instead of forcing it onto a nearby new behavior.

### `quote_engine.py`

Pure functions only: no files, HTTP, environment variables or clocks hidden
inside functions.

Required behavior:

- taker quote at fresh top ask;
- marketable-limit quote from full depth when a profile requires exact-share
  sweep semantics, including expected VWAP, worst price and blocked remainder;
- maker join bid;
- maker improve bid by configurable ticks;
- maker one tick below ask;
- price cap composition;
- BUY/SELL tick rounding;
- no valid resting quote when maker would cross;
- minimum and maximum price validation;
- official fee/rebate estimate fields needed by fallback decisions.

The engine receives a fresh `MarketBook`; the venue adapter is responsible for
fetching it. It also receives a normalized `FeeSchedule`/capability snapshot
for fee-aware decisions and generic Decimal checks. Final
order-type-specific signable amount normalization remains venue-owned.

Every quote result records:

```text
book_fetched_at_utc
book_venue_timestamp_utc
book_age_sec
tick_size
tick_size_source
rounding_mode
fee_model_version
requested_price_exact
normalized_price_exact
```

During parity migration, preserve each old policy's cap rule exactly:

- D1: trigger-time midpoint cap;
- core-carry: minimum of trigger midpoint and model probability;
- heat-death: initial fresh ask cap;
- low-price: existing max-ask/posted-price cushion;
- fast-source: one tick below fresh ask;
- generic `maker_queue_v2`: model edge minus spread adverse-selection charge.

Do not replace these with one new formula during this refactor.

### `lifecycle.py`

Keep the existing absolute data-update lifecycle helper. Add a pure state
machine:

```text
REST
REPRICE_MAKER
REPOST_LOWER
CANCEL
TAKER_FALLBACK
TERMINAL
```

The state machine inputs are:

```text
profile
authoritative order state
fresh book
lifecycle context supplied by the strategy
current time
```

`LifecycleContext` contains generic facts such as `signal_valid`,
`token_unchanged`, current data epoch and deadline. The strategy computes
thesis validity. The engine does not import or re-evaluate a weather model.

The output is one `LifecycleDecision`; it does not place or cancel an order.

Required rules:

- a maker action never crosses the ask;
- a static profile never reprices;
- a capped chase never exceeds its cap or reprice limit;
- a changed data epoch cancels unless the versioned profile explicitly says
  otherwise;
- pre-data-update blackout emits no new maker;
- taker fallback is possible only for a profile that explicitly enables it;
- fallback requires valid thesis, allowed epoch, fresh book, price cap, depth
  and fee-adjusted edge;
- missing authoritative state blocks replacement;
- remaining shares below venue minimum blocks replacement;
- terminal orders are not acted on again;
- taker fallback waits for confirmed maker cancellation and authoritative
  remaining shares;
- open maker quantity and reserved replacement quantity are not both treated
  as available risk capacity.

### `reconciliation.py`

Centralize cancel/replace quantity and idempotency:

```text
remaining_shares = original_size - authoritative_size_matched
```

Never use a delayed local fill cache to decide replacement quantity.

Required behavior:

- cancel first;
- read authenticated order state after cancel;
- compute final remaining quantity;
- do not replace below the venue minimum;
- do not replace when authoritative state is unavailable;
- recognize fills that race with cancel;
- keep one root chain across every reprice/fallback;
- deduplicate the same lifecycle action after runner restart.

Define stable `lifecycle_action_id` from:

```text
root_order_id
source_order_id
authoritative order-state version
action
normalized target price
remaining shares
data epoch
book epoch
```

The execution journal claims this ID before a side effect. An ambiguous submit
is recorded as `submit_unknown`, together with the deterministic client order
identity and expected signed venue order hash when available, and reconciled by
exchange lookup before retry. Batch submission must not be blindly retried
when the venue has no idempotency key.

This preserves the fix documented in
`2026-07-13-heada-maker-partial-fill-overbuy-incident-v1.md`.

### `engine.py`

Expose two primary pure orchestration entry points:

```python
expand_execution_intent(
    intent,
    profile,
    book,
    venue_capabilities,
    fee_schedule,
    now,
) -> list[ChildOrderPlan]

evaluate_order_lifecycle(
    order_state,
    profile,
    book,
    venue_capabilities,
    fee_schedule,
    lifecycle_context,
    now,
) -> LifecycleDecision
```

Also expose serialization helpers that preserve all canonical lineage fields.

`engine.py` must not:

- read strategy journals;
- call Polymarket;
- read weather caches;
- determine whether a weather signal is valid;
- submit orders.

### `runtime/order_runtime.py`

Extend the existing shared runtime into the single application-level entry
point used by runners:

```python
submit_intent(intent, run_context) -> ExecutionRuntimeResult
manage_active_orders(
    owner,
    lifecycle_contexts,
    run_context,
    now,
) -> ExecutionRuntimeResult
```

Initial submission:

```text
validate execution mode, pause state and live authorization
  -> load profile
  -> fetch fresh venue capabilities/book
  -> expand intent
  -> aggregate risk check
  -> claim child plan-dedupe key and reserve live exposure
  -> revalidate venue capability/book epoch immediately before signing
  -> place
  -> append evidence
```

Lifecycle:

```text
load active owned order roots
  -> fetch authoritative order state
  -> evaluate lifecycle with runner-supplied generic context
  -> claim lifecycle_action_id
  -> cancel
  -> fetch final authoritative state
  -> replacement/fallback risk recheck
  -> revalidate venue capability/book epoch immediately before signing
  -> place exact remaining shares
  -> append evidence
```

The runtime owns side-effect ordering so runners cannot each reimplement it.
It accepts injected venue, risk and journal interfaces in tests. Existing
JSON/JSONL helpers and subprocess compatibility remain available during
migration.

Risk is rechecked:

- before every initial child;
- across all children of one intent;
- before every replacement;
- before taker fallback;
- including fills and still-open/reserved shares across the root chain.

Every active order root has one `lifecycle_owner`. Another runner, patrol or
expiry sweep may observe it but cannot place a replacement. Emergency/global
cancel remains a separate safety operation and never creates a child order.

### `runtime/execution_journal.py`

Define an append-only `ExecutionJournal` protocol and JSONL implementation.
The journal owns:

- child plan-dedupe claims and live-exposure reservations;
- lifecycle action claims;
- durable attempt-before-side-effect records;
- success/failure/unknown outcomes;
- root/source-order lookup;
- mixed legacy/new row reads.

The implementation is shared, but the physical journal remains under each
registered `strategy_instance` runtime directory unless canonical source
registration is explicitly migrated. Do not silently move active order
evidence into one new global file. Existing canonical discovery paths keep
working throughout direct migration and rollback retention.

The JSONL implementation enforces one writer/claim lock per runtime execution
owner. If it cannot acquire ownership, it records/returns a blocked result and
does not call the venue.

Do not claim exactly-once exchange execution from JSONL. The operational model
is at-least-once orchestration with deterministic local identity, reconciliation
of ambiguous results and no blind retry.

### `venue/polymarket.py`

Wrap existing execution callbacks rather than reimplementing signing.

Own:

- active CLOB protocol/client/collateral metadata;
- tick-size lookup;
- dynamic tick/book-epoch invalidation;
- minimum order shares;
- BUY/SELL rounding;
- order-type-specific price/size/amount precision;
- current fee-schedule lookup and fee/rebate estimate fields;
- GTC/GTD/FOK/FAK semantics used by this repository;
- post-only placement;
- GTD security-threshold handling;
- cancel and authenticated post-cancel order lookup;
- normalization of exchange response/status/order IDs.

Dependency direction:

```text
scripts/ops CLI and strategy runners
  -> src shared order runtime
  -> venue protocol / injected CLOB client
```

Code under `src/` must not import `scripts/ops/*`. During transition, scripts
may inject existing proven callbacks into the adapter. After contract tests
exist, move reusable venue semantics into `src/` and make both scripts call
them.

The final venue adapter must not branch on `strategy_id` or
strategy-specific `execution_policy` names. Current special cases in
`weather_order_executor.py` are migration sources, not the target design.

Normalized order state and trade/fill finality are separate. Venue trade
statuses such as `MATCHED`, `MINED`, `CONFIRMED`, `RETRYING` and `FAILED` must
not be collapsed into an order being safely replaceable. The adapter exposes
authoritative matched size plus the raw venue status used to derive each
normalized state.

## 5. Canonical action and lineage contract

Every child plan and order attempt must preserve:

```text
signal_id
opportunity_id
comparison_group_id
plan_id
source_plan_id
execution_id
strategy_id
strategy_instance
config_id
execution_profile
resolved_execution_profile
execution_config_id
execution_schema_version
execution_policy
order_lifecycle_policy
child_order_role
execution_action
lifecycle_action_id
plan_dedupe_key
live_exposure_key
lifecycle_owner
maker_only
client_order_id
expected_venue_order_id
source_order_id
root_order_id
cancel_before_order_id
requested_price
posted_price
requested_shares
remaining_shares
book_epoch_ref
tick_size
fee_schedule_ref
estimated_fee_usd
estimated_maker_rebate_usd
data_epoch_ref
data_epoch_ts_utc
cancel_before_data_update_utc
```

Rules:

- signal identity does not change when execution profile changes;
- taker and maker children share `comparison_group_id`;
- child identity distinguishes profile and role;
- every replacement has a new execution/order attempt identity;
- every replacement points to the prior order and stable root order;
- cancel-only actions never place a replacement;
- a maker-to-taker fallback is recorded as an explicit new action, not as a
  maker fill;
- historical rows without new fields remain readable.

### ID authority must be resolved before implementation

The repository currently contains mixed historical statements:

- `WEATHER_SYSTEM_CONTRACT.md` still says N100 generated and pm_agent merely
  passed through `plan_id/execution_id`;
- current Mac runners and canonical migration may generate deterministic IDs
  locally through `src/strategies/weather_edge_v1/ids.py`.

Phase 0 must document the current authoritative algorithm from executable code
and canonical migration tests, then synchronize the contract documents before
new ID helpers are written. The implementation agent must not invent a third
ID algorithm.

Current code suggests the following two-namespace resolution, which Phase 0
must verify with tests:

```text
raw/source plan_id
  = runner-owned operational identity, preserved as source_plan_id when needed

canonical plan_id
  = make_plan_id(run_id, signal_id, canonical order side,
                 execution policy/profile/child role)

raw execution_id
  = preserved when present

missing canonical execution_id
  = make_execution_id(run_id, canonical plan_id, venue, stable attempt key)
```

Do not force runner `plan-...` strings and canonical 64-hex plan IDs into one
namespace. The canonical side mapper converts internal
`venue_side/outcome_side` to `BUY_YES/BUY_NO/SELL_YES/SELL_NO`; the live venue
adapter receives only `BUY/SELL`.

For migration safety:

- preserve an existing raw `plan_id/execution_id` when present;
- generated canonical IDs continue to use the established shared helper;
- add stable plan-dedupe and live-exposure keys across legacy/new paths;
- offline fixture output uses a separate namespace and is never eligible for
  submission;
- live switchover checks the retained legacy journal and new journal for matching plan-dedupe key,
  live-exposure key, signal/token/side/role and active root.

## 6. Cross-strategy and data compatibility

### Strategy boundaries

All weather strategies adapt to the same intent contract, but they retain
ownership of their model and thesis:

| Strategy family | Strategy supplies | Shared execution owns |
|---|---|---|
| D1 | token, split size, trigger-mid cap, observation epoch | split, quote, capped/static lifecycle, cancel |
| core-carry | probability, split size, midpoint/model cap, epoch | fresh-book quote, chase, TTL/epoch cancel |
| heat-death | initial-ask cap, chase window, fallback eligibility context | chase, cancel, depth/price fallback execution |
| low-price | max ask, model edge, dynamic lifecycle parameters | maker/repost/fallback state machine |
| fast-source/HKO | source signal, desired shares, full-depth book constraint | depth allocation, exact-share GTD, crossing retry |
| future model | typed constraints and profile choice | unchanged shared order runtime |

No shared module imports a strategy runner. No strategy runner reaches into
private venue methods after migration.

Other execution shapes:

- single-token taker-only strategies use `taker_now_*` profiles and the same
  shared runtime;
- position exits use `venue_side=SELL` and keep position/thesis ownership in
  the exit strategy;
- existing all-YES/FOK baskets may reuse venue normalization per leg, but
  basket atomicity and basket risk remain in the basket orchestrator;
- true two-sided MM may reuse venue/runtime primitives, but target-order
  reconciliation and inventory skew remain in a future MM layer.

### Existing canonical layers

Impact by layer:

| Layer | Refactor effect |
|---|---|
| MarketData | unchanged; execution references a fresh book snapshot/capability record |
| Signal candidate/signal | unchanged IDs and denominator |
| Strategy config | add/confirm execution profile and execution-config identity |
| Plan | additive execution schema/config/dedupe/allocation fields |
| Order | additive root/action/owner/exact normalized request fields |
| Fill | unchanged venue facts; links to the exact execution attempt |
| Position | still derived from fills; never derived from submitted plans |
| Settlement/PnL | unchanged; still joined through canonical fills |

`fact_signal_candidates` must not gain duplicate rows when a strategy compares
execution profiles. Multiple plans reference the same signal/opportunity.

`fact_trades` remains fill-grain. New execution dimensions are projected from
plan/order lineage. An unfilled maker is not synthesized into `fact_trades`;
it remains in canonical plan/order facts and the opportunity-denominator
execution report.

### Mixed-schema migration

All canonical readers must accept:

- legacy rows with only `execution_policy`;
- rows with profile/lifecycle/child role;
- new rows with original/resolved profile, execution config, root and action
  identity.

New fields are nullable/additive. Do not rewrite raw journals. Any canonical
backfill derives fields deterministically and records derivation provenance.

Before production refresh:

1. update canonical schema/migration tests in a temporary database;
2. validate old-only, new-only and mixed journals;
3. quantify row-count, order-count, fill-count and PnL differences;
4. require zero unexplained signal/fill/PnL changes;
5. run incremental refresh only after the data review;
6. do not run a full production rebuild without explicit approval.

### Execution analysis compatibility

Reports group by both:

```text
execution_profile
resolved_execution_profile
execution_config_id
```

This prevents two strategies using different runtime TTL/cap/reprice settings
from being mislabeled as the same module. Cross-strategy aggregation is valid
only for identical execution configuration and compatible venue/order side,
and is operational diagnostics rather than causal alpha evidence. Maker/taker
performance claims still require the same signal/opportunity denominator.

## 7. Implementation phases

Each phase is one reviewable change set. Do not combine phases.
An implementation agent starts with Phase 0 only, returns the required handoff
and waits for review before Phase 1. The same gate applies between every later
phase.

### Phase 0: baseline and fixtures

Actions:

1. Dynamically inventory current processes, launchers, runtime journals,
   strategy registry status, workspace commit, production checkout commit and
   the active Polymarket client/protocol version.
2. Classify each runner as live, shadow, dormant or disabled from process/raw
   evidence; do not infer status from this document.
3. Resolve the current `plan_id/execution_id` authority conflict by tracing
   executable ID helpers and canonical migration tests. Update the system
   contract if it is stale before adding new identity code.
4. Resolve `order_side` semantics into internal `venue_side/outcome_side` plus
   backward-compatible canonical serialization.
5. Run all currently focused execution tests.
6. Capture representative old-runner plan and lifecycle outputs as test
   fixtures under `tests/fixtures/weather_execution/`.
7. For an active runner, derive the baseline from its deployed source,
   running command and sanitized raw output, not an uncommitted workspace
   approximation.
8. Record canonical baseline counts for signals, plans, orders, fills and
   settled PnL over the fixture window in
   `tests/fixtures/weather_execution/baseline_manifest.json`.
9. Add a short inventory test that enumerates current profile names.

The baseline manifest also records capture time, workspace/production commits,
active client/protocol version, authoritative ID/side mappings and exact test
counts. It contains no credentials, wallet material or unsanitized live
payloads.

Fixtures must cover:

- D1 static and capped chase;
- core-carry capped chase;
- heat-death chase and taker fallback;
- low-price repost lower, reprice upward and taker fallback;
- fast-source short GTD and immediate crossing retry.

Acceptance:

- no production file or process changed;
- no pre-existing working-tree change is reverted or overwritten;
- existing focused tests still pass;
- fixtures are derived from existing deterministic unit inputs, not current
  live orders containing secrets.
- one ID algorithm is documented as current authority;
- one side-field mapping is documented as current authority;
- workspace/production drift is explicitly listed;
- canonical baseline counts are saved for later impact comparison.

### Phase 1: contracts and profile schema

Actions:

1. Add `contracts.py`.
2. Extend `profiles.py` without changing existing profile meaning.
3. Add behavior-oriented profiles and compatibility alias resolution.
4. Update `execution/__init__.py`.
5. Add `execution_schema_version`, `execution_config_id`, stable
   `plan_dedupe_key`/`live_exposure_key`, typed constraints and full-depth
   `MarketBook`.
6. Add versioned `VenueCapabilities` and `FeeSchedule` snapshots without
   hard-coding current venue values into strategy profiles.

Acceptance:

- profile round-trip is JSON-safe;
- unknown profile fails explicitly;
- invalid profile combinations fail explicitly;
- intent/profile/metadata cannot set or escalate execution mode;
- run-context validation rejects an unconfirmed or paused live invocation;
- original and resolved profile names both survive serialization;
- old single-leg planner behavior remains unchanged;
- multi-leg profiles are not silently flattened.
- optional profile requirements are validated per profile rather than made
  globally mandatory;
- dynamic book/tick epoch and fee/capability provenance survive serialization;
- execution-config hashes differ when fixed TTL/cap-policy/reprice/allocation
  rules differ, but not when only a per-opportunity cap value changes;
- metadata cannot influence execution.

### Phase 2: shared quote engine

Actions:

1. Add pure quote functions.
2. Make existing `execution_policy.py` delegate to them where behavior is
   already identical.
3. Keep strategy-specific cap policy as explicit input.
4. Use Decimal/integer-tick arithmetic with backward-compatible serialization.
5. Do not migrate a runner yet.

Acceptance:

- old/new quote parity on fixtures;
- tick `0.001` and `0.01`;
- BUY and SELL rounding;
- maker never crosses;
- exact normalized values do not depend on binary-float representation;
- no two-sided book produces an explicit blocked/deferred result according to
  the existing policy, never a silent guessed quote.

### Phase 3: lifecycle and reconciliation engine

Actions:

1. Add pure lifecycle transitions.
2. Extract authoritative remaining-share calculation.
3. Add root/source-order chain helpers.
4. Add stable lifecycle action identity and ambiguous-result states.
5. Reuse the existing executor cancellation callback contract.

Acceptance:

- partial fill then cancel replaces only final remaining shares;
- cancel/fill race cannot overbuy;
- retry after process restart is idempotent;
- missing order state produces no replacement;
- every action has complete lineage fields;
- taker fallback cannot race a still-open maker;
- repeated/ambiguous actions reconcile before retry.

### Phase 4: extend shared order runtime and add execution journal

Actions:

1. Extend existing `runtime/order_runtime.py`; do not create a competing
   application service under `execution/`.
2. Add the append-only execution journal interface/implementation under
   `runtime/`.
3. Use injected fake venue and risk protocols; do not connect to CLOB.
4. Move side-effect ordering, aggregate risk rechecks, action claims and
   attempt evidence into the shared runtime.

Acceptance:

- runner-facing tests call one shared runtime API for submit and lifecycle;
- risk includes all children and open/reserved root exposure;
- ambiguous submit is not retried without reconciliation;
- one-writer/action claims prevent concurrent duplicate side effects;
- no live credentials or network calls are used.

### Phase 5: shared Polymarket venue adapter

Actions:

1. Add the Polymarket adapter around existing injected callbacks.
2. Normalize GTC and GTD response semantics.
3. Centralize capability, tick, minimum shares, fee/rebate, post-only and
   expiration handling.
4. Keep both proven submission implementations underneath during transition
   through injected callbacks; do not import scripts from `src`.
5. Preserve current CLOB protocol/client/collateral evidence on every attempt.
6. Derive and journal deterministic client/expected venue order identity before
   submit when the signing implementation supports it.

Acceptance:

- fake CLOB contract tests pass for GTC and GTD;
- FAK/FOK price, size and amount precision fixtures match the active client;
- post-only crossing response is classified consistently;
- GTD expiration includes the venue security threshold;
- a tick-size change invalidates the prior book epoch and forces replan;
- fee-schedule changes alter fee identity without changing strategy identity;
- estimated maker rebate is not recorded as realized cash;
- unknown submit/cancel states reconcile before retry;
- order status is not conflated with trade/fill finality;
- batch timeout/unknown is not blindly retried;
- no live credentials are required by tests;
- no network call occurs in unit tests;
- no strategy-specific branch exists in the target venue adapter.

### Phase 6: migrate dormant/shadow runners

Preferred migration order, subject to the fresh Phase 0 status inventory:

1. D1 dormant runner;
2. low-price zero-notional shadow;
3. heat-death shadow/tiny-live adapter in non-live mode;
4. generic planner `single_side_maker_v1`.

Only runners proven non-live at migration time belong in this phase. If one is
live, move it to the later single-instance canary phase.

For each runner:

1. Keep signal selection and journal reading unchanged.
2. Build `ExecutionIntent`.
3. Call the shared order runtime; the runner does not perform venue side
   effects or replacement ordering.
4. Preserve legacy output fields.
5. Run old and new implementations on the same fixtures.
6. Keep the old function behind a test-only parity helper until review passes.

Acceptance:

- exact child count, side, shares, price, cap, TTL and fallback parity;
- identical blocking reasons or a documented one-to-one mapping;
- no new live path;
- canonical lineage fields preserved.

### Phase 7: first active GTC runner direct canary migration

At plan review time core-carry was an active candidate, but Phase 0 process/raw
evidence decides which runner is actually first. A coding-only agent must not
switch it.

Actions:

1. Freeze representative lifecycle fixtures from the authoritative old path.
2. New engine computes plans/actions from those inputs through pure
   planning/lifecycle entry points, without an `ExecutionRunContext`.
3. Save field-level fixture differences and resolve every mismatch offline.
4. After explicit approval, stop the old instance and directly start one tiny
   canary on the new runtime; never run two submission authorities.
5. Do not place both old and new plans.

Required parity fields:

```text
child roles
shares
limit price
maker price cap
maker-only
deadline
reprice/cancel action
remaining shares
blocker
```

Acceptance before a later switch:

- no unexplained parity differences;
- no duplicate opportunity/plan identities;
- legacy/new plan-dedupe and live-exposure keys match while shadow plan IDs
  remain isolated;
- offline lifecycle fixtures cover fresh observation changes, maker rest,
  reprice and TTL cancellation;
- explicit user approval is obtained before the old live path is replaced.

### Phase 8: fast-source/HKO migration

Do this last because it is latency-sensitive and uses GTD.

Actions:

1. Preserve depth-adaptive taker/maker allocation.
2. Replace only maker intent construction and lifecycle submission with the
   shared profile/venue adapter.
3. Preserve the 45-second effective lifetime and configured immediate retry
   count.
4. Preserve signed exact-share cap behavior.
5. Add latency telemetry around quote fetch, sign, submit and retry.
6. Route allocation, quote and submit through the common runtime while keeping
   the fast-source signal detector unchanged.

Acceptance:

- no increase in planned total shares;
- maker remainder respects the five-share minimum;
- stale pre-cancel maker intent is discarded;
- immediate reprice always re-reads the book;
- test P95 runtime is not materially slower than the baseline fixture;
- live switch requires a separate explicit deployment.

### Phase 9: migrate remaining single-token taker and SELL runners

Actions:

1. Use the Phase 0 inventory to enumerate every active or retained
   single-token execution entry point.
2. Convert each to `ExecutionIntent` plus a versioned taker/SELL profile.
3. Preserve strategy selection, skip-plan evidence, full-ladder EV recheck and
   risk parameters.
4. Route side effects through shared `runtime/order_runtime.py`.
5. Keep basket/FOK and true-MM orchestration outside this phase.

Acceptance:

- no active single-token runner constructs or submits a venue order outside
  the shared runtime;
- BUY/SELL and YES/NO semantics are unambiguous;
- taker-only plan/fill parity passes;
- skip plans remain visible;
- no basket behavior is accidentally flattened into independent live legs.

### Phase 10: canonical schema, migration and reporting

Actions:

1. Ensure new profile/action/root fields enter canonical orders and
   are projected to `fact_trades` where fill-grain allows.
2. Keep unfilled plan/order facts available to opportunity-denominator reports.
3. Update execution comparison reports to group by behavior profile and
   `execution_config_id`.
4. Preserve legacy profile grouping.
5. Add coverage checks for replacement chains.
6. Validate old-only, new-only and mixed journals in a temporary DB before any
   production incremental refresh.

The coding-only agent stops after temporary-DB validation. It does not refresh
or rebuild production canonical data.

Acceptance:

- raw order count and canonical order count reconcile;
- no missing execution IDs;
- no duplicate fill synthesis;
- fill/fee coverage gate passes on the validated window;
- opportunity-denominator reports include unfilled maker plans;
- signal, fill and settled-PnL counts match the Phase 0 baseline except for
  explicitly explained additive plan/order dimensions.

### Phase 11: controlled rollout

This phase is outside the coding-only handoff and requires the deployment skill
plus explicit approval.

Order:

1. commit and review;
2. invoke `weather-strategy-deploy` and deploy git-first;
3. use the shared JRS tmux environment/helper required by the current ops
   contract; do not invent a new process/socket context;
4. recheck current processes, pause state, open orders and production commit;
5. run zero-notional handoff and offline lifecycle fixtures;
6. directly switch one dormant/non-live strategy;
7. one active GTC runner tiny canary;
8. fast-source/GTD tiny canary last;
9. inspect raw orders, open orders, fills, canonical lineage and patrol;
10. run the approved incremental canonical refresh and coverage gates;
11. quantify signal/plan/order/fill/PnL differences against the Phase 0
    baseline;
12. retain the old path for rollback until the review window passes.

Never switch the active GTC and fast-source/GTD paths in the same deployment.

## 8. Regression test plan

### Unit: contracts and profiles

- JSON serialization and deserialization;
- execution schema version compatibility;
- versioned profile lookup;
- compatibility aliases;
- original/resolved profile lineage;
- exact-behavior aliases resolve to the same execution-config identity;
- execution-config hash changes on fixed behavior parameters but not transient
  books or per-opportunity realized cap/deadline values;
- legacy/new plan-dedupe and live-exposure-key equivalence;
- allocation never exceeds total shares;
- explicit share allocation respects CLOB minimum;
- depth-adaptive allocation returns blocked dust explicitly;
- optional input requirements differ by profile;
- full-depth book ordering and freshness validation;
- dynamic tick/book-epoch serialization and stale-epoch rejection;
- fee-schedule/capability version serialization;
- maker-only and fallback combinations validate;
- unknown quote/lifecycle policies fail.
- intent/profile/metadata cannot set `execution_mode`, `confirm_live` or
  authorization;
- snapshot-replay, paper and live modes plus shadow/comparator run purposes
  serialize explicitly;
- live without confirmation or authorization is rejected;
- a paused runtime cannot reserve exposure or call the venue.

### Unit: quote semantics

- top-ask taker;
- multi-level exact-share taker VWAP and worst executable price;
- insufficient full-depth taker liquidity returns blocked remainder;
- join bid;
- improve bid one tick;
- one tick below ask;
- spread equal to one tick;
- missing bid;
- locked/crossed book;
- stale book;
- strategy cap below bid;
- model cap below bid;
- tick `0.001` and `0.01`;
- tick changes between planning and signing;
- prices near `0` and `1`;
- BUY rounds down when preserving maker status;
- SELL rounds up when preserving maker status;
- taker fee and maker rebate fields;
- fee-model and tick-source lineage;
- maker quote never reaches or exceeds ask.

### Unit: lifecycle state machine

- static maker rests;
- capped maker reprices upward;
- maker reposts lower after book falls;
- maximum reprice count reached;
- refresh cooldown not reached;
- absolute TTL reached;
- next data update blackout entered;
- data epoch changed early;
- thesis/token changed;
- fresh book missing;
- fallback disabled;
- fallback enabled but spread too wide;
- fallback enabled but depth too small;
- fallback enabled but fee-adjusted edge negative;
- valid fallback;
- terminal order ignored.
- wrong lifecycle owner cannot create an action;
- ambiguous prior action blocks a new action pending reconciliation.

### Unit: reconciliation and idempotency

- zero fill cancel/replace;
- partial fill cancel/replace;
- full fill before cancel;
- fill races with cancel;
- exchange reports canceled with final matched size;
- authenticated state unavailable;
- remaining shares below five;
- repeated lifecycle cycle;
- repeated exchange response;
- runner restart after cancel but before replace;
- two workers racing to claim the same plan/action key;
- ambiguous submit followed by successful exchange lookup;
- ambiguous submit with unresolved exchange lookup remains blocked;
- root/source order chain remains connected.

### Integration: fake venue

- GTC post-only accepted;
- GTD post-only accepted;
- post-only crossing rejection;
- retry after crossing with a newly fetched book;
- invalid tick rejection;
- FAK/FOK BUY and SELL amount-precision boundaries;
- GTC/GTD versus FAK/FOK precision differences;
- GTD security-threshold handling;
- fee-schedule refresh and provenance;
- maker-rebate estimate remains distinct from realized rebate;
- API timeout before submit;
- timeout after possible submit followed by order lookup;
- expected venue order hash recovers an unknown submit;
- unknown batch submit is not retried;
- aggregate risk rejection across taker and maker children;
- replacement risk rejection including still-open root exposure;
- cancel success;
- cancel reports already matched;
- order not found;
- normalized response contains requested/posted price and order ID.
- normalized response preserves raw order status and separate trade finality.

### Runner parity

For each migrated runner, compare old and new output on the same fixture:

- selected opportunity count;
- child role count;
- total planned shares;
- taker price;
- maker initial price;
- maker cap;
- deadline/cancel buffer;
- lifecycle action;
- blocker;
- fallback permission;
- strategy/config/profile identity;
- execution-config, plan-dedupe and live-exposure identity;
- comparison group and plan identities.

### Canonical chain

Test:

```text
signal candidate
  -> signal
  -> split child plans
  -> initial order attempts
  -> partial fill
  -> maker replacement
  -> final fill/cancel
  -> settlement
  -> fact_trades
```

Assertions:

- no signal duplication from profile changes;
- `fact_signal_candidates` is unchanged by execution-only migration;
- both children share one opportunity denominator;
- every fill maps to one execution attempt;
- replacement fill maps to the same root chain;
- fee is attached once;
- canceled/unfilled maker remains visible in execution-quality denominator;
- settled PnL uses canonical fills, not raw submitted notional;
- old-only, new-only and mixed journals produce equal fill/PnL facts;
- nullable additive fields do not drop legacy plans/orders;
- strategy config and execution config remain separately queryable.

### Historical golden cases

Use sanitized fixtures based on:

1. Wellington D1 `bid 0.87 / ask 0.93`;
2. Atlanta terminal false cross with delayed maker fill;
3. Busan depth change and partial-fill/cancel behavior;
4. HeadA partial-fill overbuy incident;
5. data update arriving earlier than expected;
6. post-only submit racing a moving ask;
7. tick-size difference at market price extremes.

Golden tests validate execution behavior only. They must not reinterpret the
weather thesis or settlement label.

## 9. Test commands

Run the smallest relevant test after every edit. At phase boundaries run:

```bash
.venv/bin/pytest -q \
  tests/pmm_tests/test_weather_execution_contracts.py \
  tests/pmm_tests/test_weather_execution_profiles.py \
  tests/pmm_tests/test_weather_execution_quote_engine.py \
  tests/pmm_tests/test_weather_execution_lifecycle.py \
  tests/pmm_tests/test_weather_execution_reconciliation.py \
  tests/pmm_tests/test_weather_order_runtime.py \
  tests/pmm_tests/test_weather_polymarket_venue_adapter.py \
  tests/pmm_tests/test_weather_execution_pipeline.py \
  tests/pmm_tests/test_weather_order_executor_proxy.py \
  tests/pmm_tests/test_tick_engine_maker_only.py
```

The first seven files are target tests created by the corresponding phases.
Do not add empty placeholder tests merely to make the command resolve.

Runner parity:

```bash
.venv/bin/pytest -q \
  tests/pmm_tests/test_d1_yes_high_mid_shadow_v1.py \
  tests/pmm_tests/test_low_price_yes_lottery_sizing.py \
  tests/pmm_tests/test_weather_current_yes_heat_death_tiny_live_v1.py \
  tests/pmm_tests/test_weather_current_yes_core_carry_tiny_live_v2.py \
  tests/pmm_tests/test_weather_fast_source_prev_no_trial.py
```

Coverage/report chain:

```bash
.venv/bin/pytest -q \
  tests/pmm_tests/test_weather_clob_fill_coverage_gate.py \
  tests/pmm_tests/test_weather_execution_module_compare.py \
  tests/pmm_tests/test_repair_clob_fill_cache_for_gate.py
```

Final local checks:

```bash
git diff --check
.venv/bin/python -m compileall -q src/strategies/weather_edge_v1/execution
.venv/bin/python scripts/ops/check_weather_docs.py
```

Run broader tests only after focused tests pass. If an existing broader test
hangs, identify the exact test with `-vv`, terminate it cleanly and report it;
do not claim the suite passed.

## 10. Review gates

The reviewer must reject a phase if any answer is unclear:

1. Did this phase change an existing live price, size, TTL or fallback rule?
2. Can maker placement ever cross the current ask?
3. Does replacement use authenticated final remaining shares?
4. Can a runner restart repeat an action?
5. Are data epoch and pre-update cancellation explicit?
6. Are tick, fee, rebate, minimum shares and order type venue-owned?
7. Can every replacement/fallback be traced to one root order?
8. Are unfilled maker opportunities retained in the comparison denominator?
9. Did the change preserve raw and canonical lineage fields?
10. Was any live process, production checkout or real order touched?
11. Is behavior expressed through reusable policy components rather than a
    strategy-name branch?
12. Does the runner call the shared order runtime instead of ordering venue side
    effects itself?
13. Are strategy and execution configuration identities both preserved?
14. Can legacy/new paths deduplicate the same opportunity during migration?
15. Are old-only, new-only and mixed canonical rows covered?
16. Does any `src` module import operational code from `scripts/ops`?
17. Was current live/shadow status verified dynamically rather than copied
    from the plan?
18. Is execution mode and live authorization supplied only by the runtime
    context, never by the intent, profile or strategy metadata?
19. Is the active tick/precision/fee schedule revalidated at submission, with
    requested, normalized and realized fee/rebate evidence kept distinct?

Any intentional behavior difference requires:

- a new profile version;
- an explicit old/new fixture difference;
- a separate execution-quality hypothesis;
- shadow evidence;
- explicit live deployment approval.

## 11. Required handoff after each phase

The implementation agent must return:

```text
Phase completed:
Files changed:
Behavior intentionally changed: none / exact list
Old/new parity result:
Tests run and exact result:
Tests not run and reason:
Production/live actions: none
Known follow-up:
```

Do not hand back a partial phase with only design commentary. Either complete
the phase and its tests, or state the concrete blocker.

## 12. Definition of done

The refactor is complete only when:

- every active weather runner selects a versioned execution profile;
- every intent/profile is mode-neutral and only the shared runtime can
  authorize live side effects;
- strategy runners no longer implement quote/reprice/cancel/fallback logic;
- strategy runners use the shared order runtime for execution side effects;
- both GTC and GTD route through the common Polymarket adapter contract;
- GTC/GTD/FAK/FOK precision, dynamic tick epochs and current fee-schedule
  provenance are covered by adapter contract tests;
- partial-fill replacement is centralized;
- strategy-specific executor branches have been removed from the target
  adapter;
- execution configuration identity separates differing runtime parameters;
- profile/action/root lineage reaches canonical facts;
- mixed legacy/new canonical migration preserves signal/fill/PnL counts;
- all focused and canonical chain tests pass;
- shadow parity is clean for active strategies;
- live migration has been separately approved and verified;
- old runner lifecycle paths are marked dormant but retained for rollback.

Until the live migration phase is explicitly approved, "code refactor
complete" does not mean "production switched".
