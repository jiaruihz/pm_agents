# P0-02 Supplemental Rule/Corpus Revision Storage Evidence Seal

status: `COMPLETE_WITH_LIMITATIONS`

technical_gate: `PASS`

readiness_scope: `OFFLINE_COPY_ONLY`

## Root-cause correction

The original `alpha_rule_contract_revision` projection used `UNIQUE(market_id, rule_hash)`. That constraint rejected a valid new RuleContract when only the PIT dispute corpus, parser version, or compiler version changed. P0-07 cannot safely work around that conflict.

The sole Alpha migration owner now adds `alpha_p0_0002_rule_corpus_revision` with corpus-aware `alpha_rule_contract_revision_v2` and Gate A/B projection tables. The v1 table remains readable; no table or row is dropped. Replaying the migration backfills an existing v1 RuleContract from immutable canonical JSON, including its actual corpus hash rather than a guessed value.

Repository writes now project RuleContract and RuleGateDecision records to the v2 tables. Tests prove:

- same rule hash with two corpus hashes persists as two immutable revisions;
- same rule/corpus with a new compiler version persists as a new compiler revision;
- a simulated pre-v2 rule projection replays into v2 with its corpus hash intact;
- migration is transactional/idempotent and preserves legacy tables;
- all foreign-key and integrity checks pass.

No current `runtime/db/research.db`, weather DB, wallet DB, dispute DB, or production database was opened or migrated. All migration tests used pytest temporary SQLite copies.

## Sealed files

| SHA-256 | File |
|---|---|
| `ca758882d89299f3a89dc7d1ad8e6b1617946238c506cc2a711960aff32ccbce` | `src/polymarket_alpha/storage/__init__.py` |
| `8330e472f0ff3de206ec716b85525061d3f89aa22ab9e60eb72bcc7e1418e5af` | `src/polymarket_alpha/storage/migrations.py` |
| `a3ffc5a71d518da662415101249e44b8defae10361d5f7a82e07590980bd657b` | `src/polymarket_alpha/storage/repository.py` |
| `b3bc34cbce4f131eb8be2b8a31426d358e485d70ed1f0f4c30481f533b159ee7` | `tests/polymarket_alpha/test_storage_p0_02.py` |
| `bb19e0ce1b850985394e15aec7f5625c3764d7179c8b8bf7dfa522041f74ff7d` | `tests/polymarket_alpha/test_rule_gates_p0_07.py` |
| `1e0c013b98c1fa3af37a8ec54d4cb345d8196423f1834086dd1d582fd9de257d` | `junit.xml` |
| `8e77865aae505fe4c083f1d2534628880bdb4f34e70d0b97032b418ade28efad` | `commands.log` |

## Rollback

Readers can pin the v1 projection. Because the repair is additive and no current DB was migrated, rollback requires no destructive DDL. Exact model/effort/token telemetry is unavailable, so the supplemental task is `COMPLETE_WITH_LIMITATIONS`.
