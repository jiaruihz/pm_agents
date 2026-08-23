# Weather JRS Runtime Incident Ledger

Status: current-source
Updated: 2026-08-13
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
