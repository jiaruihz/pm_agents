# GLM Semantic Triage — Evidence Seal

Disposition: `COMPLETE_FOR_CONTROLLED_SEMANTIC_TRIAGE_PILOT`

Scope: external GLM semantic understanding and initial screening only. This is
not probability research, orderbook analysis, operational daily scanning, or
live execution. Every durable decision remains `NO_ORDER`.

## Delivered chain

1. Raw catalog market → allowlist-only `SemanticTriageProjection`.
2. Market identity, slug/URL, price, bid/ask/book, token ids and wallet/order
   direction remain outside the model prompt.
3. External sidecar invokes the locally configured Claude-compatible BigModel
   endpoint with `haiku → glm-4.7`, no tools, no MCP, no session persistence,
   explicit JSON schema, timeout and USD budget.
4. Core Alpha importer treats the return as untrusted, requires every item
   exactly once, rejects extra/forbidden semantics, binds results back to the
   private market-id map and writes immutable artifacts plus generic contracts.
5. Deterministic deadline eligibility overrides model priority: an elapsed
   market is effectively `DEFER` even if GLM says `ADVANCE`. Model-only terminal
   rejection is impossible.

## Final real pilot

- Input markets: `3867798` Amsterdam temperature, `3886788` Kraken IPO,
  `676804` OpenAI consumer hardware.
- Provider model: `glm-4.7` (requested alias `haiku`).
- Duration: 10,804 ms.
- Cost: USD 0.042043.
- Usage: input 1,114; cache-read input 896; output 1,441 tokens.
- Provider disposition: 3 `ADVANCE`.
- Effective disposition: 2 `ADVANCE`, 1 `DEFER`.
- The Amsterdam market was deterministically changed to `DEFER` because its
  deadline had elapsed.
- Provider server tool use: 0 web searches, 0 web fetches; permission denials: 0.
- Storage: 3 `SemanticTriageDecision` + 1 `SemanticTriageReceipt`.

The earlier exploratory model calls showed the same elapsed market once as
`DEFER` and once as `ADVANCE`. That measured inconsistency is why deterministic
deadline eligibility was added before this final sealed run.

## Verification

- Focused + Wave-0: `40 passed`.
- Full Alpha regression: `655 passed`.
- Projection/prompt isolation scan found none of the three market ids, price/
  book fields, Polymarket URLs, Gamma URLs or CLOB strings.
- Secret-pattern scan of sealed artifacts returned no match.
- Independent read-only review completed; both findings were fixed before the
  final regression. See `independent-review.md`.

`pilot-artifacts/` contains the exact projection, prompt, provider wrapper,
validated provider result, private binding, decisions and receipt. Their hashes
are in `hashes.sha256`.
