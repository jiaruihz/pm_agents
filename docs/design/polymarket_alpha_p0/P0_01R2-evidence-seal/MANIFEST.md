# P0-01R2 Shared Contract Addendum — Evidence Seal

Disposition: `ACCEPTED`

Readiness scope: `P0_OFFLINE_IMPLEMENTATION_ONLY`

Source commit: `a9003ff5` (`feat: add polymarket alpha p0 r2 contracts`)

Released additive contracts:

- `MarketChangeEvent` with typed, deterministic change taxonomy and immutable
  before/after snapshot lineage;
- `BookCaptureDemand` / `BookCaptureReceipt` with `SENSING` and
  `FORMAL_REVIEW` purpose gates;
- immutable `SourceArtifact`, `ResearchResultEnvelope` and
  `ResearchImportReceipt` with explicit hash/replay/quarantine semantics.

Contract version remains `alpha_p0_v1.0`: no existing serialized model field
was changed. The additive schema bundle fingerprint is
`25dc4cb3a82430b95cd557e5f632e3c2c48c7aaaa7d663ed8d94885b84edeed3`.
The original P0-01 seal remains historical; this R2 seal supersedes its bundle
fingerprint for all subsequent work.

Validation at the sealed commit:

- focused P0-01 + P0-01R2 contracts: `36 passed`;
- complete tracked/accepted Alpha suite: `176 passed`;
- P0-11 static audit of `src/polymarket_alpha/contracts`: PASS, 0 violations;
- `git diff --check` on owned paths: PASS.

The complete shared worktree also contained an unaccepted, untracked P0-06C
draft from an interrupted worker. Its four failing draft tests were deliberately
excluded from this contract seal; it is owned by the active GLM P0-06C task and
is not part of commit `a9003ff5`.

Out of scope and not authorized by this seal:

- storage migrations or current `research.db` migration;
- network access, live market-book capture, production configuration;
- automated external research/model calls;
- order construction, signing, private keys or live execution.
