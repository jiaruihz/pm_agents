# Current-YES cadence state live counterfactual v0

Target metric: 对 split current-YES live raw matched orders 做反事实过滤：如果把旧 `peak_forming`/fade 时机换成 cadence-aware 状态层，最近几天成绩会变成什么。

## Data Snapshot

- Window: target_date `2026-06-18`..`2026-06-21`.
- Order files: theta_current_yes_peak_forming_micro_tiny_live_v1_orders.jsonl, theta_current_yes_fade_confirmed_tiny_live_v1_orders.jsonl.
- PnL grain: raw matched order; cost/shares use CLOB immediate `place.makingAmount/takingAmount`.
- Settlement: `settlement_outcomes` by city/target_date/bracket.
- `fact_trades` note: local canonical live_real fills currently stop at 2026-06-11, so this report does not use `fact_trades` for the split current-YES PnL.
- `run_stack.sh` note: fact tables rebuilt, then script exited non-zero only because FE port 5174 stayed busy.

## Funnel

- Raw orders: 40.
- Status counts: {'matched': 37, 'error': 2, 'live': 1}.
- Matched but unsettled: 13; these are excluded from ROI and listed separately.

## Variants

| variant | matched | settled | kept settled | wins-losses | win | ROI | PnL | blocked settled | blocked L/W | date bootstrap 95% ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `actual_matched` | 37 | 24 | 24 | 18-6 | +75.0% | +1.8% | $+1.76 | 0 | 0/0 | [-4.2%, +35.1%] |
| `fade_only_stop_peak_forming` | 37 | 24 | 3 | 3-0 | +100.0% | +19.4% | $+2.11 | 21 | 6/15 | NA |
| `cadence_only` | 37 | 24 | 3 | 3-0 | +100.0% | +19.4% | $+2.11 | 21 | 6/15 | NA |
| `cadence_after_forecast_peak` | 37 | 24 | 1 | 1-0 | +100.0% | +19.0% | $+0.57 | 23 | 6/17 | NA |
| `cadence_no_reheat_strict` | 37 | 24 | 1 | 1-0 | +100.0% | +19.0% | $+0.57 | 23 | 6/17 | NA |

## By Profile

| profile | variant | settled | kept settled | wins-losses | win | ROI | PnL |
|---|---|---:|---:|---:|---:|---:|---:|
| `fade_confirmed` | `actual_matched` | 3 | 3 | 3-0 | +100.0% | +19.4% | $+2.11 |
| `fade_confirmed` | `fade_only_stop_peak_forming` | 3 | 3 | 3-0 | +100.0% | +19.4% | $+2.11 |
| `fade_confirmed` | `cadence_only` | 3 | 3 | 3-0 | +100.0% | +19.4% | $+2.11 |
| `fade_confirmed` | `cadence_after_forecast_peak` | 3 | 1 | 1-0 | +100.0% | +19.0% | $+0.57 |
| `fade_confirmed` | `cadence_no_reheat_strict` | 3 | 1 | 1-0 | +100.0% | +19.0% | $+0.57 |
| `peak_forming_micro` | `actual_matched` | 21 | 21 | 15-6 | +71.4% | -0.4% | $-0.35 |
| `peak_forming_micro` | `fade_only_stop_peak_forming` | 21 | 0 | 0-0 | NA | NA | $+0.00 |
| `peak_forming_micro` | `cadence_only` | 21 | 0 | 0-0 | NA | NA | $+0.00 |
| `peak_forming_micro` | `cadence_after_forecast_peak` | 21 | 0 | 0-0 | NA | NA | $+0.00 |
| `peak_forming_micro` | `cadence_no_reheat_strict` | 21 | 0 | 0-0 | NA | NA | $+0.00 |

## Interpretation

- `fade_only_stop_peak_forming` answers the blunt stop-loss question: what happens if peak-forming real orders are disabled and fade remains.
- `cadence_only` requires a full post-high observation cycle: `minutes_since_running_max - obs_age_min >= max(20m, 0.75 * cadence_min)`.
- `cadence_after_forecast_peak` additionally requires the decision to be at/after the forecast peak.
- `cadence_no_reheat_strict` also requires forecast remaining max not to exceed current METAR max by more than 0.9F.
- Because local observation history was not synced for 2026-06-18..21, this is a conservative telemetry replay, not a full first-high plateau reconstruction.

