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

### 6.9 2026-07-17 Observation Reuse Freshness Semantics

Possible pollution window: `2026-06-20..2026-07-17`. During a transient fetch
failure, `weather_data_feed_service/observations.py` copied the previous good
cache row without changing `status=ok`, `age_min`, or the observation clocks.
If this branch fired, a consumer could interpret a stale observation as a new
successful refresh.

Impact audit: the current 40-city cache contains `0` reused rows, and the 6,172
retained current-YES shadow decisions contain `0` observations with known
availability later than decision time. The cache is a replace-in-place latest
artifact and historical reuse rows were not journaled, so the number of past
branch activations and a per-row counterfactual list cannot be reconstructed;
research covering this window must not infer fetch health from legacy
`status=ok` alone.

Correction: reused facts now carry `status=reused_after_fetch_error`, retain the
last successful fetch timestamp, record the failed refresh status/error, and
recompute `age_min` plus running-max clocks at reuse time. Shared frame builders
also exclude observations with known availability later than `as_of_ts_utc`.
No new strategy gate was added.

### 6.10 2026-07-17 Atlanta MADISHF/OMO Terminal False Cross

Pollution window: the Atlanta `2026-07-17` previous-bracket-NO decision initiated
from the `17:30Z` MADISHF/OMO observation through settlement. The fast source
reported `91.4F` across distinct observations while the prior market bracket was
`88-89`; the routine METAR and native-F WU final maximum were `89F`, so the
winning bracket remained `88-89`. Direct NOAA MADIS contains the identical
observation, received about 134 seconds after observation time, with
`temperatureQCR=0`; neither faster delivery nor the exposed QC flag removes the
source-to-settlement basis failure.

Decision impact: the runner filled `15` shares of `88-89 NO`: `10 @0.87` taker
plus a `5 @0.86` maker child that filled 45 seconds later. Principal was
`$13.00`, verified fee was `$0.05655`, and realized loss was `$13.05655`; the
expression lost at settlement. The earlier `10 shares / $8.70` figure was a
submission-journal undercount because the maker child was still live when that
row was written. Across the fixed US runner denominator there
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

### 6.11 2026-07-18/19 Observation Running-Max Continuity Failure

Pollution window: `2026-07-18T19:33:45Z..2026-07-19T10:17:30Z`. When a primary
observation fetch failed, `aviationweather_cache_csv` often supplied only the
latest METAR. The cache builder recomputed the day-to-date running maximum from
that truncated payload, so a temperature already observed earlier in the same
station-day could disappear. The same invariant also applies to rolling-window
sources whose returned history later drops an older high.

Impact replay over the retained observation journal found 179 polluted cache
rows across 57 city-days / 40 cities. Five `d1_yes_high_mid` promotion signals
consumed a regressed maximum: Taipei was zero-notional; Singapore, Beijing,
Busan, and Chongqing produced 25 filled YES shares with `$24.935` canonical fill cost.
Their correct d1 mids at the same book snapshots were respectively `0.0070`,
`0.0065`, `0.0015`, and `0.0040`, all below the frozen `0.80` threshold, so the
four live counterfactual decisions are all **no order**. These fills must remain
tagged as incident positions and excluded from d1 promotion PnL.

Correction: commit `88767fdc` (production equivalent `f81a3efb`) makes
`running_max_c` monotone within the same station and local date across source
failover, carries forward the prior high timestamp, and emits
`history_continuity_status=merged_previous_running_max`. Five focused observation
cache tests pass. The first post-fix production cache at
`2026-07-19T10:33:32Z` reported four actual continuity merges; the d1 loop then
returned to zero triggers. Detailed order-level replay is retained in the d1
strategy living report.

2026-07-20 hardening commits `cc109b02` and `e915a312` close the remaining composition gaps:
reused observations now advance freshness and retain an explicit
`reused_after_fetch_error` status; a reused row remains eligible as trusted
history for the next continuity merge; the live d1 consumer persists an
independent station-day monotonicity ledger and fails closed on regression; and
production health now checks the exact observation cache plus recent history;
the d1 runner also rejects a cache whose generated timestamp is older than ten
minutes, so a blocked single-loop collector cannot freeze a previously fresh
row indefinitely.
The production runner was restarted in a zero-trigger / zero-plan window and
its first cycle reported 40 invariant records, zero violations, and zero new
orders. Focused production/develop suites passed `66/66` and `70/70`.

Post-close result audit separates the incident expression from the corrected
strategy decision. Polymarket's closed events resolved the accidentally bought
current brackets Singapore `32`, Beijing `33`, Busan `29`, and Chongqing `35`
YES; canonical fill reconciliation gives 25 shares / `$24.935` cost / `$0.00096`
fees, implying `$0.06404` payout profit once settlement is ingested. The corrected
d1 brackets `33/34/30/36` all resolved NO. This does **not** vindicate the bad
signals: their corrected entry mids were below `0.01`, so the strategy
counterfactual remains four no-orders. Canonical `settlement_outcomes` had not
yet ingested 2026-07-19 at audit time; keep the positions tagged incident until
that bridge catches up.

## 7. 2026-07-29 CLOB Fee-Lineage Repair

Pollution window: `2026-07-18` through `2026-07-29`. The canonical gate found
12 effective fills with `legacy_unknown` fee lineage, including seven matched
taker fills recorded with zero fee. Exact public activity also contradicted two
older non-unknown fee rows. The repair wrote 14 append-only adjustments:
13 exact (`9` tx-exact and `4` maker-zero) and one explicitly labelled Weather
fee-curve estimate. No raw fill was updated or deleted.

Affected fill ids:

