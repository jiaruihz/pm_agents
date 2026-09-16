# Weather JRS Runtime Incident Ledger

Status: current-source
Updated: 2026-08-27
Scope: Mac canonical JRS tmux permission context, production runtime control,
collector continuity, and recovery acceptance.

This ledger records operational evidence and impact. Raw runtime logs and macOS
crash reports remain on the production host; they are not copied into Git.

## 2026-08-03 JRS Permission-Context Failure

- Last successful source event: `2026-08-03T03:34:48Z`.
- First observed failure: `2026-08-03T03:37:01Z`.
- First recovered observation: `2026-08-03T15:48:42Z`.
- Source-event coverage gap: `12h13m54s`.
- Last good targeted snapshot: `2026-08-03T03:29:09Z`.
- First recovered targeted snapshot: `2026-08-03T15:55:51Z`.
- Snapshot coverage gap: `12h26m42s`.

The canonical socket and pinned tmux binary were present, but that identity did
not prove that the existing tmux parent retained macOS TCC access to
`/Volumes/jrs`. The interval is collector coverage missing, not a strategy
filter or evidence of no opportunity.

## 2026-08-04 Canonical tmux Crash

macOS crash report `tmux-2026-08-04-002658.ips` records `SIGSEGV` at
`2026-08-03T16:26:57Z` in:

```text
cmd_run_shell_callback -> cmd_run_shell_print
```

The five-minute canonical refresh LaunchAgent was the recurring caller of the
old tmux `run-shell` callback path. Its crash removed the canonical server and
all business sessions together. Source events resumed at `16:30:43Z`; the next
full targeted snapshot completed at `16:35:54Z`, after the previous good
snapshot at `16:12:43Z`.

The 15 runtimes initially described as manual were not independent restart
loops and did not cause the crash. They were 11 zero-notional shadows, three
collectors, and one monitor that had accumulated over earlier business phases
and were collateral victims of the shared server failure.

## Trading and Data Impact

- Both live raw order files contain zero rows after
  `2026-08-03T01:58:09.698036Z` during the incident window.
- Authenticated CLOB reads before and after recovery returned zero open orders.
- Canonical reconciliation found zero incremental fills, cancellations, or
  exchange errors caused by recovery.
- Counterfactual wrong-order count: `0` evidenced orders.
- The impact is collector/signal coverage loss; missing opportunities cannot be
  reclassified as strategy-filtered rows.
- Post-repair fill coverage gate: `1342/1342`, with DB/fact cost difference `0`
  and exact cost `$4022.454778`.

## Control-Plane Correction

Main control commits:

- `0fe00fee`: remove tmux `run-shell` from checked JRS operations.
- `978e63d1`: make business helper calls tmux `-N` attach-only.
- `565c77a6`: register the 15 historical runtimes in the controller.
- `c666fddb`: harden AGENTS/CLAUDE production-control prompts.
- `4b694774`: unify guarded replacement in every production helper.
- `49fc089c`: retire the old business LaunchAgent installers.
- `6d994f02`: require explicit health evidence for every business runtime.

Current invariants:

- Only controller `recover-jrs-context` may create or recreate the canonical
  permission host.
- Persistent session mutation requires controller authority.
- Canonical refresh is a bounded attach-only one-shot and fails closed when the
  server is absent.
- All 22 business runtimes have start, dependency, and health contracts; the
  permission-host keeper is the only manual runtime.
- Ten historical LaunchAgent labels are disabled at filename and launchd
  override layers. Their installers fail closed and the files remain as
  historical evidence.
- All seven active/operational checkouts carry the same canonical helper SHA-256:
  `f1a43df6eef58fceb9feef6ed8f5978c6528e0af074bc1445b6f6bafddd98737`.

## Acceptance Evidence

- Canonical tmux PID remained `73548`; session count remained `23` during
  attach-only and unauthorized-mutation probes.
- Unauthorized `new-session` was rejected and created no session.
- `tmux -N` against a missing socket returned failure and created no server.
- Canonical refresh production run `728` exited `0` without new stderr and did
  not replace the canonical PID.
- Controller result: `23/23` healthy, zero critical runtimes, zero recovery
  actions, zero extra sessions.