## Blocked Settled Orders

### fade_only_stop_peak_forming

| created | date | city | bracket | profile | win | PnL | reasons |
|---|---|---|---|---|---:|---:|---|
| 2026-06-18T12:46:03+00:00 | 2026-06-18 | Helsinki | 22 | peak_forming_micro | True | $+1.75 | missing_cadence_telemetry |
| 2026-06-19T10:55:24+00:00 | 2026-06-19 | TelAviv | 29 | peak_forming_micro | True | $+1.73 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-19T10:55:24+00:00 | 2026-06-19 | Istanbul | 24 | peak_forming_micro | True | $+1.67 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T04:14:49+00:00 | 2026-06-20 | Wellington | 17 | peak_forming_micro | True | $+0.43 | no_full_post_high_obs_cycle |
| 2026-06-20T05:24:48+00:00 | 2026-06-20 | Manila | 36 | peak_forming_micro | True | $+1.33 | no_full_post_high_obs_cycle |
| 2026-06-20T05:36:33+00:00 | 2026-06-20 | Busan | 27 | peak_forming_micro | False | $-4.92 | no_full_post_high_obs_cycle |
| 2026-06-20T06:03:59+00:00 | 2026-06-20 | KualaLumpur | 31 | peak_forming_micro | False | $-4.96 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T07:00:05+00:00 | 2026-06-20 | Wuhan | 31 | peak_forming_micro | False | $-1.93 | no_full_post_high_obs_cycle |
| 2026-06-20T08:00:54+00:00 | 2026-06-20 | Chongqing | 33 | peak_forming_micro | True | $+2.89 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T08:30:37+00:00 | 2026-06-20 | Lucknow | 40 | peak_forming_micro | False | $-4.92 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T08:48:33+00:00 | 2026-06-20 | Karachi | 34 | peak_forming_micro | False | $-4.86 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T11:11:23+00:00 | 2026-06-20 | Jeddah | 37 | peak_forming_micro | True | $+1.91 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T11:32:47+00:00 | 2026-06-20 | Helsinki | 24 | peak_forming_micro | True | $+3.32 | no_full_post_high_obs_cycle |
| 2026-06-20T12:35:51+00:00 | 2026-06-20 | Istanbul | 24 | peak_forming_micro | True | $+1.60 | missing_cadence_telemetry, forecast_peak_still_future |
| 2026-06-20T14:25:19+00:00 | 2026-06-20 | Amsterdam | 25 | peak_forming_micro | True | $+0.72 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T14:38:14+00:00 | 2026-06-20 | Milan | 34 | peak_forming_micro | True | $+0.70 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T14:52:13+00:00 | 2026-06-20 | Paris | 35 | peak_forming_micro | True | $+2.88 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T17:04:16+00:00 | 2026-06-20 | BuenosAires | 15 | peak_forming_micro | False | $-2.99 | no_full_post_high_obs_cycle |
| 2026-06-20T17:50:04+00:00 | 2026-06-20 | SaoPaulo | 24 | peak_forming_micro | True | $+1.16 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max |
| 2026-06-20T20:12:12+00:00 | 2026-06-20 | SanFrancisco | 70-71 | peak_forming_micro | True | $+1.16 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T20:30:20+00:00 | 2026-06-20 | Austin | 88-89 | peak_forming_micro | True | $+0.97 | no_full_post_high_obs_cycle, forecast_peak_still_future |

### cadence_only

