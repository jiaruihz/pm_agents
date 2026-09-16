# PeakMet public-claim fingerprint — interim

## Observed

- The public page says independent airport/runway data, city temperature updates
  as fast as every 60 seconds, ±0.1°C display precision, API access, and a
  wallet-bound member session.
- The membership surface says “50秒内整点获取METAR”, but also says airport
  on-the-hour/half-hour runway observations may differ and may not use that
  report with a stated 10% probability.
- The page says free users see data delayed by 30 minutes and links to
  `t.me/peakmets` for a free trial. The linked Telegram preview showed 9
  subscribers on 2026-08-28, while the link label described a 600-person group.
- The public API and subscription pages returned HTTP 502 to independent fetches
  during this session. The cached login page requires email registration,
  acceptance of terms, then wallet verification for member capability.

## Inferred, not yet proven

- “50 seconds” appears scoped to arrival after the clock hour, not to 50 seconds
  after a native observation/report creation timestamp. Those are different
  latency claims.
- The runway/temperature product may be a direct-sensor class while METAR is a
  report-transport class. Cross-class display timing cannot prove a same-METAR
  lead.

## Still required before disposition

- Legitimate member/trial capture for seven days, including browser receipt time,
  page/API payload, station, report text, source timestamp semantics and paired
  AWC/WIS2/MADIS events.
- No marketing verdict is issued from the public page alone.
