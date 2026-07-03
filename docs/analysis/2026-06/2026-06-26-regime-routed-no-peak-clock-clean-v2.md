# Regime-routed NO Peak-clock Clean V2

## Conclusion

Clean route confirms the bug class: v1 used `day_open_runway/day_marginal_runway` as if it meant time runway, but that label only measured forecast temperature space.  Splitting peak clock improves the current candidate's tail profile, yet this is still `shadow_candidate`, not confirmed live edge.

一句话：在 2026-05-19..2026-06-23 settled replay，clean peak-future route + soft sizing 的 ROI 为 +28.7%（95% CI +5.2%, +51.7%），前瞻仍未满足冻结 forward 门，结论 `shadow_candidate/inconclusive_for_live`。

## Data Snapshot

- states rows: 11980
- settled states rows: 11737
- source: generated intraday regime/reheat feature state rows consumed through `research_regime_routed_no_expression_v1.load_states()`
- local fact rebuild: `run_stack.sh --api-only` completed before this run; CLOB fill coverage gate passed separately.

## Variant Summary

| variant | sizing | trades | active_dates | cities | win_rate | avg_ask | avg_soft_weight | profit_usd | roi | roi_ci_low | roi_ci_high | roi_le_minus50_days | roi_eq_minus100_days | worst_day_profit_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1_space_only_d2_no_relaxed70_best_ask | full_size | 271 | 35 | 35 | +51.7% | 0.510 | 0.346 | $+155.73 | +11.5% | -6.0% | +30.9% | 4 | 1 | $-25.39 |
| v1_space_only_d2_no_relaxed70_best_ask | soft_balanced | 271 | 35 | 35 | +51.7% | 0.510 | 0.346 | $+123.21 | +26.3% | +4.1% | +48.5% | 5 | 1 | $-10.35 |
| v1_space_only_d2_no_relaxed70_best_ask | soft_balanced_base5_min5 | 77 | 31 | 29 | +48.1% | 0.330 | 0.552 | $+148.45 | +69.9% | +25.1% | +114.3% | 10 | 10 | $-10.90 |
| clean_peak_future_d2_no_relaxed70_best_ask | full_size | 227 | 35 | 34 | +56.8% | 0.532 | 0.362 | $+179.87 | +15.8% | -0.3% | +32.3% | 4 | 1 | $-19.42 |
| clean_peak_future_d2_no_relaxed70_best_ask | soft_balanced | 227 | 35 | 34 | +56.8% | 0.532 | 0.362 | $+117.85 | +28.7% | +5.2% | +51.7% | 5 | 1 | $-10.52 |
| clean_peak_future_d2_no_relaxed70_best_ask | soft_balanced_base5_min5 | 66 | 29 | 26 | +48.5% | 0.347 | 0.577 | $+125.91 | +66.1% | +19.9% | +110.1% | 11 | 11 | $-10.87 |
| clean_peak_2h_ahead_d2_no_relaxed70_best_ask | full_size | 155 | 35 | 33 | +61.3% | 0.568 | 0.343 | $+108.55 | +14.0% | -0.6% | +29.2% | 2 | 1 | $-12.65 |
| clean_peak_2h_ahead_d2_no_relaxed70_best_ask | soft_balanced | 155 | 35 | 33 | +61.3% | 0.568 | 0.343 | $+74.62 | +28.0% | +4.3% | +51.0% | 8 | 1 | $-8.78 |
| clean_peak_2h_ahead_d2_no_relaxed70_best_ask | soft_balanced_base5_min5 | 35 | 21 | 18 | +54.3% | 0.379 | 0.631 | $+74.11 | +67.1% | +16.9% | +113.2% | 8 | 8 | $-10.89 |

## Worst Days: Clean Peak Future + Soft

| target_date | trades | wins | win_rate | avg_soft_weight | profit_usd | roi | route_mix | loss_cities |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-05-31 | 7 | 2 | +28.6% | 0.387 | $-10.52 | -77.7% | capped_d2_no:3,runway_current_no:4 | Karachi,Miami,TelAviv,Tokyo,Wuhan |
| 2026-06-02 | 8 | 3 | +37.5% | 0.419 | $-9.72 | -58.0% | capped_d2_no:3,runway_current_no:5 | Amsterdam,Beijing,Busan,Houston,TelAviv |
| 2026-06-01 | 4 | 1 | +25.0% | 0.439 | $-7.19 | -82.0% | capped_d2_no:1,runway_current_no:3 | CapeTown,Houston,NYC |
| 2026-06-17 | 8 | 4 | +50.0% | 0.304 | $-5.69 | -46.8% | capped_d2_no:3,runway_current_no:5 | Beijing,Chengdu,Karachi,Wuhan |
| 2026-05-25 | 6 | 2 | +33.3% | 0.342 | $-4.81 | -46.8% | capped_d2_no:1,runway_current_no:5 | Jeddah,Shanghai,Tokyo,Wuhan |
| 2026-06-07 | 5 | 2 | +40.0% | 0.351 | $-4.19 | -47.8% | capped_d2_no:2,runway_current_no:3 | Istanbul,NYC,SanFrancisco |
| 2026-05-27 | 5 | 2 | +40.0% | 0.414 | $-3.34 | -32.3% | runway_current_no:5 | Beijing,Manila,SaoPaulo |
| 2026-05-24 | 4 | 2 | +50.0% | 0.338 | $-3.09 | -45.6% | capped_d2_no:1,runway_current_no:3 | Lucknow,NYC |
| 2026-05-21 | 8 | 3 | +37.5% | 0.309 | $-2.65 | -21.4% | capped_d2_no:3,runway_current_no:5 | Manila,Munich,SanFrancisco,SaoPaulo,TelAviv |
| 2026-06-21 | 4 | 2 | +50.0% | 0.207 | $-2.07 | -50.0% | capped_d2_no:4 | Beijing,Singapore |
| 2026-06-20 | 4 | 2 | +50.0% | 0.250 | $-2.00 | -40.0% | capped_d2_no:3,runway_current_no:1 | Manila,Tokyo |
| 2026-06-18 | 5 | 3 | +60.0% | 0.279 | $-1.97 | -28.2% | capped_d2_no:3,runway_current_no:2 | CapeTown,Manila |

## Interpretation

1. `day_regime` should be read as space regime, not time runway.
2. `peak_future` removes current-bracket NO entries whose forecast peak was already past; this is the clean mechanism expression.
3. `peak_2h_ahead` is too strict for capacity: it improves tail but drops many current-NO opportunities.
4. Current evidence is better shaped after the fix, but still lacks frozen forward proof after today's live incident.

## Files

- JSON: `docs/analysis/2026-06/generated/regime_routed_no_peak_clock_clean_v2/summary.json`
- Variant summary: `docs/analysis/2026-06/generated/regime_routed_no_peak_clock_clean_v2/variant_summary.csv`
- Daily summary: `docs/analysis/2026-06/generated/regime_routed_no_peak_clock_clean_v2/daily_summary.csv`
