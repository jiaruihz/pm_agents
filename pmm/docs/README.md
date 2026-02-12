# PMM Documentation

> **Last Updated**: 2026-02-12

## Quick Start

1. **[ARCHITECTURE.md](ARCHITECTURE.md)** — System overview, directory structure, dependency rules
2. **[CODE_IMPLEMENTATION.md](CODE_IMPLEMENTATION.md)** — Module walkthrough and implementation details
3. **[MAIN_FLOW.md](MAIN_FLOW.md)** — Tick loop lifecycle and sequence diagrams

## Strategy & Backtesting

- **[STRATEGY_PLAYBOOK.md](STRATEGY_PLAYBOOK.md)** — Strategy design patterns and signal computation
- **[BACKTEST_SCENARIO_METHOD.md](BACKTEST_SCENARIO_METHOD.md)** — Backtesting workflow and fill models

## Reference

- **[METRICS_FORMAT.md](METRICS_FORMAT.md)** — Metrics logging format specification
- **[TODO_IMPROVEMENTS.md](TODO_IMPROVEMENTS.md)** — Roadmap and future enhancements

## Archive

Historical planning documents (completed work):
- `archive/CODE_REVIEW.md` — Pre-refactoring analysis (2026-02-12)
- `archive/REFACTOR_PLAN.md` — Refactoring execution plan (completed)
- `archive/SKILL_ARCHITECTURE_PLAN.md` — Architecture planning
- `archive/EXECUTION_PLAN_REALDATA_MULTI_LEVEL.md` — Multi-level implementation plan (completed)
- `archive/STRATEGY_KEY_ROUTING_PLAN.md` — Strategy routing design (completed)

## Getting Started

```bash
# Set environment variables (see ARCHITECTURE.md for full list)
export PMM_TOKEN_IDS="token1,token2"
export PMM_EXEC_MODE="paper"
export PMM_MARKET_DATA_SOURCE="ws"
export PMM_STRATEGY_KEY="multi_level_v1"

# Run
python pmm/main.py

# Backtest
python scripts/python/pmm_backtest.py run-all
```

For detailed configuration, see [ARCHITECTURE.md#configuration](ARCHITECTURE.md#configuration).
