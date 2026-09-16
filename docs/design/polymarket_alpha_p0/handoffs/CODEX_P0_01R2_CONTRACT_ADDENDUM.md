# Codex Work Order — P0-01R2 Contract Addendum

Objective: release only the missing cross-workstream contracts that block
P0-04, P0-05 and P0-08. Do not implement those workstreams in this task.

Owned files:

```text
src/polymarket_alpha/contracts/**
tests/polymarket_alpha/test_contracts_p0_01r2.py
tests/polymarket_alpha/fixtures/p0_01r2_golden.json
docs/design/polymarket_alpha_p0/P0_01R2-evidence-seal/**
```

Required releases:

- MarketChangeEvent and typed change taxonomy;
- BookCaptureDemand/BookCaptureReceipt with SENSING/FORMAL_REVIEW purposes;
- SourceArtifact, ResearchResultEnvelope and ResearchImportReceipt.

Acceptance: additive compatibility with every existing serialized type;
deterministic IDs/hashes; complete source/packet/run lineage; exact stage and
purpose validation; content hash recomputation semantics; quarantine/reject
receipts; Blind cannot carry market/wallet/price fields through nested content.

Run contract tests, full Alpha suite, schema fingerprint/golden diff and P0-11
source audit. Seal exact source commit before releasing the types to workers.
