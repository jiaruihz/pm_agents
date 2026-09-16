# All-YES Underround Low-Latency Paper Design v0

Status: design-draft
Updated: 2026-06-16 settlement_outcomes upgrade
Source of truth: no
Used by: WEATHER_DOCS_INDEX.md; analysis/market_structure_edge.md

## Target

`all_yes_underround_basket_v0` is a model-free all-YES basket: buy every mutually exclusive YES bracket at equal shares when total best-ask cost is below 1.0.

Target metric:

```text
live_equivalent_all_yes_underround_forward_paper
= all-leg paper basket recorded within 180s of orderbook fetched_at_utc,
  with every leg present, ask depth >= 5 shares, max YES spread <= 0.05,
  total YES ask cost <= 0.98, and no real order placed.
```

This is still paper/shadow only. It does not place orders and does not modify N100 live trading.

## Current Evidence Snapshot

- Offline robust evidence: `2026-06-09-range-rv-underround-robust-v1-0.md` confirmed all-YES underround in proxy and executable orderbook thresholds.
- Canonical historical denominator: `2026-06-15-all-yes-underround-basket-facts-v0.md` scans 1,338 orderbook snapshots from 2026-05-19 through 2026-06-16 into runtime `fact_baskets.jsonl` and `fact_basket_legs.jsonl`; at 0.02 underround it has 297 strategy-candidate observations, 270 settled exactly-one-winner observations, and +3.16% settled unit ROI.
- Settlement evaluation now uses `settlements.condition_id` first, then DB `settlement_outcomes` at `city/target_date/bracket` source grain. Direct pm_history reads are migration fallback only for old DB snapshots.
- Local 2026-06-14 persistence scan: 7 snapshots, 3 with guard-passing candidates, 4 total observations. This is a local live-prep slice, not the all-history denominator.
- Candidate sequence:
  - Busan 2026-06-14: 2 observations, max underround +3.0%.
  - MexicoCity 2026-06-14: 1 observation, max underround +2.5%.
  - Denver 2026-06-14: 1 observation, max underround +2.8%.
- Current paper ledger: Busan and MexicoCity settled exactly-one-winner for +2.83% paper ROI after the pm_history fallback fix, but both were recorded 1353.209s after snapshot and remain observation-only.
- Fresh runner state: latest Denver candidate was skipped because snapshot age was 1274.191s > 180s.
- Live-equivalent forward sample: 0 baskets, 0 settled.

## Components

| Component | Role | Places orders? |
|---|---|---|
| `scripts/analysis/market_structure_edge/build_all_yes_underround_basket_facts_v0.py` | Builds the canonical all-history basket and leg facts, plus the current denominator report | no |
| `scripts/analysis/market_structure_edge/all_yes_underround_settlement.py` | Shared settlement resolver: condition_id first, then DB settlement_outcomes city/date/bracket source grain | no |
| `scripts/analysis/market_structure_edge/research_all_yes_underround_live_prep_v0.py` | Scans latest orderbook snapshot and writes current candidate report | no |
| `scripts/ops/all_yes_underround_guards.py` | Pure all-leg guard; rejects missing legs, shallow depth, wide spread, stale snapshot, duplicate condition IDs, cost cap, kill switch | no |
| `scripts/ops/all_yes_underround_paper_exec_v0.py` | Appends all-leg paper baskets, evaluates settlements with the shared resolver, writes live-prep gate and monitor | no |
| `scripts/ops/all_yes_underround_fresh_paper_cycle_v0.py` | Runs scanner + paper cycle only when snapshot is fresh under TTL | no |
| `scripts/ops/all_yes_underround_fresh_paper_loop_v0.sh` | Repeats the fresh cycle for same-host low-latency capture | no |
| historical start wrapper (removed) | Started the loop with pid/log files; retained in git history only | no |

## N100 Snapshot-Side Shape

The source orderbook path is owned by `weather-predict`:

```text
/home/jiarui/projects/weather-predict/output/orderbook_snapshots/
```

The fresh paper loop historically ran from the `pm_agent` repo and read that source path directly. The direct N100 launcher has been removed; this is provenance, not a current run command:

