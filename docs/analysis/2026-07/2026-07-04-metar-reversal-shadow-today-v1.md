# METAR Reversal Shadow Today v1

Generated: `2026-07-04T17:38:00.838518+00:00`

## Verdict

`metar_reversal.rich_current_collapse_d1_yes` remains `shadow_candidate_keep_collecting`; no live change.

Forward scarcity broke today, but the broader B4 basket faded after overshoots. False-fade ended slightly positive because Helsinki 20 offset Helsinki 19 and Lucknow losses. This is evidence to keep shadow running, not evidence to live.

## Data Snapshot

- Shadow source: `runtime/weather_edge_v1/metar_reversal_false_fade_reheat_shadow_v1`.
- UTC window: `2026-07-04T00:01:18.644098+00:00` .. `2026-07-04T17:36:52.842712+00:00`.
- Summary rows today: 212; unique snapshots: 48.
- `runtime/weather.db` mtime: `2026-07-04T17:34:58.778611+00:00`; CLOB fill gate pass: `True`.

## Trigger Funnel

- Cycles with false-fade trigger: 27 (raw trigger sum 27).
- Cycles with B4 trigger: 32 (raw trigger sum 33).
- Unique token-level triggers after dedupe: 4.
- Latest cycle: `{'cycle_id': '4da259f37e23', 'false_fade_reheat_conflict_triggered': 0, 'generated_at_utc': '2026-07-04T17:36:52.842712+00:00', 'rich_current_conflict_b4_triggered': 0, 'snapshot_file': 'snapshot_20260705_0118.json', 'states': 41, 'states_ok': 26}`.

## MTM Summary

This marks the first shadow entry to the latest best bid with $1 notional per unique token and official taker-fee formula on both entry and bid-exit. It is not a recommended exit rule.

| Slice | Tokens | MTM PnL | MTM ROI | Cities |
|---|---:|---:|---:|---|
| all_unique_tokens | 4 | $-1.27 | -30.8% |  |
| false_fade | 3 | $+0.22 | +7.2% | Helsinki, Lucknow |
| b4 | 4 | $-1.28 | -30.9% | Amsterdam, Helsinki, Lucknow |

## Token Rows

| City | D1 Bracket | Branches | Entry Ask | Latest Bid | Latest Ask | MTM ROI | Obs Proxy State | Read |
|---|---:|---|---:|---:|---:|---:|---|---|
| Amsterdam | 22 | b4 | 0.78 | 0.0 | None | -100.0% | overshot_by_obs_proxy | failed_or_faded |
| Helsinki | 19 | b4,false_fade | 0.16 | 0.0 | None | -100.0% | overshot_by_obs_proxy | failed_or_faded |
| Helsinki | 20 | b4,false_fade | 0.35 | 0.998 | 0.999 | +176.1% | in_bracket_now | pump |
| Lucknow | 37 | b4,false_fade | 0.29 | 0.0 | None | -100.0% | not_reached | failed_or_faded |

## Read

- Positive: the state frequency problem eased, and Helsinki 20 ended near binary after the trigger.
- Negative: Amsterdam 22 and Helsinki 19 were overshot by the final-looking market state, while Lucknow 37 was a false reheat and collapsed to near zero.
- Boundary: the latest cycle has no active trigger; the useful signal was the transient conflict window, not a persistent all-day state.
- Action: keep zero-notional shadow running; do not live-size from one day. Next review should separate overshoot risk, first-step vs second-step re-entry, and false-fade-only rows where bracket-aware B4 is false.