`c718a47bb0b49612cc862d84c5bb9f934f8188c4ca181e6920058aaf6150d1bd`,
`11d879dc7dd05f7c814b9e4e991408f2efeaba4d94b53176e78f77dbaf5eea3b`,
`28014f8e3fe2f5482832129c198695f6d11aa22858724d8109f9d734629eff20`,
`6054c631023b0e1f27af9e1258119d95d2d96959cc682d980c4c2d40127ea293`,
`efdd1cec6ac7bde96eb11d8f4bdbe0801fa928275070a915fa31b579fc5c2742`,
`e087b8fb0447be28dee2c9a51a26f1ece19f01ff2cd46a6a3d0471554cceb840`,
`90dcde61136db0e7f5751c1e979fca61c289bd18c995f9e83b6b4e387c385017`,
`d77c494fb8a4fcfc557515889cf179f385aa94bd3b1641a8775e1989df2cbc97`,
`a7d9e58fa2c67f2115825865f7f93310cc1c94a9b67d7c2417e8c15b734d4305`,
`cb50ad1249b4391f412b4d28957c69924f378c92c5c812295ba98c43e6aa78e6`,
`632b52b8341d0c0917ac20148e26a129609207d72518aa5acdb3afc82276bc78`,
`063c0fd4e53a1295364c68fa0556f9d7447668790c59fd697eb5b566b5bfa3e6`,
`d6f905d07abb476673ca7a229fbcf78dcea0b3a9a47b8858db8b73453c7b54f2`,
`afc0f36010f3861d6d1c19c63f8b8356b45bd71ecab22eb366fd0fc92949758e`.

The settled PnL delta is `-$0.06266`; unsettled rows retain fee-adjusted cost
without publishing realized PnL. After full fact replay the gate reports
`1321/1321` effective fills, `unknown=0`, matched-taker-zero-without-adjustment
`=0`, and zero DB/cache/fact cost mismatch. The normal five-minute canonical
entry then completed with incremental scope `14`, zero additional changes and
exit status `0`.

## 8. 2026-08-03/04 JRS Production Interruption

Pollution/coverage windows are recorded separately because the two failures had
different causes:

- JRS permission-context failure: the last successful source event was
  `2026-08-03T03:34:48Z`; failures began at `03:37:01Z` and the first recovered
  observation completed at `15:48:42Z` (`12h13m54s`). The last good targeted
  snapshot was `03:29:09Z`; the first recovered snapshot completed at
  `15:55:51Z` (`12h26m42s`). Treat this interval as collector coverage missing,
  not as strategy-filtered opportunity evidence.
- tmux server crash: macOS crash report
  `tmux-2026-08-04-002658.ips` proves `SIGSEGV` in
  `cmd_run_shell_callback -> cmd_run_shell_print` at
  `2026-08-03T16:26:57Z`. Source events resumed at `16:30:43Z`; the next full
  targeted snapshot completed at `16:35:54Z` (previous good snapshot
  `16:12:43Z`). The shared JRS helper no longer uses tmux `run-shell` for
  probes, mkdir, status bridges or prospective-host checks.

Impact replay: both live raw order files contain zero rows after
`2026-08-03T01:58:09.698036Z`, authenticated CLOB checks before and after
recovery both returned zero open orders, and canonical reconciliation found
zero incremental fills/cancellations/errors. Therefore this incident has
collector/signal coverage loss but zero evidenced wrong orders or recovery-
created fills. Post-repair targeted orderbook coverage was `163/163`, canonical
fill gate passed at `1342/1342` live-real fills with exact DB/fact cost
`$4022.454778`, and the canonical one-shot exited `0`.

KNMI quota exhaustion was a local notification-consumer amplification bug, not
an upstream outage or invalid credential. After the JRS recovery, MQTT QoS1
redeliveries were queued repeatedly before ack; each duplicate called the
Open Data file-URL endpoint before discovering `no_new_revision`, and every 403
was amplified to seven HTTP attempts by `--consistency-retries 6`. From
`15:49Z` through `16:54Z` the journal contains 1,067 processed tasks (952
no-op revisions, 82 successful events, 33 terminal failures), implying at least
1,265 Open Data requests. The 403 window was `16:16:00Z..16:47:32Z`.

Four files (`1610`, `1620`, `1630`, `1640`) were recovered 302-826 seconds late
and are coverage-only, not valid PIT first-seen evidence; `1600` remains absent
from the live first-seen state and must not be retroactively timestamped. The
same window contains zero Amsterdam canonical candidates, orders, fills,
shares, or cost, so the evidenced trading impact is zero wrong orders/fills and
one irrecoverable first-seen coverage gap. Collector commit `5fcf3f81` adds
pending plus durable notification-id deduplication, restricts consistency
retries to successful-but-stale revisions, and applies quota circuit-breaker
backoff. Controller commits `eb7118bd` and `c90a58e5` add the explicit restart
contract and align health freshness with the ten-minute source cadence.

## 9. 2026-07-25..08-05 Core Carry Probability-Lineage Pollution

`current_yes_core_carry_tiny_live_v2` writes the selected contract probability
as `model_token_probability`. The strategy-runtime migration omitted that field
from its canonical fallback and therefore materialized 22 of the instance's 54
canonical signals with `model_p_yes=0` between `2026-07-25T01:46:08Z` and
`2026-08-05T14:35:07Z`. All eight signals after the 2026-08-03 validation cutoff
are affected. The runner used the correct raw probability before migration, so
orders, fills, fees, settlement and PnL are unchanged; canonical probability,
edge and calibration analyses over these rows are polluted.

The migration now reads `model_token_probability`, and targeted regression
coverage is in place. Existing `signals` rows are append-only and were not
rewritten without an authorized full rebuild. Until then, exclude these rows
from canonical calibration/edge reports or join the exact raw runtime lineage.
The affected signal list and replay are recorded in
`analysis/2026-08/2026-08-06-current-yes-core-carry-incremental-live-review-v1.md`.

## 10. 2026-08-03..08-05 Core Carry Maker Share-Unit Pollution