- Targeted tests: `64 passed`; both production skills validated; shell syntax
  passed in the main repo and all production checkouts.
- Lock/unlock recovery was exercised successfully.

At that acceptance point, reboot/login had not been exercised. Therefore the accepted wording was
`entrypoint race eliminated; recovery improved and production-validated`, not
`resolved permanently` or `一劳永逸`.

## 2026-08-13 WindowServer Failure And Reboot Recovery

At `2026-08-13 09:51:14 +0800`, macOS watchdog terminated WindowServer after
its main thread stopped checking in. This was not an ordinary idle lock. The
failure overlapped an unbounded full-history alignment replay; system pressure
from that replay is a likely trigger, but the crash report does not prove it
was the sole cause. The replay was subsequently bounded and committed as
`92b0c572`.

The WindowServer restart invalidated the existing canonical tmux permission
context. A later host reboot removed the tmux server and every registered
session. After login, the pinned production NVMe mounted at the expected UUID,
the canonical DB compatibility path resolved to the same device/inode, and the
storage identity audit had zero critical or warning findings.

Recovery used only the production controller:

```text
recover-jrs-context --apply --confirm-live --restore-manifest <saved-manifest>
```

The prospective permission-host probe succeeded before the canonical server
was created. Controller dependency ordering restored all 34 registered
runtimes. A transient Open-Meteo SSL timeout degraded three of 94 city-target
requests on the first forecast pass; cached curves preserved 100% coverage,
and an exact controller restart of the safe forecast collector returned it to
`status=ok`. Two long-period shadows were also restarted through their exact
safe controller contracts so their first post-reboot health artifacts did not
remain stale.

### Impact and acceptance evidence

- Last pre-failure market-book batch: `2026-08-13 09:48:05 +0800`; first
  recovered batch: `11:10:22`; observed gap `1h22m17s`, or 16 nominal
  five-minute collection epochs without a durable batch.
- Last pre-failure strategy snapshot: `09:43:54`; first recovered snapshot:
  `11:11:22`; observed gap `1h27m28s`, or eight nominal snapshot epochs.
- Both active live journals contain zero new rows from
  `2026-08-13T01:51:47Z` through recovery. Authenticated CLOB post-recovery
  evidence returned zero open orders. No recovery duplicate order or fill is
  evidenced.
- Missing PIT source/book epochs cannot be reconstructed as though they had
  been observed, so missed opportunities remain a coverage gap rather than a
  strategy-filter count or a claim of zero signals.
- Final controller result: `34/34` runtimes healthy; JRS context and manifest
  healthy; data-feed semantics healthy; observation cache `41` rows with no
  blocking invalid record; latest snapshot `963` rows with `106/106` strategy
  book targets complete; API `:8000` and FE `:5174` listening.

The post-reboot wrapper itself exposed a separate control-plane bug: its
read-only `plan` preflight correctly returned exit code `2` for a missing
stack, but shell `set -e` aborted before the requested recovery. The wrapper
now treats only the documented `0` and `2` status codes as valid preflight
results while preserving all other invocation errors. A regression test
proves explicit recovery still executes when manifest and plan report the
expected post-reboot critical state.

This run exercises reboot/login, fresh permission-host creation, canonical
server recreation, full controller topology recovery, raw freshness,
authenticated open-order evidence, and API/FE postconditions. It improves and
validates recovery; it does not eliminate the architectural macOS TCC/GUI
dependency or justify unattended restoration of live trading.

## 2026-08-20 Recurrent JRS Write Failure: Contained

At `2026-08-20T03:40:08Z`, the market-book REST and WebSocket collectors again
received `Operation not permitted` while atomically writing under
`/Volumes/jrs/weather_data_feed_service_runtime/market_books`. The stale
WebSocket health artifact made the data-feed and live fast-source dependency
chain fail closed. Controller-only recovery first restored the canonical JRS
permission context, then restarted the safe `weather_market_books` collector
and the telemetry-only source-event shadow. By `03:54Z`, the market-books,
data-feed, live fast-source and source-event chain were healthy; canonical
refresh subsequently completed with exit status `0`.

