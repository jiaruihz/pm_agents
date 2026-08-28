# Verification

Verification date: 2026-08-28

## Evidence integrity

Command:

```text
shasum -a 256 -c hashes.sha256
```

Result: all entries passed. The manifest is regenerated after this verification
record is added.

## Current Alpha regression

Command:

```text
PYTHONPATH=. .venv/bin/pytest -q tests/polymarket_alpha
```

Result:

```text
724 passed
3 failed
```

The three failures are the unchanged managed-environment macOS sandbox canary
set:

- `test_security_final_p0_11.py::test_os_sandbox_canary_is_explicitly_not_run_without_sandbox_exec`
- `test_security_os_sandbox_p0_11.py::test_os_sandbox_denies_raw_network_connect`
- `test_security_os_sandbox_p0_11.py::test_os_sandbox_receives_explicit_secret_free_environment`

All three fail before the canary body because nested `/usr/bin/sandbox-exec`
returns `sandbox_apply: Operation not permitted`. This is the same pre-existing
failure set recorded in Gate R WP1 through WP5 evidence; it is not a new Alpha
logic regression.

## Scope audit

- No order, signing or private-key capability was invoked.
- No production configuration or current runtime database was modified.
- Formal paired book demand was not emitted because no Blind result was
  accepted.
- No GPT Pro result, final edge or PredictionRecord is claimed by this seal.