Authenticated CLOB order states prove that Miami, NYC and Amsterdam each
matched 5 maker shares. The fill-sync parser treated camelCase
`sizeMatched="5"` as legacy token micro-units and divided by 1,000,000, so the
cache/canonical layer recorded three `0.000005`-share dust fills. Impact:
14.999985 shares, $14.349986 cost and approximately $0.65 settled PnL were
missing; maker intent fill rate was reported as 0/8 instead of 3/8. Orders and
exchange fills were unaffected.

The parser now resolves current v2 share units without relying on field casing
and retains legacy micro-unit support using submitted-order scale. Historical
repair is append-only: the three dust fill IDs receive validity exclusions and
three authenticated 5-share correction fills are appended. Evidence and the
exact order list are in
`analysis/2026-08/2026-08-06-current-yes-core-carry-incremental-live-review-v1.md`.

The three correction fills initially inherited `legacy_unknown` fee lineage
even though their orders retain `maker_only=true` and `place.status=live`.
On 2026-08-06 they received append-only `maker_zero` adjustments with exact
order-semantic evidence. Affected grain: three corrected fills in the same
2026-08-03..08-05 window; fee delta and settled PnL delta are both `$0.00`, so
no order, fill quantity, strategy decision, or historical PnL conclusion
changes. The correction only removes those rows from unknown-fee reporting.

## 11. 2026-08-06 JRS → NVMe Production Cutover Coverage Window

Current production storage moved from the old JRS USB volume to the pinned
NVMe volume while preserving `/Volumes/jrs` as the hot-path mount contract;
the old disk is mounted at `/Volumes/jrs-archive`. The controller stopped and
restored the complete 25-session canonical topology. Post-cutover evidence:
the DB compatibility path and physical canonical DB resolve to the same
device/inode, SQLite backup `quick_check` passed, storage identity audit has
zero critical/warning findings, all registered runtime health artifacts are
healthy, targeted orderbook coverage is `156/156`, forecast curve lineage is
`103/103`, and the authenticated CLOB read returned zero open orders.

The maintenance window is nevertheless a real collector coverage gap:

- high-frequency observations: `2026-08-06T16:09:02.432765Z` to
  `16:22:55.913392Z` (`833.481s`);
- fast-source opportunities: `16:09:02.576302Z` to `16:22:52.917006Z`
  (`830.341s`);
- regular source events: `16:07:24.777060Z` to `16:23:19.875636Z`
  (`955.099s`).

The live order journals contain zero rows in that window, and the post-change
authenticated exchange check found zero open orders, so there is no evidenced
wrong order, fill, or orphan-order impact. Missed first-seen events and missed
opportunities inside the window cannot be reconstructed after the fact.
Research using source lead time, event arrival, opportunity frequency, or
signal-funnel denominators must mark this interval as `collector_downtime` and
exclude it from PIT timing/completeness claims; it must not be counted as a
strategy-filtered zero-opportunity interval. The adjacent full snapshots
(`00:03` and `00:24` Beijing time) and the first native-NVMe forecast capture
are complete, so the gap does not invalidate later snapshot/orderbook or
forecast-curve rows.

The first hot-tier inventory found that immutable July paper snapshots and
targeted orderbooks were present only on the archive volume. That layout would
have silently shortened research runners that still consume the canonical hot
paths. A bounded `--ignore-existing` fill copied the missing immutable inputs
to NVMe: the hot tier now contains all `2,860` archive paper snapshots, all
`2,960` archive targeted orderbooks, all `2,293` forecast-curve captures, and
all `921` full-ladder orderbooks, plus newer post-cutover files. Filename-set
verification reports zero archive files missing from the corresponding hot
canonical directories; no content hash comparison or historical rewrite was
performed.

The cutover also exposed a control-plane TCC mismatch: direct controller and
manifest reads from the caller process could report JRS files inaccessible
while the authorized canonical tmux parent and all producers were healthy.
DB read probes, runtime health artifacts, and data-feed semantic health now
retry through a bounded checked session on the canonical context; this changes
health visibility only and does not create a second producer or permission
host.

## 12. 2026-08-06/07 Core Carry Snapshot Publish Gap

Between the last successful snapshot at `2026-08-06T20:05:10Z` and the next
success at `2026-08-07T00:05:34Z`, the data-feed made 20 failed publication
attempts (`20:15:19Z..23:51:03Z`). Open-Meteo was returning HTTP 429 and the
snapshot builder correctly loaded durable forecast curves younger than six
hours, but its final publish guard incorrectly required a nonempty *fresh*
capture list. It therefore rejected rows whose cached curve archive and hash
were valid. The stale forecast-enrichment view was a downstream consequence,
not the root cause.

The affected Core Carry universe contains 39 potential hourly scoring
checkpoints across 14 cities: Atlanta 2; Austin 3; Buenos Aires 1; Chicago 3;
Dallas 3; Denver 4; Houston 3; Los Angeles 4; Miami 2; New York City 2; Panama
City 3; San Francisco 4; São Paulo 1; Seattle 4. These are possible checkpoints,
not missed signals or orders: the rejected snapshots never persisted complete
candidates, so exact positive-signal counterfactuals cannot be reconstructed.
There is no evidenced wrong order, fill, or realized-PnL impact. Research must
mark this interval `snapshot_publish_gap` rather than count it as a
strategy-filtered zero-opportunity interval.

The fix verifies every published `(city,target_date,model,values_hash)` against
either the current capture or its exact durable archive and still fails closed
when that evidence is absent. Control commit `d1cf1ade`; production commit
`0f1e6d96`. After restart, `snapshot_20260807_0947.json` published 1,012 rows,
all with fresh curve evidence, and Core Carry consumed that exact file. The
production manifest, canonical DB route, JRS probe, data-feed semantics and
Core Carry health were healthy after the change.

The repair also exposed that the canonical hot market-data symlink target had
not been recreated after the NVMe cutover. The exact target directory was
restored and the standard Mac market-only sync completed; this was a storage
contract repair, not a new producer or live-strategy change.

## 13. 2026-08-07 Open-Meteo Request Amplification / Forecast Freshness

