# P0-02 Alpha Storage Evidence Seal

status: `COMPLETE_WITH_LIMITATIONS`

dependency_release: `APPROVED`

readiness_scope: `OFFLINE_FIXTURE_AND_COPY_ONLY`

## Acceptance result

- 5 migration/repository tests passed; see `junit.xml`.
- Migration is additive, transactional, idempotent, and creates 26 `alpha_*` tables plus a versioned manifest, including append-only CandidateCard revisions and a current projection pointer.
- Empty, synthetic legacy, repeat, incompatible/interrupted rollback, concurrent duplicate, canonical identity conflict, stored-hash corruption, FK, integrity, and position-state cases passed.
- A read-only SQLite backup of the current `runtime/db/research.db` was migrated twice in a temporary directory. Legacy schema/count/content hashes were identical before/after; `foreign_key_check=0`, `integrity_check=ok`.
- The source DB stayed byte-identical: SHA-256 `ba2b56663de049d92db42bb3e1b963ce9362363cb187001a0600fc84f44e95c0`, device `16777232`, inode `19066216`, size `69632`, unchanged mtime.
- No current DB migration occurred. The temporary copy was deleted with its temporary directory.

## Sealed files

| SHA-256 | File |
|---|---|
| `e8877a503b23a8538efdda45e260891c73650aaeec59c00aae375bdce50f641f` | `src/polymarket_alpha/storage/__init__.py` |
| `69bf2ea64a845875f9f5d6720806dd304ee034fad799c453583e268f579a61e6` | `src/polymarket_alpha/storage/migrations.py` |
| `0e92d72b8164f7551301a41224769c61f65b52e9126da4aeba6fb46851e96077` | `src/polymarket_alpha/storage/repository.py` |
| `5294f226576a0739a53a3c5fededd22b791d97bd5246207a1931008a0e81639b` | `tests/polymarket_alpha/test_storage_p0_02.py` |
| `8bf81477d91a6904c950ef9f8cf662595d29097a880488ddd034d22fcf35ea25` | `tests/polymarket_alpha/fixtures/storage/legacy_fixture.sql` |
| `35e39cc7002c478471e68d7a622a6a0ce05415c9cca0de4bdc68b15032a52529` | `junit.xml` |
| `547192e1f469a67b0fe23fe67b28bc92feeb3f4c3a4f9f3f81b8a7bcdea85698` | `current-db-copy-rehearsal.txt` |

## Review and telemetry

The bounded worker reported 18 tool calls and approximately 4 seconds of visible command wall time; exact model/effort and token telemetry were unavailable. The root coordinator then independently changed and re-reviewed transaction validation, canonical imports/timestamps, stored-hash verification, market identity conflict handling, token/leg projections, source-DB copy rehearsal, and all tests. Missing token telemetry requires `COMPLETE_WITH_LIMITATIONS`.

## Rollback

Disable the Alpha storage feature/read view and pin the previous contract reader. P0-02 does not drop tables or delete legacy/Alpha data. Current `research.db` deployment remains separately unauthorized.
