# Data-source boundary

This package does not own a data source.

Current strategy runners consume the production paths resolved from
`src/strategies/runtime/production.yaml`. Forecast, observation and raw market
book ownership is defined in `docs/WEATHER_DATA_FEED_MODULE.md`; canonical
analysis sources are defined in `docs/WEATHER_DATA_CANONICAL_SOURCES.md`.

The retired `weather-predict` paths, B0p/B3f bridge and Wunderground scraping
workflow are historical inputs only. They must not be used as current truth,
fallback, or a production write target.
