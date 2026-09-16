# P0-03 Known Limitations (after rework v2)

1. **P0-02 repository capabilities requested, not yet implemented** (formal
   delta in `p0-02-delta-request.md`):
   - D-1 condition_id DB-level unique index + lookup API — until then the
     adapter enforces uniqueness through a **required**
     `market_identity_reader` constructor hook (fail-closed, no optional
     bypass);
   - D-2 full event-market join projection from `extensions.gamma_event_ids`
     — until then `alpha_event_market` carries only the deterministic
     primary event join; the full set is preserved on every snapshot;
   - D-3 typed `alpha_raw_artifact` projection — until then raw artifacts
     live in `alpha_contract_record` with model-level hash validation and
     snapshot lineage via `provenance`;
   - D-4 alias interval persistence + closing rule — adapter emits value
     objects only;
   - D-5 run-artifact link API.

2. **Pagination adapter is synchronous and caller-injected**; wiring the real
   async Gamma client belongs to the read-only operational pilot. Collection
   is always bounded (`DEFAULT_MAX_PAGES`).

3. **Revision granularity is raw-content scoped**: re-observation under a new
   run/clock creates a new immutable revision; semantic dedup (no-op → no
   logical change event) is P0-04's contract via `rule_hash`/content
   comparison. Non-semantic `events` array order is canonically stabilized at
   raw sealing (documented BF-P003-02 normalization); all other raw bytes are
   preserved as canonicalized parsed JSON, not HTTP response bytes.

4. **`condition_id` is identity-frozen per market once first ingested**; a
   later payload adding/changing it fails closed (`IDENTITY_CONFLICT`).

5. **`SUPERSEDED` unreachable by coordinator decision**
   (`SUPERSEDED_P0_POLICY=DEFERRED_UNREACHABLE`) — see
   `superseded-source-decision-request.md`.

6. **Numeric fields use `Decimal(str(value))` at the boundary**; catalog
   volume/liquidity are descriptive metadata.

7. **Live smoke intentionally not run**; Gamma rate/schema verification
   belongs to the later operational pilot gate.

8. **Seal reproducibility**: the generator is clock-pinned and deterministic,
   but the P0-03 owned sources are untracked files on a dirty owner worktree
   at the recorded HEAD — `manifest.json` records the exact dependency
   commits and `test-results/import_graph.json` records per-file source
   hashes instead of claiming a clean-base reproduction.
