---
name: pmm-scenario-generator
description: Generate synthetic scenarios for the dormant legacy PMM backtest framework from a fixed catalog or intent profile. Use only for PMM orderbook simulation; do not use it for the active weather/WCIR strategy, canonical facts, or production CLOB capture.
---

# PMM Scenario Generator

Use this skill when the user explicitly wants legacy PMM mock orderbook ticks. The PMM
framework is retained for possible reuse but is not the active weather strategy chain.

## Inputs

- `mode`:
  - `catalog`: read an existing catalog and generate all scenarios
  - `intent`: generate a temporary catalog from user intent, then output scenarios
- `out_dir`: output directory for generated scenario json files
- `seed`: random seed

### `catalog` mode params

- `catalog`: path to catalog json, default `src/strategies/pmm/backtest/case_catalog.json`
- `show_initial`: print initial state summary
- `show_sample`: print first tick for one scenario id

### `intent` mode params

- `base_zone`: `around_30 | around_50 | around_70`
- `market_regime`: `stable | oscillating | single_side_up | single_side_down | whipsaw`
- `liquidity`: `low | medium | high`
- `fill_expectation`: `mostly_fill | partial_fill | mostly_no_fill`
- `shock_event`: `none | spike_up | spike_down | spike_then_revert | drop_then_revert`
- `horizon_ticks`: scenario tick length
- `count`: number of scenarios to generate
- `show_initial`: print initial state summary

## Run

Catalog mode:

```bash
.venv/bin/python skills/pmm-scenario-generator/scripts/generate_scenarios.py \
  --mode catalog \
  --catalog src/strategies/pmm/backtest/case_catalog.json \
  --out-dir runtime/pmm/backtest/scenarios
```

Intent mode:

```bash
.venv/bin/python skills/pmm-scenario-generator/scripts/generate_scenarios.py \
  --mode intent \
  --base-zone around_30 \
  --market-regime oscillating \
  --liquidity medium \
  --fill-expectation partial_fill \
  --shock-event spike_then_revert \
  --horizon-ticks 600 \
  --count 10 \
  --out-dir runtime/pmm/backtest/scenarios_intent
```

## Output

- Scenario files under `out_dir`, each file includes:
  - `scenario_id`
  - `initial_state`
  - `strategy_overrides`
  - `ticks` with token orderbooks
- Plus generation summary in stdout.
