# 绩效分析：maker_queue_v2 与 v3 城市池当日拆解

> **口径状态：局部执行/未结算视图，保留为事故复盘。**
> 本报告适合解释 2026-05-28 当日 maker_queue / mid_price 分支和 open mark 体感，
> 但城市级长期 realized alpha 以
> [2026-05-29-performance-city-pool-side-strategy.md](2026-05-29-performance-city-pool-side-strategy.md)
> 的城市池策略复盘报告为准。

> 时间窗：2026-05-27 — 2026-05-29（北京时间）  
> 策略：live CLOB；重点 `maker_queue_v1` / `maker_queue_v2` / `mid_price_core_v1`  
> 城市池：`t1_trading`，并拆成旧 T1 保留城市 vs 2026-05-27 v3 新增城市  
> 数据源：DB

目标指标：拆解 2026-05-28 看到的亏损来自执行策略、旧城市池，还是 2026-05-27 v3 新增城市。

分母：真实 live CLOB fills；已结算绩效只看 `settlement_status='settled'`，未结算单独用盘口估值标注 `[UNSETTLED]`。

城市分组：

| 分组 | 城市 |
|---|---|
| v3 新增城市（2026-05-27） | BuenosAires, Amsterdam, Manila, Munich, Singapore, Chengdu |
| 旧 T1 保留城市 | 当前 T1 中除上述 6 城外的城市 |

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源路径 | `runtime/weather.db` |
| 数据快照时间 | 2026-05-28 23:07:38 +0800（DB mtime；同步 N100 后重建） |
| fills 行数 | 2,437 total；live CLOB 257 |
| unsettled 占比 | live CLOB 98 / 257（38.1%） |
| missing_bracket 数 | settlements 表 111；live CLOB 23 |

## 总览

| 指标 | 已结算（fill 口径） | 已结算（plan 口径） | 含未结算（mid 估值）[UNSETTLED] |
|---|---:|---:|---:|
| 总 PnL (USD) | +180.62 | +182.24 | +47.50（contract YES-price 估值） |
| ROI | +31.7% | +32.0% | +14.2%（按 open cost 335.25） |
| Win rate（by count） | 61.0% | 61.0% | 64.3% tentative |
| Win rate（by notional） | 58.8% | N/A | N/A |
| 总 fills 数 | 159 settled | 159 settled | 98 open |
| 总 cost (USD) | 569.88 | 569.88 | 335.25 open cost |
| 总 fill_qty (shares) | 1,122.45 | 1,122.45 | N/A |
| Sharpe-like（daily） | N/A（窗口短且大量未结算） | N/A | N/A |

补充：如果按 Polymarket UI 更接近的 token-side mark-to-market 估值，2026-05-28 未结算 live open PnL 为 **-37.70**，这解释了“今天一直亏”的体感；它不是官方已结算亏损。若把 99%/1% 的市场按 implied final 先准结算，则 5/28 有效视图约为 **-26.48**。`mid_price_core_v1` 和 `maker_queue_v2` 两条 live 分支同时跑是当前预期，下面按分支拆开。`maker_queue_v1` 不是当前仍在跑的新策略分支，而是 v2 切换前提交、切换后仍可能成交或未结算的遗留 GTC exposure。

## 2026-05-28 未结算浮盈浮亏

> `[UNSETTLED]` 三估值均不计入已结算历史绩效。  
> contract YES-price 口径按 `docs/WEATHER_ANALYSIS_CONTRACT.md` 的 PnL 公式：BUY_NO 用 `filled_price - yes_estimate`。  
> token-side 口径更接近 Polymarket UI 持仓体感：BUY_NO 用 NO token 的 bid/mid/last 估值减买入价。
> 准结算口径：若 YES/NO token mid 已经 >= 0.99 或 <= 0.01，则按 implied final_yes=1/0 先归入 `quasi_settled_99pct`，不再只当普通 open mark。官方历史绩效仍以 settlement 表为准。

**按 execution_policy：**

| policy | open fills | open cost | contract mid | contract bid | contract last_fill | token mid | token bid | token last_fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| maker_queue_v1 | 9 | 31.25 | +5.80 | +5.82 | +5.70 | -8.45 | +3.79 | -8.55 |
| maker_queue_v2 | 16 | 44.62 | +10.51 | +11.07 | +8.01 | -5.02 | +4.31 | -7.50 |
| mid_price_core_v1 | 22 | 68.73 | -2.54 | -2.13 | -5.44 | -24.23 | -14.39 | -27.02 |
| **Total** | **47** | **144.59** | **+13.77** | **+14.76** | **+8.28** | **-37.70** | **-6.30** | **-43.07** |

`maker_queue_v1` 下单时间核对：DB 中 v2 第一批 live order 从 `2026-05-27T16:09:34Z` 开始；此后 `maker_queue_v1` 新订单数为 0。5/28 仍显示的 9 笔 v1 open fills，订单均在 `2026-05-27T09:42:15Z` — `2026-05-27T15:42:53Z` 提交，也就是北京时间 2026-05-27 17:42 — 23:42，属于切换前挂出的 GTC 单；其中最晚成交到 `2026-05-27T16:56:21Z`，也就是北京时间 2026-05-28 00:56。

