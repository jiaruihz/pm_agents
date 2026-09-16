# 2026-06-06 account equity replay

## 结论先行

- 用户截图里的 Polymarket `1周 -$305.32` 是可信的账户级亏损数量级。
- 正确解释不是“open cost 被当成亏损”，也不是普通持仓波动；按 Polymarket public activity 的天气 `market_date` 聚合，`2026-05-31..2026-06-05` 已结算/已 redeem 现金流合计 `-$309.80`，和截图 `-$305.32` 基本对上。
- `weather.db` / `clob_fills.jsonl` 最近窗口只恢复了 `394` 笔、`$1225.87` 的 CLOB fills；但 Polymarket public activity 记录了 `690` 笔 BUY、`$1785.97`。两者差了约 `$560.10` 的 BUY 成交。
- raw remote live order files 最近 `2026-05-31..2026-06-06` 的 posted notional 是 `$1909.81`，和 Polymarket BUY `$1785.97` 同数量级；所以“我们发出去的单子”与 Poly 历史买入记录能对上，DB fill recovery 当前对不上。
- 因此后续策略亏损归因不能只用当前 `fact_trades live_real`，必须先修/补 CLOB fill recovery，把 Poly activity 中这批天气 BUY 回填进本地权威成交表。

## 数据源

| ledger | 来源 | 用途 |
|---|---|---|
| local DB fills | `runtime/weather.db` 的 `orders/fills/fact_trades` | 当前本地权威 fill 表，但本次发现覆盖不足 |
| raw live orders | `runtime/weather_edge_v1/remote_pm_agent/live/*orders.jsonl` | 我们实际提交/posted 的订单名义金额 |
| Polymarket activity | public data-api `/activity` | 账号历史 BUY/SELL/REDEEM/REBATE 现金流 |

## 最近窗口：发单 vs Poly 历史 vs DB fills

窗口：`2026-05-31T00:00:00Z .. 2026-06-07T00:00:00Z`；raw order file 日期为北京时间 `2026-05-31..2026-06-06`。

| 指标 | 数值 |
|---|---:|
| raw remote live posted notional | 1909.81 |
| Polymarket activity `TRADE:BUY` | 1785.97 |
| DB CLOB fill cost | 1225.87 |
| posted notional - Poly BUY | 123.84 |
| Poly BUY - DB fills | 560.10 |

解释：

- `posted notional - Poly BUY` 约 `$123.84`，可以由未成交、未完全成交、撤单、订单余量解释。
- `Poly BUY - DB fills` 约 `$560.10`，不能由策略亏损解释；这是本地 fill recovery / DB coverage 缺口。

## DB fill 与 Poly BUY 匹配

脚本：

```bash
.venv/bin/python scripts/analysis/weather_account_equity_replay.py \
  --start 2026-05-31 \
  --end 2026-06-07 \
  --target-start 2026-05-31 \
  --target-end 2026-06-06 \
  --json-out docs/analysis/2026-06/2026-06-06-account-equity-replay.json
```

结果：

| 指标 | 数值 |
|---|---:|
| matched DB fills | 369 |
| matched DB cost | 1153.33 |
| matched Poly BUY | 1153.33 |
| unmatched DB fills | 25 |
| unmatched DB cost | 72.55 |
| unmatched Poly BUY rows | 321 |
| unmatched Poly BUY money | 632.64 |
| unmatched Poly BUY weather-like money | 632.64 |
| unmatched Poly BUY known-local-token money | 623.22 |

这说明已匹配部分金额完全一致；未匹配 Poly BUY 全部是天气盘，且大多数 token 在本地 fact universe 出现过，但这些具体成交没有进入本地 CLOB fills。

## Polymarket activity 按天气 market_date 的结果

脚本：

```bash
.venv/bin/python scripts/analysis/weather_polymarket_account_activity.py \
  --start 2026-05-01 \
  --end 2026-06-07 \
  --json-out runtime/account_reconcile/weather_polymarket_activity_2026-05-01_to_2026-06-07.json
```

按标题/slug 里的天气 `market_date` 聚合：

