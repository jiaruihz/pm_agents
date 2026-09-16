# 2026-06-06 Polymarket UI account loss reconciliation

> 2026-06-07 修正：本文最初把 UI `-$305.32` 与 `fact_trades` 差额解释为账户权益时间序列/持仓价值缺口，口径仍不够准。后续按 Polymarket public activity 的天气 `market_date` 重放后，`2026-05-31..2026-06-05` signed cash 为 `-$309.80`，已基本对上截图。当前权威结论见 [2026-06-06-account-equity-replay.md](2026-06-06-account-equity-replay.md)。

## 结论先行

- 用户截图里的 Polymarket `1周 -$305.32` 应作为账户级亏损目标指标处理；不能用 `fact_trades` 的已结算逐笔 PnL 去否定这个数量级。
- 2026-06-06 near-binary 修复后的 `fact_trades` 口径只能说明：最近 7 天 `target_date` live 总体已结算 PnL 是 `-$84.34`，当前三实例是 `-$89.78`。这是已恢复 fills 的策略归因口径，不是完整账号历史成交口径。
- 后续 public activity replay 发现：`2026-05-31..2026-06-05` 的天气 `market_date` signed cash 是 `-$309.80`，与 UI `-$305.32` 基本一致。
- 差异主因不是 open cost，也不是普通持仓波动，而是本地 DB / `clob_fills.jsonl` 少恢复了约 `$560+` 的真实 Polymarket BUY 成交。
- 当前公开 positions 快照显示该账号 open/position 侧几乎全是天气：all current value `285.81`，weather current value `256.10`，other current value `29.71`。所以“这个号基本由我们的天气单子构成，亏损数量级应能对上”这个前提是成立的。

## 这次纠正的口径错误

之前把 `-$305` 的差异解释成“把 open cost 当亏损”是不准确的。

更准确的拆分是：

| 口径 | 能回答什么 | 不能回答什么 |
|---|---|---|
| `fact_trades.realized_pnl_usd` | 已成交 fill 在官方/near-binary 结算后的策略逐笔 PnL | Polymarket UI 账户 1 周权益曲线 |
| `weather_live_account_reconcile.py --date-field fill_date_bj` | 买入现金流、submitted/posted/fill cost、settled/open 分拆 | 一周前到当前的 UI portfolio value delta |
| data-api `/activity` | 可见 BUY/SELL/REDEEM/REBATE 现金流 | 持仓价值从一周前到现在的完整 MTM 变化 |
| data-api `/positions` | 当前 open/position value 和 current cashPnl | 一周前同一时点的 position value |

所以正确的账户级目标指标应为：

```text
account_equity_delta_1w
= [cash_now + position_value_now + redeemable_or_claimable_now]
- [cash_1w_ago + position_value_1w_ago + redeemable_or_claimable_1w_ago]
- deposits_or_withdrawals_in_window
```

`fact_trades` 可以解释其中哪些策略/城市/side 造成了已结算亏损，但不能单独重建这条 UI 曲线。

## 已跑证据

### Fact 表 recent slice

见 [2026-06-06-live-strategy-period-slice.md](2026-06-06-live-strategy-period-slice.md)。

| date lens | realized_pnl_usd | open_cost_usd | available_mid_mtm_open | open_missing_mid_rows |
|---|---:|---:|---:|---:|
| `fill_date_bj` | -72.95 | 262.85 | -2.00 | 51 |
| `target_date` | -84.34 | 262.85 | -2.00 | 51 |

这张表说明：策略归因里的 settled realized 最近 7 天确实没有到 `-$305`，但这不是 UI 账户亏损的反证。

### Public activity cashflow

命令：

```bash
.venv/bin/python scripts/analysis/weather_polymarket_account_activity.py \
  --start 2026-05-31 \
  --json-out runtime/account_reconcile/weather_polymarket_activity_since_2026-05-31.json
```

结果：

| type | rows | money | signed cash |
|---|---:|---:|---:|
| `TRADE:BUY` | 690 | 1785.97 | -1785.97 |
| `TRADE:SELL` | 16 | 138.23 | 138.23 |
| `REDEEM` | 118 | 1565.09 | 1565.09 |
| `MAKER_REBATE` | 7 | 11.37 | 11.37 |
| total visible cash delta | 831 |  | -71.28 |

### Current public position snapshot

命令：

```bash
.venv/bin/python scripts/analysis/weather_polymarket_position_snapshot.py \
  > runtime/account_reconcile/weather_polymarket_position_snapshot_current.json
.venv/bin/python scripts/analysis/weather_polymarket_snapshot_summary.py \
  runtime/account_reconcile/weather_polymarket_position_snapshot_current.json
```

结果：

| bucket | positions | current_value | initial_value | cash_pnl | realized_pnl |
|---|---:|---:|---:|---:|---:|
| all | 239 | 285.81 | 1974.20 | -1688.39 | -7.23 |
| weather | 235 | 256.10 | 1918.70 | -1662.60 | -7.23 |
| other | 4 | 29.71 | 55.49 | -25.78 | 0.00 |
| closed weather | 10 | 0.00 | 0.00 | 0.00 | 304.02 |

注意：positions 的 `cash_pnl` 是当前持仓累计视角，不是最近 1 周 PnL；不能直接和 UI `1周` 相减。

## 下一步缺口

要严格对上截图，需要补一个 `account_equity_replay`：

1. 固定截图口径窗口，例如 UI 的 `1周` 起止时点。
2. 拉同一窗口的 activity，分 BUY/SELL/REDEEM/REBATE。
3. 建立当前持仓 token 粒度 inventory。
4. 回放窗口内交易，倒推出窗口起点 inventory。
5. 用历史 CLOB/pm_history 价格给起点和终点 inventory 估值。
6. 单独处理 UI 是否把 redeemable/claimable payout 计入 portfolio value。

在这一步完成前，正确结论是：`-$305.32` 很可能是账户权益口径的真实亏损数量级；`fact_trades=-$84.34` 只是已结算策略归因，不能与 UI 直接比较。
