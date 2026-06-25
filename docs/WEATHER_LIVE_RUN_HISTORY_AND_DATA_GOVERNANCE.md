# Weather Live Run History and Data Governance

Status: current-reference
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; reference only, not production source of truth

Last updated: 2026-05-17

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
