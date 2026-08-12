# Tmin cross previous-NO event contract correction

Status: production correction applied; strict clean forward restarted; no live orders
Incident window: legacy journal target dates 2026-07-15 through 2026-08-12
Production correction time: 2026-08-12 02:46 UTC

## Conclusion

The previous `lowest_10m` event producer did not prove a sequential source
bracket transition. It treated the first available running-minimum state as if
the source had crossed from the adjacent warmer bracket. Event creation also
waited for independently discovered market context, so source clocks could be
delayed and true transitions could be missed.

The legacy 51-candidate journal is therefore a mixed-semantics development
artifact, not a strict-cross denominator. Its settlement/ROI slices must not be
used to promote `tmin_cross_prev_no_shadow_v1`.

## Fixed contract

- Source history now emits only explicit `source_transition_kind=strict_cross`;
  `initial_state` remains a coverage state and cannot enter the signal funnel.
- Every sequential running-extreme bracket transition is recoverable from raw
  history; same-minute Seoul runway rows are collapsed to the configured
  preferred runway before transitions are derived.
- Event persistence no longer depends on market availability. Market identity
  and quote evidence may be a later coverage layer.
- The source-event shadow consumes canonical
  `market_books/latest.json`; it no longer rediscovers markets through Gamma or
  makes duplicate CLOB book requests.
- The Tmin consumer requires the explicit strict-cross contract. The old 51
  candidates were preserved under
  `output/tmin_cross_prev_no_shadow_v1/legacy_mixed_semantics_20260812T0246Z/`;
  the active journal restarted at zero candidates.

## Quantified impact

The raw legacy journal contains 51 candidates across 17 target dates. Dated
high-frequency raw can reconstruct 21 of those candidates across 10 dates
(`2026-07-22..28`, `2026-08-10..12`):

- 6 legacy candidates are confirmed strict crosses;
- 15 legacy candidates are not strict crosses;
- 5 strict crosses were present in raw but absent from the legacy journal;
- the remaining 30 legacy candidates across 7 earlier dates cannot be honestly
  reclassified because the corresponding high-frequency raw is unavailable.

Legacy rows confirmed not strict:

```text
2026-07-22 Seoul 23
2026-07-23 Seoul 25
2026-07-24 Seoul 26, 27
2026-07-25 Seoul 26
2026-07-26 Seoul 26, 27
2026-07-27 Seoul 26, 27
2026-07-28 Seoul 26
2026-08-10 Seoul 24; Tokyo 26
2026-08-11 Seoul 26; Tokyo 24
2026-08-12 Seoul 22
```

Strict raw crosses missed by the legacy journal:

```text
2026-07-22 Tokyo 29
2026-07-25 Tokyo 25, 26
2026-07-26 Tokyo 25, 26
```

For the overnight deployment window specifically, the old two candidates
become one confirmed strict cross (`Tokyo 24 -> 23`, expression `24 NO`) and
one excluded initial state (`Seoul 22`).

There were zero selected candidates, TradeIntents, orders, fills, venue writes,
or notional impact in the affected runtime. `fact_signal_candidates` contained
zero rows for this strategy at correction time, so canonical facts were not
polluted.

## Production evidence

- code commits: `b8376888`, `0754a36c`, `1d2a1bda`
- loaded `fast_observation` SHA: `1d2a1bda1c8b7ce1d25679d9e2cb451f136672cd`
- focused tests: 21 passed
- production manifest: healthy, no findings, no pre-existing session lost
- source-event state after correction: one active Tokyo strict event, Seoul
  initial state removed; market context source is `canonical_market_books`
- active Tmin consumer after clean reset: 0 candidates, 0 intents/orders/fills

The next decision point is based only on new strict-contract target dates. The
legacy cap90 and settlement slices remain historical exploratory evidence and
are not a frozen-forward promotion basis.
