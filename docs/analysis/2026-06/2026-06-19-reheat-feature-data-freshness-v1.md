# Reheat Feature Data Freshness v1

Status: data-audit
Generated UTC: `2026-06-21T06:02:43+00:00`
Current local date: `2026-06-21`

## Human Summary

当前研究训练用的共享 feature layer 已补到 `2026-06-17`：observed-detail 到 `2026-06-17`，forecast peak backfill 到 `2026-06-17`。这三层现在是同一窗口，不再卡在旧 6/14 补丁。

settlement_outcomes 目前最大日期是 `2026-06-19`，但最新一天可能是部分城市；模型训练层按已完整补齐的 36 城研究缓存和可用 settlement 交集落在 `2026-06-17`。

## Date Ranges

| layer | max date / valid | rows/files | note |
|---|---:|---:|---|
| orderbook snapshots | 2026-06-21 | 34 dirs | raw market data is newer |
| pm_history / settlement_outcomes | 2026-06-19 | 1859 city-days | settlement bridge is newer than feature layer |
| reheat_feature_rows | 2026-06-17 | 99819 rows | model training table currently used |
| observed-detail | 2026-06-17 | 12960 rows | upstream current/running max table |
| forecast peak backfill | 2026-06-17 | 1410 rows | forecast-clock input |
| wu_obs cache | 2026-06-18T06:56:00+00:00 | 36 files | current research-compatible IEM-derived cache |
| research IEM ext patch | 2026-06-18T06:56:00+00:00 | 36 files | current reheat weather-feature cache |
| mirrored IEM v2 cache | 2026-06-09T23:58:00+00:00 | 90 files | legacy mirror, not the active reheat input |

## Remaining Gap

1. The active research cache is now repeatable via `build_reheat_iem_research_cache_v1.py`, but it is still a research command, not a scheduled production job.
2. The legacy mirrored IEM v2 cache is still stale; current reheat research no longer depends on it, but it should not be confused with the active input.
3. Dates after the latest complete settlement/label window should not be used for settled-label training until the settlement layer is complete.

## Next Data Work

1. Schedule or fold `build_reheat_iem_research_cache_v1.py` into the shared `weather_data_feed` historical observation path.
2. On each refresh, rebuild observed-detail, forecast backfill, `reheat_feature_factory_v1`, then retrain peak-forming/fade/NO-carry heads on the same date window.
3. Keep using complete settlement/label coverage as the upper bound for settled-label model training.

## Self Check

```json
{
  "fact_trades_max_built_at_utc": "2026-06-21T06:02:02.667498+00:00",
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
      "settlement_status": "",
      "rows": 150
    },
    {
      "settlement_status": "settled",
      "rows": 4250
    }
  ],
  "fact_signal_candidate_coverage": {
    "rows": 34520,
    "eligible": 12259,
    "paper_ordered": 4772,
    "live_filled": 348
  }
}
```
