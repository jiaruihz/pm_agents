# Reheat Risk Model

Status: current-reference
Created: 2026-06-16

Use `reheat_risk` for the shared physical model, and use strategy-specific
names for trade expressions.

## One Shared Model, Multiple Expressions

The shared question is:

```text
Given the current intraday running max, will the target day reheat enough to
change the final winning bracket?
```

The base output should be a path/risk distribution, not a trade by itself:

```text
p_reheat_ge_0_5c
p_reheat_ge_1c
p_current_bracket_holds
p_target_bracket_hit
```

This model should use one shared intraday fact layer: METAR/current observation,
running max, decline from max, forecast peak clock, dew point/RH, wind, sky,
solar/local time, orderbook quote, snapshot age, and final settlement label.

## Current Shared Feature Layer

`reheat_feature_factory_v1` is the maintained first shared materializer for this
branch:

- Script: `scripts/analysis/reheat_risk/research_reheat_feature_factory_v1.py`
- Report: `docs/analysis/2026-06/2026-06-16-reheat-feature-factory-v1.md`
- Coverage CSV: `docs/analysis/2026-06/generated/reheat_feature_factory_v1/coverage_by_date_city_hour.csv`

Current row grain:

```text
city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket + outcome
```

Current conclusion:

- The shared layer is good enough to support downstream `current_yes_peak_forming`,
  `current_yes_fade_confirmed`, `higher_no_carry`, and
  `low_price_yes_reheat_reversal` research without each strategy rebuilding its
  own observed-max/orderbook/settlement facts.
- It materialized 88,621 feature rows and 8,696 date/city/hour state rows on the
  2026-05-19..2026-06-14 replay window.
- Observed path, METAR dewpoint/RH/wind/temp-trend, current YES, d1/d2 NO,
  target YES, and settlement labels are usable.
- Forecast peak fields are still the blocking gap: `forecast_peak_hour_local`,
  `forecast_peak_delta_hours_local`, and `forecast_values_hash` exist in the
  schema but were 0% populated in the current DB snapshot used by v1. Do not
  treat forecast-peak-clock variants as backtestable until upstream fact
  population or a documented backfill fixes this.

## Strategy Heads

### `current_yes_peak_forming`

Trade: buy current running-max bracket YES while the temperature is still at or
near the high.

Question:

```text
Is the current bracket already forming the final winning high?
```

This is the earlier, more aggressive no-reheat expression. It can catch a better
price before visible fade, but it needs stronger evidence that the remaining
heating path is exhausted.

### `current_yes_fade_confirmed`

Trade: buy current running-max bracket YES after the temperature has already
fallen from the high.

Question:

```text
After visible fade, is the current bracket now stable enough to buy?
```

This is the later, more conservative no-reheat expression. The price may already
have repriced upward, so the edge is more execution-sensitive.

### `higher_no_carry`

Trade: buy NO on higher brackets above the current running max.

Question:

```text
Are higher brackets still overpriced relative to reheat risk?
```

This shares the same no-reheat base model as current YES, but the payoff is not
identical. A d1 NO can still win when the final max skips over the adjacent
bracket, while current YES only wins when the final bracket equals the current
running-max bracket. Therefore it belongs in the same thesis family, not as a
separate alpha source.

### `low_price_yes_reheat_reversal`

Trade: buy a low-price YES target bracket that requires later reheating.

Question:

```text
Will the day reheat into this target bracket?
```

This is the opposite side of the same physical base. It should reuse
`reheat_risk` features, but it needs a separate label and model head because the
target is `target_yes_wins`, not `current_bracket_holds`.

## Project Structure

Preferred structure:

```text
shared layer:
  reheat_risk_features
  reheat_risk_model

strategy heads:
  current_yes_peak_forming
  current_yes_fade_confirmed
  higher_no_carry
  low_price_yes_reheat_reversal

expression layer:
  compare current YES vs d1/d2 NO vs low-price YES on the same city-hour state
```

Do not let each strategy materialize its own observed-max/orderbook/settlement
facts. They should share the same row grain, then branch only at label, model
head, and payoff/EV calculation.

New scripts should live under:

```text
scripts/analysis/reheat_risk/
```

Historical scripts under `scripts/analysis/observed_max/` remain archival until
they are intentionally migrated. Do not move old scripts only to rename them;
move a script only when it becomes the maintained entrypoint for a new result.

## Research Queue

1. `reheat_feature_factory`: materialize one shared city-hour/bracket fact layer.
2. `current_yes_peak_forming`: buy current YES before visible fade.
3. `current_yes_fade_confirmed`: buy current YES after visible fade.
4. `higher_no_carry_expression`: compare current YES vs d1/d2 NO payoff.
5. `low_price_yes_reheat_reversal`: use the same physical base for the opposite
   reheat/convexity expression.
6. `execution_freshness_gate`: require fresh CLOB ask before taker conversion.

## Naming Rules

- New reports, scripts, modules, strategy instances, and docs should use
  `reheat_risk` or a strategy-head name.
- Historical files with old observed-max names remain archival evidence only.
- Current reports should say `reheat risk`, `no-reheat`, `current YES`, `higher
  NO carry`, or `reheat reversal`, depending on the actual target metric.
