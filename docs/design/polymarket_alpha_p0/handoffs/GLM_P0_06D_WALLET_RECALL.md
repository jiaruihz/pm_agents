# GLM Work Order — P0-06D Wallet Recall

```text
Task ID: P0-06D-WALLET_RECALL
Phase: P0_OFFLINE_IMPLEMENTATION_ONLY
Completion target: READY_FOR_CODEX_REVIEW
```

你只实现 specialist wallet Recall provider。你不是仓库唯一工作者；不得
回滚或格式化他人改动，不得启动subagent。

Owned files only:

```text
src/polymarket_alpha/recall/wallet.py
tests/polymarket_alpha/test_wallet_recall_p0_06d.py
tests/polymarket_alpha/fixtures/wallet_recall/**
docs/design/polymarket_alpha_p0/P0_06D-evidence-seal/**
```

The provider is pure: caller supplies frozen wallet facts, source identity,
pagination/completeness receipt, observation clock and address/entity mapping.
It performs no DB open, HTTP call, tracker update or copy-trade action.

Required behavior:

1. Enforce configurable freshness at the observation/as-of boundary. Stale
   facts return historical/no-current-hit results and never form current Recall.
2. Preserve `Address != Entity`; an address alias may reference an entity only
   with explicit provenance and confidence. Never silently merge addresses.
3. Pagination truncation, missing window coverage, source identity mismatch and
   ambiguous token/market mapping fail closed.
4. Direction/side/position/notional may be used only in a private provider input
   fingerprint. Public Recall features/reasons must not expose trade direction,
   position side or prose that can leak it to Blind research.
5. Emit deterministic `RecallHit` with `SPECIALIST_WALLET` reason codes only;
   no probability, price, fair-value or execution recommendation.

The known repository wallet stores are stale historical assets. Do not open or
refresh them; fixtures representing their age must produce no current hit.

Explicitly out of scope: wallet collector/tracker, current DB access, network,
Candidate/state-machine changes, Blind builder, copy trade, order/signing/key.

Tests must cover: freshness just-before/at/after boundary, stale historical
source, Address/Entity distinction, ambiguous alias, incomplete pagination,
direction redaction across nested features/reasons, retry/cross-run identity,
input ordering and provider isolation. Run focused tests, full Alpha tests and
P0-11 source audit.

Handback: changed files, requirement-to-test map, exact commands/results, golden
public/private fingerprints, limitations, out-of-scope confirmation and
available model/effort/token telemetry. Do not commit; Codex reviews and merges.
