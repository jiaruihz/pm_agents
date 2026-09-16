# Live account reconciliation and near-binary settlement fix

Status: `snapshot`。这是 2026-06-06 账户体感亏损、CLOB fill 对账和 settlement near-binary 口径修正的证据快照。当前分析口径以 [../../WEATHER_ANALYSIS_CONTRACT.md](../../WEATHER_ANALYSIS_CONTRACT.md) 为准。

## Target Metric

`live_wallet_cashflow_vs_realized_vs_open` = 用北京时间 `fill_date_bj` 解释最近一周真实成交花钱、已结算 realized PnL、未结算 open cost/MTM，并用 fill_id reconciliation 排除 CLOB fill 漏记。

## Data Snapshot

| field | value |
|---|---|
| cashflow window | `fill_date_bj=2026-05-31..2026-06-06` |
| settlement source | `pm_history` near-binary normalized (`0.9995/0.0005` -> `1/0`) |
| DB rebuild state | after 2026-06-06 settlement fix |
| verification commands | `sync_weather_remote.sh`, DB rebuild, account reconcile script, positions API snapshot, `py_compile`, `git diff --check` |

## Fill Reconciliation

This was not a CLOB fill loss.

| metric | value |
|---|---:|
| `db_live_real_distinct_fills` | 852 |
| `raw_clob_distinct_fills` | 852 |
| `db_not_in_raw` | 0 |
| `raw_not_in_db` | 0 |

## Settlement Bug

Old ingest logic only accepted exact `1.0 / 0.0` final prices. Polymarket `pm_history` often stores effectively settled markets as `0.9995 / 0.0005`, so many expired contracts were mislabeled `missing_bracket`.

Fixed files:

```text
weather_dashboard/ingest/pm_history_settlements.py
scripts/analysis/build_weather_fact_trades.py
scripts/analysis/build_weather_signal_candidates.py
```

After rebuild, `missing_bracket` dropped from 725 rows to 0.

## Cashflow Split

Recent one-week account view, Beijing dates `2026-05-31..2026-06-06`, `fill_date_bj` cashflow basis:

| metric | value |
|---|---:|
| `actual_fill_cost_usd` | ~$1,255.49 |
| settled `realized_pnl_usd` | ~-$72.95 |
| unsettled `open_cost_usd` | ~$251.74 |
| raw live `submitted_notional_usd` | $1,784.00 |
| raw live `posted_notional_usd` | $1,769.96 |

Interpretation: wallet/account balance cannot be explained by realized PnL alone. Around $251.74 of cost was still in 6/5, 6/6, and a small amount of 6/4 open or not officially settled exposure.

## Target-Date Attribution

| target_date | result |
|---|---:|
| 2026-05-31 | -$116.67 |
| 2026-06-01 | +$67.29 |
| 2026-06-03 | +$80.80 |
| 2026-06-04 | -$117.06 |
| 2026-06-05 | open cost $158.70 |
| 2026-06-06 | open cost $93.04 |

The main settled losses were on target dates 2026-05-31 and 2026-06-04. On 5/31, `mid_price_core_v1_25_75` and V2 both lost; on 6/4, all three instances lost.

## Polymarket Positions Snapshot

Current Polymarket positions explain most of the screenshot asset structure:

| slice | initial_value | current_value | cash_pnl |
|---|---:|---:|---:|
| all current positions | $1,855.80 | $165.77 | -$1,690.03 |
| weather current positions | $1,800.31 | $135.26 | -$1,665.04 |
| non-weather positions | n/a | n/a | -$24.98 |

This is close to a screenshot structure like portfolio value ~$528.29 and cash ~$366.28: the difference is roughly current positions value. The asset drawdown feeling mainly came from weather positions, not unrelated markets.

## Current Conclusion

The old reporting understated settled coverage because settlement near-binary prices were not normalized, but the missing piece was not CLOB fill ingestion. After the fix, true realized PnL for the one-week fill-date window is still only about -$73; the remaining gap is mainly 6/5-6/6 open/MTM exposure plus Polymarket UI's portfolio net-value time-series basis, which is not directly reproduced by the current positions snapshot API.

## Recompute Trigger Phrase

Use this exact sentence in other windows:

> 按 2026-06-06 的 weather near-binary settlement 勘误重算：先 sync+rebuild weather.db，用 pm_history 0.9995/0.0005=>1/0 的新口径，重新跑 account reconcile/fact_trades/fact_signal_candidates，并把旧 missing_bracket=725/734/28 的报告视为过时。