This incident was caused by duplicate and over-frequent local collection, not
NVMe, JRS, tmux or lock-screen permission loss. The paper-snapshot path fetched
Open-Meteo once per city and target date on every roughly 12-minute snapshot,
while forecast enrichment polled two Open-Meteo products for every configured
city every 30 minutes and also repeated single-run capture already owned by the
dedicated controller session. Before limiting began, 69 snapshots alone implied
about 6,900 city forecast requests on 2026-08-07. Open-Meteo first returned 429
at `2026-08-07T06:23:47.029781Z`; the last observed 429 in the audited window was
`2026-08-07T09:58:45.451869Z`. The last successful durable curve capture before
the window was `2026-08-07T06:04:02Z`.

Impact radius: forecast-enrichment logged 984 city rows over 21 cycles, of
which 374 carried HTTP 429 evidence. Forecast evidence became stale for research
and monitoring, but the four root live orders created after the first 429 were
all Core Carry v3 (`current_yes_core_carry_model_v3_no_peak_clock`). That frozen
artifact scores only market logit, local hour, dewpoint depression and wind
speed; its explicit contract says forecast peak clock is telemetry and not a
probability or eligibility feature. Therefore the evidenced decision impact is
zero changed or wrong orders from forecast staleness. The four roots were two
Chongqing and two Karachi orders: three matched roots with `$23.10` posted
principal and one `$4.65` maker root cancelled unfilled. This statement does not
publish realized PnL and does not claim that the missing newer forecast would
have been identical; research using forecast freshness must mark this interval
as upstream coverage degraded.

The root repair gives each mutable target one owner: paper snapshots reuse exact
durable curve evidence for one hour before attempting a live refresh;
forecast-enrichment reuses the most recent successful append-only Open-Meteo
evidence for six hours (including recovery past a failed `latest.json`), while
TAF continues on its own cadence; and the enrichment loop disables its duplicate
single-run collection because `weather_forecast_run_capture_v1` owns that chain.
Forecast-curve health now measures the six-hour source-cadence validity window,
not the collector heartbeat. It still fails closed once durable evidence exceeds
that window.

Deployment compatibility testing caught two bounded enrichment-only failures at
`10:29:37Z` (older production branch lacked the newer UTC parser dependency) and
`10:31:52Z` (that branch did not yet accept the ownership flag). Both were fixed
git-first before acceptance. Snapshots continued publishing, and the first fully
corrected enrichment at `10:34:25Z` reported 46/46 rows ok, 46 durable Open-Meteo
reuses and zero failures; `snapshot_20260807_1834.json` then completed with
160/160 live-scope orderbooks and was consumed by Core Carry.

## 14. 2026-08-07/08 Full-Ladder Forecast Coupling Gap And Canonical Cutover

The legacy `snapshot-full` entrypoint treated forecast-cache validity as a
precondition for collecting raw market books. After its last complete paper
snapshot at `2026-08-07 20:04` Beijing time and last full-ladder book archive at
`20:09`, forecast degradation caused the process to exit before requesting any
books. It emitted 44 consecutive zero-record partial snapshots through `23:49`.
This was a producer-boundary bug: raw Gamma/CLOB capture must not depend on
forecast, METAR, or a downstream strategy join being publishable.

The full-ladder research/PIT coverage gap is `2026-08-07 20:09` through the
first canonical `market_books` batch at `2026-08-08 00:53` Beijing time
(approximately 4h44m). The targeted/live path continued during that interval;
there is no evidenced wrong order, missing fill, or realized-PnL impact. The 44
empty artifacts are `upstream_market_coverage_gap`, not strategy-filtered
zero-opportunity cycles, and must be excluded from full-ladder completeness or
timing claims.

The production cutover replaces both active book writers with one canonical
owner: `weather_market_books` performs market discovery and writes append-only
raw batches under `market_books/batches`, independent of all weather inputs.
`weather_data_feed_jrs` only joins already persisted forecast/observation data
with `market_books/latest.json` into `strategy_snapshots`; complete ladder views
are materialized separately under `market_ladder_snapshots`. Historical
`targeted_output` and `full_ladder_output` remain read-only evidence and are no
longer production inputs or write targets.

Post-cutover evidence: the first market batch contained 93 events and 2,046
token books with zero failed books; the first joined strategy snapshot contained
693 records and reused all 47/47 required strategy books without another CLOB
request. The canonical DB route and storage identity audit were healthy,
canonical refresh exited zero, fill reconciliation was 1,405/1,405 with zero
cost difference, and the authenticated exchange check found zero open orders.
All consumers migrated in this cutover restarted from production commit
`d2b3fec7`; the controller also reported every registered runtime healthy. The
pre-cutover five-share SELL order was queried by order id and was `MATCHED`, not
lost or cancelled during restart.

## 15. 2026-08-07/08 Target-Day Market Retention Regression

At `2026-08-07T16:50Z` the canonical data-feed checkout changed to a target
selection path that treated Gamma `endDate` as the market's tradability cutoff.
For temperature events that field is exchange metadata, not the city-day
collection horizon. The first affected snapshot published at
`2026-08-07T16:58:29Z`; raw `market_books` still contained the markets, while
the derived strategy snapshot dropped them before the local Core Carry scoring
window had finished.

The malformed snapshot omitted 29 `2026-08-07` city-days: Amsterdam, Ankara,
Atlanta, Austin, Buenos Aires, Cape Town, Chicago, Dallas, Denver, Helsinki,
Houston, Istanbul, Jeddah, Los Angeles, London, Madrid, Mexico City, Miami,
Milan, Moscow, Munich, New York City, Panama City, Paris, San Francisco, São
Paulo, Seattle, Tel Aviv and Warsaw. Fourteen European/Middle-East/African
city-days had already completed the 13:30..17:30 Core window. The remaining 15
Americas city-days lost 73 scheduled hourly checkpoints: four each for Buenos
Aires and São Paulo (hours 14..17), and five each (hours 13..17) for Atlanta,
Austin, Chicago, Dallas, Denver, Houston, Los Angeles, Mexico City, Miami, New
York City, Panama City, San Francisco and Seattle.

