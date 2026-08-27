# Polymarket Alpha — Read-only Operational Pilot Gate

```text
GATE_STATUS=PREPARED_NOT_AUTHORIZED
OFFLINE_PREFLIGHT_STATUS=IMPLEMENTED_AND_TESTED
OFFLINE_PREFLIGHT_SOURCE_COMMIT=ef82da8844562c63eacc5d16a73b17fe5b98abbb
ENTRY_EVIDENCE=P0_UNIFIED_OFFLINE_PIPELINE-evidence-seal
ALLOWED_OUTCOME=READ_ONLY_OPERATIONAL_PILOT_READY | REWORK_OPERATIONAL_BOUNDARY
LIVE_ORDER_SIGNING_PRIVATE_KEY=OUT_OF_SCOPE
PRODUCTION_CAPTURE_EXPANSION=SEPARATE_AUTHORIZATION
```

The offline OP-01/OP-04/OP-05 preparation is sealed under
`READ_ONLY_OPERATIONAL_PILOT-PREFLIGHT-evidence-seal`. This does not change
`GATE_STATUS`: OP-02 owner demands, OP-03 production observations, and OP-06
read-only replay have not run.

## Objective

Prove that the completed offline Alpha chain can consume a small, explicitly
budgeted set of real public Gamma/CLOB read-only facts through the existing
single market-book owner without changing weather behavior, creating another
collector, migrating a current database, or making execution capability
reachable.

## Authorization boundary

Preparation, fixture tests, static audit and configuration review may run
offline. The following require explicit owner authorization before execution:

- any real Gamma/CLOB request;
- any demand sent to the current market-book owner;
- any production config/deploy/restart;
- any write outside a temporary pilot artifact root.

The pilot must never receive private keys, authenticated order credentials,
signing material, Authorization/Cookie headers, or an order endpoint.

## Work packages

| ID | Sole owner | Scope | Required evidence |
|---|---|---|---|
| OP-01 | Alpha integration | Freeze 3–5 arbitrary binary markets and canonical event/market/condition/YES/NO token identity | source URLs/receipts, identity hashes, schema-drift receipts |
| OP-02 | Existing book owner | Submit bounded `SENSING`/`FORMAL_REVIEW` demand only through the existing owner | demand/receipt pairs, paired YES/NO coverage, latency/staleness |
| OP-03 | Weather isolation | Compare owner health and weather capture before/during/after pilot | process/release identity, weather demand counts, error/latency deltas |
| OP-04 | Security | Enable only exact public endpoint policy for the pilot process | host/path/method/body/header/DNS receipts; redirect/proxy/auth bypass failures |
| OP-05 | Capacity and storage | Enforce scan and artifact budgets in a temporary pilot root | request rate, bytes/day projection, TTL/dedupe, integrity and cleanup rehearsal |
| OP-06 | Coordinator | Replay one read-only end-to-end candidate and certify the gate | frozen packets/results, no-order ledger, rollback receipt, final seal |

No worker may change shared contracts, migrations, weather production config,
or execution modules. OP-06 depends on direct evidence from OP-01 through
OP-05; workers cannot self-certify the gate.

## Fixed pilot budget

The first authorized run must stay within all limits below:

```text
max_markets_per_scan=5
max_tokens_per_batch=10
max_demands_per_minute=5
max_network_requests_total=30
max_artifact_bytes_total=50_000_000
max_runtime_minutes=30
redirects=disabled
proxy_environment=cleared
current_runtime_db_writes=0
```

Changing a limit requires a new input manifest and owner approval; limits are
not automatically widened after a successful run.

## Acceptance gates

1. At least three unrelated binary markets normalize to stable canonical
   identities; outcome ordering cannot swap YES/NO tokens.
2. Each selected market receives a paired YES/NO receipt at every requested
   target size, or a typed failure without blocking pre-book recall.
3. Candidate generation still works when all book demands are disabled.
4. Blind packets contain no slug, Polymarket URL, side, wallet payload,
   price/book field, or price-derived reason.
5. Market packets bind only a fresh post-Blind formal-review demand and exact
   owner receipt/snapshot hashes.
6. Weather capture health, error rate and p95 latency do not materially regress;
   exact pre/during/post numbers must be sealed rather than summarized.
7. Request count, rate, staleness, throughput and artifact growth remain within
   the fixed budget.
8. Generic HTTP, raw socket/websocket, redirect, proxy, encoded-path,
   unexpected body/header, dynamic import and subprocess canaries remain denied.
9. The resulting ledger is `NO_POSITION` or `SIMULATED` and always
   `execution=NO_ORDER`.
10. Rollback rehearsal stops Alpha demand production while the existing weather
    owner continues unchanged; temporary artifacts remain auditable.

## Stop conditions and rollback

Immediately stop Alpha pilot demand production if any identity mismatch,
untyped schema drift, paired-leg gap, stale formal book, budget breach,
unauthorized network receipt, weather health regression, or execution/signing
reachability appears. Preserve all receipts and artifacts, disable the Alpha
pilot entrypoint, verify the existing owner/weather process is unchanged, and
issue `REWORK_OPERATIONAL_BOUNDARY`. Do not delete failed evidence and do not
fall back to another collector or direct CLOB client.

## Required final seal

```text
READ_ONLY_OPERATIONAL_PILOT-evidence-seal/
  README.md
  manifest.json
  hashes.sha256
  commands.log
  source-identity.json
  endpoint-policy.json
  demand-receipts.jsonl
  paired-book-coverage.json
  weather-isolation.json
  capacity-storage.json
  security-canaries.json
  rollback-rehearsal.json
  no-order-ledger.json
  known-limitations.md
```

Passing this gate approves only a bounded read-only pilot. It does not approve
daily unattended operation, general capture expansion, production database
migration, order placement, signing, or funds access.
