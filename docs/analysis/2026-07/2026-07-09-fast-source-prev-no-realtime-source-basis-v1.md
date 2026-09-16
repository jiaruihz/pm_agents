# Lead-to-next-METAR + Stale Book Detector v1

Status: `snapshot`
Generated: `2026-07-09T14:11:24.545956+00:00`

## Verdict

- `lead-to-next-METAR` can be studied now, but current evidence is one-day online telemetry plus source-events history. It is a shadow feature head, not a live strategy.
- `stale-book detector` can be defined, but the current targeted snapshot often has empty executable prices on the relevant bracket (`orderbook_budget_exhausted` / scope skipped), so v1 cannot tell whether the book reacted.
- Causal filter is strict: high-frequency rows with stale observation age are excluded, so first-fetch historical MADIS rows do not become fake foresight.

## Data Snapshot

- high-frequency raw rows: `18169`
- source-event raw rows: `51384`
- causal lead rows: `7415`
- stale-book rows with prior paper snapshot: `6537`
- high-frequency observation age max: `30.0` minutes
- next-report window max: `90.0` minutes

### Superseded first-day revision

The same detector first ran on 2026-07-08 with only 2,326 high-frequency raw
rows, 31,944 source-event rows and 250 causal lead rows.  Its 10 overshoot-head
signals had 60% precision against a 12% base rate, but just one of 224
stale-book rows had an executable current-YES ask.  The expanded run below
keeps the same causal filter and output contract while increasing the lead
denominator to 7,415 rows; the first-day point estimate is retained here only
as revision history, not as an independent strategy result.

## Lead Signal

- `next_report_up_prob_head`: rows `7415`, signal_rows `3231`, precision `0.4751`, base_rate `0.3173`, recall `0.6524`, lift `1.4971`
- `cross_prob_head`: rows `7415`, signal_rows `1742`, precision `0.6286`, base_rate `0.3088`, recall `0.4782`, lift `2.0354`
- `overshoot_prob_head`: rows `7415`, signal_rows `553`, precision `0.8336`, base_rate `0.1575`, recall `0.3947`, lift `5.2923`

## Empirical Probability Buckets

- `hf_round_minus_prev_round` bucket `<= -1`: rows `1300`, positive `316`, empirical_prob `0.2431`
- `hf_round_minus_prev_round` bucket `0`: rows `4373`, positive `879`, empirical_prob `0.201`
- `hf_round_minus_prev_round` bucket `1`: rows `1407`, positive `830`, empirical_prob `0.5899`
- `hf_round_minus_prev_round` bucket `>= 2`: rows `335`, positive `265`, empirical_prob `0.791`
- `hf_round_minus_prev_day_max` bucket `<= -1`: rows `4899`, positive `183`, empirical_prob `0.0374`
- `hf_round_minus_prev_day_max` bucket `0`: rows `1963`, positive `524`, empirical_prob `0.2669`
- `hf_round_minus_prev_day_max` bucket `1`: rows `480`, positive `406`, empirical_prob `0.8458`
- `hf_round_minus_prev_day_max` bucket `>= 2`: rows `73`, positive `55`, empirical_prob `0.7534`

## Stale Book Coverage

- rows with book: `6537`
- rows with current YES ask: `223`
- rows with next YES ask: `0`
- hf-implied overshoot rows: `444`
- detector candidates: `0`
- current YES high candidates: `0`
- next YES cheap candidates: `0`
- current bracket status counts: `{'orderbook_budget_exhausted': 4673, 'orderbook_scope_skipped': 1526, 'ok': 223, 'missing': 115}`
- next bracket status counts: `{'orderbook_budget_exhausted': 4750, 'orderbook_scope_skipped': 1787}`

## Output Files

- `lead_rows_csv`: `docs/analysis/2026-07/generated/fast_source_prev_no_realtime_source_basis_20260709/lead_rows.csv`
- `stale_book_rows_csv`: `docs/analysis/2026-07/generated/fast_source_prev_no_realtime_source_basis_20260709/stale_book_rows.csv`
- `report_md`: `docs/analysis/2026-07/2026-07-09-fast-source-prev-no-realtime-source-basis-v1.md`
- `json`: `docs/analysis/2026-07/2026-07-09-fast-source-prev-no-realtime-source-basis-v1.json`

## Contract Verdict

significance=NA; baseline=NA; forward=NA; conclusion=shadow_candidate

This is a source-timing feature study. No live sizing, order placement, or city-pool change is justified from this v1 sample.
