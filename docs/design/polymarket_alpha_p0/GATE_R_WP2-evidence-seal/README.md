# Gate R WP2 Evidence Seal

Disposition: `COMPLETE`

Scope: offline-only independent GLM-5.3 work-order/import boundary, typed
semantic/rule proposals, exact-source proposal binder, bounded human rule
review, shared contracts, sidecar schema helper and focused tests. No provider
call, network access, browser, order, signing or private-key capability was
added or exercised.

## Delivered behavior

- A GLM-5.3 work order is bound to one Candidate snapshot, safe projection,
  deterministic route, canonical rule artifact, exact prompt and hard token,
  cost and duration budgets.
- GLM-4.7 output, market origins, prices and trading semantics are recursively
  excluded from the independent prompt and imported payload.
- Accepted, quarantined and budget-exceeded attempts produce typed append-only
  receipts with exact wrapper/return hashes.
- A second different provider return for the same sealed logical attempt keeps
  the same record id and is rejected by repository content-conflict semantics.
- Rule proposals cannot cross Candidate snapshots or source artifacts. Quotes
  must exist exactly once; offsets and source hashes are recomputed.
- Human PATCH actions require the same source proof, replay from the frozen
  before draft, and verify the after draft/hash/id and receipt identity.

## Verification

- Focused/shared-contract regression: `95 passed`.
- Full Alpha regression: `682 passed`, `3 failed`.
- The three failures are the unchanged managed-environment limitation: nested
  `/usr/bin/sandbox-exec` returns `sandbox_apply: Operation not permitted` in
  the Codex sandbox. They are unrelated to WP2 source and match the pre-WP2
  baseline failure set.
- Python compilation, sidecar schema generation, design-manifest verification,
  `git diff --check`, and the scoped capability-import scan pass.

Review disposition was initially `REQUEST_CHANGES`; all eight findings listed
in `REVIEW.md` were addressed before this seal. Worker and reviewer usage
telemetry were unavailable from the agent runtime and is recorded honestly.
