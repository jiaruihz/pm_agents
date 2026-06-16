# Reheat Risk Analysis Module

Owner doc: `docs/analysis/reheat_risk.md`

This module is for new intraday observed-path research. Historical scripts under
`scripts/analysis/observed_max/` remain archival unless they are intentionally
migrated.

## Maintained Entrypoints

Current maintained scripts in this module:

- `research_m3_jump_model_v1.py` through `research_m3_jump_model_v3_bad_case_attribution.py`: shared jump / reheating risk modeling and quote calibration.
- `research_m3_exhaustion_source_aware_restart_v1.py`: bridge report that separates pure theta/reheat risk from station-basis effects.
- `research_theta_no_*.py`: higher-NO carry and sibling current-YES expression tests.
- `research_theta_yes_current_*.py` and `research_theta_current_yes_*.py`: current-YES replay, tiny-live gate, model accuracy, peak-clock, and execution freshness follow-ups.

Scripts that still import helper functions from `scripts/analysis/observed_max/`
must do so explicitly. Do not copy old observed-max helpers into this module
unless they become part of a shared reheat feature factory.

## Contract

Use this module when the target metric depends on today's observed path:

- current running max
- decline from max
- minutes since max
- forecast peak clock
- later reheat / no-reheat risk

Use explicit reheat-risk names in new script names. Prefer:

```text
research_reheat_feature_factory_v*.py
research_current_yes_peak_forming_v*.py
research_current_yes_fade_confirmed_v*.py
research_higher_no_carry_expression_v*.py
research_low_price_yes_reheat_reversal_v*.py
research_reheat_execution_freshness_v*.py
```

## Shared Row Grain

Preferred research grain:

```text
city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket/outcome
```

Every report should state whether the row is:

- city-hour state
- current-YES quote
- d1/d2 NO quote
- target low-price YES quote
- city-day aggregate

## First Work Items

1. Build a reusable reheat feature factory.
2. Compare `current_yes_peak_forming` vs `current_yes_fade_confirmed`.
3. Build a sibling expression selector for current YES / d1 NO / d2 NO.
4. Add fresh-book execution gates before any tiny-live expansion.
