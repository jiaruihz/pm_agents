# Weather Canonical DB JRS Cutover — P0 Record

Status: P0 closed; canonical refresh restored after P1, first-seen writer remains paused
Generated: 2026-07-29

## Incident and pollution window

The 2026-07-28 first-seen forward runner wrote directly to a new
`/Volumes/jrs/pm_agents/runtime/weather.db`, while dashboard/live lineage
continued to use the repository-local 11GB DB. The JRS file therefore contained
new first-seen lineage but no `fact_trades`; the local file contained the
canonical trade/fact history but not all new first-seen rows. Neither side was
a complete truth.

At `2026-07-29T04:32Z` the local canonical was checkpointed and promoted to the
formal JRS path. The repository compatibility path is now a symlink to the same
JRS device/inode. The old JRS DB and the pre-cutover local DB were retained:

- `/Volumes/jrs/pm_agents/runtime/db_cutover_backups/20260729T1130Z/`
- `runtime/weather.db.pre-jrs-cutover.20260729T1130Z`
- `runtime/weather.jrs-first-seen.pre-cutover.20260729T1130Z.db`

## Merge and validation

The retained first-seen DB contributed:

| Table/grain | Inserted |
|---|---:|
| information events | 3,051 |
| observation events | 1,826 |
| forecast curves | 1,063 |
| state checkpoints | 6,522 |
| `v2_event_checkpoint` candidates | 78,672 |

Three source-native revision rows referenced parents absent from both DBs; their
events were retained and only the invalid `revision_of_event_id` links were
cleared. Minimal schema/count/FK/checkpoint verification passed. A full 11GB
integrity scan was not completed because JRS random I/O was too slow; this
cutover must not be described as having passed a full integrity scrub.

Final manifest evidence at `2026-07-29T05:06:29Z`:

- DB route: `healthy`
- expected physical DB: `/Volumes/jrs/pm_agents/runtime/weather.db`
- distinct existing DB paths: none
- all observed consumers opened the JRS path

## SQLite writer collision

Unifying the physical DB exposed a single-writer collision previously hidden by
the split. `canonical-refresh` rebuilt `fact_trades` by dropping the published
table before refill, and the zero-notional first-seen process could hold JRS
writer transactions for minutes. During the 04:31–05:05Z remediation window,
core-carry heartbeat publication intermittently reported `database is locked`;
signal and exchange execution had already completed.

Corrections:

- `fact_trades` rebuild now uses a staging table plus short atomic rename.
- first-seen writes use bounded batches/retries and defer bulk settlement
  attachment.
- busy runtime-state telemetry is recorded as `deferred_db_busy` without
  changing the trading-loop result.
- launchers pin `PYTHONPATH` to their own checkout.

Commits: `4bb95f0d`, `53893a46`, `08d17144`; production equivalents:
`57630a7e`, `1d46653e`, `9953627c`.

Production validation still showed one first-seen cycle exceeding five minutes
on JRS. Consequently `weather_first_seen_zero_notional` and
`com.pm-agents.weather-canonical-refresh` remain intentionally stopped at P0.
Raw collector JSONL is preserved for maintenance-window incremental ingest.
They must not be restored as continuous canonical writers until a bounded
production cycle demonstrates that live heartbeat/decision cadence is not
degraded.

## Maintenance-associated real execution

Restarting the existing authorized `current_yes_core_carry_tiny_live_v2` runner
at 04:43Z produced a Wellington target-date 2026-07-29 split entry:

| Leg | Exchange evidence | Result |
|---|---|---|
| taker | `0xd513…9877a`, 5 shares at 0.942 | matched |
| maker | `0xe5de…b1da`, repriced to `0x8904…9639` | canceled at 04:50:15Z |

Treat the taker fill as real exposure and the maker leg as canceled, not as two
fills. The final code-loading restart added zero live-order rows (`125 -> 125`).

## Final P0 runtime state

- core-carry: `ok`, runtime-state publication `published`, loaded production SHA
  `9953627c`, no restart order increment
- fast-source: `ok`, `live_orders_posted=0`, `live_orders_submitted=0`
- dashboard API: HTTP 200
- canonical-refresh: stopped intentionally
- first-seen zero-notional: stopped intentionally
- rollback DBs: retained; nothing deleted

