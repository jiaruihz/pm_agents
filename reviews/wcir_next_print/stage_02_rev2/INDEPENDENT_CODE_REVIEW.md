# WCIR Stage 2/3 rev2 independent code review

## Review boundary

Read-only review of the new Stage 2/3 rev2 runner, deterministic replay verifier,
compact package builder, and focused tests. The reviewer was instructed to check
correctness, PIT/future-leakage boundaries, identity and append-only semantics,
failure closure, determinism, and test gaps. The reviewer did not modify code.

## Findings and disposition

1. **High — within-file receive-clock regressions were counted but still
   applied. Fixed.** A regressed frame is now tagged by the hashed reader,
   excluded from reconstruction, recorded as `CLOCK_UNCERTAINTY`, and makes its
   day non-normal.
2. **High — REST/WS parity selection key omitted checkpoint. Fixed.** The key is
   now `(event_id, checkpoint, token_id)`.
3. **Medium — duplicate event serialization had only an aggregate count.
   Fixed.** The runner writes a row-level immutable-field audit and fails closed
   on any immutable payload drift. It also points to the separate frozen Stage 0
   candidate immutability audit.
4. **Medium — replay verification did not cover all frozen identities. Fixed.**
   It now verifies frozen events, frozen universe, complete WS identity records
   including clocks and regressions, subscription epoch files, day summaries,
   replay counters, blocker counts, and coverage identities.
5. **Low — evidence-manifest helper was not invoked. Fixed.** The runner writes
   both manifests and the package builder refreshes them before building its
   strict whitelist archive.

Coordinator final inspection also found that a non-empty one-row baseline
intersection was labelled `available` even though the Stage 2 coverage gate was
closed. The label now fails closed as
`blocked_stage2_coverage_gate_and_insufficient_exact_intersection`; the frozen
baseline rows themselves are unchanged.

## Verification after fixes

```text
python -m py_compile <three rev2 scripts>
python -m pytest -q tests/research_tests/test_wcir_stage23_rev2.py \
  tests/pmm_tests/test_executable_book_truth.py \
  tests/pmm_tests/test_weather_ws_incremental_book.py

37 passed in 0.18s
```

`ruff` and `mypy` are not installed in this environment, so no lint/type-check
result is claimed. The reviewer-provided model/usage telemetry was unavailable;
the review was nevertheless completed in one read-only turn as required.
