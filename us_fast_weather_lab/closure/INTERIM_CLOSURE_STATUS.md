# Full-closure execution status — 2026-08-29

This is an execution checkpoint, not a terminal disposition.

## Observed

- 20/20 target airports now have current FAA DCS ASOS/AWOS dial-in numbers;
  18 have a published D-ATIS phone and KPHX has a D-ATIS frequency.
- NOAA's current WIS2 node, broker and cache have valid TLS certificates. In a
  125-second probe, 12/14 MQTT clients connected and 11 subscriptions were
  accepted, but the exact METAR topics delivered zero notifications.
- The discovery metadata still advertises `metar_features`, but that collection
  returned 404 and the general notification collection had zero rows for the
  advertised METAR metadata ID. Other NOAA WIS2 notifications were current.
- AWC in the same window completed 61 API requests and three cache downloads,
  creating 1,280 source-seen rows with no HTTP errors; replay identity was 20/20.
- Thirteen real access/status emails were sent. Two addresses bounced: NOAA's
  WIS2 dataset metadata contact was unrecognized, and FAA's listed URF domain
  had no usable mail service. Current NOAA feedback/OpenWIS contacts and the FAA
  Non-Federal Program received replacement requests.
- The first long public capture `run_1787934417858506000_f1909926` lost its
  owning exec session after roughly two hours and was sealed as an orphaned
  partial run: 3,281 transports, 65,589 source-seen rows, 62/62 replay-stable
  observation versions, nine AWC errors and zero WIS2 notifications. It is not
  counted as the required 24-hour smoke.
- Persistent r1 `run_1787942529236808000_1daa49aa` exposed and quantified an
  HTTP pool root cause: after repeated transport failures AWC entered sustained
  `PoolTimeout` and stopped producing successes. It was stopped cleanly and
  sealed as a partial failure with 6,805 transports, 136,057 source-seen rows,
  110/110 replay, 197 AWC errors and zero WIS2 notifications.
- The collector now closes/replaces a poisoned HTTP pool after transport
  failures and records resets append-only. Independent review findings were
  fixed; 26/26 tests pass. A 120-second real canary produced 58 API polls, two
  cache downloads, zero errors and 20/20 replay.
- Corrected non-overlapping persistent r2 is running as
  `run_1787957333862002000_630d16c2`; initial SQLite integrity is `ok`, WIS2 has
  12 TLS connections and 11 accepted subscriptions, and AWC evidence advances.
- R2 later encountered an upstream/network TLS EOF burst producing a maximum
  56.816-second success gap. Every transport error caused a fresh pool reset,
  successful two-second polling resumed, and no persistent local pool
  saturation recurred. This gap remains in source-health evidence.
- FAA independently confirmed the listed URF email bounce and referred the
  downstream WMSCR access question to Stewart Stepney. The referred inquiry was
  sent; no downstream-access determination has arrived yet.
- Current METAR.ws and Synoptic WebSocket collectors are implemented and pass
  raw-retention, source-class, cached-replay exclusion and event parsing tests.
  At 2026-08-29 17:08 UTC, explicit zero-cost activation requests were sent for
  METAR.ws Professional (72 hours) and Synoptic Weather API + Push (14 days),
  both expressly excluding automatic paid conversion. Neither source has been
  measured because activation and credentials are not yet confirmed.
- Three independent network-time probes placed the Mac roughly +130 to +154 ms
  ahead, above the contract threshold. The run preserves these rows with
  `clock_valid=false`; they do not enter the latency ranking.

## Inferred

- The stale NOAA hostname in GDC was one collector failure, but fixing it did not
  produce a usable WIS2 METAR stream. The remaining failure is upstream/topic
  publication state, not merely local TLS configuration.
- An external developer can research IDD/LDM, but Unidata's own primary upstream
  is normally restricted to qualified U.S. `.edu` hosts; a community or paid
  authorized upstream is required for this project.

## Unverified

- Any same-METAR source lead meeting the 15-second/70%/500-pair contract.
- WMSCR intermediary downstream receive capability.
- Synoptic, METAR.ws, FAA SCDS and PeakMet trial latency.
- Phone/D-ATIS speech-to-actionable latency and agreement.

## Blocked on explicit human/external state

- Account/SAA/one-time trial/wallet/payment actions listed in
  `MANUAL_ACTION_QUEUE.md`.
- Provider replies, telephony account/spend cap and two external vantage hosts.
- Provider activation confirmation and locally exposed API credentials for
  Synoptic/METAR.ws; user authorization to request both free trials is complete.
- Administrator-authorized Mac clock correction; passwordless correction was
  attempted and correctly failed without bypass.
