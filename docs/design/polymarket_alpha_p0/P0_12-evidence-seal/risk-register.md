# Residual risk register

| Risk | Offline status | Required next gate |
|---|---|---|
| Live Gamma/CLOB schema or rate drift | Not exercised | Read-only operational pilot |
| Arbitrary binary-market paired book availability | Not exercised | Read-only operational pilot |
| Weather capture-owner load/isolation | No production change | Read-only operational pilot |
| External research source availability | Manual fixtures only | Provider-specific operational review |
| Credential/order reachability | Static, transport, canary, and OS tests pass | Re-run before every operational release |
| Prediction calibration/resolution backfill | Not part of P0 | P1 learning-loop design |

Any failed operational gate disables the Alpha entrypoint while retaining all
immutable fixture and failure evidence. It does not authorize a fallback to
order, signing, or production capture code.
