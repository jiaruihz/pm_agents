# Final architecture recommendation

Current action: continue the append-only collector; do not change live trading.

Candidate architecture pending evidence:

```text
race-and-dedupe notifications: WIS2 origin across four Global Brokers
reliable fallback:             WIS2 cache + bounded AWC batch/cache
context-only pre-signal:       OMO, only after source-to-routine basis modeling
application-gated candidate:   FAA SWIFT, only if paired evidence beats WIS2
```

Deploy identical commit/config to `US_EAST` and `ASIA_SG_OR_MY`. The current
single-node smoke result cannot separate upstream latency from network route.
