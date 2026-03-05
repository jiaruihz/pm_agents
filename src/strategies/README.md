# Strategy Catalog

`src/strategies/` is the single source of truth for strategy metadata used by
runtime registry, dashboard APIs, and operator tooling.

Each strategy folder should contain:

- `manifest.yaml`: strategy metadata
- `README.md`: strategy notes
- `params.example.json`: parameter template
- `run.sh`: runner command
