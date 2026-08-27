# P0-06C Controversy Recall Evidence Seal

status: `COMPLETE_WITH_LIMITATIONS`

readiness_scope: `OFFLINE_FIXTURE_ONLY`

completion_target: `READY_FOR_CODEX_REVIEW`

review_pass: see `SELF_REVIEW.md` — post-handback self-review (findings,
fixes, external-change incident, final re-run).  This MANIFEST reflects the
post-review state; the first revision's cross-run record_id claim and
goldens are superseded as documented there.

## Acceptance result

- 17 focused controversy-recall tests passed (15 original + 2 added by the
  review pass); see `junit.xml`.
- Complete `tests/polymarket_alpha` suite passed — 371 tests in the final
  post-review verification run (the suite grew from 221 during the task as
  parallel workers landed P0-08A and other tests).
- P0-11 source audit over `src/polymarket_alpha` (owned module included):
  `passed=True`, `violations=0`; see `source-audit.txt`.
- The provider is a pure function: caller passes source identity, as-of
  cutoff, corpus revision, frozen source payloads and market mapping; it
  performs no DB open, no network, no clock and no filesystem access.
- Every accepted hit binds absolute source identity (path + device/inode or
  an explicit frozen fixture id, exactly one grounding), schema hash, corpus
  revision hash, source artifact id, PIT effective time, exact quote offsets
  and the excerpt hash, via both `features` and `ProvenanceRef`.
- Typed rejections: `POST_CUTOFF_EVIDENCE` (boundary `effective_at == as_of`
  is inclusive), `SOURCE_IDENTITY_MISMATCH`, `SOURCE_HASH_MISMATCH` (bytes vs
  declared hash), `SOURCE_INCOMPLETE` (out-of-bounds or blank quote span),
  `UNMAPPED_MARKET`, `DUPLICATE_CASE` (identical duplicate retained once),
  `DUPLICATE_CASE_CONFLICT` (conflicting entries all rejected). Blank or
  conflicting market-mapping entries fail closed at request construction.
- Missing source and unexpected payload are typed per-source skips
  (`SOURCE_MISSING` / `SOURCE_UNEXPECTED`) and never block other sources or
  providers.
- Hits are deterministic: `created_at` is the as-of cutoff, outputs are
  canonically sorted, and `request_sha256` is computed over an order-free
  request projection so input reordering yields byte-identical outcomes.
  An exact retry (same `run_id`) reproduces the outcome byte-for-byte; hit
  `record_id` is run-attempt scoped (envelope `run_id` included) so a later
  run cannot collide with different canonical bytes in append-only storage —
  cross-run business dedupe is owned by the released P0-06A
  `recall_dedupe_key`, which excludes the retry envelope.
- Reason codes come from the closed `CONTROVERSY_*` vocabulary
  (RULE_CONTESTED / RESOLUTION_DISPUTED / CLARIFICATION_PENDING /
  AMBIGUITY_RAISED) — contested / clarification-dependent statements only.
  The outcome contract machine-rejects foreign reason codes, non-CONTROVERSY
  recallers, and feature keys outside the source-binding vocabulary (no
  probability, price, fair value, edge or direction estimation). `raw_score`
  is the constant attention weight `1`.

## Requirement-to-test map

| Requirement | Test |
|---|---|
| 1 pure provider API, no DB/network | `test_frozen_corpus_emits_fully_bound_controversy_hits`, `test_descriptor_and_request_wiring`, P0-11 audit |
| 2 full source/PIT binding per hit | `test_frozen_corpus_emits_fully_bound_controversy_hits`, `test_real_file_identity_binds_device_and_inode` |
| 3 post-cutoff rejection + exact PIT boundary | `test_exact_pit_boundary_is_inclusive_and_post_cutoff_rejected` |
| 3 identity mismatch | `test_source_identity_mismatch_rejects_every_case_of_the_source`, `test_real_file_identity_binds_device_and_inode` |
| 3 incomplete/truncated source | `test_incomplete_or_truncated_sources_are_rejected_typed` |
| 3 blank/ambiguous mapping, duplicate case | `test_blank_or_ambiguous_market_mapping_fails_closed`, `test_duplicate_logical_case_is_adjudicated_deterministically`, unmapped case-004 in happy path |
| 4 missing source typed skip | `test_missing_source_is_a_typed_skip_not_a_global_failure`, isolation half of `test_provider_isolation_in_registry_and_aggregation` |
| 5 deterministic CONTROVERSY-only hits, no estimation | `test_frozen_corpus_emits_fully_bound_controversy_hits`, `test_outcome_contract_forbids_estimation_semantics`, `test_input_reordering_produces_identical_outcomes`, `test_exact_retry_is_stable_and_cross_run_attempt_is_distinct` |
| input reordering | `test_input_reordering_produces_identical_outcomes` |
| retry/cross-run identity | `test_exact_retry_is_stable_and_cross_run_attempt_is_distinct` |
| review-pass additions | `test_intra_payload_duplicate_case_ids_are_adjudicated`, `test_skip_and_rejection_contracts_reject_blank_fields` |
| provider isolation | `test_provider_isolation_in_registry_and_aggregation` |