## Data-feed starvation follow-up

The production feed loop was sequential: one slow `snapshot-targeted` cycle
blocked observations and source-events for more than four minutes. Initial
child deadlines exposed the failure but a 240-second snapshot deadline also
terminated a legitimate long snapshot, leaving forecast-curve capture out of
sync with the newest partial snapshot.

The snapshot producer now runs as one supervised, non-overlapping asynchronous
child in the same canonical JRS tmux context. Observations, source-events and
forecast enrichment retain bounded synchronous deadlines; snapshot retains a
600-second hard deadline without starving those producers. Production evidence:

- snapshot ran `05:25:34Z..05:31:18Z` and completed `returncode=0`;
- while it was still running, source-events completed at `05:29:33Z` and
  observations completed at `05:29:48Z`;
- final health was `warn`, not `fail`: snapshot parity/source model/forecast
  curves/orderbook/live-cross were healthy, with only the explicit Chicago
  `awaiting_first_observation` local-midnight window and a non-trading
  TelAviv weather-state gap remaining.

Chicago's three-source chain was not broken. At 05:17Z Chicago local time was
00:17 and the newest KORD report was the prior local day at 23:51. This normal
pre-first-report interval is now emitted as `awaiting_first_observation` for a
bounded 90-minute grace, never as `fetch_failed`, and does not reuse the prior
day's running maximum.

`source-events` also printed its complete cumulative private `_state` every two
minutes; the tmux log had reached about 400MB. CLI logging now excludes private
state and records only the bounded summary. The first deployed cycle increased
the log by about 1KB instead of serializing the full state.

Production commits: `4f5e1b94`, `e457f011`, `dbff6e9e`, `e5342069`.
Mainline commits: `1ce084f9`, `937a8dba`, `7e1cdbcb`.

## Targeted orderbook coverage and batch alignment

The snapshot collector's nominal 60-second orderbook budget started before
sequential forecast/Gamma work. Time spent outside CLOB requests therefore
consumed the budget, and most later target rows were mislabeled
`orderbook_budget_exhausted`; non-target rows could receive the same misleading
label. The completed 13:25 Beijing snapshot had 968 records but only 33
successful books in its archive, versus 1,386 side rows marked budget-exhausted.
The last 20 pre-fix snapshots all had only 23–41 successful books and roughly
1,300–1,560 exhausted side rows. The earliest directly verified affected
capture is `2026-07-01T18:01Z`; 2,314 published snapshot files existed at fix
time, so this is a long-lived coverage defect, but a full 9GB historical
rescan was not performed and the exact affected-file count is not asserted.

Corrections:

- budget accounting now measures only CLOB batch work;
- four workers and a 120-second targeted budget are explicit production
  parameters;
- scope-skipped and genuinely incomplete targets are separate states;
- every snapshot publishes an aggregate target-coverage contract;
- production health fails incomplete targeted coverage;
- forecast curves and the compressed orderbook archive become durable before
  the final snapshot path is atomically published;
- forecast first-seen lookup scans only capture-date directories capable of
  containing the current target dates instead of all 2,314 historical
  captures.

The first fully validated production batch used snapshot timestamp
`2026-07-29T05:51:32Z`:

| Artifact | Evidence |
|---|---|
| snapshot | 979 records, atomically visible at 05:55:16Z |
| targeted orderbooks | 130/130 `ok`, 0 incomplete, 18.263 seconds of CLOB work |
| orderbook archive | 130 rows, all `ok`, same snapshot timestamp |
| forecast curves | 98 rows, same snapshot timestamp, durable before snapshot |
| full snapshot cycle | 05:51:32Z–05:55:31Z, 3m59s, `returncode=0` |
| production health | `snapshot_orderbook_coverage=ok`, overall `warn` only for non-trading weather-state coverage |

Production commits: `d534fa9c`, `85b7f112`, `d40fb26a`.
Mainline commits: `cf40b586`, `bac065e9`.

## Signal PIT availability alignment

