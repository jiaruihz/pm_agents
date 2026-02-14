# RESEARCH Integration Notes

This repo contains the migrated research pipeline under `src/domains/research/`.

## What was migrated

- `src/domains/research/` package (pipeline, nodes, schema, CLI, web UI)
- research scripts under `scripts/research/`
- research tests under `tests/research_tests/`
- prompt template at `data/template.md`

## Client structure

- Shared client/storage infrastructure is under `src/platform/`.
- Research domain clients live under `src/domains/research/clients/`:
  - `src/domains/research/clients/gamma.py`
  - `src/domains/research/clients/clob.py`
  - `src/domains/research/clients/http.py`
  - `src/agents/llm/client.py`

## Run examples

```bash
python -m src.domains.research.cli init-db
python -m src.domains.research.cli sync --active true --pages 2 --page-size 100
python -m src.domains.research.cli enrich --limit 200 --top-n 20
python -m src.domains.research.cli candidates --output output/candidates.csv
```

## Environment variable migration

- Prefix: `RESEARCH_*` (for example `RESEARCH_DB_PATH`)
