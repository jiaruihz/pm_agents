# WCIR Stage 3 rev2 — GPT Pro review packet

## Requested disposition

```text
CONTINUE_COLLECTION_WITHOUT_MODELING
DATA_INFRA_BLOCKED
```

## What is implemented and frozen

- Full settlement-relevant universe: complementary YES/NO plus neighboring
  brackets.
- Deterministic primary oracle: actual next print + prior running state + t0
  entry cost only; no future price chooses contract, side, size or horizon.
- Primary execution: 5 shares, conservative empirical p95 latency, ask entry,
  bid exit at official+30s, with entry and exit fees. No-trade is allowed.
- Separate unattainable ex-post envelope; never used for promotion.
- Persistence, recent-slope, frozen PIT forecast-only and blocked-OOF
  market-only baseline contracts.
- Fixed horizons, pre-source/placebo reaction windows, target-date bootstrap,
  official-print grouping and city concentration gates.

## Why Stage 3 is not accepted

- Only 89/841 events have a paired executable primary oracle row.
- The exact common oracle/baseline intersection is only **1 row / 1 official
  print / 1 target date / effective N=1**.
- Every city fails the required coverage and paired-execution gates. Seoul has
  zero paired rows.
- Any apparent PnL from the 89 available rows is selection-dominated because
  Stage 2 source-t0 book-valid is only 7.85%. It is not decision-grade economic
  evidence and must not drive horizon, city or model selection.
- Market-only OOF fitting remains blocked; the single-row exact intersection is
  explicitly labelled
  `blocked_stage2_coverage_gate_and_insufficient_exact_intersection`.

The oracle/baseline/reaction code is therefore useful as a measurement harness,
not as evidence of alpha or futility. Stage 4 substantive modeling remains
unauthorized. Measurement-only fitting is also unnecessary now because it
would not cure the executable denominator failure.

## Recommended next sequence

1. Review and, if accepted, separately authorize the Stage 2 collector-clock
   amendment described in the Stage 2 packet.
2. Collect clean-forward data without changing the frozen event, book-valid,
   latency, 5-share or +30s contracts.
3. Re-run the same Stage 2/3 gates after at least 10 clean-forward target dates;
   if source-t0 book-valid remains below 80%, return to infrastructure diagnosis.
4. Start evidence-grade Stage 4 only after per-city Stage 3 gates and matched
   baselines close; then require at least 30 untouched dates after model freeze.
