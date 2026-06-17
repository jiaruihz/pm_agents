# Forecast Peak Clock Data Fill v1

Generated: `2026-06-17T15:33:33+00:00`

## Human Verdict

这次已经把 `pm_agent` 的 fact builder 补上了：当 snapshot 里没有 `forecast_peak_*` / `forecast_values_hash` 时，builder 会从镜像 hourly forecast cache 派生这些字段。

当前本地 `fact_signal_candidates` 只有 `108` / `30919` 行有 peak/hash，覆盖率 `0.3%`。这些行只落在 `2026-05-06` 和 `2026-05-07`，和 reheat factory 的 `2026-05-19..2026-06-14` 主窗口没有交集，所以 current-YES forecast-peak-clock 仍不能正式回测。

真正的下一步在 upstream：N100 `weather-predict` 现在产出的 latest paper snapshots 仍是 `v2_cross_section`，只有 `forecast_max_f`，没有 peak/hash。需要让生产 snapshot producer 写出 v3 forecast peak fields，然后同步回本机重建。

## Mandatory SQL Self-Check

```json
{
  "fact_trades_max_built_at_utc": "2026-06-16T15:50:05.971834+00:00",
  "fact_trades_by_class": [
    {
      "trade_class": "live_real",
      "rows": 855
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
  "fact_trades_by_settlement_status": [
    {
      "settlement_status": null,
      "rows": 90
    },
    {
      "settlement_status": "settled",
      "rows": 4310
    }
  ],
  "fact_signal_candidate_coverage": {
    "rows": 30919,
    "eligible": 10685,
    "paper_ordered": 4123,
    "live_filled": 348
  },
  "clob_order_fill_join": [
    {
      "status": "error",
      "orders": 33,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 961,
      "with_fill": 855
    }
  ]
}
```

## Candidate Coverage

| scope | rows | with peak hour | with hash |
|---|---:|---:|---:|
| all fact_signal_candidates | 30919 | 108 | 108 |

### By Source

| forecast_source | rows | with peak hour | with hash |
|---|---:|---:|---:|
| `open_meteo_live_ecmwf` | 17663 | 0 | 0 |
| `open_meteo_live_gfs` | 13256 | 108 | 108 |

### Dates With Peak Fields

| event_date | rows | with peak hour | with hash |
|---|---:|---:|---:|
| `2026-05-06` | 203 | 82 | 82 |
| `2026-05-07` | 220 | 26 | 26 |

## Cache Coverage

| source | files | hourly files | date range |
|---|---:|---:|---|
| `gfs` | 67 | 67 | 2024-05-01..2026-05-06 |
| `ecmwf` | 52 | 52 | 2025-05-10..2026-04-29 |

## Reheat Factory Impact

- feature factory exists: `True`
- feature rows: `88621`
- state rows: `8696`
- forecast peak feature-row rate: `0.0`
- forecast hash feature-row rate: `0.0`

## Next Actions

1. Deploy or enable the `weather-predict` snapshot producer version that emits `forecast_peak_hour_local`, `forecast_peak_time_local`, `forecast_peak_hour_utc`, `forecast_values_hash`, and `forecast_peak_delta_hours_local`.
2. Sync fresh N100 snapshots back into `pm_agent`.
3. Rebuild `fact_signal_candidates`; the new builder will automatically preserve snapshot peak fields or derive them from hourly cache.
4. Re-run `reheat_feature_factory_v1`, then re-run current-YES peak-clock research.
