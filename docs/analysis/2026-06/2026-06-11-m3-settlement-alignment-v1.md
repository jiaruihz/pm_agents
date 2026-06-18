# reheat-risk Settlement Alignment v1

Status: snapshot
Updated: 2026-06-11
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; analysis/reheat_risk.md

## 数据快照

数据源：镜像 CSV/JSON，而不是 `fact_trades` fill-grain 绩效表。

原因：本报告验证 reheat-risk 的 source/settlement alignment。它回答的是：

```text
WU/IEM observed final max 能否替代 pm_history 官方 winning bracket 来判定 reheat-risk payout？
```

它不是 live PnL，不是 CLOB fill ROI，也不使用账户真实成交。

| source | path | rows/files |
|---|---|---:|
| observed detail | `docs/analysis/2026-06/generated/m3_observed_max_v1/m3_observed_max_residual_detail.csv` | 150,203 rows |
| best-ask v1 trades | `docs/analysis/2026-06/generated/m3_orderbook_best_ask_v1/m3_orderbook_best_ask_trades.csv` | 60 rows |
| pm_history | `runtime/weather_edge_v1/market_data/cache/pm_history/` | 1,403 valid single-winner city-days in observed universe |

Contract status:

```text
significance=NA
baseline=NA
forward=NA
conclusion=inconclusive
```

本报告禁止导出 live 动作。

## 勘误结论

reheat-risk 的 v0/v1 observed-payout orderbook 回测不能作为收益结论。

根因：

```text
WU/IEM observed final max 与 Polymarket pm_history 官方 winner bracket 不稳定一致。
```

尤其是 Celsius 市场，`16.1°C`、`27.2°C` 这类 WU/IEM raw 值不能直接解释成
“16°C / 27°C bracket 已经不可能”。官方结算经常仍落在这些整数 bracket。

因此：

```text
below_running_max_buy_no 当前不能进入 shadow/paper/live。
```

它必须先解决 source/rounding alignment。

## Alignment 结果

脚本：

```text
scripts/analysis/observed_max/research_m3_settlement_alignment.py
```

产物：

```text
docs/analysis/2026-06/generated/m3_settlement_alignment_v1/
```

对所有 observed city-day，用 `pm_history` 的 `unit` 做四种映射测试：

| scope | valid city-days | raw match | floor match | round match | ceil match |
|---|---:|---:|---:|---:|---:|
| all | 1,403 | 43.41% | 66.50% | 88.10% | 68.57% |
| unit C | 1,065 | 26.67% | 57.09% | 85.54% | 59.81% |
| unit F | 338 | 96.15% | 96.15% | 96.15% | 96.15% |

解释：

- F 市场基本可由 WU/IEM `final_max_f` 对齐，仍有约 3.85% mismatch。
- C 市场即使用 `round(final_max_c)`，也只有 85.54% 和官方 winner 一致。
- 这说明 reheat-risk 的核心风险不是 orderbook 价格，而是官方 settlement source / unit / rounding / station 口径。

## Unit-Aware Best-Ask v1

同时修正了 best-ask 脚本：

```text
scripts/analysis/observed_max/research_m3_orderbook_best_ask_backtest.py
```

v1 改动：

- 从 `pm_history` 读取 city-date 的 `unit`。
- F 市场用 `running_max_f / final_max_f` 对齐 bracket。
- C 市场默认用 `round(running_max_c / final_max_c)` 对齐 bracket。
- 产物改为 `docs/analysis/2026-06/generated/m3_orderbook_best_ask_v1/`。

observed-payout 口径下，v1 仍看似赚钱：

| strategy | trades | cost | observed pnl | observed roi | observed win rate |
|---|---:|---:|---:|---:|---:|
| below_running_max_buy_no | 23 | 3.43 | 19.57 | 569.97% | 100.00% |
| observed_bucket_buy_yes | 37 | 18.67 | 16.33 | 87.47% | 94.59% |

但这只是 WU/IEM observed payout。

## 官方结算重算

用 `pm_history` winner label 直接重算同一批 v1 trades 后：

| strategy | trades | cost | official pnl | official roi | official win rate | payout mismatches |
|---|---:|---:|---:|---:|---:|---:|
| below_running_max_buy_no | 23 | 3.43 | -0.43 | -12.61% | 13.04% | 20 |
| observed_bucket_buy_yes | 37 | 18.67 | -1.67 | -8.94% | 45.95% | 18 |

按小时拆分：

| strategy | hour | trades | cost | official pnl | official roi | win rate |
|---|---:|---:|---:|---:|---:|---:|
| below_running_max_buy_no | 20 | 15 | 2.32 | -0.32 | -13.87% | 13.33% |
| below_running_max_buy_no | 21 | 8 | 1.11 | -0.11 | -9.99% | 12.50% |
| observed_bucket_buy_yes | 20 | 23 | 10.78 | -0.78 | -7.22% | 43.48% |
| observed_bucket_buy_yes | 21 | 14 | 7.89 | -0.89 | -11.30% | 50.00% |

这直接推翻了 v0 报告里 “lower bracket NO 强正” 的收益解释。

## 机制样例

典型 mismatch：

```text
Milan 2026-05-20
running_max_c=27.22
reheat-risk v1 would buy NO on bracket 25
pm_history winner_labels=25
official payout for BUY_NO = 0
```

这说明 WU/IEM 观测到的 max 与 Polymarket 官方 source 并不同步；不能用 WU/IEM raw max 当结算真相。

## 当前动作

reheat-risk 当前状态降级为：

```text
settlement_blocked
```

允许继续研究：

1. 找到 Polymarket 使用的官方 weather source / station / rounding 规则。
2. 用该官方 source 重建 observed running max。
3. 只在 official-source running max 与 `pm_history` winner 近似 100% 对齐后，再重跑 best-ask。
4. 若继续研究 WU/IEM，只能把它当 noisy proxy，不能把 observed payout 当收益。

禁止：

1. 用 `m3_orderbook_best_ask_v0` 或 `v1 observed_payout` ROI 作为收益结论。
2. 把 `below_running_max_buy_no` 接入 shadow/paper/live。
3. 在 settlement alignment 未过前讨论 live sizing 或部署。
