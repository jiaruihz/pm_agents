# Independent read-only code review

Reviewer: fresh single-turn read-only subagent `wcir_fullseal_canary_readonly_review`.
Model/usage telemetry: unavailable to reviewer.

Review scope: full-seal v2 builder, network canary, collector state machine, strict
packager, relevant tests, and both review roots. The reviewer did not edit files and
did not spawn another reviewer.

## Findings and disposition

1. HIGH — offline reproduction located the wrong root. Fixed with explicit extracted-
   package/repository root discovery; local reproduction and extracted-package
   reproduction are both required tests.
2. HIGH — causal gate did not independently constrain the entry book checkpoint.
   Fixed with `entry_checkpoint_gate`: checkpoint must be comparable, no later than
   decision-ready, and strictly before official first_seen. A post-ready mutation test
   now fails closed.
3. HIGH — queue/gap injections were detached synthetic state tests. Fixed by adding two
   bounded real-network fault phases: actual WS frame → overflowing bounded buffer and
   actual WS baseline → controlled gap → rejected delta.
4. MED — reproduction did not verify the whole package entry set. Fixed by verifying
   `PACKAGE_CONTENTS.json`, exact extracted file set, every entry size/hash, nine
   deterministic outputs, and normalized audit semantics.
5. MED — one-token request fraction could be mistaken for future demand coverage.
   Fixed by public token/condition verification, demand→request latency, and explicit
   `SINGLE_FROZEN_CANARY_TOKEN_TRANSPORT_ONLY` / no source-opportunity labels.

Reviewer command before fixes: relevant pytest, 23 passed with one intentional duplicate-
zip warning; the old reproduction script failed. After the fixes the coordinator reran
the expanded suite and both reproduction paths; results are in
`TEST_COMMANDS_AND_RAW_OUTPUT.txt`.
