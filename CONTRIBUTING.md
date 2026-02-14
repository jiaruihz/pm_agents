# Contributing

## Scope

This repository focuses on:

- `pmm/` market-making engine and backtest framework
- `pm_arb_bot/` arbitrage bot scaffold

Legacy `agents/` code has been removed.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run Tests

```bash
pytest tests/pmm_tests -q
```

## Pull Request Guidelines

1. Keep changes focused and small.
2. Add or update tests for behavior changes.
3. Update docs when changing configs, CLI, or output schema.
4. Use clear commit messages.

## Security

Do not commit private keys, API keys, or wallet secrets.
Use `.env` locally and keep `.env.example` sanitized.
