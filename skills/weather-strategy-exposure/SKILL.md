---
name: weather-strategy-exposure
description: 查看 weather 策略当前未结算 fill、开放订单和风险敞口，按实例、城市、market、target_date、side 聚合，并列 mid/bid/last_fill 估值与估值时间。用于持仓、未平仓、在险资金、集中度、当前仓位、还挂着哪些单。余额或钱包现金流问题应联合 weather-live-account-reconcile；禁止把 open cost 或未实现 PnL说成已实现亏损。
---

# Weather strategy exposure

持仓快照必须把“已成交未结算 position”和“尚未成交 open order”分开。

## 流程

1. 读 `AGENTS.md`、`docs/WEATHER_ANALYSIS_CONTRACT.md`。
2. 运行 `.venv/bin/python scripts/ops/weather_production_ctl.py health` 与
   `.venv/bin/python scripts/ops/weather_production_manifest.py --strict`，动态发现当前 active strategy runtime 和
   DB identity；先看本机 raw，不从 registry 推断进程真的在跑。
3. 确认 manifest 无 `critical`、`db_route.status=healthy` 且兼容入口与 JRS physical canonical 为同一 device/inode 后，检查 `runtime/weather.db` 对目标窗口、估值时间和 raw order 的覆盖。无关 warning 逐项记录；若 DB split，open orders 仍可按 authenticated/raw 报告，但不得发布 canonical fill position/MTM。
4. DB 滞后时走 `weather-fact-rebuild` 的最小刷新路径；不要为了状态查询无条件全量 rebuild。
5. 固定 DB realpath/device/inode、build time/`build_id` 和 `observed_at_utc`；如 refresh 切换
   build，重启查询或按 build 分层。
6. 从 `fact_trades` 查询 fill position，从 raw/authenticated orders 查询 open/reserved。

只读连接：

```python
import sqlite3

conn = sqlite3.connect("file:runtime/weather.db?mode=ro", uri=True, timeout=1.0)
conn.execute("PRAGMA query_only=ON")
conn.execute("PRAGMA busy_timeout=1000")
```

## Canonical open fills

运行前先 `PRAGMA table_info(fact_trades)`，不要假定旧列名。当前实例字段是 `instance_id`。

```sql
SELECT
  instance_id, strategy_id, execution_policy, city, target_date,
  condition_id, bracket, side, fill_id, fill_ts_utc,
  fill_price, fill_qty, fees_usd, cost_usd,
  settlement_status, val_mid, val_bid, val_last_fill,
  unrealized_pnl_mid, val_snapshot_ts_utc
FROM fact_trades
WHERE COALESCE(settlement_status, '') <> 'settled'
  AND (:trade_class = 'all' OR trade_class = :trade_class)
ORDER BY target_date, city, condition_id, side, fill_ts_utc;
```

注意 `settlement_status != 'settled'` 会漏掉 NULL；使用 `COALESCE`。

## 必须报告

- DB build time、最新 fill、最新 valuation time。
- open fill 数、fill cost、fee、按 market/city/date/instance 的集中度。
- mid / bid / last_fill 三估值并列；缺值与 stale 时间明确写出。
- 已过 target_date 仍 unresolved 的清单。
- open orders 的 side/price/shares/notional/status/reserved；与 positions 分表。
- 最大单 market、单 city、单 target_date、同天气事件相关性风险。

未结算估值统一标 `[UNSETTLED]`，不得加入 realized PnL。余额问题转 `weather-live-account-reconcile`，使用 `fill_date_bj`。

报告模板：`docs/analysis/templates/exposure.md`。实质性审计写入当月 `docs/analysis/YYYY-MM/`；单纯即时查询可直接答，不为一条快照制造文档。
