# Vendor and provider responses

As of 2026-08-29 17:08 UTC, fifteen messages have been sent. Two delivery
failures were observed: the NOAA contact embedded in the WIS2 METAR metadata
returned `550 unrecognized address`, and the FAA-listed URF domain had no usable
mail service. These are contact-maintenance failures, not provider rejections.
The WIS2 report was resent to NOAA's currently published WIS2 feedback/OpenWIS
contacts, the URF correction request was sent to the FAA Non-Federal Program,
and a NOAAPort receiver RFQ was sent to Novra. The authoritative evidence is each Gmail message ID in
`ACCESS_ATTEMPT_LEDGER.csv`; replies are appended here without rewriting the
original request state.

The NOAA delivery-status evidence reported the recipient administrator response
`550 5.7.1 unrecognized address`; therefore `ncep.pmb.dm@noaa.gov` is closed as
an invalid contact and will not be retried.

The commercial sweep has researched 17 relevant vendor/provider paths and made
eight direct commercial contacts.  This satisfies the *contact action* floor,
not the live-trial or acceptance floor.

## METAR.ws — 2026-08-28 15:36 UTC

The provider states that U.S. METAR is received from multiple sources, mainly
the NOAAPort satellite. It offered either a seven-day Starter trial limited to
10 stations (`EUR 49/month` list price) or a 72-hour Professional trial with all
stations and D-ATIS (`EUR 199/month` list price); both deliver METAR by
WebSocket. This is a credible access offer, not a measured lead.

A reply requested protocol frames, sample METAR/D-ATIS messages, COR/SPECI
retention, upstream lineage, timestamp definitions, reconnect semantics and
limits. The 72-hour trial was selected in principle but explicitly deferred
until the collector is ready, so its clock has not started.

At 15:41 UTC the provider supplied its current documentation and confirmed that
the trial can be started on request. The implemented collector now subscribes
to `metar.obs.*`, `metar.obs10.*`, and the documented D-ATIS station channels,
captures each frame before normalization, and keeps cached replay out of the
latency denominator. The feed remains **unverified** until an API key is issued
and the explicitly time-limited trial is activated.

At 2026-08-29 17:08 UTC, after explicit user authorization, a reply requested
immediate activation of the zero-cost 72-hour Professional trial for
`jiaruihzcn@gmail.com`. The reply explicitly disallowed paid conversion,
renewal, subscription, or charge and requested UTC activation/expiry times plus
secure console delivery of the API key. Gmail evidence:
`gmail:1a04e7e35353d290`. This is an activation request, not evidence that the
trial or stream is active.

At 2026-08-29 17:11 UTC the provider replied that no platform account exists
for the project email and that API keys are self-service in the Console. Gmail
evidence: `gmail:1a04e81933d6f5c3`. The current official documentation also
resolves the free-plan ambiguity: Sandbox carries only a fixed demo channel and
basic model forecasts; measured `metar.obs.*`, `metar.obs10.*`, and
`metar.atis.*` are permission-denied. Sandbox can therefore validate protocol
handling, but not data lead. A real-data experiment requires the zero-cost
Starter or Professional trial. Human credential entry and creation of the first
persistent API key remain pending; neither has occurred.

At 2026-08-29 17:39 UTC the user completed registration and reported creating
an API key. That first key was disclosed in chat and is therefore treated as
compromised; its value was not copied into any command, Git file, log, raw
payload, or evidence record, and rotation is required before collection. A
follow-up told the provider the account is ready and requested immediate
activation of the promised zero-cost 72-hour Professional grant, explicitly
including all stations, D-ATIS and U.S. high-frequency observations and
excluding payment, automatic conversion, renewal or charge. Gmail evidence:
`gmail:1a04e9a764b700d0`. The self-service Starter button was intentionally not
used because the live Billing page states that Starter excludes ATIS alerts and
5–10 minute sub-hourly observations.

At 2026-08-29 17:55 UTC a read-only account-status inspection found the Console
still on the one-time plaintext display for the newly created replacement key.
The browser inspection output therefore disclosed that second key to the local
tool transcript. Its value is deliberately omitted here and must not be used.
Both disclosed keys require revocation; a third key must be created and pasted
only into the non-echoing local shell prompt. No key was used, copied into a
command, committed, emailed, or written into benchmark evidence, and no plan or
billing action was taken.

At 2026-08-29 18:04 UTC the user reported creating the third key and exporting
it through the non-echoing local zsh prompt. The key value was not visible to
the collector process controlled by Codex and is not recorded here. A bounded
commercial-only canary was prepared so the user can launch it from that same
shell without duplicating the still-active WIS2/AWC r2 run.

