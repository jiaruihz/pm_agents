# P0-02 Repository/Schema Delta Request — from P0-03 rework (BF-P003-01/02/05)

Requested by: P0-03 adapter owner (GLM rework, per `P0_03_CODEX_INDEPENDENT_REVIEW.md` §7)
Owner of implementation: P0-02 repository/migration owner (Codex coordinator)
Status: REQUESTED — the adapter ships fail-closed or value-object behavior until
each capability lands; it must not write migration-owned tables directly.

## D-1 — Condition identity uniqueness + repository lookup (BF-P003-01)

Request:

1. Migration: partial unique index on non-null `condition_id`:
   `CREATE UNIQUE INDEX IF NOT EXISTS alpha_market_condition_uidx ON alpha_market(market_id) WHERE condition_id IS NOT NULL;`
   (targeting `condition_id`; adjust to your migration style — the requirement
   is: two markets can never share a non-null condition_id at the DB level).
2. Repository: public read API, e.g.
   `def market_id_for_condition(self, condition_id: str) -> str | None`.
3. Optional write API that enforces the conflict inside `save_contract`
   (raising `ContractConflictError("condition_id ...")`) so the invariant
   holds even for writers that bypass the reader.

Adapter behavior until then: `GammaCatalogIngestor` **requires** a
`market_identity_reader` callable at construction (TypeError without it) and
checks in-batch + cross-batch condition uniqueness through it; the test
`test_constructor_refuses_missing_identity_reader` locks the fail-closed
constructor.

## D-2 — Full event-market join projection (BF-P003-02)

Request: `AlphaRepository._save_projection` for `MarketSnapshot` should
project **every** event-market relationship from
`snapshot.extensions["gamma_event_ids"]` (list of event ids, sorted,
deduplicated; falls back to `identity.event_id` when absent) into
`alpha_event_market`, not only the singular primary id.

Adapter contract already delivered: the full sorted deduped set travels on
`extensions.gamma_event_ids`; the singular `identity.event_id` is the
deterministic minimum (order-independent); `alpha_event(alpha_event_id)` rows
must exist for every id in that list (the current projection only inserts the
primary — extend together with the join). Locked by
`test_multi_event_market_is_order_independent`.

## D-3 — Typed raw-artifact projection (BF-P003-05)

Request: `_save_projection` branch (or public method) populating
`alpha_raw_artifact(artifact_id, content_sha256)` for contract types
`GammaPageArtifact` / `GammaMarketPayloadArtifact`, where `content_sha256` is
**recomputed from the stored canonical JSON**, not trusted from the caller's
field. Suggested: reuse `contract.canonical_sha256` of the stored payload
subset or add the builder's `page_sha256`/`payload_sha256` to the projection
after recomputation.

Adapter behavior until then: raw artifacts are persisted as
`alpha_contract_record` rows (types `GammaPageArtifact` /
`GammaMarketPayloadArtifact`) with model-level validators proving
`page_sha256`/`payload_sha256` match the stored canonical content
(`test_artifact_models_reject_mismatched_content_hash`); snapshot lineage
resolves through `MarketSnapshot.provenance`.

## D-4 — Alias interval persistence (BF-P003-05)

Request: repository method, e.g.
`save_market_alias(alias: MarketAlias, *, close_previous: bool = True)`,
that (a) inserts `alpha_market_alias` rows, (b) on a changed `alias_value`
for the same `(market_id, source, alias_type)` closes the previous interval
(`effective_to = new.effective_from`) so active intervals never overlap,
(c) replays identical aliases idempotently.

Adapter contract already delivered: `build_slug_alias` emits the current
interval value object with `effective_from = page observation clock`;
`MarketAlias` is not a `CommonEnvelope`, so the adapter cannot and does not
persist it (locked by `test_same_slug_different_markets_stay_separate` and
`test_alias_values_replay_identically_and_dedup_in_batch` at value level).

## D-5 — Run-artifact association (BF-P003-04 follow-up)

Request: expose `alpha_run_artifact_link(run_id, artifact_id, relation)` via
a repository method so per-run association can live there. Until then the
adapter keeps run-scoped fields inside artifact content and derives record
ids from **all** content-changing fields (payload content, both clocks,
run_id), with `payload_sha256`/`page_sha256` as explicit run-independent
logical dedup keys (`logical_key` properties). Locked by
`test_reingest_under_new_run_never_conflicts`.
