# US MADISHF execution / competition v1

Generated: `2026-07-19T02:35:46.282067+00:00`
Status: `research_snapshot`; no live authorization

## Conclusion

The present IEM-MADISHF route has no demonstrated executable US edge. It is both too late and adversely selected: among `23` settled production-runner candidates, `22` were directionally correct, but `0` correct candidates became executable under the policy active at the time during the next 10 minutes. The sole false Atlanta signal became executable on the next cycle and filled `15` shares: `10` taker plus a later `5`-share maker fill.

This does **not** prove that raw one-minute ASOS has no information. It proves that the current route -- IEM archive family split, 5-minute polling, then persistent confirmation -- reaches the book after useful liquidity has normally disappeared. A lower-latency direct MADIS/OMO experiment is still testable, but only as zero-notional telemetry.

## Fixed denominator

- signal: every unique `noaa_madis_hfmetar` `cross_candidate` written by `fast_source_prev_no_trial_v2` (`27` rows, `23` settled)
- expression: BUY NO on the runner-resolved previous Fahrenheit bracket
- label: native-F WU-aligned settled market winner
- execution: direct CLOB best ask and top size at runner decision; 10-share taker requirement; fee `0.05*p*(1-p)`
- duplicate diagnostic: `20` first candidates by market token

## Adverse selection

- correct signals executable under the historical policy: `0/22 (0.0%)`
- correct signals executable under hypothetical `max_no_ask=0.97` with the same 10-share requirement: `0/22 (0.0%)`
- correct signals becoming executable within 10 minutes under historical policy: `0/22 (0.0%)`
- correct signals becoming executable within 10 minutes at max 0.97: `0/22 (0.0%)`
- false signals executable on the first book read: `0/1 (0.0%)`
- false signals becoming executable within 10 minutes: `1/1 (100.0%)`
- false signals actually filled: `1/1 (100.0%)`
- false-signal fill impact: `15` shares, `$13.00` principal + `$0.05655` verified fees = `$13.05655` realized loss
- median observation → our first-seen lag: `23.9` minutes
- median first-seen → runner direct-book read: `10.1` seconds

The 0.94→0.97 threshold change does not recover these trades. One correct Atlanta candidate printed at exactly 0.97, but only 4.22 shares were on the top ask versus the 10-share taker requirement; every other correct candidate was no-ask or above 0.97.

## City execution record

| city | settled candidates | correct | executable within 10m | filled events / shares | median source lag |
|---|---:|---:|---:|---:|---:|
| `Atlanta` | 12 | 11 | 1 | 1 / 15sh | 23.9m |
| `Miami` | 5 | 5 | 0 | 0 / 0sh | 23.2m |
| `SanFrancisco` | 6 | 6 | 0 | 0 / 0sh | 23.4m |

## Was the market already gone before our source arrived?

Using the 15-minute archive snapshots around each source observation, deduplicated to the first candidate per old-bracket token:

- already unexecutable before the MADISHF observation timestamp: `4`
- executable before, lost by the next snapshot after the observation: `1`
- still executable at the next snapshot: `2`
- archive coverage gap: `10`

`already unexecutable` means the prior-bracket NO had no ask, ask >0.97, or <10 top shares before the source observation timestamp. Those rows are not evidence that MADISHF moved the market; they indicate forecast/prior observations/another feed had already resolved the expression. `lost within next 15m` is the slice where a genuinely faster feed could plausibly compete.

The last pre-observation snapshot was executable for `3` first-market events: `2` correct and `1` false. A mechanical 10-share replay at those archived asks produces `$-4.39` fee-adjusted PnL on `$24.39` cost (`-18.0%`). This is only an Atlanta-heavy three-row diagnostic, not a backtest, but it shows that lower latency alone does not cure source adverse selection.

## Source vs execution diagnosis

1. **Execution chain after first-seen is not the bottleneck.** The runner reads the direct book a median `10.1` seconds after source first-seen.
2. **The data route is late.** Observation-to-first-seen is a median `23.9` minutes. This route reads IEM's ASOS archive, not a direct real-time OMO stream.
3. **US books are competitive/adversely selective at this latency.** Correct signals are priced to ~1/no-ask; the sole bad source print retained a cheap 0.87 taker ask and then filled a resting 0.86 maker child 45 seconds later.
4. **Source correctness alone is insufficient.** `22/23` directional correctness looks strong, but executable correctness is `0/22`. Backtests that mark at a stale or synthetic price would invert this conclusion.

## Upstream latency reality

- IEM describes this ASOS archive as synced from real-time ingest every 10 minutes: <https://mesonet.agron.iastate.edu/request/download.phtml?network=GN__ASOS>.
- NOAA says MADIS stage-2-QC real-time data are available on average 8 minutes after receipt and full-QC data after 11 minutes: <https://madis.ncep.noaa.gov/madis_database.shtml>.
- NOAA recommends LDM for the fastest real-time access: <https://madis.ncep.noaa.gov/madis_ui.shtml>.
- NOAA also states that OMO/one-minute ASOS is the raw minute value and does not have METAR quality control: <https://madis.ncep.noaa.gov/madis_metar.shtml>.

Therefore an ordinary processed MADIS/HTTPS replacement may still miss the observed 3-8 minute liquidity windows. Only raw LDM/OMO telemetry can test the remaining speed hypothesis, and removing QC increases exactly the false-print risk exposed by Atlanta.

## Remaining opportunity and action

- Keep Atlanta, Miami, and SanFrancisco MADISHF expressions in shadow; do not add other US cities to live.
- Stop treating IEM `MADISHF` as the fast production feed. Retain it as a historical/source-alignment input.
- The only worthwhile next experiment is a direct MADIS OMO/LDM (or another feed with measured ingest timestamps) zero-notional collector. Pre-register success as: median first-seen <5 minutes with p90 <8 minutes, at least 30 settled persistent events, direct-book coverage >=80%, and positive fee/depth-adjusted residual on the same rows. Do not route orders during collection.
- Prioritize only `lost_within_next_15m_snapshot` cases. If that slice is empty or direct feed still arrives after repricing, the US previous-bracket-NO speed race should be marked dormant, not widened by threshold or size changes.

## Files

- `runner_events.csv`: production candidate/fill denominator
- `repricing_panel.csv`: production-runner direct books at 0/30/60/120/300 seconds
- `archive_absorption_panel.csv`: pre/post observation archive books
- `first_candidate_by_market.csv`: duplicate-controlled competition denominator
- `pre_observation_executable_proxy.csv`: tiny observation-time feasibility diagnostic

## Contract

significance=NA; baseline=direct market; forward=FAIL for current route; conclusion=current IEM route disproven for live, direct OMO experiment inconclusive
