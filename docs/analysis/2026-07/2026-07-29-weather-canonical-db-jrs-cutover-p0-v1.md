# Weather Canonical DB JRS Cutover — P0 Record

Status: P0 closed with two auxiliary canonical writers intentionally paused
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
