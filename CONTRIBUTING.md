# Contributing

## Scope

The active mainline is the weather research, canonical-data, dashboard and
execution system. PMM/ARB packages remain available as dormant assets; do not
infer current production behavior from their historical entrypoints.

Read `AGENTS.md`, `docs/PROJECT_STRUCTURE.md` and the task-specific skill before
changing production or research behavior.

## Development Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pre-commit install
```

## Run Tests

```bash
python scripts/ops/verify_repo.py --profile fast
python scripts/ops/verify_repo.py --profile maintained
```

Run focused tests first. The default CI suites cover maintained production,
platform, dashboard and strategy contracts. `tests/research_tests` remains a
separate task-scoped surface because some tests require immutable mounted
archives or intentionally long research fixtures. Use `--profile full` only
for the explicit repository-wide audit; it also runs docs and research gates.

## Pull Request Guidelines

1. Keep changes focused and small.
2. Add or update tests for behavior changes.
3. Update docs when changing configs, CLI, or output schema.
4. Use clear commit messages.

## Security

Do not commit private keys, API keys, or wallet secrets.
Use `.env` locally and keep `.env.example` sanitized.
