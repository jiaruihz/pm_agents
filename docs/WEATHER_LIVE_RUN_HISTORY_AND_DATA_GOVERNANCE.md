# Weather Live Run History and Data Governance

Status: current-reference
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; reference only, not production source of truth

Last updated: 2026-07-14

This document records the early weather live-trading rollout history, known mistakes, and data-model rules needed to keep future analysis reproducible. Read it with:

- [WEATHER_STRATEGY_ENTRYPOINT.md](WEATHER_STRATEGY_ENTRYPOINT.md)
- [WEATHER_STRATEGY_QUANT_DESIGN.md](WEATHER_STRATEGY_QUANT_DESIGN.md)
- [WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md](WEATHER_DASHBOARD_DATA_MODEL_AUDIT.md)

## 1. Plain-English Summary

The first weather live rollout is not a clean single run. It includes local `pm_agent` live orders, N100 `pm_agent` live orders after the strategy moved to N100, and multiple code/config revisions over three days.

For live performance, **all real orders must be counted**, regardless of whether they were placed by the local machine or N100. For strategy evaluation, the history must be split by run/config/source because the first days used different logic.

Use these labels:

- **Local early live**: real orders submitted from local `pm_agent`; these count in wallet/live PnL.
- **N100 legacy live**: real orders submitted from N100 before order metadata and `city_pool` guardrails were fully clean.
- **N100 current live**: real orders submitted from N100 after the active rollout config was fixed.

Do not describe local early live as "not ours". It is ours, just not part of the clean current N100 T1 strategy identity.

### 1.1 取代说明：早期 5 月"盈利模式"结论已作废（2026-06-19）

AGENTS.md / CLAUDE.md 早期"已知的盈利模式"段（BUY_NO 胜率 76% vs YES 12%、Warsaw ROI +52.9%、
ECMWF +12% vs GFS +1.4%、LA 经常 missing_bracket）是 **near-binary settlement 修复前**的口径，已作废，
2026-06-19 从常驻文件删除，不再作为现行结论。当前权威结论见评估层 living docs：
`analysis/side_alpha.md`（胜率 ≠ alpha）、`analysis/city_selection.md`（pre-fix 城市 ROI `invalidated-numbers`）、
`analysis/model_vs_market.md`（global probability alpha 为负）。`missing_bracket` 本身是 near-binary bug，
已 725→0 修复，不是 LA 数据问题。

注意：作废的是**修复前的具体数字**，不是把 BUY_NO / ECMWF 这些方向判死——它们当前是 `unconfirmed`（未确认）
而非 `disproven`（已否定），各策略当前状态见 [WEATHER_STRATEGY_REGISTRY.md](WEATHER_STRATEGY_REGISTRY.md)。

## 2. Confirmed Live Rollout Timeline

Source files audited:

- Local: `runtime/weather_edge_v1/live/live_*_orders.jsonl`
- N100 mirror: `runtime/weather_edge_v1/remote_pm_agent/live/live_*_orders.jsonl`
- N100 cycle summaries: `runtime/weather_edge_v1/remote_pm_agent/live_cycle/*.json`

### 2.1 Order Source Summary

| Target date | Source | Confirmed behavior | Submitted/orders | Cities | Planned notional | Interpretation |
|---|---|---:|---:|---:|---:|---|
| 2026-05-14 | local live | repeated live submissions across cycles | 147 / 239 | 7 | $914.40 | Day 1 process invalid for clean strategy eval; duplicate exposure risk existed. |
| 2026-05-15 | local live | broad/non-T1 city universe | 29 / 33 | 21 | $128.70 | Day 2 city universe was wrong. |
| 2026-05-16 | local live | broad Asia/non-T1 universe including Wuhan | 14 / 15 | 13 | $58.50 | This is the Wuhan / all-city-like error bucket. |
| 2026-05-16 | N100 live | early N100 run, partial/dirty metadata | 17 / 18 | 9 | $90.00 | N100 started live for target 2026-05-16; not yet fully clean metadata. |
| 2026-05-17 | N100 live | current T1 rollout, $5 notional | 17 / 18 | 10 | $90.00 | Current version family; use for ongoing live monitoring. |
| 2026-06-25 | N100 live | regime-routed NO feature-parity + duplicate-risk incident | 2 / 2 | 1 | $6.38 | Invalid clean strategy sample; live runner used a simplified feature path vs historical atlas and allowed repeated NYC/date/token exposure. |

Notes:

- `Submitted/orders` counts source order rows, not actual filled shares.
- Planned notional is source order intent, not final matched cost.
- Real fills can be partial or unfilled; final PnL must use CLOB fills / account history.

### 2.2 Active Current Config

Latest mirrored N100 cycle inspected: `runtime/weather_edge_v1/remote_pm_agent/live_cycle/20260517T063649Z.json`