The published snapshot timestamp previously meant collection **start**, while a
complete batch did not become visible until 83–300 seconds later. The signal
runner nevertheless used the start timestamp as its PIT cutoff. It could
therefore exclude observations/forecast curves that were already part of the
published batch, mix the new snapshot with older enrichment, and evaluate a
state that never existed as one atomic decision input. The feature builder also
allowed a record-level snapshot timestamp to override the caller's explicit
as-of time.

Corrections:

- snapshots now carry separate `collection_started_at_utc`,
  `available_at_utc`, and `published_at_utc`;
- signal decisions and freshness checks use batch availability;
- an explicit feature-layer as-of timestamp takes precedence over embedded
  record metadata;
- old snapshots remain readable through the legacy `ts_utc` fallback;
- runtime-state publication uses the schema-valid `blocked` health state, so an
  executor error is no longer hidden by a telemetry CHECK failure.

The first validated production batch after deployment was collected at
`2026-07-29T06:03:41Z` and became available at
`2026-07-29T06:07:28.394Z`. It contained 128/128 complete targeted books and 99
matching forecast curves. All 35 decisions used `06:07:28Z`; no selected
observation was newer than its decision time, no decision had an empty curve,
and pre-live freshness measured about 0.30 minutes from availability rather
than about four minutes from collection start.

### Live-decision impact

All 22 unique core-carry live entry signals from 2026-07-25 through 2026-07-29
were replayed at the historical snapshot file modification time, used as the
best retained proxy for batch availability. Thirteen would still have entered;
nine would not. This is an eligibility/data-alignment impact, independent of
whether the strategy itself was accurate.

| Target date | City | Signal suffix | Old p | Corrected p | Corrected edge | Counterfactual |
|---|---|---|---:|---:|---:|---|
| 2026-07-25 | PanamaCity | `c8a602` | 0.942186 | 0.926225 | -0.015635 | would not order |
| 2026-07-25 | Chicago | `47a952` | 0.960774 | 0.938906 | -0.003914 | would not order |
| 2026-07-26 | Guangzhou | `24cfdd` | 0.997422 | 0.988412 | -0.002088 | would not order |
| 2026-07-27 | Wellington | `163596` | 0.981631 | 0.975073 | -0.005907 | would not order |
| 2026-07-27 | Taipei | `b8d4e0` | 0.925074 | 0.897207 | -0.026473 | would not order |
| 2026-07-27 | Singapore | `cf8eda` | 0.875516 | 0.852079 | -0.004301 | would not order |
| 2026-07-27 | Beijing | `8b3814` | 0.906142 | 0.904088 | -0.000412 | would not order |
| 2026-07-27 | Wuhan | `8dc86f` | 0.950203 | 0.936739 | -0.006081 | would not order |
| 2026-07-27 | Chengdu | `f6a5e2` | 0.988502 | 0.914653 | -0.015132 | would not order |

The other 13 signals remained eligible; their corrected probability deltas
ranged from -0.003313 to +0.007892 or were zero. Three historical snapshots
lacked an exact retained curve capture (Chicago 07-25, San Francisco 07-26,
Cape Town 07-27), but the frozen model did not consume the hourly curve
directly, so their probability replay is complete. The replay keeps each
signal's direct CLOB book/effective cost unchanged because the runner fetches
that book after snapshot publication. File modification time is still a proxy,
not a first-class historical availability event; the new explicit availability
fields remove that ambiguity prospectively.

Production commit: `6edba326`. Mainline commit: `0d052420`.

## Order-state and crash-consistency remediation

The core-carry maker lifecycle treated the latest legacy `live_orders.jsonl`
row as authoritative. A Wellington replacement order
`0x890457…ba9639` had actually been cancelled at
`2026-07-29T04:50:15Z`, but that side effect existed only in
`execution_journal.jsonl`; the replacement/projection step did not finish.
Every later loop therefore treated the stale `submitted` row as active, tried
to cancel it again, and published `executor_error`.

The runner now replays venue-order state from the side-effect journal before
lifecycle evaluation and writes one deterministic terminal projection. The
production recovery added exactly one row, changed that order to `cancelled`,
and reduced each subsequent cycle from one failing lifecycle plan to zero.
Authenticated CLOB open orders were zero before and after that recovery.

The wider signal→plan→exchange handoff also had two crash windows:

