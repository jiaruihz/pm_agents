# Regime Execution Liquidity Quality V1

## Conclusion

CapeTown-style thin/wide books are an execution-risk bucket, not a confirmed city edge.  The current live sample is too small for a hard city-pool action: 12 settled regime live fills, with only 1 CapeTown live fill and it is still open.  The execution evidence does show that wide-spread/thin-depth rows are fragile and should remain tiny/diagnostic unless fresh forward proves otherwise.

Verdict: `inconclusive_execution_bucket_keep_tiny_probe`.

## Data Snapshot

- Rebuilt DB: `/Users/deepsleep/projects/pm_agents/runtime/weather.db`.
- CLOB fill coverage gate: `gate_pass=true` before this report.
- Regime live fact rows: `14`; settled rows: `12`.
- Raw live order rows after execution_id dedupe: `15`.
- Candidate diagnostic rows after de-dupe: `651`; settled: `0`.
- Live tables use actual fact_trades fills and settlement PnL. Candidate tables are per-share diagnostic only, not live fill simulation.

## Live Real By Liquidity Quality

| quality | rows | dates | cities | avg spread | avg posted/top ask $ | cost | pnl | roi | ci low | ci high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| thin_or_wide | 8 | 4 | 7 | 0.080 | 2.351 | $+17.69 | $+7.48 | +42.3% | -100.0% | +165.8% |
| not_thin_or_wide | 4 | 2 | 3 | 0.043 | 3.098 | $+12.01 | $+5.83 | +48.6% |  |  |

## Live Real By Spread

| spread | rows | dates | cities | avg spread | cost | pnl | roi | ci low | ci high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mid_3c_to_8c | 4 | 4 | 4 | 0.050 | $+10.20 | $+4.29 | +42.1% | -100.0% | +226.1% |
| tight_le_3c | 4 | 3 | 4 | 0.020 | $+10.12 | $+6.36 | +62.8% | -100.0% | +217.0% |
| wide_gt_8c | 4 | 3 | 4 | 0.131 | $+9.38 | $+2.66 | +28.3% | -100.0% | +170.3% |

## Candidate Diagnostic By Liquidity Quality

Current runtime candidate rows are not settled yet, so this section is intentionally empty for PnL.

_No rows._

## Candidate Diagnostic By Spread

Current runtime candidate rows are not settled yet, so this section is intentionally empty for PnL.

_No rows._

## Historical Thin-Depth Proxy

This uses the existing V4 live-like replay rows whose sizing failed the min-share threshold (`below_min5_after_sizing`).  It is a capacity/thin-book proxy, not a direct spread replay.

| layer | bucket | rows | dates | cities | unit cost | unit pnl | unit roi | ci low | ci high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_best_ask_diagnostic | below_min5_after_sizing | 179 | 38 | 33 | $+108.57 | $-6.57 | -6.1% | -16.7% | +5.5% |
| frozen_live_like_route_price | below_min5_after_sizing | 64 | 32 | 22 | $+32.57 | $-2.63 | -8.1% | -30.3% | +15.0% |
| historical_best_ask_diagnostic | shadow_only_tail_or_false_fade | 40 | 24 | 26 | $+18.13 | $+3.87 | +21.3% | -9.9% | +51.2% |
| frozen_live_like_route_price | shadow_only_tail_or_false_fade | 36 | 22 | 23 | $+15.57 | $+4.43 | +28.4% | -7.7% | +63.5% |
| frozen_live_like_route_price | city_source_bias_resized | 1 | 1 | 1 | $+0.29 | $-0.29 | -100.0% |  |  |
| historical_best_ask_diagnostic | city_source_bias_resized | 1 | 1 | 1 | $+0.29 | $-0.29 | -100.0% |  |  |

## CapeTown Historical Thin-Depth Proxy

| layer | city | rows | dates | unit cost | unit pnl | unit roi | ci low | ci high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| historical_best_ask_diagnostic | CapeTown | 7 | 7 | $+4.12 | $-0.12 | -2.9% | -75.1% | +53.1% |
| frozen_live_like_route_price | CapeTown | 3 | 3 | $+1.63 | $+0.37 | +22.7% | -100.0% | +88.7% |

## CapeTown Case

| city | target | bracket | spread | posted px | posted $ | fill cost | settlement | pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CapeTown | 2026-07-05 | 18 | 0.090 | 0.240 | 1.92 | $+1.06 | None |  |

## Interpretation

- CapeTown 7/05 was a thin/wide execution: spread `9c`, posted/top-ask notional `$1.92`, actual fill `$1.0584 @ 18c`; it is still unsettled.
- The positive realized regime PnL is not coming from a broad proof that thin/wide books are good.  The settled live sample is small and CI crosses zero.
- Current runtime candidate rows do not yet have settlement, so they cannot answer opportunity-vs-loss.
- The longer historical proxy is mildly negative for `below_min5_after_sizing`: frozen/live-like `-8.1%` unit ROI and historical-best-ask `-6.1%`.  That argues against treating thin-capacity rows as the source of edge.
- For live action this supports keeping these markets as tiny probes and adding/using execution telemetry, not cutting or sizing them up from this evidence alone.

## Files

- Summary JSON: `docs/analysis/2026-07/generated/regime_execution_liquidity_quality_v1/summary.json`
- Live enriched rows: `docs/analysis/2026-07/generated/regime_execution_liquidity_quality_v1/live_orders_enriched.csv`
- Candidate diagnostic rows: `docs/analysis/2026-07/generated/regime_execution_liquidity_quality_v1/candidate_settled_rows.csv`
- Historical thin-depth proxy rows: `docs/analysis/2026-07/generated/regime_execution_liquidity_quality_v1/historical_thin_depth_proxy_rows.csv`
