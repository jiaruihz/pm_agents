# WEATHER-RELEASE-ROOT-01 Handoff

TASK_ID: WEATHER-RELEASE-ROOT-01
PROJECT_ID: WEATHER
STATUS: READY_FOR_REVIEW
COMPLETED_AT: 2026-08-27T23:35:00+08:00
REPO_ROOT: /Users/deepsleep/projects/pm_agents

## Claims and evidence

1. **Release lifecycle is centralized.** `production.yaml` declares one
   `/Users/deepsleep/.local/share/pm_agents/releases` root and 23 exact
   `<release_id>/<expected_sha>` checkouts. Final strict manifest reports
   `status=healthy`, `findings=[]`, 23 declared release checkouts and no
   missing pre-change session.
2. **Production chain is restored.** Controller health reports
   `status=healthy`, `manifest_status=healthy`, `critical_runtimes=[]`, JRS
   context healthy and data-feed semantics healthy. All 31 release-backed
   desired-running runtimes use the new root; two dispute runtimes remain
   intentionally paused and the JRS keeper is unchanged.
3. **State identity is unchanged.** All 23 release `runtime/weather.db` routes
   resolve to `/Volumes/jrs/pm_agents/runtime/weather.db`. Storage audit is
   healthy with zero findings and the canonical/compatibility DB inode is
   `54444` on device `16777244`.
4. **Trading state is unchanged by cutover.** Fill coverage passes with 1,567
   fill IDs, 1,116 executions, cost `$5590.906674`, zero DB/cache/fact delta,
   zero missing-order and over-order rows. Authenticated balance stayed
   `162513747`; the exact 32-open-order payload remained unchanged with SHA-256
   `5cf2cabe1961371ec438120f6a3c1d78a033f67e61d03e4b03bb5119f3da7542`.
5. **Legacy data was retained before cleanup.** Unique feature stores were
   verified source-to-target and archived at
   `/Volumes/jrs-archive/pm_agents/research/artifact_store/release_root_legacy/20260827/`.
   Archive manifest records core `5,964 files / 1,979,577,803 bytes` and
   strategy `1,440 files / 358,001,459 bytes`, with their tree hashes.
6. **Migration transients were contained.** A zero-byte checkout-local DB and
   a checkout-relative fill-cache default were found and fixed. The affected
   core window had `entry_plans=0`, `live_orders=0`, and
   `signal_status=already_processed`: extra orders 0, missed orders 0, fills 0,
   canonical pollution 0. The canonical refresh LaunchAgent now exits 0.

## Verification

- Focused production manifest/controller/tmux tests: `99 passed`.
- Task protocol: `PASS: 3 projects; 1 tasks`.
- Durable incident/impact record:
  `docs/WEATHER_JRS_RUNTIME_INCIDENTS.md`.
- Durable archive inventory:
  `/Volumes/jrs-archive/pm_agents/research/artifact_store/release_root_legacy/20260827/migration_manifest.json`.
- Temporary command evidence for this host session:
  `/private/tmp/weather-release-root-prechange.XcIqym/`,
  `/private/tmp/weather-release-final-manifest.json`,
  `/private/tmp/weather-release-final-storage.json`,
  `/private/tmp/weather-release-final-fill-gate.json`, and
  `/private/tmp/weather-release-final-open-orders.txt`.

## Review disposition

Independent read-only review found two P1 path-lifecycle gaps: old WCIR/KNMI
fallbacks and failure to scan stale SHA worktrees under the managed root. Both
were corrected and covered by focused tests. No strategy parameter, sizing,
mode, cap, JRS raw root or canonical DB identity changed. Owner acceptance is
still required to move this task from `READY_FOR_REVIEW` to `ACCEPTED`.
