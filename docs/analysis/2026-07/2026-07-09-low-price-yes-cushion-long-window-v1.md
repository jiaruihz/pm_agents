# Low-Price YES Cushion Long Window Replay v1

Status: snapshot
Date: 2026-07-09
Strategy family: `forecast_tail_low_price_yes` / HeadA

## 结论

这次是专门回应 `0.05` 是否因 Tel Aviv / Shanghai 两个已知 winner 后验选出来的问题。长窗 PIT execution-lag replay 不支持把 5c 当成稳健最优阈值：在阈值选择前的 5/29-6/30，5c 没有稳定优于更窄的 1c/3c；5c 的优势主要来自 7/1-7/7 后验窗口。

因此 5c 只能解释为“避免过紧”的临时执行探针，不是 confirmed 配置。更干净的折中是 `0.03`：比 1c 少卡合理重定价，比 5c 少吃后验窗口外的 loser。

## 数据与口径

- target_date window: `2026-05-29`..`2026-07-07`
- orderbook root: `runtime/weather_edge_v1/market_data/orderbook_snapshots`
- execution-lag book: first YES orderbook row after `decision_snapshot_ts_utc + 0m`, max wait `60` minutes
- candidate rows: `383`, matched rows: `355`, hot-tail matched rows: `244`
- selector approximation: HeadA base low-price YES (`ask 5-20c`, `edge>=20c`, first city-date row) + current `dist>0` hot-tail boundary
- fee: official Weather taker fee `0.05 * price * (1-price)`

## Pre-Choice Window

| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 120 | 32 | 37 | 15 | 12.5% | 0.095 | 0.099 | 21.5% | 2.652 | 18 |
| 0.02 | 180 | 32 | 44 | 19 | 10.6% | 0.096 | 0.103 | -2.2% | -0.418 | 17 |
| 0.03 | 198 | 32 | 44 | 22 | 11.1% | 0.097 | 0.106 | 0.4% | 0.077 | 16 |
| 0.05 | 208 | 32 | 44 | 23 | 11.1% | 0.098 | 0.109 | -3.0% | -0.714 | 15 |
| 0.08 | 213 | 32 | 44 | 24 | 11.3% | 0.098 | 0.110 | -1.8% | -0.449 | 15 |

## Post-Choice / Known-Winner Window

| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 8 | 4 | 7 | 2 | 25.0% | 0.086 | 0.094 | 154.9% | 1.215 | 2 |
| 0.02 | 16 | 5 | 13 | 5 | 31.2% | 0.101 | 0.112 | 167.4% | 3.130 | 1 |
| 0.03 | 18 | 5 | 14 | 5 | 27.8% | 0.097 | 0.109 | 143.4% | 2.946 | 1 |
| 0.05 | 18 | 5 | 14 | 5 | 27.8% | 0.097 | 0.109 | 143.4% | 2.946 | 1 |
| 0.08 | 18 | 5 | 14 | 5 | 27.8% | 0.097 | 0.109 | 143.4% | 2.946 | 1 |

## Full Window

| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 128 | 36 | 38 | 17 | 13.3% | 0.094 | 0.098 | 29.4% | 3.867 | 20 |
| 0.02 | 196 | 37 | 45 | 24 | 12.2% | 0.096 | 0.104 | 12.7% | 2.712 | 18 |
| 0.03 | 216 | 37 | 45 | 27 | 12.5% | 0.097 | 0.106 | 12.6% | 3.023 | 17 |
| 0.05 | 226 | 37 | 45 | 28 | 12.4% | 0.098 | 0.109 | 8.7% | 2.232 | 16 |
| 0.08 | 231 | 37 | 45 | 29 | 12.6% | 0.098 | 0.110 | 9.4% | 2.497 | 16 |

## 动作判断

```text
0.05 cushion:
  status=execution_probe_not_confirmed
  reason=post-choice uplift not confirmed in pre-choice historical replay
  live_action=do not keep as default; downgrade to 3c unless future forward proves 5c fill uplift

0.03 cushion:
  status=cleaner_pre_choice_candidate
  reason=less obviously selected from known 7/1-7/7 winners and keeps most execution flexibility
  action=use as current tiny-live execution cushion; monitor forward, do not size up
```

Caveat: this is execution-lag PIT replay, not exact live runner fresh-book replay. It uses the first orderbook snapshot after the decision timestamp within 60 minutes; actual live timing depends on fact-refresh and loop cadence.