```text
city_pool = t1_trading
sizing_mode = notional
max_order_notional = 5.00
max_order_shares = 25.00
fixed_order_shares = 10.00   # configurable alternative, not active
entry window = 0.25 <= price < 0.75
min_edge = 0.10
contract_alerts = []
```

N100 pause status at audit time:

```text
paused = false
```

## 3. Known Mistakes and How To Label Them

### Incident A: Day 1 Duplicate / Over-Submission Risk

Target date: `2026-05-14`

What happened:

- Local live execution submitted many orders over repeated cycles.
- Cross-cycle de-duplication / open-order awareness was insufficient.
- This day may have positive account PnL, but it is not a clean strategy sample.

Label:

```text
run_family = weather_edge_v1_live_local_early_duplicate_risk
run_quality = invalid_process_duplicate_risk
execution_host = local_pm_agent
strategy_state = live_debug
```

### Incident B: City Universe Was Too Broad

Target dates: `2026-05-15` and `2026-05-16`

What happened:

- Local live signal building used a broad/full snapshot pool instead of strict `city_pool=t1_trading`.
- Confirmed non-T1 examples include Wuhan and other broad Asia cities.
- These orders are real live trades and must remain in wallet PnL, but must not be mixed with current T1 strategy evaluation.

Label:

```text
run_family = weather_edge_v1_live_local_early_wrong_universe
run_quality = invalid_process_wrong_universe
execution_host = local_pm_agent
intended_city_pool = t1_trading
actual_city_pool = broad_or_unknown
```

### Incident C: N100 Legacy Metadata Was Not Clean

Target date: `2026-05-16`

### Incident D: Regime-Routed NO Live/Backtest Feature-Parity Break

Target date: `2026-06-25`

What happened:

- `regime_routed_no_soft_balanced_tiny_live_v1` was started as a tiny-live probe from the
  current-bracket NO / regime-routed research line.
- The research atlas rows used PIT trend, humidity/cloud/dewpoint, wind, and running-max
  freshness features with high historical coverage.
- The live runner initially only consumed current/running temperature, GFS forecast max/peak,
  and market ask/capacity; it filled the atlas mechanism fields as unknown, then treated
  unknown as a small size discount rather than a live veto.
- The signal id included `decision_snapshot_ts_utc`, so a later snapshot produced a new
  signal for the same NYC `82-83` NO token. The executor deduped by signal id, not by
  `city + target_date + token_id`, and the runner's daily cap check did not count prior
  orders when `--target-date` was omitted.

Observed live exposure:

```text
strategy_instance = regime_routed_no_soft_balanced_tiny_live_v1
city = NYC
target_date = 2026-06-25
token/bracket = 82-83 NO
orders = 2
posted_notional ≈ $6.38
status = paused
```

Label:

```text
run_family = regime_routed_no_live_feature_parity_incident
run_quality = invalid_process_feature_parity_duplicate_risk
strategy_state = paused
```

### Incident E: Fast-Source Candidate-Bracket Confirmation Mismatch

Target date: `2026-07-14`

What happened:

- Busan AMOS `30.6C` mapped to the `30C NO` candidate while the METAR running max was `29C`.
- Persistent-cross state and thresholds were incorrectly keyed to the METAR running max, so prior
  `29.5C+` observations counted toward confirmation of the higher `30C NO` bracket.
- The first FOK attempt at `09:59:39 KST` was definitively rejected as not fully fillable; the next
  runner-cycle retry filled at `10:00:29 KST`, about 50 seconds later and at a worse ask.

Observed live exposure:

```text
strategy_instance = fast_source_prev_no_trial_v1
city = Busan
target_date = 2026-07-14
token/bracket = 30C NO
matched_cost = $8.899998
matched_shares = 10.348835
order_id = 0x65ef26d51e094452cd75a9b4feb3f2c1a8d1756b46def53f8e1aa9fad20f444d
```

Correction:

- Version persistent confirmation by `city + date + source + candidate NO bracket`.
- Require two distinct observations at `candidate + 0.5C` or above, with the latest at
  `candidate + 0.7C` or above.
- Retry only definitive FOK-unfilled rejections immediately after refreshing the live book;
  do not retry ambiguous transport failures that could duplicate an accepted order.

Label:

```text
run_family = fast_source_prev_no_trial_candidate_basis_incident
run_quality = invalid_process_confirmation_basis_mismatch
strategy_state = live_corrected_after_2026-07-14
```

### Incident F: Fast-Source Execution Sizing And Shared-Policy Audit

Audit date: `2026-07-14`

Reproduction:

```bash
.venv/bin/python scripts/analysis/market_structure_edge/audit_fast_source_live_chain_v1.py
```

