# P0-11 Wave-0 Security Evidence Seal

wave_status: `COMPLETE_WITH_LIMITATIONS`

technical_gate: `PASS`

overall_P0_11_status: `BLOCKED_BY_DEPENDENCY`

readiness_scope: `OFFLINE_IMPLEMENTATION_ONLY`

## Acceptance result

- 24 adversarial Wave-0 tests passed; see `junit.xml`.
- Full `src/polymarket_alpha` AST audit passed with zero violations; see `source-audit.txt`.
- The source audit denies generic network roots, raw socket/websocket paths, process/shell APIs, dynamic imports/code, `ctypes`, and known execution/signing imports.
- `OfflineCapabilityPolicy` fails closed for network, process, dynamic-import, and execution requests.
- P0-01 contract code was re-audited after integration and remained clean.

## Deliberate boundary

This is only Wave-0 policy, deny rules, CI-test scaffold, and adversarial fixtures. It is not the final P0-11 proof. Concrete read-only transport/receipt/env-endpoint schemas now may begin because P0-01 is released; OS/process network isolation, redirect/proxy handling, endpoint canaries, sentinel signing/order attempts, CLI/config/DB checks, and final proof remain pending.

## Sealed files

| SHA-256 | File |
|---|---|
| `665d4640a5f50e1fa0f7bef0da7cb21ed4c8d75b19f61e85d00858bc8130379e` | `src/polymarket_alpha/security/__init__.py` |
| `2193800036cac42855abd2c4b8f2d61d2cd2cf9e7690d4f310a3abf4fd68d763` | `src/polymarket_alpha/security/wave0.py` |
| `b9918f62ebc2a3e13bb88df9233c4adbb9da5cafb232da81806e100b3715f4a4` | `tests/polymarket_alpha/test_security_wave0.py` |
| `62e205541ee5d31089872c06ace41441ec8e3bd7f07a5a59f582adeb36a67b3a` | `junit.xml` |
| `a8592425f7d1866f019c0855bebf6ef85091af148969faa88ab263f4c3bda3b1` | `source-audit.txt` |

## Review and telemetry

The initial bounded worker reported 4 tool calls and approximately one minute wall time; exact model/effort and token telemetry were unavailable. The root coordinator independently inspected, hardened, reran, and re-audited the implementation. Missing token telemetry is why this Wave is `COMPLETE_WITH_LIMITATIONS`, despite a passing technical gate.

## Rollback

Disable every Alpha entrypoint. Wave-0 has no network or persistent side effects and does not modify legacy execution code.
