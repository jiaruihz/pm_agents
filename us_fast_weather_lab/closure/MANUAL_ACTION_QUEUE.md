# Manual action queue

Only actions that require identity, credentials, agreements, payment, or a
user-owned external resource are queued here. Public research and bounded probes
continue without waiting for these items.

1. **FAA SWIFT/SCDS** — sign in or create the public account at
   `https://portal.swim.faa.gov/`, review/sign the current SCDS Service Access
   Agreement, then export the current catalog and request **Common Support
   Services Weather (CSS-Wx)**. This creates a regulated account and annual SAA.
2. **Synoptic** — user authorization is complete and the zero-cost 14-day
   Weather API + Push Streaming activation request was sent at
   2026-08-29 17:08 UTC with no paid conversion authorization. Await provider
   confirmation, then expose the existing Data Credentials token to the
   collector as `SYNOPTIC_API_TOKEN`; never paste it into chat, Git, a report,
   email, or an evidence payload.
3. **PeakMet** — create/login to a normal account, review its terms, then use the
   advertised Telegram free trial or member path. Wallet binding, payment and
   API-key creation remain user-controlled. Capture must run for seven days from
   the browser-visible receipt boundary.
4. **METAR.ws** — registration is complete. The first API key was disclosed in
   chat and must be revoked; do not use it. Create one replacement key, keep it
   out of chat/Git/email/reports/raw evidence, and inject it only through the
   prepared non-echoing local prompt as `METAR_WS_API_KEY`. A follow-up at
   2026-08-29 17:39 UTC requested the promised zero-cost 72-hour Professional
   all-station+D-ATIS grant with no paid conversion. Do not start the
   self-service Starter trial because it excludes ATIS and sub-hourly data.
5. **Direct ASOS/D-ATIS calls** — provide an approved Twilio/SIP/telephony
   account and a hard spend cap. The 20-airport official dial-in inventory is
   ready in `config/airport_direct_sensor_inventory.csv`.
6. **IDD/LDM** — only after an upstream accepts a non-academic leaf, provision a
   stable public Linux FQDN with reverse DNS and inbound LDM port. Do not probe
   an upstream before its `ALLOW` authorization.
7. **Second vantage** — provide US-East and SG/MY hosts or cloud-budget approval.
   Both must deploy the same commit/config hash before the final bakeoff.
8. **Mac clock** — the current public run is approximately +130 ms from sampled
   network time and therefore invalid for second-level ranking. In macOS Date &
   Time, re-enable automatic time, or run `sudo sntp -sS pool.ntp.org` in a local
   terminal and enter the administrator password. Collection continues safely,
   but only later clock-valid samples may enter the ranking.
