# April Historical Opportunity Mapping Audit v0

> generated_at_utc: `2026-06-10T01:27:35.158252+00:00`
> git_sha: `4a252c7`
> Scope: schema/coverage audit only; no raw-file strategy PnL; no N100/live config changed.

## 数据快照

- 策略结论授权源仍是 `runtime/weather.db.fact_signal_candidates` / `fact_trades`。
- 审计窗口：`2026-04-01` 到 `2026-04-30`。
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

## Mapping 覆盖

| source | coverage | meaning |
| --- | --- | --- |
| gamma_events | 390 found files / 390 city-days / 4290 markets / 8580 tokens | 可提供 city、event_date、condition_id、market_id、bracket、YES/NO token 映射。 |
| clob_price_history | 8580/8580 token files matched; 390 city-days | 可按 token 接历史成交价格序列，但不是 orderbook depth。 |
| pm_history city-day settlement | 0 files in April | 当前缺 April 同 grain final_yes，是 fact 化硬缺口。 |

## Decision-Time Price 覆盖

| hours_to_noon_utc | tokens with price <= decision_ts | coverage | median stale minutes |
| --- | --- | --- | --- |
| 24 | 8482 | 98.9% | 59.85 |
| 18 | 8577 | 100.0% | 59.86666666666667 |
| 12 | 8580 | 100.0% | 59.916666666666664 |
| 6 | 8580 | 100.0% | 59.916666666666664 |

## 人话结论

- April raw market mapping 原型：`可以做`。
- 直接扩成完整 `fact_signal_candidates`：`不可以`。
- 关键好消息：gamma 事件本身已经给出 city/date/bracket/condition/token 映射，clob price history 也能用 full token_id 对上。
- 关键坏消息：这还只是 token 价格历史，不是可成交盘口；而且 April city-day settlement final_yes 当前缺失，forecast/model probability 也还没接进来。
- 所以它能支持下一步 historical opportunity builder 原型，不能直接支持策略 ROI 或 live-test 结论。

## Hard Blockers

- April city-day settlement files are missing from pm_history, so final_yes cannot be filled.
- CLOB history is token price history, not orderbook depth; taker/maker executable cost still needs orderbook or a conservative price proxy.
- No model probability/eligible snapshot is present in gamma/clob history; a historical forecast join is required before strategy rules can be evaluated.
- Raw files are not an authorized strategy PnL source; they must be materialized into fact_signal_candidates first.

## 最小 Builder 路线

1. Build token_to_market from gamma_events markets: city, event_date, condition_id, market_id, bracket, side, clob token.
2. Join clob_price_history by full token_id to attach historical YES/NO token price time series.
3. Choose a frozen decision timestamp policy before looking at outcomes, then select last price at or before that timestamp.
4. Join historical forecast/model probability visible at that decision timestamp.
5. Backfill April city-day settlement final_yes at the same bracket grain.
6. Materialize rows into a fact-like table with condition_id, side, event_date, decision_snapshot_ts_utc, market_yes_price, decision_entry_price, final_yes.
7. Only after materialization, rerun Range RV/adjacent3 gates with train/holdout and baselines.

## 样例 Market

```json
[
  {
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0x6d32de1ec727b87bed0c53722fe48feb943769b1ae81d6c43250ceb93873449a",
    "market_id": "1780119",
    "bracket": "84-85°F",
    "outcomes": [
      "Yes",
      "No"
    ],
    "token_count": 2,
    "best_bid": null,
    "best_ask": 0.001
  },
  {
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0x7a443878251c215e455861a183069b98bf3a1a9c4b0ea3efa002b906c8d871ce",
    "market_id": "1780121",
    "bracket": "88-89°F",
    "outcomes": [
      "Yes",
      "No"
    ],
    "token_count": 2,
    "best_bid": null,
    "best_ask": 0.001
  },
  {
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0x91055036f5080c7e8c6cb1de27683626136127391da59fc3d642f830b1a21bd8",
    "market_id": "1780123",
    "bracket": "92-93°F",
    "outcomes": [
      "Yes",
      "No"
    ],
    "token_count": 2,
    "best_bid": null,
    "best_ask": 0.001
  },
  {
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0xe597fb004a844b57178ec7e5784c53509775e4586eba23ea3d907b6978f8538c",
    "market_id": "1780125",
    "bracket": "96-97°F",
    "outcomes": [
      "Yes",
      "No"
    ],
    "token_count": 2,
    "best_bid": null,
    "best_ask": 0.001
  },
  {
    "city": "Austin",
    "event_date": "2026-04-01",
    "condition_id": "0x2ad6c500478d5d735d6673051cd5b4e3e599add4e5a1bd5c1b119cb7528c83c1",
    "market_id": "1780127",
    "bracket": "100-101°F",
    "outcomes": [
      "Yes",
      "No"
    ],
    "token_count": 2,
    "best_bid": null,
    "best_ask": 0.001
  }
]
```

## 样例 Matched Price Files

```json
[
  {
    "file": "clob_price_history/2026-04-01/Austin/09238171088503367558.json",
    "city": "Austin",
    "event_date": "2026-04-01",
    "bracket": "94-95°F",
    "side": "No",
    "history_rows": 50
  },
  {
    "file": "clob_price_history/2026-04-01/Austin/11400856820677460012.json",
    "city": "Austin",
    "event_date": "2026-04-01",
    "bracket": "83°F or below",
    "side": "No",
    "history_rows": 70
  },
  {
    "file": "clob_price_history/2026-04-01/Austin/15604415835387749295.json",
    "city": "Austin",
    "event_date": "2026-04-01",
    "bracket": "90-91°F",
    "side": "No",
    "history_rows": 81
  },
  {
    "file": "clob_price_history/2026-04-01/Austin/16786745350791591265.json",
    "city": "Austin",
    "event_date": "2026-04-01",
    "bracket": "84-85°F",
    "side": "No",
    "history_rows": 58
  },
  {
    "file": "clob_price_history/2026-04-01/Austin/26305538452483472917.json",
    "city": "Austin",
    "event_date": "2026-04-01",
    "bracket": "100-101°F",
    "side": "Yes",
    "history_rows": 81
  }
]
```
