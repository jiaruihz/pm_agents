# P0-11 Concrete Policy/Transport Evidence Seal

phase_status: `COMPLETE_WITH_LIMITATIONS`

technical_gate: `PASS`

overall_P0_11_status: `BLOCKED_BY_DEPENDENCY`

readiness_scope: `OFFLINE_IMPLEMENTATION_ONLY`

## Acceptance result

- 59 security tests passed across Wave-0 static policy, concrete transport policy, and macOS OS-sandbox canaries.
- Full `src/polymarket_alpha` AST audit passed with zero violations.
- The concrete facade performs no I/O. It canonicalizes and authorizes only sealed `https` host/path/method routes, exact query/body/header schemas, and explicit secret-free environment keys.
- Redirects, encoded/noncanonical paths, userinfo/non-default ports, duplicate/unknown query keys, Authorization/Cookie/signature/unknown headers, proxy/auth/secret env, malformed `/books` POSTs, private/loopback/reserved connect targets, and DNS/connect mismatch are denied.
- The frozen fixture contains only public `GET /events` and precise public `POST /books`; policy SHA-256 is `97d5d27d0e2305bb0ebfa5e63d7d93b74304649bfc1808f8ba3bd0a797c05051`.
- macOS `sandbox-exec` dynamically returned `EPERM` for a raw public-IP socket connect, and the sandbox received an explicit proxy/secret-free environment.

## Deliberate remaining boundary

This does not approve `READ_ONLY_OPERATIONAL_PILOT`. The operational adapter, every-redirect-hop reauthorization (redirects are currently fully disabled), DNS/connect integration, persistent canary receipts, signing/order sentinel, CLI/config/position DB integration, and final import/capability graph remain for final P0-11 after P0-09. A global macOS `deny process-exec` profile was verified fail-closed, but it also denies the initial Python exec; a usable child-process-denying launcher is therefore not claimed complete. Static Alpha source rules continue to ban child-process APIs.

## Sealed files

| SHA-256 | File |
|---|---|
| `665d4640a5f50e1fa0f7bef0da7cb21ed4c8d75b19f61e85d00858bc8130379e` | `src/polymarket_alpha/security/__init__.py` |
| `9c1de45fe280fafdc40c2e2e2c0a6aeb8cada6b34b694a40d884f4474eac56bc` | `src/polymarket_alpha/security/transport.py` |
| `2193800036cac42855abd2c4b8f2d61d2cd2cf9e7690d4f310a3abf4fd68d763` | `src/polymarket_alpha/security/wave0.py` |
| `b9918f62ebc2a3e13bb88df9233c4adbb9da5cafb232da81806e100b3715f4a4` | `tests/polymarket_alpha/test_security_wave0.py` |
| `d5fa243644aaabae017d44e063ff866212b2b0edf18e8cc6b1e3f16cfe824b63` | `tests/polymarket_alpha/test_security_transport_p0_11.py` |
| `b274ece39708f4f8e92bedb6e4c5eb17e2fa4b015ef3f0757f73bf30895a5a61` | `tests/polymarket_alpha/test_security_os_sandbox_p0_11.py` |
| `140e9d974a552ded74003cdb0a9abcf024a96690298d67ae99921d389a3786a0` | `tests/polymarket_alpha/fixtures/security/read_only_policy_v1.json` |
| `1a9eb0221bd80a4fcb6aa9bb0cc7647ee2ad5c928fa6f8a5ceb30b2b6ec09c60` | `junit.xml` |
| `a8592425f7d1866f019c0855bebf6ef85091af148969faa88ab263f4c3bda3b1` | `source-audit.txt` |
| `21933499ee4a86aa37dfdde15ec0c9467109222c67aca7236197803b33871c38` | `policy-fingerprint.txt` |

## Telemetry and rollback

Concrete policy implementation/review was performed by the root coordinator; exact model/effort/token telemetry is not exposed. Disable all Alpha entrypoints to roll back. No network call, DB migration, production config change, order, signing, or credential access was performed.
