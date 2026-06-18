# Pure Theta-NO Backtest v1

Status: snapshot
Updated: 2026-06-15
Source of truth: no
Used by: WEATHER_DOCS_INDEX.md

## 数据快照

- 数据源: `runtime/weather.db` self-check + reheat-risk observed/orderbook artifacts from `docs/analysis/2026-06/generated/m3_exhaustion_no_v0/`.
- DB fact built at: `2026-06-15T14:43:25.893487+00:00`.
- CLOB gate: `gate_pass=True`, `missing_order_rows=0`, `over_order_keys=0`.
- `fact_trades` by class: `[{'trade_class': 'live_real', 'rows': 855}, {'trade_class': 'live_simulated', 'rows': 624}, {'trade_class': 'paper', 'rows': 2285}, {'trade_class': 'snapshot_replay', 'rows': 636}]`.
- `fact_trades` by settlement: `[{'settlement_status': '', 'rows': 150}, {'settlement_status': 'settled', 'rows': 4250}]`.
- `fact_signal_candidates`: `{'rows': 30132, 'eligible': 10364, 'paper_ordered': 3961, 'live_filled': 348}`.
- CLOB order/fill join: `[{'status': 'error', 'orders': 33, 'with_fill': 0}, {'status': 'submitted', 'orders': 961, 'with_fill': 855}]`.
- Note: last full `run_stack.sh` rebuilt DB/facts/gate, then failed only at frontend port startup; this report does not depend on the frontend.

## Target Metric

`pure_theta_no_alpha` = in source-aligned/default-WU cities only, does a decision-time no-reheat signal create positive taker EV in above-running-max BUY_NO quotes?

Main denominator: default-WU/whitelist cities only. Station-basis repaired cities are a separate contrast table, not part of the theta conclusion.

Row grain: first qualifying orderbook quote per `city + target_date + bracket`; this is opportunity research, not live fills.

## Funnel

- Raw tail-NO quote rows: `11800` from `2026-05-20` to `2026-06-09`.
- Groups: `{'whitelist': 10275, 'repaired': 1525}`.
- Default-WU grid rules tested: `179`.
- Train/holdout split: `< 2026-06-01` vs `>= 2026-06-01`.

## Physical Layer

The physics is strong: in default-WU cities, 13-17h P(jump>=1) drops from 40.0% when decline is `<0.5C` to 1.7% when decline is `>=2C`.

## Default-WU Theta Grid

| rule | train rows | train ROI | train excess CI | holdout rows | holdout ROI | holdout excess CI |
|---|---:|---:|---|---:|---:|---|
| `h14_17_decline0.5_d2_ask0.97` | 38 | +7.2% | +13.6% [+7.3%..+20.1%] | 28 | -10.3% | -3.9% [-13.2%..+3.1%] |
| `h13_17_decline1_d2_ask0.97` | 54 | +4.7% | +9.1% [+2.7%..+13.4%] | 48 | -7.4% | -2.5% [-9.9%..+3.3%] |
| `h13_17_decline0.5_d2_ask0.97` | 73 | +2.6% | +6.9% [+1.7%..+11.2%] | 52 | -7.5% | -2.5% [-9.9%..+2.9%] |
| `h16_17_decline1_d1_ask0.97` | 44 | -2.9% | +3.8% [-5.5%..+13.5%] | 16 | -1.8% | +4.5% [-13.7%..+17.6%] |
| `h16_17_decline0.5_d1_ask0.97` | 52 | -5.7% | +1.1% [-6.5%..+8.5%] | 27 | -10.8% | -4.4% [-11.3%..+3.7%] |
| `h15_17_decline1_d1_ask0.97` | 73 | -4.1% | +0.7% [-4.8%..+6.0%] | 38 | -3.8% | +2.9% [-8.6%..+12.1%] |
| `h15_17_decline0.5_d1_ask0.97` | 99 | -5.1% | -0.2% [-5.6%..+4.6%] | 55 | -6.7% | +0.1% [-9.1%..+8.4%] |
| `h14_17_decline0.5_d1_ask0.97` | 153 | -5.3% | -0.8% [-5.3%..+3.1%] | 97 | -6.0% | -1.8% [-9.2%..+4.1%] |
| `h13_17_decline0.5_d1_ask0.97` | 216 | -4.7% | -1.1% [-4.1%..+1.6%] | 141 | -1.9% | +1.6% [-4.0%..+8.4%] |
| `h13_17_decline1.5_d1_ask0.97` | 39 | -5.2% | -1.7% [-8.8%..+7.1%] | 37 | -2.9% | +0.6% [-11.8%..+10.5%] |


The best train-selected default-WU theta rule was `h14_17_decline0.5_d2_ask0.97`: train ROI +7.2%, train excess +13.6%, holdout ROI -10.3%, holdout excess -3.9%.

## Basis Contrast

This is not part of the pure theta conclusion, but confirms why the two tracks must stay separate:

| rule | train rows | train ROI | train excess CI | holdout rows | holdout ROI | holdout excess CI |
|---|---:|---:|---|---:|---:|---|
| `h13_17_decline0.5_d1_ask0.9` | 14 | +9.4% | +13.2% [-16.4%..+31.3%] | 29 | +11.2% | +5.5% [-4.8%..+22.5%] |
| `h13_17_decline1_d1_ask0.9` | 14 | +9.4% | +13.2% [-16.4%..+31.3%] | 26 | +10.2% | +4.5% [-5.9%..+24.8%] |
| `h13_17_decline1_d1_ask0.97` | 27 | +6.6% | +8.3% [-3.3%..+19.3%] | 44 | +8.0% | +2.5% [-3.7%..+11.3%] |
| `h13_17_decline0.5_d1_ask0.97` | 28 | +6.6% | +8.2% [-2.8%..+18.9%] | 46 | +8.7% | +3.3% [-3.2%..+12.0%] |
| `h14_17_decline1_d1_ask0.97` | 21 | +7.9% | +1.9% [-6.6%..+9.2%] | 30 | +5.4% | +4.2% [-2.9%..+14.7%] |


## Verdict

significance=FAIL
baseline=FAIL
forward=FAIL
conclusion=inconclusive

一句话: 在 `2026-05-20..2026-06-09`，纯 theta-NO 在 default-WU/source-aligned 城市里没有通过 train/holdout 的正超额检验；物理信号是真的，但 taker 价格大体已经包含它。这个方向可以继续作为独立研究线改模型，但当前回测不支持 shadow/paper/live。