- `entry_attempts.jsonl` recorded `planned` before invoking the executor, so a
  crash before execution permanently consumed the signal;
- an exchange outcome could be journaled before its legacy live-order
  projection, leaving the venue action real but invisible to downstream
  consumers.

Corrections:

- business-blocked attempts remain terminal, but `planned` without durable
  execution evidence is retryable;
- planned attempts are appended only after the executor returns;
- a stale claim with no side-effect attempt can be reclaimed after a 60-second
  concurrency grace;
- a journaled submit outcome missing its live projection is projected without
  another venue submit;
- an attempt with no outcome is fail-closed as
  `reconciliation_required`, never automatically resubmitted.

Historical core-carry audit after remediation:

| Check | Result |
|---|---:|
| planned entry signals | 22 |
| planned signal without any live projection | 0 |
| submit identities | 11 |
| attempt without outcome | 0 |
| submitted venue order IDs | 10 |
| submitted outcome missing live projection | 0 |
| recovered terminal maker rows | 1 |

The fast-source live runner had the same class of gap: its dedupe key was
persisted only in the end-of-cycle `state.json`, after the CLOB call and
`orders.jsonl` append. It now fsyncs an execution-side-effect row immediately
before and after each live child, reconstructs dedupe keys from the journal and
durable order stream, and blocks ambiguous dispatches as
`execution_reconciliation_required`. A confirmed submit can no longer be
repeated merely because the process died before the state-file replace.

Production verification:

- core-carry SHA `79e15231`: 47 tests passed; runtime `ok`,
  `live_errors=0`, `reconciliation_required=0`;
- fast-source SHA `f0074e2f`: 83 tests passed; runtime `ok`,
  `execution_reconciliation_required=0`;
- the fast-source restart wrote no order rows and submitted no order;
- the one authenticated open order seen during the fast-source restart was an
  independently running, correctly journaled Guangzhou core-carry maker, so it
  was left untouched.

Production commits: `d8d23b65`, `79e15231`, `f0074e2f`.

## Canonical trade-fact duplication, false fill, and stalled refresh

The post-trade refresh had three independent correctness defects and one
performance defect:

1. A physical CLOB `order_id` could be imported again under a new,
   plan-dependent `execution_id` after migration enrichment changed. Fifty
   later executions were aliases of an earlier canonical execution.
2. The old public-activity fallback assigned account-level activity to a local
   order even when authenticated order evidence said `CANCELED` with
   `size_matched=0`.
3. The routine refresh only named the D1 order journal; current core-carry and
   fast-source journals were not part of its explicit input set.
4. Every routine fact build scanned and randomly joined the full physical
   tables in the 11GB JRS DB. The strict gate repeated the same scans and read
   large `orders.exchange_response` payloads. Both could spend more than a
   minute in filesystem I/O with no progress output.

Corrections are append-only at the canonical layer:

- `order_execution_aliases` maps the later execution to the earliest physical
  order execution; raw orders and fills are retained.
- `fill_validity_adjustments` excludes disproved fills without updating or
  deleting `fills`.
- the refresh explicitly ingests D1, core-carry and fast-source order journals;
- `fact_materialization_watermarks` advances `fills`, `settlements` and
  `settlement_outcomes` rowid cursors in the same transaction as the scoped
  fact publication;
- routine refresh materializes only new or invalidated `fill_id` /
  `target_date` rows; explicit rebuild retains the full builder;
- compact covering indexes keep the CLOB gate away from large order payload
  pages; matched response amounts are loaded lazily only for preliminary
  over-cap orders.

### Impact radius

The duplicate lineage affected 39 fill rows: one fill on 2026-07-20 and 38 on
2026-07-26. It duplicated 341.5 shares and $279.677 of fill cost in
`fact_trades`. Settled realized PnL was overstated by $0.30149; the much larger
damage was polluted volume, win-rate and ROI denominators. The raw rows remain
available for audit, but all 39 are now absent from the derived fact table.

The exact affected physical order IDs are:

