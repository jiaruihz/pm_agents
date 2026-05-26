<!--
  M1 绩效切片报告模板
  规则：H2 顺序不得改变，不得删除段落，可在末尾追加"观察与建议"。
  输出到：docs/analysis/YYYY-MM/YYYY-MM-DD-performance-<topic>.md
  口径：docs/WEATHER_ANALYSIS_CONTRACT.md
-->

# 绩效分析：{topic}

> 时间窗：{date_start} — {date_end}（北京时间）  
> 策略：{strategy_id}  
> 城市池：{city_pool}  
> 数据源：{data_source}

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源路径 | {source_path} |
| 数据快照时间 | {snapshot_ts} |
| fills 行数 | {fill_row_count} |
| unsettled 占比 | {unsettled_n} / {total_n}（{unsettled_pct}%） |
| missing_bracket 数 | {missing_bracket_n} |

## 总览

| 指标 | 已结算（fill 口径） | 已结算（plan 口径） | 含未结算（mid 估值）[UNSETTLED] |
|---|---|---|---|
| 总 PnL (USD) | | | |
| ROI | | | |
| Win rate（by count） | | | |
| Win rate（by notional） | | | |
| 总 fills 数 | | | |
| 总 cost (USD) | | | |
| 总 fill_qty (shares) | | | |
| Sharpe-like（daily） | | | |

## 切片：by_date

| 日期（北京时间） | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |
|---|---|---|---|---|---|---|---|

## 切片：by_city

| city | fills | wins | win_rate | cost_usd | pnl_usd (fill) | roi |
|---|---|---|---|---|---|---|

## 切片：by_model

| model | fills | wins | win_rate | pnl_usd (fill) | roi |
|---|---|---|---|---|---|

## 切片：by_side

| side | fills | wins | win_rate | avg_fill_price | pnl_usd (fill) | roi |
|---|---|---|---|---|---|---|

## 切片：by_pool

| pool | fills | wins | win_rate | pnl_usd (fill) | roi |
|---|---|---|---|---|---|

## Top Winners / Top Losers

**Top 5 winners（by pnl_usd_at_fill）：**

| city | target_date | side | fill_price | plan_price | fill_qty | settlement_price | pnl_usd |
|---|---|---|---|---|---|---|---|

**Top 5 losers：**

| city | target_date | side | fill_price | plan_price | fill_qty | settlement_price | pnl_usd |
|---|---|---|---|---|---|---|---|

## 数据完整性自检

- [ ] fill_row_count 与预期时间窗匹配
- [ ] unsettled_pct < 20%（否则说明结算延迟，总览数字不可靠）
- [ ] missing_bracket：列出具体城市/日期（若无则写"无"）
- [ ] by_date 行数 = 时间窗天数（否则说明某天无数据，注明原因）

## 观察与建议

<!-- agent 自由发挥，不超过 500 字 -->
