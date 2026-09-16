# Tmax Coherent Cal Quote Live Readiness v1

## 结论

`coherent_cal_quote` 已接入与研究同源的共享 calibrator，并通过历史概率 parity、fresh snapshot dry-run 与 canonical CLOB fill gate。策略维持研究判级 `shadow_candidate`；本次仅按用户明确批准启动 5-share、每 city-day first-lock、每轮最多 1 单的 tiny-live 取证。

## 模型与执行口径

- strategy instance/id: `tmax_coherent_cal_quote_tiny_live_v1`
- base model: `loo_no_city_source_blend`, `C=0.03`, `alpha=0.50`
- second stage: `coherent_cal_quote`, `C=0.30`, `alpha=0.25`
- expressions: `current_no / d1_no / d2_no / d1_yes / d2_yes`; `current_yes` 不启用
- entry: ask `0.40..0.99`, official taker fee-adjusted edge `>=0.02`, fresh CLOB ask, 5 shares
- timing: first city-day lock, `trend3h_flat` 排除，snapshot `<=60m`，observation `<=90m`
- lineage: 新实例也读取 predecessor live journal，避免换 strategy id 后同 city-day 再下单

## Parity

- 共享实现：`src/strategies/weather_edge_v1/tools/tmax_coherent_calibrator.py`
- 2026-07-08 历史切片：25 state rows；新共享实现与研究冻结输出最大概率绝对误差 `1.11e-16`
- fresh dry-run：snapshot `2026-07-10T19:41:56Z`，age `12.0m`；base fit 8,376 rows；calibrator fit 7,539 rows / 44 dates；无订单提交
- 离线证据沿用冻结报告：first-lock ROI `+11.2%`；相对修复基线 `+3.1pp`, CI `[+0.6,+5.7]`；但相对旧 missing 模型不显著且研究窗口已反复观察，因此不是正式 live 晋级证据

## P0 Fill 修复

根因有两层：runtime order migration 先按 matched response 合成 fill，随后 CLOB sync 又导入真实 fill；CLOB sync/cache 又只按 `fill_id` 去重，无法识别不同 id 的同一物理成交。

修复后：

- runtime migration 只迁 `order`，fill 统一归共享 CLOB sync；matched response 只在 CLOB sync 内作为 fallback
- DB/cache 均按 `(order_id, filled_at_utc, filled_shares, filled_price)` 识别物理重复
- coverage gate 显式拒绝 runtime synthetic fill 和物理重复 fill
- canonical 修复只替换 `polymarket_clob` fill 分区，不动 paper/snapshot fill

影响半径：旧库 2026-06-16..2026-07-10 有 88 条 synthetic fill，虚增 cost `$291.84`；其中 86 条已结算，虚增 reported PnL `$29.36`。旧 cache 另有 5 个物理重复。逐条清单在 `runtime/backups/20260711_tmax_p0_fill_fix/synthetic_fill_impact_rows.csv`。

最终 gate：DB/cache/fact_trades 均为 1,007 个 CLOB fills、cost `$2,622.173162`；synthetic=0、physical duplicate=0、over-cap=0、missing-order=0、cost delta=0，`gate_pass=true`。

## P1/P2 修复

- sibling ask 在任何 edge/排序计算前必须是 finite；NaN 只写 blocked telemetry
- observation age 缺失保留 `null` 并 block；不再伪装为 `0.0m`
- live 参数新增 `--max-obs-age-min=90`

## 数据链恢复

Mac data-feed 旧 tmux server 对 JRS 卷出现 `Operation not permitted`。启动器改用独立 socket `weather-data-feed-jrs`，新进程已真实写出 observations、source events、forecast enrichment 与 `snapshot_20260711_0341.json`；HK 1x proxy probe 正常。

## 状态边界

tiny-live 是用户批准的执行取证，不改变研究判级。若后续 forward 概率评分、执行后 edge 或分支 PnL 失败，应暂停实例并保留完整 selected/blocked/order/fill 血缘，不通过新增天气 gate 追单个坏 case。
