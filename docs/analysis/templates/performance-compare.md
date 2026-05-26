<!--
  M3 A/B 对比报告模板
  规则：H2 顺序不得改变，每个切片段都必须有 A | B | delta | delta% 四列。
  输出到：docs/analysis/YYYY-MM/YYYY-MM-DD-compare-<A>-vs-<B>.md
  口径：docs/WEATHER_ANALYSIS_CONTRACT.md
-->

# 策略对比：{selector_A} vs {selector_B}

> 时间窗：{date_start} — {date_end}（北京时间）  
> 对比维度：{compare_dimension}（如 strategy_id / city_pool / code_version）  
> 数据源：{data_source}

## 数据快照

| 项目 | A（{selector_A}） | B（{selector_B}） |
|---|---|---|
| 数据快照时间 | | |
| fills 行数 | | |
| unsettled 占比 | | |
| missing_bracket 数 | | |

## 对比设定

- **Selector A**：{selector_A 完整描述，含 strategy_id / city_pool / code_version / 时间窗}
- **Selector B**：{selector_B 完整描述}
- **对齐方式**：{同时间窗 / 同城市池 / 其他，说明是否完全可比}

## 总览对比

| 指标 | A | B | delta (B−A) | delta% |
|---|---|---|---|---|
| PnL (USD, fill 口径) | | | | |
| ROI | | | | |
| Win rate（by count） | | | | |
| Win rate（by notional） | | | | |
| fills 数 | | | | |
| 总 cost (USD) | | | | |

## 切片对比：by_date

| 日期（北京时间） | A pnl | B pnl | delta | A win_rate | B win_rate |
|---|---|---|---|---|---|

## 切片对比：by_city

| city | A pnl | B pnl | delta | A roi | B roi |
|---|---|---|---|---|---|

## 切片对比：by_model

| model | A pnl | B pnl | delta | A win_rate | B win_rate |
|---|---|---|---|---|---|

## 切片对比：by_side

| side | A pnl | B pnl | delta | A win_rate | B win_rate |
|---|---|---|---|---|---|

## 显著差异 Top-N

**B 显著优于 A（delta > +$2 或 win_rate delta > +10%）：**

| 维度 | 值 | A | B | delta |
|---|---|---|---|---|

**A 显著优于 B：**

| 维度 | 值 | A | B | delta |
|---|---|---|---|---|

## 数据完整性自检

- [ ] A 和 B 时间窗完全对齐（或差异已在"对比设定"注明）
- [ ] A 和 B 城市池范围一致（或差异已注明）
- [ ] 双方 unsettled 占比均 < 20%

## 观察与建议

<!-- agent 自由发挥，不超过 500 字 -->
