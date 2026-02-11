---
name: pmm-scenario-generator
description: Generate synthetic PMM mock/backtest scenarios in batch from either a fixed catalog or an intent-driven profile (base zone, regime, liquidity, fill style, shock type).
---

# PMM Scenario Generator

Use this skill when the user wants to generate mock market data (orderbook ticks) for PMM strategy testing.

## Inputs

- `mode`:
  - `catalog`: read an existing catalog and generate all scenarios
  - `intent`: generate a temporary catalog from user intent, then output scenarios
- `out_dir`: output directory for generated scenario json files
- `seed`: random seed

### `catalog` mode params

- `catalog`: path to catalog json, default `pmm/backtest/case_catalog.json`
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
python skills/pmm-scenario-generator/scripts/generate_scenarios.py \
  --mode catalog \
  --catalog pmm/backtest/case_catalog.json \
  --out-dir pmm/backtest/scenarios
```

Intent mode:

```bash
python skills/pmm-scenario-generator/scripts/generate_scenarios.py \
  --mode intent \
  --base-zone around_30 \
  --market-regime oscillating \
  --liquidity medium \
  --fill-expectation partial_fill \
  --shock-event spike_then_revert \
  --horizon-ticks 600 \
  --count 10 \
  --out-dir pmm/backtest/scenarios_intent
```

## Output

- Scenario files under `out_dir`, each file includes:
  - `scenario_id`
  - `initial_state`
  - `strategy_overrides`
  - `ticks` with token orderbooks
- Plus generation summary in stdout.
