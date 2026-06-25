# Post-Cross Repricing

Status: current-reference
Updated: 2026-06-25
Source of truth: yes for the new post-cross repricing research task
Used by: `WEATHER_LATENCY_ARB_OBSERVATION_MICROSTRUCTURE.md`, `market_structure_edge.md`

## Question

After an official/live-eligible observation first pushes a city running max from
`T-1` to `T`, how does the market reprice nearby temperature brackets?

This is separate from the low-latency `T-1 NO` taker bot. The crossed bracket is
nearly deterministic and highly latency-sensitive. The research target here is
the less deterministic repricing surface around it:

- `T-1` bracket: just invalidated; should collapse.
- `T` bracket: current new high; may be under- or over-adjusted.
- `T+1` / `T+2`: tail continuation; may lag the new information.

## Current Hypothesis

There may be retail-accessible space in the **probability redistribution after a
fresh running-max update**, even if the pure crossed-NO trade is too latency
competitive.

Candidate edges:

1. **Underreaction on current `T YES`.** The city just reached `T`, but market
   has not raised `T YES` enough given remaining heat is low.
2. **Overreaction on current `T YES`.** Market spikes `T YES` after the fresh
   print, but the day still has strong runway to break `T+1`.
3. **Underreaction on `T+1 YES`.** Market kills `T-1` and raises `T`, but does
   not update continuation probability enough.

This is not risk-free. It must be evaluated as a probability/market-structure
strategy with real executable prices and forward validation.

## First Observations

Small N100 orderbook-log samples around 2026-06-25 crossings showed:

| Event | Crossed Bracket | Current / Tail Reaction |
|---|---|---|
| Manila 31 -> 32C | `31C YES` 0.115 -> 0.010 quickly | `32C YES` stayed around 0.485; `33C YES` moved only slightly. |
| Singapore 30 -> 31C | `30C YES` 0.191 -> 0.003 quickly | `31C YES` 0.485 -> 0.775; `32C YES` mostly stable. |
| Paris 39 -> 40C | `39C YES` 0.064 -> near zero | `40C YES` 0.450 -> 0.555; `41C YES` mostly stable. |
| BuenosAires 8 -> 9C | `8C NO` remained buyable for minutes | current/tail repricing was muted. |

Broad rough scan over recent crossing events:

- crossed/previous bracket YES generally falls;
- current bracket median YES change over 30-300s was near zero;
- mean current-bracket YES change was positive because a minority of events
  repriced sharply upward;
- examples of large current-bracket repricing included Singapore, Paris, Busan,
  Wuhan, Milan, Guangzhou, Warsaw, and Beijing;
- examples of muted or negative current-bracket repricing included Busan early
  ladder steps, Shanghai, CapeTown, Warsaw, and some low-probability tail states.

The shape is heterogeneous, which is exactly why this deserves a separate
research track instead of a hard rule.

## Evidence Grain

Primary raw inputs:

- `runtime/weather_edge_v1/metar_cross_prev_no_shadow/opportunities.jsonl`
- `runtime/weather_edge_v1/source_orderbook_timing/books.jsonl`

Durable analysis script:

```bash
.venv/bin/python scripts/analysis/market_structure_edge/research_post_cross_repricing_v0.py \
  --since 2026-06-24T23:00:00+00:00 \
  --output-json docs/analysis/2026-06/generated/post_cross_repricing_v0/post_cross_repricing_v0.json \
  --output-md docs/analysis/2026-06/generated/post_cross_repricing_v0/post_cross_repricing_v0.md
```

Run this on the machine that has fresh timing logs. Today that is N100, not the
stale Mac mirror.

## Required Denominator

One row per:

```text
event_slug + source_report_ts_utc + city + relative_bracket
```

where `relative_bracket` is:

- `prev_or_cross`
- `current`
- `next1`
- `next2`

For each row, record:

- report timestamp
- local crossing detection timestamp
- source lag
- label / bracket
- YES mid and NO ask before report
- YES mid and NO ask at fixed horizons after report, initially 30s, 90s, 180s, 300s
- executable ask/size when strategy action would occur

## Research Gates

Before any live consideration:

1. Use real YES/NO books. Do not infer NO price as `1 - yes_ask`.
2. Use time-aligned orderbook snapshots; executable pricing must satisfy
   `snapshot_ts_utc <= decision_ts_utc`.
3. Separate labels:
   - current `T` survives to settlement
   - current `T` gets broken by `T+1`
   - `T+1` eventually wins
4. Compare against same-price baselines, not only raw ROI.
5. Validate forward by date; do not use same-day exploratory thresholds as live
   rules.

## First Research Tasks

1. Run `research_post_cross_repricing_v0.py` on current N100 logs and save the
   generated JSON/Markdown output.
2. Join repricing rows to final settlement once available.
3. Split events by market reaction type:
   - crossed bracket killed before our source
   - crossed bracket killed after report but before our detection
   - crossed bracket still tradable at our detection
4. For current `T YES`, test whether large upward repricing is overdone when:
   - there is still strong solar/runway remaining;
   - the previous report was already near `T`;
   - `T+1` tail did not reprice.
5. For `T+1 YES`, test whether continuation is underpriced after high-momentum
   crossings.
6. Keep the first output as `shadow/research`; no live action from this task
   until the standard significance, baseline, and forward gates pass.

## Current Verdict

`research_only`.

This is more promising than trying to beat every crossed-NO robot on speed, but
it is probabilistic and must be treated like a normal strategy research line.
