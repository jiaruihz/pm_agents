---
name: weather-live-account-reconcile
description: >
  对账 weather 实盘账户现金变化、真实 CLOB fills、DB live_real、未结算持仓和已结算 PnL。
  触发词：余额少了、钱包余额、cash、USDC、账户净值、最近几天实盘亏了、为什么钱变少、
  实盘对账、CLOB fill 对不上、live_real 和 clob_fills 不一致、open orders reserved。
  禁止：只用 settled PnL 回答余额变化；把 fill cost 当亏损；把未结算估值混入 realized PnL；
  用 signals 快照冒充权威当前盘口；跳过 raw CLOB fill 与 fact_trades 的一致性检查。
---

# weather-live-account-reconcile

用于回答“钱包余额为什么变少 / 最近几天实盘到底亏没亏 / 记录链路是否漏 fill”。

这个问题必须分成四层，不准混说：

| 层 | 含义 | 默认来源 |
|---|---|---|
| `cash_cost_usd` | 真实 fill 买入花掉的现金 | `fact_trades.cost_usd` + raw `clob_fills.jsonl` 交叉检查 |
| `submitted_or_error_cost_usd` | 提交订单名义金额，可能含未成交/失败 | `orders` |
| `realized_pnl_usd` | 已结算真实 PnL | `fact_trades.pnl_usd_at_fill WHERE settlement_status='settled'` |
| `open_cost_usd` / `unrealized_pnl_*` | 未结算仓位成本与估值 | `fact_trades.val_mid/val_bid/val_last_fill`，必须标注估值时间 |

**余额变化不是 PnL。** 钱包可用余额下降通常先对应 `cash_cost_usd` 和 open-order reserved，
只有 market settlement / exit 后才会进入 realized PnL。

## 标准入口

先按 `weather-fact-rebuild` 刷新数据；如果用户明确说刚刷新过且给出 `fact_built_at_utc`，可以只自检。

```bash
bash scripts/ops/sync_weather_remote.sh
bash scripts/weather_dashboard/run_stack.sh
```

然后运行账户级对账脚本：

```bash
python3 scripts/analysis/weather_live_account_reconcile.py \
  --start 2026-06-04 \
  --end 2026-06-06 \
  --date-field fill_date_bj \
  --instances mid_price_core_v1_25_75,mid_price_core_v2_25_75,mid_price_core_v1_side_band \
  --group-by instance,selected_date
```

常用 date lens：

| `--date-field` | 回答什么 |
|---|---|
| `fill_date_bj` | 用户“最近几天余额实际花了多少”；默认现金流口径 |
| `target_date` | 某些天气合约日最终会赚亏多少；适合策略/城市/side 归因，不解释余额变化 |
| `order_date_bj` | 仅作策略下单归属诊断；禁止用于钱包现金流结论 |
| `fill_date_utc` | CLOB 成交发生在哪天；适合和 raw CLOB fills / API 对账 |

常用分组：

```bash
--group-by instance,selected_date
--group-by city,side
--group-by instance,city,side
```

`selected_date` 总是当前 `--date-field` 选择出来的日期，报告里必须写清楚 date lens。

## 必须报告

1. DB 新鲜度：`fact_built_at_utc`、最新 order/fill 时间。
2. 分析窗口和 date lens：不能省略。
3. `cash_cost_usd` 与 `realized_pnl_usd` 分开。
4. 未结算成本：`open_cost_usd`，单独说明它不是已亏。
5. `unrealized_pnl_mid/bid/last_fill` 的估值时间；若估值旧或缺失，不能当当前钱包净值。
6. Raw Live Order Files：`submitted_notional_usd` 与 `posted_notional_usd`，用于 DB 滞后时解释最新下单名义金额。
7. raw CLOB fill 与 `fact_trades live_real` 的 fill_id reconciliation：
   - `db_live_real_distinct_fills`
   - `raw_clob_distinct_fills`
   - `db_not_in_raw`
   - `raw_not_in_db`
8. CLOB fill coverage gate：
   - `gate_pass`
   - `missing_order_rows`
   - `over_order_keys`
   - `db_vs_primary_cache`
   - `db_fill_cost_minus_fact_cost`

## 禁止事项

- 不准回答“没 settle 所以看不到策略结果”。正确说法是：
  “看不到 realized PnL，但可以看到现金成本、未结算敞口和估值覆盖。”
- 不准把 `target_date` 和 `order_date_bj` 混在一起。
- 不准用 `fact_trades.order_date_bj` 解释钱包余额变化；现金流默认使用 `fill_date_bj`。
- 不准用 `signals.market_price` 当权威当前盘口；它只是策略信号快照。
- 不准看到 `realized_pnl_usd=0` 就说没亏；近期可能只是未结算。
- 不准看到钱包余额下降就说策略亏；可能只是 fill cost / open positions / reserved notional。
- 不准再写一次性 pandas 临时脚本替代 `scripts/analysis/weather_live_account_reconcile.py`；脚本缺字段就先补脚本和 contract。
- 不准用 public activity 单独解释 order-level fill；它只能作为 fallback，最终必须被 `weather_clob_fill_coverage_gate.py` 约束。

## 后续分析衔接

- 若 cash cost 和 raw CLOB fill 对不上：先修数据链路，不做策略结论。
- 若 cash cost 对得上但 open exposure 很大：转 `weather-strategy-exposure`。
- 若 settled 覆盖足够后要评价策略：转 `weather-strategy-performance`。
- 若某笔为什么下单/为什么反向：转 `weather-strategy-lineage`。
