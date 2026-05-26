<!--
  M4 持仓敞口报告模板
  规则：H2 顺序不得改变，未实现 PnL 三估值必须并列，标注 [UNSETTLED]。
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
| 数据源路径 | {source_path} |
| 快照时间 | {snapshot_ts} |
| 未结算持仓总笔数 | {open_position_count} |
| 涉及城市数 | {city_count} |
| 最早到期日 | {earliest_settle_date} |
| 最晚到期日 | {latest_settle_date} |
| mid/bid 盘口数据来源 | {snapshot_source 或 "N/A（无 snapshot）"} |

## 未结算持仓清单

| city | city_pool | target_date | side | fill_price | fill_qty | cost_usd | condition_id |
|---|---|---|---|---|---|---|---|

## 聚合：by_market（按 condition_id）

| condition_id | city | target_date | open_positions | total_cost_usd |
|---|---|---|---|---|

## 聚合：by_city

| city | open_positions | total_cost_usd | avg_fill_price |
|---|---|---|---|

## 聚合：by_settle_date

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

**单到期日占比 Top-5：**

| rank | target_date | cost_usd | pct_of_total |
|---|---|---|---|

## 数据完整性自检

- [ ] 所有持仓都有对应 fill 记录
- [ ] 没有已过到期日但未结算的持仓（若有，列出并标注异常）
- [ ] mid/bid 估值数据来源已在"数据快照"段说明

## 观察与建议

<!-- agent 自由发挥，不超过 500 字 -->