**准结算 vs 仍未决：**

| bucket | fills | cost | quasi/official PnL | token_mid_pnl |
|---|---:|---:|---:|---:|
| quasi_settled_99pct | 11 | 31.06 | -0.44 | -11.66 |
| still_open_marked | 36 | 113.53 | N/A | -26.04 |
| **quasi + still_open token_mid** | **47** | **144.59** | **-0.44 quasi** | **-26.48 adjusted view** |

按这个口径，5/28 的 `-37.70` raw token-side open mark 里，有 11 笔其实已经接近确定结果。把这 11 笔按 implied final 先准结算后，当日有效视图约为 **-26.48**（`-0.44` 准结算 + `-26.04` 仍未决浮动）。

**准结算口径按 policy：**

| policy | quasi fills | quasi PnL | still-open fills | still-open token_mid_pnl | adjusted view |
|---|---:|---:|---:|---:|---:|
| maker_queue_v1 | 3 | +3.45 | 6 | -7.35 | -3.90 |
| maker_queue_v2 | 2 | +3.53 | 14 | -6.40 | -2.87 |
| mid_price_core_v1 | 6 | -7.42 | 16 | -12.29 | -19.71 |

`mid_price_core_v1` 仍是主要拖累；`maker_queue_v2` 在准结算口径下也只是小亏。

**按城市池变更分组：**

| 分组 | open fills | open cost | contract mid | contract bid | contract last_fill | token mid | token bid | token last_fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 旧 T1 保留城市 | 45 | 140.92 | +13.06 | +13.90 | +7.60 | -37.15 | -5.62 | -42.52 |
| v3 新增 6 城 | 2 | 3.67 | +0.70 | +0.86 | +0.67 | -0.55 | -0.67 | -0.55 |

**按 policy × 城市池分组：**

| policy | 分组 | open fills | open cost | contract mid | token mid |
|---|---|---:|---:|---:|---:|
| maker_queue_v1 | 旧 T1 保留城市 | 9 | 31.25 | +5.80 | -8.45 |
| maker_queue_v2 | 旧 T1 保留城市 | 15 | 43.12 | +10.22 | -4.79 |
| maker_queue_v2 | v3 新增 6 城 | 1 | 1.50 | +0.29 | -0.22 |
| mid_price_core_v1 | 旧 T1 保留城市 | 21 | 66.56 | -2.95 | -23.90 |
| mid_price_core_v1 | v3 新增 6 城 | 1 | 2.17 | +0.42 | -0.33 |

**按 city（token mid，最接近今日 UI 体感）：**

| city | open fills | open cost | token_mid_pnl [UNSETTLED] | contract_mid_pnl [UNSETTLED] |
|---|---:|---:|---:|---:|
| London | 2 | 9.99 | -9.95 | -9.21 |
| Karachi | 5 | 15.11 | -10.69 | -5.44 |
| Shanghai | 3 | 7.10 | -7.08 | -3.28 |
| Moscow | 2 | 8.45 | -6.97 | -2.42 |
| Guangzhou | 5 | 21.18 | -6.04 | +3.21 |
| Paris | 3 | 5.77 | -3.61 | -2.37 |
| Tokyo | 5 | 18.28 | -2.56 | +6.03 |
| Miami | 4 | 10.08 | -2.23 | -2.47 |
| NYC | 4 | 13.43 | -2.05 | +3.25 |
| BuenosAires | 2 | 3.67 | -0.55 | +0.70 |
| Ankara | 5 | 16.04 | +7.80 | +16.01 |
| Jeddah | 3 | 9.99 | +3.82 | +9.92 |
| Warsaw | 2 | 2.75 | +5.15 | +5.15 |

## 切片：by_date

| 日期（北京时间） | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-05-26 | 18 settled / 22 fills | 8 | 44.4% | 70.23 | +2.88 | +3.19 | +4.1% |
| 2026-05-27 | 43 settled / 62 fills | 25 | 58.1% | 155.19 | +36.53 | +37.21 | +23.5% |
| 2026-05-28 | 0 settled / 47 fills | N/A | N/A | 0.00 | N/A | N/A | N/A |
| 2026-05-29 | 0 settled / 11 fills | N/A | N/A | 0.00 | N/A | N/A | N/A |

2026-05-28 尚未结算，所以当日只能看 `[UNSETTLED]` 盘口估值。

## 切片：by_city

2026-05-28 token-side open PnL 主要拖累：

| city | fills | token_mid_pnl [UNSETTLED] | 主要来源 |
|---|---:|---:|---|
| London | 4 | -7.56 | mid_price_core_v1 |
| Paris | 6 | -7.83 | mid_price_core_v1 |
| Miami | 13 | -11.76 | mid + maker_queue |
| Karachi | 6 | -10.66 | maker_queue_v1/v2 + mid |
| Tokyo | 10 | -5.71 | mid/maker_queue_v1 被拖，maker_queue_v2 为正 |
| Shanghai | 3 | -7.08 | 三个策略分支均为负 |
| Moscow | 2 | -6.97 | mid + maker_queue_v2 |

