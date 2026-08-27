# Operational proxy boundary — evidence seal

```text
disposition=PROXY_BOUNDARY_PASS
source_commit=480b1ed47b7dd7b8992847c1ff6a53aa81bfe554
gamma_http_status=200
gamma_events=4
nested_distinct_markets=21
proxy_connect_status=200
owner_demands_submitted=0
production_changes=0
execution=NO_ORDER
```

One bounded Gamma canary passed through the explicit profile
`mac_local_market_proxy_v1`. The process cleared ambient proxy variables,
connected to the fixed loopback peer `127.0.0.1:7896`, sent only CONNECT
`gamma-api.polymarket.com:443`, verified Gamma through the normal TLS trust
store and received HTTP 200. The 84,979-byte response contained four events
and 21 distinct nested market ids.

The successful request and proxy security receipts are copied into this seal.
The bounded raw response remains at the temporary pilot root and is bound by
SHA-256 `abd388787d22f521fc4d4547a2850579872db979cebe902dd9a0f863889317f2`.
This seal proves only the proxy/Gamma read boundary. It does not approve or
claim owner-demand deployment, paired live books, weather isolation, daily
operation, production capture expansion, order placement or signing.

## Review and tests

- Independent review: 3 findings; all fixed before canary.
- Focused proxy/security/preflight regression after final parser fix: 100 passed.
- Full `tests/polymarket_alpha`: 473 passed.
- First live attempt reached Gamma but rejected standard chunked framing; its
  immutable failure receipt remains at
  `/private/tmp/polymarket-alpha-pilot/proxy-canary-7fdGXz`.
- Second live attempt passed; its temporary artifacts remain at
  `/private/tmp/polymarket-alpha-pilot/proxy-canary-bhhLlg`.

