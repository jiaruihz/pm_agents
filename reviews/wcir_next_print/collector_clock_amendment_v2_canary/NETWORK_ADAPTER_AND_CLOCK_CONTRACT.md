# Isolated public read-only network canary contract

This artifact is a bounded canary, not an authorized shadow deployment and not a
formal clean-forward epoch. It connects only to Polymarket's public market WebSocket,
uses no authentication, order client, user channel, database, production consumer,
or production configuration, and exits after five bounded phases.

The initial request carries one operator-frozen weather token and `initial_dump=true`.
The token/condition pair is independently verified against the public CLOB book endpoint.
The public WebSocket protocol defines no standalone subscription ACK, so the first
token-scoped server frame is recorded as a protocol receipt; it is not misrepresented
as an exchange ACK message. A `book` frame is separately required as the active-epoch
baseline. Application `PING`/`PONG` proves liveness for the bounded phase.

Reconnect and process restart invalidate prior validity and require a new request,
protocol receipt, baseline, and pong. REST never repairs WS state. Queue overflow,
sequence gap, heartbeat timeout, scheduler stall, exchange timestamp regression, and
wall-clock regression all fail closed.

The queue-overflow injection uses an actual public WS frame in the bounded adapter
buffer. The sequence-gap injection occurs immediately after an actual public WS
baseline in the same adapter/state path. Exchange messages do not expose a total
sequence number, so this is not a claim that a real exchange sequence gap was observed.

The reported 100% request fraction covers exactly one frozen canary token. It is not
future expected-demand coverage and no formal source opportunity was observed.
