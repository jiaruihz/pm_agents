# Regime-routed NO Live Case Review

## Conclusion

最近三天不是一个 bug，而是四类问题同时暴露：重复下单、peak-clock 未进旧 live、bracket escape margin 太薄、以及 forecast overestimate / mixing 风险。

- live cases reviewed: 6
- total filled cost: $+16.73
- settled PnL currently in fact_trades: $+8.83
- CLOB fill coverage gate: pass_before_case_review

## Cases

| city | target | bracket | entry | cost | fact/result | gamma yes/no | mechanism | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NYC | 2026-06-25 | 82-83 | 0.315 | $+3.47 | settled pnl $+7.53 | 0.000/1.000 | day=None; state=None; peak_delta=None; gap_run=None; rh=None; wind=None | duplicate_live_submission_but_won |
| NYC | 2026-06-25 | 82-83 | 0.370 | $+2.53 | settled pnl $+4.31 | 0.000/1.000 | day=None; state=None; peak_delta=None; gap_run=None; rh=None; wind=None | duplicate_live_submission_but_won |
| Seattle | 2026-06-25 | 64-65 | 0.529 | $+3.00 | settled pnl $-3.00 | 1.000/0.000 | day=day_marginal_runway; state=active_warming; peak_delta=None; gap_run=1.259999999999991; rh=64.85950389891138; wind=7.0 | lost_thin_bracket_escape_margin |
| Manila | 2026-06-26 | 30 | 0.450 | $+2.85 | open in fact | 0.001/1.000 | day=day_marginal_runway; state=pullback_uncertain; peak_delta=None; gap_run=1.3000000000000007; rh=100.0; wind=5.0 | appears_winning_but_pullback_state_needs_review |
| Chongqing | 2026-06-26 | 23 | 0.290 | $+3.47 | open in fact | 0.985/0.015 | day=day_marginal_runway; state=fresh_high; peak_delta=None; gap_run=0.6000000000000014; rh=100.0; wind=8.0 | bad_peak_clock_and_thin_margin |
| Karachi | 2026-06-26 | 35 | 0.270 | $+1.41 | open in fact | 0.976/0.025 | day=day_marginal_runway; state=active_warming; peak_delta=-0.9916666666666671; gap_run=1.2999999999999972; rh=46.96963437807198; wind=19.0 | post_fix_loss_forecast_overestimate_windy_mixing |

## Root Causes

1. `NYC`: duplicate city/date/token submission happened before duplicate live gate was fixed. It won, but process-wise it was wrong.
2. `Seattle`: peak was still ahead and trend was warm, but forecast max was only 65.3F against bracket 64-65F. This is too thin for a current-bracket NO.
3. `Chongqing`: old live lacked peak-clock enforcement; decision was after forecast peak and margin was only +0.6C.
4. `Karachi`: after the peak-clock patch, it still passed because peak was ahead. Current market marks 35C YES near certain, so this is a separate forecast-overestimate / windy-mixing failure.
5. `Manila`: current market marks NO near certain, but it was a pullback-from-high / humid case. It should stay in review because the same shape can become false runway in other cities.

## Actions

- Already deployed: duplicate city/date/token veto, peak-clock sizing, current-NO after-peak veto.
- Still needed: add `forecast_max - bracket_upper` escape margin as a core current-NO feature; do not rely on `forecast_max - running_max` alone.
- Still needed: log accepted candidate full feature payload in live orders; pre-fix cases required reconstruction from blocked candidates.
- Still needed: isolate windy-mixing / forecast-overestimate failures like Karachi in frozen forward, not by one-off city blacklist.

## Activity-Only Missing Case: San Francisco

The first pass missed one real account fill because it was not in `fact_trades`.

- source: public Polymarket account activity
- trade: San Francisco 2026-06-25 `68-69°F` NO
- activity time: `2026-06-25T19:59:50Z`
- price / size: `0.69 * 5`
- transaction: `0xb476982cb990e1a467048d6d9f4d65f6cfa45b14f3fe7baa4375d0911c763bd0`
- runtime source: `metar_cross_prev_no_shadow`, not regime-routed NO
- runtime order: `0xad04319996cb3160d11745a0d5f1255aeda51f8c5afd6008766fe01e757461e5`
- settlement: market closed YES=`1`, NO=`0`

Mechanism: the runner saw NOAA/METAR `current_temp_c=21.0`, converted it to `running_value=70°F`, treated the 68-69°F bracket as crossed, and bought NO. The market resolves against WU/KSFO whole-degree Fahrenheit; it settled 68-69°F YES. This is a source-basis / rounding failure, not a regime-routed signal failure.

Operational issue: the process name includes `shadow`, but it was running with `--live --confirm-live`. It was stopped on N100 after this review. The dashboard/fact layer also needs an account-activity reconciliation view so real account trades that bypass strategy lineage cannot be invisible to live-case reviews.
