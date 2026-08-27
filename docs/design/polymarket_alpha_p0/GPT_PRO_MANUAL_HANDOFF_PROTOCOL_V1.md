# GPT Pro Manual Handoff Protocol V1

## Preconditions and prompt seal

Rule A, Candidate snapshot, Blind packet, QuestionSet and SourcePlan must all be
current and accepted. The work-order owner compiles canonical UTF-8 plain-text
prompt bytes with fixed newline mode, schema, cutoff and provider policy.
Prompt bytes, preview, byte length, hashes, expiry, identities and seal manifest
are immutable. Preview is a deterministic rendering, not an editable source.

Recursive pre-export checks cover prompt text, nested JSON, source policy,
questions, attachments and origin metadata. Any venue, market id/slug/URL,
Polymarket/Gamma/CLOB reference, price/book/odds, wallet/direction, RecallHit
reason or preceding model output fails closed.

## Human approval and copy

The operator may APPROVE, REJECT, REQUEST_RECOMPILE, or submit a schema-bound
patch. A patch changes inputs and therefore requires recompile, reseal and a new
approval. ExportApprovalReceipt binds work order, prompt hash, approver, UTC
approval/expiry, check codes, copy policy and optional patch id.

The operator copies the exact sealed file into a fresh isolated GPT Pro session
with no extra attachment or context. The honest audit claim is
COPY_ATTESTED_NOT_CRYPTOGRAPHICALLY_OBSERVED unless a provider transcript allows
prompt back-comparison.

## Blind research restrictions

GPT Pro receives only the neutral proposition, frozen RuleContract,
QuestionSet, SourcePlan, PIT cutoff and output schema. It must not use any
prediction-market page, odds, price, book, wallet, candidate direction, recall
reason, or GLM output. It returns human-readable research plus one mandatory
machine-readable JSON appendix with claim/source/probability structure.

## Return and source capture

Before editing, preserve complete transcript bytes, assistant response bytes,
JSON appendix bytes and attempt clocks. An authorized human or external
sidecar—not the Alpha domain process—captures referenced sources.

Every source records canonical URL, publisher, source class, publication/update/
effective/access clocks, PIT availability, redirect/version identity, claim
bindings, representation, byte length, locally recomputed hash and one scope:

- FULL_DOCUMENT proves only the complete saved representation;
- EXCERPT_ONLY proves only saved excerpt and context;
- REFERENCE_ONLY proves only that a reference was cited and cannot alone
  support a critical claim.

ResearchReturnCaptureSeal binds prompt, approval, attempt, raw transcript,
response, JSON appendix, SourceCaptureManifest, operator and clocks. Provider
supplied Alpha ids or hashes are never trusted.

The attempt binding is the canonical `ResearchAttempt` id and content hash, not
an operator-provided label. Durable records retain hashes, lengths and artifact
locators without embedding source/return bytes; every read into an importer or
persist operation must reload and rehash the immutable bytes. Batch persistence
preflights every locator and writes a completion marker last. A partial batch
without that marker is never treated as a complete capture.

## Import, retry and quarantine

The existing ResearchDraftCompiler and importer recompute identities and check
schema, hashes, leakage, PIT, source coverage, probability bounds and all
upstream bindings. COMPLETE may advance only with critical captured evidence.
Valid but weak evidence becomes non-advancing INSUFFICIENT_EVIDENCE. Tamper,
leakage, schema, identity, or hash failures quarantine.

Provider/UI failure uses the same prompt hash and a new attempt id. One bounded
FORMAT_REPAIR may request only the same JSON structure. Any substantive prompt,
rule, plan or cutoff change creates a new job revision, not a retry.

Implementation status: WP4 `COMPLETE`; evidence is sealed in
`GATE_R_WP4-evidence-seal/`. This status does not authorize a provider call.
