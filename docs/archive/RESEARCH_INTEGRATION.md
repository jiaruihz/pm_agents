# RESEARCH Integration Notes

This repo contains the migrated research pipeline under `src/strategies/rule_lawyer/`.

## What was migrated

- `src/strategies/rule_lawyer/` package (pipeline, nodes, schema, CLI)
- research command entry under `src.strategies.rule_lawyer.cli`
- research tests under `tests/research_tests/`
- prompt template at `data/template.md`

## Client structure

- Shared client/storage infrastructure is under `src/platform/`.
- Research API clients now live under `src/platform/clients/`:
  - `src/platform/clients/gamma.py`
  - `src/platform/clients/clob.py`
  - `src/platform/clients/research_http_client.py`
- LLM client remains in `src/agents/llm/client.py`.

## Run examples

```bash
python -m src.strategies.rule_lawyer.cli init-db
python -m src.strategies.rule_lawyer.cli sync --active true --pages 2 --page-size 100
python -m src.strategies.rule_lawyer.cli enrich --limit 200 --top-n 20
python -m src.strategies.rule_lawyer.cli candidates --output output/candidates.csv
```

## Environment variable migration

- Prefix: `RESEARCH_*` (for example `RESEARCH_DB_PATH`)
