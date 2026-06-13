# All-YES Underround Live-Prep v0

> generated_at_utc: `2026-06-13T19:27:37.639836+00:00`
> target_metric: `current_all_yes_underround_equal_share_basket`
> verdict: `PAPER_SHADOW_ENGINEERING_CANDIDATE`
> Scope: scanner/live-prep only; no N100/live config changed and no orders placed.

## Data Snapshot

- DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`
- latest orderbook snapshot: `/Users/deepsleep/projects/pm_agents/runtime/weather_edge_v1/market_data/orderbook_snapshots/2026-06-14/orderbook_snapshot_20260614_0300.jsonl.gz`
- snapshot rows: `1044`; YES rows `522`; YES events `69`.
- snapshot ts UTC: `2026-06-13T19:00:53Z` to `2026-06-13T19:00:53Z`.
- event_date rows: `{'2026-06-13': 208, '2026-06-14': 836}`.
- fact built at: `2026-06-13T19:19:44.825906+00:00`.
- CLOB fill coverage gate: `gate_pass=True`; fail_reasons `[]`.

### Mandatory 5-Line SQL Self-Check

```text
MAX(fact_built_at_utc) = 2026-06-13T19:19:44.825906+00:00
fact_trades by trade_class = [{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]
fact_trades by settlement_status = [{'settlement_status': None, 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]
fact_signal_candidates coverage = {'rows': 28492, 'eligible': 9697, 'paper_ordered': 3701, 'live_filled': 348}
CLOB orders with fills = [{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]
```

## Current Scanner Gates

- Candidate threshold: underround >= `0.02`, legs >= `5`, min top-of-book shares >= `5.0`, max YES spread <= `0.05`.
- Current paper/shadow candidates: `1`.
- Station-basis comparison gate: `NOT_READY_ACCUMULATE_SHADOW` with settled `0` / pending `1`.
- All-YES paper execution gate: `NOT_READY_ACCUMULATE_PAPER_SHADOW` with baskets `2` / settled `0`.
- Live-equivalent paper baskets: `0`; stale/observation-only baskets `2`; max record age `1353.209` seconds.
- Live-equivalent settled exactly-one-winner baskets: `0`; ROI `None`.
- All-YES paper passed checks: `['clob_coverage_gate_pass', 'paper_ledger_exists']`.
- All-YES monitor blockers: `['live_equivalent_paper_ledger_empty', 'paper_baskets_not_live_equivalent', 'current_guard_audit_fail', 'forward_settled_baskets_low', 'forward_roi_not_ready', 'positive_basket_rate_not_ready', 'live_executor_missing']`; pending by date `{'2026-06-14': 2}`.
- Stale basket guard: max snapshot age `180.0` seconds before paper/live candidate recording.
- Fresh paper cycle: verdict `STALE_SNAPSHOT_SKIP_CYCLE`; executed `False`; latest snapshot age `1598.678` seconds; reason `snapshot_too_old`.
- Repeatable local command: `scripts/ops/run_all_yes_underround_paper_v0.sh`.
- Low-latency forward-paper command: `scripts/ops/run_all_yes_underround_fresh_paper_v0.sh`; it only records baskets while the latest snapshot is inside the TTL.
- Guard coverage: `scripts/ops/all_yes_underround_guards.py test` and `pytest tests/pmm_tests/test_all_yes_underround_guards.py` cover all-leg completeness, depth, spread, cost, duplicate legs, underround mismatch, and kill switch.

## Passing Paper/Shadow Candidates

| event_date | city | legs | YES ask cost | underround | min ask size | max spread | 5-share basket cost | 5-share gross profit | shadow candidate | blockers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2026-06-14 | `Denver` | 10 | 0.972 | +2.8% | 7.33 | 0.03 | 4.860 | 0.140 | True | `` |

## Top Baskets And Rejections

Rows with blockers are shown to make the funnel explicit; they are not executable candidates.

| event_date | city | legs | YES ask cost | underround | min ask size | max spread | 5-share basket cost | 5-share gross profit | shadow candidate | blockers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2026-06-13 | `Munich` | 1 | 0.004 | +99.6% | 55.00 | NA | 0.020 | 4.980 | False | `leg_count_below_live_floor` |
| 2026-06-14 | `Denver` | 10 | 0.972 | +2.8% | 7.33 | 0.03 | 4.860 | 0.140 | True | `` |
| 2026-06-14 | `Busan` | 9 | 0.999 | +0.1% | 5.03 | 0.029 | 4.995 | 0.005 | False | `underround_below_threshold` |
| 2026-06-13 | `Amsterdam` | 2 | 1.003 | -0.3% | 403.98 | 0.001 | 5.015 | -0.015 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-13 | `Milan` | 2 | 1.003 | -0.3% | 341.99 | 0.003 | 5.015 | -0.015 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-13 | `BuenosAires` | 3 | 1.006 | -0.6% | 37.00 | 0.006 | 5.030 | -0.030 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-13 | `Houston` | 5 | 1.006 | -0.6% | 6.00 | 0.022 | 5.030 | -0.030 | False | `underround_below_threshold` |
| 2026-06-13 | `Paris` | 3 | 1.006 | -0.6% | 5.00 | 0.005 | 5.030 | -0.030 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `Ankara` | 9 | 1.014 | -1.4% | 5.00 | 0.03 | 5.070 | -0.070 | False | `underround_below_threshold` |
| 2026-06-13 | `SaoPaulo` | 4 | 1.015 | -1.5% | 14.91 | 0.008 | 5.075 | -0.075 | False | `leg_count_below_live_floor,underround_below_threshold` |
| 2026-06-14 | `MexicoCity` | 11 | 1.016 | -1.6% | 10.31 | 0.05 | 5.080 | -0.080 | False | `underround_below_threshold` |
| 2026-06-14 | `SanFrancisco` | 10 | 1.016 | -1.6% | 8.00 | 0.04 | 5.080 | -0.080 | False | `underround_below_threshold` |
| 2026-06-14 | `Karachi` | 8 | 1.018 | -1.8% | 6.22 | 0.04 | 5.090 | -0.090 | False | `underround_below_threshold` |
| 2026-06-14 | `HongKong` | 5 | 1.022 | -2.2% | 20.00 | 0.02 | 5.110 | -0.110 | False | `underround_below_threshold` |
| 2026-06-13 | `NYC` | 7 | 1.023 | -2.3% | 5.00 | 0.02 | 5.115 | -0.115 | False | `underround_below_threshold` |
| 2026-06-14 | `London` | 8 | 1.032 | -3.2% | 15.42 | 0.02 | 5.160 | -0.160 | False | `underround_below_threshold` |
| 2026-06-14 | `Beijing` | 10 | 1.033 | -3.3% | 6.49 | 0.03 | 5.165 | -0.165 | False | `underround_below_threshold` |
| 2026-06-14 | `Seoul` | 7 | 1.038 | -3.8% | 7.30 | 0.02 | 5.190 | -0.190 | False | `underround_below_threshold` |
| 2026-06-14 | `Paris` | 7 | 1.040 | -4.0% | 10.00 | 0.01 | 5.200 | -0.200 | False | `underround_below_threshold` |
| 2026-06-13 | `PanamaCity` | 4 | 1.042 | -4.2% | 160.00 | 0.03 | 5.210 | -0.210 | False | `leg_count_below_live_floor,underround_below_threshold` |

## Verdict

The live direction is stronger for `all_yes_underround_basket_v0` than for the current BUY_NO forecast-quality sleeve or station-basis v1, but it is not a direct live switch yet.

- Offline evidence: confirmed in the 2026-06-09 robust all-YES underround run, including time-aligned executable thresholds.
- Current market state: latest snapshot has executable-looking baskets, led by Denver 2026-06-14.
- Paper ledger state: `2` baskets recorded, `2` pending, `0` settled exactly-one-winner.
- Live-equivalent forward state: `0` baskets recorded within the `180.0` second TTL; stale paper observations remain useful for opportunity discovery but do not count toward live-prep forward evidence.
- Fresh-cycle state: `STALE_SNAPSHOT_SKIP_CYCLE`; this path must run immediately after snapshot capture, preferably on the same host as the orderbook snapshot writer, before live-equivalent forward baskets can accumulate.
- Minimum-size check: any passing candidates must have enough top-of-book size for an equal-share 5-share basket.
- Paper execution: local all-leg-or-none paper ledger records current candidates, and the shared guard fails closed on missing legs, shallow depth, wide spread, cost cap, duplicate condition ids, underround mismatch, or kill switch.
- Blocking risk: forward settlements are still pending, and live execution still needs signed CLOB order placement plus partial-fill cancellation/unwind orchestration.

Recommended next action: keep running the scanner plus local all-leg-or-none paper cycle on fresh snapshots, then wait for settled exactly-one-winner baskets. Any N100/live deployment must first add signed all-leg execution with partial-fill cancellation/unwind rules and go through `weather-strategy-deploy`.
