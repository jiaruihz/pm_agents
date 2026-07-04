# METAR Reversal Live Readiness v1

Generated: `2026-07-04T17:45:00Z`

## Verdict

`metar_reversal.false_fade_reheat_conflict_d1_yes` remains `shadow_candidate_keep_collecting`.

Do not start real-money live yet. If we later run it, it should be a separate HeadB micro-live runner, not part of HeadA forecast-tail.

## Evidence

- Historical same-snapshot matrix: `false_fade_reheat_conflict -> d1 YES` has 47 rows / 25 dates / 18 cities, win rate 29.8%, avg ask 14.5%, ROI +61.0%, but date-block CI is wide `[-31.6%, +172.7%]`; June-only point estimate falls to +3.5%.
- Rich-current B4 broader shape: 70 rows / 29 dates, d1 YES ROI +30.8%, CI `[-33.0%, +105.7%]`, top5-removed -17.2%; not strong enough to live.
- Fresh 7/04 shadow: 4 unique tokens. All combined MTM -30.8%; false-fade subset +7.2%; B4 -30.9%. This is mark-to-bid, not official settlement.
- 7/05 UTC shadow so far: 0 token triggers. This is trigger-frequency evidence only, not edge evidence.

## Expected Forward

- Frequency: sparse and bursty, not daily stable flow. Current best estimate is 0-4 token triggers on active days, often 0; do not budget it as 5-6 trades/day.
- If micro-live is eventually approved: `$1/token`, taker entry, hold to settlement, no TP/stop, and only after fresh forward + depth replay passes.
- Main live blocker: execution depth. Prior research found top-of-book around 8-16 shares in the relevant d1 leg, so historical full-fill backtests overstate what a real order can buy.

## Action

Keep the zero-notional shadow loop running. Next required research is HeadB depth-aware replay: exact entry-time orderbook depth, simulated $1 fill feasibility, and overshoot-vs-single-step branch attribution. Live promotion needs fresh trigger rows plus positive depth-adjusted results.
