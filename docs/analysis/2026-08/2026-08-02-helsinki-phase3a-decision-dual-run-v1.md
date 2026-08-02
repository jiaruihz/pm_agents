# Helsinki Phase 3A decision dual-run v1

## Status

`superseded migration method / evidence retained / no longer an activation plan`

2026-08-02 direct-migration decision: dual-run is no longer the rollout method. The
byte-level parity evidence in this report remains valid, but runtime v3 becomes the sole
active contract authority and legacy v2 is retained read-only. Current implementation and
full-flow evidence are recorded in
`2026-08-02-city-probability-runtime-v3-direct-migration-v1.md`.

This change adds a non-authoritative Phase-2 decision sink to the existing
`city_probability_shadow_v2` runtime. Legacy evaluations and paper intents remain the
authority and remain append-only. The sink is restricted to Helsinki, writes a separate
directory, has no venue client, and can only emit zero-size `TradeIntent` rows.

Production was not restarted. At the 2026-08-02 06:04 UTC preflight the production
manifest was `critical`, the canonical JRS tmux context failed its write probe, and the
city probability session was absent. Restarting the canonical tmux server would also
affect active live processes and therefore requires a separate explicit production
maintenance authorization.

## Implementation

- `DecisionContractJournalSink` converts each legacy evaluation to the shared
  information-event, checkpoint, `ModelOutput`, and `SignalCandidate` bundle. Selected
  paper intents enrich the same candidate and create a zero-notional `TradeIntent`.
- Runtime injection keeps model code and legacy journals unchanged. The committed config
  enables the sink only for Helsinki and writes to
  `/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_shadow_vnext/helsinki`.
- Checkpoint blockers are copied with stable IDs. Stale books now use the expected
  `stale_market_expression` blocker instead of raising a new age-dependent exception on
  every loop.
- The materialization CLI is limited to `/tmp`, `runtime/research`, or
  `research_outputs`; it cannot write the canonical or production runtime DB.
- Runtime identity now hashes the loaded sink module as well as model adapters and the
  entrypoint. Neither config metadata nor the sink can grant live mode.

## Deployed raw parity

Input snapshot:

- 180 Helsinki evaluation rows: 10 for target date 2026-07-31 and 170 for 2026-08-01;
- 100 scored and 80 structured one-sided `not_scorable` rows;
- 6 zero-notional paper intents;
- 2 target-date 2026-08-02 checkpoint blockers: `awaiting_official_observation` and
  `missing_market_expression`.

The materialized vNext side contained 186 append records because each of the six selected
candidates has an unselected evaluation observation and a selected intent observation.
They consolidate to 180 unique candidates, 180 model outputs, 6 selected candidates, and
6 zero-notional intents. Temporary canonical reconciliation produced 180 candidates with
`candidate_delta=0`.

Legacy-direct and vNext-through-sink bridge outputs were byte-identical:

| artifact | rows | SHA-256 | missing / extra / changed IDs |
|---|---:|---|---:|
| model outputs | 180 | `3fb828bf7aceacb8f0e9b631543b26ad8b62c67c8819caf2aae2241b9205eda9` | 0 / 0 / 0 |
| signal candidates | 180 | `c9b656c0937cf1a7307548239b7659c6d8c24388120b62c5f5124356af76741f` | 0 / 0 / 0 |
| trade intents | 6 | `95e5c7e2190d347240f2842285e78e58771630ece4f1d80218a65e645e58c86f` | 0 / 0 / 0 |

The 80 historical one-sided rows lack physical `*_input_ref` objects in their already
written legacy lineage, although they retain source/forecast/book hashes. The vNext sink
does not invent those paths, so this is a shared historical lineage gap rather than a
parity difference. Current Helsinki adapter code writes book, official, and forecast
input refs; a newly activated forward window must confirm that behavior.

## Defects and impact radius

Two independent operational defects were found:

1. Stale books were raised as age-dependent `RuntimeError` messages. This produced 94
   Helsinki error rows from 2026-08-01 19:15:12 to 20:59:47 UTC and 61 Tokyo rows from
   13:02:40 to 14:06:32 UTC. The root fix converts the state to a stable, deduplicated
   checkpoint blocker keyed by the captured book. It changes no model probability or
   eligibility decision.
2. The city runner stopped after a JRS `PermissionError` while opening
   `evaluations.jsonl`; its last summary was 2026-08-02 02:56:54 UTC and the traceback was
   written around 02:58 UTC. At 06:04 UTC the session was still absent, the shared JRS
   write probe failed, the upstream high-frequency `latest.json` was also stale at
   02:57 UTC, and `weather-canonical-refresh` had last exit status 1. This is a shared JRS
   permission-context outage, not a decision-sink defect.

Counterfactual execution impact for both defects is zero: this instance recorded
`orders_submitted=0`; all six intents had zero shares and zero notional; fills, fees, and
PnL delta are all zero. The stale-error fix changes 155 repeated error poll rows into
stable coverage incidents; it does not add or remove any historical candidate or intent.

## Phase 3A gate

- Deployed-journal candidate/intent parity: PASS.
- Checkpoint blocker preservation and UTC/business-date boundary: PASS on the two
  2026-08-02 boundary blockers.
- Zero-notional safety: PASS in code, config, tests, and historical impact.
- Exception-storm root fix: PASS in code; forward production confirmation pending.
- Three complete local active windows: FAIL, because deployed raw currently contains only
  two Helsinki target dates and the 2026-07-31 window is partial.
- Production dual-run activation: BLOCKED by the shared JRS permission outage and critical
  manifest.

Therefore Phase 3A is not cut over. The safe next action is a separately authorized JRS
maintenance restart, followed by git-first deployment of this commit, process/raw/API
verification, and collection of a third complete Helsinki active window. Legacy remains
the rollback authority and is not deleted.

## Verification

- Focused Phase 1/2/3 city suites: 44 passed.
- Real materialization summary hash:
  `2d6f369988c0e900a42c99d43b619562c916194a5707f50192c5ad7b9bb2dcba`.
- Real rows: 180 evaluations, 6 paper intents, 2 blockers, 0 conversion errors, 0 orders,
  and 0 notional.
