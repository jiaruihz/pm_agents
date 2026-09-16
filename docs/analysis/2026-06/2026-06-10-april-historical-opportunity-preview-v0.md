# April Historical Opportunity Preview v0

> generated_at_utc: `2026-06-10T01:34:20.712726+00:00`
> git_sha: `e3d7140`
> Scope: fact-like preview only; no raw-file strategy PnL; no N100/live config changed.

## 数据快照

- 策略结论授权源仍是 `runtime/weather.db.fact_signal_candidates` / `fact_trades`。
- 本 preview 不写入 DB，不计算 ROI，不填 live action。
- 输出 CSV：`/home/rui/projects/pm_agent/runtime/weather_edge_v1/market_data/research/april_historical_opportunity_preview_v0.csv.gz`。
- fact_signal_candidates 当前范围：`{'min_event_date': '2026-05-05', 'max_event_date': '2026-06-10', 'event_dates': 37, 'rows': 25117}`。

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

## Preview 覆盖

| metric | value |
| --- | --- |
| April gamma city-days | 390 |
| April gamma markets | 4290 |
| April gamma tokens | 8580 |
| CLOB token files loaded | 8580 |
| Preview rows | 34320 |

## Decision Price 覆盖

| hours_to_noon_utc | rows | entry token coverage | YES price coverage |
| --- | --- | --- | --- |
| 24 | 8580 | 98.9% | 98.9% |
| 18 | 8580 | 100.0% | 100.0% |
| 12 | 8580 | 100.0% | 100.0% |
| 6 | 8580 | 100.0% | 100.0% |

## 人话结论

- April 可以生成 fact-like opportunity preview：city/date/bracket/condition/token/side/decision price 都能落表。
- 这一步解决的是“交易机会分母和历史价格”问题，不解决最终策略验证。
- 仍缺 `model_p_yes`、`final_yes`、orderbook depth，所以不能从这个 preview 直接算 ROI 或 live-test。
- 下一步应先补 forecast probability join 和 settlement join，再把 preview 升级成真正的 historical `fact_signal_candidates` backfill。

## Schema 缺口

- `model_p_yes=NULL`：还没接历史 forecast probability engine。
- `final_yes=NULL`：April settlement 未进入 `settlements`/pm_history city-day fact。
- `orderbook_depth_available=0`：CLOB history 是价格序列，不是盘口深度。

## Sample Rows

