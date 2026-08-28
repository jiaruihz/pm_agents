# GPT Pro review packet — collector clock v2 bounded network canary

## Requested disposition

Please return exactly one of:

```text
ACCEPT_COLLECTOR_CLOCK_AMENDMENT_FOR_ISOLATED_ZERO_NOTIONAL_SHADOW_DEPLOYMENT
ACCEPT_WITH_BLOCKING_FIXES
REWORK_COLLECTOR_CLOCK_AMENDMENT
STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK
```

## What ran

One current Amsterdam weather token was verified through the public CLOB book endpoint
and requested over the public read-only market WebSocket. Five bounded phases completed:
initial connect, hard reconnect, fresh-process restart, actual-frame queue overflow,
and actual-baseline sequence-gap injection. The three normal/recovery phases each
received a token-scoped protocol receipt, full baseline, and application `PONG`;
parse errors and normal-path queue drops were zero. Thirteen frame-metadata records and
10,603 wire bytes were retained.

Eight controlled faults were actively induced and all failed closed: queue overflow,
sequence gap, heartbeat timeout, scheduler stall, exchange timestamp regression,
wall-clock regression, hard reconnect, and process restart. Queue overflow and sequence
gap are tied directly to actual public network frame/baseline paths.

No credentials, user/order channel, production consumer, daemon, order, fill, or
notional were used. This canary counts as zero of the required 10 clean-forward target
dates. The single-token request fraction is transport evidence only, not a future
expected-demand or source-opportunity claim.

## Start here

1. `NETWORK_CANARY_RESULTS.json`
2. `CONTROLLED_FAULT_MATRIX.json`
3. `TOKEN_IDENTITY_INPUT.json`
4. `NETWORK_ADAPTER_AND_CLOCK_CONTRACT.md`
5. `EXPECTED_TOKEN_DEMAND.json`
6. `ZERO_NOTIONAL_AND_ISOLATION_AUDIT.json`
7. `TEST_COMMANDS_AND_RAW_OUTPUT.txt`
8. `FINAL_CODE_AND_ENVIRONMENT_FREEZE.json`

Only after the requested acceptance may a separately authorized isolated shadow
deployment begin accumulating formal clean-forward dates under the frozen gates.
