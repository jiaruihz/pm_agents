# WCIR layered denominator contract

The row-level denominator artifact separates four questions:

1. `EVENT_UNIVERSE`: every frozen official-print event, 841/841.
2. `OPERATIONAL_FAIL_CLOSED`: every event is accounted for operationally; policy
   abstain and entry-data failure produce no trade and zero operational PnL.
3. `RESEARCH_MARKET_DATA_ELIGIBLE`: only causally paired executable entry/exit
   rows enter conditional alpha measurement; unavailable rows have NULL alpha
   PnL, not zero.
4. `PAIRWISE_BASELINE_COMPARABLE`: the strict same-row intersection with all
   frozen baseline inputs.

Operational disposition is exactly one of `POLICY_ACTION`, `POLICY_ABSTAIN`, or
`DATA_FAIL_CLOSED`.  Research disposition is `RESEARCH_ELIGIBLE` or
`RESEARCH_INELIGIBLE`.  These labels are not interchangeable.

The frozen counts are 841 event-universe rows, 841 operational rows, 89 research
market-data-eligible rows, and 1 fully pairwise-baseline-comparable row.  These
counts do not authorize an alpha, futility, model, selector, or deployment claim.