```json
[
  {
    "preview_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131|BUY_NO|2026-04-01|T24",
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131",
    "market_id": "1780118",
    "bracket": "83°F or below",
    "bracket_low_f": null,
    "bracket_high_f": 83.0,
    "bracket_center_f": 83.0,
    "side": "BUY_NO",
    "token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "yes_token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "no_token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "decision_hours_to_noon_utc": 24,
    "decision_snapshot_ts_utc": "2026-03-31T12:00:00+00:00",
    "token_price_ts_utc": "2026-03-31T11:00:35+00:00",
    "token_price_stale_minutes": 59.416666666666664,
    "entry_price": 0.973,
    "market_yes_price": 0.009,
    "model_p_yes": null,
    "final_yes": null,
    "orderbook_depth_available": 0,
    "source": "gamma_events+clob_price_history"
  },
  {
    "preview_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131|BUY_NO|2026-04-01|T18",
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131",
    "market_id": "1780118",
    "bracket": "83°F or below",
    "bracket_low_f": null,
    "bracket_high_f": 83.0,
    "bracket_center_f": 83.0,
    "side": "BUY_NO",
    "token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "yes_token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "no_token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "decision_hours_to_noon_utc": 18,
    "decision_snapshot_ts_utc": "2026-03-31T18:00:00+00:00",
    "token_price_ts_utc": "2026-03-31T17:00:04+00:00",
    "token_price_stale_minutes": 59.93333333333333,
    "entry_price": 0.992,
    "market_yes_price": 0.008,
    "model_p_yes": null,
    "final_yes": null,
    "orderbook_depth_available": 0,
    "source": "gamma_events+clob_price_history"
  },
  {
    "preview_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131|BUY_NO|2026-04-01|T12",
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131",
    "market_id": "1780118",
    "bracket": "83°F or below",
    "bracket_low_f": null,
    "bracket_high_f": 83.0,
    "bracket_center_f": 83.0,
    "side": "BUY_NO",
    "token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "yes_token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "no_token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "decision_hours_to_noon_utc": 12,
    "decision_snapshot_ts_utc": "2026-04-01T00:00:00+00:00",
    "token_price_ts_utc": "2026-03-31T23:00:41+00:00",
    "token_price_stale_minutes": 59.31666666666667,
    "entry_price": 0.985,
    "market_yes_price": 0.015,
    "model_p_yes": null,
    "final_yes": null,
    "orderbook_depth_available": 0,
    "source": "gamma_events+clob_price_history"
  },
  {
    "preview_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131|BUY_NO|2026-04-01|T6",
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131",
    "market_id": "1780118",
    "bracket": "83°F or below",
    "bracket_low_f": null,
    "bracket_high_f": 83.0,
    "bracket_center_f": 83.0,
    "side": "BUY_NO",
    "token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "yes_token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "no_token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "decision_hours_to_noon_utc": 6,
    "decision_snapshot_ts_utc": "2026-04-01T06:00:00+00:00",
    "token_price_ts_utc": "2026-04-01T05:00:35+00:00",
    "token_price_stale_minutes": 59.416666666666664,
    "entry_price": 0.991,
    "market_yes_price": 0.009,
    "model_p_yes": null,
    "final_yes": null,
    "orderbook_depth_available": 0,
    "source": "gamma_events+clob_price_history"
  },
  {
    "preview_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131|BUY_YES|2026-04-01|T24",
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131",
    "market_id": "1780118",
    "bracket": "83°F or below",
    "bracket_low_f": null,
    "bracket_high_f": 83.0,
    "bracket_center_f": 83.0,
    "side": "BUY_YES",
    "token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "yes_token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "no_token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "decision_hours_to_noon_utc": 24,
    "decision_snapshot_ts_utc": "2026-03-31T12:00:00+00:00",
    "token_price_ts_utc": "2026-03-31T08:00:58+00:00",
    "token_price_stale_minutes": 239.03333333333333,
    "entry_price": 0.009,
    "market_yes_price": 0.009,
    "model_p_yes": null,
    "final_yes": null,
    "orderbook_depth_available": 0,
    "source": "gamma_events+clob_price_history"
  },
  {
    "preview_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131|BUY_YES|2026-04-01|T18",
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131",
    "market_id": "1780118",
    "bracket": "83°F or below",
    "bracket_low_f": null,
    "bracket_high_f": 83.0,
    "bracket_center_f": 83.0,
    "side": "BUY_YES",
    "token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "yes_token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "no_token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "decision_hours_to_noon_utc": 18,
    "decision_snapshot_ts_utc": "2026-03-31T18:00:00+00:00",
    "token_price_ts_utc": "2026-03-31T17:00:29+00:00",
    "token_price_stale_minutes": 59.516666666666666,
    "entry_price": 0.008,
    "market_yes_price": 0.008,
    "model_p_yes": null,
    "final_yes": null,
    "orderbook_depth_available": 0,
    "source": "gamma_events+clob_price_history"
  },
  {
    "preview_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131|BUY_YES|2026-04-01|T12",
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131",
    "market_id": "1780118",
    "bracket": "83°F or below",
    "bracket_low_f": null,
    "bracket_high_f": 83.0,
    "bracket_center_f": 83.0,
    "side": "BUY_YES",
    "token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "yes_token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "no_token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "decision_hours_to_noon_utc": 12,
    "decision_snapshot_ts_utc": "2026-04-01T00:00:00+00:00",
    "token_price_ts_utc": "2026-03-31T23:00:59+00:00",
    "token_price_stale_minutes": 59.016666666666666,
    "entry_price": 0.015,
    "market_yes_price": 0.015,
    "model_p_yes": null,
    "final_yes": null,
    "orderbook_depth_available": 0,
    "source": "gamma_events+clob_price_history"
  },
  {
    "preview_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131|BUY_YES|2026-04-01|T6",
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xccf8d1253354ef3e53a75e76cba64f3efa30bbb4dd41e55e240f5f6d726d8131",
    "market_id": "1780118",
    "bracket": "83°F or below",
    "bracket_low_f": null,
    "bracket_high_f": 83.0,
    "bracket_center_f": 83.0,
    "side": "BUY_YES",
    "token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "yes_token_id": "94753592904928809915317233664313018043364752903337195665576954896909713727318",
    "no_token_id": "85614201615246658376875538945287519581665932882127893062311400856820677460012",
    "decision_hours_to_noon_utc": 6,
    "decision_snapshot_ts_utc": "2026-04-01T06:00:00+00:00",
    "token_price_ts_utc": "2026-04-01T05:00:48+00:00",
    "token_price_stale_minutes": 59.2,
    "entry_price": 0.009,
    "market_yes_price": 0.009,
    "model_p_yes": null,
    "final_yes": null,
    "orderbook_depth_available": 0,
    "source": "gamma_events+clob_price_history"
  }
]
```
