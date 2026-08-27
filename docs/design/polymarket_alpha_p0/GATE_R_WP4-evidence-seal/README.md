# Gate R WP4 Evidence Seal

Disposition: `COMPLETE`

Scope: offline exact-file approval/export, immutable manual return and source
capture, quarantine/insufficiency routing, and the existing ResearchDraftCompiler
plus importer bridge. No GPT Pro/provider call, browser, network, scheduler,
database migration, order, signing or private-key capability was added or used.

## Delivered behavior

- Export approval replays the exact WP3 prompt seal; expiry, reject, mutation and
  cross-work-order use fail closed. Structured patch lineage binds the complete
  parent approval and requires a newly sealed child work order and prompt.
- Every return binds a validated canonical `ResearchAttempt` id/hash, job,
  approval, exact prompt, and lease clocks. Conflicting bytes for the same
  logical attempt become repository conflicts rather than new logical results.
- FULL/EXCERPT sources retain replayable durable metadata and require separately
  loaded bytes to pass local length/hash verification. REFERENCE cannot claim
  content or satisfy critical evidence.
- Raw transcript, response and JSON appendix are sealed before parsing. Invalid
  UTF-8, malformed/absent JSON and Blind leakage quarantine; honest critical
  evidence gaps remain non-advancing `INSUFFICIENT_EVIDENCE`.
- Batch persistence preflights every immutable locator and writes a completion
  marker last. Durable metadata without the original bytes cannot be persisted.
- The accepted fixture runs through the existing draft compiler and sole
  importer; WP4 creates neither a second importer nor a repository owner.

## Verification

- Focused WP1-WP4 plus contracts/draft/importer regression: `116 passed`.
- Full Alpha regression: `711 passed`, `3 failed`.
- The same three managed-environment canaries fail because nested
  `/usr/bin/sandbox-exec` returns `sandbox_apply: Operation not permitted`.
  This is the pre-existing environment-only failure set, unrelated to WP4.
- Python compilation, design-manifest verification, `git diff --check`, and the
  scoped capability-import scan pass.

The fresh independent review initially returned `REQUEST_CHANGES`. All five
findings were closed and coordinator hardening added explicit non-UTF8 and
durable-metadata persistence tests. Worker and reviewer usage telemetry were
unavailable from the runtime and are recorded as such.
