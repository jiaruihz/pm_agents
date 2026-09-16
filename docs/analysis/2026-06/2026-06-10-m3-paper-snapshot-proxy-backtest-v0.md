# reheat-risk Paper Snapshot Proxy Backtest v0

Status: snapshot
Updated: 2026-06-10
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; 2026-06-10-m3-observed-max-residual-v0.md

## 数据快照

本报告尝试把 reheat-risk observed running max 接到现有历史价格 proxy。

重要限制：这不是 executable orderbook backtest。它使用
`runtime/weather_edge_v1/remote_pm_agent/market_data/paper_snapshots/` 里的
`market_yes_price`，不是 `orderbook_snapshots` 里的 best ask。

为什么不用真正 orderbook：

| 数据 | 当前本机覆盖 |
|---|---|
| WU observed cache | 2024-04-30 to 2026-05-12 |
| paper snapshots | 2026-05-05 to 2026-05-17 |
| orderbook snapshots | 2026-05-19 to 2026-06-10 |

因此当前本机 `orderbook_snapshots` 和 observed cache 没有重叠窗口；无法直接算 reheat-risk 的 best-ask 收益。

## Target Metric

本轮只尝试两个 proxy 规则：

| strategy | 定义 |
|---|---|
| `observed_bucket_buy_yes` | 买 observed running max 所在 bracket 的 YES |
| `below_running_max_buy_no` | 买低于 observed running max 的 bracket 的 NO |

价格口径：

```text
BUY_YES cost = market_yes_price
BUY_NO cost = 1 - market_yes_price
```

收益口径：

```text
pnl = payout - cost
roi = pnl / cost
```

## 产物

脚本：

```text
scripts/analysis/observed_max/research_m3_paper_snapshot_proxy_backtest.py
```

输出：

```text
docs/analysis/2026-06/generated/m3_paper_snapshot_proxy_v0/
docs/analysis/2026-06/generated/m3_paper_snapshot_proxy_18_21_v0/
```

## 结果

### 20/21 点

```text
raw_quote_rows=15,797
price_grid_rows=4,552
joined_market_rows=4
```

触发交易：

| strategy | hour | trades | city_days | cities | cost | pnl | roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| below_running_max_buy_no | 20 | 2 | 1 | 1 | 1.006 | 0.994 | 98.8% |
| below_running_max_buy_no | 21 | 2 | 1 | 1 | 1.000 | 1.000 | 100.0% |
| below_running_max_buy_no | ALL | 4 | 1 | 1 | 2.006 | 1.994 | 99.4% |

全部来自：

```text
Moscow 2026-05-12
```

### 18/19/20/21 点

```text
raw_quote_rows=28,565
price_grid_rows=8,848
joined_market_rows=14
```

触发交易：

| strategy | hour | trades | city_days | cities | cost | pnl | roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| below_running_max_buy_no | 18 | 6 | 2 | 2 | 4.002 | 1.998 | 49.9% |
| below_running_max_buy_no | 19 | 4 | 1 | 1 | 2.997 | 1.003 | 33.5% |
| below_running_max_buy_no | 20 | 2 | 1 | 1 | 1.006 | 0.994 | 98.8% |
| below_running_max_buy_no | 21 | 2 | 1 | 1 | 1.000 | 1.000 | 100.0% |
| below_running_max_buy_no | ALL | 14 | 2 | 2 | 9.005 | 4.995 | 55.5% |

全部来自：

```text
Madrid 2026-05-05
Moscow 2026-05-12
```

## 结论

```text
significance=NA
baseline=NA
forward=NA
conclusion=inconclusive
```

不要引用上面的 ROI 作为 reheat-risk 收益。原因：

- 20/21 点只有 4 条交易、1 个 city-day。
- 18-21 点也只有 14 条交易、2 个 city-day。
- 样本只来自 Madrid / Moscow，不能代表 core 9 或全城市。
- paper snapshot 可能是策略候选过滤后的记录，不是完整市场 universe。
- 价格是 `market_yes_price` proxy，不是 executable best ask；没有 spread、depth、fill probability。
- `observed_bucket_buy_yes` 在这个 proxy 里没有触发，说明当前 paper snapshot 缺完整 observed bucket 市场覆盖。

因此当前收益问题的诚实答案是：

```text
本地现有数据还不能给 reheat-risk 严肃 ROI。
P2 物理层已过，但 P4 收益层需要先补 observed cache 与 orderbook 的重叠窗口。
```

## 下一步

要得到可引用收益，必须补齐其中一条路径：

1. 把 WU/IEM observed cache 补到至少 2026-06-10，然后 join 现有 `orderbook_snapshots`。
2. 或者把 2026-05-05 到 2026-05-12 的真实 orderbook best ask 回填出来。

优先推荐第 1 条，因为本机已经有 2026-05-19 至 2026-06-10 的 orderbook snapshots。