These are missed scheduled checkpoints, not proven positive signals: the
malformed derived snapshots never persisted the feature rows needed for an
exact candidate counterfactual. Audited Core journals contain zero scores,
entry attempts, would-orders or live orders for those 15 city-days after the
first bad snapshot. Therefore evidenced wrong orders, fills and submitted
notional are all zero; missed-order/PnL impact is unknown and this interval must
be labelled `target_day_snapshot_coverage_gap`, not strategy-filtered no-signal.

The repair separates exchange end metadata from collection timing and retains
each temperature market through the configured city-local 22:00 horizon.
Cross-timezone regression tests cover Los Angeles, New York City, London,
Istanbul and Wellington. Production commit `f4907781` published
`snapshot_20260808_1104.json` from the corrected build; it restored the still-
local-current 2026-08-07 markets for Denver, Los Angeles, Mexico City, San
Francisco and Seattle, and Core Carry consumed that snapshot. Pre/post manifest
comparison found no missing production sessions and no DB-route change.

## 16. 2026-08-09 Strategy-Snapshot Legacy Proxy Coupling Gap

After the market proxy endpoint moved from `127.0.0.1:7890` to the unified
`7897` control-plane state, the derived strategy-snapshot loop still ran its own
legacy `7890`/Clash-controller preflight. The canonical market-books owner and
observation cache remained fresh, but the join view skipped 11 scheduled
publications from `2026-08-08T17:23:03Z` through `19:07:54Z`. The last good
artifact was `snapshot_20260809_0109.json` (available at
`2026-08-08T17:12:37Z`); the first recovered artifact was
`snapshot_20260809_0318.json` (available at `19:22:18Z`). Label this interval
`strategy_snapshot_proxy_coupling_gap`, not strategy-filtered no-signal.

Core Carry ran 443 cycles between those two artifact times but only re-read the
old immutable snapshot: 0 entry plans, 0 entry attempts, 0 live orders and 0
live execution errors. The first recovered 946-row snapshot was consumed at
`19:22:37Z` and also produced 0 entry plans. Therefore there are no evidenced
wrong orders; intragap missed-opportunity count remains unknown because the 11
derived PIT join artifacts were never published and must not be reconstructed
from later observations.

Root repair commit `7dfdf61c` removes network/proxy probing from the derived
join. `weather_market_books` exclusively owns market network health, while the
snapshot builder fails closed on canonical on-disk book age via
`--orderbook-source-max-age-sec`. Production checkout commit `cc0d279e` was
restarted through the controller and produced the recovered artifact above.

The first recovered run also exposed a second legacy dependency: the derived
view still fetched METAR city by city and transiently omitted current weather
state for trading cities London and Madrid. Commit `9ec26969` changes the view
to fail-closed loading of the canonical observation cache; production commit
`9bab95e4` published `snapshot_20260809_0327.json`. Final health showed 135/135
strategy books complete, zero missing trading-city weather states, fresh
observations/books and Core Carry consumption at `2026-08-08T19:29:49Z`.

## 17. 2026-08-07..09 Forecast Owner Interface Drift And Canonical-Ladder Bypass

The forecast owner deployed at `2026-08-07T16:53:40Z` passed two arguments
that its enrichment CLI did not implement (`--market-snapshot-dir` and
`--include-research-cities`). Enrichment therefore failed deterministically for
150 consecutive cycles through `2026-08-09T06:49:34Z`. At the same time the
curve collector swallowed every non-429 HTTP/transport failure as generic
`forecast_unavailable`, so cached coverage decayed to zero without preserving
the provider cause. Label this interval `forecast_owner_interface_and_observability_gap`;
do not treat it as strategy-filtered no-signal or evidence that every failure
was a provider quota response.

The last complete strategy snapshot before the downstream outage was
`snapshot_20260809_1155.json` (`2026-08-09T03:56:22Z`). Seventeen partial
snapshots followed. Core Carry placed 0 orders during this gap. The independent
fast-source path placed two Seoul fills totaling `$8.20`; those orders did not
consume forecast curves and are not attributed to this incident.

Commits `14fc8848` and `21352807` restore the CLI contract and publish exact
request failure reason/status/error plus attempted-request counts. Production
forecast checkout commits `3185285e`/`0983b0d7` captured 100/100 city-targets
and enrichment captured 40/40 cities without a 429. This proves the provider
path was healthy at recovery time, not that an external quota can never recur.
The same desired-state change removes the dormant July D1 multisource and
distance-2 zero-notional runners from current controller ownership while
retaining their code and dated evidence for replay.

Recovery validation exposed a separate consumer bypass: `strategy-snapshot`
loaded canonical books but still rediscovered every event through Gamma. With
the direct Gamma path unavailable it spent 227 seconds and emitted a zero-row
partial snapshot even though `market_books/latest.json` contained 2,068 books
for 94 events. Commits `5009ad95` and `d9c517bc` materialize event ladders and
the live target scope directly from canonical book identity. Production
data-feed commits `a7760dfb`/`6316ca6f` then published
`snapshot_20260809_1513.json`: 1,034 records, 47 cities, 94 city-targets,
134/134 targeted books, complete forecast lineage, 0 new Core Carry orders and
no consumer-side market request.

## 18. 2026-08-07..09 Observation Append-Only History Gap

The canonical observation producer continued to refresh
`output/observations/latest.json`, but stopped appending the global
`observations.jsonl` and its UTC daily shard after
`2026-08-07T16:45:45Z`. The first repaired batch landed at
`2026-08-09T13:54:17Z`, so the affected history window is 45.14 hours. The
data-feed log records 503 successful cache publications inside that window
before recovery; those captures are absent from raw observation history and
must be labelled `observation_append_history_gap`, not no-observation or
strategy-filtered no-signal.

