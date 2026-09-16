# Weather Execution Architecture

Status: authoritative execution-module contract
Updated: 2026-07-17
Source of truth: yes, together with `WEATHER_SYSTEM_CONTRACT.md`

## 1. Boundary

Weather strategy and execution module are separate identities.

```text
signal/opportunity
  -> strategy config (filter + sizing + execution_profile)
  -> child plan (execution_policy + lifecycle + role)
  -> order attempt / replacement chain
  -> fill + fee
  -> settlement + PnL
```

Changing a maker/taker implementation must not create a new signal. The same
`signal_id` is the comparison denominator; execution changes begin at `plan`.

The strategy owns:

- whether the opportunity is eligible;
- total size and risk budget;
- which `execution_profile` to use.

The execution profile owns:

- one or more child legs;
- the quote policy of each leg;
- the lifecycle policy of each leg;
- the planned share fraction of each leg.

The shared executor owns venue semantics, post-only enforcement, signing,
submission, cancellation, durable order evidence, fill reconciliation and fee
lineage. Profiles do not bypass the live pause switch, notional caps or other
fund-safety boundaries.

## 2. Identity fields

These names are not interchangeable:

| Field | Grain | Meaning |
|---|---|---|
| `execution_profile` | strategy plan bundle | Stable, versioned module selected by the strategy, such as `taker_now_v1` |
| `execution_policy` | child plan | Quote algorithm for one leg, such as `taker_top_ask_v1` or `maker_queue_v2` |
| `order_lifecycle_policy` | child plan | What happens after posting: immediate, wait for data epoch, chase, fallback, cancel |
| `child_order_role` | child plan/order | `single`, `taker`, `maker`, or an explicit lifecycle action |
| `comparison_group_id` | opportunity | Joins alternative profiles generated from the same signal/book epoch |
| `source_order_id` | order attempt | Links a reprice/fallback attempt to the previous exchange order |
| `execution_action` | order attempt | Actual lifecycle transition, for example maker reprice or taker fallback |
| `maker_only` | child plan/order | Planned/actual post-only semantics; not inferred from whether a fee happened to be zero |

`execution_profile` is part of strategy configuration identity. Quote and
lifecycle policies are the profile's expanded child parameters. A behavior
change requires a new versioned profile name; do not mutate the meaning of an
existing profile.

## 3. Profiles

The registry is `src/strategies/weather_edge_v1/execution/profiles.py`.

### `taker_now_v1`

- one `single` leg, 100% of planned shares;
- quote policy `taker_top_ask_v1`;
- lifecycle `taker_now`;
- marketable order, not maker-only.

### `single_side_maker_v1`

- one `single` leg, 100% of planned shares;
- quote policy `maker_queue_v2`;
- lifecycle `maker_until_data_update`;
- post-only; cancels before the next registered data epoch.

### `split_taker_maker_chase_v1`

- `taker` leg: 50%, `current_yes_heat_death_taker_probe_v1`, `taker_now`;
- `maker` leg: 50%, `current_yes_heat_death_maker_probe_v1`,
  `maker_chase_then_taker_fallback_v1`;
- the maker leg may reprice without a count limit but never above the initial
  fresh ask cap; after the chase window it may fall back to taker only when the
  registered price/depth conditions still hold;
- this is currently expanded by the heat-death strategy-specific multi-leg
  orchestrator. The generic single-leg planner must reject it rather than
  silently flattening it.

### d1 configurable profiles

- `d1_taker_only_v1`: one 5-share taker leg; maker disabled;
- `d1_taker_plus_maker_static_v1`: 5-share taker plus a 5-share maker that
  keeps its initial post-only quote;
- `d1_taker_plus_maker_chase_to_mid_v1`: the same split, but the maker may
  follow the fresh bid for at most three reprices;
- both maker profiles post one tick above the fresh bid, never cross the ask,
  and are capped at the trigger-time mid;
- neither maker profile converts to taker. Both expire 90 seconds before the
  absolute next weather-data epoch; inside that blackout, only taker is emitted;
- these profiles are expanded by the d1 strategy-specific multi-leg
  orchestrator, while submission, post-only enforcement, authoritative
  cancel/replace sizing and expiry cancellation stay in the shared executor.

## 4. Canonical lineage

`plans` stores the selected profile and expanded child identity:

```text
plan_id, signal_id, config_id,
execution_profile, execution_policy, order_lifecycle_policy,
child_order_role, comparison_group_id, maker_only
```

Profile-aware `plan_id` includes `execution_profile + child_order_role`, so two
legs using the same quote policy cannot collide. Legacy unprofiled single-leg
plans retain their historical IDs.

`orders` stores the actual attempt:

```text
execution_id, order_id, plan_id, instance_id,
execution_action, child_order_role, maker_only,
source_order_id, cancel_before_order_id,
requested_price, posted_price, best_bid, best_ask, placed_at_utc
```

`fills.execution_id` attaches quantity, price, time and fee evidence. The
derived `fact_trades` projects profile/policy/lifecycle/role so settled PnL can
be sliced without bypassing canonical fills.

Raw strategy journals remain durable evidence. Canonical refresh discovers all
`strategy_instance` rows with `desired_status=enabled` and `expected_live=1`;
adding a future module requires registering the instance, not editing another
hard-coded order-file list.

## 5. Comparison contract

The primary comparison is opportunity based, not filled-order based.

For every profile report:

- planned opportunities, shares and notional;
- submitted attempts and replacement count;
- filled shares / planned shares;
- time to first fill;
- average fill price and fee;
- settled PnL / actual fill cost;
- settled PnL / planned notional, so non-fills remain in the denominator;
- route counts such as maker fill, reprice, cancel and taker fallback.

Maker versus taker price improvement is computed only for matching
`comparison_group_id` (or legacy matching `signal_id`). A maker-only report
conditioned on filled orders is invalid because it drops queue risk and missed
winners. H1 and H2 must not be treated as a causal maker/taker A/B: their signal
and price regimes differ. The valid live pair is the two children inside one H1
opportunity.

Current report:

```bash
.venv/bin/python scripts/analysis/execution_quality/weather_execution_module_compare.py \
  --db runtime/weather.db \
  --active-live-only \
  --json-out runtime/weather_edge_v1/execution_module_compare/latest.json
```

## 6. Operational refresh

The small post-trade refresh runs:

```text
active registered live journals
  -> canonical signals/plans/orders
  -> runtime-order coverage gate
  -> CLOB fill sync
  -> fact_trades rebuild
  -> fill/fee coverage gate
```

The active-journal coverage gate must pass before an execution-module PnL
comparison is published. Raw order count, canonical order count and missing
execution IDs are reported per journal.

## 7. Safety and promotion

- A comparison profile is paper/shadow by default.
- Live promotion remains git-first and explicitly confirmed.
- New profiles inherit the existing pause switch, order/city-day/day caps and
  CLOB reconciliation requirements.
- Execution quality cannot promote a strategy whose underlying signal remains
  unconfirmed; it only selects the better way to express the same signal.
