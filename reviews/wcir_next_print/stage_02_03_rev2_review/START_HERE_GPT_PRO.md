# WCIR Stage 2/3 rev2 — start here

## Executive answer

The reconstruction/oracle machinery is now auditable and deterministic, but
the historical executable-book evidence is not sufficient for modeling.

- Full archive: 15,856,707 frames, 465 files, 19 days.
- Frozen events: 841; source-t0 book-valid: 66 (7.85%).
- Root cause: 278 never subscribed, 419 stale, 44 no baseline, 34 open gap.
- Valid but economically one-sided/depth-insufficient: only 22.
- Normal transport days: 0; reconnect/gap days: 19; unknown reasons: 0.
- Primary paired oracle rows: 89; exact matched baseline intersection: 1.
- Forward/reverse/chunked replay: identical and pass.
- Production strict: healthy; WCIR orders/fills/notional remain 0/0/0.

## Recommended disposition

```text
Stage 2:
ACCEPT_STAGE2_REV2_RECONSTRUCTION_AND_DIAGNOSIS
AUTHORIZE_SEPARATE_COLLECTOR_CLOCK_CONTRACT_AMENDMENT

Stage 3:
CONTINUE_COLLECTION_WITHOUT_MODELING
DATA_INFRA_BLOCKED

Stage 4:
NOT_AUTHORIZED
```

The proposed next amendment is limited to archive/epoch/source-event token
coverage and freshness. It must not change model, selector, threshold, position
policy, production config, order path or live behavior without a separate
deployment review.

This zip is intentionally compact: it contains contracts, summaries, hashes,
reproduction and review records only. It contains no raw WS/REST/forecast data
and no row-level event/oracle tables.
