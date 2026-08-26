# P0 Dependency Graph

## 1. Implementation dependency DAG

该图描述“代码任务何时可以开始”，不把运行时数据流误当实现依赖。特别是：P0-06A～D均不依赖book；P0-05只为P0-06E可选book recall和P0-08 Market阶段提供接口。

~~~mermaid
flowchart TD
  D0[Revised Design Gates PASS]
  C1[P0-01 Shared Contracts]
  S2[P0-02 Alpha Storage/Migrations]
  M3[P0-03 Gamma Catalog Adapter]
  M4[P0-04 Change Events]
  B5[P0-05 Existing-owner Book Adapter]
  A6[P0-06A Recall Aggregator]
  B6[P0-06B Pre-book Structural Recall]
  C6[P0-06C Controversy Recall]
  D6[P0-06D Wallet Recall]
  E6[P0-06E Optional Book Anomaly Recall]
  L7[P0-07 RuleContractCompiler A/B]
  P8[P0-08 Blind Projection + Packet Roundtrip]
  G9[P0-09 Candidate Invalidation + Rank + Ledger]
  E10[P0-10 Harness/Evidence Seal]
  N11A[P0-11 Wave-0 Sandbox Policy]
  N11F[P0-11 Final Capability Proof]
  I12[P0-12 Offline E2E Pilot]

  W[(wallet facts/artifacts\nread-only)]
  Q[(canonical dispute.db\nread-only)]
  O[(existing market-book capture owner)]
  X[(runtime/db/research.db\nadditive alpha_* only)]

  D0 --> C1
  C1 --> S2
  C1 --> N11A
  C1 --> A6
  C1 --> M3
  S2 --> M3
  S2 --> A6
  S2 --> B5
  S2 --> L7
  N11A --> M3
  N11A --> B5
  N11A --> B6
  N11A --> C6
  N11A --> D6
  N11A --> E6
  N11A --> P8
  N11A --> G9
  M3 --> M4
  M3 --> L7
  M3 --> C6
  M3 --> D6
  M4 --> B5
  M4 --> B6
  A6 --> B6
  A6 --> C6
  A6 --> D6
  A6 --> E6
  W --> D6
  Q --> C6
  O --> B5
  B5 --> E6
  B5 --> P8
  L7 --> P8
  A6 --> P8
  A6 --> G9
  L7 --> G9
  P8 --> G9
  G9 --> E10
  G9 --> N11F
  N11A --> N11F
  B6 --> I12
  C6 --> I12
  D6 --> I12
  E6 --> I12
  M3 --> I12
  M4 --> I12
  B5 --> I12
  L7 --> I12
  P8 --> I12
  G9 --> I12
  E10 --> I12
  N11F --> I12
  S2 --> X
~~~

## 2. Runtime event flow

~~~text
Catalog revision -> Change Event
                     |
                     +-> New/Changed + Metadata/Family Recall -----+
                     +-> Controversy Recall -----------------------+
                     +-> Specialist Wallet Recall -----------------+-> RecallHit
                                                                  -> Candidate
                                                                  -> Rule A
                                                                  -> BlindCandidateProjection
                                                                  -> Blind Packet/Result accepted
                                                                  -> FORMAL_REVIEW book demand
                                                                  -> fresh paired book
                                                                  -> Market Packet/Result
                                                                  -> Rule B -> Rank/Ledger

Optional side route:
Change Event -> SENSING book demand -> paired book
             -> Structural Market Anomaly Recall -> RecallHit -> Candidate merge/refresh
~~~

运行时保证：

- 没有book或P0-06E关闭时，P0-06B/C/D仍能生成Candidate；
- SENSING demand不阻断Candidate或Blind；
- FORMAL_REVIEW demand只能在blind result accepted后产生；
- book-side RecallHit晚到且改变frozen blind inputs时，产生RESEARCH_REFRESH_REQUIRED，不静默合并进既有decision。

## 3. Critical paths

Pre-book candidate path：

~~~text
Design Gate -> P0-01 -> P0-02 -> P0-03 -> P0-04
-> P0-06A -> P0-06B -> Candidate -> P0-07/P0-08 -> P0-09
-> P0-10/P0-11 final -> P0-12
~~~

Formal market review path：

~~~text
P0-01/P0-02/P0-04 -> P0-05
P0-06A + P0-07 + accepted Blind result -> P0-08 FORMAL_REVIEW demand
-> paired book -> Market result -> P0-09
~~~

