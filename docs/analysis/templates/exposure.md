<!--
  M4 持仓敞口报告模板
  按问题组织标题；未实现 PnL 三估值并列，标注 [UNSETTLED]，缺失填 N/A 并说明原因。
  输出到：docs/analysis/YYYY-MM/YYYY-MM-DD-exposure-<snapshot_time>.md
  口径：docs/WEATHER_ANALYSIS_CONTRACT.md §2.1（未结算 PnL 三估值）
-->

# 持仓敞口快照：{snapshot_time}

> 快照时间：{snapshot_time}（北京时间）  
> 城市池：{city_pool}  
> 数据源：{data_source}

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源路径 / DB identity / build_id | {source_path / realpath / device / inode / build_id} |
| 最新 fill / valuation 时间及 stale 状态 | {latest_fill / valuation_ts / stale} |
| 快照时间 | {snapshot_ts} |
| 未结算持仓总笔数 | {open_position_count} |
| 涉及城市数 | {city_count} |
| 最早目标日 | {earliest_target_date} |
| 最晚目标日 | {latest_target_date} |
| mid/bid 盘口数据来源 | {snapshot_source 或 "N/A（无 snapshot）"} |

先给持仓成本、开放订单与主要集中度结论。即时查询可直接回答；完整逐笔清单放机器产物并提供链接。

## 未结算持仓清单

| instance_id | city | target_date | side | fill_price | fill_qty | cost_usd | fees_usd | condition_id |
|---|---|---|---|---|---|---|---|---|

## 开放订单与 reserved

| instance | city | target_date | side | posted price | open shares | posted notional | reserved | exchange status | evidence time |
|---|---|---|---|---:|---:|---:|---:|---|---|

开放订单不得并入已成交 position cost；reserved 无 authenticated evidence 时填 N/A。

## 聚合：by_market（按 condition_id）

| condition_id | city | target_date | open_positions | total_cost_usd |
|---|---|---|---|---|

## 聚合：by_city

| city | open_positions | total_cost_usd | avg_fill_price |
|---|---|---|---|

## 聚合：by_target_date（合约目标日，不是实际结算时间）

| target_date | open_positions | total_cost_usd |
|---|---|---|

## 未实现 PnL（三估值并列）[UNSETTLED]

> 三列均为估值，不计入历史绩效。mid/bid 不可用时填"N/A（无 snapshot）"。

| city | target_date | side | cost_usd | unrealized_pnl (mid) [UNSETTLED] | unrealized_pnl (bid) [UNSETTLED] | unrealized_pnl (last_fill) [UNSETTLED] |
|---|---|---|---|---|---|---|

**合计（三估值并列）：**

| 估值口径 | 未实现 PnL (USD) |
|---|---|
| mid（当前盘口中间价） | [UNSETTLED] |
| bid（当前买一价） | [UNSETTLED] |
| last_fill（最近成交价） | [UNSETTLED] |

## 集中度风险

**单市场占比 Top-5（by cost_usd）：**

| rank | condition_id / city | cost_usd | pct_of_total |
|---|---|---|---|

**单目标日占比 Top-5：**

| rank | target_date | cost_usd | pct_of_total |
|---|---|---|---|

## 数据完整性自检

- [ ] 所有持仓都有对应 fill 记录
- [ ] 没有已过到期日但未结算的持仓（若有，列出并标注异常）
- [ ] mid/bid 估值数据来源已在"数据快照"段说明

## 观察与建议

<!-- agent 自由发挥，不超过 500 字 -->
