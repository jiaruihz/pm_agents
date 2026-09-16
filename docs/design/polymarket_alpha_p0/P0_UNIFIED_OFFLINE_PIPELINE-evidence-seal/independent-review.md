# Independent read-only review and fix record

Reviewer scope was limited to the unified offline pipeline, handoff and focused
tests. The reviewer was instructed not to edit files, commit Git or spawn other
agents, and to check correctness, binding, idempotency, append-only semantics,
filesystem/database partial failure and offline safety.

## Initial review result

```text
review_status=CHANGES_REQUESTED
review_focused_tests=23 passed
review_alpha_tests=427 passed
model_effort_usage_telemetry=unavailable
```

## Findings and coordinator fixes

1. **Prior hits were absent from a second scan's persistence payload.**
   `MultiRecallScanOutcome.accepted_hits` now joins prior and current canonical
   payloads, rejects same-id byte conflicts and proves every aggregated hit has
   available bytes. A two-scan repository test was added.
2. **Malformed provider output could escape the provider isolation boundary.**
   Output extraction, type/schema metadata, registry validation, counting and
   hashing are now inside one exception boundary with
   `PROVIDER_OUTPUT_INVALID`. `object()` and `hits=None` attacks are tested.
3. **The caller's result locator could be replaced after acceptance.**
   Every submission is copied to a content-addressed immutable artifact, and an
   accepted packet hash is locked to one exact byte sequence. Locator mutation
   cannot create a second accepted result.
4. **Path checking had an intermediate-directory TOCTOU gap.**
   Reads, writes and directory creation now resolve each component through an
   owned dirfd with `O_DIRECTORY|O_NOFOLLOW`; the final regular file is opened
   with `O_NOFOLLOW`.

The coordinator also made state-transition IDs unique per Candidate edge and
kept Rule B plus decision/prediction ledger writes in one repository
transaction. Exact stage retries are covered end to end.

## Post-fix verification

```text
focused_unified_tests=27 passed
alpha_regression_tests=430 passed
source_capability_audit=PASS (0 violations)
```

No second reviewer cascade was used. The primary coordinator applied the
findings and reran all relevant tests as required by the repository contract.
