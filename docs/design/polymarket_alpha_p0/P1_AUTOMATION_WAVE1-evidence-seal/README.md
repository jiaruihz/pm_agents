# P1 Controlled Automation Wave 1 — Evidence Seal

Disposition: `COMPLETE_OFFLINE`

Wave 1 releases the public immutable artifact owner and the additive research
job/attempt/work-order/return/transition contracts and storage projections.
It does not execute an external provider, open the network, start a scheduler,
touch a production database, or add order capability.

## Delivered

- `src/polymarket_alpha/artifacts/` is the sole confined immutable filesystem
  owner. Research keeps compatibility wrappers; operational code no longer
  imports private `research.handoff` filesystem symbols.
- `ResearchJob`, `ResearchAttempt`, `ResearchWorkOrder`,
  `ResearchReturnReceipt`, and `ResearchJobTransition` are immutable contracts
  under a separate P1 automation schema fingerprint.
- migration `alpha_p1_0002_research_automation` adds only `alpha_*` tables.
- repository enforcement covers packet/rule hashes, sequential bounded
  attempts, one active lease, work-order binding, late-return quarantine,
  terminal state, invalidation, atomic rollback, and transition-derived state.

## Verification

```text
focused_wave1=16 passed
full_alpha=603 passed in 12.96s
capability_audit=0 violations across artifacts/research/operational/contracts/storage
git_diff_check=PASS
network_use=NONE
production_access=NONE
order_capability=NONE
```

The independent read-only review reported two MAJOR findings. Both are closed:
exact transition replay rebuilds a drifted current-state projection from the
append-only ledger, and a late old-attempt payload may be preserved as a
QUARANTINED receipt without advancing the current job.

Rollback is a reader/code pin. No existing migration, sealed contract, evidence
artifact, or current database is deleted or rewritten.