```bash
PROJECT_DIR=/home/jiarui/projects/pm_agent_all_yes_fresh
DATA_PROJECT_DIR=/home/jiarui/projects/pm_agent
SNAPSHOT_ROOT=/home/jiarui/projects/weather-predict/output/orderbook_snapshots
```

Freshness is based on row-level `fetched_at_utc` when available, not the service-level `snapshot_ts_utc`. The runner also rejects a sidecar that appears to still be writing (`min_file_stable_seconds=10`) or has too few rows (`min_snapshot_rows=500`) so a partial gzip cannot create a false all-YES underround basket.

Do not deploy this by `scp`/`rsync`. Any N100 change must go through `weather-strategy-deploy` and the git-first flow.

## Suggested User Systemd Template

This is a template for deploy review, not an instruction that it is already installed.

`~/.config/systemd/user/all-yes-underround-fresh-paper.service`

```ini
[Unit]
Description=All-YES underround fresh paper loop

[Service]
Type=simple
WorkingDirectory=/home/jiarui/projects/pm_agent_all_yes_fresh
Environment=PROJECT_DIR=/home/jiarui/projects/pm_agent_all_yes_fresh
Environment=DATA_PROJECT_DIR=/home/jiarui/projects/pm_agent
Environment=SNAPSHOT_ROOT=/home/jiarui/projects/weather-predict/output/orderbook_snapshots
Environment=MAX_SNAPSHOT_AGE_SECONDS=180
Environment=MIN_FILE_STABLE_SECONDS=10
Environment=MIN_SNAPSHOT_ROWS=500
Environment=CYCLE_INTERVAL_SECONDS=30
ExecStart=/home/jiarui/projects/pm_agent_all_yes_fresh/scripts/ops/all_yes_underround_fresh_paper_loop_v0.sh
Restart=always
RestartSec=10
```

Start command after deploy review:

```bash
systemctl --user daemon-reload
systemctl --user start all-yes-underround-fresh-paper.service
systemctl --user status all-yes-underround-fresh-paper.service --no-pager
```

## Go/No-Go Gates

Before tiny live review, all of these must be true:

| Gate | Required |
|---|---|
| Data integrity | CLOB fill coverage gate passes (`gate_pass=true`) |
| Fresh capture | `ttl_equivalent_baskets >= 20` |
| Forward settlement | `ttl_equivalent_settled_exactly_one_winner >= 20` |
| Forward ROI | `ttl_equivalent_settled_roi >= +2%` |
| Positive basket rate | `ttl_equivalent_positive_basket_rate >= 55%` |
| Settlement sanity | no `settled_winner_count_anomaly` |
| Execution design | signed all-leg executor, all-leg-or-none behavior, and partial-fill cancel/unwind rules reviewed |
| Deploy path | `weather-strategy-deploy` git-first flow, no direct remote edits |

Current gate: `NOT_READY_ACCUMULATE_PAPER_SHADOW`.

## Operator Checks

Local dry check:

```bash
scripts/ops/run_all_yes_underround_fresh_paper_v0.sh
cat runtime/weather_edge_v1/all_yes_underround_paper_v0/fresh_cycle.json
cat runtime/weather_edge_v1/all_yes_underround_paper_v0/monitor.json
```

Expected stale local behavior when run after sync delay:

```text
verdict=STALE_SNAPSHOT_SKIP_CYCLE
executed_cycle=false
reason=snapshot_too_old
```

Expected same-host behavior immediately after a snapshot:

```text
verdict=FRESH_SNAPSHOT_CYCLE_RAN
executed_cycle=true
```

The paper ledger may still append 0 baskets if the fresh snapshot has no guard-passing candidate. That is healthy; it proves the loop is timely but the market did not offer the basket.

## Verdict

`all_yes_underround_basket_v0` is the nearest live direction, but not live-ready. The next concrete step is not broader research; it is low-latency forward paper collection on the snapshot source host. If the next 20+ TTL-valid settled baskets preserve positive ROI and exactly-one-winner settlement behavior, then tiny-live review becomes justified.
