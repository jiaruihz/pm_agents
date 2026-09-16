# Independent read-only review

Reviewer: fresh `luna_verifier`, medium fixed role, one turn, no edits.

## Findings and closure

1. `MAJOR` — exact replay of an existing transition returned without repairing
   `current_status/current_transition_id`. Fixed by reconstructing the mutable
   pointer from the complete append-only transition chain; added a corrupted
   projection plus exact-replay test.
2. `MAJOR` — the active-lease guard prevented a late payload from being stored
   as a quarantine fact. Fixed so only QUARANTINED old-attempt receipts may be
   stored after lease expiry; they cannot drive the active job state. Added a
   late quarantine/no-state-advance test.
3. `BLOCKER/MINOR/NIT` — none confirmed.

Coordinator additionally closed pre-review gaps for pre-created second
attempts, late successful returns, work-order-before-lease binding, retry clock
ordering, and packet/rule invalidation lineage.

Reviewer telemetry: model role `luna_verifier`; token usage unavailable from
the subagent interface.
