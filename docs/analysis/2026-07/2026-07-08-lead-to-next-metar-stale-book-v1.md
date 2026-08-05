# Lead-to-next-METAR + Stale Book Detector v1

Status: `snapshot`
Generated: `2026-07-08T12:23:12.135732+00:00`

## Verdict

- `lead-to-next-METAR` can be studied now, but current evidence is one-day online telemetry plus source-events history. It is a shadow feature head, not a live strategy.
- `stale-book detector` can be defined, but the current targeted snapshot often has empty executable prices on the relevant bracket (`orderbook_budget_exhausted` / scope skipped), so v1 cannot tell whether the book reacted.
- Causal filter is strict: high-frequency rows with stale observation age are excluded, so first-fetch historical MADIS rows do not become fake foresight.

## Data Snapshot

- high-frequency raw rows: `2326`
- source-event raw rows: `31944`
- causal lead rows: `250`
- stale-book rows with prior paper snapshot: `224`
- high-frequency observation age max: `30.0` minutes
- next-report window max: `90.0` minutes

## Lead Signal

- `next_report_up_prob_head`: rows `250`, signal_rows `100`, precision `0.38`, base_rate `0.212`, recall `0.717`, lift `1.7925`
- `cross_prob_head`: rows `250`, signal_rows `38`, precision `0.4474`, base_rate `0.2`, recall `0.34`, lift `2.2368`
- `overshoot_prob_head`: rows `250`, signal_rows `10`, precision `0.6`, base_rate `0.12`, recall `0.2`, lift `5.0`

## Empirical Probability Buckets

- `hf_round_minus_prev_round` bucket `<= -1`: rows `57`, positive `4`, empirical_prob `0.0702`
- `hf_round_minus_prev_round` bucket `0`: rows `155`, positive `29`, empirical_prob `0.1871`
- `hf_round_minus_prev_round` bucket `1`: rows `32`, positive `14`, empirical_prob `0.4375`
- `hf_round_minus_prev_round` bucket `>= 2`: rows `6`, positive `3`, empirical_prob `0.5`
- `hf_round_minus_prev_day_max` bucket `<= -1`: rows `164`, positive `3`, empirical_prob `0.0183`
- `hf_round_minus_prev_day_max` bucket `0`: rows `76`, positive `21`, empirical_prob `0.2763`
- `hf_round_minus_prev_day_max` bucket `1`: rows `10`, positive `6`, empirical_prob `0.6`
- `hf_round_minus_prev_day_max` bucket `>= 2`: rows `0`, positive `0`, empirical_prob `None`

## Stale Book Coverage

- rows with book: `224`
- rows with current YES ask: `1`
- rows with next YES ask: `0`
- hf-implied overshoot rows: `9`
- detector candidates: `0`
- current YES high candidates: `0`
- next YES cheap candidates: `0`
- current bracket status counts: `{'orderbook_budget_exhausted': 137, 'orderbook_scope_skipped': 58, 'missing': 28, 'ok': 1}`
- next bracket status counts: `{'orderbook_budget_exhausted': 164, 'orderbook_scope_skipped': 60}`

## Output Files

- `lead_rows_csv`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/lead_to_next_metar_stale_book_v1/lead_rows.csv`
- `stale_book_rows_csv`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/generated/lead_to_next_metar_stale_book_v1/stale_book_rows.csv`
- `report_md`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/2026-07-08-lead-to-next-metar-stale-book-v1.md`
- `json`: `/Users/deepsleep/projects/pm_agents/docs/analysis/2026-07/2026-07-08-lead-to-next-metar-stale-book-v1.json`

## Contract Verdict

significance=NA; baseline=NA; forward=NA; conclusion=shadow_candidate

This is a source-timing feature study. No live sizing, order placement, or city-pool change is justified from this v1 sample.
