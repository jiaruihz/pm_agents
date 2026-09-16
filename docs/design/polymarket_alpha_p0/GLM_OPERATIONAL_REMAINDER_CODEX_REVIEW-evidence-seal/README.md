# GLM Operational Remainder — Codex Review Seal

Disposition: `ACCEPTED_AFTER_FIXES_OFFLINE_ONLY`

Codex independently reviewed GLM-OP-01 through GLM-OP-04, reproduced their
focused suite, fixed three integration/security findings, and reran the complete
Alpha suite. This accepts the implementation as an offline operational bridge;
it does not authorize a read-only network pilot or any production mutation.

## Findings closed

1. `MAJOR` — the CLI checked lexical paths but could follow a symlink outside
   the fixed pilot root. Inputs now use canonical root confinement, reject
   symlink path components, and use dirfd plus `O_NOFOLLOW` reads. The Alpha DB
   must be a direct child of the fixed pilot root.
2. `MAJOR` — a multi-market Gamma response could write catalog rows before the
   single-market coordinator rejected it. Caller cardinality is now enforced
   by `ingest_captured_gamma_response(..., max_flattened_markets=1)` before the
   first repository mutation.
3. `MINOR` — a future `requested_at` could evade the per-minute demand budget.
   New incoming future clocks fail before write. Existing later-clock rows are
   counted conservatively, while legitimate concurrent writers with different
   caller clocks remain serializable.

## Verification

```text
operational_focused=48 passed
full_alpha=559 passed
operational_security_audit=PASS (0 violations)
cli_security_audit=PASS (0 violations)
git_diff_check=PASS
```

Adversarial coverage includes symlink escape, encoded/canonical path handling,
zero-write multi-market rejection, future-clock rate evasion, and nondeterministic
lock acquisition across three concurrent appends.

## Boundary

- No network request, production DB, weather owner mutation, deploy, restart,
  order, signing, key, or authenticated CLOB capability was used.
- `READ_ONLY_OPERATIONAL_PILOT` remains not approved.
- GLM's historical result document remains intact; this seal is the coordinator
  review and fix closure.
