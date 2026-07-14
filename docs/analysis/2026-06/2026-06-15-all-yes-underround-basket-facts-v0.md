# All-YES Underround Basket Facts v0

> **2026-07-14 fee correction:** 本报告的 `+3.16%` 是 gross、未扣 Weather taker fee，不能再作为 confirmed/executable ROI。固定同一 270 个 settled baskets 后，fee-adjusted ROI 为 `-0.42%`，date-bootstrap CI `[-0.68%,-0.19%]`；详见 [fee correction v1](../../2026-07/2026-07-14-all-yes-underround-fee-correction-v1.md)。

> generated_at_utc: `2026-06-15T16:12:28.191053+00:00`
> target_metric: `all_yes_underround_basket_fact_refresh_v0`
> contract: `strategy_id + snapshot_ts_utc + event_date + city + event_slug`
> Scope: canonical data refresh and research denominator; no live behavior changed.

## Data Snapshot

- DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- fact built at: `2026-06-15T16:08:57.366089+00:00`
- Snapshot files scanned: `1338` from `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/orderbook_snapshots/2026-05-19/orderbook_snapshot_20260519_0100.jsonl.gz` to `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/orderbook_snapshots/2026-06-16/orderbook_snapshot_20260616_0000.jsonl.gz`.
- Basket fact rows: `101530`; leg fact rows `827189`.
- Settlement outcomes loaded: `18011` from `settlement_outcomes city/target_date/bracket`.
- CLOB fill coverage gate: `gate_pass=True`.
- Outputs: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/all_yes_underround_basket_v0/fact_baskets.jsonl`, `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/all_yes_underround_basket_v0/fact_basket_legs.jsonl`, `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/all_yes_underround_basket_v0/summary.json`.

### Mandatory 5-Line SQL Self-Check

```text
MAX(fact_built_at_utc) = 2026-06-15T16:08:57.366089+00:00
fact_trades by trade_class = [{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]
fact_trades by settlement_status = [{'settlement_status': None, 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]
fact_signal_candidates coverage = {'rows': 30136, 'eligible': 10365, 'paper_ordered': 3968, 'live_filled': 348}
CLOB orders with fills = [{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]
```

## Canonical Grain

- `fact_baskets.jsonl`: one row per strategy/snapshot/city/event basket observation.
- `fact_basket_legs.jsonl`: one row per basket leg, keyed by `basket_id + condition_id`.
- `quality_guard_pass` means complete all-leg top-of-book representation without using live TTL.
- `strategy_candidate` additionally applies the configured underround threshold.
- Settlement is resolved by `settlements.condition_id` first, then DB `settlement_outcomes` keyed by `city + target_date + bracket`.
- Forward paper/live readiness remains a separate evidence layer and must use TTL-valid paper/live ledgers.

## Historical Funnel

- Snapshot events observed: `101530`.
- Quality guard pass observations: `48479`.
- Strategy candidates at min_underround `0.02`: `297`.
- Unique strategy candidate events: `195`.
- Settled exactly-one-winner candidates: `270`.
- Pending / missing-settlement candidates: `27`.

## Threshold Results

| threshold | observations | unique events | active dates | settled exact | unit ROI |
|---:|---:|---:|---:|---:|---:|
| 0.005 | 947 | 435 | 28 | 877 | +1.7% |
| 0.010 | 649 | 336 | 28 | 600 | +2.2% |
| 0.020 | 297 | 195 | 27 | 270 | +3.2% |
| 0.030 | 123 | 86 | 26 | 111 | +4.2% |
| 0.050 | 18 | 13 | 8 | 18 | +6.4% |

## Retail Cost Buffer

- Per-leg buffer cents min/median/p90/max: `[0.18181818181818182, 0.30000000000000004, 0.5181818181818182, 0.9199999999999999]`.
- Survivors after extra per-leg cost: `{'0.1': 297, '0.25': 215, '0.5': 34, '1.0': 0}`.

## Data Gaps

- 27 historical strategy-candidate observations still lack fully resolved settlement outcomes.

## Verdict

Historical all-YES orderbook facts are now on a single basket grain. Use this output for opportunity, settlement, and retail-cost sensitivity; do not use it as live approval, because forward TTL-valid all-leg fills remain a separate evidence layer.