This was a branch-regression failure. The append writer had been added by
`142f890a`, but the registered production checkout descended from a line that
did not contain that commit and again called the latest-only writer. Commit
`b35bf4c2` restores one producer-owned write of latest plus global/daily JSONL
and adds batch identity, stable observation-history identity, event/available/
ingested clocks and producer build identity. A regression test now exercises
the three outputs together.

The missing raw rows cannot be truthfully reconstructed from derived strategy
snapshots, so no synthetic backfill was written. Online decision inputs were
not stale because strategies consumed the continuously refreshed
`latest.json` (or their independent source-event lane), not
`observations.jsonl`. During the gap the two registered live journals recorded
12 distinct Core Carry venue order ids and 13 fast-source venue order ids;
none read the missing history file, so evidenced counterfactual order changes
from this writer defect are zero. The gap still blocks exact raw-observation
PIT replay for those 503 captures and must remain excluded or explicitly
coverage-labelled in research.

Production validation wrote 41 city rows with 41 unique history ids to both
the global file and `2026-08-09/observations.jsonl`; every row carries build
`b35bf4c2`. Controller health retained all persistent sessions. The only
pre/post missing session was the expected completion of bounded one-shot
`weather_canonical_refresh`, explicitly allowed in the comparison.

## 19. 2026-08-09..10 Health Identity, Proxy Consumer And Loaded-SHA Convergence

Production health had two blind/misclassified states. First, a fresh
`observations/latest.json` could hide a stopped append-only history writer.
Health now requires the latest global history batch to be fresh, carry the
producer/build/clock/stable-id contract, match the cache identity set, and
match the UTC daily shard. The first production check after deployment passed
all three surfaces with 41 rows, 41 unique history ids and zero parse errors.

Second, two Seoul `fast_source_prev_no_trial_v1` fills at 06:19 and 06:20 UTC
shared the same city/date/token/policy but had different `event_key`,
`execution_key` and exchange order id. They consumed 5 shares each against a
10-share market cap with zero cap violation. The old coarse health key treated
the second row as a duplicate and made the whole feed health critical. Health
now deduplicates current risk by stable execution identity while retaining the
conservative legacy key when an old row has no identity. This incident changed
zero orders, fills, notional or PnL; it was one false alert over two legitimate
fills.

The Helsinki pre-cross zero-notional shadow was the only running proxy consumer
still bound to `127.0.0.1:7890`. Its desired checkout is now the canonical
market-books production checkout and its controller restart resolved the shared
proxy state to `127.0.0.1:7897`. The proxy probe returned HTTP 200 and the
running-consumer mismatch count fell from one to zero. This runner cannot place
orders, so order/fill/notional impact is zero.

Core Carry was healthy but reported loaded SHA `9bab95e4` while its clean
registered checkout was at `0983b0d7`. The intervening changes were forecast
owner/configuration changes, not Core Carry signal or execution changes. Before
the guarded-live restart, the authenticated CLOB query returned zero open
orders. Controller restart preserved the 10 taker / 5 maker shares, 15-second
maker refresh, 10 city-days and $100 daily cap and loaded `0983b0d7`; the
post-restart authenticated query also returned zero open orders. The final
manifest was healthy with no findings or lost persistent sessions. Data-feed
health is `warn` only for missing same-day state in four non-trading cities;
all registered runtimes and the JRS context are healthy.

On August 10 the same control boundary exposed a later Core Carry transport
outage. The last successful loop summary was `2026-08-10T12:56:38Z`; from
`12:57:07Z` through `13:52:28Z` the append-only summary history contains 142
consecutive `PolyApiException: Request exception!` cycles. The old loop wrote
those errors only to history, so `latest_summary.json` remained stale and hid
the current failure reason. The repaired release writes every caught loop error
to latest, history and runtime state before sleeping. Controller restart loaded
release `1866ed88`, and the first recovered cycle was healthy at
`13:52:57Z`.

Persisted PIT score rows inside the affected window contain exactly one
eligible city-day: Cape Town at decision time `13:37:36Z`. It was delayed, not
lost: recovery submitted the corresponding 10-share taker at `13:52:55Z`, and
authenticated CLOB evidence reports all 10 shares matched at `0.94` versus the
earlier `0.98` ask. The five-share maker sleeve and its three replacements
produced four venue order ids; all four were cancelled with zero matched
shares. The final cancel projection created no venue order. Fast Source added
zero order rows during the maintenance window, and the maintenance delta has
no repeated venue order submission. Research and execution reviews must label
the failed interval `core_carry_clob_transport_error_20260810`; it is 142
failed execution cycles and one 15-minute delayed entry, not 142 strategy
rejections.

The proxy release then reloaded all 12 registered consumers from their pinned
checkouts. One real compatibility defect was found during that transaction:
the older city-runtime release lacked the controller-owned proxy-state fields,
so the Tokyo zero-notional session exited before process start. The release now
contains the shared proxy-state contract, resolves `127.0.0.1:7897` in an
executed smoke, and was restored through the controller. Final Gamma and CLOB
probes both returned HTTP 200, all 12 process bindings match the controller
state, controller/manifest/API/data-feed/storage checks are healthy, and the
pre/post manifest comparison lost no persistent session.

The full data-feed check also misclassified Core Carry maker replacements as
duplicate current executions because it ignored their stable `execution_id`
and treated cancel projections as active rows. Health now keys modern rows by
execution identity, retains the conservative coarse key only for legacy rows,
and excludes exchange-confirmed maker cancel projections. The production check
now reports zero duplicate order ids, zero duplicate current execution ids and
zero current YES/NO conflicts.

## 20. 2026-08-07..2026-08-10 WCIR Helsinki Forecast-Path Drift

The registered WCIR production checkout continued to read Helsinki forecast
curves from the retired `targeted_output/forecast_hourly_curves` directory after
the canonical producer moved them to `forecast/forecast_hourly_curves`. The
runner process and controller session stayed alive, but Helsinki stopped
producing decision bundles after `2026-08-07T16:52:02.241468Z` and emitted
`RuntimeError: no PIT forecast` on every subsequent cycle.

