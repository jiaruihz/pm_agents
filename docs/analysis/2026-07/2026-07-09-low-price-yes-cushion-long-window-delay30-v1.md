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
- execution-lag book: first YES orderbook row after `decision_snapshot_ts_utc + 30m`, max wait `45` minutes
- candidate rows: `383`, matched rows: `350`, hot-tail matched rows: `240`
- selector approximation: HeadA base low-price YES (`ask 5-20c`, `edge>=20c`, first city-date row) + current `dist>0` hot-tail boundary
- fee: official Weather taker fee `0.05 * price * (1-price)`

## Pre-Choice Window

| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 115 | 32 | 38 | 13 | 11.3% | 0.095 | 0.093 | 16.4% | 1.833 | 19 |
| 0.02 | 154 | 32 | 41 | 17 | 11.0% | 0.096 | 0.099 | 6.9% | 1.099 | 18 |
| 0.03 | 173 | 32 | 42 | 23 | 13.3% | 0.097 | 0.102 | 25.3% | 4.638 | 16 |
| 0.05 | 195 | 32 | 43 | 24 | 12.3% | 0.097 | 0.106 | 11.2% | 2.410 | 16 |
| 0.08 | 203 | 32 | 43 | 24 | 11.8% | 0.097 | 0.108 | 5.2% | 1.191 | 16 |

## Post-Choice / Known-Winner Window

| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 7 | 4 | 5 | 3 | 42.9% | 0.110 | 0.115 | 258.0% | 2.162 | 2 |
| 0.02 | 15 | 5 | 11 | 5 | 33.3% | 0.103 | 0.113 | 183.0% | 3.233 | 1 |
| 0.03 | 16 | 5 | 12 | 5 | 31.2% | 0.100 | 0.111 | 169.3% | 3.143 | 1 |
| 0.05 | 16 | 5 | 12 | 5 | 31.2% | 0.100 | 0.111 | 169.3% | 3.143 | 1 |
| 0.08 | 16 | 5 | 12 | 5 | 31.2% | 0.100 | 0.111 | 169.3% | 3.143 | 1 |

## Full Window

| cushion | rows | dates | cities | wins | win rate | avg snapshot ask | avg fresh ask | ROI | pnl/share | losing days |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 122 | 36 | 38 | 16 | 13.1% | 0.096 | 0.094 | 33.3% | 3.995 | 21 |
| 0.02 | 169 | 37 | 42 | 22 | 13.0% | 0.097 | 0.100 | 24.5% | 4.332 | 19 |
| 0.03 | 189 | 37 | 43 | 28 | 14.8% | 0.097 | 0.102 | 38.5% | 7.781 | 17 |
| 0.05 | 211 | 37 | 44 | 29 | 13.7% | 0.098 | 0.106 | 23.7% | 5.553 | 17 |
| 0.08 | 219 | 37 | 44 | 29 | 13.2% | 0.097 | 0.108 | 17.6% | 4.335 | 17 |

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