| market_date | REDEEM | BUY | SELL | signed_cash |
|---|---:|---:|---:|---:|
| 2026-05-31 | 130.13 | 299.33 | 12.68 | -156.52 |
| 2026-06-01 | 447.07 | 531.41 | 106.65 | 22.31 |
| 2026-06-02 | 218.32 | 227.62 | 0.00 | -9.30 |
| 2026-06-03 | 276.82 | 182.47 | 0.00 | 94.36 |
| 2026-06-04 | 142.03 | 276.49 | 0.00 | -134.46 |
| 2026-06-05 | 105.29 | 231.47 | 0.00 | -126.18 |
| 2026-06-06 | 0.00 | 186.84 | 0.00 | -186.84 |

聚合：

| target set | signed_cash |
|---|---:|
| `2026-05-31..2026-06-05` | -309.80 |
| `2026-05-31..2026-06-06` | -496.64 |

`2026-06-06` 当时还没有 REDEEM，所以不能直接和已结算周损益混算。`2026-05-31..2026-06-05` 的 `-$309.80` 已经解释了截图 `1周 -$305.32` 的主要数量级。

## 需要修的地方

### 根因判断

本次少数不是 settlement near-binary 的问题，而是 CLOB fill recovery 的部分成交覆盖问题：

1. Polymarket 一个 maker order 可以拆成多笔 partial trades。
2. `weather_dashboard/ingest/clob_fill_sync.py` 的 public activity fallback 旧逻辑每个 submitted order 只取第一笔 public trade。
3. 认证 CLOB 路径旧逻辑只在 `status == MATCHED` 时写 fill；如果订单还是 `LIVE/OPEN` 但 `sizeMatched > 0`，会被归为 still_open。
4. `fills.fill_id` 旧逻辑按 `execution_id + order_id` 生成，天然把“一单多成交”压成“一单一 fill”；cache 里已有旧 first-fill 后，`INSERT OR IGNORE` 不会覆盖。

这解释了为什么：

- raw live posted notional `$1909.81` 能和 Polymarket BUY `$1785.97` 对上；
- 但 DB CLOB fills 只有 `$1225.87`；
- unmatched Poly BUY `$632.64` 全部是 weather-like，其中 `$623.22` 是本地已知 token。

### 已做代码修复

已修改 `weather_dashboard/ingest/clob_fill_sync.py`：

1. public activity fallback 不再只取 earliest trade，而是按 exact condition/token、side、price、placement time 匹配多笔 partial fills。
2. 第一笔 public fill 继续使用旧 per-order `fill_id`，兼容已有 cache；同一订单后续 partial fills 使用 `transactionHash/asset/timestamp/size/price` 派生新的 trade-grain `fill_id`。
3. 认证 CLOB 路径对 `LIVE/OPEN` 但 `sizeMatched > 0` 的订单写入 partial fill，不再直接当 still_open。
4. `BUY` order side 兼容 public activity 的 `BUY`，避免只接受 `BUY_YES/BUY_NO`。

新增测试：

```bash
.venv/bin/python -m pytest tests/weather_dashboard/test_clob_fill_sync.py -q
```

结果：`4 passed`。

### 还需要执行的恢复步骤

1. 用修复后的 `clob_fill_sync` 重跑 CLOB fill recovery，让缺失 partial fills 追加到 `runtime/weather_edge_v1/clob_fills.jsonl`。
2. 重新 rebuild `runtime/weather.db` 和 `fact_trades`。
3. 再跑 `weather_account_equity_replay.py`，目标是：
   - `Poly BUY - DB fills` 从约 `$560.10` 降到接近 0；
   - unmatched Poly BUY 不再有 `$600+` weather-like 缺口；
   - `fact_trades live_real` 的 recent settled PnL 与 Poly market-date signed cash 同数量级。

### 以后怎么保证

后续账户/策略分析前必须加这个 gate：

| gate | 通过条件 |
|---|---|
| raw order vs Poly BUY | posted notional 与 Poly BUY 只差未成交/撤单余量 |
| DB fills vs Poly BUY | 同窗口 weather-like Poly BUY unmatched money 接近 0 |
| fact_trades vs raw CLOB fills | distinct fill_id 差异为 0 |
| market_date cash replay | 已结算日期的 BUY/SELL/REDEEM signed cash 与账户 UI 同数量级 |

如果任一 gate 不通过，不能发布 city/side/strategy PnL 结论，只能报告“成交恢复不完整”。

## 当前交易结论

最近一周账户级亏损确实接近 `-$300`。V2 和部分扩池城市的策略归因仍需要重算，但必须先补齐 `$560+` 的缺失 BUY 成交，否则 city/side/strategy PnL 会系统性低估亏损。
