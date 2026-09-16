# D-1 market-residual zero-notional shadow

This package is a standalone research/shadow harness for a frozen D-1 full-ladder
artifact. It does not import an exchange client, an order runtime, credentials, or
network code. Every summary hard-codes `orders_submitted=0`, `venue_calls=0`, and
`no_order_placed=true`.

The model contract is:

```text
posterior(bracket) ∝ normalized_market(bracket) × exp(residual_delta(bracket))
```

Therefore `residual_delta=0` returns the normalized market distribution exactly.
The feature book used by the model and the later execution book used for cost are
different required lineage fields. A missing/stale/incomplete input becomes a
structured blocker; it is never silently dropped from the checkpoint universe.
Required model features may be checkpoint-global or supplied under each feature-book
rung's `features` object. Rung values override globals; this is required for ordinal
location/scale and weather/market log-ratio residuals whose value differs by bracket.
If any rung is missing a required value, the whole distribution fails closed because
a partial posterior cannot be normalized coherently.
Selection requires an explicit fee-adjusted ask and configured minimum depth; a raw
ask alone is evidence, not an executable edge.

## Run the self-contained fixture

```bash
.venv/bin/python -m src.strategies.weather_d1_market_residual_shadow demo \
  --output /tmp/weather_d1_market_residual_shadow_demo
```

This writes full-ladder predictions, optional candidates, zero-size intents, and a
settlement replay. Even with candidate/intent recording enabled, the intent mode is
`zero_notional` and `requested_size=0`.

## Score a frozen artifact

```bash
.venv/bin/python -m src.strategies.weather_d1_market_residual_shadow run \
  --artifact /path/to/frozen_artifact.json \
  --artifact-sha256 EXPECTED_SHA256 \
  --checkpoints /path/to/pit_checkpoints.jsonl \
  --config /path/to/shadow_policy.json \
  --output runtime/research/d1_market_residual/run_id
```

Candidate and intent output are disabled when `--config` is omitted. Enabling them
only records standard WCIR `SignalCandidate` and zero-notional `TradeIntent` rows.
It still cannot create a plan or order.

## Settlement replay

```bash
.venv/bin/python -m src.strategies.weather_d1_market_residual_shadow evaluate \
  --predictions runtime/research/d1_market_residual/run_id/predictions.jsonl \
  --settlements /path/to/settlements.jsonl \
  --output runtime/research/d1_market_residual/run_id/evaluation
```

The evaluator compares posterior and market on identical complete checkpoint
ladders using date-equal logloss, multiclass Brier, RPS, and target-date block
bootstrap. It reports execution as `not_available_shadow`; no fill or PnL is
invented.