| created | date | city | bracket | profile | win | PnL | reasons |
|---|---|---|---|---|---:|---:|---|
| 2026-06-18T12:46:03+00:00 | 2026-06-18 | Helsinki | 22 | peak_forming_micro | True | $+1.75 | missing_cadence_telemetry |
| 2026-06-19T10:55:24+00:00 | 2026-06-19 | TelAviv | 29 | peak_forming_micro | True | $+1.73 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-19T10:55:24+00:00 | 2026-06-19 | Istanbul | 24 | peak_forming_micro | True | $+1.67 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T04:14:49+00:00 | 2026-06-20 | Wellington | 17 | peak_forming_micro | True | $+0.43 | no_full_post_high_obs_cycle |
| 2026-06-20T05:24:48+00:00 | 2026-06-20 | Manila | 36 | peak_forming_micro | True | $+1.33 | no_full_post_high_obs_cycle |
| 2026-06-20T05:36:33+00:00 | 2026-06-20 | Busan | 27 | peak_forming_micro | False | $-4.92 | no_full_post_high_obs_cycle |
| 2026-06-20T06:03:59+00:00 | 2026-06-20 | KualaLumpur | 31 | peak_forming_micro | False | $-4.96 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T07:00:05+00:00 | 2026-06-20 | Wuhan | 31 | peak_forming_micro | False | $-1.93 | no_full_post_high_obs_cycle |
| 2026-06-20T08:00:54+00:00 | 2026-06-20 | Chongqing | 33 | peak_forming_micro | True | $+2.89 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T08:30:37+00:00 | 2026-06-20 | Lucknow | 40 | peak_forming_micro | False | $-4.92 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T08:48:33+00:00 | 2026-06-20 | Karachi | 34 | peak_forming_micro | False | $-4.86 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T11:11:23+00:00 | 2026-06-20 | Jeddah | 37 | peak_forming_micro | True | $+1.91 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T11:32:47+00:00 | 2026-06-20 | Helsinki | 24 | peak_forming_micro | True | $+3.32 | no_full_post_high_obs_cycle |
| 2026-06-20T12:35:51+00:00 | 2026-06-20 | Istanbul | 24 | peak_forming_micro | True | $+1.60 | missing_cadence_telemetry, forecast_peak_still_future |
| 2026-06-20T14:25:19+00:00 | 2026-06-20 | Amsterdam | 25 | peak_forming_micro | True | $+0.72 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T14:38:14+00:00 | 2026-06-20 | Milan | 34 | peak_forming_micro | True | $+0.70 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T14:52:13+00:00 | 2026-06-20 | Paris | 35 | peak_forming_micro | True | $+2.88 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T17:04:16+00:00 | 2026-06-20 | BuenosAires | 15 | peak_forming_micro | False | $-2.99 | no_full_post_high_obs_cycle |
| 2026-06-20T17:50:04+00:00 | 2026-06-20 | SaoPaulo | 24 | peak_forming_micro | True | $+1.16 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max |
| 2026-06-20T20:12:12+00:00 | 2026-06-20 | SanFrancisco | 70-71 | peak_forming_micro | True | $+1.16 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T20:30:20+00:00 | 2026-06-20 | Austin | 88-89 | peak_forming_micro | True | $+0.97 | no_full_post_high_obs_cycle, forecast_peak_still_future |

### cadence_after_forecast_peak