```text
0xd9edc94f8eae8f042d774c0a8d0a4df83028e7c0790db351a0e7655db26e6d28
0xabf6fec7bec9e792d51c8fdb1d956d3e0611eb873779e3b91cddaca8f6ba7dd2
0x4cceacfa3a3e08188873f94e3a0bc2e923efb359633e96beceef27ceb4943d21
0x4aa3b77904edd0f82ba794bffcd0893e678c702d7e594b863f988d96c274fb6e
0x9f64f6930a8284e2912f57e16d16195036cc76e60d0bdfe49e7172a8b7d485e7
0xc6599365ebc64e2b2ba9b365fa37eef35178e8891c98354c7d4227edc55fecb1
0xfb3e14f81264a851f049d7d9b131afc5c7d7690d848b3ee74426f36da05aa528
0x4da9b43cdc124ac42701b655788ee684616de26d042e659b569bbcbda981ce56
0x4717089ddf3df06033fbde7c228836ef0b1c47cf083ed705d554941a3adaf8ff
0xc92c6caab73a12d24f6fa92aa6a8090a9449971b0a465de9e761d43c57d21898
0xa9c2b041db74e2645fd5bd243dadb1ec6a65a7a373d892272bd3a1f6abc29985
0x98ce03c035a276e4df2cbceef4f13ee8b4c32f25352dbf396298d84df2c8428f
0xda80f76d8fda4b8bc99362fbf7a88c6327c9ca765aa6b7490e054a9f20f9c416
0xe257ebb0d24eec5746ec4c1d4c7475182e86c16832c7b9fcea2c6a37b6616646
0x9820f2c92538c0e53cb0739cbeac9683d295f44191439465f33e58b21df0e268
0x26bcc3cd70e5c3543bf0937101a684751f3e17cdea386921004d19c10226e1a5
0x48643866187e2e6c0d4584582bb2fbd180b592b61db14f15e64fcab19fde173f
0x19eae1e11a5fb7c49e669badb0df011d9f66efd0d30fc94788cbce19e87f2422
0x25b3d6b436f241663ed30cf280a13ded15bb07ae52c8b4ed45a9b36ddccd7c36
0x482eb0442af75e409e7aa4f5519ad93dde8506d9cf3fcd9e0c0990a88bbe6638
0x289c14526d6264c730774660716e3158a10b53a58acfb6d5c0176f9685937b7a
0xa58bf79dd319d66e0294005494b3f5014a0f5141213e7ab23ba611833382be8f
0xb40fefb9826715875b4f207f0fec170cf5a9a8d84ccd85e1a037a27e54c52080
0xaa899b7f2974ef6bbab46ca8a567eed045faca2c0e337e4bd51be78849c6fe3a
0xbff148008c7f199ffaa0d2e8b0363257597c96479da215f6538ec2518b7c03a1
0x5eb1c4fbbf6910c235ee68ebfc2bcbfc0ddbc52d56c4094fd298f7d783b8a749
0x6e3fe5b6a78f414d2e994f6613741224990a2bc3ee19510f798dee2f7062e162
0x2466c429d31047f7f8a96420f10f61ef6d1c744e41a9065ee9635106134666fe
0x78a828d2cd434382712541adeca5a08d0b882d4170272bc52b497131beddb5c1
0x86ee7b20432ed9a1469d261339e8995c82fca541e82342fdef17acffcb6a1565
0x26bd458162d582d15a9720da9a9359d6e6cf1a767adfe4b8923682576c501d2c
0x87395674a0e71a300bf00aa0a2de629093b603a7ca8a0a9685e1d94807964c9b
0xbe9dcb8afd306543cf7c46a55ebed7565c33602cf43edb6d034806a4e437cfb9
0x711895d24ea9bbb8f5cc381fe5fdee961e50f8b3d915e86a03bd9782b42f2d42
0xe13e3559cc4246ab1fb5e335a147de56ab66da7b7dc522fa09762c3cb43c5ac3
0x32e637b5299009195975fa782f47a321ef2470f76918f656e0f2026072be2455
0xfbdd998a4c66fe6f21d4e2eeb0849de049dd09fa44df0168c8f1fe4148e17179
0x224a1e4de692aea7dfefcb2f1f15a065027d87ae4888470f5c31d1d99df03fb4
0xd044e5f45950bf790db517f2adf32253f11f81cadd10c2fccf064f677e43385a
```