As of `2026-08-09T18:14:57.997298Z`, the append-only runtime error journal
contains 1,054 affected cycles across target dates `2026-08-07` and
`2026-08-09`; the affected window remains open until the production checkout is
updated and the controller-managed runtime is restarted. The last pre-gap
Helsinki bundle is one of 777 historical Helsinki bundles in the journal.
Research must label this interval `wcir_helsinki_forecast_path_gap`; it is not a
strategy rejection or evidence that no Helsinki opportunities existed.

Commit `c17eaa67` updates both WCIR configs to the canonical path. A read-only
production-checkout smoke using the canonical input and a temporary output
directory changed the same run from `errors=1, evaluated=0` to
`errors=0, evaluated=2`. This isolates the path drift as the forecast failure's
root cause. WCIR is `zero_notional_shadow`; both the affected production summary
and the repair smoke report `orders_submitted=0`, so evidenced order, fill,
notional and PnL impact is zero. This record must be updated with the recovery
timestamp and first restored durable bundle after deployment.

Commit `f2110dd3` also makes the WCIR summary expose `status=error` whenever an
adapter cycle records an exception, and the production contract now accepts
only `status=ok`. The still-running old build is therefore reported critical
instead of being treated as healthy merely because its summary timestamp moves.

Production checkout `a97d064d` was restarted through the controller at
`2026-08-10T01:53:39Z`. The first completed production cycle at
`2026-08-10T01:54:28.761232Z` reported `status=ok`, `errors=0`,
`evaluated=8`, `scored=7` and `orders_submitted=0`; later cycles remained
healthy. The loaded config resolves `forecast_curve_dir` to
`/Volumes/jrs/weather_data_feed_service_runtime/forecast/forecast_hourly_curves`.
No new Helsinki information event arrived during the deployment validation
window, so no new durable Helsinki bundle was fabricated merely to close the
incident. The path outage ends at the first healthy production cycle; the next
natural Helsinki event will be the first post-recovery durable bundle.

## 21. 2026-07-28..2026-08-10 Retired Fast-Observation Consumer Gap

The `weather_live_cross_observations` controller instance became the sole
current high-frequency observation owner, but the broad stale-book collector,
the Tmax first-lock shadow and data-feed health still referenced the retired
`output/high_frequency_observations` tree. That tree stopped updating at
`2026-07-28T00:18:07.402121Z`. The broad collector continued overwriting a
fresh `status=ok` summary even though `source_cities`, `source_city_dates` and
`new_events` were all empty. Its last durable quote was
`2026-07-28T00:18:06.329036Z`; its last event was created at
`2026-07-27T23:11:43.653834Z`.

Commit `f34ec41c` routes current consumers and health exclusively through
`live_cross_observations`, adds the source owner as an explicit controller
dependency, and removes the dormant second producer from the data-feed loop.
Setting the retired producer flag now fails closed instead of creating another
mutable owner. A read-only parser smoke against the current canonical latest
file returned five current city-target rows (Ankara, Atlanta, Helsinki,
Istanbul and Miami), while the deployed stale-book process still returned zero
until its checkout is updated and controller-managed session restarted.

The broad collector and Tmax instance are shadow/telemetry-only, so evidenced
order, fill, notional and PnL impact is zero. The affected interval is a source
event/quote coverage gap, not evidence that no qualifying events existed. The
record must be closed with the first restored durable event/quote timestamps
after deployment.

Commit `f2110dd3` replaces freshness-only checks with producer-owned semantic
health: the broad collector must prove its canonical input identity and input
freshness, while the Tmax summary must report healthy source-context inputs.
Until the new builds are deployed, controller health intentionally reports
both old runtimes critical rather than preserving their former false-OK state.

The production integration is `a8113585` in the dedicated immutable checkout
`pm_agents_fast_observation_prod`; controller contract commit `796753b1`
separates these telemetry consumers from the checkout used by Core Carry live.
The old shared checkout was returned to its loaded live SHA `0983b0d7`, so no
live restart was needed and the final manifest has no loaded-SHA drift.

Recovery evidence is:

- at `2026-08-10T01:58:24Z`, Tmax reported `status=ok`, canonical
  high-frequency input with 63 rows / 6 city-date keys, all three source-context
  components `ok`, `paper_executor_only`, and `live_orders_written=0`;
- broad stale-book and source-event health both reported canonical route and
  fresh input, six current cities, `telemetry_only_no_orders`, and
  `orders_submitted=0`;
- the lowest-temperature ladder wrote the first two restored durable quote rows
  for Seoul and Tokyo, with fresh CLOB fetches from
  `2026-08-10T01:58:38.069358Z` through `01:58:44.282235Z`. The broad path had
  no qualifying cross in that cycle, so its zero new events is a real idle
  cycle, not an empty retired input;
- Core Carry PID `57664` and fast-source live PID `35905` were identical before
  and after deployment. Final production manifest and storage identity audit
  were healthy; controller health had no critical runtime and retained only the
  known missing-weather-state warning for six non-trading cities.

The first attempt to move the collector into its isolated checkout failed
before process start because ignored `.venv` bootstrap state was absent. After
adding the standard local links, controller recovery succeeded. Commit
`86d632cb` now preflights a git production checkout before killing an existing
session, so a missing `.venv` or required `.env` fails while the old process is
still running.

## 22. 2026-08-10 Amsterdam local-day and CrossNo state-anchor incident

Amsterdam's observation cache accepted a provider padding METAR from
`2026-08-09T21:55:00Z` (23:55 local on August 9) into the August 10 running
maximum. Until EHAM actually printed 23°C on August 10, WCIR therefore anchored
the current bracket at 23 instead of 22. The polluted interval ended with the
first-seen 08:25Z EHAM report at `2026-08-10T08:27:54Z`. It contains 62 KNMI
new-content checkpoints / 184 model-expression bundle rows and two selected
zero-notional paper candidates; WCIR submitted zero orders, and the live
CrossNo journal contains zero Amsterdam orders. These rows remain append-only
evidence but must be tagged/excluded as wrong-anchor data in forward scoring.