2026-05-27 v3 新增城市在 2026-05-28 只有 2 个 live fills（BuenosAires），token_mid_pnl **-0.55**；样本太小，不是今天亏损主因。

## 切片：by_model

本次问题更集中在 execution_policy / city / side；未单独展开 by_model。DB 中 `forecast_source` 可继续拆，但当前样本下 by_model 不是主解释变量。

## 切片：by_side

2026-05-27 — 2026-05-28 旧 T1 已结算表现：

| side | fills | wins | win_rate | avg_fill_price | pnl_usd (fill) | roi |
|---|---:|---:|---:|---:|---:|---:|
| BUY_NO | 34 settled / 88 fills | 22 | 64.7% | N/A | +39.33 | +32.5% |
| BUY_YES | 7 settled / 17 fills | 1 | 14.3% | N/A | -12.51 | -51.2% |

BUY_YES 仍然是结构性拖累，尤其旧 T1。

## 切片：by_pool

| pool / 分组 | fills | settled | cost_usd | pnl_usd (fill) | roi | token_mid_pnl [UNSETTLED] |
|---|---:|---:|---:|---:|---:|---:|
| 旧 T1 保留城市（2026-05-27 — 05-28） | 105 | 41 | 145.49 | +26.83 | +18.4% | 2026-05-28: -37.15 |
| v3 新增 6 城（2026-05-27 — 05-28） | 2 | 0 | 0.00 | N/A | N/A | 2026-05-28: -0.55 |
| v2 新增 8 城（2026-05-27 — 05-28） | 46 | 21 | 74.46 | +18.30 | +24.6% | 未单独汇总 |

旧 T1 是今天 UI 亏损主因；v3 新增城市目前 exposure 很小。

## Top Winners / Top Losers

**Top losers（2026-05-28 token_mid [UNSETTLED]）：**

| city | target_date | side | fill_price | plan_price | fill_qty | settlement_price | pnl_usd |
|---|---|---|---:|---:|---:|---:|---:|
| Guangzhou | 2026-05-28 | BUY_NO | 0.63 | N/A | 7.93 | token_mid 0.0015 | -4.98 |
| London | 2026-05-28 | BUY_NO | 0.54 | N/A | 9.25 | token_mid 0.0015 | -4.98 |
| London | 2026-05-28 | BUY_YES | 0.37 | N/A | 13.51 | token_mid 0.0025 | -4.96 |
| Karachi | 2026-05-28 | BUY_NO | 0.59 | N/A | 8.40 | token_mid 0.0020 | -4.94 |
| Karachi | 2026-05-28 | BUY_NO | 0.59 | N/A | 7.18 | token_mid 0.0020 | -4.22 |

**Top winners（2026-05-28 token_mid [UNSETTLED]）：**

| city | target_date | side | fill_price | plan_price | fill_qty | settlement_price | pnl_usd |
|---|---|---|---:|---:|---:|---:|---:|
| Ankara | 2026-05-28 | mixed | N/A | N/A | N/A | token_mid | +7.80 aggregate |
| Jeddah | 2026-05-28 | mixed | N/A | N/A | N/A | token_mid | +3.82 aggregate |
| Warsaw | 2026-05-28 | mixed | N/A | N/A | N/A | token_mid | +5.15 aggregate |

## 数据完整性自检

- [x] 已执行 `scripts/ops/sync_weather_remote.sh`，随后重建 `runtime/weather.db`。
- [x] fill_row_count 与 DB 状态匹配：total fills 2,437；live CLOB 257。
- [ ] unsettled_pct < 20%：未通过。live CLOB unsettled 38.1%，所以 2026-05-28 只能作为盘口估值，不是最终绩效。
- [ ] missing_bracket：存在。settlements 表 111；live CLOB 23，集中在历史/近期部分美国城市 bracket。
- [x] by_date 行数覆盖 2026-05-27 — 2026-05-29；2026-05-28/29 尚未 settled。

## 观察与建议

交易动作：不要因为 2026-05-28 盘中红就回滚 v3 新增城市；新增 6 城目前 live exposure 太小，不能解释亏损。更应该先限制旧 T1 中的 London / Paris / Miami / Karachi / Shanghai / Moscow / Tokyo，以及继续压 BUY_YES。

执行动作：当前 `mid_price_core_v1` 与 `maker_queue_v2` 两条 live 分支同时跑是正确实验设计；按 2026-05-28 未结算 token_mid 估值，`mid_price_core_v1` 是最大分支拖累（-24.23），`maker_queue_v2` 为 -5.02。`maker_queue_v1` 不是当前新提交分支，是切换前遗留 open exposure，仍有 9 笔 target_date=2026-05-28 未结算。

风控动作：2026-05-28 有大量 `not enough balance / allowance` live_order_error，说明钱包余额/allowance 被 active orders 占用。短期建议暂停或降低新单，先取消/梳理 active orders，再恢复 maker_queue_v2。
