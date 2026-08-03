# Weather JRS Runtime Incident Ledger

Status: current-source
Updated: 2026-08-04
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

Reboot/login has not been exercised. Therefore the accepted wording is
`entrypoint race eliminated; recovery improved and production-validated`, not
`resolved permanently` or `一劳永逸`.