Measured impact before the correction:

```text
submitted orders = 13
intended shares = 95.000000
actual matched shares = 111.562142
orders above intended shares = 12
excess matched shares = 16.562142
max actual/intended ratio = 2.764706
stale (>15m) cross-candidate telemetry rows = 1127
stale (>15m) submitted orders = 0
```

Root causes:

- FOK BUY orders used `best ask + cushion`, so the signed USDC maker amount bought more shares
  whenever execution occurred below the limit. The per-market cap counted requested `size`, not
  the exchange `takingAmount`.
- Observation freshness was emitted but the blocker checked detection age only.
- One global Asia/Shanghai target date and a local arithmetic-round helper bypassed the shared
  city calendar and source profile.
- The shared latest-source selector preferred the highest temperature instead of the newest
  observation and could merge two source feeds for one city.
- The HKO dedicated runner still used obsolete market-index call signatures and its own older
  FOK implementation; it would fail after restart.

Correction:

- Submit at the freshly observed best ask, retry only definitive FOK-unfilled responses, require
  a matched response, persist actual fill shares/cost, and enforce later cap checks from actual fills.
- Check observation age, detection age, observation-to-detection lag, monotonic observation time,
  city-local target date, and the exact source bound by city policy.
- Keep exact-C/METAR signal flow generic; keep HKO official floor semantics in a dedicated signal
  runner; share book fetch, FOK retry, match validation, and cap accounting between both.

Label:

```text
run_family = fast_source_prev_no_live_chain_pre_v2
run_quality = invalid_process_execution_sizing_and_freshness
contaminated_window_end = 2026-07-14T02:00:24Z
```

### Incident G: Lower-Bound Market Containment Produced False Locks

Target date: `2026-07-14`

What happened:

- Helsinki's `20C or below` market was returned by containment lookup for candidate values `18C`
  and `19C`.
- The runner consequently emitted three false lock candidates for `18 -> 19` and `19 -> 20`.
  Crossing either value does not guarantee that `20C or below NO` wins; only a `20 -> 21` crossing
  locks that lower-bound market.
- All three false candidates had NO asks above the configured cap (`0.990`, `0.993`, `0.990`).
  They produced zero order rows, zero submissions, and zero fills.

Correction:

- A previous-NO candidate may use an exact or ranged market only at that market's upper boundary.
- Top buckets are never lockable by a higher-temperature crossing.
- Exclude the three Helsinki events from cross-precision denominators.

Label:

```text
run_family = fast_source_prev_no_lower_bound_containment_incident
run_quality = invalid_signal_market_semantics
contaminated_window = 2026-07-14T08:32:17Z..2026-07-14T09:32:04Z
affected_events = 3
affected_orders = 0
```

### Incident J: JRS External-Volume Runtime Host Permission Loss

Incident date: `2026-07-16`

Root cause:

- JRS-consuming runners were split across `screen`, the default tmux server, LaunchAgent, and the
  `weather-jrs` tmux server. macOS external-volume permission remained reliable only for processes
  spawned by the `weather-jrs` server.
- A live child could therefore remain present while its upstream summary stopped advancing or its
  loop emitted `Operation not permitted` / `no_snapshot`.

Measured impact (UTC):

| Runtime | Contaminated window | Decision impact |
|---|---|---|
| `fast_source_prev_no_trial_v1` | `04:45:34..12:32:13` | Four counterfactual submission opportunities were not attempted: Tokyo 31 NO, Busan 33 NO, Singapore 32 NO, Tokyo 32 NO. These are not guaranteed fills. Two Busan attempts immediately before the outage were rejected because a post-only order crossed the book. |
| `d1_yes_high_mid_live_v1` | `08:29:53..12:52:14` | The recovered Ankara 30 YES signal depended on a new `12:49:10` observation and matched 5 shares at `12:52:16` for `0.84`; permanent missed fills confirmed from this incident: `0`. |
| `current_yes_heat_death_shadow_v1` | `04:39:14..12:52:31` | `8h13m` signal-production coverage gap. Do not treat missing decisions in this window as negative examples. Before the gap, four Wellington strong rows were non-executable at asks `0.997/0.999`; after recovery, one Jeddah strong row had no ask. |
| `low_price_yes_lottery_shadow_v1` | `07:47:06..13:02:45` | Shadow-only coverage loss; live orders affected: `0`. |

Correction at the time (superseded by Incident K below):

- Rehost all active JRS consumers and the fast-source patrol on `tmux -L weather-jrs`.
- Monitor the current signal producer and both current-YES live heads, not only their child-process
  heartbeats.
- Keep dormant live instances out of expected-live monitoring and monitor their active shadows.

Label:

```text
run_family = weather_jrs_runtime_host_permission_incident
run_quality = coverage_gap_external_volume_permission
affected_live_fills_confirmed_missed = 0
current_yes_exclusion_window = 2026-07-16T04:39:14Z..2026-07-16T12:52:31Z
fix_commits = e834311c,7182a13d,82d90bd
```

### Incident K: Fast-observation collector left on superseded JRS tmux context

Incident date: `2026-07-18`

Root cause and launch lineage:

- `start_mac_weather_fast_observations_jrs_tmux.sh` was introduced by commit `1fe52ea7`
  (`Add scheduled fast observation polling`, 2026-07-10) with default socket `weather-jrs`.
- The main data-feed moved to the working `weather-data-feed-jrs` socket in commit `aba685ef`
  on 2026-07-11, but the independent fast-observation launcher was not migrated.
- The currently failed `weather_fast_obs_jrs` session was recreated at
  `2026-07-14T14:56:14Z` by the `2d7368b7` task (`fix(weather): isolate and capture lowest markets`)
  on the old `weather-jrs` server. Its parent tmux server dated to `2026-07-08T15:58:29Z`.
- Incident J's 2026-07-16 correction standardized several strategy/patrol entries on
  `weather-jrs`, contradicting the 2026-07-11 data-feed correction and leaving two active standards.

Measured impact:

```text
first_observed_permission_error_utc = 2026-07-10T15:49:55Z (intermittent)
last_successful_fast_source_state_utc = 2026-07-17T21:19:59Z
continuous_coverage_gap_start_utc = 2026-07-17T21:19:59Z
affected_live_cities = Busan,Helsinki,Singapore,Tokyo
observed_cross_events_at_diagnosis = 0
observed_orders_at_diagnosis = 0
observed_fills_at_diagnosis = 0
failed_child_cycles_seen_at_diagnosis = 4741
counterfactual_missed_signals = unobservable_without_the_missing_source_prints
```

Patrol result:

- `weather_live_runtime_patrol` sent Telegram CRITICAL message `8512` at
  `2026-07-17T21:21:06Z`, but it only reported runner missing/latest stale.
- The patrol did not inspect high-frequency producer state freshness or child return codes, so it
  could not distinguish an upstream collector outage from an execution-runner outage or prevent a
  false recovery when the runner returned while the source remained stale.
- `weather_data_feed_prod_health_check.py` checked snapshot/orderbook/forecast products but did not
  include the independent high-frequency observation state.

Correction:

- One canonical JRS process context: `tmux -L weather-data-feed-jrs`, resolved by
  `scripts/ops/weather_jrs_tmux_env.sh`; all JRS startup entries must use it.
- Every core JRS launcher performs a write probe from inside the target tmux server before starting.
- A non-zero runway/high-frequency child exit terminates the collector loop instead of being swallowed.
- Production health and live patrol include high-frequency producer state freshness.
- Production was restored after explicit approval. The old `weather-jrs`, `weather-full-ladder`,
  default-tmux, screen, and LaunchAgent JRS entries were stopped; all active JRS consumers were
  recreated on the canonical server from the clean deploy checkout.

Recovery and post-restore live-action audit:

```text
fast_observation_recovered_at_utc = 2026-07-18T05:32:10.937771Z
fast_observation_continuous_gap = 2026-07-17T21:19:59.941052Z..2026-07-18T05:32:10.937771Z (8h12m11s)
d1_live_cycle_recovered_at_utc = 2026-07-18T05:35:23Z
d1_live_cycle_gap = 2026-07-17T21:19:50Z..2026-07-18T05:35:23Z (8h15m33s)
canonical_tmux_sessions = 19 sessions / 20 panes, pane_dead=0
legacy_jrs_process_contexts_remaining = 0
post_restore_submit_failures = 0
post_restore_live_actions = 6 submitted child orders across 3 signals: 5 matched, 1 canceled unfilled
cross_busan_2026-07-18 = 10 NO matched @ 0.818 + 5 NO maker matched @ 0.817; cost $12.265
heat_h1_tokyo_2026-07-18 = 5 YES matched @ 0.973 + 5 YES maker canceled @ 0.942 + 5 YES replacement matched @ 0.973; fill cost $9.73
heat_h2_kualalumpur_2026-07-18 = 5 YES matched @ 0.92; cost $4.60
post_restore_total_fills = 30 shares; cash cost $26.595
full_ladder_first_complete_after_restore = snapshot_20260718_1332.json, rc=0
focused_chain_tests = 134 passed
canonical_fill_reconciled = 30 shares / $26.595 across 5 fills
canonical_fill_gate = pass; missing_order_rows=0; over_order_keys=0; db_vs_cache_delta=0
fix_commits = fcc84094,1bda3832,74b1f98f,aae767bc,271aac9,32d474c,9a88e7c
```

