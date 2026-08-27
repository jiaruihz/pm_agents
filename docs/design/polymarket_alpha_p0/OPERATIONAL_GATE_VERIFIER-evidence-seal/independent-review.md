# Independent review

The reviewer found six fail-closed gaps:

- P0: raw bytes could be replaced through `model_copy` without verifier-side
  hash/length recomputation;
- P0: nested Literal/capability constraints were not revalidated;
- P1: budget events were counted but not cross-bound to Gamma/books/raw;
- P1: security canary hashes did not bind receipt payloads;
- P1: market packet and formal book were matched only by record id;
- P2: a Gamma event without an explicit `markets` field was accepted.

All findings were fixed. The verifier now revalidates the full evidence model,
recomputes raw/canary hashes, binds every budget event, compares the exact book
snapshot bytes and requires explicit Gamma market lists. New adversarial tests
exercise each bypass.

Reviewer test result: 9 passed. Reviewer model/effort/usage telemetry:
`TELEMETRY_UNAVAILABLE`.

