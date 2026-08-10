# weather_edge_v1 implementation package

This directory is a shared implementation package for the current weather
strategy family. It is not a model, data-source, research, deployment, or
operations entrypoint.

Current ownership is split by contract:

- production topology and process identity: `src/strategies/runtime/production.yaml`
- weather/observation/forecast/market-book collection: `weather_data_feed/` and
  `weather_data_feed_service/`
- strategy status and evidence: `docs/WEATHER_STRATEGY_REGISTRY.md`
- operator handoff: `docs/WEATHER_STRATEGY_ENTRYPOINT.md`
- model workflows: repository `skills/weather-*`

The modules here provide reusable execution, lineage, source-policy and model
artifact helpers consumed by registered runners. A runtime path named
`runtime/weather_edge_v1` is a compatibility namespace, not proof that the old
monolithic Weather Edge v1 strategy is running.

The retired May 2026 `weather-predict` B0p/B3f bridge and its nested model skill
were removed. Historical evidence remains available through git history and the
canonical historical-data/archive contracts; do not restore those files as a
current producer or model entrypoint.
