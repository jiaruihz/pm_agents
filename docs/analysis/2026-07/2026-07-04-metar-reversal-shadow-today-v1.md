# METAR Reversal Shadow Today v1

Generated: `2026-07-04T14:04:49.179889+00:00`

## Verdict

`metar_reversal.rich_current_collapse_d1_yes` remains `shadow_candidate_keep_collecting`; no live change.

Today is useful because the state finally appeared again after the prior 6/21+ trigger starvation, but this is still intraday MTM / open-weather evidence, not settled ROI.

## Data Snapshot

- Shadow source: `runtime/weather_edge_v1/metar_reversal_false_fade_reheat_shadow_v1`.
- UTC window: `2026-07-04T00:01:18.644098+00:00` .. `2026-07-04T14:01:46.827433+00:00`.
- Summary rows today: 169; unique snapshots: 35.
- `runtime/weather.db` mtime: `2026-07-04T13:57:33.973125+00:00`; CLOB fill gate pass: `True`.

## Trigger Funnel

- Cycles with false-fade trigger: 27 (raw trigger sum 27).
- Cycles with B4 trigger: 32 (raw trigger sum 33).
- Unique token-level triggers after dedupe: 4.
- Latest cycle: `{'cycle_id': 'e95539fca12b', 'false_fade_reheat_conflict_triggered': 0, 'generated_at_utc': '2026-07-04T14:01:46.827433+00:00', 'rich_current_conflict_b4_triggered': 0, 'snapshot_file': 'snapshot_20260704_2140.json', 'states': 38, 'states_ok': 25}`.

## MTM Summary

This marks the first shadow entry to the latest best bid with $1 notional per unique token and official taker-fee formula on both entry and bid-exit. It is not a recommended exit rule.

| Slice | Tokens | MTM PnL | MTM ROI | Cities |
|---|---:|---:|---:|---|
| all_unique_tokens | 4 | $+2.59 | +62.9% |  |
| false_fade | 3 | $+2.49 | +79.9% | Helsinki, Lucknow |
| b4 | 4 | $+2.59 | +62.7% | Amsterdam, Helsinki, Lucknow |

## Token Rows

| City | D1 Bracket | Branches | Entry Ask | Latest Bid | Latest Ask | MTM ROI | Obs Proxy State | Read |
|---|---:|---|---:|---:|---:|---:|---|---|
| Amsterdam | 22 | b4 | 0.78 | 0.91 | 0.95 | +14.9% | in_bracket_now | pump |
| Helsinki | 19 | b4,false_fade | 0.16 | 0.85 | 0.91 | +406.0% | overshot_by_obs_proxy | needs_settlement_rule_check |
| Helsinki | 20 | b4,false_fade | 0.35 | 0.101 | 0.158 | -73.3% | in_bracket_now | failed_or_faded |
| Lucknow | 37 | b4,false_fade | 0.29 | 0.001 | 0.004 | -99.7% | not_reached | failed_or_faded |

## Read

- Positive: Amsterdam 22 and Helsinki 19 both repriced sharply after the trigger; this is exactly the forward evidence Head B lacked.
- Negative: Lucknow 37 was a false reheat and collapsed to near zero; Helsinki 20 shows second-step chasing can be weak even after the first one-step reversal works.
- Boundary: the latest cycle has no active trigger; the useful signal was the transient conflict window, not a persistent all-day state.
- Action: keep zero-notional shadow running; do not live-size from one day. Next review should separate first-step vs second-step re-entry and false-fade-only rows where bracket-aware B4 is false.
