# Busan fast-source 34 NO lineage review v1

Status: snapshot
Target date: 2026-07-11
Reviewed: 2026-07-12

## Conclusion

The Busan `34 NO` order was a false cross from a boundary print, not a confirmed
settlement-source cross. The runner arithmetic-rounded one AMOS runway-air
observation of `34.5 C` to `35`, compared it with the METAR running maximum of
`34`, and immediately bought the previous bracket (`34 NO`). AMOS never printed
above `34.5 C`; METAR and Weather.com history both finished with a daily maximum
of `34 C`.

The unsafe assumption was:

```text
AMOS decimal air temperature >= X.5  => next/final whole-degree METAR/WU >= X+1
```

That implication is not valid at the half-degree boundary. AMOS and METAR/WU
have different sampling times and reporting precision, and this case had no
margin above the boundary and no persistence confirmation before execution.

## Lineage

All times below are UTC; Busan local time is UTC+9.

| Time UTC | Local | Evidence |
|---|---:|---|
| 04:00 report | 13:00 | RKPK METAR printed `34 C`; running max became 34. |
| 04:40:28 detect | 13:40:28 | Weather.com history first exposed the 04:00 row as `34 C`; daily running max 34. |
| 04:46:00 obs | 13:46:00 | AMOS runway-air printed exactly `34.5 C`. |
| 04:46:01 book snapshot | 13:46:01 | `34 NO` book was bid/ask `0.32/0.39`. |
| 04:47:42 source detect | 13:47:42 | Trial runner saw the AMOS observation. |
| 04:47:46 decision | 13:47:46 | `arith_round(34.5)=35`; METAR max=34; selected `34 NO`. Fresh ask was `0.33`. |
| 04:47:47 order | 13:47:47 | FOK matched. Intended size 5; old FOK sizing bug produced `13.823528` shares for about `$4.60`. |
| 04:58 and 05:07 obs | 13:58 / 14:07 | AMOS printed `34.5 C` twice more, but never printed `34.6 C` or `35.0 C`. |
| 05:00 METAR | 14:00 | RKPK remained `34 C`. |
| 05:13 SPECI | 14:13 | RKPK remained `34 C`. |
| 06:00 / 07:00 METAR | 15:00 / 16:00 | RKPK remained `34 C`; 08:00 report fell to 33. |
| end of day | | Weather.com history daily running max remained `34 C`. |

## Source-path evidence

- Fast source: `amos_runway`, station RKPK, `source_kind=runway_air_temperature`.
  This is runway-level air temperature, not pavement temperature and not the
  settlement observation itself.
- Trigger observation: `34.5 C` at 04:46 UTC. It was only the first of three
  exact-boundary prints (`04:46`, `04:58`, `05:07`). There were zero observations
  at or above `34.6 C` and zero at or above `35.0 C`.
- Settlement-aligned paths: RKPK METAR and Weather.com history both recorded a
  maximum of `34 C`.
- Market disagreement was visible before entry: the fresh `34 NO` ask was
  approximately `0.33`, so the order paid to oppose a market that still assigned
  substantial probability to exact 34.

## Root cause and impact

Root cause: the generic runner uses `arith_round(temp_c)` as a deterministic
cross classifier for every source. That is too strong for AMOS at exactly `.5 C`.
The runner had no source-specific minimum margin, persistence requirement, or
calibrated probability that the next METAR/WU row would cross.

Affected live decisions on 2026-07-11:

1. Busan `33 NO`: separate earlier cross; not caused by the 34.5 boundary case.
2. Busan `34 NO`: incorrectly treated as confirmed cross. Correct classification
   was `boundary_cross_unconfirmed`; it should not have been live-eligible under
   the evidence available at 04:47 UTC.

The sizing bug amplified this bad decision from an intended 5 shares to an
actual `13.823528` shares. This report records the signal error separately from
that execution-size error.

## Mitigation deployed

The Busan AMOS live policy now requires both:

```text
two consecutive distinct-minute observations above METAR running max + 0.5 C
at least one of those observations above METAR running max + 0.7 C
```

An exact `.5 C` boundary print remains in opportunity telemetry with
`source_cross_margin_not_met`. A sequence such as `.6 C` followed by `.8 C`
confirms; `.6 C` followed by `.7 C` does not, because neither observation is
strictly above `.7 C`. Until both conditions hold, the row is recorded with
`source_cross_persistence_not_met`. Other city/source policies retain the
existing arithmetic-round behavior while more Busan episodes accumulate.

This is an interim conservative live policy. It must be reevaluated on
independent cross episodes after several additional collection days; repeated
polls of the same source observation do not increase the confirmation count.

Data completeness: raw order response, AMOS minute observations, RKPK METAR
events, Weather.com history rows, and the contemporaneous orderbook snapshot are
present. The local canonical settlement table did not yet contain the final
market settlement row at review time; the source-path result is independently
confirmed by the complete METAR sequence and Weather.com daily running max.
