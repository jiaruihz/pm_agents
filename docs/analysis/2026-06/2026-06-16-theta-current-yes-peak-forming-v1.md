# Theta Current YES Peak Forming v1

Status: research_only / shadow_candidate_candidate
Generated: 2026-06-16T09:34:49+00:00

Target metric: `peak_forming_current_yes` = current temperature is still at the observed running max, but the model estimates the current bracket is likely to remain the final max bracket.

## Data Self-Check

- fact_built_at_utc: `2026-06-15T16:31:04.483297+00:00`
- fact_trades trade_class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`
- settlement_status: `[{'settlement_status': None, 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`
- fact_signal_candidates coverage: `{'rows': 30140, 'eligible': 10366, 'paper_ordered': 3968, 'live_filled': 348}`
- CLOB orders/fills join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`

## Human Verdict

There are two separate edges, and they should stay as separate branches.

- `post_decline_confirmed`: wait for a visible temperature fade. This is the current live branch: lower volume, cleaner evidence.
- `peak_forming`: enter while temperature is still at the running max. This can find cheaper asks, but only the early h13 slice looks worth shadowing; h14/h15 plateau is noisy and should not go live.

Recommended action: keep current live unchanged, add a paper/shadow branch for `peak_h13_ev05` only. Do not deploy real orders until we collect fresh-book telemetry around METAR update minutes.

## Branch Comparison

| slice | orders | active days | orders/day | win | ROI +2c | model EV | avg ask | avg p | avg edge |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| post_decline_live_ev05 | 30 | 11 | 2.7 | +93.3% | +14.7% | $13.39 | 0.802 | 0.894 | +9.2% |
| peak_all_ev05 | 81 | 14 | 5.8 | +77.8% | -0.4% | $43.88 | 0.771 | 0.872 | +10.2% |
| peak_h13_ev05 | 19 | 12 | 1.6 | +89.5% | +17.1% | $11.71 | 0.762 | 0.873 | +11.1% |
| peak_h14_ev05 | 34 | 12 | 2.8 | +76.5% | -1.7% | $19.27 | 0.764 | 0.869 | +10.5% |
| peak_h15_ev05 | 34 | 11 | 3.1 | +70.6% | -11.9% | $18.42 | 0.792 | 0.896 | +10.4% |
| peak_h13_ev03 | 31 | 13 | 2.4 | +83.9% | +7.0% | $13.13 | 0.777 | 0.860 | +8.3% |

## Key Read

- The confirmed-decline branch remains the cleaner live branch: about 30 holdout orders, 90%+ win rate, and positive +2c ROI.
- Plateau as a broad rule is not good enough. The full peak slice has more volume, but win rate/ROI are dragged down by later hours.
- The interesting new branch is `peak_h13_ev05`: it has more pre-confirmation character, cheaper average ask, and strong point estimates in holdout.
- h14/h15 plateau should be treated as a warning: by then the market and weather path behave differently, and the raw model overestimates some cases.

## METAR Update-Minute Execution Risk

This report uses the existing half-hour orderbook replay and weather feature rows. That means it can understate execution error around a METAR update minute:

- The weather observation can update first, then the market can reprice within seconds.
- A backtest row may pair the new weather state with an orderbook quote that is stale by one snapshot interval or by a fast repricing burst.
- This is exactly why the live branch now uses `fresh_ask <= snapshot_ask + 0.02`; the peak-forming shadow branch must record the same fresh-book delta before any live discussion.

Required shadow telemetry fields:

- `snapshot_ask`, `fresh_best_ask`, `fresh_best_bid`, `fresh_ask_size`
- `fresh_ask_minus_snapshot_ask`
- `snapshot_age_seconds`
- `metar_last_obs_age_seconds` if available
- `decision_second_within_minute`
- `would_trade_after_fresh_book_guard`

## Candidate Shadow Rule

```text
branch: theta_current_yes_peak_forming_v1
side: BUY_YES current running-max bracket
state: decline_c == 0 / still at running max
time: local hour == 13
price: ask >= 0.55
model: p_yes_win >= 0.5 and p_yes_win - ask >= 0.05
execution telemetry: fresh_ask <= snapshot_ask + 0.02, but shadow-only first
size: zero-notional shadow / paper only
```

## Output

- JSON: `docs/analysis/2026-06/2026-06-16-theta-current-yes-peak-forming-v1.json`