The Busan, Tokyo, and Kuala Lumpur orders were ordinary pre-existing live-policy signals after data
recovery, not deployment test orders. Busan respected the `10 taker + 5 maker` per-market share cap;
Tokyo respected H1's `5 taker + 5 maker` cap, with the initial maker canceled before replacement;
Kuala Lumpur respected the H2 fixed `5 shares` cap. No order was submitted for Atlanta, Miami, or
San Francisco after their downgrade to zero-notional shadow.

Resolution / restored probe:

```text
restored_at = 2026-06-26
restored_commit = 0c3a38e5
strategy_state = tiny_live_forward_probe
```

The restored runner defaults to the shared `weather_data_feed` observation cache
instead of strategy-local live METAR feature fetches.  It also requires live
feature parity, blocks duplicate `(city,target_date,token_id)` exposure, counts
the daily cap by actual target date, and records snapshot / observation-cache
freshness in the runtime summary.  This repairs the live/backtest parity process
issue; it does not promote the strategy to a confirmed edge.

Required before any restore:

- Live feature builder must compute the same mechanism fields used by the replay layer:
  1h/3h temperature trend, humidity/cloud/dewpoint context, wind, and minutes since running max.
- Unknown core mechanism fields must be a live veto, not a soft discount.
- Live dedup must block repeated `city + target_date + token_id` exposure across cycles.
- Daily cap must be keyed by actual order `target_date` when no CLI target date is supplied.
- A parity replay and deploy review must pass before the instance can leave `paused`.

What happened:

- N100 began live execution after local environment issues.
- Early N100 order rows include missing `city_pool` metadata and were not yet the fully guarded current path.
- These are real N100 live trades, but not the same clean run identity as the current config.

Label:

```text
run_family = weather_edge_v1_live_n100_legacy
run_quality = legacy_metadata_or_universe_guard_incomplete
execution_host = n100_pm_agent
```

### Incident D: Current $5 Notional T1 Rollout

Target date: `2026-05-17` onward, until the next config/code change.

What changed:

- Live cycle passes `--city-pool t1_trading`.
- Sizing is explicit and configurable: active mode is `$5` fixed notional; fixed shares remains a strategy option.
- Live cycle summaries emit `contract_alerts`.
- The old misleading cumulative `max_position` interpretation was replaced by per-order `max_order_shares` in the current path.

Label:

```text
run_family = weather_edge_v1_live_n100_t1_25_75_notional_5
run_quality = active_candidate
execution_host = n100_pm_agent
actual_city_pool = t1_trading
sizing_mode = notional
max_order_notional = 5.00
entry_price_window = 0.25-0.75
```

### Incident H: ECMWF Cities Silently Fell Back To GFS

Target dates: `2026-07-02` .. `2026-07-05` (fixed at ECMWF collection restore ~`2026-07-06`).

What happened:

- Mac cache was missing `ecmwf_v4_*` forecast files, so the per-city fixed-model pipeline
  (`CITY_MODEL`, see `weather_data_feed_service/legacy_weather_predict/paper_snapshot.py`)
  **silently fell back to GFS** for cities that should run ECMWF. First flagged as P0 in
  [2026-07-05-heada-review-work-order-v1.md](analysis/2026-07/2026-07-05-heada-review-work-order-v1.md).
- The fallback was not an alert; signals were built on the wrong forecast source for three days.
- `signals.forecast_source` / `signals.model_version` faithfully record `open_meteo_live_gfs` / `gfs`.
  **These rows are NOT corrupt — they are an accurate record of what actually traded.** Do not rewrite
  `model_version` to `ecmwf`; that would falsify a real live record.

Affected canonical rows (`runtime/weather.db` `fact_trades`, all `trade_class=live_real`, 16 rows):

```text
2026-07-03  Ankara(1)  Dallas(1)  London(3)
2026-07-04  Busan(2)   Helsinki(2)
2026-07-05  Helsinki(3) Lucknow(4)
```

How to label / handle:

```text
contamination = ecmwf_silent_gfs_fallback
window = 2026-07-02..2026-07-05
affected_cities = Ankara, Dallas, London, Busan, Helsinki, Lucknow  # ECMWF-designated per CITY_MODEL
data_status = faithful_record   # do not rewrite model_version
research_status = excluded_from_ecmwf_by_model_slices
```

- Any `by_model` ECMWF slice, forecast-source A/B, or forward evidence keyed on these city-days must
  exclude or flag them — they are GFS forecasts, not ECMWF, despite these being ECMWF-designated cities.
- Root cause (silent fallback) is fixed going forward: fallback must alert, per CLAUDE.md §2 and the
  work-order P0. This window remains in the DB as an honest live record, tagged here, not deleted.

