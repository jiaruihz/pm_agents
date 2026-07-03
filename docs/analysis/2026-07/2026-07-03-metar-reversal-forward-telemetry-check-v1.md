# METAR Reversal Forward Telemetry Check v1

Generated: 2026-07-03T15:48:59.039390+00:00

## Data Snapshot

- Data sources: rebuilt `runtime/weather.db`, generated Head B CSV matrices, and zero-notional shadow journal.
- DB mtime: `2026-07-03T15:45:45.942384+00:00`; `fact_signal_candidates` rows=44654 event_date=2026-05-05..2026-07-05 built=2026-07-03T15:45:44.740788+00:00.
- `fact_trades` rows=4414 target_date=2026-05-06..2026-07-01 built=2026-07-03T14:46:54.265291+00:00; unsettled=152; missing_bracket=0.
- CLOB fill coverage gate: gate_pass=True fail_reasons=[].
- `run_stack.sh` completed DB/fact/gate work, then exited on frontend port 5174 still busy; analysis uses the rebuilt DB/gate artifacts.

## Verdict

`rich_current_collapse_d1_yes` remains `shadow_candidate_keep_collecting`: keep zero-notional shadow running, do not change live, and do not approve future small size yet.

```text
significance=PASS historically for the frozen Head B branch already documented
baseline=PASS historically versus same-snapshot broad d1/current siblings
forward=FAIL/NA: no 2026-06-21+ qualifying state in offline matrices; current shadow has 0 triggers
conclusion=shadow_candidate, not confirmed
```

## State Frequency

- Historical B4/runway proxy: 133 rows / 31 dates / 28 cities, date range 2026-05-20..2026-06-20; 2026-06-21+ rows=0.
- Historical false_fade sibling: 32 rows / 22 dates / 15 cities, date range 2026-05-20..2026-06-20; 2026-06-21+ rows=0.
- Broad one-step runway rows after 2026-06-21: 0 rows.
- Forward shadow journal: 19 summary rows / 9 unique snapshots; total false_fade triggers=0, total B4 triggers=0.
- Latest cycle `5b80cc428819` on `snapshot_20260703_2340.json`: states=31, states_ok=20, false_fade=0, B4=0.

## Fresh-Book Fill Feasibility

- Latest d1 quote source: {'clob_book': 15, 'market_yes_price': 5}; latest d1 book status: {'ok': 15, 'orderbook_budget_exhausted': 5}.
- Latest finite `d1_yes_ask_size` count: 15; all journaled ok rows finite d1 size: 66 / 371.
- Historical d1 sizes were already thin: B4 proxy median=14.0 shares, false_fade median=16.0 shares; false_fade had 20 / 32 rows below 20 shares.
- Current fresh-book feasibility is partially restored after switching the local data-feed snapshot loop to full orderbook coverage: latest ok states now include executable d1 ask size, but residual budget exhaustion and thin historical size mean fill feasibility still needs fresh-forward accumulation.

## Runner State

- `scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py` is Head B-only and writes zero-notional JSONL rows; it does not call the order executor.
- A Mac LaunchAgent was added and loaded as `com.pm-agents.metar-reversal-shadow`, reading local data-feed paper snapshots and the shared observation cache every 300s.

## Action

- Continue shadow: yes.
- Mark dormant: no, not yet; the evidence gap is fresh-forward frequency and book depth, exactly what the shadow now collects.
- Future small live: no. Revisit only after nonzero fresh-forward triggers with actual CLOB d1 ask size/depth and settled forward outcomes.