During recovery, LA/KLAX emitted one incomplete `aviationweather_cache_csv`
row at `03:51:52Z` that reset its running maximum from `26.7C` to `21.7C`.
The next complete primary batch at `03:53:28Z` restored `26.7C`. The production
health check now records that raw regression but only fails closed when the
latest cache remains below the earlier maximum for the same city/date/station.
This preserves the evidence without treating a repaired state as current
corruption.

Impact review: the affected LA target date had no live order journal rows in
the `03:40Z–03:59Z` window. The two journal rows at `03:57Z` are the taker and
maker legs of one Tokyo/JMA event, after recovery, with a fresh book; its taker
leg matched `8` shares for `$7.20` and is unrelated to the LA cache row. No
recovery duplicate execution identity or LA-derived fill is evidenced. This is
contained/recovery-improved, not a claim that macOS TCC/JRS failures are
permanently eliminated.

## 2026-08-24 Host Reboot And Full Runtime Recovery

The Mac rebooted at `2026-08-24 00:48:52 +0800`. After login the production
NVMe and canonical DB identity were healthy, but the canonical
`weather-data-feed-jrs` tmux permission host and every registered session were
absent. A strict manifest was saved on the internal disk, then the controller
performed the authorized bounded recovery:

```text
recover-jrs-context --apply --confirm-live --restore-manifest <saved-manifest>
```

The prospective host passed JRS write and canonical DB read probes before the
controller created the new canonical server. Dependency-ordered recovery
restored every runtime that had been active at reboot, including both guarded
live instances. The two dispute/court zero-notional runtimes remained stopped:
their production contract had already marked them `manual` and user-paused
since `2026-08-14`, so the reboot recovery did not override that decision. The
market-state zero-notional shadow was restored and its process is alive; its
stale artifact incident also predates the reboot and remains a separate health
issue.

### Impact and acceptance evidence

- The last completed market-book batch before failure was
  `2026-08-23T16:38:30Z`; the `16:43:21Z` batch was interrupted. The first
  recovered batch completed at `16:54:45Z` with `2,178/2,178` books and zero
  failures. The durable-batch gap was `975s`, covering three nominal
  five-minute epochs.
- Strategy snapshots advanced from `2026-08-23T16:40:53Z` to
  `16:55:35Z`, a `882s` gap and one missing nominal snapshot epoch. Both
  adjacent snapshots contain `1,053` records. Missing PIT epochs remain a
  coverage gap and must not be relabelled as strategy-filtered zero signals.
- Both active live journals added zero rows after the reboot, and the canonical
  fill cache contains zero fills after the reboot. An authenticated CLOB read
  found `30` open orders; all were created between `2026-08-19T03:36:10Z` and
  `2026-08-22T22:44:54Z`, before the reboot. Recovery therefore created zero
  new or duplicate order and zero fill/notional delta.
- The fill coverage gate passed: `1,544/1,544` effective live fills, zero
  DB/raw mismatch, zero missing-order rows, zero over-order keys and zero
  unknown fee lineage.
- Post-recovery JRS context, storage identity and data-feed semantics are
  healthy. The strict manifest has only two warnings for unrelated research
  processes that were already running from a temporary checkout; production
  DB/session/release identity is healthy. Both live summaries are fresh with
  `status=ok`; API `:8000` and FE `:5174` are listening. The separate crypto
  PM5M registry was also reconciled to `45/45` retained LaunchAgents, with
  fresh BTC/ETH collector, settlement and context artifacts.

This is a successful post-reboot recovery and additional real reboot/login
acceptance evidence. It remains `recovery improved`; it does not eliminate the
architectural macOS TCC/GUI permission-host dependency or authorize unattended
live restoration.

## 2026-08-27 Production Release-Root Migration

The 23 weather production release checkouts were moved from ad-hoc
`/Users/deepsleep/projects/pm_agents*` directories to the declared managed
root `/Users/deepsleep/.local/share/pm_agents/releases/<release_id>/<sha>`.
All 23 are detached linked worktrees of the operational repository, have the
declared HEAD, share its Git common directory, and route `runtime/weather.db`
to the unchanged JRS physical canonical DB. The old project-root checkouts
were removed only after process cwd/command, tmux pane and LaunchAgent scans
showed no consumers. Unique legacy feature stores were hash-verified and
retained under the JRS archive before removal.