### Incident I: D1 High-Mid Shadow Consumed Growing Full-Ladder Files

Window: `2026-07-15T06:57:27Z` .. `2026-07-15T07:37:29Z`.

What happened:

- The dedicated `snapshot-full` collector appends token books directly to its final `.jsonl.gz` while
  a pass is running. The first version of `d1_yes_high_mid_shadow_v1` selected that newest file by
  mtime, so three cycles consumed incomplete city coverage (`1`, `15`, and `15` observed city-books).
- One promotion-track event was emitted: Beijing `2026-07-15`, d1 bracket `38`, YES ask `0.938`,
  mid `0.9165`, book age about `3m`, observation age `35.16m`. The row itself had a fresh valid quote;
  the defect is its incomplete cross-city evidence denominator, not its per-row market lineage.
- This instance is `zero_notional_shadow`: submitted orders=`0`, fills=`0`, cash impact=`$0`.

How to label / handle:

```text
contamination = partial_full_ladder_snapshot
affected_cycle_count = 3
affected_selected_events = 1  # Beijing 2026-07-15 d1 YES
financial_impact_usd = 0
research_status = coverage_gap_exclude_from_full_coverage_cohort
```

- Preserve the raw journal and Beijing paper position. Do not call omitted cities `no_signal`; the
  source file was still growing, so missing cities are coverage gaps.
- Fixed in `7319b84`: full-ladder files require a matching completed `paper_snapshots/snapshot_*.json`
  marker, missing/stale obs and quotes fail closed, and monitor heartbeat/snapshot fields are explicit.
- Coverage accounting was corrected in `446c6e5`: a completed target-date book universe of at least
  36 cities is `full_ladder`; observed-book intersection and usable d1 quotes are reported separately.

## 4. Current PnL Interpretation

Do not use only current wallet positions to evaluate historical performance. That misses closed positions and confuses realized vs open PnL.

Correct order of truth:

1. **CLOB fills by order ID**: best source for actual filled shares and fill prices.
2. **Polymarket current + closed positions**: best account-level reconciliation source.
3. **Live submitted JSONL**: intent/source lineage, not final fill truth.
4. **Paper ledger / snapshot replay**: research comparisons only, not account PnL.

Manual audit snapshot from 2026-05-17 found:

| Target date | Broad account/current+closed view | Interpretation |
|---|---:|---|
| 2026-05-15 | about `-$6.06` | Mostly local early broad-city live. |
| 2026-05-16 | about `-$15.34` | Worst early live date; broad/wrong-universe bucket was the main loss driver. |
| 2026-05-16 N100 actual fills | about `-$1.45` | N100 live for that target date was slightly negative, not the main broad-city loss. |
| 2026-05-17 | positive mark during audit, not final settlement | Current N100 T1 rollout; do not call final before settlement. |

These numbers should be treated as an audit note until a repeatable reconciliation script writes a versioned report. The classification above is the important part: **wallet PnL counts all real orders; strategy PnL must split by run/config/source.**

## 5. Historical Data Backfill Plan

The dashboard and research DB should ingest early live data as multiple live runs, not as one merged strategy.

### 5.1 Required Source Inventory

| Source | Path / API | Purpose |
|---|---|---|
| Local live orders | `runtime/weather_edge_v1/live/live_*_orders.jsonl` | Early local submitted order intent and metadata. |
| N100 live orders | `runtime/weather_edge_v1/remote_pm_agent/live/live_*_orders.jsonl` | N100 submitted order intent and metadata. |
| N100 live cycles | `runtime/weather_edge_v1/remote_pm_agent/live_cycle/*.json` | Cycle config, alerts, dedup, status. |
| CLOB fills | Polymarket CLOB trade API by maker/funder and order ID | Actual matched shares / fill prices. |
| Account positions | Polymarket data API positions + closed positions | Account reconciliation and realized/marked PnL. |
| Settlement | N100 `weather-predict/cache/pm_history/{City}_{date}.json` | Final winning bracket / final price. |

### 5.2 Backfill Run Families

Create separate `runs` entries:

| Run family | Execution host | Target dates | State | Why separate |
|---|---|---|---|---|
| `weather_edge_v1_live_local_early_duplicate_risk` | local | 2026-05-14 | retired | Duplicate/over-submission risk. |
| `weather_edge_v1_live_local_early_wrong_universe` | local | 2026-05-15 to 2026-05-16 | retired | City universe was wrong. |
| `weather_edge_v1_live_n100_legacy` | n100 | 2026-05-16 | retired or debug | Metadata/guardrails not fully clean. |
| `weather_edge_v1_live_n100_t1_25_75_notional_5` | n100 | 2026-05-17 onward | live | Current active rollout. |

Do not merge these into a single "weather live" run except at the portfolio/account layer.

