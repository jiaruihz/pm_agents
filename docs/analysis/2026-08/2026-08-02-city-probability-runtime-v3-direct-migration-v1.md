# City probability runtime v3 direct migration

## Status

`direct migration implemented / full-flow verified / production activation blocked by JRS`

The city probability runtime no longer uses dual-run as its migration mechanism.
`city_probability_runtime_v3` is the sole active design authority for Helsinki and Tokyo
and writes only shared information-event, checkpoint, `ModelOutput`, `SignalCandidate`,
and zero-notional `TradeIntent` journals. The old
`city_probability_shadow_v2/evaluations|paper_intents|checkpoints|errors` path is marked
`deprecated_read_only`; its starter refuses normal startup and remains available only as
an explicitly acknowledged rollback.

No production process was restarted. The canonical JRS tmux write probe still fails and
the production manifest remains critical, so the git-first production cutover is blocked
before any runtime mutation.

## Direct-migration architecture

- Runtime v3 uses schema `weather_city_probability_runtime_config_v3` and output
  fingerprint `a108e39c36707bc6ed6cc0175d241f49d93442218b047a0534644eb239928985`.
- Dedupe after restart reads `source_evaluation_id`, intent `dedupe_key`, and checkpoint
  IDs from the shared journals. It never reads the deprecated legacy journals.
- Tokyo's `previous_same_bracket_weather_probability_stay` now reads prior model metadata
  from `decision_bundles.jsonl`; this removes the last active model dependency on
  `evaluations.jsonl`.
- Runtime errors use `runtime_errors.jsonl`. Expected stale/missing/one-sided states use
  structured blocker journals. The active output never creates legacy evaluation,
  paper-intent, checkpoint, or error files.
- Config, sink, and contracts all require zero-notional mode. There is no exchange client
  and no metadata path can grant live authority.

## Full historical migration

Inputs were the complete deployed v2 snapshot at migration time plus the exact Helsinki
and Tokyo raw book journals:

- 388 evaluation rows;
- 10 historical zero-notional paper-intent rows;
- 2 checkpoint blockers;
- 212 missing legacy market outcomes recovered only where an exact raw token ID mapped
  uniquely to the raw book outcome.

Outputs:

- 392 append-only decision bundle observations, consolidating to 386 unique model outputs
  and candidates;
- 200 scored candidates and 186 blocked candidates;
- blockers: 83 one-sided interval-censored rows and 103 Tokyo YES expressions whose saved
  token is the NO token;
- 6 valid Helsinki zero-notional intents;
- 4 explicit historical Tokyo intent blockers. Those rows proposed YES but retained only
  the NO token, so the migration preserves them as unexecutable evidence and does not
  guess a YES token;
- 2 checkpoint blockers;
- 0 conversion errors, 0 orders, and 0 notional.

Temporary canonical materialization inserted 172 information events, 283 checkpoints,
386 candidates, and produced `candidate_delta=0`. The migration summary hash is
`fab87ce6a082d16d026534622da76477980065a0d869ee9b0937bd2b1f2b0486`; the canonical bridge
summary hash is
`22d754c86cd21f7329c6703dd9a79178d2243bf0873e9f84f3ef77b2d18f8021`.

## Actual-runtime smoke

The committed v3 config was redirected only to a temporary directory while all actual
producer, official, forecast, and book inputs remained the deployed JRS raw paths. The
first current-time run produced three expected checkpoint blockers, zero errors, zero
orders, and no legacy files. A second run wrote zero duplicate blockers, bundles, or
intents. This verifies startup schema/artifact/producer handshakes, real input loading,
expected-state routing, journal persistence, and restart dedupe without touching
production output.

Focused Phase 1/2/3 suites passed 46 tests. Shell syntax and Python compilation passed;
the deprecated v2 starter exits with code 2 unless
`ALLOW_DEPRECATED_CITY_PROBABILITY_V2=1` is explicitly set.

## Impact and rollback

This migration changes storage authority only. Models, probabilities, edge thresholds,
fee treatment, and city policies are unchanged. All historical intents are zero-size;
order, fill, submitted notional, fee, and PnL deltas are all zero.

Rollback preserves the v2 raw directory unchanged. An explicit rollback can start the
old launcher with the acknowledgement environment variable, but normal registry routing
and startup point only to runtime v3. Legacy data is retained, not deleted.

## Production blocker

Production activation requires the shared JRS permission context to be repaired first.
That maintenance affects the canonical tmux server that also hosts active live processes,
so it is outside a zero-notional code migration and requires an explicit maintenance
authorization. After that authorization the exact sequence is: restore the canonical
tmux server, rerun strict manifest, migrate the v2 snapshot into the empty v3 directory,
start the committed v3 instance, and verify PID/SHA, first raw rows, second-cycle dedupe,
API visibility, and zero orders. No third forward city-day is required by the revised
direct-migration gate.