The controlled cutover ran approximately `2026-08-27T15:05Z–15:20Z`. During
the first core-carry restart, its new checkout-relative `runtime/weather.db`
default created a zero-byte split SQLite file. The runtime failed before it
could read `strategy_instance_runtime`; the local file had no tables and no
canonical writes. The file was isolated, every release DB path was pinned to
the canonical JRS DB, and controller restart restored the runtime. Review of
the affected approximately four-minute window found `entry_plans=0`,
`live_orders=0`, and `signal_status=already_processed`: extra orders `0`,
missed orders `0`, fills `0`, and canonical DB pollution `0`.

The first post-cutover canonical refresh also exposed a release-relative fill
cache default. It produced a failed gate only inside the release checkout; it
did not alter canonical fills or `fact_trades`. The operational wrapper now
provisions an explicit pointer to the operational canonical fill cache before
each refresh. The next LaunchAgent run exited `0` and the gate passed with
`1,567/1,567` fills, zero DB/cache ID delta, zero DB/fact cost delta, zero
missing-order rows, and zero over-order keys.

Final acceptance: strict manifest `healthy` with no findings and no missing
pre-change sessions; controller, JRS context, storage identity and data-feed
semantics all `healthy`; all 31 release-backed desired-running runtimes run
from the managed root, both intentionally paused dispute runtimes remain
paused, and the JRS context keeper was not migrated. Authenticated CLOB
evidence before and after cutover had the same balance (`162513747`) and exact
same 32-open-order payload (SHA-256
`5cf2cabe1961371ec438120f6a3c1d78a033f67e61d03e4b03bb5119f3da7542`).
The pre-existing JRS permission-host failure observed before the cutover was
recovered through the controller; the migration does not claim to eliminate
the underlying macOS TCC dependency.

## 2026-08-28 Canonical Refresh Log Growth Contained

The registered five-minute canonical refresh one-shot appended every run to
`runtime/weather_edge_v1/canonical_refresh/tmux.log` without a retention
contract.  The log reached `83,450,763` bytes even though no persistent process
consumed it.  The one-shot helper now supports opt-in compaction, and the
canonical wrapper opts in at `64 MiB`, retaining the most recent `8 MiB` before
the next append.  Compaction runs inside the successfully created named tmux
session, so tmux session-name uniqueness atomically prevents a concurrent
caller from replacing a log that another refresh is writing.  Other one-shot
callers remain unchanged unless they explicitly opt in.

The first scheduled run after commit compacted the old log to `8,421,700`
bytes.  That retained tail was moved to the recoverable cleanup set, and a
reinstalled registered LaunchAgent completed with exit status `0`; its new log
was `16,763` bytes.  The post-change manifest was `healthy` with the same 32
pre-change sessions, controller and storage identity were healthy, and the
fill coverage gate passed with `1,567/1,567` DB/raw fills and zero cost delta.
No strategy configuration, release pin, runtime root, order journal, fill,
position or execution mode changed; order/fill impact is zero.

## 2026-08-30 Host Reboot Recovery With Pre-existing Health Contract Drift

The Mac rebooted at `2026-08-30 13:46:45 +0800`.  After login the production
JRS volume UUID and canonical DB identity were healthy, but the canonical
`weather-data-feed-jrs` permission host and every registered runtime session
were absent.  The user explicitly authorized restoration of the full topology,
including the two guarded live instances.  A strict manifest was saved on the
internal disk and the controller ran the bounded recovery with
`recover-jrs-context --apply --confirm-live --restore-manifest`.

The prospective host and the recreated canonical host both passed JRS write
and canonical DB read probes.  Controller reconciliation restored 33 desired
running sessions; the two user-paused dispute/court zero-notional instances
remained stopped.  The newly pinned market-books release initially failed
closed because its release checkout was missing the standard `.venv` bootstrap
symlink.  Adding the same operational-repo venv link used by the other managed
release checkouts allowed the registered launcher to start without changing
the pinned SHA or market-books policy.

### Impact and acceptance evidence

