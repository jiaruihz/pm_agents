# P0-07 RuleContractCompiler and Gate A/B Evidence Seal

phase_status: `COMPLETE_WITH_LIMITATIONS`

technical_gate: `PASS`

integration_status: `AWAITING_P0_03_RULE_REVISION_INTERFACE`

readiness_scope: `OFFLINE_IMPLEMENTATION_ONLY`

## Acceptance result

- Implemented the sole deterministic `RuleContractCompiler` and local compilation/gate trace models without changing the P0-01 shared RuleContract schema.
- Legacy RuleParse is consumed as serialized data; the Alpha package never imports its LLM/Codex CLI backend and performs no external call.
- Rule text hash, PIT contract corpus hash, parser version, and compiler version revise independently. Exact source text, content hash, legal role, adjudication use, and quote offsets are frozen in the compilation receipt.
- Missing/invalid backend output, rule-hash mismatch, missing source evidence, and excluded evidence fail closed. Ambiguity, corpus blockers, review-required precedence, and low clarity cannot silently pass Gate A.
- Gate B verifies Gate A decision, market identity, RuleContract id, rule hash, contract revision, compiler version, and Market Packet rule revision. Any mismatch returns `BLOCK`.
- Gate A/B and RuleContract revisions persist through the Alpha repository with foreign-key checks clean.
- Golden contract/receipt/Gate A/Gate B IDs and canonical hashes are pinned in `p0_07_rule_gate_golden.json`.
- Targeted implementation/storage tests: 16 passed. Full Alpha suite: 109 passed. Adjacent legacy Rule Lawyer tests: 12 passed.
- Full `src/polymarket_alpha` capability audit passed with zero violations.

## Deliberate remaining dependency

GLM P0-03 has not returned its concrete MarketSnapshot rule-revision adapter. P0-07 therefore accepts the frozen P0-01/P0-03 request shape and is technically complete, but its final cross-module evidence seal must be rerun against the accepted P0-03 output before P0-08 starts. No schema guessing or parallel Gamma adapter was added.

## Sealed files

| SHA-256 | File |
|---|---|
| `d6981f996eb747c38d9448dc72d04e8288f89b2b4f09b5326a88ba12196d0ce0` | `src/polymarket_alpha/rules/__init__.py` |
| `9b0e1d6ecd0199efc3689d7195d6d64a25d163ac663a784f4f69ad67a10e6cd0` | `src/polymarket_alpha/rules/models.py` |
| `5799951a1ed2a36527d5369d1aaf4cf67c30f5dddb1ec5d42964cb864386e44d` | `src/polymarket_alpha/rules/compiler.py` |
| `64e14c478f62b91dcb4e5741feb438808d753afa365b46ea5dbbde05904ec873` | `src/polymarket_alpha/rules/gates.py` |
| `bb19e0ce1b850985394e15aec7f5625c3764d7179c8b8bf7dfa522041f74ff7d` | `tests/polymarket_alpha/test_rule_gates_p0_07.py` |
| `db95dcd43550d8f6ad5ef12638074a8b64278b25b991fd6963b7f7527c289582` | `tests/polymarket_alpha/fixtures/p0_07_rule_gate_golden.json` |
| `a4bc12fc4ceba7c79bf5c880ecd5a13a808d81dab2d2617c20b972c651170b02` | `junit.xml` |
| `de7312f68532957cf831534e0acb579f9a7f6e05e595e0fe960abb78b335391f` | `source-audit.txt` |
| `0898273f495fdb2b5f48a21c365df1a655a362f3c47e53dd58a6db62ed45dd10` | `commands.log` |

## Telemetry and rollback

Implementation and review were performed by the root coordinator. Exact model/effort/token telemetry is not exposed, so the task remains `COMPLETE_WITH_LIMITATIONS`. Roll back by disabling Gate A/B callers and pinning the previous Alpha reader; immutable contracts, receipts, and decisions remain readable. No network, current DB write, production config, order, signing, or credential access occurred.