The disproved fill was
`7acaee34a061a828ef79c57f3826529127d3c4efd41d1168a22ebdde2c2d9f6a`
for physical order
`0x53c34b1f8058babbd938d49766fabaa6f77b43096aabbdfba3629bf575eb9839`.
The raw fast-source journal line records immediate cancellation,
`order_after_cancel.status=CANCELED`, `size_matched=0`; its validity adjustment
is `0a5ff70546e36af20e03ba588f5b3a10a042ad5d44a7fb8c304d1d8379f472d0`.

### Final validation

- first scoped cutover: 46 fill IDs, four inserted/changed facts, 39 removed
  alias facts, 9.44 seconds;
- post-index no-op refresh: 42 fill IDs, zero fact changes, 13.54 seconds;
- strict CLOB gate: 11.59 seconds before warm-cache follow-up, then 3–5
  seconds inside the full refresh; `gate_pass=true`;
- final `fact_trades`: 4,865 rows, including 1,320 `live_real`; newest fact
  fill is through `2026-07-29T07:57:07.580865Z`, and the refresh published it
  at `2026-07-29T08:04:34.051877Z`;
- `alias_fact_rows=0`, `excluded_fact_rows=0`;
- DB/cache fill IDs match, over-order keys are zero, missing-order rows are
  zero, and effective fill cost equals fact cost;
- actual canonical refresh completed successfully in 111.77 seconds. Of that,
  the remaining dominant cost is authenticated serial status lookup for 104
  recent CLOB orders; it confirmed one new fill, one cancellation and 102 open
  orders. This is bounded and observable, but remains a P1 latency target.

Production commits: `b9feb50a`, `39b9b8c1`, `59bfe74a`, `e99a7c07`,
`4683a7be`.

### P1 refresh latency closure

The remaining 111.77-second routine refresh was not one isolated network
delay. Cold-cache replay exposed four sequential costs:

1. authenticated CLOB order status was fetched one order at a time;
2. order discovery parsed large `exchange_response` values and joined
   `plans/signals` even though the authenticated path did not need signal
   identity;
3. alias reconciliation ran a full SQL window plus lineage joins over every
   CLOB order on every refresh;
4. the incremental fact builder replayed every historical alias and adjustment
   row even when no correction had changed.

The P1 implementation:

- fetches immutable CLOB status snapshots with bounded concurrency
  (`status_workers=8`) while keeping all SQLite writes single-threaded and in
  deterministic order;
- records progress at roughly 25% intervals and publishes
  `status_requested/status_fetch_seconds` in the sync result;
- uses a compact expression covering index for CLOB discovery, including exact
  immediate-match amounts, without reading payload overflow pages;
- loads condition/token identity only for the unauthenticated public fallback;
- linearizes alias discovery on a compact covering index, and performs
  `plans/signals` lineage joins only for newly discovered duplicate
  executions;
- adds rowid watermarks for alias, validity, fee, price and timestamp
  corrections, so unchanged historical corrections are not replayed;
- fixes `weather_jrs_tmux_mkdir` using zsh's special `path` variable as a local
  loop name, which had temporarily removed `shasum/awk` from command lookup.

The two covering indexes took 46.96 and 93.17 seconds to create once on the
11GB physical DB. They are persistent DDL and are not routine refresh costs.
During validation, one run was terminated while the old alias query exceeded
the per-stage threshold, and one was terminated while the old correction
replay exceeded it. Neither reached fact publication. The correction cursor
cutover was then seeded only after proving every existing correction predates
the successful `2026-07-29T08:04:34Z` fact build:

| Correction source | Seeded rowid |
|---|---:|
| `order_execution_aliases` | 50 |
| `fill_validity_adjustments` | 1 |
| `fill_fee_adjustments` | 1,014 |
| `fill_price_adjustments` | 2 |
| `fill_timestamp_adjustments` | 1 |

Final production measurements:

- authenticated status dry-run: 104 submitted orders, 25 exact local matches,
  79 external status requests, 3.51 seconds for the request batch and 9.34
  seconds total; zero external errors;
- alias no-op reconciliation: 62.59 seconds before lazy lineage, 1.90 seconds
  after;