## Synoptic Push — 2026-08-28 15:41 UTC

The provider asked whether an account already exists and which email address
should receive the trial. Historical inbox evidence confirms an existing
account for the project Gmail address. The Push collector is implemented with
session resume, secret-scrubbed evidence endpoints, raw-frame retention, and a
distinct high-frequency temperature event class. Trial activation was not sent:
the connector correctly required explicit user authorization because this is a
one-time account/terms action. No latency claim is made.

At 2026-08-29 17:08 UTC, after explicit user authorization, a reply requested
immediate activation of the zero-cost 14-day Weather API + Push Streaming trial
for the existing `jiaruihzcn@gmail.com` account. It explicitly disallowed any
paid contract, automatic conversion, renewal, or charge and requested UTC
activation/expiry confirmation. The token is to remain in Synoptic's Data
Credentials console and must not be sent by email or written to evidence.
Gmail evidence: `gmail:1a04e7e4412277ee`. Activation is still **unverified**
until the provider/account state confirms it.

## NSF Unidata — 2026-08-28 15:38 UTC

Ticket `YMO-1566450` is open in the IDD department. The acknowledgement contains
no upstream decision yet.

## NOAA WIS2 — 2026-08-28 15:33 UTC

The current web-feedback address acknowledged receipt and says it attempts a
response within seven business days. This is not yet a technical answer.

## FAA Non-Federal Program — 2026-08-28 17:40 UTC

FAA independently reproduced the bounce from the WMSCR list's URF address,
called the listed telephone number, received no answer, and left a message. FAA
referred the substantive downstream receive-path question to
`Stewart.Stepney@faa.gov`. A focused follow-up was sent at 18:41 UTC asking
whether WMSCR has an external consumer interface, which current FAA or
commercial service carries the reports downstream, and whether listed WMSCR
intermediaries are inbound-only. This is an authoritative referral, not yet an
access answer.

## Public 24-hour capture — 2026-08-28 16:04 UTC

Run `run_1787934417858506000_f1909926` stopped receiving evidence after its
owning exec session disappeared and was sealed as an orphaned partial run rather
than represented as 24 hours. Its immutable evidence contains 3,281 transports,
65,589 source-seen rows, 62/62 stable replay identities, 60 invalid-clock pairs,
nine bounded AWC errors and zero WIS2 notifications. Partial reports are under
`runtime/us_fast_weather_lab_24h_20260829/partial_reports/`.

A non-overlapping persistent retry started at 18:42 UTC as
`run_1787942529236808000_1daa49aa` in
`runtime/us_fast_weather_lab_24h_20260829_r1/`. Initial integrity is `ok`, WIS2
again has 12 successful TLS connections and 11 accepted subscriptions, and AWC
is writing raw evidence. The initial clock offset was +49.624 ms, still above
the 20 ms contract, so ranking remains gated.

At 22:41 UTC the r1 health audit found a real collector defect: after repeated
TLS transport failures the long-lived HTTPX pool reached persistent
`PoolTimeout`, and AWC had produced no successful transport for 12 minutes even
though the clock thread was alive. The run was stopped cleanly and sealed as a
partial failure with 6,805 transports, 136,057 source-seen rows, 110/110 replay
identity, 197 AWC errors, zero clock-valid rows and zero WIS2 notifications. It
does not satisfy the 24-hour gate.

The root fix now replaces and closes the HTTP client after any transport-layer
failure, records each pool reset append-only, and preserves the same request
cadence/timestamp contract. Independent review found no implementation defect
and one P2 recovery-test gap; the test now covers two consecutive poisoned
pools followed by a successful captured response. All 26 tests pass. A real
120-second canary completed 58 AWC API polls and two cache downloads with zero
errors and 20/20 replay. Corrected persistent r2
`run_1787957333862002000_630d16c2` started at 22:48 UTC without overlap.

At 2026-08-29 17:31 UTC, r2 remained active with SQLite integrity `ok`, no
`collector_run_end`, 32,859 AWC API transports, 1,109 full-cache transports,
1,114 clock probes (zero contract-valid), and zero WIS2 notifications. A
same-version exploratory comparison contained 463 AWC API/cache pairs: API lead
median 0.464 seconds and win rate 0.5464. Because every clock row is invalid and
the two paths share an AWC upstream, this is neither a publishable seconds-level
ranking nor evidence of alpha.
