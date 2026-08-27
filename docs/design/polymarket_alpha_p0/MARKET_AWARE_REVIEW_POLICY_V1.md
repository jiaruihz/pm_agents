# Market-Aware Review Policy V1

## Required Gate R path

Only BLIND_RESULT_ACCEPTED may emit a fresh FORMAL_REVIEW paired-book demand.
The existing market-book owner remains sole capture owner. MarketResearchPacket
binds the exact accepted Blind result/hash, RuleContract/hash, paired book
receipt and snapshot hashes, capture clocks, and fee/depth/cost policy.

A deterministic Blind-vs-Book comparator computes executable YES/NO bid/ask and
policy-size depth, staleness, liquidity, cross-outcome consistency and edge
intervals relative to the unchanged Blind central/low/high estimate.

DeterministicMarketAssessmentCompiler converts MarketComparison to the existing
ResearchResultEnvelope at stage MARKET with probability_update=NONE. The
existing importer must accept it as MARKET_RESULT_ACCEPTED before Rule B.

## Failure and refresh

Rule, baseline, book, cost-policy or identity mismatch fails closed. Stale,
one-sided or insufficient-depth data is explicit risk/block; missing prices are
never fabricated. Book TTL expiry emits BOOK_REFRESH_REQUIRED.

If new public information is discovered, emit RESEARCH_REFRESH_REQUIRED and
start a new Blind revision. Neither comparator nor Rule B may rewrite the
accepted Blind probability.

## Optional critic

MarketCritique is disabled for the first pilot. A future separately authorized
critic may emit only challenge codes, liquidity/selection-risk notes, or refresh
requests. It cannot provide a replacement probability or directly drive a
decision/order. Disabling the comparator version returns to the prior offline
fixture path while preserving all artifacts.
