# Known limitations

1. The two research results are deterministic engineering fixtures. They prove
   packet/brief/work-order/import/state-machine integration only, not factual
   probability quality.
2. Every recall provider executed, but controversy and wallet produced zero hits
   because no current mapped dispute case or current wallet fact was supplied.
   Zero is an honest provider result, not evidence those routes find live alpha.
3. Structural metadata produced 50 typed suppressions because every catalog row
   was an initial NEW snapshot. A later changed-snapshot pilot is still required
   to demonstrate a positive structural hit on real incremental data.
4. Fresh orderbooks covered the 11-market Amsterdam weather family (22 tokens),
   not all 50 selected markets. This was enough to execute the book route and a
   formal paired-book refresh for one full chain.
5. The production weather WS owner was not restarted or changed. Its JRS write
   permission fault meant this run used the same existing owner batch code as a
   bounded local one-shot. Daily owner health/coverage is therefore not proven.
6. Runtime artifacts are under `/private/tmp` and are not a durable archive.
   Hashes and compact evidence are committed, but long-term raw retention needs
   a separately approved artifact destination.
7. No production DB migration, capture expansion, order, signing, or private-key
   access occurred or is authorized by this seal.
8. The original network receipts predate the post-review authorization
   policy/budget hash-binding fix. The live calls were explicitly user-authorized
   and transport-policy receipted, but no new live receipt was fabricated after
   expiry; the fix is covered by adversarial regression tests.
