# P1 Research Draft Compiler — Evidence Seal

Disposition: `COMPLETE_OFFLINE_COMPILER`

The external research seam no longer requires GPT Pro or a human to manufacture
Alpha record IDs and content hashes. A provider-friendly draft plus actual
source bytes is compiled locally into the released ResearchResultEnvelope and
can be handed directly to the existing importer.

## Delivered

- Frozen, extra-forbid draft DTOs for sources, claims and probability ranges.
- Exact one-to-one actual-byte coverage for captured sources; missing, extra,
  duplicate and model-copy-tampered bindings fail closed.
- Actual RAW_BYTES/NORMALIZED_TEXT/CLAIM_EXCERPT hash and length recomputation;
  deterministic artifact/evidence/estimate/result IDs and canonical sorting.
- FULL_DOCUMENT, EXCERPT_ONLY and REFERENCE_ONLY replay semantics, including
  excerpt-context containment in the supplied bytes.
- Blind recursive semantic/source/origin isolation and null market probability.
- Market FINAL compilation bound to the exact accepted Blind result, Blind
  packet id, packet provenance hash, evidence and blind-candidate baseline.
- Immutable artifact-byte tuple plus a read-only mapping accepted by the
  existing importer. The test runs the real importer and reaches ACCEPTED.

## Verification

```text
focused_research_compiler_and_pipeline=53 passed
full_alpha=585 passed
draft_security_audit=PASS (0 violations)
compileall=PASS
git_diff_check=PASS
```

## Review closure

The independent reviewer found one High lineage gap: Market compilation checked
the accepted Blind result id/hash/evidence but did not explicitly require its
`packet_id` to equal `MarketResearchPacket.blind_packet_id`. The compiler now
enforces the packet identity, and an adversarial cross-packet result/provenance
fixture fails closed.

Coordinator review additionally fixed a model-copy edge where validated actual
source bindings were used for hashing but the original untrusted tuple was used
when constructing the returned artifact-byte bindings.

## Limits

- This compiles already performed research; it does not call a model, browser,
  network endpoint or scheduler.
- It cannot independently verify whether a provider's substantive claim is true;
  it makes the claim/source/probability lineage replayable and importable.
- Operational pilot and production activation remain unauthorized.