- The host booted at `05:46:45Z`; the canonical data-feed loop restarted at
  `05:53:31Z`.  The last completed pre-reboot market-book batch ended at
  `05:42:11Z` in degraded state, and the first recovered healthy batch ended at
  `05:55:43Z` with `2,090/2,090` books and zero failures.  The completion gap
  was `812s`; the reboot-to-healthy-book interval was `538s`.
- The last full strategy snapshot completed at about `05:29:46Z`.  The
  `05:42Z` attempt was interrupted by the reboot; the first post-recovery
  attempt at `05:54Z` was correctly archived as non-publishable partial because
  it preceded the first healthy market-book batch.  The first recovered full
  snapshot published at `06:05:08Z` with `1,007` records, `47` cities and `89`
  city-date pairs.  The two missing/non-publishable attempts remain PIT coverage
  gaps and are not relabelled as zero signals.  The recovered full epoch had
  zero BUY_YES and zero BUY_NO records.
- From the start of the recovery transaction (`05:52Z`) through acceptance,
  both live journals added zero order rows and the canonical fill cache added
  zero fill rows.  The fill coverage gate passed with `1,595/1,595` effective
  live fills, zero DB/cache ID delta, zero DB/fact cost delta, zero missing-order
  rows, zero over-order keys and zero unknown fee lineage.  The standalone
  authenticated open-order check failed during client initialization, so the
  current reserved/open-order count was not inferred from local submitted
  notional.
- Both guarded live summaries were fresh with `status=ok` and
  `live_enabled=true`; API `:8000` and FE `:5174` were listening.  Strict
  manifest comparison showed all 33 canonical sessions, healthy DB/storage
  identity, and canonical-refresh last exit `0`.

Production health remains `critical` for one pre-existing contract drift: the
dirty operational `production.yaml` expects execution-evidence
`enabled/schema_version/daily_budget_bytes`, while pinned market-books release
`bced50c33328a7b0d2d987a5d44b05d68d279b5f` publishes only the two semantics
fields.  The running collector itself is fresh and `status=ok`; dependents are
marked critical only by propagation from this contract mismatch.  The recovery
did not deploy the unrelated uncommitted execution-evidence implementation or
weaken the expected-health contract.  This event is `recovery improved`, not
root-cause elimination of the macOS TCC permission-host dependency.

Follow-up hardening from this reboot keeps the same safety boundary while
making the operator path deterministic.  The wrapper now auto-selects full
context recovery only when zero managed sessions are observed; when managed
sessions remain it requires a healthy canonical JRS write probe before using
reconcile, and otherwise fails closed for inspection or explicit recovery.  It
can also forward an explicit saved restore manifest.  The
controller performs a whole-plan release/bootstrap/health-contract preflight
before any canonical server mutation, treats a non-zero launcher with a live
target session as warming rather than immediate failure, and requires a fresh
post-start health artifact within a per-runtime grace period.  Guarded session
replacement now excludes only the explicitly classified controller,
reliability and canonical-refresh bounded sessions from peer-loss comparison;
persistent registered sessions remain fail-closed.  The explicit live
confirmation and prospective-host JRS probe are unchanged.

## 2026-09-04 Recurrent JRS EPERM and Core Carry Recovery

The canonical tmux permission host again lost external-volume access without a
host reboot.  Core Carry last completed a healthy cycle at
`2026-09-04T02:37:08Z`; its next readiness read and error publication failed
with `PermissionError: [Errno 1] Operation not permitted`.  The near-Core
controller independently failed its atomic readiness write.  This is another
recurrence of the macOS TCC/JRS permission-host incident, not a Core signal or
sizing change.

The user authorized full live restoration.  Two bounded
`recover-jrs-context --apply --confirm-live` transactions passed the
prospective and recreated-host JRS probes, restored the declared topology, and
kept `cross_no_v2_metar_v1` paused.  The second transaction exercised the
controller fix that injects the operational `WEATHER_PRODUCTION_CONFIG` into
every restored session and window.  Post-change strict manifest found zero
managed sessions without the binding; controller and data-feed semantic health
were healthy.  The remaining manifest warning is the pre-existing inventory of
unregistered release worktrees and is not a runtime failure.

