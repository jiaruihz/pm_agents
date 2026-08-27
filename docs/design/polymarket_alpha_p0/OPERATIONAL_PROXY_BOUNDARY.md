# Alpha explicit proxy boundary

```text
BOUNDARY=EXPLICIT_VERSIONED_LOOPBACK_PROXY
PROFILE_ID=mac_local_market_proxy_v1
PROXY_PEER=127.0.0.1:7896
ALLOWED_CONNECT=gamma-api.polymarket.com:443
TARGET_METHOD_PATH=GET /events
ENV_PROXY_INHERITANCE=DENIED
PROXY_AUTH=DENIED
ARBITRARY_PROXY_URL=DENIED
ORDER_SIGNING_PRIVATE_KEY=UNREACHABLE
```

The prior operational attempt correctly cleared inherited `HTTP_PROXY`,
`HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY`, but incorrectly treated the ensuing
direct-TLS timeout as proof that proxy use itself conflicted with ADR-011.
ADR-011 requires an explicit process capability boundary. A fixed, reviewed
proxy profile is compatible with that boundary; ambient proxy inheritance is
not.

The implementation therefore exposes one CLI choice rather than a proxy URL.
It connects only to the numeric loopback peer, sends one exact CONNECT
authority, rejects proxy authentication and redirect responses, and performs
normal CA-verified TLS with Gamma as SNI inside the tunnel. The target endpoint
still passes the existing host/path/method/query/header policy. Proxy profile,
peer, CONNECT status, target resolution mode, TLS name and target response are
bound into an immutable security receipt.

This capability approves neither owner demand submission nor production
configuration. Those remain separate operational-pilot gates.
