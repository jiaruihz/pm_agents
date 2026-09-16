# Gate R MVP 5-Market Sample Evidence Seal

Run date: 2026-08-28
Disposition: `PARTIAL_SUCCESS_STOPPED_AT_BLIND_PACKET_FROZEN`
Execution: `NO_ORDER`

## Scope

This seal preserves the one-off, user-authorized read-only Gamma capture and
five real-market smoke sample. It is not the preregistered eight-case Gate R
WP6, a read-only operational pilot, daily scanning authorization, production
capture expansion, or trading authorization.

## Result

- Gamma `/events`: HTTP 200 through the explicit local proxy;
- 5 events and 24 nested markets captured;
- no redirects and no authentication headers;
- five selected markets reached Candidate, Rule A, GLM semantic triage and an
  exact sealed Blind prompt;
- all five GLM decisions were `ADVANCE / ELIGIBLE / HIGH`;
- all five actual provider receipts reported `glm-4.7`; no independent GLM-5.3
  attempt was run in this smoke sample;
- five human-copyable prompts passed recursive Blind leakage checks;
- every run stopped at `BLIND_PACKET_FROZEN` because no real GPT Pro result had
  been returned;
- no formal-review book, MarketComparison, Rule B, PredictionRecord or order
  was produced.

## Preserved artifacts

- `gamma/`: authorization, budget, endpoint policy, raw Gamma bytes, transport
  receipt and proxy security receipt;
- `batch/`: readable five-market index, cards, copyable Blind prompts and batch
  manifest;
- `runs/<market_id>/`: per-market input, semantic triage provider receipts and
  outputs, Candidate/Rule/Blind artifacts, prompt seals, outbox and state
  markers;
- `hashes.sha256`: recursive SHA256 manifest for every preserved artifact except
  the hash file itself.

The temporary per-run `alpha.db` files are intentionally not copied. They are
scratch execution stores, not canonical project databases. The immutable
records needed to establish the smoke result are preserved as JSON/text
artifacts here. Original temporary paths remain in historical receipts as
capture-time provenance.

## Market list

| Market id | Market | Snapshot YES ask | Spread | Stop state |
|---|---|---:|---:|---|
| 691547 | Kraken IPO by 2026-12-31 | 13.0¢ | 4.0¢ | BLIND_PACKET_FROZEN |
| 3206940 | Macron out by 2026-12-31 | 6.8¢ | 2.4¢ | BLIND_PACKET_FROZEN |
| 2354064 | UK election called by 2026-12-31 | 8.0¢ | 1.0¢ | BLIND_PACKET_FROZEN |
| 677404 | China–India military clash by 2026-12-31 | 8.0¢ | 1.0¢ | BLIND_PACKET_FROZEN |
| 2749382 | NATO/EU troops fighting in Ukraine by 2026-12-31 | 6.7¢ | 2.3¢ | BLIND_PACKET_FROZEN |

## Provider telemetry

Across the five GLM semantic triage attempts:

```text
reported_model=glm-4.7
input_tokens=5199
cache_read_input_tokens=1984
output_tokens=3874
provider_reported_cost_usd=0.123837
web_search_requests=0
web_fetch_requests=0
```

## Correct continuation

Use each file in `batch/PROMPT_*.md` in a separate fresh GPT Pro session. Do
not copy the corresponding market card into the Blind session. Preserve the
complete response and source artifacts before importing. Only an accepted
Blind result may trigger fresh paired book demand and the remaining Rule B /
`NO_ORDER` ledger path.