Core Carry's first recovered full cycle completed at
`2026-09-04T11:11:56Z`, so the decision-evidence interruption was `8h34m48s`.
Twenty-four durable PIT snapshots inside that interval were replayed with the
deployed model: `0` ten-share taker would-orders and `0` near-Core five-share
maker candidates.  Missing snapshot intervals between the surrounding durable
artifacts were `02:34:05Z..05:29:01Z`, `09:23:33Z..10:15:01Z`, and
`10:15:01Z..11:11:39Z`; these remain coverage gaps and must not be relabelled as
zero signals.  Confirmed missed orders on replayable evidence: `0`; behavior in
the missing PIT intervals is unknown.

Recovery also exposed a restart-only execution hygiene defect.  While the
near-Core user channel was becoming ready, the old controller sent 73 repeated
cancel requests for the same LA order already exchange-cancelled at
`2026-09-03T21:48:09Z`; every response said the order was already cancelled or
matched.  There were no new Core order-journal rows, no new fill, and the
authenticated open-order set remained 23 orders with its newest order created
at `2026-09-03T09:39:14Z`.  Release
`501d299e6e4b2db489e394f1449d2cfaa2474a02` now persists explicit exchange
terminal evidence across controller restarts.  After deployment the historical
terminal order was loaded and the cancel journal did not grow.

Final live contract: Core Carry remains `10` taker shares plus the additive
near-Core `5` maker shares, both live and fresh; Cross NO remains explicitly
paused.  Recovery-controller hardening is commit `91186547`; the terminal-safe
Core release pin is commit `f3d22a4d`.  Classification remains
`recovery_improved`: this closes the recovery/config and repeated-cancel defects
but does not eliminate macOS TCC as an architectural dependency.

### Canonical refresh and cancel-tombstone follow-up

The same recovery window exposed a canonical order-state defect rather than a
new exchange action.  Order
`0x5282ad67f0feadf749b8eff56e24146adf04ad407376837007f2f6011eac3197`
was exchange-cancelled at `2026-09-03T21:48:09Z`; the strict append-only cancel
lifecycle was materialized at `2026-09-04T02:04:06Z`, but authenticated CLOB
lookup returns a tombstone after terminal deletion.  The generic fill sync
treated that tombstone as still open instead of joining the confirmed cancel
lifecycle.  Release `0af06655d6da0f3a005366e618d9afdd856efa9b` corrected the
join, distinguished lookup errors from successful tombstones, and made
`filled/cancelled/still_open` mutually exclusive.  The target has `0` fills,
`0` fact-trade rows, `$0` actual cost and `$0` PnL; the defect changed canonical
state classification only, not exchange exposure.

Two independent control-plane blockers were then removed.  Commit `d8d9f255`
bounded canonical tmux probe timeouts so the reliability supervisor no longer
crashes on `TimeoutExpired`.  Commit `58c38fbd` keeps the host/Codex TAG route
maintainer visible as a warning while preventing its unrelated non-zero exit
from freezing the weather `7896 / PM-STABLE` canonical pipeline; weather
LaunchAgent failures remain critical.  Canonical-refresh release
`58c38fbd2ae4a4569ef4128248dbbb6e0ef84ef6` completed at
`2026-09-04T12:58:54Z` with exit `0`.  Its authenticated fill sync classified
`24` filled, `2` cancelled and `1` still-open order across `27` checked orders,
with `0` errors and `data_incomplete=false`.  Fill coverage reconciled
`1,645/1,645` distinct fill ids and `$6,180.716074/$6,180.716074` DB versus
`live_real` cost with zero id or cost delta.  The catch-up added one Busan
shadow candidate and no fill or fact-trade delta.

One attempted refresh observed a failed JRS write probe and exited without
writing.  The immediately repeated canonical-context probe succeeded, so the
permission host was not recreated and no runtime tree was migrated.  A missing
safe KNMI collector was restored through the registered controller contract;
post-change health returned to warning-only and the saved pre/post manifest
comparison found no lost canonical session.  This follow-up is classified
`contained / recovery_improved`; it is not evidence that the recurrent macOS
TCC/JRS permission-host dependency has been eliminated.
