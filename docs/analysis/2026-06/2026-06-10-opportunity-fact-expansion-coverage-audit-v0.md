# Opportunity Fact Expansion Coverage Audit v0

> generated_at_utc: `2026-06-09T18:56:46.292593+00:00`
> git_sha: `90fce40`
> Scope: local coverage audit only; no raw-file strategy PnL; no N100/live config changed.

## 数据快照

- 策略结论授权源仍是 `runtime/weather.db.fact_signal_candidates` / `fact_trades`。
- fact_signal_candidates range：`{'min_event_date': '2026-05-05', 'max_event_date': '2026-06-10', 'event_dates': 37, 'rows': 25117}`。
- 主合规 opportunity 分母：`{'min_event_date': '2026-05-12', 'max_event_date': '2026-06-08', 'rows': 918, 'event_dates': 26, 'city_days': 372}`。

### 强制 5 行 SQL 自检

```json
{
  "max_fact_built_at_utc": "2026-06-09T17:37:40.513320+00:00",
  "trade_class_distribution": [
    {
      "trade_class": "live_real",
      "n": 1405
    },
    {
      "trade_class": "live_simulated",
      "n": 1147
    },
    {
      "trade_class": "paper",
      "n": 2285
    },
    {
      "trade_class": "snapshot_replay",
      "n": 636
    }
  ],
  "settlement_status_distribution": [
    {
      "settlement_status": null,
      "n": 232
    },
    {
      "settlement_status": "settled",
      "n": 5241
    }
  ],
  "candidate_coverage": {
    "rows": 25117,
    "eligible": 8306,
    "paper_ordered": 3139,
    "live_filled": 554
  },
  "order_fill_coverage": [
    {
      "status": "error",
      "orders": 151,
      "with_fill": 0
    },
    {
      "status": "submitted",
      "orders": 1635,
      "with_fill": 1405
    }
  ]
}
```

## 市场数据覆盖

| source | files/dated | min date | max date | distinct dates | path |
| --- | --- | --- | --- | --- | --- |
| paper_snapshots | 1690/1690 | 2026-05-05 | 2026-06-10 | 37 | /home/rui/projects/pm_agent/runtime/weather_edge_v1/market_data/paper_snapshots |
| orderbook_snapshots | 1053/1053 | 2026-05-19 | 2026-06-10 | 23 | /home/rui/projects/pm_agent/runtime/weather_edge_v1/market_data/orderbook_snapshots |
| pm_history | 16484/1575 | 2026-05-04 | 2026-06-08 | 36 | /home/rui/projects/pm_agent/runtime/weather_edge_v1/market_data/cache/pm_history |
| research_files | 37/8 | 2026-05-12 | 2026-06-09 | 5 | /home/rui/projects/pm_agent/runtime/weather_edge_v1/market_data/research |

## 天气缓存覆盖

| source | files | filename min | filename max | sample content min | sample content max |
| --- | --- | --- | --- | --- | --- |
| iem_cache | 38 | 2025-05-12 | 2025-05-12 | 2025-05-12 | 2026-05-11 |
| wu_obs | 52 | None | None | None | None |

## Builder 输入

```json
{
  "builder_path": "/home/rui/projects/pm_agent/scripts/etl/build_weather_signal_candidates.py",
  "exists": true,
  "uses_paper_snapshots": true,
  "uses_paper_orders": true,
  "uses_fact_trades": true,
  "uses_settlements": true,
  "hardcoded_snapshot_dir": true,
  "hardcoded_paper_orders_path": true
}
```

## 人话结论

- 本机现在不能把交易 opportunity fact 合规扩成两年历史；关键缺口是历史 Polymarket decision-time snapshot/orderbook，不是天气观测。
- IEM/WU/forecast cache 可以支持模型质量、校准、季节误差研究，但不能直接证明策略 ROI 或可成交 edge。
- 要回答两年里 adjacent3/side-band 是否有交易 edge，必须先把历史市场价格、condition_id、盘口和结算标签重建进 `fact_signal_candidates` 同粒度表。
- 因此当前不应拿“两年天气数据”批准或否定 live；现在只能说近窗 fact 下 non-all-YES 策略未过 live-test 三门。

## 最小扩样路线

1. Recover or ingest historical Polymarket paper_snapshots/orderbook snapshots with decision timestamps.
2. Ensure condition_id/market_id/bracket/event_date mapping is available for those historical markets.
3. Ensure pm_history/settlement labels cover the same event dates.
4. Re-run build_weather_signal_candidates.py so expanded data lands in runtime/weather.db fact_signal_candidates.
5. Only then rerun adjacent3 matched-baseline/readiness gates.
