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
