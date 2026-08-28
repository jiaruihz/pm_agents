# WCIR causal entry and oracle-family contract

This amendment applies to every oracle policy, size, horizon, row-level output,
and aggregate.  It does not authorize policy selection, modeling, deployment, or
Stage 4.

The only causal entry gate is:

```text
causal_entry_eligible =
    effective_entry_decision_ready_ts_utc < official_first_seen_ts_utc
    AND effective_lead_seconds > 0
```

`effective_entry_decision_ready_ts_utc == official_first_seen_ts_utc` fails
closed.  A missing, timezone-naive, malformed, or otherwise uncomparable source,
official-first-seen, or latency clock fails closed.  No exception is allowed for
policy, share size, horizon, city, or aggregate.

Action construction may use only the frozen semantic label and book state at the
entry checkpoint.  Book/reaction/exit data after official first_seen may be used
only for measurement, never to choose the action or token.  Every measurement row
stores the effective entry clock, official first_seen clock, lead, causal result,
reason, action-data cutoff, and the assertion that post-official market data was
not used for action construction.

The historical 49.773-second entry remains a legacy reconciliation diagnostic.
It is not a forward-primary operational SLO.
