# METAR Reversal Shadow Today v1

Generated: `2026-07-04T17:38:00.928977+00:00`

## Verdict

`metar_reversal.rich_current_collapse_d1_yes` remains `shadow_candidate_keep_collecting`; no live change.

No token-level trigger appeared in this UTC review window. This is a frequency/coverage observation, not evidence for live.

## Data Snapshot

- Shadow source: `runtime/weather_edge_v1/metar_reversal_false_fade_reheat_shadow_v1`.
- UTC window: `None` .. `None`.
- Summary rows today: 0; unique snapshots: 0.
- `runtime/weather.db` mtime: `2026-07-04T17:34:58.778611+00:00`; CLOB fill gate pass: `True`.

## Trigger Funnel

- Cycles with false-fade trigger: 0 (raw trigger sum 0).
- Cycles with B4 trigger: 0 (raw trigger sum 0).
- Unique token-level triggers after dedupe: 0.
- Latest cycle: `{'cycle_id': '4da259f37e23', 'false_fade_reheat_conflict_triggered': 0, 'generated_at_utc': '2026-07-04T17:36:52.842712+00:00', 'rich_current_conflict_b4_triggered': 0, 'snapshot_file': 'snapshot_20260705_0118.json', 'states': 41, 'states_ok': 26}`.

## MTM Summary

This marks the first shadow entry to the latest best bid with $1 notional per unique token and official taker-fee formula on both entry and bid-exit. It is not a recommended exit rule.

| Slice | Tokens | MTM PnL | MTM ROI | Cities |
|---|---:|---:|---:|---|
| all_unique_tokens | 0 | $+0.00 | NA |  |
| false_fade | 0 | $+0.00 | NA |  |
| b4 | 0 | $+0.00 | NA |  |

## Token Rows

| City | D1 Bracket | Branches | Entry Ask | Latest Bid | Latest Ask | MTM ROI | Obs Proxy State | Read |
|---|---:|---|---:|---:|---:|---:|---|---|

## Read

- No token-level trigger appeared in this UTC review window.
- This is a frequency/coverage observation only; it is not evidence for or against the entry edge.
- Action: keep zero-notional shadow running and wait for fresh trigger rows before discussing live.
