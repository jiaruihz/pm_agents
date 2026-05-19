# Weather Low Price Gamma V1

Research package for low-price weather exact-bin YES opportunities.

This package is intentionally separate from `weather_edge_v1` live execution. Its first job is to test whether low-price YES tickets show path-dependent repricing value before settlement.

## Research Scope

Initial universe:

- Daily highest-temperature exact-bin YES markets.
- Entry proxy price between `0.05` and `0.20`.
- Model probability / entry price at least `1.5`.
- Absolute model edge at least `0.03`.

The first backtest is a weak, historical proxy study using weather paper snapshots. It uses snapshot prices as a path proxy and must not be treated as a real executable bid/ask backtest.

Executable gamma conclusions require CLOB price history and, preferably, live orderbook snapshots with bid/ask and depth.

## Commands

Run from the repository root:

```bash
.venv/bin/python -m src.strategies.weather_low_price_gamma_v1.cli audit
.venv/bin/python -m src.strategies.weather_low_price_gamma_v1.cli weak-backtest
```

Default inputs:

```text
runtime/weather_edge_v1/market_data/
```

Default outputs:

```text
runtime/weather_low_price_gamma_v1/research/
```