### 5.3 Required Columns / Tags

Every live row should carry:

```text
execution_host        local_pm_agent | n100_pm_agent
source_path           original jsonl/cycle file
source_row_hash       canonical row hash
run_family            see table above
run_quality           active_candidate | invalid_process_duplicate_risk | invalid_process_wrong_universe | legacy_metadata_or_universe_guard_incomplete
intended_city_pool    t1_trading
actual_city_pool      t1_trading | broad_or_unknown | missing
sizing_mode           notional | fixed_shares | unknown
max_order_notional    decimal string
fixed_order_shares    decimal string
entry_price_window    0.25-0.75
external_order_id     Polymarket order id
fill_source           clob_trades | data_api_positions | none
```

If an old row lacks the field, do not invent precision. Use `unknown` / `missing` and attach `run_quality`.

### 5.4 Reconciliation Rules

1. Use submitted JSONL to identify intended order lineage.
2. Join CLOB fills by Polymarket `orderID`.
3. Aggregate fills into positions by `(target_date, city, bracket, side, token_id)`.
4. Join settlement by `token_id` first, then by `(city, target_date, bracket)` as fallback.
5. Compare reconstructed positions against Data API current + closed positions.
6. Emit discrepancy rows for submitted-but-no-fill, fill-without-source-order, unexplained account position, and mismatched city/date/bracket parsing.

For historical analysis, show both:

```text
account_live_pnl = all real filled weather orders in wallet
strategy_clean_pnl = only rows matching the chosen run_family/config
```

## 6. Future Design

### 6.1 Live Must Be Treated As Experiment Runs

Every config/code/universe change starts a new live run identity. At minimum:

```text
run_id = hash(config_id + code_version + universe_id + execution_host + start_ts)
```

Changing any of these starts a new run:

- city pool / universe
- sizing mode or notional amount
- entry price window
- min edge
- maker policy
- dedup / retry / catch-up semantics
- execution host
- code version

### 6.2 Execution Host Is Part Of Lineage

`execution_host` is not cosmetic. Local and N100 runs can differ in environment variables, proxy/network path, package versions, live pause state, scheduler cadence, local files, and deployment version. It must be stored and visible in dashboard filters.

### 6.3 Incident Notes Are Data, Not Chat Context

Each live run should have append-only `run_state_log` / `run_alerts` rows:

```text
paused
resumed
config_changed
deployed
doctor_failed
doctor_passed
contract_alert
manual_audit_note
```

Telegram alerts are useful operationally, but the dashboard/research DB needs durable structured records.

### 6.4 Backfill Is Allowed, Rewriting Source Is Not

For early live cleanup:

- Do not edit historical JSONL source files.
- Add derived classification in DB/report artifacts.
- If a source row is wrong or missing metadata, preserve it and add a correction/classification table.
- Mark invalid process runs as `retired`, not deleted.

### 6.5 Fast-Source Common And City-Specific Boundaries

The reusable layers are deliberately narrow:

```text
source profile  -> station/unit/rounding/settlement-basis/calibration
city policy     -> strategy handler/mode/thresholds/shares/caps
common runner   -> local date/freshness/METAR clock/market lookup/book checks
common executor -> exact-best-ask FOK/retry/match proof/actual-fill cap
```

Busan, Helsinki, Singapore, Tokyo, Seoul, Ankara, Istanbul, and Tel Aviv use the generic
`metar_prev_no_exact` handler. Hong Kong does not: HKO Daily Extract and floor semantics belong
to `weather_hko_official_tminus1_no_live.py`, while its order execution still uses the common
executor. US Fahrenheit range markets and lowest-temperature strategies likewise stay out of
the exact-C handler and use separate signal handlers rather than city conditionals in the runner.

### 6.6 2026-07-16 Runtime/Analysis Freshness Separation

Pollution window: `fact_signal_candidates.decision_snapshot_ts_utc` stopped at
`2026-07-14T06:36:45Z` until the 2026-07-16 incremental repair. During the same
window, the local orderbook mirror stopped at 2026-07-14 and
`settlement_outcomes` stopped at target date 2026-07-14. Reports generated from
the canonical DB in that window must be treated as stale analysis evidence.

Repair evidence:

- candidate facts: 54,054 rows before, 55,889 after; latest decision snapshot
  `2026-07-16T13:58:38Z`; 2,429 rows in the replaced `event_date >= 2026-07-15`
  partition;
- settlement outcomes: 33,992 before, 34,509 after, adding 517 bracket outcomes
  across 47 city-days for 2026-07-15;
- orderbook mirror: restored through 2026-07-16;
- obsolete static-CSV tmax loops: 1,541 replay events and 1,128 candidate cycles,
  both zero-notional, stopped and marked `stale + paused`.

