# U.S. airport fast-source full-closure execution v1

Status: `execution active / no terminal disposition / no-live-change`

## Current decision

No source has yet met the same-METAR acceptance contract, and PeakMet has not
yet been legitimately observed behind its member boundary. The work is no
longer only a plan: public transports were re-probed, the full direct-phone
inventory was built, commercial/provider RFIs were sent, and all external
account/payment steps were reduced to a precise manual queue.

Do not adopt a source or abandon the search yet. The next admissible decision
must be one of the full-closure dispositions after trials, replies, two-vantage
capture and the fixed pair count finish.

## Existing evidence retained

- Direct NOAA MADIS: 12,775 OMO observations; 1,522 exact-time NOAA↔IEM
  overlaps; 1,522/1,522 rounded-temperature agreement. Historical median
  NOAA received lag was 136.0 seconds versus 1,269.1 seconds in the old IEM
  collector.
- Mandatory negative control: Atlanta 2026-07-17 OMO printed 91.4F with
  `temperatureQCR=0`, while routine METAR/WU finished at 89F.
- Execution consequence: 22 correct U.S. runner cases had 0/22 executable
  opportunities within ten minutes; the false Atlanta event alone filled 15
  shares and lost $13.05655 including verified fee.
- Prior AWC/WIS2 smoke: 303.8 seconds, 149 raw transports, 21 same-semantic
  API/cache pairs and replay 21/21; all pairs were clock-invalid and therefore
  excluded from ranking.

## New public-path execution

### WIS2

The first smoke followed a stale NOAA hostname still returned by the Global
Discovery Catalogue. NOAA's current SCN/PNS instead names
`wis2broker.globaldata.nws.noaa.gov` and
`wis2cache.globaldata.nws.noaa.gov`; the dataset metadata also names the direct
node `wis2node.globaldata.nws.noaa.gov`. All three current names passed TLS
hostname verification.

The collector now preserves the stale discovery result and adds dated
authoritative endpoints rather than silently overwriting one with the other.
In the 125-second rerun, 12/14 origin/cache clients connected and 11
subscriptions were accepted across seven node/broker endpoints. The exact
METAR topic delivered zero notifications. AWC in the same window made 61 API
requests and three cache downloads without HTTP error, wrote 1,280 source-seen
rows and replayed 20/20 identities.

The upstream inconsistency is independently visible: current non-METAR NOAA
WIS2 notifications existed on 2026-08-28; filtering the notification collection
by the advertised METAR metadata ID returned zero; and the advertised
`metar_features` collection returned HTTP 404. This is now a NOAA publication
status question, not a local TLS workaround. The metadata contact itself
bounced with `550 unrecognized address`; the report was resent to NOAA's
currently published WIS2 feedback and OpenWIS contacts.

### Direct airport observations

FAA Digital Chart Supplement edition 2026-07-09 was parsed for all 20 target
airports. All 20 have a current ASOS/AWOS telephone number; 18 have a D-ATIS
telephone number, and KPHX exposes D-ATIS frequency 127.575. The exact inventory
is `us_fast_weather_lab/config/airport_direct_sensor_inventory.csv`.

Measurement is ready but not executed because a call account and hard spend
cap are user-controlled external resources. Phone/D-ATIS is a `DIRECT_SENSOR`
class and will be compared temporally to later METAR; it will never be raw-hash
paired as the same report.

### NOAAPort and IDD

NOAA SCN 24-99 establishes that SAUS99/SPUS99 publish METARs received during
each minute onto SBN for timelier delivery. Direct receipt requires a U.S.
C-band site and qualified DVB-S2 receiver. A real budgetary RFQ was sent to
Novra for an S300N, complete dish/feed/LNB/5G-filter BOM, U.S.-East install,
Linux tooling and hosted alternatives.

NSF Unidata documents NTEXT as the NOAAPort text feed that will replace
IDS|DDPLUS, but its primary IDD upstream normally feeds qualified U.S. `.edu`
hosts. A non-academic external developer needs an authorized community or paid
upstream and a stable public FQDN/reverse DNS. A legitimate path inquiry was
sent to Unidata; no upstream will be probed without `ALLOW` authorization.

