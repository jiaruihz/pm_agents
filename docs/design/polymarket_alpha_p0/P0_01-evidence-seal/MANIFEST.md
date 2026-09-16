# P0-01 Shared Contracts Evidence Seal

status: `COMPLETE_WITH_LIMITATIONS`

dependency_release: `APPROVED`

readiness_scope: `OFFLINE_IMPLEMENTATION_ONLY`

## Acceptance result

- 26 contract tests passed; see `junit.xml`.
- Contract schema fingerprint: `011015df25b35d844c2bfbe6b0453aca22526f9c19c9a76fe35a970b3e63211c`.
- Strict `alpha_p0_v1.0` compatibility policy rejects malformed, unreleased-minor, and unknown-major versions.
- Canonical JSON rejects floats, naive timestamps, non-finite Decimal values, and Unicode-normalized key collisions.
- Blind export uses `BlindRuleView`, opaque blind identifiers, approved question templates, recursive semantic scanning, and forbids market/wallet/operator evidence.
- Candidate invalidation/refresh/closed/resolved/superseded/archive events are explicit.
- This seal creates no migration, network adapter, current DB write, production configuration, order, signer, or credential path.

## Sealed files

| SHA-256 | File |
|---|---|
| `f474bfd2d0b6724cbe7232a7d809c9c0cf59993d750bc50c6e543031a0a6c2a4` | `src/polymarket_alpha/contracts/__init__.py` |
| `ba37622d011a47dec90c33e560a2e3c3590eab6f4690bc321581ce9e1008eea8` | `src/polymarket_alpha/contracts/base.py` |
| `d75bebf018395b28301707659ab7fda8e84dad2acb0ed4925843f1797ac8dd52` | `src/polymarket_alpha/contracts/compatibility.py` |
| `b19b1bbc9813ee1eb9145e7ae13de26acd4a111541f91c685fcfa97a77daeadd` | `src/polymarket_alpha/contracts/models.py` |
| `df8013df38726a8bab2e6024e4da115330f079db9674733c79cafc570ce6ce3e` | `tests/polymarket_alpha/test_contracts_p0_01.py` |
| `5010b6dcd0e197f82d9df855da3030c120b32c38da9914ab2d9c8e40161a07b0` | `tests/polymarket_alpha/fixtures/p0_01_golden.json` |
| `19bc346a6191f762e14567fb26399671bee15aa79c9d11420e9f81224d83ae23` | `contract-schemas.json` |
| `281f0b01dcd75a0dc3804f6dd24a8f8bc79472487ade7992f90f779433477ce6` | `junit.xml` |

## Telemetry limitation

Implementation and final review were performed by the root coordinator. Exact model/effort and input/output/cached token telemetry are not exposed by this runtime. Tool/test evidence is complete, but the task contract requires that missing telemetry be reported as `COMPLETE_WITH_LIMITATIONS`.

## Rollback

Downstream readers pin `alpha_p0_v1.0`. Roll back by pinning the previous contract package and disabling Alpha entrypoints; no persistent data was migrated by P0-01.
