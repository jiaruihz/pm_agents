# All-YES Underround Live-Prep v0

> generated_at_utc: `2026-06-14T07:19:07.424133+00:00`
> target_metric: `current_all_yes_underround_equal_share_basket`
> verdict: `PAPER_SHADOW_ENGINEERING_CANDIDATE`
> Scope: scanner/live-prep only; no N100/live config changed and no orders placed.

## Data Snapshot

- DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- latest orderbook snapshot: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/orderbook_snapshots/2026-06-14/orderbook_snapshot_20260614_1500.jsonl.gz`
- snapshot rows: `1566`; YES rows `783`; YES events `94`.
- snapshot ts UTC: `2026-06-14T07:00:36Z` to `2026-06-14T07:00:36Z`.
- event_date rows: `{'2026-06-14': 614, '2026-06-15': 952}`.
- fact built at: `2026-06-14T07:15:01.773373+00:00`.
- CLOB fill coverage gate: `gate_pass=True`; fail_reasons `[]`.

### Mandatory 5-Line SQL Self-Check

```text
MAX(fact_built_at_utc) = 2026-06-14T07:15:01.773373+00:00
fact_trades by trade_class = [{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]
fact_trades by settlement_status = [{'settlement_status': None, 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]
fact_signal_candidates coverage = {'rows': 29207, 'eligible': 9986, 'paper_ordered': 3768, 'live_filled': 348}
CLOB orders with fills = [{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]
```

## Current Scanner Gates

- Candidate threshold: underround >= `0.02`, legs >= `5`, min top-of-book shares >= `5.0`, max YES spread <= `0.05`.
- Current paper/shadow candidates: `1`.
- Same-family threshold telemetry: `[{'threshold': 0.005, 'candidate_count': 2, 'top_candidates': [{'event_date': '2026-06-14', 'city': 'Atlanta', 'underround': 0.024, 'total_yes_ask_cost': 0.976, 'min_ask_size': 5.0, 'max_yes_spread': 0.026}, {'event_date': '2026-06-15', 'city': 'Munich', 'underround': 0.009, 'total_yes_ask_cost': 0.991, 'min_ask_size': 5.0, 'max_yes_spread': 0.02}]}, {'threshold': 0.01, 'candidate_count': 1, 'top_candidates': [{'event_date': '2026-06-14', 'city': 'Atlanta', 'underround': 0.024, 'total_yes_ask_cost': 0.976, 'min_ask_size': 5.0, 'max_yes_spread': 0.026}]}, {'threshold': 0.02, 'candidate_count': 1, 'top_candidates': [{'event_date': '2026-06-14', 'city': 'Atlanta', 'underround': 0.024, 'total_yes_ask_cost': 0.976, 'min_ask_size': 5.0, 'max_yes_spread': 0.026}]}]`.
- Station-basis comparison gate: `NOT_READY_ACCUMULATE_SHADOW` with settled `1` / pending `0`.
- All-YES paper execution gate: `NOT_READY_ACCUMULATE_PAPER_SHADOW` with baskets `3` / settled `0`.
- Live-equivalent paper baskets: `0`; stale/observation-only baskets `2`; max record age `1353.209` seconds.
- Live-equivalent settled exactly-one-winner baskets: `0`; ROI `None`.
- All-YES paper passed checks: `['clob_coverage_gate_pass', 'paper_ledger_exists', 'current_guard_audit_has_allowed', 'dry_run_live_plan_available']`.
- All-YES monitor blockers: `['live_equivalent_paper_ledger_empty', 'paper_baskets_not_live_equivalent', 'source_snapshot_invalid', 'forward_settled_baskets_low', 'forward_settled_active_dates_low', 'forward_roi_not_ready', 'positive_basket_rate_not_ready', 'dry_run_executor_state_missing', 'dry_run_executor_plan_bridge_missing', 'live_executor_missing']`; pending by date `{'2026-06-14': 2, '2026-06-15': 1}`.
- Stale basket guard: max snapshot age `180.0` seconds before paper/live candidate recording.
- Fresh paper cycle: verdict `FRESH_SNAPSHOT_CYCLE_RAN`; executed `True`; latest snapshot age `86.734` seconds; reason `fresh`.
- Repeatable local command: `scripts/ops/run_all_yes_underround_paper_v0.sh`.
- Low-latency forward-paper command: `scripts/ops/run_all_yes_underround_fresh_paper_v0.sh`; it only records baskets while the latest snapshot is inside the TTL.
- Guard coverage: `scripts/ops/all_yes_underround_guards.py test` and `pytest tests/pmm_tests/test_all_yes_underround_guards.py` cover all-leg completeness, depth, spread, cost, duplicate legs, underround mismatch, and kill switch.

## Passing Paper/Shadow Candidates

| event_date | city | legs | YES ask cost | underround | min ask size | max spread | 5-share basket cost | 5-share gross profit | shadow candidate | blockers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2026-06-14 | `Atlanta` | 7 | 0.976 | +2.4% | 5.00 | 0.026 | 4.880 | 0.120 | True | `` |

## Top Baskets And Rejections

Rows with blockers are shown to make the funnel explicit; they are not executable candidates.

| event_date | city | legs | YES ask cost | underround | min ask size | max spread | 5-share basket cost | 5-share gross profit | shadow candidate | blockers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2026-06-14 | `MexicoCity` | 10 | 0.974 | +2.6% | 1.83 | 0.03 | 4.870 | 0.130 | False | `top_of_book_capacity_below_min_shares` |
| 2026-06-14 | `Atlanta` | 7 | 0.976 | +2.4% | 5.00 | 0.026 | 4.880 | 0.120 | True | `` |
| 2026-06-15 | `Munich` | 11 | 0.991 | +0.9% | 5.00 | 0.02 | 4.955 | 0.045 | False | `underround_below_threshold` |
| 2026-06-15 | `Austin` | 11 | 1.000 | +0.0% | 27.66 | 0.033 | 5.000 | 0.000 | False | `underround_below_threshold` |
| 2026-06-14 | `Tokyo` | 3 | 1.004 | -0.4% | 32.00 | 0.004 | 5.020 | -0.020 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `Manila` | 4 | 1.005 | -0.5% | 7.50 | 0.027 | 5.025 | -0.025 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `KualaLumpur` | 3 | 1.006 | -0.6% | 10.00 | 0.008 | 5.030 | -0.030 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `Singapore` | 4 | 1.009 | -0.9% | 49.00 | 0.012 | 5.045 | -0.045 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `NYC` | 8 | 1.011 | -1.1% | 1.11 | 0.03 | 5.055 | -0.055 | False | `underround_below_threshold,top_of_book_capacity_below_min_shares` |
| 2026-06-14 | `Busan` | 3 | 1.012 | -1.2% | 64.10 | 0.009 | 5.060 | -0.060 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `Chengdu` | 6 | 1.012 | -1.2% | 0.46 | 0.05 | 5.060 | -0.060 | False | `underround_below_threshold,top_of_book_capacity_below_min_shares,spread_above_threshold` |
| 2026-06-14 | `Amsterdam` | 7 | 1.013 | -1.3% | 6.43 | 0.03 | 5.065 | -0.065 | False | `underround_below_threshold` |
| 2026-06-14 | `HongKong` | 3 | 1.015 | -1.5% | 31.01 | 0.01 | 5.075 | -0.075 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `Shenzhen` | 3 | 1.015 | -1.5% | 30.00 | 0.009 | 5.075 | -0.075 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `Seoul` | 3 | 1.015 | -1.5% | 12.62 | 0.002 | 5.075 | -0.075 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-15 | `Dallas` | 11 | 1.017 | -1.7% | 1.02 | 0.03 | 5.085 | -0.085 | False | `underround_below_threshold,top_of_book_capacity_below_min_shares` |
| 2026-06-15 | `Busan` | 11 | 1.019 | -1.9% | 5.00 | 0.04 | 5.095 | -0.095 | False | `underround_below_threshold` |
| 2026-06-14 | `Lucknow` | 7 | 1.022 | -2.2% | 17.06 | 0.03 | 5.110 | -0.110 | False | `underround_below_threshold` |
| 2026-06-14 | `SanFrancisco` | 11 | 1.026 | -2.6% | 8.00 | 0.03 | 5.130 | -0.130 | False | `underround_below_threshold` |
| 2026-06-14 | `Guangzhou` | 2 | 1.027 | -2.7% | 10.51 | 0.109 | 5.135 | -0.135 | False | `leg_count_below_live_floor,underround_below_threshold,spread_above_threshold` |

## Verdict

The live direction is stronger for `all_yes_underround_basket_v0` than for the current BUY_NO forecast-quality sleeve or station-basis v1, but it is not a direct live switch yet.

- Offline evidence: confirmed in the 2026-06-09 robust all-YES underround run, including time-aligned executable thresholds.
- Current market state: latest snapshot has executable-looking baskets, led by Atlanta 2026-06-14.
- Paper ledger state: `3` baskets recorded, `3` pending, `0` settled exactly-one-winner.
- Live-equivalent forward state: `0` baskets recorded within the `180.0` second TTL; stale paper observations remain useful for opportunity discovery but do not count toward live-prep forward evidence.
- Fresh-cycle state: `FRESH_SNAPSHOT_CYCLE_RAN`; this path must run immediately after snapshot capture, preferably on the same host as the orderbook snapshot writer, before live-equivalent forward baskets can accumulate.
- Minimum-size check: any passing candidates must have enough top-of-book size for an equal-share 5-share basket.
- Paper execution: local all-leg-or-none paper ledger records current candidates, and the shared guard fails closed on missing legs, shallow depth, wide spread, cost cap, duplicate condition ids, underround mismatch, or kill switch.
- Blocking risk: forward settlements are still pending, and live execution still needs signed CLOB order placement plus partial-fill cancellation/unwind orchestration.

Recommended next action: keep running the scanner plus local all-leg-or-none paper cycle on fresh snapshots, then wait for settled exactly-one-winner baskets. Any N100/live deployment must first add signed all-leg execution with partial-fill cancellation/unwind rules and go through `weather-strategy-deploy`.
