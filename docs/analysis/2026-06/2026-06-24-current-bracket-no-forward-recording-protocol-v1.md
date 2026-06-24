# Current-Bracket NO Forward Recording Protocol V1

## 结论

已建立一个冻结规则的 PIT forward ledger。这个不是挂真钱/zero-notional runner，而是固定训练截止日和候选版本，用 cutoff 后的 point-in-time replay 持续记录 benchmark/champion/challenger 的候选、size multiplier 和结算表现。

Verdict: `frozen_forward_ledger_started`，live_ready=`False`。

## Frozen Protocol

- Freeze date: `2026-06-24`
- Training cutoff: `2026-06-22`
- Base notional: `$5.00` per candidate before multiplier
- Benchmark: `benchmark_full_size_base_p40`
- Champion: `champion_trade_cap_soft_size`
- Challenger: `challenger_overconf_cap_soft_size`
- Diagnostic: `diagnostic_principled_cap_soft_size`

## Current Ledger State

- Candidate rows after cutoff: `9`
- Settled candidate rows: `0`
- Open candidate rows: `9`
- Dates: `2026-06-23`..`2026-06-23`

## Settled Policy Totals

| policy | settled_dates | candidate_days | weighted_notional_usd | settled_profit_usd | settled_roi |
| --- | --- | --- | --- | --- | --- |

## Daily Settled Replay

| target_date | policy | candidates | weighted_notional_usd | settled_profit_usd | settled_roi | win_rate | avg_p_cross | avg_p_cap | overconf_count |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Open Forward Rows

| target_date | policy | candidates | weighted_notional_usd | avg_p_cross | avg_p_cap | overconf_count |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-06-23 | benchmark_full_size_base_p40 | 9 | $+45.00 | 0.723 | 0.615 | 1 |
| 2026-06-23 | challenger_overconf_cap_soft_size | 9 | $+16.16 | 0.723 | 0.615 | 1 |
| 2026-06-23 | champion_trade_cap_soft_size | 9 | $+24.24 | 0.723 | 0.615 | 1 |
| 2026-06-23 | diagnostic_principled_cap_soft_size | 9 | $+15.26 | 0.723 | 0.615 | 1 |

## Promotion Rules

1. Research -> forward_candidate: already satisfied by this frozen ledger.
2. Forward_candidate -> paper/shadow-runner: require at least 10 new settled forward dates, >=80 settled candidates, champion ROI > 0, champion excess over benchmark > +5pp, and worst rolling 2-day weighted loss improved by >=30% versus benchmark.
3. Paper/shadow-runner -> tiny live review: require at least 20 settled forward dates, CLOB/PIT coverage clean, champion ROI positive after costs, no unresolved settlement/source anomaly, and a separate execution/capacity review.
4. Any rule change resets the freeze version. Old ledger remains evidence, but cannot be merged into the new version's forward proof.

## Files

- Protocol JSON: `docs/analysis/2026-06/generated/current_bracket_no_forward_replay_v1/protocol_summary.json`
- Candidate ledger: `docs/analysis/2026-06/generated/current_bracket_no_forward_replay_v1/frozen_forward_candidate_ledger.csv`
- Daily summary: `docs/analysis/2026-06/generated/current_bracket_no_forward_replay_v1/frozen_forward_daily_policy_summary.csv`