The root fix filters observation records by the city's local target date before
computing day maxima. A second restart-only defect projected an aggregate byte
cursor onto partitioned source-event shards, losing the earlier 23°C EHAM high
between `12:22:58Z` and `12:26:24Z`. The retained city-day state now rebuilds
once from dated shards on migration. No Amsterdam order was submitted or missed
in that interval: the observed KNMI ta values (22.5°C and 21.3°C) did not satisfy
the 0.7°C CrossNo margin under either anchor.

Production verification after the fixes shows official running max 23°C,
24 routine EHAM timestamps restored, KNMI notification as a direct wake path,
Amsterdam in the live city policy, a 10-share city cap, and no critical runtime.

## 23. 2026-08-10 Market Proxy Transport and Market-Books Coverage Gap

The canonical `127.0.0.1:7897` Clash Verge endpoint remained the single proxy
entry, but its upstream route became intermittent. There were two evidenced
market-books failure windows: `14:08:43Z..14:16:36Z` and
`14:18:43Z..14:44:46Z`. In the first, one REST batch lost 1,082/1,276 books
(582 TLS connect timeouts and 500 read timeouts) before the next batch
recovered. The hot group was 82/82 failed because the batch endpoint had one
attempt, while later cold chunks happened to reach the recovered route.

The second window exposed a separate false-OK defect in Gamma discovery.
Successive batches discovered 8/89, 34/89, 4/89, 0/89 and 0/89 city-date
events. The first two were nevertheless published as `status=ok` because
status considered only books for the small discovered subset. The final
healthy batch at `14:44:45.693Z` restored 79 events, 1,738/1,738 books and only
the usual 10 expected unavailable city-date events. Strategy snapshot join
retained the last complete book batches at 22:09 and 22:19 Beijing time, then
missed the 22:29, 22:39 and 22:49 publication cycles; the first restored
snapshot was 22:51.

Deployment testing then found that marking discovery loss `degraded` was not
enough to preserve the denominator. The 23:09 batch contained 1,782 books but
omitted the failed Miami event. The 23:12 strategy snapshot accepted that
batch and reported `target_count=108`, `target_ok_count=108`, because its target
set had already shrunk with the missing event. Later 23:19 and 23:29 batches
collapsed to 66 and 22 books; the 23:23 and 23:33 joins were correctly retained
only as partial snapshots with 0 and 11 records. This is a coverage defect, not
a strategy filter, and supersedes the earlier statement that no
partial-discovery batch reached a later strategy snapshot.

Within `14:08:43Z..14:44:46Z`, Core Carry wrote 101 loop summaries: 62 `ok`,
36 `error` and three `runtime_state_error`, including 38
`PolyApiException: Request exception!` records. It created zero entry plans,
entry attempts, live order rows or execution-journal rows. The 404 maker
lifecycle rows in this interval were all detached historical retry TTL expiry
records with no source order id. Fast Source wrote 116 opportunity rows (71
blocked, 45 source-missing), zero eligible rows and zero order-bearing rows.
Thus evidenced new order/fill/notional impact is zero. Exact missed-opportunity
count is not reconstructible because the missing PIT books were never captured;
research must treat both windows as `market_proxy_transport_coverage_gap`, not
as evidence of zero opportunities.

Commit `3b539342` implements the transport repair. Gamma discovery is bounded
eight-way concurrent, retries one transport failure, records attempt/failure
class, and marks operational discovery loss degraded. CLOB `/books` retries
transport/408/425/429/5xx failures twice within the existing 240-second budget,
records attempt lineage, and reports hot/cold success separately. Contract and
payload errors still fail immediately. The WebSocket client pins its actually
used 15.0.1 API family and handles a proxy reset before `connection_made`
through the documented connection factory hook, preventing the misleading
`recv_messages` callback error while retaining the real reconnect failure.

Commit `b222b948` closes the denominator defect. `market_books` now maintains
one immutable event/token identity cache under
`market_ladder_snapshots/event_contract_cache.json`. On an operational Gamma
failure it may reuse a matching contract discovered within 24 hours, recompute
the current hot targets from current observations, and still fetch every CLOB
book fresh. It never reuses a price or book. Rows record
`market_discovery_source=gamma_live|cached_event_contract` and the last live
discovery clock. A fully recovered batch is
`status=ok_with_discovery_reuse`; any unrecovered contract or fresh-book
failure remains `degraded`.

The controller deployed release `742b5fed`, then deployed the denominator
follow-up release `c4531c1899a13efb41b3a0e9b7fc939edee709b8` with a second
restart limited to `weather_market_books`. The first `c4531c18` REST batch at
23:35 Beijing time discovered 82 events and fetched 1,804/1,804 fresh books
(114 hot, 1,690 cold, zero failures) in 15 seconds. It created a 98-record
event-contract cache. The WebSocket process loaded the same SHA, connected on
websockets 15.0.1 with the pre-transport guard active, and consumed the same
healthy REST batch. Main-tree relevant tests passed 155/155 and the specialized
release module suite passed 62/62. Both live order journals and the Core Carry
live-order file retained their exact pre-restart line counts and SHA-256, so
the two market-books restarts created zero order, fill or notional delta.
The first natural downstream publication after the final restart was
`snapshot_20260810_2344.json`: it consumed a `c4531c18` batch with 1,804 books,
published 902 records across 47 cities and 82 city-date pairs, and resolved all
114/114 live orderbook targets with zero incomplete target. This closes the
collector-to-strategy-snapshot deployment check. The cached-discovery failure
path is covered by the regression suite; the observed post-restart batches all
used live Gamma discovery, so production has not yet exercised
`ok_with_discovery_reuse` naturally.

## 24. Immediate Follow-Up Work

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
5. Do not start, stop or restart local live execution without explicit confirmation.
