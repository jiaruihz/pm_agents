# P0-03 Self-Review Record (pre-delivery subagent review round)

Convention: `AGENTS.md` / `CLAUDE.md` §7 — every complete coding task gets
one independent read-only subagent review before delivery; the main agent
fixes per findings, re-runs all tests, and files this record into the
evidence seal.

- Reviewer: independent general-purpose subagent (read-only; 13 findings,
  R-01..R-13, verdict "not ready for merge gate")
- Review scope: the nine P0-03 owned source/test files against the frozen
  contracts, repository semantics and acceptance invariants (a)–(h)
- Key discovery: the reviewer surfaced the coordinator's committed
  `P0_03_CODEX_INDEPENDENT_REVIEW.md` (REWORK_REQUIRED, BF-P003-01..07),
  which post-dated the first delivery; this rework addresses that
  authoritative list, with the reviewer's findings as a superset.

## Finding resolution map

| Reviewer finding | Maps to Codex BF | Resolution in this rework | Locking test |
|---|---|---|---|
| R-01 artifact ids exclude run/ingest fields; conflict kills batch mid-write | BF-P003-04 | record ids now include run_id + ingest clock; `logical_key` (payload/page sha) is the explicit run-independent dedup key; page/raw saves wrapped in receipt boundary | `test_reingest_under_new_run_never_conflicts` |
| R-02 verified path enterable without stated identity | BF-P003-03 | ≥1 non-blank expectation required; blank/whitespace rejected; mismatches return error receipts instead of crashing | `test_verify_requires_at_least_one_stated_identity`, `test_ingest_verified_market_fails_closed_on_every_bad_path` |
| R-03 condition reader optional → cross-batch fail-open | BF-P003-01 | `market_identity_reader` is a required constructor arg; P0-02 delta D-1 requests DB-level unique index + lookup API | `test_constructor_refuses_missing_identity_reader`, duplicate-condition tests |
| R-04 `max_pages=None` unbounded; offsets reconstructed | BF-P003-06 | finite `DEFAULT_MAX_PAGES` fallback; negative offset rejected; `PaginationReceipt` carries requested offset/limit; `CapturedPage` carries the authoritative window | `test_endless_unique_pages_terminate_at_finite_default_budget`, `test_negative_start_offset_rejected`, `test_captured_page_rejects_malformed_window` |
| R-05 multi-event market loses joins; identity order-dependent | BF-P003-02 | full sorted deduped event set on `extensions.gamma_event_ids`; deterministic primary = min id; `events` array order canonically stabilized in raw sealing so no hash depends on it; P0-02 delta D-2 requests full join projection | `test_multi_event_market_is_order_independent` |
| R-06 aliases returned but never persisted; open intervals | BF-P003-05 | unchanged adapter value objects + explicit P0-02 delta D-4 (interval closing + persistence + recomputed hashes); documented as not-persisted | alias value tests; `p0-02-delta-request.md` |
| R-07 result-level duplicate snapshots/aliases | — | result lists dedup by record id / alias equality | `test_alias_values_replay_identically_and_dedup_in_batch` |
| R-08 contradictory lifecycle silently resolved | hardening #1 | `LIFECYCLE_CONTRADICTORY` fail-closed error code | `test_contradictory_lifecycle_flags_fail_closed` |
| R-09 non-str/int identifiers stringify garbage | — | `_identifier_text` accepts only str/int (bool excluded); dict/list tokens rejected | token-mapping parametrize incl. `[{"a":1},{"b":2}]` |
| R-10 naive endDate assumed UTC; non-mapping payload crashes batch | — | naive endDate → `END_DATE_INVALID`; non-mapping page item → `PAYLOAD_NOT_MAPPING` receipt, batch continues | `test_invalid_numeric_and_end_date_fail_closed`, `test_non_mapping_payload_item_gets_receipt_and_batch_survives` |
| R-11 `pytest.raises(Exception)` too loose | — | asserts `GammaTokenMappingError` | token-mapping tests |
| R-12 five missing adversarial fixtures | — | all added (see tests above) | — |
| R-13 perf/dead-code polish | hardening | fingerprint from already-decimalized items; `set` for fingerprints; result dedup via membership; dead branch removed | existing suite |
| (review hardening #2) builder-only sha validation | — | model-level validators recompute `page_sha256`/`payload_sha256` from stored content | `test_artifact_models_reject_mismatched_content_hash` |
| (review hardening #3/#4) "verbatim" overclaim; generator determinism | BF-P003-07 | wording corrected to "canonicalized parsed JSON"; generator fully clock-pinned and documented | seal regeneration |

## Test evidence after rework

```text
tests/polymarket_alpha/test_gamma_catalog_p0_03.py -> 50 passed
tests/polymarket_alpha/ (incl. P0-07 rules + P0-11 security subset) -> 159 passed
legacy gamma regression (3 files) -> 8 passed
```
