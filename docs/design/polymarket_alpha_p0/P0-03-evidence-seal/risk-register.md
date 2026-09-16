# P0-03 Risk Mapping

| Risk | Status in this delivery | Evidence |
|---|---|---|
| R-003 canonical identity mismatch / YES-NO reversal | Gated | label-mapped tokens (`map_yes_no_tokens`), permutation + invalid-label tests, repository identity-conflict fail closed (`IDENTITY_CONFLICT` test) |
| R-004 Gamma schema/pagination/rate drift | Gated offline; rate part deferred to operational pilot | drift receipts + raw retention (`test_unknown_fields_produce_drift_receipt_and_stay_in_raw`), duplicate/empty/short/max-page termination tests |
| R-005 current-row overwrite loses PIT/rule history | Gated | append-only revisions; one-char change keeps both revisions; replay adds zero rows |
| R-023 stable-ID algorithm forks across adapters | Gated | all ids derive from the frozen `stable_record_id`/`content_sha256` factory; golden cross-DB stability test |
| R-025 source clock vs ingest clock confusion | Gated | dual-clock fields, `ingested_at >= source_observed_at` enforced at contract and adapter level; Gamma `updatedAt` never fakes an observation clock |
| R-027 dirty worktree baseline | Mitigated | base commit pinned (`40d98649`); only untracked owned paths added; no tracked file modified by this task |
| R-001 second collector | Not applicable by construction | no HTTP/network import in owned tree (Wave-0 audit in `test-results/import_graph.json`); page fetching is an injected callable |