### FAA SWIFT/SCDS

The current public NSRR lists production CSS-Wx JMS subscriptions, and FAA
describes SCDS as near-real-time public SWIM data over Solace JMS after a public
account and Service Access Agreement. Account creation, SAA signature and
subscription are correctly queued for the user; no queue/JNDI/service name was
guessed from old material.

## Commercial/provider sweep

The ledger contains 17 relevant commercial/provider paths, exceeding the
12-vendor research floor. Real RFIs/RFQs went to all five FAA-approved WMSCR
intermediaries, Synoptic Push, METAR.ws and Novra. Seven commercial messages have
not bounced; URF's FAA-listed domain did bounce and the FAA program office was
asked for the current contact. These actions meet the contact floor, not the
three-live-trial floor.

Synoptic officially offers a one-time 14-day Push Streaming trial and sends
observations within seconds of its own ingest. METAR.ws replied that its U.S.
METAR is mainly received from the NOAAPort satellite and offered a seven-day
Starter trial (10 stations; EUR49/month list price) or a 72-hour Professional
trial (all stations plus D-ATIS; EUR199/month list price), both by WebSocket.
The Professional trial was selected in principle but explicitly deferred until
the collector is ready; protocol and timestamp semantics were requested.
Neither claim is accepted until raw receipts are measured. AvioWeather
is already rejected as a fast-path hypothesis because its own documentation
says current data comes from a five-minute AWC snapshot.

## PeakMet adversarial fingerprint

Observed public claims are internally mixed: “fastest every 60 seconds” for
city/runway data, and “METAR within 50 seconds of the clock hour” for members,
with an explicit statement that airport on-the-hour/half-hour runway
observations may differ and may not use that METAR about 10% of the time. This
is not the same as 50 seconds after native observation creation or source
publication.

The public API/subscription pages returned 502 during this run. The normal
member path requires email account, acceptance of terms, wallet verification
and possibly payment; the linked Telegram preview showed 9 subscribers while
the link label described a 600-person group. These discrepancies justify a
measurement, not a marketing verdict. A legitimate seven-day member/browser
capture remains required.

## Evidence funnel

| Gate | Result |
|---|---:|
| target airports with current phone inventory | 20/20 |
| relevant commercial/provider paths researched | 17 |
| commercial RFI/RFQ sends | 8 |
| successful/non-bounced commercial sends so far | 7 |
| live commercial trials | 0/3 |
| current NOAA WIS2 TLS-valid endpoints | 3/3 |
| WIS2 accepted subscriptions in rerun | 11 |
| WIS2 METAR notifications in rerun | 0 |
| clock-valid same-METAR pairs | 0/500 |
| two-vantage final capture | 0/2 |
| PeakMet legitimate member days | 0/7 |

## Required next external actions

The precise user-owned steps are in
`us_fast_weather_lab/closure/MANUAL_ACTION_QUEUE.md`: FAA SAA, one-time
Synoptic trial activation, PeakMet member access, a telephony account/spend cap,
authorized LDM upstream/host and two vantage hosts. Provider replies and all
subsequent live-trial receipts append to the closure ledgers.

No order, position, plan, live strategy, production config or canonical weather
database was changed.

## Independent code review seal

Review prompt: perform one read-only pass over this change's owned scope for
correctness, boundary conditions, idempotent/append-only semantics, test gaps,
obvious performance issues and readability; do not edit files or delegate.

The reviewer found one P1 and no P0/P2/P3 findings: `command_replay` selected
the newest `collector_run` for the audit row but replayed every historical
`observation_event` in a reused database. The authoritative smoke database had
only one run, so its prior 20/20 result was not altered. The query now joins
`observation_event → source_observation_seen → transport_message` and filters
on the selected `run_id`; a two-run regression test proves that only the latest
run contributes to its audit.

Post-fix verification: 17 lab tests passed, package `compileall` passed,
authoritative SQLite `PRAGMA integrity_check` returned `ok`, the authoritative
run replay remained 20/20 with zero mismatches, and `git diff --check` passed.
The repository-wide document audit still fails on pre-existing
`AGENTS.md`/`CLAUDE.md` contract drift plus untracked dated analysis reports;
those unrelated user-owned files were not staged or rewritten. Reviewer usage
telemetry was unavailable from the agent runtime and is therefore not claimed.

