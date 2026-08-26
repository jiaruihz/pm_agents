# P0-03 Evidence Seal — Gamma Catalog Adapter (offline, rework v2)

Task ID: `P0-03-GAMMA_CATALOG`
Stage: `P0_OFFLINE_IMPLEMENTATION_ONLY`
Run id: `p0-03-offline-20260826-rework2`
Rework basis: `P0_03_CODEX_INDEPENDENT_REVIEW.md` (REWORK_REQUIRED → this seal)
Seal file count: **20** (verified by `hashes.sha256` line count below generation)

## Scope statement

Implemented owned scope only (untracked additions on a dirty owner worktree;
base pinned in `manifest.json`):

```text
src/polymarket_alpha/adapters/{__init__,gamma_pages,gamma_identity,gamma_raw,gamma_normalize}.py
src/polymarket_alpha/census/{__init__,catalog}.py
tests/polymarket_alpha/fixtures/gamma_payloads.py
tests/polymarket_alpha/test_gamma_catalog_p0_03.py
docs/design/polymarket_alpha_p0/P0-03-evidence-seal/**   (this seal)
```

Not modified (explicit confirmation): `src/polymarket_alpha/contracts/**`,
`src/polymarket_alpha/storage/**` (incl. `migrations.py`/`repository.py`),
`src/polymarket_alpha/recall/**`, `src/polymarket_alpha/rules/**`,
`src/polymarket_alpha/security/**`, `src/platform/clients/gamma.py`,
`src/models/market.py`, any production/weather/capture configuration, any
order/signing/private-key code. No network, no runtime DB; only throwaway
SQLite copies under this seal and pytest tmp dirs are touched.

## What this seal contains

| Path | Content |
|---|---|
| `commands.log` | Every command executed to produce this seal, verbatim |
| `test-results/test_results_p0_03.xml` | JUnit for the reworked P0-03 module (50 tests) |
| `test-results/test_results_full.xml` | JUnit for the whole `tests/polymarket_alpha/` suite incl. P0-07 rules and the P0-11 offline/security subset (159 tests) |
| `test-results/import_graph.json` | P0-11 Wave-0 capability audit over the two owned source dirs (zero violations) plus per-file SHA256 |
| `sample-artifacts/catalog_ingest_result.json` | Golden run: counts, record ids, canonical hashes, alias values |
| `sample-artifacts/replay_idempotency.json` | Same-batch replay: table counts before/after identical, revision digest unchanged |
| `sample-artifacts/cross_run_retry.json` | BF-P003-04: new run_id + later clock → zero conflicts, run-scoped ids, shared logical payload sha |
| `sample-artifacts/multi_event_order_independence.json` | BF-P003-02: reordered event arrays → identical snapshot/raw ids and canonical hash, full event set preserved |
| `sample-artifacts/rule_change_revisions.json` | One-char rule change → two revisions with distinct rule_hash; whitespace-only → distinct revision, identical rule_hash |
| `sample-artifacts/db_manifest.json` | Table counts, DB sha, revision-row digest of the scratch catalog DB |
| `sample-artifacts/scratch/catalog.db` | The scratch SQLite copy used by the golden runs |
| `p0-02-delta-request.md` | Formal P0-02 API/schema requests D-1..D-5 (condition uniqueness, full event joins, raw projection, alias intervals, run links) |
| `self-review-record.md` | Pre-delivery subagent review round: findings R-01..R-13 → fixes → locking tests (AGENTS.md §7 convention) |
| `superseded-source-decision-request.md` | F-04 record, closed as `DEFERRED_UNREACHABLE` by the coordinator |
| `known-limitations.md` | Known limitations after rework |
| `risk-register.md` | Risk mapping |
| `manifest.json` | Machine-readable seal manifest (written before `hashes.sha256`, which covers it) |
| `hashes.sha256` | SHA-256 over every evidence file in this seal (itself excluded) |

## Ordering note (BF-P003-07)

`manifest.json` is written **before** `hashes.sha256`, and `hashes.sha256`
covers `manifest.json`. The generator is fully clock-pinned (no wall-clock
inputs); `manifest.json.generated_at_utc` is the only intentionally
time-varying field and is inside the hashed manifest itself.

## Disposition requested

`P0-03` offline implementation complete after rework; blocking findings
BF-P003-01..07 addressed as mapped in `manifest.json` (`bf_status`), with
the four repository-side capabilities formally requested from P0-02 in
`p0-02-delta-request.md`. Coordinator acceptance gate: review §8.
