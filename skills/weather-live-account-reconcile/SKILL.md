---
name: weather-live-account-reconcile
description: 对账 weather 实盘账户现金变化、真实 CLOB fills、submitted/posted notional、open orders、未结算持仓、fee-adjusted 已结算 PnL 与 DB live_real。用于余额少了、USDC/cash、钱包净值、CLOB fill 或 fee 对不上、live_real 与 raw 不一致、open-order reserved。禁止把 fill cost 当亏损、用 order_date_bj 解释现金流或跳过 raw/canonical reconciliation。
---

# Weather live account reconcile

回答“钱去哪了”时先拆现金、持仓、PnL 和预留资金，不能只报 settled PnL。

## 权威层

| 指标 | 含义 | 来源 |
|---|---|---|
| submitted notional | 尝试提交的名义金额，可能失败/取消 | 当前策略 raw orders/events |
| posted notional | 交易所实际接受/挂出的金额 | raw order/exchange response |
| actual fill cost | 已成交买入花掉的现金 | raw CLOB fill + `fact_trades.cost_usd` |
| fees | 实际或调整后的 fee | `fact_trades.fees_usd` + fee evidence fields |
| open cost | 未结算仓位成本，不是亏损 | `fact_trades` unsettled fills |
| realized PnL | 已结算、fee-adjusted PnL | `fact_trades.pnl_usd_at_fill` |
| unrealized valuation | mid/bid/last_fill 估值 | `fact_trades.val_*`，附估值时间 |
| reserved | 仍开放订单占用 | authenticated open orders；不得从 submitted notional 猜 |

当前 live order journals 从 `production.yaml` 的 expected-live `live_order_path` 解析；不得默认等于控制仓库某个旧 runtime 目录。`runtime/weather.db` 是 physical canonical DB 的兼容链接，`runtime/weather_edge_v1/clob_fills.jsonl` 是当前 canonical fill cache，这两者保留既有合同。N100 镜像只用于历史窗口。

## 流程

1. 读 `AGENTS.md` 与 `docs/WEATHER_ANALYSIS_CONTRACT.md`。
2. 运行 `.venv/bin/python scripts/ops/weather_production_ctl.py health` 与
   `.venv/bin/python scripts/ops/weather_production_manifest.py --strict`，再用 raw runtime 与 authenticated exchange
   动态发现实例；不从旧文档复制实例清单。
3. 比较 raw 最新 order/fill 与 DB `fact_built_at_utc` / `fill_ts_utc`。
4. DB 缺最新 fill 时先走 `weather-fact-rebuild` 的最小刷新路径。
5. 运行固定对账脚本。

脚本默认读取 `fact_trades.instance_id` / `orders.instance_id` 并从 production desired state
发现所有 active live journals；`--raw-live-dir` 只用于显式历史兼容，不得作为当前生产默认。

使用 canonical 数字时保存 DB realpath/device/inode、build time/`build_id` 和
`observed_at_utc`；对账运行中 refresh 改变 build 时重启查询，不混用两个分母。

```bash
.venv/bin/python scripts/analysis/account_reconcile/weather_live_account_reconcile.py \
  --start YYYY-MM-DD \
  --end YYYY-MM-DD \
  --date-field fill_date_bj \
  --instances all \
  --group-by instance,selected_date
```

固定对账脚本与 standalone coverage gate 必须共享同一套 effective fill
过滤（execution alias、fill validity、price/fee adjustment）和 fee lineage
判定。修改 gate 的公共 helper 或函数签名时，必须保留兼容默认值，并同时跑
固定对账脚本 smoke；禁止让下游报告复制一份过滤逻辑或依赖未声明的内部参数。

日期口径：

- `fill_date_bj`：钱包现金流默认口径。
- `fill_date_utc`：与 CLOB/API 对账。
- `target_date`：天气合约归因，不解释余额变化。
- `order_date_bj`：仅下单归属诊断，禁止用于钱包现金流。

## 必跑一致性

```bash
.venv/bin/python scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py
```

必须报告：

- `db_live_real_distinct_fills` / `raw_clob_distinct_fills`
- `db_not_in_raw` / `raw_not_in_db`
- `missing_order_rows` / `over_order_keys`
- DB/cache fill-id mismatch
- DB fill cost 与 fact cost delta
- effective fee=0 且缺 fee evidence 的 matched taker 数
- fee evidence class 分布与 adjustment 总额

`gate_pass=false` 时只给链路诊断，不发布 live_real PnL/ROI。
production manifest 为 critical（尤其 DB split/非 canonical consumer）时同样只做 raw/exchange 现金与订单诊断，不发布 DB live_real 桥接。

## 最终输出

先给余额变化桥接：

```text
期初可用现金
- actual fill cost
- current reserved
+ settlement/redemption/exit cash inflow
+/- external transfers
= 期末可用现金（在可见证据范围内）
```

然后分开列：submitted、posted、filled cash、fees、realized PnL、open cost、三估值及估值时间。若无法取得 authenticated wallet/open-order/transfer 数据，明确写“桥接不闭合”及差额，不用策略 PnL硬解释。

若发现 order-chain 异常，逐条拆出 submitted size、filled size、scope/cap state 和 exchange rejection text。