## 2026-08-29 continued execution

The work continued beyond provider email. A real 24-hour public capture started
as `run_1787934417858506000_f1909926` under
`runtime/us_fast_weather_lab_24h_20260829/`. At the latest verification it had
525 immutable transport envelopes and 10,482 source-seen rows; SQLite integrity
was `ok`. AWC accounted for 507 API polls and 18 full-cache downloads without a
recorded HTTP error at that checkpoint. The accepted WIS2 METAR subscriptions
were still silent. All 10,482 observations were correctly `clock_valid=false`
because three independent network-time probes put the Mac about +130 to +154 ms
ahead. Raw collection continues; no invalid-clock row enters speed ranking.

METAR.ws supplied current protocol documentation and confirmed the offered
trial is ready on request. Synoptic confirmed the one-time trial path and asked
which existing account to activate. Credential-gated collectors now cover
METAR.ws official METAR, HF-METAR and D-ATIS channels plus Synoptic Push
high-frequency temperature. Cached replay is retained but non-latency. D-ATIS
is forced to `DATIS_DERIVED`, Synoptic minute temperature to
`HF_TEMPERATURE`, and neither can pair as an official METAR. Trial activation
and administrator time correction remain explicit user actions.

### Commercial collector independent review seal

Review prompt: one read-only pass over only the commercial collector, its CLI,
model, evidence/pairing changes, config, tests and directly related documents;
check credentials, append-only/raw-first semantics, cached replay, source-class
separation, reconnect/stop concurrency, replay identity and test gaps; do not
edit or delegate.

The reviewer reported no P0, one P1 and two P2 findings:

- P1: rawless `HF_TEMPERATURE` events were counted but skipped by replay. Replay
  now reconstructs every event from `normalized_fields_json`, reports raw and
  replayed counts separately, and fails unless all selected observations have
  stable identity.
- P2: an expired Synoptic resume session could be retried indefinitely. A
  failed resume now clears the session and forces a fresh authenticated URL on
  the following connection.
- P2: reconnect backoff could outlive `stop()` and race SQLite close. Backoff is
  now stop-interruptible; shutdown waits for the worker and fails explicitly if
  it cannot stop cleanly.

The P3 gaps were covered by new rawless replay, D-ATIS separation,
failed-resume fallback and interruptible-backoff regressions. Post-fix
verification is 25/25 tests passed in 0.83 s, `compileall` passed, owned-scope
`git diff --check` passed, and the live SQLite `PRAGMA integrity_check` returned
`ok`. Reviewer telemetry: one fresh read-only reviewer turn, one pytest run plus
compile/diff validations; exact token counters were not exposed and are not
claimed.

### 2026-08-29 AWC pool-exhaustion root-cause seal

Persistent r1 stopped producing successful AWC transports for 12 minutes while
clock probes continued. Evidence showed 97 SSL connect failures followed by
repeated `PoolTimeout`; a single HTTP client had remained alive across the
entire run. R1 was stopped cleanly and retained as a failed partial window:
6,805 transports, 136,057 source-seen rows, 110/110 replay-stable observation
versions, 197 AWC errors, zero valid-clock observations and zero WIS2 messages.

The fix closes and replaces the HTTP client after any `httpx.TransportError`,
uses an explicit bounded pool, records each reset append-only, and leaves poll
rate and first-byte timing unchanged. The independent read-only reviewer found
no P0/P1/P3 implementation issue and one P2 test gap: recovery after consecutive
poisoned clients was not proven. The regression now forces two sequential
`PoolTimeout` clients, verifies two close/reset records, then proves a fresh
client captures a valid response. Post-fix verification: 26/26 tests and
`compileall` pass. A 120-second real canary completed 58 AWC API requests, two
cache requests, zero errors and 20/20 replay. Corrected persistent r2 started as
`run_1787957333862002000_630d16c2`; no partial run is merged into its acceptance
window. Reviewer telemetry: one fresh read-only turn and one test command;
exact token counters were unavailable and are not claimed.