Decision impact: no live order was created or suppressed by these warnings.
Current live/shadow runners consume production raw snapshots and journals, not
`fact_signal_candidates`; the stale window affected dashboard/research freshness
only. Runtime health and analysis freshness now have separate read-only monitors,
and recent fact repair uses an explicit event-date partition instead of a full DB
rebuild.

### 6.7 2026-07-16 Fast-Source Post-Only Repricing Gap

Pollution window: `2026-07-16T04:45:21Z..04:45:31Z`. One unique Busan `32 NO`
signal generated two 10-share post-only submissions. Both were rejected with
`invalid post-only order: order crosses book` after the observed ask moved from
`0.67` to `0.64`; accepted orders and fills were both zero.

The settlement-facing WU RKPK history later recorded `33 C` at 14:00 local and
`35 C` at 15:00 local, while the AWC METAR mirror had no routine rows at those
hours. The WU daily maximum was `35 C`, so `32 NO` was the correct expression.
At the first observed ask, the missed 10-share fill had approximately `$3.30`
gross settlement profit; this is counterfactual because no exchange fill exists.

Correction: crossing rejects now trigger at most two immediate fresh-book maker
reprices. Each retry remains exact-share, post-only, below the current ask, and
stops when the ask exceeds the city cap or top-level depth is insufficient.
Direct taker fallback remains disabled because the previous FOK BUY path did not
provide a reliable hard share cap.

### 6.8 2026-07-16 AWC Multi-Report Persistence Gap

Pollution window for the Busan incident: the source-event collector had no run
between `2026-07-16T04:44:38Z` and `06:54:13Z`. Its first successful AWC response
after recovery contained the missing `05:00Z` and `06:00Z` RKPK routine METARs
plus the newer `06:29Z` SPECI, but the old persistence path stored only the last
record in the response. The two routine rows were therefore absent from the
local journal even though AWC retained and later returned them.

Correction: AWC source-event collection now indexes every report timestamp in
the returned multi-record payload and appends previously unseen rows as
`first_seen_type=late_backfill`. These rows also carry
`original_first_seen_unknown=true`, so they restore the official temperature
path but are excluded from first-arrival latency evidence. The one-time upgrade
recovered 939 omitted AWC report rows across 46 configured cities; a targeted
Busan reconciliation then restored `05:00Z 33 C` and `06:00Z 35 C`. The compact
report index is persisted in collector state, preventing repeated journal scans
or duplicate backfills. This was an incremental journal repair; no canonical DB
or existing raw file was rebuilt.

### 6.10 2026-07-17 Atlanta MADISHF/OMO Terminal False Cross

Pollution window: the Atlanta `2026-07-17` previous-bracket-NO decision initiated
from the `17:30Z` MADISHF/OMO observation through settlement. The fast source
reported `91.4F` across distinct observations while the prior market bracket was
`88-89`; the routine METAR and native-F WU final maximum were `89F`, so the
winning bracket remained `88-89`. Direct NOAA MADIS contains the identical
observation, received about 134 seconds after observation time, with
`temperatureQCR=0`; neither faster delivery nor the exposed QC flag removes the
source-to-settlement basis failure.

Decision impact: the runner filled `10` shares of `88-89 NO` for `$8.70`; the
expression lost at settlement. Across the fixed US runner denominator there
were `22` correct settled candidates and `1` false candidate: `0/22` correct
candidates became executable under the active policy within ten minutes, while
the false Atlanta candidate became executable and filled. This is both a basis
failure and an adverse-selection failure, not merely one bad temperature row.

Governance consequence: every OMO/MADISHF/Synoptic-1m, airport-fast,
source-event cross, and previous-bracket-NO analysis must report (a)
`terminal_false_cross`, (b) same-timestamp source→routine METAR→WU native-F
basis, and (c) correct-versus-false fresh executable/fill denominators. Raw
crosses remain probabilistic features; persistence and `temperatureQCR=0` do
not authorize a deterministic live expression. The affected US cities remain
shadow; the underlying collectors and evidence are retained.

## 7. Immediate Follow-Up Work

1. Implement a repeatable live reconciliation report:
   - input: local + N100 live JSONL, CLOB fills, Data API positions/closed positions, pm_history
   - output: per target date / run family / city / side PnL, fill rate, unmatched rows
2. Add live ingest support to dashboard:
   - orders/fills from live JSONL + CLOB
   - run alerts from live_cycle JSON
   - source/run quality tags
3. Add dashboard filters:
   - `execution_host`
   - `run_family`
   - `run_quality`
   - `actual_city_pool`
   - `sizing_mode`
4. Add daily automated reconciliation:
   - alert when source order count, fill count, and account position count diverge beyond expected partial-fill cases
5. Keep local live execution stopped unless explicitly requested.
