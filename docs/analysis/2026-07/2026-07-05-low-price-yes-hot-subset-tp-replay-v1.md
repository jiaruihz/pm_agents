# HeadA hot 子集 TP/Stop 重放 v1（现 live 姿态分母）

Generated: 2026-07-05
Scope: HeadA `forecast_tail_low_price_yes`，退出政策专项。分母 = `low_price_yes_heada_refinement_v1/hot_rows.csv`
（333 行 `dist>0`，即当前 live blocker 之后的口径）+ `path_rows.csv` 路径事件。
执行代理 = `price_tier_6_8_10_shares` + 官方 Weather taker fee + hold 基线。
退出语义与 sizing_fee_stop_v2 完全一致（bid 触及 TP → 按固定 TP 价成交，resting-consistent）。
复算脚本：会话 scratchpad `heada_hot_tp_replay.py`（逻辑全部来自上述冻结 CSV，可逐行复算）。

## One-Line Read

**在当前 live 姿态（hot-only）分母上，所有 TP 变体都显著为负——包括此前全分母上唯一 CI>0 的
recover-stake@30c（hot 上 delta -21.1%，CI [-31.6%,-11.4%] 全负）。机制：hot 票触及 0.30 后最终胜率
51.4%（37/72）、触及 0.20 后 33.0%（38/115），远高于对应价格——市场对已启动的 hot tail 重定价不足，
在 20/30c 卖出等于把入场 alpha 在其表达最强的时刻卖回给市场。hold-to-settlement 是正确姿态；
strict dead stop delta +2.8% CI 跨 0，可不加。**

```text
significance=PASS（paired date-block delta CI 全负，train/full 两窗一致）
baseline=hold（同分母同 sizing 同 fee）
forward=NA（train 事后重放；不触发任何 live 变更——live 本来就是 hold）
conclusion=tp_all_variants_harmful_on_hot; forward plan A3 的 recover-stake@30c 候选在 hot 分母上不成立
```

## 数字

paired date-block bootstrap，delta = 政策 ROI − hold ROI（同日配对）：

| 政策 | 窗口 | ROI | delta vs hold | delta CI |
|---|---|---:|---:|---|
| hold | train ≤6/20 | +35.1% | — | — |
| hold_plus_strict_dead | train | +35.8% | +0.7% | [-14.3%, +11.8%] |
| recover_stake_tp30 | train | +18.2% | **-16.9%** | **[-28.1%, -6.5%]** |
| tp20_live_fixed20 | train | -1.2% | -36.4% | [-69.6%, -5.6%] |
| tp30_live_fixed30 | train | -3.9% | -39.0% | [-63.8%, -16.9%] |
| hold | full 5/06-6/30 | +41.8% | — | — |
| recover_stake_tp30 | full | +20.8% | **-21.1%** | **[-31.6%, -11.4%]** |
| tp20_live_fixed20 | full | -3.1% | -44.9% | [-74.5%, -17.9%] |
| tp30_live_fixed30 | full | -3.8% | -45.7% | [-69.1%, -24.2%] |

触及统计（hot 333 行，50 赢家）：tp20 触及 115 行→38 赢 77 输；tp30 触及 72 行→37 赢 35 输。

## 为什么与 take-profit v1 的 +10.8% 相反

take-profit v1 的 recover-stake@30c CI>0 是在 **476 行全分母**（含 143 张 cold 票）上：cold 票触及 30c 后
更常回落归零，recover-stake 在它们身上省钱。剔掉 cold 后剩下的 hot 触及者是接近 coin-flip 的 1.00 赔付票，
卖 30c 纯粹让渡凸性。**dist>0 blocker 上线后，全分母 TP 证据不再适用于 live 口径。**

## Caveats

- 5 月 bid 路径覆盖仅 60%（6 月 99%）；无路径行退化为 hold，方向性偏 TP 有利——真实 TP 伤害只会更大。
- 6/21-6/30 为 burned 窗口，只并入 full 佐证方向，不单独作为证据。
- 触及检测基于 ~17min cadence 快照 bid，漏掉盘中短暂触及；同样只会低估 TP 的伤害。

## 派生假说（只预注册，不动 live）

`E-touch`：hot 票重定价至 ≥0.30 后仍系统性低估（触及后胜率 51% vs 价格 30c）。若成立，正确表达
不是 TP 而是**触及后不动/加仓**。这是 train 事后切片，多重检验风险自知；如要推进，走 shadow
would-trigger telemetry（A3 三态记录已在收）+ 预注册，不直接改任何规则。