- fact no-op materialization: scope zero, 5.14 seconds;
- complete canonical refresh: 23.36 seconds versus the prior 111.77 seconds,
  a 79.1% reduction / 4.8x speed-up;
- final live state remains 4,865 facts / 1,320 `live_real`,
  `alias_fact_rows=0`, `excluded_fact_rows=0`, and strict CLOB
  `gate_pass=true`.

P1 production commits: `2cc6f561`, `af0083cb`, `fd9273cf`, `717ba5cd`,
`0d0507b2`, `b84d6850`.

### P1 scheduled refresh restoration

The canonical refresh LaunchAgent had remained stopped after P0. Its wrapper
delegated the refresh process to the canonical tmux server, but still created,
removed and read the JRS-backed log/status files from the LaunchAgent context.
The plist also pointed launchd stdout/stderr directly at the JRS-backed runtime.
That violated the single permission-bearing context even though the tmux server
itself was healthy.

The wrapper now performs every JRS log/status operation through the pinned
`weather-data-feed-jrs` tmux server. Exit status is copied from JRS to a
per-invocation local temporary bridge before the LaunchAgent reads it, and
launchd stdout/stderr live under
`~/Library/Logs/pm-agents/weather-canonical-refresh`. Entrypoint tests reject
direct JRS mkdir/remove/read regressions.

Production was restored at a 300-second interval from committed checkout
`0bcbd9cd`:

- first cycle completed with exit 0 and imported one previously missing
  Busan `BUY_NO` fill (`5.81395` shares at `0.14`), moving canonical facts
  from `4,865 / 1,320 live_real` to `4,866 / 1,321 live_real`;
- that cold cycle spent about 110 seconds materializing the one-fill scope,
  but core-carry continued publishing `status=ok` throughout;
- the next two automatically scheduled no-op cycles completed in about 22 and
  58 seconds, both with `scope=0`, zero fact changes and `gate_pass=true`;
- LaunchAgent stderr remained empty and all three observed cycles returned 0.

The production live runner was not restarted and no execution parameters,
orders or funds behavior changed. `weather_first_seen_zero_notional` remains
intentionally stopped; its separately unbounded writer path is not covered by
this restoration.

Production commit: `0bcbd9cd`. Mainline commit: `4439365f`.

### Unified one-shot entry and authenticated fail-closed correction

The first restoration still left the one-shot tmux lifecycle implemented in
the canonical-refresh wrapper. It is now centralized as
`weather_jrs_tmux_run_oneshot` in `weather_jrs_tmux_env.sh`: socket selection,
write probe, JRS directory creation, session dedupe, log/status paths, waiting
and exit-code bridging have one implementation. The canonical-refresh entry
now declares only its session, job directory and business command.

Production observation then exposed a separate fill-integrity P0. At
`2026-07-29T10:43:43Z`, authenticated CLOB setup failed on an SSL handshake.
The refresh correctly marked `data_incomplete=true`, but the old fallback still
allocated account-level public activity to submitted orders and inserted two
false Wellington maker fills even though the execution journal had durably
cancelled both orders:

| Order | False fill | Shares | Price | False cost |
|---|---|---:|---:|---:|
| `0xe5de…b1da` | `6fb914…17d86b` | 5 | 0.890 | $4.450 |
| `0x890457…9639` | `78e7ba…95a7` | 5 | 0.885 | $4.425 |

Impact was exactly two derived facts, 10 shares and $8.875 of false open cost
from `2026-07-29T10:44:01Z` until append-only validity adjustments were written
at `2026-07-29T11:10:31Z`. Raw fills remain retained for audit. The scoped
incremental rebuild removed both from `fact_trades`; effective canonical counts
returned to 4,866 facts / 1,321 `live_real`, and the strict gate passed with
1,321 effective DB fills and zero fail reasons.

Production canonical refresh now invokes fill sync with
`--require-authenticated`. If authenticated setup is unavailable it returns
incomplete and performs no public order allocation; the existing retry loop
fails the refresh rather than publishing approximate fills.

Production commits: `5c10ef22`, `73e0ef69`. Mainline commits:
`1d661aff`, `8d6abdc8`.
