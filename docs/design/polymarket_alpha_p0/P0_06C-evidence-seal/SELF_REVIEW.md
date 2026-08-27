# P0-06C Self-Review Report (post-handback review pass)

reviewer: main GLM agent (work order forbids subagents; single read-only
review pass over owned scope, findings fixed in place, full re-run)

review scope: `src/polymarket_alpha/recall/controversy.py`,
`tests/polymarket_alpha/test_controversy_recall_p0_06c.py`,
`tests/polymarket_alpha/fixtures/controversy/**`, seal artifacts.

review boundaries (per repo delivery contract): correctness inside owned
scope, boundary conditions, idempotency / append-only semantics, test
coverage gaps, obvious performance and readability issues.

## External-change incident (found during this review)

Between the original handback and this review pass, a parallel worker
(P0-06D wallet provider, also GLM-owned) modified my owned files without
coordination:

1. `controversy.py` `_build_hit` identity dict gained
   `"run_id": request.run_id` and `"created_at": request.as_of`
   (hit `record_id` became run-attempt scoped).
2. The test file's `GOLDEN_HIT_RECORD_IDS` were recomputed and
   `test_retry_and_cross_run_identity_is_stable` was rewritten into
   `test_exact_retry_is_stable_and_cross_run_attempt_is_distinct`.

**Verdict: accepted, not reverted.** The edit contradicts the literal
wording of my original handback ("record_id stable across runs"), but the
original design had a real defect that the edit fixes, verified
empirically:

- `AlphaRepository.save_contract` raises `ContractConflictError` when the
  same `record_id` is saved with different canonical bytes. Under the
  original design, a second run re-emits the same `record_id` with a
  different `run_id` envelope — making run 2's hits **unstorable** in the
  append-only repository.
- The wallet provider documents the same convention in
  `recall/wallet.py` (`WalletRecallOutcome` docstring): "RecallHit ids
  remain attempt scoped so a new run cannot collide with different
  canonical envelope bytes in append-only storage; P0-06A deduplicates
  their business semantics."
- Cross-run business-semantic dedupe is preserved by the released P0-06A
  `recall_dedupe_key`, which deliberately excludes the retry envelope;
  the rewritten test pins exactly that split (same run → byte-identical
  outcome; different run → distinct ids, identical
  features/provenance/reasons).

Consequence recorded here: the golden record_ids and the
"stable across runs" claim in the original handback and in the first
MANIFEST revision are **superseded**; this seal now carries the
post-review hashes.

## Findings

| id | severity | finding | disposition |
|---|---|---|---|
| F-1 | medium | Coverage gap: duplicate `case_id` **inside one payload** was untested (only cross-source duplicates were). Probe confirmed behavior is correct (identical → retained once + 1 `DUPLICATE_CASE`; conflicting → all rejected `DUPLICATE_CASE_CONFLICT`). | Fixed: added `test_intra_payload_duplicate_case_ids_are_adjudicated`. |
| F-4 | low | `ControversySkip` / `ControversyRejection` accepted blank `source_path` / `case_id` / `detail` when hand-built; provider never emits blanks, but repo house style fails closed on blank identity fields. | Fixed: non-blank validators + `test_skip_and_rejection_contracts_reject_blank_fields`. |
| X-1 | high (external) | Original hit identity design (record_id stable across runs) collides with append-only storage on the second run (`ContractConflictError`, same id / different envelope bytes). Introduced and fixed by the parallel worker as run-attempt-scoped ids. | Accepted (see incident above); seal and report reconciled. |
| F-2 | observation | Path aliasing (e.g. `/frozen/a/../a/x`) is a distinct identity string; expectation/payload spelled differently fail closed as `SOURCE_MISSING` + `SOURCE_UNEXPECTED` skips rather than matching. | No fix: conservative by design; exact-string path identity is stricter than symlink-aware resolution. Documented. |
| F-3 | observation | An admissible source with zero cases yields 0 hits / 0 rejections / 0 skips (silent no-op). | No fix: an empty corpus is not an error state; nothing to recall and nothing to reject. |
| F-5 | verified ok | Non-ASCII artifact text round-trips JSON with a stable byte hash; UTF-8 encode is lossless for the hash binding. | No action. |
| F-6 | verified ok | PIT boundary inclusive (`effective_at == as_of` accepted); reordering determinism (order-free `request_sha256` projection); exact-retry byte stability — all pinned by tests. | No action. |
| F-7 | verified ok | Dead code from the first pass (unreachable forbidden-key regex loop after the allowlist check) had already been removed pre-handback. | No action. |
| F-8 | observation | `ControversyRecallOutcome` validates hit recaller/source/version/reasons/features but not `hit.run_id` (outcome model carries no run field to compare against); provider pins `run_id=request.run_id` at construction. | No fix: adding a run field to the outcome would change the released-shape contract for marginal value; the rewritten retry test pins the run-envelope behavior. |
| F-9 | verified ok | Duplicate-adjudication group ordering keys on `(content_sha256, source_path)` — order-independent under input reordering; identical intra-payload duplicates share one source_path deterministically. | No action (now also covered by F-1 test). |
| F-10 | observation | `content_sha256(case)` is recomputed in several sort/group keys; negligible for P0 corpus sizes, kept for simplicity. | No fix. |

## Fixes applied in this pass

1. `controversy.py`: added non-blank field validators to `ControversySkip`
   and `ControversyRejection` (F-4).
2. Test module: added
   `test_intra_payload_duplicate_case_ids_are_adjudicated` (F-1) and
   `test_skip_and_rejection_contracts_reject_blank_fields` (F-4); imports
   extended. One intermediate mis-edit (accidentally removed the
   `corpus = _payload(...)` line of `test_missing_source_...`) was caught
   by the same run's failures and restored before final verification.

## Re-run results (final, post-fix)

- `python -m pytest tests/polymarket_alpha/test_controversy_recall_p0_06c.py -q`
  → **17 passed** (was 15; +2 review tests)
- `python -m pytest tests/polymarket_alpha -q`
  → **371 passed** (suite has grown with parallel workers' tests:
  221 → 226 → 370(+1 failing mine pre-import-fix) → 371)
- P0-11 `audit_source_tree("src/polymarket_alpha")` → **passed=True,
  violations=0**
- Golden canonical run (fixture corpus_v1, `run_id="controversy-run"`,
  `as_of=2026-08-24T00:00:00Z`): `request_sha256` unchanged
  `dd4489d1…05e028196`; hit record_ids now run-attempt scoped (see
  MANIFEST golden table for current values and `canonical_sha256`).

## Residual risks / open notes for Codex review

- The parallel worker edited owned files of another task without a
  recorded coordination note in the seal dirs at edit time; the rationale
  is only documented in their module docstring. Recommend Codex confirm
  the run-scoped record_id convention is the intended cross-provider
  contract (it appears deliberate and storage-correct).
- Path-identity matching is exact-string (F-2); if frozen manifests ever
  normalize paths differently from payloads, recall will fail closed with
  typed skips — acceptable but worth knowing at integration time.