## Golden hit hashes

Canonical run: fixture `corpus_v1.json`, `run_id="controversy-run"`,
`as_of=2026-08-24T00:00:00Z`, corpus revision
`3a21a4a0e7538fd0b34ea04b1d87d8815af57bffe108742d19e146c6964112ee`;
`request_sha256=dd4489d11b07fc088a06e3966bcadec3b139586c499822323649daa05e028196`.
Post-review values (run-attempt-scoped record ids); the first revision's
goldens are superseded — see `SELF_REVIEW.md`.

| case | record_id | canonical_sha256 |
|---|---|---|
| case-001 | `recall_hit:594bf38de90d1f61996fb80856bf23430834677e6fc6e9f47af4d250b3232410` | `e277596fef99e2d8fa76f5381125b37be5a06185881a848f42661ca4b82635b9` |
| case-002 | `recall_hit:7f5bdfa7638f0b441e0382d4c2cf84f72591ee4fd2d4ad4dada2157ab0c0150c` | `a34e7b2eff3f102bf27d8cb753fc7248ce7676c6178e002434d3b120249f17eb` |
| case-003 | `recall_hit:6589da262f731035eaa39f7e493ae37e458870a619fc693e8d4d186af77337c8` | `d8deb515abe6564b9ed7cf8b002b8dcbcfea4c38987065c8e3fe9709f70284ed` |

## Sealed files

| SHA-256 | File |
|---|---|
| `515ad6f938351b0d108cd05605f0ff6bb10393877ec2e7ed03ecbe58c331c740` | `src/polymarket_alpha/recall/controversy.py` |
| `5cdf12bc6607a96ecd26bc758bbd791fe485213472804a5f96a74fd0b0000fc9` | `tests/polymarket_alpha/test_controversy_recall_p0_06c.py` |
| `d882d0cb867b096ad5e1587224f2b0e67129fec70c15db122edfce8295c4c81a` | `tests/polymarket_alpha/fixtures/controversy/corpus_v1.json` |
| `72f624735b9e45327102d21d7553a487350e3091eabccb5c5f7add2fb05d8360` | `tests/polymarket_alpha/fixtures/controversy/corpus_v1_truncated.json` |
| `947a89b5a9fd1e640756d5ea010139ce8f94e50a9e8a09c919e45a0ffd0926c5` | `tests/polymarket_alpha/fixtures/controversy/corpus_offset_overflow.json` |
| `6c1eb08f8fc2e33d56051dc8eb60954e3992f3521be2e6d111bbfd36a9c82e04` | `tests/polymarket_alpha/fixtures/controversy/README.md` |
| `a24ac5ddbcad49d99f9b8651f7a0ebb2e9ff3f32431aa7410ab5d55dd4b42c02` | `SELF_REVIEW.md` |
| `08098c7cd5b952fd430c361bd859f7b2839e749d826644e7506a5813c19f9069` | `commands.log` |
| `da461b09b441916015f092652e758442f499e091c67b20f02eb12631dc226cfb` | `junit.xml` |
| `ee962d15b9071604ce84efc1ab87428da3aa67d47ab2384b4f6ede20e5597947` | `source-audit.txt` |

## Out-of-scope confirmation

No current/canonical dispute DB was opened; no dispute tables copied or
migrated; no `RuleContract` or other released contract changed; no
Candidate/state-machine writes performed (aggregation in tests is read-only
on released APIs); no book, network, order, signing or private-key code; no
files outside the owned paths were created or modified (`recall/__init__.py`
left untouched — provider imported via
`src.polymarket_alpha.recall.controversy`). Nothing committed.

## Limitations

- Fixture identities are frozen virtual paths (`/frozen/...`) with explicit
  fixture ids; real path/device/inode grounding is exercised against
  `tmp_path` files, so cross-device inode semantics are untested.
- The provider trusts the caller to pass frozen bytes; it verifies content
  hashes but cannot re-stat a path by design (pure, no I/O).
- Duplicate adjudication keys logical cases by `case_id` across all admitted
  sources within one request; cross-request dedupe belongs to the released
  P0-06A aggregator, exercised here only via registry integration.
- `raw_score` is a constant attention weight (`1`); downstream CORE/MICRO
  priority interpretation is P0-06A policy, not re-decided here.

## Telemetry

Implemented by the GLM coding agent (builtin:bigmodel-coding-plan/GLM-5.3)
within this ZCode session; exact model/effort/token telemetry is not exposed
by the harness, so the seal is `COMPLETE_WITH_LIMITATIONS` on telemetry
visibility. Roll back by deleting the four owned paths; no released module
imports the provider.

Codex independent review telemetry was also unavailable; its prompt, findings,
coordinator fixes and rerun evidence are preserved in
`CODEX_INDEPENDENT_REVIEW.md` and `hashes.sha256`.