| created | date | city | bracket | profile | win | PnL | reasons |
|---|---|---|---|---|---:|---:|---|
| 2026-06-18T12:46:03+00:00 | 2026-06-18 | Helsinki | 22 | peak_forming_micro | True | $+1.75 | missing_cadence_telemetry |
| 2026-06-19T10:55:24+00:00 | 2026-06-19 | TelAviv | 29 | peak_forming_micro | True | $+1.73 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-19T10:55:24+00:00 | 2026-06-19 | Istanbul | 24 | peak_forming_micro | True | $+1.67 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T04:14:49+00:00 | 2026-06-20 | Wellington | 17 | peak_forming_micro | True | $+0.43 | no_full_post_high_obs_cycle |
| 2026-06-20T05:24:48+00:00 | 2026-06-20 | Manila | 36 | peak_forming_micro | True | $+1.33 | no_full_post_high_obs_cycle |
| 2026-06-20T05:36:33+00:00 | 2026-06-20 | Busan | 27 | peak_forming_micro | False | $-4.92 | no_full_post_high_obs_cycle |
| 2026-06-20T06:03:59+00:00 | 2026-06-20 | KualaLumpur | 31 | peak_forming_micro | False | $-4.96 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T07:00:05+00:00 | 2026-06-20 | Wuhan | 31 | peak_forming_micro | False | $-1.93 | no_full_post_high_obs_cycle |
| 2026-06-20T08:00:54+00:00 | 2026-06-20 | Chongqing | 33 | peak_forming_micro | True | $+2.89 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T08:30:37+00:00 | 2026-06-20 | Lucknow | 40 | peak_forming_micro | False | $-4.92 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T08:48:33+00:00 | 2026-06-20 | Karachi | 34 | peak_forming_micro | False | $-4.86 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T11:11:23+00:00 | 2026-06-20 | Jeddah | 37 | peak_forming_micro | True | $+1.91 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T11:32:47+00:00 | 2026-06-20 | Helsinki | 24 | peak_forming_micro | True | $+3.32 | no_full_post_high_obs_cycle |
| 2026-06-20T12:35:51+00:00 | 2026-06-20 | Istanbul | 24 | peak_forming_micro | True | $+1.60 | missing_cadence_telemetry, forecast_peak_still_future |
| 2026-06-20T14:25:19+00:00 | 2026-06-20 | Amsterdam | 25 | peak_forming_micro | True | $+0.72 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T14:38:14+00:00 | 2026-06-20 | Milan | 34 | peak_forming_micro | True | $+0.70 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T14:52:13+00:00 | 2026-06-20 | Paris | 35 | peak_forming_micro | True | $+2.88 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T17:04:16+00:00 | 2026-06-20 | BuenosAires | 15 | peak_forming_micro | False | $-2.99 | no_full_post_high_obs_cycle |
| 2026-06-20T17:50:04+00:00 | 2026-06-20 | SaoPaulo | 24 | peak_forming_micro | True | $+1.16 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max |
| 2026-06-20T20:12:12+00:00 | 2026-06-20 | SanFrancisco | 70-71 | peak_forming_micro | True | $+1.16 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T20:30:20+00:00 | 2026-06-20 | Austin | 88-89 | peak_forming_micro | True | $+0.97 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T11:01:24+00:00 | 2026-06-20 | Jeddah | 37 | fade_confirmed | True | $+0.94 | forecast_peak_still_future, forecast_remaining_above_current_max |
| 2026-06-20T17:01:03+00:00 | 2026-06-20 | SaoPaulo | 24 | fade_confirmed | True | $+0.60 | forecast_peak_still_future, forecast_remaining_above_current_max |

### cadence_no_reheat_strict

