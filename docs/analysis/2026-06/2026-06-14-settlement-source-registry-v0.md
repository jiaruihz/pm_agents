# Settlement Source Registry v0

> generated_at_utc: `2026-06-13T17:35:29.663305+00:00`
> target_metric: `settlement_source_reliability_gap`
> Scope: data/source reliability research only; no N100/live config changed; no live action.

## 数据快照

- 数据源: `runtime/weather.db.fact_signal_candidates` for coverage counts; generated official-source CSVs for source evidence.
- DB last_modified: `2026-06-13T17:33:00.533301+00:00`.
- fact_signal_candidates rows: `28480`.
- 本报告不发布 `live_real` PnL/ROI/rank/curve，因此不使用 CLOB coverage gate 作为结论来源。

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-13T17:32:49.732951+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "rows": 856
    },
    {
      "trade_class": "live_simulated",
      "rows": 624
    },
    {
      "trade_class": "paper",
      "rows": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "rows": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": "",
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4251
    }
  ],
  "candidate_coverage": {
    "rows": 28480,
    "eligible": 9692,
    "paper_ordered": 3680,
    "live_filled": 348
  },
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 962,
      "with_fill": 856
    }
  ]
}
```

## Target Metric

`settlement_source_reliability_gap` = city-level gap between Polymarket's official settlement source and the weather source currently used by local forecast/observed features.

Important distinction: `pm_history` / `final_yes` remains the market settlement truth. The problem is whether our forecast or observed-weather feature source is predicting the same station/feed/rule that Polymarket settles against.

## Source-Class Summary

| class | cities | candidate_rows | settled_rows | settled_dates_sum |
| --- | --- | --- | --- | --- |
| default_wu_station_by_rules | 34 | 20675 | 2694 | 617 |
| official_station_diff_confirmed | 7 | 3719 | 491 | 106 |
| no_recent_market_or_unknown_rules | 4 | 85 | 19 | 3 |
| blocked_unresolved_settlement_basis | 3 | 1826 | 245 | 52 |
| non_wu_source_by_rules | 2 | 1088 | 152 | 37 |
| default_source_watchlist | 1 | 609 | 8 | 3 |
| special_source_confirmed | 1 | 478 | 53 | 13 |

## City Registry Highlights

| city | class | configured | official | align | days | settled_candidate_rows | action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Moscow | blocked_unresolved_settlement_basis | UUWW | unknown_effective_source | 88.9% | 27 | 88 | exclude from official-source reheat-risk/station-basis research until root cause is found |
| Seoul | blocked_unresolved_settlement_basis | RKSI | unknown_effective_source | 77.8% | 36 | 109 | exclude from official-source reheat-risk/station-basis research until root cause is found |
| Shenzhen | blocked_unresolved_settlement_basis | ZGSZ | unresolved_wu_feed | 71.4% | 28 | 48 | exclude from official-source reheat-risk/station-basis research until root cause is found |
| MexicoCity | default_source_watchlist | MMMX | MMMX | 96.4% | 28 | 8 | allowed for broad research but keep in settlement-watchlist |
| Boston | no_recent_market_or_unknown_rules | KBOS | None | rules_only |  | 0 | do not use for source-sensitive research until rules/settlement source is identified |
| Lagos | no_recent_market_or_unknown_rules | DNMM | None | rules_only |  | 19 | do not use for source-sensitive research until rules/settlement source is identified |
| Minneapolis | no_recent_market_or_unknown_rules | KMSP | None | rules_only |  | 0 | do not use for source-sensitive research until rules/settlement source is identified |
| Phoenix | no_recent_market_or_unknown_rules | KPHX | None | rules_only |  | 0 | do not use for source-sensitive research until rules/settlement source is identified |
| Istanbul | non_wu_source_by_rules | LTFM | https://www.weather.gov/wrh/timeseries?site=LTFM | rules_only |  | 98 | needs source-specific feature fetch before source-sensitive research |
| TelAviv | non_wu_source_by_rules | LLBG | https://www.weather.gov/wrh/timeseries?site=LLBG | rules_only |  | 54 | needs source-specific feature fetch before source-sensitive research |
| Chicago | official_station_diff_confirmed | KMDW | KORD | 100.0% | 37 | 72 | use official station for observed/source features; treat as station-basis candidate only after rules recheck |
| Jakarta | official_station_diff_confirmed | WIII | WIHH | 100.0% | 8 | 23 | use WIHH/Halim; WIII is wrong for settlement/source features |
| KualaLumpur | official_station_diff_confirmed | WMSA | WMKK | 100.0% | 28 | 52 | use official station for observed/source features; treat as station-basis candidate only after rules recheck |
| London | official_station_diff_confirmed | EGLL | EGLC | 97.2% | 36 | 163 | use official station for observed/source features; treat as station-basis candidate only after rules recheck |
| Milan | official_station_diff_confirmed | LIML | LIMC | 100.0% | 28 | 59 | use official station for observed/source features; treat as station-basis candidate only after rules recheck |
| PanamaCity | official_station_diff_confirmed | MPTO | MPMG | 100.0% | 28 | 39 | use official station for observed/source features; treat as station-basis candidate only after rules recheck |
| Paris | official_station_diff_confirmed | LFPG | LFPB | 100.0% | 36 | 83 | use official station for observed/source features; treat as station-basis candidate only after rules recheck |
| HongKong | special_source_confirmed | VHHH | HKO | 100.0% | 27 | 53 | use official HKO data; do not use VHHH/IEM/WU as payout feature source |

## Research Reuse Rules

- Forecast-quality / model-reliability research: attach `settlement_source_class` as a covariate. Do not mix `blocked_unresolved_settlement_basis` cities into a generic city-model reliability label.
- reheat-risk / observed-running-max research: only use official-source confirmed cities. For station-diff cities, rebuild running max from the official station/feed before any orderbook backtest.
- Station-basis strategies: confirmed station-diff cities are candidates only after per-market rules recheck. The edge is the market watching the wrong station, not generic temperature theta.
- HongKong: use HKO Daily Extract / live HKO feed semantics, decimal daily max, and floor-to-bracket mapping. VHHH/IEM is not an acceptable settlement feature source.
- Jakarta: use WIHH/Halim, not WIII/Soekarno-Hatta, for settlement/source features.
- Moscow, Seoul, Shenzhen: blocked for source-sensitive trading research until the unresolved mismatch is explained.
- `pm_history` remains the payout label for strategy PnL; official-source reconstruction is for feature alignment, not replacing market settlement truth.

## Next Research Directions

1. Promote this registry into a generated sidecar artifact joined by city/date in forecast-quality, reheat-risk, station-basis, and basket scripts.
2. Add official source fields to fact tables: `settlement_source_class`, `official_station_or_feed`, `settlement_mapping_rule`, and `source_verified_at`.
3. Build live-capable HKO and WIHH source fetchers before HK/Jakarta can enter any shadow feed.
4. Rerun forecast-quality base excluding or separately tagging station-diff/special-source/blocked cities to measure how much of reliability is source mismatch.
5. Diagnose Moscow/Seoul/Shenzhen by fetching the exact official rendered page values around mismatch dates and comparing to METAR/WU API minute history.
6. Add a pre-entry rules checker for station-basis candidates; station changes must block would-trades rather than silently falling back.

## Three-Gate Verdict

| gate | status | reason |
| --- | --- | --- |
| significance | NA | This is a data/source registry, not a strategy ROI test. |
| baseline | NA | No trading rule is proposed. |
| forward | NA | Registry must be rerun as new markets/rules appear. |

`conclusion=data_registry_current_reference`, allowed action: use in research/shadow feature alignment only; no live config change.
