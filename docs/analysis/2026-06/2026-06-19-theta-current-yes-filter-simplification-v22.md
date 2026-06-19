# Current-YES Filter Simplification v22

Date: 2026-06-19
Status: deployed change candidate

## Decision

The current-YES tiny-live runner no longer treats forecast peak clock, forecast
max gap, cloud clearing, warming trend, local hour, or snapshot top-of-book
notional as pre-model hard filters.

Those fields remain in forward telemetry and model context. They should be
learned or scored by the probability layer instead of blocking live plans before
the model/quote decision.

## Kept Hard Vetoes

- fixed live risk caps: `$5` per order and `$5` per city-day per instance;
- stale snapshot and stale METAR checks;
- pre-METAR-update blackout;
- market/date/station availability checks;
- `peak_forming_micro` fresh-running-max wait;
- fresh CLOB taker cushion;
- cumulative executable ask depth inside the taker limit.

## Removed As Hard Filters

- missing forecast peak;
- forecast peak still ahead;
- current running max above forecast max;
- cloud-clearing METAR pattern;
- still-warming 3-hour METAR trend;
- local hour outside the former `13..15` window;
- snapshot available notional below the order size.

Fresh-book depth still matters at execution time: the runner now sums all ask
levels at or below the taker limit, rather than requiring the first ask level to
cover the whole order.

## Rationale

The removed fields describe reheat risk, but they are not reliable enough as
binary production vetoes. Keeping them as hard filters caused the live runner to
emit zero plans even when the market/model layer should have been allowed to
price the risk.

The remaining vetoes are operational constraints: they prevent stale data,
METAR update-window accidents, duplicate exposure, and uncontrolled taker
slippage.
