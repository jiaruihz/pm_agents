# Helsinki city-probability zero-notional shadow v1

## Status

`deployed zero-notional shadow / forward evidence collecting / no live change`

This is the forward evidence chain for the frozen Helsinki remaining-heat market
expression. It evaluates both the incumbent offset-fade artifact and the research
challenger on every newly archived FMI/current-bracket book checkpoint. It records all
evaluations, including negative-edge and dust-price rows; the first positive edge per
`city × target_date × bracket × model` is recorded as a paper intent with zero shares
and zero notional.

The runtime is deliberately model-agnostic. `ShadowRuntime` owns deduplication, official
weather fee treatment, signal/evidence journals and the zero-order invariant. A city
adapter owns source clock, settlement lattice, physical features and artifact inference.
Adding another city therefore requires a new adapter/profile, not another execution
chain; model features are not forced to be identical across cities.

## Helsinki inputs and known parity gap

- FMI airport observations: 10-minute first-seen journal.
- EFHK METAR: settlement-facing running maximum and current official state.
- Forecast: collector-exact ECMWF hourly curve available before the decision.
- Market: continuous active-ladder current-bracket NO direct CLOB book.
- Frozen artifacts: v5 coherent weather hazard, fade morphology, incumbent
  `offset_fade_v1`, and challenger `market_expression_v2`; every artifact is SHA-locked.
- The current FMI live collector does not provide radiation. The runtime records
  `global_radiation_slope_30m` as missing and reports expression feature coverage instead
  of claiming full feature parity. Frozen median handling remains unchanged.

## First smoke checkpoint

At the 2026-07-31 12:20 UTC FMI checkpoint, the current official bracket was 26°C and
the direct 26-NO book was 0.001/0.008. The incumbent/challenger estimated 0.0034% and
0.1447%; after the official fee both edges were negative, so neither produced a paper
intent. Two evaluations were written, zero orders were submitted, and the unit suite
passed 3/3.

Promotion remains governed by the frozen-forward requirements in the Helsinki model
reports. Shadow collection does not authorize live trading.
