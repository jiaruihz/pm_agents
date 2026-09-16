# P0-06D Specialist Wallet Recall Evidence Seal

status: `COMPLETE_WITH_LIMITATIONS`

phase: `P0_OFFLINE_IMPLEMENTATION_ONLY`

## Acceptance result

- 41 focused wallet-recall tests passed; see `junit.xml`.
- Full `tests/polymarket_alpha/` suite passed (371 tests in the Codex acceptance rerun); see `full-suite-result.txt`.
- P0-11 AST source audit over `src/polymarket_alpha` (including the new `recall/wallet.py`) passed with zero violations; see `source-audit.txt`.
- Provider is pure and offline: caller supplies frozen facts, source identity, pagination receipt, observation clock and address/entity mapping. No DB open, HTTP, tracker update or copy-trade action anywhere in the module (enforced by the audit plus a dedicated in-test audit of `src/polymarket_alpha/recall`).
- Freshness is enforced at the observation/as-of boundary (`age <= max_source_age` is current; just-before/at/after covered). Stale facts emit `historical_only=True` hits only; the P0-06A aggregator rejects them as `HISTORICAL_ONLY` (integration test).
- The stale-store fixture (receipt window ~59 days old, representing the known repository wallet stores' measured 48–79 day staleness) produces zero current hits, zero historical hits and `WINDOW_COVERAGE_MISSING` rejections for every fact. The repository wallet stores were never opened.
- `Address != Entity`: entity ids may not be addresses (model-level rejection); two addresses mapping to one entity remain two distinct addresses with two explicit attribution records (provenance + confidence); addresses are never merged; unaliased addresses never inherit entities.
- Fail-closed matrix covered by fixtures: source identity mismatch, pagination truncation, page-count mismatch, missing window coverage (start/end), unmapped token, ambiguous token/market, ambiguous alias, below-threshold alias confidence, direction-bearing alias text, fact observed after as-of, fact outside the snapshot receipt window. Each returns explicit machine-readable reasons; unaffected routes stay isolated.
- Direction privacy: `side`, `position_direction`, `notional_usdc`, `size` and the direction-bearing `token_id` appear only in the private input fingerprint. Public features are an allowlist (addresses, count, entity attributions, freshness config) re-scanned recursively by `wallet_public_leak_reasons` before emission. Within the same run, BUY↔SELL flips keep the public fingerprint and record id identical while changing the private fingerprint.
- Output contract: deterministic `RecallHit`s, `recaller=SPECIALIST_WALLET`, reason codes restricted to the closed set `{SPECIALIST_WALLET_ACTIVITY, SPECIALIST_WALLET_ENTITY_ACTIVITY}`, fixed non-probabilistic `raw_score`, no price/fair-value/execution fields, empty extensions.
- Retry/cross-run identity, input-ordering independence and provider isolation (instances, registry cross-checks) are tested; golden public/private fingerprints are pinned in `golden_request_fingerprints.json`.

## Golden fingerprints (canonical fresh snapshot request)

- public_input_sha256: `2555ab9ac2ae201acc883eb9047f85de99332d38e167a01ef7c30146d5064435`
- private_input_sha256: `974f0b26ec77b4e4e4ea6a916356b4795fcec647b4accba698239de1a81eda29`
- hit_record_ids for `run_id=wallet-recall-run-1`: `recall_hit:bddc391b36310ed0774ff6f1b62170ab46cc53a4456bffb79c2f3887719d445b` (market-alpha), `recall_hit:ac0ec6a773010a2320d27027db286897745463089f8d87e818ee5ebb4e6921c7` (market-beta)

## Sealed files

| SHA-256 | File |
|---|---|
| `5bac311ed842bd08d444ced772a8f20cc8fcfb3e0d232d9e9f82812d26651956` | `src/polymarket_alpha/recall/wallet.py` |
| `8ed7e0cdff1506f39d28c95ece609e387009e37f8554669bd794470b2da9550e` | `tests/polymarket_alpha/test_wallet_recall_p0_06d.py` |
| `f0bb7d5b125d2f58d1c4d9a8b2cad98c133a6e2f9a28e642eb2080fee0407270` | `tests/polymarket_alpha/fixtures/wallet_recall/fresh_snapshot_request.json` |
| `989c497e035ab233bfdbfdaf1e0c4b64a69e48c84fc2c5a2681bb97dc19b753a` | `tests/polymarket_alpha/fixtures/wallet_recall/stale_historical_store_snapshot.json` |
| `fa3ad56f2a812ae9197dcd5c655206d3f372e5e241cfc0a5f10bd101cc5f68de` | `tests/polymarket_alpha/fixtures/wallet_recall/fail_closed_cases.json` |
| `3a5b07f9a0bd84077c5d4bf899cb10a829530a79274608490c14728c68c54c38` | `tests/polymarket_alpha/fixtures/wallet_recall/golden_request_fingerprints.json` |
| `085a13827bc4eff35490e8d4732ffebc111a6ebe2512b59a965044ea712930e0` | `junit.xml` |
| `ee962d15b9071604ce84efc1ab87428da3aa67d47ab2384b4f6ede20e5597947` | `source-audit.txt` |
| `7cfe76484647cdf9d10ca9b795441bc67694fe98a62b10e4a96582a6c6206acb` | `full-suite-result.txt` |
| (see `hashes.sha256`) | `SELF_REVIEW.md`, `CODEX_INDEPENDENT_REVIEW.md` |

## Limitations

- The leak scanner is lexical (banned key fragments, fragment-in-value, exact direction tokens). It blocks the encoded side channels covered by tests but is not a semantic NLP guarantee against arbitrarily paraphrased prose; the allowlist-constructed features remain the primary control.
- Record ids intentionally include the run envelope. Same-run exact retry is byte-identical; different runs receive attempt-scoped ids so append-only storage never sees the same id with different envelope bytes. P0-06A `recall_dedupe_key` performs cross-run business-semantic dedupe.
- The provider never verifies `snapshot_sha256` against content (no I/O); it is lineage-only.
- No export was added to `src/polymarket_alpha/recall/__init__.py` (not an owned file); import via `src.polymarket_alpha.recall.wallet`.
- Per the work order's "不得启动subagent" constraint, the AGENTS.md §7 independent review subagent was not run; a systematic self-review against the work-order checklist was performed instead and all findings were fixed before sealing. The full self-review record (fixed findings F1–F3, boundary probes P1–P6, non-blocking observations, coverage-gap assessment and Codex review pointers) is sealed in `SELF_REVIEW.md`.

## Telemetry

GLM (builtin:bigmodel-coding-plan/GLM-5.3) single-threaded implementation; exact model/effort/token telemetry is not exposed to the worker, so this dimension remains `COMPLETE_WITH_LIMITATIONS` in the same sense as the P0-06A/P0-11 seals.