| created | date | city | bracket | profile | win | PnL | reasons |
|---|---|---|---|---|---:|---:|---|
| 2026-06-18T12:46:03+00:00 | 2026-06-18 | Helsinki | 22 | peak_forming_micro | True | $+1.75 | missing_cadence_telemetry |
| 2026-06-19T10:55:24+00:00 | 2026-06-19 | TelAviv | 29 | peak_forming_micro | True | $+1.73 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-19T10:55:24+00:00 | 2026-06-19 | Istanbul | 24 | peak_forming_micro | True | $+1.67 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T04:14:49+00:00 | 2026-06-20 | Wellington | 17 | peak_forming_micro | True | $+0.43 | no_full_post_high_obs_cycle |
| 2026-06-20T05:24:48+00:00 | 2026-06-20 | Manila | 36 | peak_forming_micro | True | $+1.33 | no_full_post_high_obs_cycle |
| 2026-06-20T05:36:33+00:00 | 2026-06-20 | Busan | 27 | peak_forming_micro | False | $-4.92 | no_full_post_high_obs_cycle |
| 2026-06-20T06:03:59+00:00 | 2026-06-20 | KualaLumpur | 31 | peak_forming_micro | False | $-4.96 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T07:00:05+00:00 | 2026-06-20 | Wuhan | 31 | peak_forming_micro | False | $-1.93 | no_full_post_high_obs_cycle |
| 2026-06-20T08:00:54+00:00 | 2026-06-20 | Chongqing | 33 | peak_forming_micro | True | $+2.89 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T08:30:37+00:00 | 2026-06-20 | Lucknow | 40 | peak_forming_micro | False | $-4.92 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T08:48:33+00:00 | 2026-06-20 | Karachi | 34 | peak_forming_micro | False | $-4.86 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T11:11:23+00:00 | 2026-06-20 | Jeddah | 37 | peak_forming_micro | True | $+1.91 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T11:32:47+00:00 | 2026-06-20 | Helsinki | 24 | peak_forming_micro | True | $+3.32 | no_full_post_high_obs_cycle |
| 2026-06-20T12:35:51+00:00 | 2026-06-20 | Istanbul | 24 | peak_forming_micro | True | $+1.60 | missing_cadence_telemetry, forecast_peak_still_future |
| 2026-06-20T14:25:19+00:00 | 2026-06-20 | Amsterdam | 25 | peak_forming_micro | True | $+0.72 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T14:38:14+00:00 | 2026-06-20 | Milan | 34 | peak_forming_micro | True | $+0.70 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max, latest_not_same_running_bracket |
| 2026-06-20T14:52:13+00:00 | 2026-06-20 | Paris | 35 | peak_forming_micro | True | $+2.88 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T17:04:16+00:00 | 2026-06-20 | BuenosAires | 15 | peak_forming_micro | False | $-2.99 | no_full_post_high_obs_cycle |
| 2026-06-20T17:50:04+00:00 | 2026-06-20 | SaoPaulo | 24 | peak_forming_micro | True | $+1.16 | no_full_post_high_obs_cycle, forecast_peak_still_future, forecast_remaining_above_current_max |
| 2026-06-20T20:12:12+00:00 | 2026-06-20 | SanFrancisco | 70-71 | peak_forming_micro | True | $+1.16 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T20:30:20+00:00 | 2026-06-20 | Austin | 88-89 | peak_forming_micro | True | $+0.97 | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-20T11:01:24+00:00 | 2026-06-20 | Jeddah | 37 | fade_confirmed | True | $+0.94 | forecast_peak_still_future, forecast_remaining_above_current_max |
| 2026-06-20T17:01:03+00:00 | 2026-06-20 | SaoPaulo | 24 | fade_confirmed | True | $+0.60 | forecast_peak_still_future, forecast_remaining_above_current_max |

## Pending Matched Orders

| created | date | city | bracket | profile | kept by cadence_no_reheat_strict | reasons |
|---|---|---|---|---|---:|---|
| 2026-06-21T03:03:59+00:00 | 2026-06-21 | Wuhan | 28 | peak_forming_micro | False | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-21T05:14:14+00:00 | 2026-06-21 | Taipei | 37 | peak_forming_micro | False | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-21T06:08:05+00:00 | 2026-06-21 | Chongqing | 27 | peak_forming_micro | False | no_full_post_high_obs_cycle |
| 2026-06-21T07:12:21+00:00 | 2026-06-21 | Karachi | 34 | peak_forming_micro | False | no_full_post_high_obs_cycle |
| 2026-06-21T07:31:45+00:00 | 2026-06-21 | Singapore | 31 | peak_forming_micro | False | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-21T09:37:22+00:00 | 2026-06-21 | Lucknow | 39 | peak_forming_micro | False | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-21T12:02:52+00:00 | 2026-06-21 | Helsinki | 26 | peak_forming_micro | False | no_full_post_high_obs_cycle |
| 2026-06-21T13:35:58+00:00 | 2026-06-21 | London | 28 | peak_forming_micro | False | no_full_post_high_obs_cycle |
| 2026-06-21T14:45:53+00:00 | 2026-06-21 | Paris | 36 | peak_forming_micro | False | no_full_post_high_obs_cycle |
| 2026-06-21T15:11:13+00:00 | 2026-06-21 | Amsterdam | 26 | peak_forming_micro | False | no_full_post_high_obs_cycle, forecast_peak_still_future |
| 2026-06-21T03:10:27+00:00 | 2026-06-21 | Wuhan | 28 | fade_confirmed | False | forecast_peak_still_future |
| 2026-06-21T05:25:05+00:00 | 2026-06-21 | Manila | 36 | fade_confirmed | False |  |
| 2026-06-21T12:40:10+00:00 | 2026-06-21 | Jeddah | 35 | fade_confirmed | False | forecast_peak_still_future |
