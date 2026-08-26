# P0-06A Recall Aggregator Evidence Seal

status: `COMPLETE_WITH_LIMITATIONS`

dependency_release: `APPROVED`

readiness_scope: `OFFLINE_FIXTURE_ONLY`

## Acceptance result

- 8 provider-neutral aggregation tests passed; see `junit.xml`.
- Zero providers returns an empty valid run; missing/disabled providers are isolated and do not block other routes.
- A pre-book `NEW_CHANGED` hit creates a Candidate with no book fixture while the optional book provider is explicitly skipped.
- Multi-provider merge, retry/restart dedupe, score/priority, candidate/card IDs, and output bytes are deterministic.
- `historical_only` and expired incoming hits are rejected explicitly.
- Late hits before Blind create a new CandidateCard revision. Late hits after Blind freeze require the caller-provided projection input hashes: changed hash returns `RESEARCH_REFRESH_REQUIRED`; missing impact remains blocked and cannot silently advance.
- Logical Candidate identity is stable while CandidateCard revisions are append-only. Repository integration preserved two revisions, one current pointer, two Recall links, and zero FK violations.
- No provider algorithm, book fetch, network, Rule A/B, ranker, order, signer, or production configuration is included.

## Sealed files

| SHA-256 | File |
|---|---|
| `291ea4b1c24295e6dd91a9b7a1263f7d9bbce6b189fa1b1373c0afee3980f94c` | `src/polymarket_alpha/recall/__init__.py` |
| `af4d54f23b61edd3f168546227de2708a1db17b2cae5887629bf7b12aa8a5bd7` | `src/polymarket_alpha/recall/aggregate.py` |
| `cd968bc429c1ba4b3449be2c377052131ca99f2f3d5b63d144526555c8634153` | `src/polymarket_alpha/recall/registry.py` |
| `672af2b3ebfa752603b0e13f6f16b6011ee449c35afdc8c7e4b61d208a9fd542` | `tests/polymarket_alpha/test_recall_aggregator_p0_06a.py` |
| `eba2f5c1c30e80536bb128569e4f4582cf4dd7e1e35583db0a5dac0a54239d28` | `junit.xml` |

## Telemetry and rollback

Implementation/review was performed by the root coordinator; exact model/effort/token telemetry is not exposed, so the task is `COMPLETE_WITH_LIMITATIONS`. Roll back by disabling the aggregator writer; immutable RecallHits and CandidateCard revisions remain available for rebuild.