P0-06C/D和P0-06E是独立provider支路；P0-12必须集成验证全部provider，但任何单个provider不可成为其他pre-book provider的公共前置。

## 4. Safe parallel sets

| Wave | Tasks allowed in parallel | Shared lock |
|---|---|---|
| 0 | P0-01 + P0-11 Wave-0 policy/denylist/CI/adversarial scaffold | 两者可立即并行；P0-01独占contracts，P0-11不得先写transport/receipt/env-endpoint artifact schema |
| 1 | P0-02 | migrations exclusive |
| 2 | P0-03 + P0-06A + P0-07 initial compiler | released contract version；不得各自改schema |
| 3 | P0-04 + P0-06C + P0-06D | catalog identity frozen；provider只写各自目录 |
| 4 | P0-05 + P0-06B | change-event contract frozen；P0-06B无book依赖 |
| 5 | P0-06E + P0-08 | book adapter public contract frozen；不同owned scope |
| 6 | P0-09 | candidate transition/rank/ledger exclusive |
| 7 | P0-10 + P0-11 final proof | domain schema frozen；security可阻断merge |
| 8 | P0-12 | integration coordinator exclusive |

每个Wave merge都运行P0-11静态与sandbox subset；final proof不是第一次检查no-live。

P0-11 Wave-0分界：policy、denylist、CI scaffold和adversarial fixtures可与P0-01并行；`AlphaReadOnlyTransport` concrete interface、security receipt及env/endpoint allowlist artifact schema必须等待P0-01 release并只消费其合同，不得由security owner另建共享schema。

## 5. Ownership locks

| Resource | Sole writer during implementation | Other tasks |
|---|---|---|
| shared contracts、BlindCandidateProjection、claim Evidence schema | P0-01 | propose change via contract review only |
| alpha_* migration/repository primitives | P0-02 | no direct SQL/schema edits |
| Gamma/raw/catalog adapter | P0-03 | consume public adapter |
| change events | P0-04 | no direct book connection |
| SENSING/FORMAL_REVIEW demand、book pairing/metrics | P0-05 | no capture service reconfiguration |
| Recall aggregation/Candidate merge interface | P0-06A | providers call public interface |
| New/Changed + metadata/family provider | P0-06B | no book/fair-value model |
| dispute/controversy provider | P0-06C | canonical dispute store read-only |
| specialist-wallet provider | P0-06D | wallet sources read-only；no trade direction to Blind |
| optional book anomaly provider | P0-06E | no fair-value claim；no capture ownership |
| rule compiler/A/B | P0-07 | no alternate parser owner |
| projection/packet filesystem protocol + claim ingest | P0-08 | no implicit external calls |
| candidate invalidation/ranker/prediction ledger | P0-09 | no harness leases/jobs |
| WorkOrder/evidence seal | P0-10 | no domain-state projection |
| capability sandbox/no-live tests | P0-11 | may block every merge |

## 6. Readiness gates

~~~text
READY_FOR_P0_IMPLEMENTATION
  == OFFLINE_IMPLEMENTATION_ONLY

offline Gate 3/4 PASS
  does not imply
READ_ONLY_OPERATIONAL_PILOT

READ_ONLY_OPERATIONAL_PILOT_GATE PASS
  does not imply
PRODUCTION_CAPTURE_EXPANSION

Any production capture change
  requires
manifest -> deploy review -> explicit approval -> health/load/rollback

Any real execution
  is outside this design
  and requires a new design, risk review and explicit authorization
~~~

## 7. Cycle and overlap check

- No DAG cycle：P0-05的runtime FORMAL_REVIEW trigger来自P0-08 result，但实现依赖是P0-05先提供接口、P0-08后消费；二者不是任务环。
- No book gate on pre-book recall：P0-06A/B/C/D没有P0-05依赖。
- No collector overlap：只有P0-05提交typed demand；现有capture service仍是唯一连接/写入owner。
- No wallet tracker overlap：P0-06D只读source facts并写RecallHit。
- No rule owner overlap：Gate A/B共享P0-07 compiler。
- No migration overlap：只有P0-02可写migration；P0-09只用repository API。
- No orchestration overlap：harness只管task/evidence，Alpha只管domain state。
- No execution edge：图中没有order/signing/key/exchange节点；P0-11另外证明通用transport/importlib/subprocess也不可绕过。
